"""Surrogate-assisted Bayesian calibration of the OpenFOAM case.

The OpenFOAM half of aXqua's HydroBayesCal integration; the TELEMAC half is
:mod:`axqua.bayescal`, and the plumbing both share is :mod:`axqua.hbc`. What is
calibrated here is the bed roughness ``ks`` and, optionally, the k-epsilon
closure coefficients, against **velocity components** measured at points -
``U_x``, ``U_y``, ``U_z`` (and ``TKE``) - which is what an ADV campaign such as
a FlowTracker survey actually produces, and what a depth-averaged 2D model
cannot be calibrated against at all.

**Run the campaign coarse and under a rigid lid.** ``mode: rigid-lid`` removes
the air phase by construction (about 90% of a two-phase case's cells) and fits
the layers to the water alone, and ``cell_size_factor`` coarsens the plan
lattice; a surrogate needs tens of runs, and a production-resolution two-phase
case cannot supply them. :func:`campaign_config` builds that variant from the
case's own config without disturbing it. Two consequences worth being explicit
about:

* the free surface is **prescribed** from the 2D result rather than solved, so
  any roughness error that would have shown up as a surface-slope error is
  absorbed into the velocity field instead. The posterior should be verified
  once at production resolution with ``mode: vof`` before it is believed.
* rigid-lid has exactly **one** run stage (:func:`axqua.solvers.openfoam.dicts.stages`),
  so the case HydroBayesCal copies per run is correct straight out of
  ``build_case`` with no stage to activate incorrectly.

**One global ks.** HydroBayesCal's OpenFOAM binding writes a single
``Ks uniform`` to the bed patch. There is no per-zone roughness here, so this
posterior is *not* comparable with the zoned ``zone<N>`` posterior of a TELEMAC
calibration on the same reach, and the two must not be reported as if they were.
"""

from __future__ import annotations

import copy
import logging
import re
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from axqua import campaigns, hbc
from axqua.calibration import build_calibration_csv, merged_parameters
from axqua.config import Config
from axqua.solvers.openfoam import dicts
from axqua.solvers.openfoam.case import build_case
from axqua.solvers.openfoam.spec import OPENFOAM_PARAMETERS

log = logging.getLogger("axqua")

#: Sub-directory of ``calibration_dir`` holding everything this module produces.
CAMPAIGN_SUBDIR = "openfoam"

#: Error floor [m/s] on the **vertical** velocity component. Deliberately coarser
#: than the horizontal floor: a side-looking ADV's ``w`` at 0.6*h is dominated by
#: mounting tilt and by the probe's own beam geometry, so the raw per-point error
#: claims a precision the measurement does not have. This keeps U_z in the
#: likelihood as a weak constraint on the secondary circulation instead of as the
#: loudest voice in it. It is a judgement, not a measurement - drop U_z from
#: ``calibration_quantities`` (it is still extracted) if that trade is not wanted.
VERTICAL_ERROR_FLOOR = 0.03

#: Canonical spelling of every parameter the binding dispatches on. HydroBayesCal
#: matches case-insensitively but then writes the name straight into the OpenFOAM
#: dictionary, where ``sigmaeps`` is not ``sigmaEps``.
_CANONICAL = {"ks": "ks", "cmu": "Cmu", "c1": "C1", "c2": "C2",
              "sigmak": "sigmak", "sigmaeps": "sigmaEps"}

#: Driver rewrites applied on top of :data:`axqua.hbc.EXTRACTION_PATCH`. Empty
#: because the upstream OpenFOAM binding is used unmodified; `required` below is
#: what makes a regression in it fail loudly instead of silently.
_DRIVER_PATCHES: dict[str, str] = {}


# --------------------------------------------------------------------------- #
# the campaign case
# --------------------------------------------------------------------------- #
def campaign_config(cfg: Config, *, cell_size_factor: float, end_time: float,
                    write_interval: float, n_processors: int,
                    n_avg_timesteps: int = 5,
                    turbulence: str = "kEpsilon") -> Config:
    """A copy of *cfg* describing the **campaign** case, not the production case.

    A deep copy rather than a mutation: the caller's config still describes the
    full-resolution two-phase case that the verification run rebuilds, so the two
    never have to be reconciled and running a campaign cannot quietly change what
    ``openfoam_preprocessing.py`` would build afterwards.
    """
    c = copy.deepcopy(cfg)
    c.openfoam.mode = "rigid-lid"
    c.openfoam.cell_size_factor = float(cell_size_factor)
    c.openfoam.turbulence = turbulence
    c.openfoam.end_time = float(end_time)
    # No interface to settle under a rigid lid, so stages() yields a single stage
    # and a spin-up would be dead time in every one of tens of runs.
    c.openfoam.spinup_time = 0.0
    c.openfoam.write_interval = float(write_interval)
    c.openfoam.n_processors = int(n_processors)
    # Only the trailing window is ever read. Keeping every write time would put
    # dozens of binary time directories per run on disk purely to delete them.
    c.openfoam.purge_write = int(n_avg_timesteps) + 1
    c.openfoam_dir = Path(cfg.calibration_dir) / CAMPAIGN_SUBDIR / "case-template"
    return c


