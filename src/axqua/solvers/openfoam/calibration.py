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
import json
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
from axqua.solvers.openfoam.layout import PREVIOUS_RUN
from axqua.solvers.openfoam.spec import OPENFOAM_PARAMETERS

log = logging.getLogger("axqua")

#: Sub-directory of ``calibration_dir`` holding everything this module produces.
CAMPAIGN_SUBDIR = "openfoam"

#: Error floor [m/s] on the **vertical** velocity component. Deliberately coarser
#: than the horizontal floor: a side-looking ADV's ``w`` at 0.6*h is dominated by
#: mounting tilt and by the probe's own beam geometry, so the raw per-point error
#: claims a precision the measurement does not have. This keeps U_z in the
#: likelihood as a weak constraint on the secondary circulation instead of as the
#: loudest voice in it. It is a judgment, not a measurement - drop U_z from
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
                    turbulence: str = "kEpsilon",
                    mode: str = "rigid-lid",
                    n_layers: int | None = None,
                    spinup_time: float | None = None,
                    air_velocity_cap: float | None = None,
                    min_column_height: float | None = None,
                    auto_bed_layer: bool | None = None) -> Config:
    """A copy of *cfg* describing the **campaign** case, not the production case.

    A deep copy rather than a mutation: the caller's config still describes the
    full-resolution two-phase case that the verification run rebuilds, so the two
    never have to be reconciled and running a campaign cannot quietly change what
    ``openfoam_preprocessing.py`` would build afterwards.

    *mode* defaults to ``rigid-lid``, which is what makes a surrogate affordable:
    about 90% of a two-phase case's cells are air and a campaign needs tens of
    runs. Pass ``vof`` where the rigid lid is not admissible - it is a slip WALL,
    so wherever the prescribed surface steps it converts head into velocity, and
    on a shallow reach that is most of the domain. The remaining overrides exist
    because a two-phase campaign has to be cheapened along different axes than a
    rigid-lid one (fewer layers, a tighter air cap) and those are exactly the
    numbers a production config should not carry.
    """
    c = copy.deepcopy(cfg)
    c.openfoam.mode = mode
    c.openfoam.cell_size_factor = float(cell_size_factor)
    c.openfoam.turbulence = turbulence
    c.openfoam.end_time = float(end_time)
    if spinup_time is not None:
        c.openfoam.spinup_time = float(spinup_time)
    elif mode == "rigid-lid":
        # No interface to settle under a rigid lid, so stages() yields a single
        # stage and a spin-up would be dead time in every one of tens of runs.
        c.openfoam.spinup_time = 0.0
    # else: keep the case's own spin-up. It is run ONCE by _prespin_template and
    # promoted into 0/, never per run - and OpenFoam.validate() rejects
    # end_time <= spinup_time, so zeroing it here would make mode="vof" a hard
    # failure rather than the cheaper campaign it is meant to be.
    for name, value in (("n_layers", n_layers),
                        ("air_velocity_cap", air_velocity_cap),
                        ("min_column_height", min_column_height),
                        ("auto_bed_layer", auto_bed_layer)):
        if value is not None:
            setattr(c.openfoam, name, value)
    c.openfoam.write_interval = float(write_interval)
    c.openfoam.n_processors = int(n_processors)
    # Only the trailing window is ever read. Keeping every write time would put
    # dozens of binary time directories per run on disk purely to delete them.
    c.openfoam.purge_write = int(n_avg_timesteps) + 1
    c.openfoam_dir = Path(cfg.calibration_dir) / CAMPAIGN_SUBDIR / "case-template"
    return c


def _clean_template(case_dir: Path) -> None:
    """Strip a built case down to what is worth copying once per run."""
    for pattern in ("processor*", "VTK", "postProcessing", "log.*", "*.log",
                    "system/decomposeParDict-*",          # the dictionaries of earlier runs
                    PREVIOUS_RUN):                        # what a rebuild of the template set aside
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
        mt = case_dir / "constant" / "turbulenceProperties"
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
    # Read endTime and writeInterval from the ACTIVATED controlDict, never from
    # the config. HydroBayesCal copies this directory and makes ONE solver call,
    # so whatever system/controlDict says is what every run actually does. A
    # two-phase case whose spin-up stage was activated by mistake ends at 30 s
    # while the config still claims the full end_time - which this catches by
    # name instead of leaving as dozens of runs averaged over a spin-up.
    def _number(key: str) -> float | None:
        m = re.search(rf"^\s*{key}\s+([0-9.eE+-]+)\s*;", ctext, re.M)
        return float(m.group(1)) if m else None

    end_time = _number("endTime")
    interval = _number("writeInterval")
    if end_time is None or interval is None or interval <= 0:
        raise SystemExit(
            f"{control} has no readable endTime/writeInterval, so the number of "
            "writes the extraction averages over cannot be checked.")
    if abs(end_time - cfg.openfoam.end_time) > 1e-9:
        raise SystemExit(
            f"{control} ends at {end_time:g} s but the campaign config says "
            f"{cfg.openfoam.end_time:g} s. The wrong stage is activated - a "
            "two-phase case must have its single production stage activated "
            "(dicts.stages(cfg, single=True)), not its spin-up.")
    n_writes = int(end_time // interval)
    if n_writes < n_avg_timesteps:
        raise SystemExit(
            f"the template writes {n_writes} time(s) "
            f"(endTime {end_time:g} s / writeInterval {interval:g} s) but "
            f"extraction averages the last {n_avg_timesteps}. Lower "
            "n_avg_timesteps or lengthen the run.")

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
        # natural center for the ks prior.
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
    # HydroBayesCal decomposes every copy with the template's own file, so it names the ranks of this config, also for a template reused from a campaign on other ranks.
    dicts.write_decomposition(dest, int(cfg.openfoam.n_processors or 1))
    # A two-phase template is spun up ONCE, here, and the settled state promoted
    # into 0/ - see _prespin_template. Must happen before _clean_template's
    # sibling below would have removed the produced time directory, and before
    # the production stage is activated.
    _prespin_template(dest, cfg)

    # Regenerate system/ from THIS config, so a reused template cannot carry an
    # end_time or Courant number from an earlier campaign. single=True: HBC makes
    # ONE solver call per run, so a two-phase case must present a single
    # full-length production stage rather than its spin-up.
    dicts.activate(dest, dicts.stages(cfg, single=True)[0].name, cfg, single=True)
    _assert_template(dest, cfg, parameters, n_avg_timesteps)
    return dest


#: Marker written into a pre-spun template so a reused one is not spun up twice.
_PRESPIN_MARKER = ".axqua-prespun"


def _prespin_template(case_dir: Path, cfg: Config) -> None:
    """Settle the interface once, into the template's ``0/``.

    HydroBayesCal copies the template and makes **one** solver call per run, so a
    two-phase case cannot use the normal two-stage sequence: it would run only the
    spin-up. Running every calibration run at spin-up settings instead would be
    both slower and wrong.

    So the spin-up is run **once**, at the nominal ``ks``, and its final time
    directory is promoted to ``0/``. The cost is one short run for the whole
    campaign, against roughly three times the steps per run if each spun itself up.

    The approximation this buys - **every run starts from the same settled
    interface**, rather than from one settled at its own roughness - is the same
    class as the single reference column in :func:`model_relative_z_resolver`, and
    is registered as a named caveat (``spun-up-from-one-state``) rather than left
    implicit. A rigid-lid case has no interface and is skipped entirely.
    """
    of = cfg.openfoam
    if of.mode == "rigid-lid" or float(getattr(of, "spinup_time", 0.0) or 0.0) <= 0:
        return
    marker = case_dir / _PRESPIN_MARKER
    if marker.is_file():
        log.info("template already pre-spun (%s); not repeating it", marker.name)
        return

    from axqua.solvers.openfoam.runtime import OpenFoamRuntime

    log.info("pre-spinning the two-phase template for %g s (once for the whole "
             "campaign)", of.spinup_time)
    runtime = OpenFoamRuntime(of)
    nprocs = int(getattr(of, "n_processors", 1) or 1)
    proc = runtime.run_stage(case_dir, "spinup", end_time=of.spinup_time, cfg=cfg)  # splits the case for nprocs
    if getattr(proc, "returncode", 0) != 0:
        raise SystemExit(
            f"the pre-spin of the calibration template failed (exit "
            f"{proc.returncode}); see log.{of.solver} in {case_dir}. Every run "
            "would otherwise start from an unsettled interface.")
    if nprocs > 1:
        # A parallel run leaves its result in processor*/; without this there is
        # no top-level time directory to promote and the spin-up is silently lost.
        runtime.reconstruct(case_dir)
        for path in case_dir.glob("processor*"):
            shutil.rmtree(path, ignore_errors=True)

    produced = sorted(
        (p for p in case_dir.iterdir()
         if p.is_dir() and re.fullmatch(r"[0-9]+(\.[0-9]+)?", p.name)
         and float(p.name) > 0.0),
        key=lambda p: float(p.name))
    if not produced:
        raise SystemExit(
            f"the pre-spin produced no time directory in {case_dir}. Without one "
            "there is no settled interface to promote into 0/, and every "
            "calibration run would start from the raw depth-averaged hotstart.")

    settled = produced[-1]
    zero = case_dir / "0"
    if zero.exists():
        shutil.rmtree(zero)
    settled.rename(zero)
    log.info("promoted the pre-spun state t=%s to 0/", settled.name)
    for leftover in produced[:-1]:
        shutil.rmtree(leftover, ignore_errors=True)
    # The spin-up's own logs and postProcessing would otherwise be copied into
    # every run, and its monitor files would be appended to rather than started.
    for pattern in ("log.*", "postProcessing", "VTK"):
        for path in case_dir.glob(pattern):
            shutil.rmtree(path, ignore_errors=True) if path.is_dir() \
                else path.unlink(missing_ok=True)
    marker.write_text(
        f"pre-spun {of.spinup_time:g} s at ks={of.friction_ks:g} m\n"
        "Delete this file (or pass rebuild=True) to spin up again.\n")


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
            "constant/turbulenceProperties carries no kEpsilonCoeffs subdictionary, so "
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
                         only_bal_mode: bool = False,
                         flows: Sequence[Mapping[str, Any]] | None = None) -> Path:
    """Write ``config_OpenFOAM.py`` for the staged ``bal_openfoam.py``.

    With *flows* (``name``, ``case_template_dir``, ``calibration_pts_file_path`` per
    calibration situation) it writes ``config_OpenFOAM_multiflow.py`` for
    ``bal_openfoam_multiflow.py`` instead, whose ``multiflow`` block names them; *template*
    and *csv* are then those of the first situation.

    Deliberately not a parameterisation of :func:`axqua.calibration.emit_hbc_config`:
    the OpenFOAM schema uses ``simulation`` where TELEMAC uses
    ``hydrodynamic_simulation``, puts ``n_cpus`` inside ``sampling``, carries an
    extra ``interfoam`` block, and treats ``case_template_dir`` as a directory
    **copied per run** rather than referenced in place.
    """
    c = cfg.calibration
    from axqua.calibration import run_budget

    planned, ceiling, first = run_budget(c, len(parameters))
    init_runs = planned if init_runs is None else int(init_runs)
    if max_runs is None:
        # no convergence loop on this path: a fixed number of active-learning iterations
        from axqua.calibration import default_iterations

        max_runs = ceiling if ceiling is not None else init_runs + default_iterations()
    max_runs = int(max_runs)
    first = min(first, init_runs)
    names = [_canonical(p.name) for p in parameters]
    ranges = [[p.min, p.max] for p in parameters]
    model_dir = Path(out_dir) / "simulations"

    def pylist(items):
        return "[" + ", ".join(repr(str(i)) for i in items) + "]"

    # Describe the mode the campaign ACTUALLY runs in. Hard-coding "rigid-lid"
    # here made the generated file state the opposite of the truth for a
    # two-phase campaign - in the one document someone reads to find out what
    # produced a posterior.
    mode = cfg.openfoam.mode
    if mode == "rigid-lid":
        mode_note = ("a rigid-lid, coarsened variant of the production case: the "
                     "free surface is PRESCRIBED from the 2D result rather than "
                     "solved, so a roughness error that would show as a "
                     "surface-slope error is absorbed into the velocity field")
    else:
        mode_note = ("a coarsened two-phase (VOF) variant of the production case, "
                     "pre-spun once into 0/ so that every run starts from the "
                     "same settled interface")

    name = "config_OpenFOAM_multiflow.py" if flows else "config_OpenFOAM.py"
    driver_name = "bal_openfoam_multiflow.py" if flows else "bal_openfoam.py"
    text = f'''"""HydroBayesCal OpenFOAM config generated by axqua for case '{cfg.name}'.

Run a surrogate-assisted Bayesian calibration with:
    python {driver_name} --config {Path(out_dir) / name}

The case at 'case_template_dir' is COPIED for every run. It is {mode_note}
(see axqua.solvers.openfoam.calibration.campaign_config) - NOT the case that
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
    # the kEpsilonCoeffs subdictionary of constant/turbulenceProperties.
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
    'init_runs': {init_runs},
    'max_runs':  {max_runs},
    'parameter_distribution':    'uniform',
    'parameter_sampling_method': {c.parameter_sampling_method!r},
    'adaptive_init_runs': {bool(c.adaptive_init_runs)},
    'init_runs_min':      {first},
    'tp_selection_criteria': 'dkl',
    'eval_steps':     1,
    # prior samples the surrogate is predicted at ({len(names)} parameter(s)); a sharp
    # posterior keeps few of them, so raise calibration.prior_samples when few are
    # accepted.
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
    if flows:
        entries = ",\n".join(
            "        {{'name': {n!r},\n"
            "         'case_template_dir': {t!r},\n"
            "         'calibration_pts_file_path': {c!r}}}".format(
                n=str(f["name"]), t=str(f["case_template_dir"]),
                c=str(f["calibration_pts_file_path"]))
            for f in flows)
        text += f'''