def _clean_template(case_dir: Path) -> None:
    """Strip a built case down to what is worth copying once per run."""
    for pattern in ("processor*", "VTK", "postProcessing", "log.*", "*.log"):
        for path in case_dir.glob(pattern):
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    for path in case_dir.iterdir():
        # every numeric time directory except 0: a run starts from its own 0/
        if path.is_dir() and re.fullmatch(r"[0-9]+(\.[0-9]+)?", path.name) \
                and float(path.name) != 0.0:
            shutil.rmtree(path)


def _assert_template(case_dir: Path, cfg: Config, parameters: Sequence[Any],
                     n_avg_timesteps: int) -> None:
    """Fail before the campaign starts, not hours into it.

    Every check here corresponds to a failure that otherwise surfaces late and far
    from its cause - as an OpenFOAM parse error, as a ``Key not found`` on the
    first perturbed run, or as an extraction that silently averages the wrong
    window. Each message is distinct so the log says which one fired.
    """
    nut = case_dir / "0" / "nut"
    if not nut.is_file():
        raise SystemExit(f"calibration template has no 0/nut: {nut}")
    text = nut.read_text()
    if "nonuniform" in text and re.search(r"^\s*Ks\s+nonuniform", text, re.M):
        raise SystemExit(
            f"calibration template carries a per-face bed Ks ({nut}).\n"
            "HydroBayesCal rewrites the single line matching '^\\s*Ks\\s+' with "
            "'Ks uniform <v>;' and cannot skip a multi-line list, so the list body "
            "would survive as orphaned tokens and 0/nut would stop parsing.\n"
            "Build the template with uniform_bed_ks=True (stage_case_template does).")

    coeffs = [p for p in parameters if _canonical(p.name) != "ks"]
    if coeffs:
        mt = case_dir / "constant" / "momentumTransport"
        if not mt.is_file():
            raise SystemExit(
                f"calibrating {', '.join(p.name for p in coeffs)} needs "
                f"{mt}, which does not exist.")
        body = mt.read_text()
        if "kEpsilonCoeffs" not in body:
            raise SystemExit(
                f"{mt} carries no kEpsilonCoeffs subdictionary, so HydroBayesCal "
                f"cannot perturb {', '.join(p.name for p in coeffs)} "
                "(update_dictionary_entry raises \"Key not found\"). Set "
                "openfoam.turbulence: kEpsilon and rebuild the template.")
        for p in coeffs:
            key = _canonical(p.name)
            if not re.search(rf"^\s*{re.escape(key)}\s+", body, re.M):
                raise SystemExit(
                    f"{mt} has a kEpsilonCoeffs block but no '{key}' line, so "
                    f"perturbing {p.name} would be a silent no-op (OpenFOAM falls "
                    "back to its built-in value for a coefficient it cannot find).")

    control = case_dir / "system" / "controlDict"
    if not control.is_file():
        raise SystemExit(f"calibration template has no system/controlDict: {control}")
    ctext = control.read_text()
    if not re.search(r"^\s*startFrom\s+startTime\s*;", ctext, re.M):
        raise SystemExit(
            f"{control} does not start from startTime. Each calibration run is a "
            "fresh copy of the template and must start from its own 0/, not from a "
            "latestTime that the copy does not carry.")
    n_writes = int(cfg.openfoam.end_time // cfg.openfoam.write_interval)
    if n_writes < n_avg_timesteps:
        raise SystemExit(
            f"the template writes {n_writes} time(s) "
            f"(end_time {cfg.openfoam.end_time:g} s / write_interval "
            f"{cfg.openfoam.write_interval:g} s) but extraction averages the last "
            f"{n_avg_timesteps}. Lower n_avg_timesteps or lengthen the run.")

    if not (case_dir / "system" / "decomposeParDict").is_file():
        raise SystemExit(
            f"calibration template has no system/decomposeParDict; a parallel run "
            f"would fail inside HydroBayesCal rather than here ({case_dir}).")

    stray = sorted(p.name for p in case_dir.iterdir()
                   if p.is_dir() and re.fullmatch(r"[0-9]+(\.[0-9]+)?", p.name)
                   and float(p.name) != 0.0)
    if stray:
        raise SystemExit(
            f"calibration template still holds result time director{'ies' if len(stray) > 1 else 'y'} "
            f"{', '.join(stray)}. They would be copied into every run and averaged "
            "into its result. Rebuild the template with rebuild=True.")


def stage_case_template(cfg: Config, *, state=None, rebuild: bool = False,
                        parameters: Sequence[Any] = (),
                        n_avg_timesteps: int = 5) -> Path:
    """Build (or reuse) the case HydroBayesCal copies once per calibration run."""
    dest = Path(cfg.openfoam_case_dir)
    if rebuild and dest.exists():
        shutil.rmtree(dest)

    built = None
    if not (dest / "constant" / "polyMesh" / "faces").is_file():
        log.info("building the calibration case template in %s", dest)
        artifacts = built = build_case(cfg, state=state, uniform_bed_ks=True)
        # The template's nominal Ks should be representative of the reach rather
        # than the 0.05 m fallback: it is the value every run starts from and the
        # natural centre for the ks prior.
        bed_ks = getattr(artifacts.mesh, "bed_ks", None)
        if bed_ks is not None:
            import numpy as np
            finite = np.asarray(bed_ks, dtype=float)
            finite = finite[np.isfinite(finite)]
            if finite.size:
                cfg.openfoam.friction_ks = float(np.median(finite))
                log.info("template bed Ks set to the wetted median %.4f m",
                         cfg.openfoam.friction_ks)
                build_case(cfg, state=state, uniform_bed_ks=True)
    else:
        log.info("reusing the calibration case template in %s", dest)

    _clean_template(dest)
    # Persist the campaign lattice's plan columns for the target placer. Written
    # beside the calibration inputs, NOT into the template: _clean_template does
    # not strip it and every per-run copy would carry it.
    try:
        from axqua.model_column import write_column_cache
        cache = Path(cfg.openfoam_case_dir).parent / "column-geometry.npz"
        faces = dest / "constant" / "polyMesh" / "faces"
        # Only (re)build when there is something to learn: with a fresh build the
        # mesh is already in hand, and with a reused template an up-to-date cache
        # means re-deriving the lattice would cost minutes to reproduce a file
        # that already exists.
        if built is not None:
            write_column_cache(cfg, cache, mesh=built.mesh, state=state)
        elif not cache.is_file() or cache.stat().st_mtime < faces.stat().st_mtime:
            write_column_cache(cfg, cache, state=state)
    except Exception as exc:  # noqa: BLE001 - the placer rebuilds if this is absent
        log.debug("column cache not written (%s: %s); the target placer will "
                  "rebuild the lattice", type(exc).__name__, exc)
    # Regenerate system/ from THIS config, so a reused template cannot carry an
    # end_time or Courant number from an earlier campaign.
    dicts.activate(dest, dicts.stages(cfg)[0].name, cfg)
    _assert_template(dest, cfg, parameters, n_avg_timesteps)
    return dest


# --------------------------------------------------------------------------- #
# parameters
# --------------------------------------------------------------------------- #
def _canonical(name: str) -> str:
    return _CANONICAL.get(str(name).strip().lower(), str(name).strip())


def openfoam_parameters(cfg: Config, *, names: Sequence[str] | None = None) -> list:
    """The OpenFOAM-legal subset of the case's declared calibration parameters.

    :func:`axqua.calibration.merged_parameters` also collects TELEMAC roughness
    zones and ``.cas`` keywords, every one of which the OpenFOAM binding rejects -
    and rejects *after* the experimental design has been sampled and a case copied.
    Filtering here, with a warning naming each dropped parameter, is the difference
    between a mis-declared parameter costing a log line and costing a setup.
    """
    from axqua.config import CalibrationParameter

    merged = merged_parameters(cfg)
    if names is not None:
        wanted = {str(n).strip().lower() for n in names}
        merged = [p for p in merged if str(p.name).strip().lower() in wanted]

    keep, drop = [], []
    for p in merged:
        if str(p.name).strip().lower() in OPENFOAM_PARAMETERS:
            keep.append(CalibrationParameter(name=_canonical(p.name), min=p.min,
                                             max=p.max, comment=p.comment))
        else:
            drop.append(p.name)

    if drop:
        log.warning("not OpenFOAM calibration parameters, skipped: %s. The OpenFOAM "
                    "binding calibrates only %s (case-insensitive); roughness zones "
                    "and .cas keywords belong to the TELEMAC calibration.",
                    ", ".join(sorted(drop)), ", ".join(sorted(OPENFOAM_PARAMETERS)))
    if not keep:
        raise SystemExit(
            "no OpenFOAM calibration parameters declared. Add them under "
            "calibration.parameters in case-config.yml, e.g.\n"
            "    parameters:\n"
            "      - { name: ks,  min: 0.02, max: 0.30 }\n"
            "      - { name: Cmu, min: 0.06, max: 0.12 }\n"
            "or pick the 'OPENFOAM ...' rows in the parameters tab of "
            "calibration-target-data.xlsx.")
    return keep


def check_turbulence(cfg: Config, parameters: Sequence[Any]) -> None:
    """Refuse a k-epsilon coefficient on a case that does not solve k-epsilon.

    Checked here rather than in ``OpenFoam.validate()``, which runs on every
    ``load_config`` and cannot see the calibration block. The failure it prevents
    is specific and expensive: with kOmegaSST the case carries no
    ``kEpsilonCoeffs``, so HydroBayesCal raises on the first perturbed run - after
    the design has been sampled.
    """
    coeffs = [p.name for p in parameters if _canonical(p.name) != "ks"]
    if coeffs and cfg.openfoam.turbulence != "kEpsilon":
        raise SystemExit(
            f"calibrating {', '.join(coeffs)} needs the k-epsilon closure, but "
            f"openfoam.turbulence is {cfg.openfoam.turbulence!r}. Set\n"
            "    openfoam:\n      turbulence: kEpsilon\n"
            "in case-config.yml and rebuild the calibration template. (Without it "
            "constant/momentumTransport carries no kEpsilonCoeffs subdictionary, so "
            "every run would fail once the design had already been sampled.)")


# --------------------------------------------------------------------------- #
# the emitted HydroBayesCal config
# --------------------------------------------------------------------------- #
def _bed_datum(cfg: Config, template: Path) -> float:
    """Datum for depth / free-surface extraction: the case's own bed minimum.

    Not left at 0.0 (the binding's default): this reach sits at ~370 m a.s.l., and
    a datum of zero would make any depth the binding derives meaningless.
    """
    try:
        from axqua.core.selafin import read_slf
        # read_slf returns a DICT. The old `result.variable("BOTTOM")` raised
        # AttributeError into the bare except below, so every emitted config
        # carried reference_z = 0.0000 - on a reach at ~375 m a.s.l. Read the bed
        # from the geometry, which is small and always present, not from the
        # multi-frame result.
        geom = read_slf(cfg.model_path(cfg.geometry_slf))
        return float(np.min(geom["values"]["BOTTOM"]))
    except Exception as exc:                          # noqa: BLE001
        log.debug("bed datum from the 2D result unavailable (%s: %s); using 0.0",
                  type(exc).__name__, exc)
        return 0.0


def emit_openfoam_config(cfg: Config, *, template: Path, csv: Path,
                         parameters: Sequence[Any],
                         calibration_quantities: Sequence[str],
                         extraction_quantities: Sequence[str],
                         out_dir: Path,
                         n_avg_timesteps: int = 5,
                         n_processors: int = 8,
                         init_runs: int | None = None,
                         max_runs: int | None = None,
                         only_bal_mode: bool = False) -> Path:
    """Write ``config_OpenFOAM.py`` for the staged ``bal_openfoam.py``.

    Deliberately not a parameterisation of :func:`axqua.calibration.emit_hbc_config`:
    the OpenFOAM schema uses ``simulation`` where TELEMAC uses
    ``hydrodynamic_simulation``, puts ``n_cpus`` inside ``sampling``, carries an
    extra ``interfoam`` block, and treats ``case_template_dir`` as a directory
    **copied per run** rather than referenced in place.
    """
    c = cfg.calibration
    names = [_canonical(p.name) for p in parameters]
    ranges = [[p.min, p.max] for p in parameters]
    model_dir = Path(out_dir) / "simulations"

    def pylist(items):
        return "[" + ", ".join(repr(str(i)) for i in items) + "]"

    text = f'''"""HydroBayesCal OpenFOAM config generated by axqua for case '{cfg.name}'.

Run a surrogate-assisted Bayesian calibration with:
    python bal_openfoam.py --config {Path(out_dir) / "config_OpenFOAM.py"}

The case at 'case_template_dir' is COPIED for every run; it is a rigid-lid,
coarsened variant of the production case (see
axqua.solvers.openfoam.calibration.campaign_config), not the case that
openfoam_preprocessing.py builds.
"""

import os

paths = {{
    'case_template_dir':         {str(template)!r},
    'model_dir':                 {str(model_dir)!r},
    'res_dir':                   {str(out_dir)!r},
    'calibration_pts_file_path': {str(csv)!r},
}}

simulation = {{
    'solver_name':           {cfg.openfoam.solver!r},
    # MPI ranks per run. Distinct from sampling['n_cpus'], which is the surrogate
    # layer's own CPU budget.
    'n_processors':          {n_processors},
    'results_filename_base': 'results_interfoam',
    'control_file':          'system/controlDict',
    # Extraction averages this many trailing WRITE TIMES, not time steps. It damps
    # the residual unsteadiness of a run marched to steady state, and is the
    # OpenFOAM analogue of taking the last converged SELAFIN frame.
    'n_avg_timesteps':       {n_avg_timesteps},
}}

interfoam = {{
    'alpha_water_name':    'alpha.water',
    'water_surface_alpha': 0.5,
    # The case's own bed minimum [m a.s.l.], not 0.0: this reach is at real
    # elevation, so a zero datum would make an extracted depth meaningless.
    'reference_z':         {_bed_datum(cfg, template):.4f},
}}

calibration = {{
    # 'ks' goes to the rough-wall BC in 0/nut; the k-epsilon coefficients go to
    # the kEpsilonCoeffs subdictionary of constant/momentumTransport.
    'parameters':   {pylist(names)},
    'param_values': {ranges},
    'extraction_quantities':  {pylist(extraction_quantities)},
    # Spelled exactly as OpenFOAMModel.EXTRACTABLE_QUANTITIES has them: that
    # membership test is case-SENSITIVE, unlike the CSV column lookup.
    'calibration_quantities': {pylist(calibration_quantities)},
    'measurement_error':      {c.measurement_error},
    # 0.0 while include_surrogate_error is True, or the emulator's uncertainty
    # would be counted twice.
    'gpe_error':              0.0,
    'model_structural_error': 0.0,
    'dict_output_name': 'extraction-data',
}}

sampling = {{
    'n_cpus':    {n_processors},
    'init_runs': {init_runs if init_runs is not None else c.init_runs},
    'max_runs':  {max_runs if max_runs is not None else c.max_runs},
    'parameter_distribution':    'uniform',
    'parameter_sampling_method': {c.parameter_sampling_method!r},
    'adaptive_init_runs': {bool(c.adaptive_init_runs)},
    'init_runs_min':      {c.init_runs_min!r},
    'tp_selection_criteria': 'dkl',
    'eval_steps':     1,
    # prior_samples drives a (prior_samples x prior_samples) dense covariance PER
    # calibration location in gpytorch. With {len(names)} parameter(s) a few thousand
    # is ample, and 25000 will OOM a 16 GB box.
    'prior_samples':  {c.prior_samples},
    'mc_samples_al':  2000,
    'mc_exploration': 1000,
    'gp_library':     {c.gp_library!r},
    'include_surrogate_error': True,
    'bal_exploration_tradeoff': {c.bal_exploration_tradeoff!r},
}}

execution = {{
    'complete_bal_mode':      True,
    'only_bal_mode':          {bool(only_bal_mode)},
    'delete_complex_outputs': True,
    'validation':             False,
    'user_param_values':      False,
}}
'''
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "config_OpenFOAM.py"
    path.write_text(text)
    return path


def stage_openfoam_driver(out_dir: Path, *, checkout: Path | None = None) -> Path:
    """Stage ``bal_openfoam.py`` beside the emitted config."""
    return hbc.stage_driver(out_dir, "bal_openfoam.py", checkout=checkout,
                            replacements=_DRIVER_PATCHES,
                            required=tuple(_DRIVER_PATCHES))


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def _velocity_csv(cfg: Config, campaign: Config, *, out_dir: Path,
                  calibration_quantities: Sequence[str],
                  extraction_quantities: Sequence[str],
                  vel_err_floor: float,
                  vertical_err_floor: float,
                  z_mode: str = "model-relative", state=None) -> Path:
    """Compile the ground truth into the component-velocity CSV.

    A separate file from the TELEMAC ``measurements-calibration.csv``: the same 30
    points, but ``U_x``/``U_y``/``U_z`` columns instead of ``SCALAR VELOCITY``.
    """
    # build_calibration_csv reads the quantities off cfg.calibration, so give it a
    # copy carrying the OpenFOAM names rather than mutating the caller's config.
    scratch = copy.deepcopy(cfg)
    scratch.calibration.calibration_quantities = list(calibration_quantities)
    scratch.calibration.extraction_quantities = list(extraction_quantities)
    floors: dict[str, float] = {}
    for qty in calibration_quantities:
        upper = str(qty).upper()
        if upper == "U_Z":
            floors[qty] = vertical_err_floor
        elif upper in ("U_X", "U_Y", "U_MAG"):
            floors[qty] = vel_err_floor
    # z_mode "model-relative" places each target at its measured RELATIVE depth in
    # the model's own column; "measured" writes the surveyed z, which is what the
    # 2D path wants (where z is ignored) and what silently broke the 3D one.
    resolver = None
    if z_mode == "model-relative":
        resolver = model_relative_z_resolver(
            campaign, out_dir=Path(out_dir), state=state,
            cache=Path(campaign.openfoam_case_dir).parent / "column-geometry.npz")
    elif z_mode != "measured":
        raise SystemExit(f"unknown z_mode {z_mode!r}; expected "
                         "'model-relative' or 'measured'")

    path = build_calibration_csv(
        scratch, floors=floors, z_resolver=resolver,
        path=Path(out_dir) / "measurements-calibration-openfoam.csv")
    if path is None:
        raise SystemExit(
            "no ground truth to calibrate against. Set ground_truth.sources (or "
            "ground_truth.measurements / ground_truth.targets) in case-config.yml.")
    return path


def run_openfoam_calibration(
        cfg: Config, *,
        calibration_quantities: Sequence[str] = ("U_x", "U_y", "U_z"),
        extraction_quantities: Sequence[str] = ("U_x", "U_y", "U_z", "TKE"),
        parameters: Sequence[str] | None = None,
        cell_size_factor: float = 4.0,
        end_time: float = 600.0,
        write_interval: float = 20.0,
        n_avg_timesteps: int = 5,
        n_processors: int | None = None,
        init_runs: int | None = None,
        max_runs: int | None = None,
        vel_err_floor: float = campaigns.VELOCITY_ERROR_FLOOR,
        vertical_err_floor: float = VERTICAL_ERROR_FLOOR,
        launch_mode: str = "prepare",
        rebuild: bool = False,
        checkout: Path | None = None,
        state=None,
        force: bool = False,
        ctx: Any = None) -> int:
    """Calibrate the OpenFOAM case against measured velocity components.

    *launch_mode*:

    ``prepare``
        build the template, write the CSV and config, stage the driver, print the
        command and stop. Nothing is run.
    ``smoke``
        the same, then a tiny isolated 3-run campaign on a short template. This is
        the gate to run before committing solver hours: it exercises the whole
        chain - parameter routing into the dictionaries, the solver launch, the
        field extraction, the per-run accumulation - in minutes.
    ``run``
        launch the full campaign.
    ``resume``
        launch with ``only_bal_mode``, reusing a completed initial design.
    """
    if launch_mode not in ("prepare", "smoke", "run", "resume"):
        raise SystemExit(f"unknown launch_mode {launch_mode!r}; expected one of "
                         "prepare, smoke, run, resume")
    cfg.ensure_dirs()

    n_processors = int(n_processors if n_processors is not None
                       else cfg.openfoam.n_processors or 1)

    if state is None:
        # The 2D seed is obtained HERE, by the adapter, and handed down - the
        # backend must not reach into axqua.prerun (it imports the TELEMAC
        # backend transitively). Both the case build and the target placer need
        # the same seed, or they describe different meshes.
        from axqua.prerun import ensure_seed
        from axqua.solvers.openfoam.hotstart import load_hotstart
        seed = ensure_seed(cfg)
        for line in seed.summary():
            log.info("%s", line)
        if seed.ok:
            state = load_hotstart(cfg, seed.path)
    if not force:
        # Before any solver time: refuse targets whose elevations cannot be right.
        from axqua.ground_truth_qa import check_ground_truth_elevations
        from axqua.ground_truth_qa import report as report_elevations
        report_elevations(check_ground_truth_elevations(cfg), strict=True,
                          source="before the OpenFOAM calibration")

    picked = openfoam_parameters(cfg, names=parameters)
    log.info("calibrating %s against %s",
             ", ".join(p.name for p in picked), ", ".join(calibration_quantities))

    campaign = campaign_config(
        cfg, cell_size_factor=cell_size_factor,
        end_time=(30.0 if launch_mode == "smoke" else end_time),
        write_interval=(10.0 if launch_mode == "smoke" else write_interval),
        n_processors=n_processors,
        n_avg_timesteps=(1 if launch_mode == "smoke" else n_avg_timesteps))
    check_turbulence(campaign, picked)

    out_dir = Path(cfg.calibration_dir) / CAMPAIGN_SUBDIR
    if launch_mode == "smoke":
        out_dir = out_dir / "smoke"
        campaign.openfoam_dir = out_dir / "case-template"
    out_dir.mkdir(parents=True, exist_ok=True)

    template = stage_case_template(
        campaign, state=state, rebuild=rebuild, parameters=picked,
        n_avg_timesteps=(1 if launch_mode == "smoke" else n_avg_timesteps))
    csv = _velocity_csv(cfg, campaign, out_dir=out_dir,
                        calibration_quantities=calibration_quantities,
                        extraction_quantities=extraction_quantities,
                        vel_err_floor=vel_err_floor,
                        vertical_err_floor=vertical_err_floor, state=state)
    config_path = emit_openfoam_config(
        campaign, template=template, csv=csv, parameters=picked,
        calibration_quantities=calibration_quantities,
        extraction_quantities=extraction_quantities,
        out_dir=out_dir,
        n_avg_timesteps=(1 if launch_mode == "smoke" else n_avg_timesteps),
        n_processors=n_processors,
        init_runs=(3 if launch_mode == "smoke" else init_runs),
        max_runs=(3 if launch_mode == "smoke" else max_runs),
        only_bal_mode=(launch_mode == "resume"))
    driver = stage_openfoam_driver(out_dir, checkout=checkout)

    print(f"\ncalibration inputs in {out_dir}:")
    for path in (template, csv, config_path, driver):
        print(f"  {path}")

    if launch_mode == "prepare":
        print("\nprepared only. Launch it with:\n"
              f"  python {driver.name} --config {config_path}\n"
              "(or re-run this script with launch_mode='run')")
        return 0

    if launch_mode == "resume":
        design = (out_dir / "auto-saved-results-HydroBayesCal" / "restart_data"
                  / "initial-model-outputs.json")
        if not design.is_file():
            raise SystemExit(
                f"cannot resume: no completed initial design at {design}. Run the "
                "campaign with launch_mode='run' first.")

    if not force and hbc.solver_running(cfg.openfoam.solver, Path(campaign.openfoam_case_dir)):
        raise SystemExit(
            f"a {cfg.openfoam.solver} run from this case is already active. Two "
            "calibrations sharing one case fight over the dictionaries each run "
            "rewrites. Wait for it, or pass force=True if you know it is unrelated.")

    environment = hbc.solver_environment(cfg.openfoam.environment, cfg.openfoam.bashrc)
    note = ("OpenFOAM calibration smoke test" if launch_mode == "smoke"
            else "OpenFOAM Bayesian calibration")
    return hbc.launch_driver(environment, driver, config_path, note=note,
                             env={"HBC_MPI_LAUNCHER": environment.mpi_launcher or "mpirun"})


# --------------------------------------------------------------------------- #
# using the posterior
# --------------------------------------------------------------------------- #
def read_posterior(campaign_dir: Path, *, statistic: str = "map") -> dict[str, float]:
    """The calibrated parameter values from a finished campaign.

    Reads ``BAL_dictionary.pkl``, which carries the posterior sample of every BAL
    iteration alongside ``calibration_parameters`` and ``param_values`` - so the
    file is self-describing and the parameter order cannot be mismatched here.

    *statistic* is ``map`` (the highest-density point of the posterior sample,
    approximated by the sample mode over a histogram) or ``mean``. The MAP is the
    default because a roughness posterior is often skewed, and its mean can sit
    where the sample has little mass.
    """
    import numpy as np

    path = _find_bal_dictionary(Path(campaign_dir))
    import pickle

    with open(path, "rb") as fh:
        bal = pickle.load(fh)

    names = list(bal["calibration_parameters"])
    posteriors = [p for p in bal["posterior"] if p is not None and len(p)]
    if not posteriors:
        raise SystemExit(
            f"{path} carries no posterior sample yet - the campaign has not reached "
            "its first BAL iteration.")
    sample = np.atleast_2d(posteriors[-1])

    out: dict[str, float] = {}
    for i, name in enumerate(names):
        column = sample[:, i]
        if statistic == "mean":
            out[name] = float(column.mean())
        else:
            counts, edges = np.histogram(column, bins=min(50, max(5, column.size // 20)))
            peak = int(counts.argmax())
            out[name] = float(0.5 * (edges[peak] + edges[peak + 1]))
    return out


def _find_bal_dictionary(campaign_dir: Path) -> Path:
    candidates = sorted(campaign_dir.rglob("BAL_dictionary.pkl"))
    if not candidates:
        raise SystemExit(
            f"no BAL_dictionary.pkl under {campaign_dir}. Run the calibration "
            "first (run_Bayes_cal_openfoam.py --run).")
    return candidates[-1]


def apply_posterior(cfg: Config, values: Mapping[str, float], *,
                    bound_margin: float = 0.05) -> list[str]:
    """Write calibrated values into *cfg*, and report what changed.

    Refuses when a calibrated value sits within *bound_margin* of its own prior
    bound: that is the signature of an answer lying **outside** the range that was
    allowed, and adopting it silently would present a clipped estimate as a result.
    """
    declared = {p.name.lower(): p for p in merged_parameters(cfg)}
    lines: list[str] = []
    pinned: list[str] = []

    for raw, value in values.items():
        name = _canonical(raw)
        prior = declared.get(raw.lower()) or declared.get(name.lower())
        if prior is not None:
            span = float(prior.max) - float(prior.min)
            if span > 0 and (value - prior.min < bound_margin * span
                             or prior.max - value < bound_margin * span):
                pinned.append(f"{name} = {value:.4g} (prior [{prior.min:g}, "
                              f"{prior.max:g}])")

        if name == "ks":
            before = cfg.openfoam.friction_ks
            cfg.openfoam.friction_ks = float(value)
        else:
            attr = {"Cmu": "kepsilon_cmu", "C1": "kepsilon_c1", "C2": "kepsilon_c2",
                    "sigmak": "kepsilon_sigmak", "sigmaEps": "kepsilon_sigma_eps"}.get(name)
            if attr is None:
                log.warning("calibrated parameter %r has nowhere to go in the "
                            "OpenFOAM config; ignored", raw)
                continue
            before = getattr(cfg.openfoam, attr)
            setattr(cfg.openfoam, attr, float(value))
        lines.append(f"  {name:10s} {before:>10.4g} -> {value:.4g}")

    if pinned:
        raise SystemExit(
            "the calibrated value sits at the edge of its prior for:\n    "
            + "\n    ".join(pinned)
            + "\nThat usually means the best value lies OUTSIDE the range the "
              "campaign was allowed to explore, so this is a clipped estimate "
              "rather than a result. Widen the prior in case-config.yml and "
              "re-run, or pass bound_margin=0 to adopt it deliberately.")
    return lines


# --------------------------------------------------------------------------- #
# placing targets in the model's own column
# --------------------------------------------------------------------------- #
def model_relative_z_resolver(campaign: Config, *, out_dir: Path,
                              margin: float = 0.05,
                              min_depth: float = 0.05,
                              cache: Path | None = None,
                              state=None):
    """A ``z_resolver`` that puts each target at its measured RELATIVE depth.

    ``z = bed_model(x, y) + f * depth_model(x, y)``, with ``f`` derived from the
    measurement (:func:`axqua.ground_truth.relative_height`). Nothing but ``x``,
    ``y`` and a *ratio* comes from the survey, so no vertical survey error can
    reach the target - which is the whole point, since on this reach the survey
    carried an un-subtracted rover-pole height, the measurement height was never
    added, and the DEM has a depth-proportional bias.

    Writes ``target-placement.csv`` beside the calibration inputs so every target's
    position is auditable rather than implicit.
    """
    from axqua.ground_truth import relative_height
    from axqua.model_column import openfoam_column

    out_dir = Path(out_dir)

    def resolve(df: pd.DataFrame) -> np.ndarray:
        column = openfoam_column(df["x"].to_numpy(dtype=float),
                                 df["y"].to_numpy(dtype=float),
                                 cfg=campaign, cache=cache, state=state)
        f, f_source = relative_height(df)
        z, clamped = column.z_at(f.to_numpy(dtype=float), margin=margin)
        usable = column.usable(min_depth=min_depth)

        audit = pd.DataFrame({
            "id": np.arange(1, len(df) + 1),
            "x": column.x, "y": column.y,
            "f": np.round(np.asarray(f, dtype=float), 4),
            "f_source": f_source,
            "bed_model": np.round(column.bed, 4),
            "wse_model": np.round(column.wse, 4),
            "h_model": np.round(column.depth, 4),
            "z_target": np.round(z, 4),
            "height_above_bed": np.round(z - column.bed, 4),
            "inside": column.inside,
            "clamped": clamped,
            "usable": usable,
            "source": column.source,
        })
        audit["status"] = np.where(
            ~column.inside, "outside-domain",
            np.where(~usable, f"column shallower than {min_depth} m",
                     np.where(clamped, "clamped to the margin", "ok")))
        out_dir.mkdir(parents=True, exist_ok=True)
        audit.to_csv(out_dir / "target-placement.csv", index=False)

        log.info("target placement: %d/%d usable (f from %s, column from %s)",
                 int(usable.sum()), len(df), f_source, column.source)
        _assert_targets_placed(audit)
        return z

    return resolve


def _assert_targets_placed(placement: pd.DataFrame, *, min_points: int = 6,
                           min_fraction: float = 0.5) -> None:
    """Refuse a campaign whose targets do not land in real water.

    This is not defence in depth - it is the only defence. HydroBayesCal's
    OpenFOAM binding finds the nearest extraction point with **no distance
    cutoff**, so a target metres from the water returns a plausible number instead
    of an error, and the whole campaign trains on it.
    """
    usable = placement["usable"].to_numpy(dtype=bool)
    n_ok, n = int(usable.sum()), len(placement)
    if n_ok >= max(min_points, int(np.ceil(min_fraction * n))):
        return
    bad = placement.loc[~usable, ["id", "status"]]
    detail = ", ".join(f"{int(r.id)} ({r.status})" for r in bad.itertuples())
    raise SystemExit(
        f"only {n_ok} of {n} calibration targets land in a usable model column "
        f"(need at least {max(min_points, int(np.ceil(min_fraction * n)))}).\n"
        f"unusable: {detail}\n"
        "See target-placement.csv. Either the measurement points lie outside the "
        "modelled wetted extent, or the model is far too shallow there. Do NOT run "
        "the campaign: HydroBayesCal's nearest-point search has no distance "
        "cutoff, so it will silently return values from wherever it can reach.")