# One case template and one calibration-points CSV per calibration situation; each
# runs in <model_dir>/flow-<name>. paths['case_template_dir'] and
# paths['calibration_pts_file_path'] above are the first situation's.
multiflow = {{
    'flows': [
{entries},
    ],
}}
'''
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
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
                  z_mode: str = "model-relative", state=None,
                  path: Path | None = None, point_filter=None,
                  min_points: int = 6) -> Path:
    """Compile the ground truth into the component-velocity CSV.

    A separate file from the TELEMAC ``measurements-calibration.csv``: the same
    points, but with the OpenFOAM names of the quantities (``U_MAG``, ``U_x``, ...).
    *path* overrides its location, *point_filter* leaves out points
    (:func:`axqua.calibration.build_calibration_csv`).
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
            cache=Path(campaign.openfoam_case_dir).parent / "column-geometry.npz",
            min_points=min_points)
    elif z_mode != "measured":
        raise SystemExit(f"unknown z_mode {z_mode!r}; expected "
                         "'model-relative' or 'measured'")

    path = build_calibration_csv(
        scratch, floors=floors, z_resolver=resolver, point_filter=point_filter,
        path=Path(path) if path is not None
        else Path(out_dir) / "measurements-calibration-openfoam.csv")
    if path is None:
        raise SystemExit(
            "no ground truth to calibrate against. Set ground_truth.sources (or "
            "ground_truth.measurements / ground_truth.targets) in case-config.yml.")
    table = pd.read_csv(path)
    lost = table["z"].isna().to_numpy()
    if lost.any():
        log.warning("%d of %d measurement points lie outside the water of the OpenFOAM "
                    "lattice (shallow margins, columns the coarse lattice drops) and are "
                    "left out: %s (see target-placement.csv)", int(lost.sum()), len(table),
                    ", ".join(str(int(i)) for i in table.loc[lost, "id"]))
        table = table.loc[~lost].reset_index(drop=True)
        table["id"] = range(1, len(table) + 1)
        table.to_csv(path, index=False)
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
# calibration situations: one parameter set against several discharges
# --------------------------------------------------------------------------- #
#: Sub-folder of :data:`CAMPAIGN_SUBDIR` for a calibration at several discharges.
MULTIFLOW_SUBDIR = "multiflow"
#: The calibration quantity of the situations: the horizontal speed at 0.6 h above
#: the riverbed. FlowTracker components are in the frame of the probe, which was
#: turned into the local flow at each vertical, so they cannot be compared with the
#: east and north components of the model (the user, 2026-11-17).
SITUATION_QUANTITIES = ("U_MAG",)
SITUATION_EXTRACTION = ("U_MAG", "U_x", "U_y", "U_z", "TKE")
#: The HydroBayesCal release that brings ``bal_openfoam_multiflow.py``.
HBC_MULTIFLOW_MINIMUM = "1.14.0"


def require_multiflow_driver() -> None:
    """Refuse an installed HydroBayesCal that cannot calibrate at several discharges.

    Checked before the TELEMAC runs of the situations, which the job starts first; an unreadable version passes, and staging the driver still checks that it exists.
    """
    from axqua.core.errors import ConfigError

    installed = hbc.hbc_version()
    if hbc.hbc_too_old(installed, HBC_MULTIFLOW_MINIMUM):
        raise ConfigError(
            f"the installed HydroBayesCal {installed} cannot calibrate OpenFOAM at "
            "several discharges", subject="hydroBayesCal",
            remedy=f"Install HydroBayesCal {HBC_MULTIFLOW_MINIMUM} or newer "
                   "(pip install --upgrade hydroBayesCal).")


def _rating_at(cfg: Config, discharge: float, path: Path) -> None:
    """The outflow rating of a situation, as the TELEMAC run of that situation has it.

    A case without a measured stage-discharge curve derives its outflow level from the channel geometry for its own discharge; a situation derives its own for its discharge (as :func:`axqua.validation.with_boundaries_of` does for the TELEMAC run whose water surface the model follows), written to *path*.
    """
    from axqua.core.boundaries import outlet_level
    from axqua.workflow import synthesize_rating_if_missing

    given = cfg.boundaries.stage_discharge
    if outlet_level(cfg.outflow_condition) != "rating" or (given and Path(given).exists()):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    cfg.boundaries.stage_discharge = path
    synthesize_rating_if_missing(cfg, discharge)


def situation_config(cfg: Config, situation: Mapping[str, Any], folder: Path) -> Config:
    """A copy of *cfg* at the discharge of one calibration situation, with its measurements.

    *situation* is a mapping with ``name``, ``tag``, ``discharge`` and ``sources``
    (as :mod:`axqua.openfoam_situations` hands it over). The measurements are compiled
    into ``ground-truth-<tag>.xlsx`` in *folder*. Inflow lines with their own target
    flows keep their split and are scaled to the situation's total.
    """
    there = copy.deepcopy(cfg)
    q = float(situation["discharge"])
    there.boundaries.prescribed_flowrate = q
    there.situation_discharge = q          # read by case._inlet_discharges
    _rating_at(there, q, Path(folder) / f"rating-{situation['tag']}.csv")
    sources = list(situation.get("sources") or [])
    if sources:
        there.ground_truth.sources = sources
        there.ground_truth.targets = None
        there.ground_truth.measurements = Path(folder) / f"ground-truth-{situation['tag']}.xlsx"
    return there


def _template_record(folder: Path) -> Path:
    """What the campaign template of a situation was built from, beside it."""
    return Path(folder) / "case-template.json"


def _template_source(situation: Mapping[str, Any], cell_size_factor: float) -> dict:
    return {"seed": str(situation.get("seed")), "seed_stamp": situation.get("seed_stamp"),
            "discharge": float(situation["discharge"]),
            "cell_size_factor": float(cell_size_factor)}


def _template_outdated(folder: Path, situation: Mapping[str, Any], cell_size_factor: float,
                       *, resume: bool = False) -> bool:
    """Whether the campaign template of a situation was built from another water surface,
    discharge or lattice than this calibration has (a new TELEMAC run after the TELEMAC
    calibration changed, for example). Such a template is rebuilt; resuming over it is
    refused, because the runs done would no longer match the ones to come."""
    if not (Path(folder) / "case-template" / "constant" / "polyMesh" / "faces").is_file():
        return False
    try:
        built = json.loads(_template_record(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        built = None
    if built == _template_source(situation, cell_size_factor):
        return False
    if resume:
        raise SystemExit(
            f"cannot resume: the case template of {situation['name']} was built from "
            "another water surface, discharge or cell size factor than this calibration "
            "has. Run the calibration without resume.")
    log.info("the case template of %s was built from another water surface, discharge "
             "or cell size factor: rebuilding it", situation["name"])
    return True


def run_openfoam_situation_calibration(
        cfg: Config, situations: Sequence[Mapping[str, Any]], *,
        calibration_quantities: Sequence[str] = SITUATION_QUANTITIES,
        extraction_quantities: Sequence[str] = SITUATION_EXTRACTION,
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
        point_filter=None,
        force: bool = False,
        ctx: Any = None) -> int:
    """Calibrate the OpenFOAM case against the measurements of several situations.

    Every situation gets its own campaign template (rigid lid, coarse lattice, see
    :func:`campaign_config`), built at its discharge and seeded from its own 2D
    result (``seed``: the water surface the rigid lid follows, from a TELEMAC run at
    that discharge with the calibrated TELEMAC values), and its own points CSV.
    HydroBayesCal's ``bal_openfoam_multiflow.py`` runs every tested parameter set in
    every situation and fits them jointly. Everything goes to
    ``calibration-validation/openfoam/multiflow/`` (``.../smoke/`` for the smoke test).

    *point_filter* is called per situation with the copy of the config of that
    situation and the table of its points, and returns which to keep: the rules of the
    case that need its 2D mesh (inside the model, away from inflow and outflow lines)
    come in this way, since this backend does not read TELEMAC files. *launch_mode* is
    that of :func:`run_openfoam_calibration`.
    """
    from axqua.core.errors import ConfigError
    from axqua.solvers.openfoam.hotstart import load_hotstart

    if launch_mode not in ("prepare", "smoke", "run", "resume"):
        raise SystemExit(f"unknown launch_mode {launch_mode!r}; expected one of "
                         "prepare, smoke, run, resume")
    if not situations:
        raise ConfigError("the case names no calibration situations",
                          subject="calibration.situations",
                          remedy="Enter the measurement file and the discharge of each "
                                 "situation under Measurement files in the case settings.")
    tags = [str(s["tag"]) for s in situations]
    if len(set(tags)) != len(tags):
        raise ConfigError("two calibration situations have the same name",
                          subject="calibration.situations",
                          remedy="Give every situation a name of its own.")
    cfg.ensure_dirs()
    smoke = launch_mode == "smoke"
    n_processors = int(n_processors if n_processors is not None
                       else cfg.openfoam.n_processors or 1)
    picked = openfoam_parameters(cfg, names=parameters)
    log.info("calibrating %s against %s in %d situations",
             ", ".join(p.name for p in picked), ", ".join(calibration_quantities),
             len(situations))

    out_dir = Path(cfg.calibration_dir) / CAMPAIGN_SUBDIR / MULTIFLOW_SUBDIR
    if smoke:
        out_dir = out_dir / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    averaged = 1 if smoke else n_avg_timesteps

    flows, first, total_points = [], None, 0
    for situation in situations:
        tag = str(situation["tag"])
        seed = situation.get("seed")
        if not seed or not Path(seed).is_file():
            raise ConfigError(
                f"situation {situation['name']}: no 2D result to take the water surface "
                "from", subject=str(seed or tag),
                remedy="The rigid lid of the calibration runs follows the water surface "
                       "of a TELEMAC run at the discharge of the situation. Submit the "
                       "OpenFOAM calibration as a job, which runs it first.")
        folder = out_dir / tag
        there = situation_config(cfg, situation, out_dir)
        state = load_hotstart(there, seed)
        campaign = campaign_config(
            there, cell_size_factor=cell_size_factor,
            end_time=(30.0 if smoke else end_time),
            write_interval=(10.0 if smoke else write_interval),
            n_processors=n_processors, n_avg_timesteps=averaged)
        campaign.openfoam_dir = folder / "case-template"
        check_turbulence(campaign, picked)
        fresh = _template_outdated(folder, situation, cell_size_factor,
                                   resume=(launch_mode == "resume"))
        template = stage_case_template(campaign, state=state, rebuild=rebuild or fresh,
                                       parameters=picked, n_avg_timesteps=averaged)
        _template_record(folder).write_text(
            json.dumps(_template_source(situation, cell_size_factor), indent=1),
            encoding="utf-8")
        keep = (None if point_filter is None
                else (lambda df, there=there: point_filter(there, df)))
        csv = _velocity_csv(there, campaign, out_dir=folder,
                            calibration_quantities=calibration_quantities,
                            extraction_quantities=extraction_quantities,
                            vel_err_floor=vel_err_floor,
                            vertical_err_floor=vertical_err_floor, state=state,
                            path=out_dir / f"measurements-calibration-openfoam-{tag}.csv",
                            point_filter=keep, min_points=1)
        n_points = len(pd.read_csv(csv))
        total_points += n_points
        print(f"  {situation['name']}: Q = {float(situation['discharge']):g} m3/s, "
              f"{n_points} points, seed {Path(seed).name} -> {template}")
        flows.append({"name": tag, "case_template_dir": template,
                      "calibration_pts_file_path": csv})
        if first is None:
            first = (campaign, template, csv)

    if total_points < 6:
        raise ConfigError(
            f"only {total_points} measurement points of all situations lie in the water "
            "of the OpenFOAM model", subject=str(out_dir),
            remedy="See target-placement.csv of each situation. A finer lattice "
                   "(a smaller cell size factor) keeps more points at shallow margins.")
    campaign, template, csv = first
    config_path = emit_openfoam_config(
        campaign, template=template, csv=csv, parameters=picked,
        calibration_quantities=calibration_quantities,
        extraction_quantities=extraction_quantities, out_dir=out_dir,
        n_avg_timesteps=averaged, n_processors=n_processors,
        init_runs=(3 if smoke else init_runs), max_runs=(3 if smoke else max_runs),
        only_bal_mode=(launch_mode == "resume"), flows=flows)
    try:
        driver = hbc.stage_driver(out_dir, "bal_openfoam_multiflow.py", checkout=checkout,
                                  also=("bal_openfoam.py",), replacements=_DRIVER_PATCHES,
                                  required=tuple(_DRIVER_PATCHES))
    except FileNotFoundError as exc:
        raise ConfigError(
            "the installed HydroBayesCal cannot calibrate OpenFOAM at several discharges",
            subject="hydroBayesCal",
            remedy=f"Install HydroBayesCal {HBC_MULTIFLOW_MINIMUM} or newer "
                   "(pip install --upgrade hydroBayesCal).", cause=str(exc)) from exc

    print(f"\ncalibration inputs in {out_dir}:")
    for path in (config_path, driver):
        print(f"  {path}")
    if launch_mode == "prepare":
        print("\nprepared only. Launch it with:\n"
              f"  python {driver.name} --config {config_path}")
        return 0
    if launch_mode == "resume":
        missing = [tag for tag in tags
                   if not (out_dir / f"flow-{tag}" / "auto-saved-results-HydroBayesCal"
                           / "restart_data" / "initial-model-outputs.json").is_file()]
        if missing:
            raise SystemExit(
                f"cannot resume: no completed initial design of {', '.join(missing)} in "
                f"{out_dir}. Run the calibration without resume first.")
    if not force and hbc.solver_running(cfg.openfoam.solver, out_dir):
        raise SystemExit(
            f"a {cfg.openfoam.solver} run of this calibration is already active in "
            f"{out_dir}. Wait for it, or pass force=True if you know it is unrelated.")
    environment = hbc.solver_environment(cfg.openfoam.environment, cfg.openfoam.bashrc)
    note = ("OpenFOAM calibration smoke test at several discharges" if smoke
            else f"OpenFOAM Bayesian calibration at {len(flows)} discharges")
    code = hbc.launch_driver(environment, driver, config_path, note=note,
                             env={"HBC_MPI_LAUNCHER": environment.mpi_launcher or "mpirun"})
    if code == 0 and not smoke:
        _report(cfg, out_dir, ctx)
    return code


def _report(cfg: Config, folder: Path, ctx: Any = None) -> None:
    """The calibration report (posteriors, evidence; :mod:`axqua.calibration_report`);
    a failure is a warning, never a failed calibration."""
    from axqua.calibration_report import write_report

    try:
        written = write_report(cfg, folder)
    except Exception as exc:                      # noqa: BLE001 - the calibration is done
        log.warning("the calibration report could not be written: %s", exc)
        return
    if ctx is not None:
        for name, path in written.items():
            ctx.record(f"Calibration report: {name}", path,
                       kind="figure" if Path(path).suffix == ".png" else "file")


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
                              state=None, min_points: int = 6):
    """A ``z_resolver`` that puts each target at its measured RELATIVE depth.

    ``z = bed_model(x, y) + f * depth_model(x, y)``, with ``f`` derived from the
    measurement (:func:`axqua.ground_truth.relative_height`). Nothing but ``x``,
    ``y`` and a *ratio* comes from the survey, so no vertical survey error can
    reach the target - which is the whole point, since on this reach the survey
    carried an un-subtracted rover-pole height, the measurement height was never
    added, and the DEM has a depth-proportional bias.

    Writes ``target-placement.csv`` beside the calibration inputs so every target's
    position is auditable rather than implicit. A target outside the model's water
    (outside the lattice, or in a column shallower than *min_depth*) gets ``NaN``, and
    :func:`_velocity_csv` leaves it out: HydroBayesCal would otherwise read the nearest
    cell, wherever that is.
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
        _assert_targets_placed(audit, min_points=min_points)
        return np.where(usable, z, np.nan)

    return resolve


def _assert_targets_placed(placement: pd.DataFrame, *, min_points: int = 6,
                           min_fraction: float = 0.5) -> None:
    """Refuse a campaign whose targets do not land in real water.

    This is not defense in depth - it is the only defense. HydroBayesCal's
    OpenFOAM binding finds the nearest extraction point with **no distance
    cutoff**, so a target meters from the water returns a plausible number instead
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
        "modeled wetted extent, or the model is far too shallow there. Do NOT run "
        "the campaign: HydroBayesCal's nearest-point search has no distance "
        "cutoff, so it will silently return values from wherever it can reach.")
