"""HydroBayesCal integration: build inputs, emit config, launch calibration.

Keeps the per-case ``run_Bayes_cal.py`` / ``run_Bayes_cal_multiflow.py`` scripts
thin - they only declare case-specific choices (calibration target, and for the
multi-flow case the list of :class:`FlowSpec`) and call
:func:`run_single_flow_calibration` / :func:`run_multiflow_calibration`.

Two calibration modes:

* **single-flow** - one steady discharge, ground truth compiled from the
  configured ``ground_truth.sources`` (see :mod:`axqua.ground_truth`); the
  case's built ``.cas`` is calibrated directly with HydroBayesCal's stock
  ``bal_telemac.py``.
* **multi-flow** - several steady discharges, each with its own field campaign
  (:mod:`axqua.campaigns`) and its own steering file derived from the built
  base case; run with the additive ``bal_telemac_multiflow.py`` +
  ``MultiflowTelemacModel``. One shared roughness is calibrated against all
  campaigns jointly.

**HydroBayesCal is a pip dependency** (``pip install 'axqua[calibration]'``),
not an external checkout: the drivers ship with the package from 1.4.2 on
(``hydroBayesCal.copy_driver``), so there is no directory to point at and no
second conda environment to name. It is an *optional* extra because its surrogate
stack - bayesvalidrox, gpytorch, scikit-learn - is heavy and irrelevant to
building or running a TELEMAC case, so :func:`require_hbc` imports it lazily and
explains the install if it is missing.

A staged driver is still run as a **subprocess**, but in this interpreter's own
environment: the driver is a script with an ``argparse`` ``main()``, and it must
run with TELEMAC sourced (every surrogate iteration launches the solver), which
mutates the environment in ways not to inflict on the caller's process. Pass
``checkout=`` to develop against an unreleased driver from a source tree.

Lessons baked in (see the module functions):

* the built ``.cas`` must output ``SCALAR VELOCITY`` (graphic printout ``M``);
* the staged driver extracts the **last** (converged) SELAFIN frame, not the
  ``mean_last`` window that would average a dry-start transient;
* HydroBayesCal keeps a prior sample with probability likelihood / largest
  likelihood, so a sharp posterior needs more of them (``calibration.prior_samples``,
  default 5000); since 1.12.2 the memory grows in proportion to their number;
* per-flow steering files differ only in Q, the outflow stage (from the
  synthesized rating at that Q), ``DURATION`` and the results name.
"""

from __future__ import annotations

import logging
import shutil
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from axqua import campaigns, hbc
from axqua.config import Config
from axqua.ground_truth import compile_ground_truth, read_tidy
from axqua.rating import synthesize_outflow_rating

log = logging.getLogger("axqua")


# --------------------------------------------------------------------------- #
# install location
# --------------------------------------------------------------------------- #
# require_hbc / _hbc_version / stage_driver now live in axqua.hbc (they are the
# same whichever solver is calibrated). Re-exported, not reimplemented, so every
# existing import of them from axqua.bayescal keeps working.
require_hbc = hbc.require_hbc
_hbc_version = hbc.hbc_version
stage_driver = hbc.stage_driver


# --------------------------------------------------------------------------- #
# shared HydroBayesCal plumbing
# --------------------------------------------------------------------------- #
def ensure_graphic_output(cas_path: Path, code: str = "M") -> bool:
    """Ensure the ``.cas`` writes a graphic-printout variable (default ``M`` =
    SCALAR VELOCITY). Returns True if the file was patched. HydroBayesCal reads
    the quantity straight from the results SELAFIN, so it must be printed.
    """
    cas_path = Path(cas_path)
    if not cas_path.exists():
        raise SystemExit(f"no built case at {cas_path}; run preprocessing.py first")
    from axqua.solvers.telemac.steering import read_cas_values, set_keyword

    key = "VARIABLES FOR GRAPHIC PRINTOUTS"
    value = read_cas_values(cas_path, key)[key]
    if not value:
        print(f"  warning: no VARIABLES FOR GRAPHIC PRINTOUTS in {cas_path.name}")
        return False
    codes = [c.strip() for c in value.strip().strip("'\"").split(",") if c.strip()]
    if code in codes:
        return False
    codes.append(code)
    cas_path.write_text(set_keyword(cas_path.read_text(), key, "'" + ",".join(codes) + "'"))
    print(f"  added graphic printout {code!r} to {cas_path.name}")
    return True


def other_calibration_running(model_dir: Path) -> bool:
    """A TELEMAC run from this model dir already active (would fight over the
    shared friction.tbl)."""
    return hbc.solver_running("telemac2d", model_dir, extra=("steady2d",))


def _telemac_environment(cfg: Config):
    """The TELEMAC environment a staged driver is launched inside.

    ``environment`` is read defensively: the previous implementation needed nothing
    but ``telemac.pysource``, so requiring the newer structured block would narrow
    what a caller may pass for no gain (``from_config(None, ...)`` already falls
    back to a POSIX environment against the legacy script).
    """
    return hbc.solver_environment(getattr(cfg.telemac, "environment", None),
                                  cfg.telemac.pysource)


def launch(cfg: Config, driver: Path, config_path: Path, *, env: str | None = None,
           note: str = "HydroBayesCal calibration") -> int:
    """Run a staged TELEMAC driver with TELEMAC sourced. See :func:`axqua.hbc.launch_driver`.

    *env* is accepted and ignored; it named the separate conda environment this used
    to need, before HydroBayesCal became a dependency of axqua itself.
    """
    if env:
        print(f"note: env={env!r} ignored - HydroBayesCal now runs in axqua's own "
              "environment (it is a dependency), so no separate env is needed")
    return hbc.launch_driver(_telemac_environment(cfg), driver, config_path, note=note)


# --------------------------------------------------------------------------- #
# single-flow calibration
# --------------------------------------------------------------------------- #
def build_velocity_csv(cfg: Config, *, vel_err_floor: float = campaigns.VELOCITY_ERROR_FLOOR,
                       depth_err_floor: float = campaigns.DEPTH_ERROR_FLOOR,
                       positional: bool = True) -> tuple[Path, pd.DataFrame]:
    """Compile the configured ground truth and write the calibration-points CSV.

    The velocity target is the horizontal FlowTracker speed (0.6-depth proxy for
    the depth-averaged velocity) with propagated, floored error; water depth is
    carried alongside. Reads ``ground_truth.sources`` via
    :func:`axqua.ground_truth.compile_ground_truth`.

    With *positional*, a declared ``ground_truth.position_quality`` (or
    ``position_sigma``) adds the spread of the steady result within that radius to
    each error, in quadrature (:mod:`axqua.positional_error`), as the build's
    calibration table does. Until 2026-11-08 this table, which the calibration job
    writes last and HydroBayesCal reads, left that term out without a word.
    """
    compile_ground_truth(cfg)
    tables = read_tidy(cfg.ground_truth_path)
    if "hydraulics" not in tables:
        raise SystemExit(f"no 'hydraulics' tab in {cfg.ground_truth_path}; check "
                         "ground_truth.sources in case-config.yml")
    df = tables["hydraulics"].reset_index(drop=True)
    for col in ("x", "y", "u", "v", "h"):
        if col not in df.columns:
            raise SystemExit(f"hydraulics ground truth missing '{col}' (has {list(df.columns)})")
    out = campaigns.assemble_velocity_csv(
        df["x"], df["y"], df["z"] if "z" in df.columns else 0.0,
        df["u"], df["v"],
        df["u_err"] if "u_err" in df.columns else 0.0,
        df["v_err"] if "v_err" in df.columns else 0.0,
        df["h"], [f"pt-{i + 1}" for i in range(len(df))],
        vel_err_floor, depth_err_floor).drop(columns="label")
    out = _inside_the_model(cfg, out)
    out = _away_from_liquid_boundaries(cfg, out)
    if positional:
        out = _with_positional_error(cfg, out)
    path = cfg.calibration_path(cfg.calibration_csv)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return path, out


def _with_positional_error(cfg: Config, points: pd.DataFrame) -> pd.DataFrame:
    """*points* with the positional error of :func:`axqua.calibration._positional_errors` added in quadrature to each ``<QTY>_ERROR``."""
    from axqua.calibration import _positional_errors

    quantities = [c[:-len("_ERROR")] for c in points.columns if c.endswith("_ERROR")]
    if points.empty or not quantities:
        return points
    extra = _positional_errors(cfg, points, quantities)
    points = points.copy()
    for qty, spread in extra.items():
        column = f"{qty}_ERROR"
        points[column] = np.round(np.sqrt(points[column].astype(float) ** 2
                                          + np.asarray(spread, dtype=float) ** 2), 6)
    return points


def _inside_the_model(cfg: Config, points: pd.DataFrame) -> pd.DataFrame:
    """Leave out the measurement points that lie outside the model.

    A campaign often includes the cross sections at which the inflow was gauged,
    upstream of where the model begins. HydroBayesCal reads the model at the node
    nearest to a point with no limit on the distance, so such a point is compared with
    a node on the model boundary: a velocity measured in mid-channel against one at a
    wall. Of the 56 verticals of the Isar campaign of November 2025, 23 lie upstream of
    the model.
    """
    from shapely.geometry import Point

    outline = cfg.geodata.boundary
    if outline is None or not Path(outline).exists() or points.empty:
        return points
    try:
        import geopandas as gpd

        layer = gpd.read_file(outline)
        if layer.crs is not None and layer.crs.to_epsg() != cfg.crs_epsg:
            layer = layer.to_crs(epsg=cfg.crs_epsg)
        shape = layer.geometry.union_all() if hasattr(layer.geometry, "union_all") \
            else layer.geometry.unary_union
        if shape.geom_type in ("LineString", "MultiLineString"):
            from shapely.ops import polygonize, unary_union
            shape = unary_union(list(polygonize(shape)))
        inside = [shape.covers(Point(float(x), float(y)))
                  for x, y in zip(points["x"], points["y"])]
    except Exception as exc:                     # noqa: BLE001 - never lose the data
        log.warning("could not tell which measurement points lie inside the model "
                    "(%s); all of them are used", exc)
        return points
    outside = len(inside) - sum(inside)
    if outside:
        log.warning("%d of %d measurement points lie outside the model outline and "
                    "are left out", outside, len(inside))
        points = points.loc[inside].reset_index(drop=True)
        if "id" in points.columns:
            points["id"] = range(1, len(points) + 1)
    return points


#: Measurement points closer to an inflow or outflow line than this many mesh nodes are
#: not used: there the flow is what the boundary prescribes, not what the river does
#: (the user's rule of 2026-11-04). Applies to the calibration and the mesh study.
MIN_BOUNDARY_NODES = 15
#: The remedy the user named for points too close to a liquid boundary.
NEAR_BOUNDARY_REMEDY = "Increase ROI beyond the concerned liquid boundary."
#: The logger of that warning; a job puts its records into the result manifest.
near_boundary_log = logging.getLogger("axqua.near_boundary")


def away_from_liquid_boundaries(cfg: Config, x, y, *, labels=None,
                                min_nodes: int = MIN_BOUNDARY_NODES):
    """Which points lie at least *min_nodes* mesh nodes from every liquid boundary.

    Measured on the built mesh of the case (``geometry.slf`` and ``boundaries.cli`` of
    the model folder) by the node nearest to each point
    (:func:`axqua.solvers.telemac.boundary.liquid_boundary_hops`). Returns a boolean
    array; all True when the case is not built yet. Points that fail are reported as a
    warning that names the boundary and the remedy (:data:`NEAR_BOUNDARY_REMEDY`).
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    keep = np.ones(len(x), dtype=bool)
    model = getattr(cfg, "model_dir", None)
    if not len(x) or model is None:
        return keep
    geometry = Path(model) / cfg.geometry_slf
    cli = Path(model) / cfg.boundary_cli
    if not geometry.exists() or not cli.exists():
        return keep
    try:
        from scipy.spatial import cKDTree

        from axqua.solvers.telemac.boundary import liquid_boundary_hops

        nx, ny, hops, kind = liquid_boundary_hops(geometry, cli)
        _, near = cKDTree(np.column_stack([nx, ny])).query(np.column_stack([x, y]))
    except Exception as exc:                     # noqa: BLE001 - never lose the data
        log.warning("could not tell how far the measurement points lie from the inflow "
                    "and outflow (%s); all of them are used", exc)
        return keep
    close = (hops[near] >= 0) & (hops[near] < int(min_nodes))
    keep = ~close
    if close.any():
        names = list(labels) if labels is not None else [str(i + 1) for i in range(len(x))]
        for side in ("inflow", "outflow"):
            hit = [names[i] for i in np.flatnonzero(close & (kind[near] == side))]
            if hit:
                near_boundary_log.warning(
                    "%d of %d measurement points lie less than %d nodes from the %s "
                    "boundary and are not used (%s). %s", len(hit), len(x), min_nodes,
                    side, ", ".join(hit), NEAR_BOUNDARY_REMEDY)
    return keep


def _away_from_liquid_boundaries(cfg: Config, points: pd.DataFrame) -> pd.DataFrame:
    """*points* without the rows :func:`away_from_liquid_boundaries` leaves out."""
    if points.empty:
        return points
    labels = (points["id"].astype(str).tolist() if "id" in points.columns else None)
    keep = away_from_liquid_boundaries(cfg, points["x"], points["y"], labels=labels)
    if keep.all():
        return points
    points = points.loc[keep].reset_index(drop=True)
    if "id" in points.columns:
        points["id"] = range(1, len(points) + 1)
    return points


#: The steering file of calibration runs that start from the steady result.
CALIBRATION_HOTSTART_CAS = "calibration2d.cas"
#: Simulated time of such a run, in residence times of the reach (wetted volume over
#: discharge). Measured on the finest Inn KB15 mesh (2026-11-08; 133,500 m3 at
#: 54.3 m3/s, a residence time of 2,460 s), continuing from the steady result with
#: the four calibrated zones at their prior bounds: at the upper bounds the stored
#: volume rose 5 % and the inflow-outflow imbalance stayed below 0.1 % from 2,949 s
#: on (1.2 residence times), the depth and velocity at the 30 verticals then within
#: 0.06 % and 0.3 % of their final values on average; at the lower bounds the volume
#: fell 10 % and was flat from about 2,000 s, while the flow kept shedding eddies
#: (outflow +/- 1 %), which no run length removes.
HOTSTART_RESIDENCE_TIMES = 1.2
#: The shortest such run [s], for a reach whose water is replaced within minutes.
HOTSTART_MIN_DURATION = 600.0


def _steady_result(cfg: Config) -> Path:
    """The converged result of the steady run that calibration runs start from.

    Refused when it is missing, older than the mesh, or computed on another mesh: a
    run that continues from a result of an earlier build stops at once on the node
    count, or, worse, runs from the flow of another roughness map.
    """
    from axqua.core import errors
    from axqua.core.selafin import SelafinFile

    result = cfg.model_path(cfg.results_slf)
    geometry = cfg.model_path(cfg.geometry_slf)
    remedy = "Run the steady simulation of the case first (Baseline (Telemac) > Run)."
    if not result.is_file():
        raise errors.ConfigError(
            f"calibration.hotstart needs the result of the steady run, {result}, "
            "which does not exist", subject="calibration.hotstart", remedy=remedy)
    if geometry.is_file() and result.stat().st_mtime < geometry.stat().st_mtime:
        raise errors.ConfigError(
            f"{result.name} is older than {geometry.name}: it belongs to an earlier "
            "build of the case", subject="calibration.hotstart", remedy=remedy)
    if geometry.is_file() and SelafinFile(result).npoin != SelafinFile(geometry).npoin:
        raise errors.ConfigError(
            f"{result.name} and {geometry.name} have different meshes",
            subject="calibration.hotstart", remedy=remedy)
    return result


def case_inflows(cfg: Config) -> dict[int, float]:
    """The discharge of each inflow line of the built case, where every line names one.

    Empty when the build left the split to the total ``prescribed_flowrate`` or has not
    written its ``liquid-boundaries.json`` yet.
    """
    from axqua.solvers.telemac import boundary

    meta = cfg.model_path(cfg.liquid_boundaries_json)
    if not meta.is_file():
        return {}
    inflows = [lb for lb in boundary.load_liquid_boundaries(meta) if lb.kind == "inflow"]
    if not inflows or any(lb.discharge is None for lb in inflows):
        return {}
    return {lb.index: float(lb.discharge) for lb in inflows}


def case_discharge(cfg: Config) -> float | None:
    """The total inflow of the case: ``prescribed_flowrate``, else the sum of the discharges of its inflow lines."""
    if cfg.boundaries.prescribed_flowrate is not None:
        return float(cfg.boundaries.prescribed_flowrate)
    inflows = case_inflows(cfg)
    return float(sum(inflows.values())) if inflows else None


def calibration_hotstart_duration(cfg: Config) -> float:
    """Simulated time [s] of a calibration run that starts from the steady result.

    ``calibration.hotstart_duration`` when given. Otherwise the run lasts
    :data:`HOTSTART_RESIDENCE_TIMES` residence times of the reach, rounded up to
    100 s: the residence time is the volume of water in the converged result over the
    discharge, the time in which the reach exchanges its water once. A changed
    roughness changes the stored volume, and the flow has settled once that change
    has passed the outflow.
    """
    import numpy as np

    from axqua.core.selafin import SelafinFile

    given = cfg.calibration.hotstart_duration
    if given:
        return float(given)
    discharge = case_discharge(cfg) or 0.0
    if discharge <= 0.0:
        if cfg.hydrodynamics.duration:
            return float(cfg.hydrodynamics.duration)
        from axqua.core.errors import ConfigError
        raise ConfigError(
            "a calibration run that starts from the steady result needs its duration, "
            "and the case names neither a discharge to derive it from nor a duration",
            subject="calibration.hotstart_duration",
            remedy="Enter calibration.hotstart_duration in seconds, or the discharge "
                   "of the case (boundaries.prescribed_flowrate).")
    # the coordinates of the geometry, in double precision (a result stores them in
    # single precision, which at a UTM northing is a spacing of 0.5 m)
    mesh = SelafinFile(cfg.model_path(cfg.geometry_slf))
    x, y, ikle = mesh.x, mesh.y, mesh.ikle
    h = SelafinFile(cfg.model_path(cfg.results_slf)).frame(
        -1, ["WATER DEPTH"])["WATER DEPTH"].astype(float)
    area = 0.5 * np.abs((x[ikle[:, 1]] - x[ikle[:, 0]]) * (y[ikle[:, 2]] - y[ikle[:, 0]])
                        - (x[ikle[:, 2]] - x[ikle[:, 0]]) * (y[ikle[:, 1]] - y[ikle[:, 0]]))
    volume = float((area * h[ikle].mean(axis=1)).sum())
    residence = volume / discharge
    return float(max(HOTSTART_MIN_DURATION,
                     np.ceil(HOTSTART_RESIDENCE_TIMES * residence / 100.0) * 100.0))


def write_calibration_hotstart(cfg: Config) -> str:
    """Write the steering file of calibration runs that start from the steady result.

    ``calibration2d.cas`` is the built ``steady2d.cas`` continuing from ``r2d.slf``
    (:func:`axqua.solvers.telemac.steering.write_hotstart_cas`) for
    :func:`calibration_hotstart_duration`. HydroBayesCal names the result of each run
    ``r2d_<n>.slf``, so the seed is never overwritten. Returns the file name.
    """
    from axqua.solvers.telemac.steering import write_hotstart_cas

    # a calibration that was killed set aside the steering file of its time; put it
    # back now, or case_as_built() would restore it over the one written here
    restore_as_built(cfg)
    _steady_result(cfg)
    if not cfg.model_path("hotstart2d.cas").is_file():
        log.warning("the steady run had not balanced its inflow and outflow (no "
                    "hotstart2d.cas), so every calibration run starts from a flow "
                    "that is not steady; run the steady simulation longer first")
    duration = calibration_hotstart_duration(cfg)
    path = write_hotstart_cas(cfg, duration, out_name=CALIBRATION_HOTSTART_CAS)
    print(f"calibration runs start from {cfg.results_slf} and last {duration:g} s "
          f"-> {path.name}")
    return path.name


def _set_quantities(cfg: Config, calibration_quantities=None,
                    extraction_quantities=None) -> list[str]:
    """Settle the calibration targets and the extracted quantities on *cfg*; returns the targets.

    The caller's lists win over the case file's. Every calibration target is also
    extracted, or HydroBayesCal never reads it from the results.
    """
    # The case file decides what is calibrated against. Until 2026-10-17 a built-in
    # ("SCALAR VELOCITY",) replaced it whenever the caller named nothing, which is
    # what every job did: a case asking for depth and velocity was calibrated
    # against velocity alone, without a word.
    if calibration_quantities is None:
        calibration_quantities = (cfg.calibration.calibration_quantities
                                  or ["SCALAR VELOCITY"])
    if extraction_quantities is None:
        extraction_quantities = (cfg.calibration.extraction_quantities
                                 or ["WATER DEPTH", "SCALAR VELOCITY"])
    extraction_quantities = list(dict.fromkeys([*extraction_quantities,
                                                *calibration_quantities]))
    cfg.calibration.calibration_quantities = list(calibration_quantities)
    cfg.calibration.extraction_quantities = list(extraction_quantities)
    return list(calibration_quantities)


def run_single_flow_calibration(
        cfg: Config, *, calibration_quantities=None,
        extraction_quantities=None,
        vel_err_floor: float = campaigns.VELOCITY_ERROR_FLOOR,
        depth_err_floor: float = campaigns.DEPTH_ERROR_FLOOR,
        prepare_only: bool = False, resume: bool = False,
        checkout: Path | None = None, env: str | None = None) -> int:
    """End-to-end single-discharge calibration of the built case.

    Compiles the calibration CSV, ensures the ``.cas`` prints the target,
    emits the HydroBayesCal config, stages the driver and (unless
    ``prepare_only``) launches it, in two passes with ``calibration.structural_error:
    unreachable`` (:func:`_launch_in_passes`); *resume* starts from the initial
    simulations an earlier calibration saved. Returns the driver exit code (0 when
    ``prepare_only``).
    """
    from axqua.calibration import emit_hbc_config

    mode = structural_error_mode(cfg)
    cfg.ensure_dirs()
    calibration_quantities = _set_quantities(cfg, calibration_quantities,
                                             extraction_quantities)
    print(f"calibration target(s): {list(calibration_quantities)}")

    csv, table = build_velocity_csv(cfg, vel_err_floor=vel_err_floor,
                                    depth_err_floor=depth_err_floor)
    print(f"calibration points ({len(table)}) -> {csv}")
    # the zones of the roughness table and the rows of the target workbook too, not
    # only the list of the case file (the Inn KB15 case printed "parameters: []"),
    # less the zones the points say nothing about (calibration_zones)
    parameters = settle_zone_plan(cfg, [table], Path(cfg.calibration_dir), resume=resume)
    if "SCALAR VELOCITY_DATA" in table:
        print(f"  scalar velocity [m/s]: {table['SCALAR VELOCITY_DATA'].min():.3f} .. "
              f"{table['SCALAR VELOCITY_DATA'].max():.3f}")

    ensure_graphic_output(cfg.model_path(cfg.cas_file))
    if cfg.calibration.hotstart:
        if cfg.calibration.control_file:
            log.warning("calibration.hotstart is ignored: calibration.control_file "
                        "names the steering file (%s)", cfg.calibration.control_file)
        else:
            cfg.calibration.control_file = write_calibration_hotstart(cfg)

    def emit(complete: bool, only_bal: bool) -> Path:
        return emit_hbc_config(cfg, csv, complete_bal_mode=complete,
                               only_bal_mode=only_bal, parameters=parameters)

    driver = stage_driver(cfg.calibration_dir, "bal_telemac.py", checkout=checkout)
    folder = Path(cfg.calibration_dir)
    code = _launch_in_passes(
        cfg, mode=mode, resume=resume, prepare_only=prepare_only, emit=emit,
        driver=driver, restarts={"calibration": folder / RESTART_OUTPUTS},
        csvs={"calibration": Path(csv)}, folder=folder, env=env,
        note="HydroBayesCal calibration")
    if code == 0 and not prepare_only:
        report(cfg, folder)
    return code


#: Folder in the calibration directory that holds the files of the built case while a
#: calibration rewrites them.
AS_BUILT = "case-as-built"


def _rewritten_by_the_driver(cfg: Config) -> list[str]:
    """The files of the built case a HydroBayesCal run writes into, relative to it."""
    names = [cfg.calibration.control_file or cfg.cas_file, cfg.friction_tbl]
    if cfg.gain_lose.active and cfg.gain_lose.implementation == "fortran":
        names.append("user_fortran/user_rain.f")
    if cfg.morphodynamics.enabled:
        names.append(cfg.gaia_cas)
    return [str(name) for name in names if name]


@contextmanager
def case_as_built(cfg: Config):
    """Hand the built case back the way the calibration found it.

    HydroBayesCal runs TELEMAC in the folder of the built case and writes every
    parameter set it tests into the files there: the roughness values into the
    friction table, the name of the results file into the steering file. When it has
    finished, the case holds the **last set it tried**, which is neither the case
    that was built nor the calibrated one, and the next steady run uses it without a
    word. On the Isar example the friction table read ks = 0.195 m in zone 4 after a
    calibration of a case built with 0.05 m.

    The files are copied into the calibration folder before the driver starts and
    copied back when it returns, whether it succeeded or not. A calibration that is
    killed restores nothing; see :func:`restore_as_built` for that case.
    """
    model = Path(cfg.model_dir)
    keep = cfg.calibration_path(AS_BUILT)
    restore_as_built(cfg)
    for name in _rewritten_by_the_driver(cfg):
        if (model / name).is_file():
            (keep / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(model / name, keep / name)
    try:
        yield
    finally:
        restore_as_built(cfg)


def restore_as_built(cfg: Config) -> bool:
    """Copy the files a calibration set aside back into the built case.

    Returns whether there was anything to restore. A copy is only ever found here
    outside a running calibration when that calibration was killed, by a cancelled job
    or a power cut, before it could restore the case itself. The copy is then the case
    as built, and what is in the model folder is the last parameter set the driver had
    reached. Every TELEMAC run of the case calls this first, so a cancelled calibration
    cannot leak into the next simulation, and every build discards the copy
    (:func:`discard_as_built`), so it cannot replace a newer build either.
    """
    keep = cfg.calibration_path(AS_BUILT)
    if not keep.is_dir():
        return False
    model = Path(cfg.model_dir)
    for source in sorted(path for path in keep.rglob("*") if path.is_file()):
        target = model / source.relative_to(keep)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.rmtree(keep, ignore_errors=True)
    return True


def discard_as_built(cfg: Config) -> bool:
    """Drop the copy a killed calibration set aside, because the case is built anew.

    The copy holds the files of the build the calibration started from. Once the case
    is rebuilt, restoring it would put the old build back over the new one: on the Isar
    example a calibration cancelled during its first run, then a build with a longer
    ``DURATION``, then a steady run gave the old steering file and the old duration.
    Returns whether there was a copy.
    """
    keep = cfg.calibration_path(AS_BUILT)
    if not keep.is_dir():
        return False
    shutil.rmtree(keep, ignore_errors=True)
    return True


# --------------------------------------------------------------------------- #
# multi-flow calibration
# --------------------------------------------------------------------------- #
@dataclass
class FlowSpec:
    """One steady discharge in a multi-flow calibration.

    ``kind`` + ``values`` (+ ``positions``) feed :func:`axqua.campaigns`; the
    steering file is derived from the built base case, differing only in Q, the
    outflow stage, ``DURATION`` and the results name. ``vel_err_floor`` raises
    the per-point velocity error for a campaign that should inform but not
    dominate the joint likelihood (e.g. a single cross-section).
    """
    name: str                       # short tag (steering + results names)
    discharge: float                # m3/s
    kind: str                       # adapter | transect | inline | points | csv
    values: Path                    # FlowTracker xlsx, point layer ("points") or a ready CSV ("csv")
    positions: Path | None = None   # DGPS point layer (adapter/transect)
    sheet: str = "KB15"
    duration: float | None = None   # steady sim seconds (default: cfg duration)
    vel_err_floor: float = campaigns.VELOCITY_ERROR_FLOOR
    # Continue from an already-converged result instead of re-filling the reach
    # from the pre-wet seed on every calibration run. The flow field then only has
    # to re-equilibrate to the perturbed roughness, which is a small fraction of
    # the time a fill takes - the difference between a feasible and an infeasible
    # calibration on a large mesh. See write_flow_cas for the caveat on DURATION.
    hotstart_from: str | None = None    # e.g. "r2d.slf"

    def compile(self, crs_epsg: int) -> pd.DataFrame:
        if self.kind == "csv":
            # pre-compiled calibration points (already in the HydroBayesCal
            # schema: id, x, y, z, <QTY>_DATA, <QTY>_ERROR); passed through so
            # case-specific target preparation (e.g. bathymetry-corrected
            # depths, profile-averaged velocities) survives the launcher's
            # recompilation step.
            return pd.read_csv(self.values)
        if self.kind == "adapter":
            return campaigns.compile_adapter(self.values, self.positions, crs_epsg,
                                             self.vel_err_floor, self.name)
        if self.kind == "transect":
            return campaigns.compile_transect(self.values, self.positions,
                                              self.sheet, self.vel_err_floor,
                                              label=self.name)
        if self.kind == "inline":
            return campaigns.compile_inline(self.values, self.sheet,
                                            self.vel_err_floor, label=self.name)
        if self.kind == "points":
            return campaigns.compile_points(self.values, crs_epsg,
                                            self.vel_err_floor, label=self.name)
        raise ValueError(f"flow {self.name!r}: unknown kind {self.kind!r}")


def _patch_cas(text: str, key: str, value: str) -> str:
    """*text* of a steering file with *key* set to *value* (:func:`axqua.solvers.telemac.steering.set_keyword`); a keyword the file does not have raises."""
    from axqua.solvers.telemac.steering import set_keyword

    return set_keyword(text, key, value)


def _set_cas(text: str, key: str, value: str) -> str:
    """Like :func:`_patch_cas` but appends the keyword when the base case has no
    such line (a hotstart adds keywords a pre-wet start never carried)."""
    from axqua.solvers.telemac.steering import set_keyword

    return set_keyword(text, key, value, append=True)


def outflow_stage(cfg: Config, discharge: float, out_dir: Path) -> float:
    """Outflow water-surface elevation at ``discharge`` from the synthetic
    normal-flow rating (extrapolation warning: unreliable far above the geometry
    calibration discharge / overbank)."""
    rating = synthesize_outflow_rating(cfg, discharge,
                                       out=out_dir / f"rating-q{discharge:g}.csv")
    return float(pd.read_csv(rating)["WSE"].iloc[0])


def write_flow_cas(cfg: Config, base_cas: Path, flow: FlowSpec, rating_dir: Path,
                   *, duration: float | None = None, dest_dir: Path | None = None,
                   listing: int | None = None, graphic: int | None = None) -> Path:
    """Derive one flow's steering file from the built base ``.cas``.

    With ``flow.hotstart_from`` the run continues from that result file instead of
    the base case's pre-wet/dry seed. ``DURATION`` then measures *re-equilibration*
    after the roughness change, not the fill - but it must still be long enough for
    the field to forget the seed, or every run is pulled toward the roughness the
    seed was converged at and the calibration under-reports sensitivity. Verify it
    at the prior extremes before trusting a short hotstart duration.
    """
    text = base_cas.read_text()
    wse = outflow_stage(cfg, flow.discharge, rating_dir)
    dur = duration if duration is not None else (flow.duration
                                                 or cfg.hydrodynamics.duration)
    text = _patch_cas(text, "TITLE", f"'{cfg.name} steady {flow.name}'")
    if flow.hotstart_from:
        text = _set_cas(text, "PREVIOUS COMPUTATION FILE", flow.hotstart_from)
        text = _set_cas(text, "PREVIOUS COMPUTATION FILE FORMAT", "'SERAFIN'")
        text = _set_cas(text, "INITIAL TIME SET TO ZERO", "YES")
    text = _patch_cas(text, "PRESCRIBED FLOWRATES", f"0.;{flow.discharge:.4f}")
    text = _patch_cas(text, "PRESCRIBED ELEVATIONS", f"{wse:.4f};0.")
    text = _patch_cas(text, "DURATION", f"{dur}")
    text = _patch_cas(text, "RESULTS FILE", f"r2d-{flow.name}.slf")
    if listing is not None:
        text = _patch_cas(text, "LISTING PRINTOUT PERIOD", str(listing))
    if graphic is not None:
        text = _patch_cas(text, "GRAPHIC PRINTOUT PERIOD", str(graphic))
    dest = (dest_dir or base_cas.parent) / f"steady2d-{flow.name}.cas"
    dest.write_text(text)
    print(f"  {dest.name}: Q={flow.discharge} m3/s, outflow WSE={wse:.3f} m, "
          f"DURATION={dur} s")
    return dest


def emit_multiflow_config(cfg: Config, out_dir: Path, model_dir: Path,
                          flows: list[FlowSpec], csv_paths: dict[str, Path], *,
                          n_cpus: int, init_runs: int, max_runs: int,
                          complete_bal_mode: bool = True,
                          only_bal_mode: bool = False,
                          prior_samples: int | None = None,
                          adaptive_init_runs: bool | None = None,
                          parameters: list | None = None) -> Path:
    """Write ``config_Telemac_multiflow.py`` (standard blocks + a ``multiflow``
    block of per-flow steering/CSV entries). *parameters* as in
    :func:`axqua.calibration.emit_hbc_config`."""
    from axqua.calibration import telemac_parameters

    c = cfg.calibration
    # the merged parameters, not c.parameters: the roughness table's calibrated zones
    # and the target template's rows must reach the multiflow config too; and only
    # TELEMAC's, so an OpenFOAM ks or Cmu of the same list is not emitted
    parameters = telemac_parameters(cfg) if parameters is None else list(parameters)
    params = [p.name for p in parameters]
    ranges = [[p.min, p.max] for p in parameters]
    prior = prior_samples if prior_samples is not None else getattr(
        c, "prior_samples", 5000)
    adaptive = bool(getattr(c, "adaptive_init_runs", True)
                    if adaptive_init_runs is None else adaptive_init_runs)
    flow_entries = ",\n".join(
        "        {{'name': {n!r}, 'control_file': {cf!r},\n"
        "         'results_filename_base': {rb!r},\n"
        "         'calibration_pts_file_path': {csv!r}}}".format(
            n=f.name, cf=f"steady2d-{f.name}.cas", rb=f"r2d-{f.name}",
            csv=str(csv_paths[f.name]))
        for f in flows)
    text = f'''"""HydroBayesCal multi-discharge config generated by axqua for '{cfg.name}'.

Run:  python bal_telemac_multiflow.py --config config_Telemac_multiflow.py
"""

import os

BASE_DIR = {str(model_dir)!r}

paths = {{
    'case_template_dir': BASE_DIR,
    'model_dir': BASE_DIR,
    'res_dir': {str(out_dir)!r},
    'calibration_pts_file_path': {str(csv_paths[flows[0].name])!r},
}}

multiflow = {{
    'flows': [
{flow_entries},
    ],
}}

hydrodynamic_simulation = {{
    'solver_name': 'Telemac2d',
    'n_processors': {n_cpus},
    'results_filename_base': {f"r2d-{flows[0].name}"!r},
    'control_file': {f"steady2d-{flows[0].name}.cas"!r},
    'friction_file': {cfg.friction_tbl!r},
    'fortran_file': None,
}}

morphodynamic_simulation = {{
    'gaia_cas': None,
    'gaia_results_filename_base': None,
}}

calibration = {{
    'parameters': {params!r},
    'param_values': {ranges!r},
    'extraction_quantities': {list(c.extraction_quantities)!r},
    'calibration_quantities': {list(c.calibration_quantities)!r},
    'dict_output_name': 'extraction-data',
}}

sampling = {{
    'init_runs': {init_runs},
    'max_runs': {max_runs},
    'parameter_distribution': 'uniform',
    'parameter_sampling_method': {c.parameter_sampling_method!r},
    # HydroBayesCal >= 1.5: grow the initial design in Sobol blocks and stop when
    # it is measurably sufficient. init_runs is the ceiling, so this can only save
    # runs - and here one run means one TELEMAC simulation PER FLOW.
    'adaptive_init_runs': {adaptive},
    'init_runs_min': {getattr(c, "init_runs_min", None)!r},
    # exploit the posterior until >1 well-separated mode is found, then explore
    'bal_exploration_tradeoff': {getattr(c, "bal_exploration_tradeoff", "auto")!r},
    'tp_selection_criteria': 'dkl',
    'eval_steps': 1,
    # prior samples the surrogate is predicted at (calibration.prior_samples); a sharp
    # posterior keeps few of them, so raise it when few are accepted.
    'prior_samples': {prior},
    'mc_samples_al': 2000,
    'mc_exploration': 1000,
    'gp_library': {c.gp_library!r},
    # 'variables': one multi-output GPE per location, its outputs the calibration
    # quantities (calibration.multitask_selection)
    'multitask_selection': {getattr(c, "multitask_selection", "variables")!r},
}}

execution = {{
    'complete_bal_mode': {complete_bal_mode},
    'only_bal_mode': {only_bal_mode},
    'delete_complex_outputs': True,
    'validation': False,
    'user_param_values': False,
}}

extraction = {{
    # Read the CONVERGED final frame, not a mean over the last n frames: these runs
    # march to steady state from a pre-wetted start, so a mean_last window folds the
    # residual transient into the values the surrogate is trained on. The multiflow
    # driver silently defaulted to 'mean_last' until HydroBayesCal 1.5 made it read
    # this block (single-flow runs on the same config already honored it), so the
    # two modes were fitted to different data - emit it explicitly.
    'output_extraction_time': 'last',
    'n_last': 80,
}}
'''
    path = Path(out_dir) / "config_Telemac_multiflow.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --------------------------------------------------------------------------- #
# model error at points the model cannot reach (calibration.structural_error)
# --------------------------------------------------------------------------- #
#: What a calibration does with a measured value that no parameter set within the
#: ranges reproduces: keep the measurement error (``none``), or add the misfit that
#: remains to the error of the point (``unreachable``).
STRUCTURAL_ERRORS = ("none", "unreachable")
#: HydroBayesCal's record of the simulated values of the initial design, per point.
RESTART_OUTPUTS = Path("auto-saved-results-HydroBayesCal") / "restart_data" / \
    "initial-model-outputs.json"


def structural_error_mode(cfg: Config) -> str:
    """``calibration.structural_error``, checked."""
    from axqua.core.errors import ConfigError

    mode = (getattr(cfg.calibration, "structural_error", None) or "none").strip().lower()
    if mode not in STRUCTURAL_ERRORS:
        raise ConfigError(f"calibration.structural_error must be one of "
                          f"{list(STRUCTURAL_ERRORS)}, not {mode!r}",
                          subject="calibration.structural_error")
    return mode


def _point_runs(outputs: dict, point_id) -> list | None:
    """The runs of one point in the per-point layout; the id as HydroBayesCal wrote it (the first column of the points file, as text)."""
    runs = outputs.get(str(point_id))
    if runs is None and isinstance(point_id, (int, float, np.number)) \
            and float(point_id).is_integer():
        runs = outputs.get(str(int(point_id)))
    return runs


def per_point_outputs(stored: dict, points: pd.DataFrame) -> dict:
    """HydroBayesCal's ``initial-model-outputs.json`` in the per-point layout.

    From 1.12 on, HydroBayesCal writes ``{point_id: [{quantity: value} per run]}``; where
    its detailed table does not hold one row per point for every run, it writes the
    flat layout of earlier releases instead: ``model_evaluations``, one row per run
    with the columns point by point and, within a point, one per
    ``calibration_quantities``. That layout is converted here, with the ids of
    *points* (the order HydroBayesCal read them in).
    """
    if "model_evaluations" not in stored:
        return stored
    matrix = np.atleast_2d(np.asarray(stored["model_evaluations"], dtype=float))
    quantities = list(stored.get("calibration_quantities") or [])
    if not quantities or matrix.shape[1] != len(points) * len(quantities):
        raise ValueError(
            f"the initial simulations hold {matrix.shape[1]} values per run, not one "
            f"per point ({len(points)}) and calibration target ({quantities})")
    width = len(quantities)
    return {str(point_id): [{q: float(row[i * width + j])
                             for j, q in enumerate(quantities)} for row in matrix]
            for i, point_id in enumerate(points["id"])}


def unreachable_misfit(points: pd.DataFrame, outputs: dict,
                       quantities) -> pd.DataFrame:
    """Per point and quantity, the part of the misfit that no simulated set removes.

    *points* is a calibration points table (``id``, ``x``, ``y``, ``<Q>_DATA``,
    ``<Q>_ERROR``), *outputs* HydroBayesCal's ``initial-model-outputs.json``: per point
    id, one ``{quantity: value}`` per simulation of the initial design. The misfit that
    remains is the distance from the measured value to the range of the simulated
    values, zero where the range contains it. It is added in quadrature to the
    measurement error, so a point the model reaches keeps its error, and a point it
    cannot reach with any roughness in the ranges (a water level the terrain model
    does not hold up, Inn KB15 2026-11-09: 17 to 38 cm too low in all 16 simulations)
    no longer decides the calibration on its own. Returns one row per point with, per
    quantity, the measured value, the measurement error, the smallest and largest
    simulated value, the misfit that remains and the combined error.
    """
    rows = []
    # iterrows turns every value of a row into a float; the id keeps its own type
    for point_id, (_, point) in zip(points["id"], points.iterrows()):
        runs = _point_runs(outputs, point_id)
        if not runs:
            raise ValueError(f"the initial simulations hold no values for point "
                             f"{point_id}")
        row = {"id": point_id, "x": point["x"], "y": point["y"]}
        for quantity in quantities:
            values = np.array([run.get(quantity, np.nan) for run in runs], dtype=float)
            values = values[np.isfinite(values)]
            data = float(point[f"{quantity}_DATA"])
            error = float(point[f"{quantity}_ERROR"])
            if values.size:
                low, high = float(values.min()), float(values.max())
                gap = max(low - data, data - high, 0.0)
            else:                            # dry in every simulation: nothing to add
                low = high = np.nan
                gap = 0.0
            row.update({f"{quantity}_DATA": data,
                        f"{quantity}_MEASUREMENT_ERROR": error,
                        f"{quantity}_MODEL_MIN": low, f"{quantity}_MODEL_MAX": high,
                        f"{quantity}_UNREACHABLE": gap,
                        f"{quantity}_ERROR": float(np.hypot(error, gap))})
        rows.append(row)
    return pd.DataFrame(rows)


#: HydroBayesCal adds this share of the measured value to every error of the points
#: file itself (its ``measurement_error``, which aXqua leaves at the default).
HBC_MEASUREMENT_SHARE = 0.10


def _run_values(points: pd.DataFrame, outputs: dict, quantity: str) -> np.ndarray:
    """Simulated values of *quantity*, one row per run of the initial design."""
    columns = []
    for point_id in points["id"]:
        runs = _point_runs(outputs, point_id)
        columns.append([run.get(quantity, np.nan) for run in runs or []])
    if len({len(column) for column in columns}) > 1:
        raise ValueError("the initial simulations hold different numbers of runs per point")
    return np.array(columns, dtype=float).T


def joint_misfit(tables: dict[str, pd.DataFrame], points: dict[str, pd.DataFrame],
                 outputs: dict[str, dict], quantities,
                 share: float = HBC_MEASUREMENT_SHARE) -> int:
    """Widen the errors of a group of points that no single simulated set fits jointly.

    The second step of the model error: :func:`unreachable_misfit` covers a point the
    model misses with every set, but a group of points can each lie inside the
    simulated range and still not be reached together by any one set (Inn KB15
    2026-11-10: August and March velocities, chi-square per point approximately 10 at
    the best set, depth and November velocity approximately 1). The best run of the
    initial design is the one with the smallest chi-square over all situations and
    quantities, with the variance HydroBayesCal uses: the error of the points file
    plus *share* of the measured value, in quadrature. For each situation and calibration target
    whose chi-square per point exceeds 1 at that run, the variance of its points is
    multiplied by that value, so the group counts as much as its fit allows. *tables*
    (situation -> the table of :func:`unreachable_misfit`) are completed in place with
    ``<Q>_BEST_FIT``, ``<Q>_VARIANCE_FACTOR`` and the final ``<Q>_ERROR``. A run of the
    same number is the same parameter set in every situation. Returns the number of
    the best run, counted from 1.
    """
    terms, runs = {}, set()
    for tag, table in tables.items():
        for quantity in quantities:
            values = _run_values(points[tag], outputs[tag], quantity)
            data = table[f"{quantity}_DATA"].to_numpy(dtype=float)
            variance = (share * np.abs(data)) ** 2 + \
                table[f"{quantity}_ERROR"].to_numpy(dtype=float) ** 2
            terms[tag, quantity] = (values, data, variance)
            runs.add(values.shape[0])
    if len(runs) != 1 or not runs.pop():
        raise ValueError("the situations hold different numbers of initial simulations")

    def chi2(values, data, variance):
        return np.nansum((values - data) ** 2 / variance, axis=-1)

    best = int(np.argmin(sum(chi2(*term) for term in terms.values())))
    for (tag, quantity), (values, data, variance) in terms.items():
        fitted = np.isfinite(values[best])
        per_point = chi2(values[best], data, variance) / max(int(fitted.sum()), 1)
        factor = max(1.0, float(per_point))
        measured = (share * np.abs(data)) ** 2
        table = tables[tag]
        table[f"{quantity}_BEST_FIT"] = values[best]
        table[f"{quantity}_VARIANCE_FACTOR"] = factor
        table[f"{quantity}_ERROR"] = np.sqrt(factor * variance - measured)
        log.info("%s, %s: chi-square per point %.3g at the best initial run %d%s",
                 tag, quantity, per_point, best + 1,
                 f" -> variance x{factor:.3g}" if factor > 1 else "")
    return best + 1


def apply_unreachable_misfit(csvs: dict[str, Path], restarts: dict[str, Path],
                             quantities, records: dict[str, Path]
                             ) -> dict[str, pd.DataFrame]:
    """Write the model error into the points file of every situation.

    Per situation, :func:`unreachable_misfit` adds what no simulated set reaches at a
    point; then :func:`joint_misfit` widens, across all situations together, the
    errors of the groups that no single set fits. Each *csvs* file holds the
    measurement errors when this is called (it is compiled again before every
    launch), so applying it twice does not add the model error twice. The table with
    every term is written to *records* (situation -> path).
    """
    import json

    points = {tag: pd.read_csv(csv) for tag, csv in csvs.items()}
    outputs = {tag: per_point_outputs(json.loads(Path(restarts[tag]).read_text()),
                                      points[tag]) for tag in csvs}
    tables = {tag: unreachable_misfit(points[tag], outputs[tag], quantities)
              for tag in csvs}
    for tag, table in tables.items():
        for quantity in quantities:
            gap = table[f"{quantity}_UNREACHABLE"]
            reached = int((gap > 0).sum())
            log.info("%s, %s: %d of %d points lie outside the simulated range%s",
                     Path(csvs[tag]).name, quantity, reached, len(table),
                     f" (misfit {gap[gap > 0].median():.3g} median, "
                     f"{gap.max():.3g} at most)" if reached else "")
    joint_misfit(tables, points, outputs, quantities)
    columns = ["id", "x", "y"] + [f"{quantity}_{part}" for quantity in quantities
                                  for part in ("DATA", "MEASUREMENT_ERROR", "MODEL_MIN",
                                               "MODEL_MAX", "UNREACHABLE", "BEST_FIT",
                                               "VARIANCE_FACTOR", "ERROR")]
    for tag, table in tables.items():
        for quantity in quantities:
            points[tag][f"{quantity}_ERROR"] = table[f"{quantity}_ERROR"].to_numpy()
        points[tag].to_csv(csvs[tag], index=False)
        table[columns].to_csv(records[tag], index=False)
    return tables


def _restart_ready(multiflow_dir: Path, flows: list[FlowSpec]) -> list[str]:
    """Names of flows whose completed initial design is NOT on disk."""
    return [f.name for f in flows if not (
        multiflow_dir / f"flow-{f.name}" / "auto-saved-results-HydroBayesCal"
        / "restart_data" / "initial-model-outputs.json").exists()]


def compile_flow_csvs(cfg: Config, flows: list[FlowSpec],
                      multiflow_dir: Path) -> dict[str, Path]:
    """Compile each flow's calibration-points CSV; returns {flow name: path}."""
    multiflow_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    print("compiling per-flow calibration points:")
    for f in flows:
        df = _away_from_liquid_boundaries(cfg, f.compile(cfg.crs_epsg))
        out = multiflow_dir / f"measurements-calibration-{f.name}.csv"
        df.to_csv(out, index=False)
        paths[f.name] = out
        print(f"  {f.name} (Q={f.discharge} m3/s): {len(df)} points, "
              f"U {df['SCALAR VELOCITY_DATA'].min():.3f}..{df['SCALAR VELOCITY_DATA'].max():.3f} "
              f"m/s -> {out.name}")
    return paths


def run_multiflow_smoke(cfg: Config, flows: list[FlowSpec], multiflow_dir: Path,
                        csv_paths: dict[str, Path], *, checkout: Path | None = None,
                        env: str | None = None, init_runs: int = 3) -> int:
    """Isolated end-to-end plumbing test: tiny 30 s runs in a copied model dir.

    Verifies steering/friction updates, extraction at every point, per-run JSON
    accumulation (rows must differ across ``init_runs`` distinct ks levels) and
    the combined matrix shape - without the full solver cost.
    """
    smoke = multiflow_dir / "smoke"
    model = smoke / "model"
    if smoke.exists():
        shutil.rmtree(smoke)
    model.mkdir(parents=True)
    src = cfg.model_dir
    inputs = ["geometry.slf", "boundaries.cli", "initial-conditions.slf"]
    # a hotstarted flow reads its seed instead of (or besides) the pre-wet initial
    # conditions, so it has to exist in the smoke model dir too
    inputs += sorted({f.hotstart_from for f in flows if f.hotstart_from})
    for name in inputs:
        if (src / name).exists():
            (model / name).symlink_to(src / name)
        elif name not in ("initial-conditions.slf",):
            raise SystemExit(f"smoke: {name} missing from {src}")
    shutil.copy2(src / cfg.friction_tbl, model / cfg.friction_tbl)

    base_cas = src / cfg.cas_file
    print("smoke steering files (DURATION 30 s):")
    for f in flows:
        write_flow_cas(cfg, base_cas, f, smoke, duration=30.0, dest_dir=model,
                       listing=50, graphic=50)
    config_path = emit_multiflow_config(
        cfg, smoke, model, flows, csv_paths, n_cpus=2, init_runs=init_runs,
        max_runs=init_runs, complete_bal_mode=False,
        # the plumbing checks below assert exactly init_runs rows; the adaptive
        # ladder may stop at a different size, so pin the design for the smoke test
        adaptive_init_runs=False)
    driver = stage_driver(smoke, "bal_telemac_multiflow.py", checkout=checkout,
                          also=("bal_telemac.py",))
    rc = launch(cfg, driver, config_path, env=env,
                note=f"multiflow SMOKE ({init_runs * len(flows)} tiny runs)")
    if rc != 0:
        return rc
    combined = (smoke / "auto-saved-results-HydroBayesCal" / "calibration-data"
                / "_".join(cfg.calibration.calibration_quantities)
                / "model-results-multiflow.csv")
    if not combined.exists():
        print(f"missing combined results {combined}")
        return 3
    arr = np.loadtxt(combined, delimiter=",", skiprows=1, ndmin=2)
    n_pts = (sum(len(pd.read_csv(p)) for p in csv_paths.values())
             * len(cfg.calibration.calibration_quantities))
    checks = {
        "shape": arr.shape == (init_runs, n_pts),
        "no all-zero run": bool(np.all(arr.any(axis=1))),
        "runs differ (ks varies)":
            len({tuple(np.round(r, 6)) for r in arr}) == init_runs,
    }
    for name, ok in checks.items():
        print(f"  smoke check - {name}: {'OK' if ok else 'FAIL'}")
    return 0 if all(checks.values()) else 3


def run_multiflow_calibration(
        cfg: Config, flows: list[FlowSpec], *, launch_mode: str = "prepare",
        checkout: Path | None = None, env: str | None = None,
        force: bool = False) -> int:
    """Compile CSVs + per-flow steering, emit config, stage drivers, then act by
    ``launch_mode``:

    * ``prepare`` - write everything, do not launch (default);
    * ``smoke``   - run the isolated plumbing test;
    * ``run``     - launch the full calibration (initial design + BAL);
    * ``resume``  - reuse completed per-flow initial designs (only_bal_mode) and
      go straight to surrogate + BAL.
    """
    cfg.ensure_dirs()
    multiflow_dir = cfg.calibration_path("multiflow")
    csv_paths = compile_flow_csvs(cfg, flows, multiflow_dir)

    if launch_mode == "smoke":
        return run_multiflow_smoke(cfg, flows, multiflow_dir, csv_paths,
                                   checkout=checkout, env=env)

    base_cas = cfg.model_path(cfg.cas_file)
    if not base_cas.exists():
        raise SystemExit(f"no built case at {base_cas}; run preprocessing.py first")
    print("per-flow steering files:")
    for f in flows:
        write_flow_cas(cfg, base_cas, f, multiflow_dir)

    resume = launch_mode == "resume"
    if resume:
        missing = _restart_ready(multiflow_dir, flows)
        if missing:
            raise SystemExit(f"resume: no completed initial design for {missing}; "
                             "run the initial design (launch_mode='run') first")

    config_path = emit_multiflow_config(
        cfg, multiflow_dir, cfg.model_dir, flows, csv_paths,
        n_cpus=cfg.telemac.n_processors, init_runs=cfg.calibration.init_runs,
        max_runs=cfg.calibration.max_runs, only_bal_mode=resume)
    print(f"multiflow config -> {config_path}"
          + (" (only_bal_mode: reusing completed initial designs)" if resume else ""))
    driver = stage_driver(multiflow_dir, "bal_telemac_multiflow.py",
                          checkout=checkout, also=("bal_telemac.py",))

    if launch_mode == "prepare":
        print("\nprepared (not launched). Launch with launch_mode='run' "
              "(or 'resume' to reuse completed initial designs).")
        print(f"  cd {driver.parent} && {sys.executable} {driver.name} "
              f"--config {config_path}")
        return 0

    if other_calibration_running(cfg.model_dir) and not force:
        raise SystemExit("another TELEMAC calibration appears active on this case "
                         "(shared friction.tbl); wait or pass force=True")
    note = ("multi-discharge calibration - BAL only (reusing initial designs)"
            if resume else "multi-discharge calibration")
    # the driver writes every tested set into the case's friction table, as in the
    # single-flow calibration: hand the built case back when it returns
    with case_as_built(cfg):
        return launch(cfg, driver, config_path, env=env, note=note)


# --------------------------------------------------------------------------- #
# calibration at several discharges, from the situations of the case file
# --------------------------------------------------------------------------- #
#: Folder of a calibration at several discharges, inside the calibration directory.
MULTIFLOW = "multiflow"


def calibration_situations(cfg: Config) -> list:
    """The flow situations a calibration fits, when it fits more than the case's own.

    ``calibration.situations`` (each measured at a discharge of its own), preceded by
    one at the discharge of the case when ``ground_truth`` holds measurements too.
    Empty when the case names no situation: the calibration is then the single-flow
    one of the case's discharge.
    """
    from axqua.config import ValidationSituation

    given = list(cfg.calibration.situations)
    if not given:
        return []
    gt = cfg.ground_truth
    if gt.sources or gt.measurements or gt.targets:
        # no sources of its own: its measurements are those of ground_truth, and its
        # boundary values are those of the case (the outflow level too, which
        # outflow_condition: elevation requires of every situation)
        from axqua.core.boundaries import outlet_level

        inflows = {} if cfg.boundaries.prescribed_flowrate is not None \
            else case_inflows(cfg)
        fixed = outlet_level(cfg.outflow_condition) == "fixed"
        given.insert(0, ValidationSituation(
            name="case discharge", prescribed_flowrate=cfg.boundaries.prescribed_flowrate,
            inflows=inflows,
            prescribed_elevation=cfg.boundaries.prescribed_elevation if fixed else None))
    return given


def situation_points(cfg: Config, situation, folder: Path, *,
                     vel_err_floor: float = campaigns.VELOCITY_ERROR_FLOOR,
                     depth_err_floor: float = campaigns.DEPTH_ERROR_FLOOR
                     ) -> tuple[Path, pd.DataFrame]:
    """The calibration-points CSV of one situation, by the rules of the single-flow table.

    Points outside the model or near a liquid boundary are left out
    (:func:`build_velocity_csv`), so a situation is held to the same rules as the
    measurements of the case's own discharge. A declared position quality adds its
    error only at the case's discharge: the error is the spread of the steady result
    around a point, and the steady result is that of the case's discharge, whose
    velocity gradients are not those of another (as in :func:`axqua.validation.measurements`).
    """
    import copy
    import math

    from axqua.validation import slug

    tag = slug(situation.name)
    there = copy.deepcopy(cfg)
    if situation.sources:                 # else the measurements of ground_truth
        there.ground_truth.sources = list(situation.sources)
        there.ground_truth.targets = None
        there.ground_truth.measurements = Path(folder) / f"ground-truth-{tag}.xlsx"
    there.calibration_csv = f"{MULTIFLOW}/measurements-calibration-{tag}.csv"
    q, own = situation.total_flowrate, case_discharge(cfg)
    positional = not situation.sources or (
        q is not None and own is not None and math.isclose(q, own, rel_tol=1e-3))
    gt = cfg.ground_truth
    if not positional and (gt.position_quality or gt.position_sigma):
        log.warning("%s: the positional error is left out, because the steady result "
                    "it is read from is at %s m3/s and the situation at %s m3/s",
                    situation.name, own, q)
    return build_velocity_csv(there, vel_err_floor=vel_err_floor,
                              depth_err_floor=depth_err_floor, positional=positional)


def write_situation_cas(cfg: Config, situation, folder: Path) -> tuple[str, float]:
    """Write ``steady2d-<situation>.cas`` into the model folder; returns its name and Q.

    The built steady case with the boundary values of the situation
    (:func:`axqua.validation.with_boundaries_of`, the validation's own rules) and its
    own results file. With ``calibration.hotstart`` it continues from the steady result
    of the case for 1.2 residence times at the situation's discharge
    (:func:`calibration_hotstart_duration`).
    """
    import copy

    from axqua.solvers.telemac.steering import fit_columns, write_hotstart_cas
    from axqua.validation import slug, with_boundaries_of

    tag = slug(situation.name)
    name = f"steady2d-{tag}.cas"
    q = situation.total_flowrate or cfg.boundaries.prescribed_flowrate
    results = f"{Path(cfg.results_slf).stem}-{tag}.slf"
    if cfg.calibration.hotstart:
        _steady_result(cfg)
        at_q = copy.deepcopy(cfg)
        at_q.boundaries.prescribed_flowrate = q
        duration = (float(situation.duration) if situation.duration
                    else calibration_hotstart_duration(at_q))
        text = write_hotstart_cas(cfg, duration, out_name=name,
                                  results_name=results).read_text()
    else:
        text = cfg.model_path(cfg.cas_file).read_text()
        if situation.duration:
            text = _patch_cas(text, "DURATION", f"{float(situation.duration)}")
    text, total, notes, _flow, _elev = with_boundaries_of(
        cfg, situation, text, folder, role="calibration")
    for note in notes:
        log.warning(note)
    text = _patch_cas(text, "TITLE", f"'{cfg.name} calibration {tag}'")
    text = _patch_cas(text, "RESULTS FILE", results)
    path = cfg.model_path(name)
    path.write_text("\n".join(fit_columns(text.splitlines())) + "\n")
    return name, float(total)


def run_situation_calibration(cfg: Config, *, calibration_quantities=None,
                              extraction_quantities=None,
                              vel_err_floor: float = campaigns.VELOCITY_ERROR_FLOOR,
                              depth_err_floor: float = campaigns.DEPTH_ERROR_FLOOR,
                              prepare_only: bool = False, resume: bool = False,
                              force: bool = False,
                              checkout: Path | None = None) -> int:
    """Calibrate one set of parameters against the measurements of several situations.

    Each situation of :func:`calibration_situations` gets its calibration points and
    its own steering file; HydroBayesCal's multi-flow driver runs every tested
    parameter set at every discharge and fits them jointly. The files go to
    ``calibration-validation/multiflow/``. The quantities and error floors are those
    of :func:`run_single_flow_calibration`. With ``calibration.structural_error:
    unreachable`` the driver runs twice (:func:`_launch_in_passes`); *resume* starts
    from the initial simulations an earlier calibration saved. Refused while a TELEMAC
    run of the case is active, unless *force*. Returns the driver exit code (0 when
    ``prepare_only``).
    """
    from axqua.validation import slug

    from axqua.core.errors import ConfigError

    empty = [s.name for s in cfg.calibration.situations if not s.sources]
    if empty:
        raise ConfigError(f"calibration situations without measurements: {empty}",
                          subject="calibration.situations",
                          remedy="Enter the measurement file of each situation.")
    unknown = [s.name for s in cfg.calibration.situations if s.total_flowrate is None]
    if unknown:
        raise ConfigError(f"calibration situations without a discharge: {unknown}",
                          subject="calibration.situations.prescribed_flowrate",
                          remedy="Enter the discharge at which each situation was "
                                 "measured.")
    if not prepare_only and not force and other_calibration_running(cfg.model_dir):
        raise ConfigError("a TELEMAC run of this case is active, and the calibration "
                          "would write into the same friction table",
                          subject=str(cfg.model_dir),
                          remedy="Wait until it has finished.")
    calibration_quantities = _set_quantities(cfg, calibration_quantities,
                                             extraction_quantities)
    print(f"calibration target(s): {calibration_quantities}")
    restore_as_built(cfg)                 # what a killed calibration left behind
    situations = calibration_situations(cfg)
    if not situations:
        raise ValueError("the case names no calibration situations")
    mode = structural_error_mode(cfg)
    tags = [slug(s.name) for s in situations]
    if len(set(tags)) != len(tags):
        raise ConfigError("two calibration situations have the same name",
                          subject="calibration.situations",
                          remedy="Give every situation a name of its own.")
    cfg.ensure_dirs()
    folder = cfg.calibration_path(MULTIFLOW)
    folder.mkdir(parents=True, exist_ok=True)
    ensure_graphic_output(cfg.model_path(cfg.cas_file))
    flows, csvs, tables = [], {}, []
    print("calibration situations:")
    for situation, tag in zip(situations, tags):
        csv, table = situation_points(cfg, situation, folder,
                                      vel_err_floor=vel_err_floor,
                                      depth_err_floor=depth_err_floor)
        cas, q = write_situation_cas(cfg, situation, folder)
        flows.append(FlowSpec(name=tag, discharge=q, kind="csv", values=csv))
        csvs[tag] = csv
        tables.append(table)
        print(f"  {situation.name}: Q = {q:g} m3/s, {len(table)} points -> {cas}")
    parameters = settle_zone_plan(cfg, tables, folder, resume=resume)

    def emit(complete: bool, only_bal: bool) -> Path:
        return emit_multiflow_config(
            cfg, folder, cfg.model_dir, flows, csvs, n_cpus=cfg.telemac.n_processors,
            init_runs=cfg.calibration.init_runs, max_runs=cfg.calibration.max_runs,
            complete_bal_mode=complete, only_bal_mode=only_bal, parameters=parameters)

    driver = stage_driver(folder, "bal_telemac_multiflow.py", checkout=checkout,
                          also=("bal_telemac.py",))
    restarts = {tag: folder / f"flow-{tag}" / RESTART_OUTPUTS for tag in tags}
    code = _launch_in_passes(
        cfg, mode=mode, resume=resume, prepare_only=prepare_only, emit=emit,
        driver=driver, restarts=restarts, csvs=csvs, folder=folder,
        note=f"HydroBayesCal calibration at {len(flows)} discharges")
    if code == 0 and not prepare_only:
        report(cfg, folder)
    return code


def report(cfg: Config, folder: Path) -> dict:
    """Write the calibration report of *folder* (:mod:`axqua.calibration_report`);
    a failure is a warning, never a failed calibration."""
    from axqua.calibration_report import write_report

    try:
        written = write_report(cfg, folder)
    except Exception as exc:                      # noqa: BLE001 - the calibration is done
        log.warning("the calibration report could not be written: %s", exc)
        return {}
    if written:
        print(f"calibration report -> {Path(folder) / 'report'}")
    return written


def settle_zone_plan(cfg: Config, tables, folder: Path, *, resume: bool = False) -> list:
    """The parameters a calibration varies, after the zone check, written down.

    :func:`axqua.calibration_zones.zone_plan` leaves out the roughness zones without a
    calibration point and lowers the largest ks of zones shallower than it; the plan
    is printed, its warnings logged, and written as ``zone-plan.json`` into *folder*.
    A *resume* continues the initial simulations of an earlier calibration, which were
    run with the parameters of that calibration: a plan that names other parameters or
    other ranges is refused.
    """
    from axqua.calibration_zones import log_plan, read_plan, zone_plan
    from axqua.core.errors import ConfigError

    plan = zone_plan(cfg, tables)
    log_plan(plan)
    if plan.excluded and not plan.parameters:
        raise ConfigError(
            "no calibration parameter is left: " + " ".join(plan.warnings),
            subject="geodata.roughness_table",
            remedy="Check in QGIS where the measurement points lie relative to the "
                   "roughness zones, and which zones the roughness table calibrates.")
    if resume:
        earlier = read_plan(folder)
        now = [[p.name, float(p.min), float(p.max)] for p in plan.parameters]
        before = ([[q["name"], float(q["min"]), float(q["max"])]
                   for q in earlier["parameters"]] if earlier else None)
        if before is not None and before != now:
            raise ConfigError(
                "the saved initial simulations were run with other calibration "
                f"parameters ({before}) than this calibration would vary ({now})",
                subject=str(Path(folder) / "zone-plan.json"),
                remedy="Start the calibration without 'resume'.")
    plan.write(folder)
    print(f"calibration parameters: {[p.name for p in plan.parameters]}")
    return plan.parameters


def _launch_in_passes(cfg: Config, *, mode: str, resume: bool, prepare_only: bool,
                      emit, driver: Path, restarts: dict[str, Path],
                      csvs: dict[str, Path], folder: Path, note: str,
                      env: str | None = None) -> int:
    """Launch a HydroBayesCal driver once, or in two passes for the model error.

    One pass (``structural_error: none``): initial simulations and active learning in
    one run of the driver. Two passes (``unreachable``): the driver first runs the
    initial simulations only; then :func:`apply_unreachable_misfit` adds to the error
    of each point and quantity what no simulated set reaches, widens the errors of
    groups of points that no single set fits together, and the driver runs
    again with the active learning only, on the saved simulations. With *resume*, the
    first pass is skipped: the initial simulations of an earlier calibration of the
    case are used, which have to exist for every situation (*restarts*: situation ->
    HydroBayesCal's ``initial-model-outputs.json``). *emit(complete, only_bal)* writes
    the driver configuration for HydroBayesCal's two execution switches.
    """
    from axqua.core.errors import ConfigError

    passes = mode == "unreachable"
    if prepare_only:
        config_path = emit(not passes, False)
        print(f"HydroBayesCal config -> {config_path}")
        if passes:
            print("structural_error: unreachable - the driver runs the initial "
                  "simulations first; the errors are then completed and the active "
                  "learning runs in a second pass.")
        print("\n--prepare-only: not launched. Run it with:")
        print(f"  cd {driver.parent} && {sys.executable} {driver.name} "
              f"--config {config_path}")
        return 0
    with case_as_built(cfg):
        if not resume:
            config_path = emit(not passes, False)
            print(f"HydroBayesCal config -> {config_path}")
            code = launch(cfg, driver, config_path, env=env,
                          note=note + (", initial simulations" if passes else ""))
            if code != 0 or not passes:
                return code
        missing = [tag for tag, path in restarts.items() if not path.is_file()]
        if missing:
            raise ConfigError(
                f"no saved initial simulations for {missing}",
                subject=str(folder),
                remedy="Run the calibration without 'resume' first.")
        if passes:
            records = {tag: Path(csv).with_name(f"model-error-{tag}.csv")
                       for tag, csv in csvs.items()}
            try:
                apply_unreachable_misfit(csvs, restarts,
                                         list(cfg.calibration.calibration_quantities),
                                         records)
            except (ValueError, KeyError) as exc:
                raise ConfigError(
                    f"the model error could not be computed from the initial "
                    f"simulations: {exc}", subject=str(folder),
                    remedy="The initial simulations are kept. Correct the cause and "
                           "start the calibration again with 'resume'.") from exc
            for record in records.values():
                print(f"model error at points the model cannot reach -> {record}")
        config_path = emit(True, True)
        print(f"HydroBayesCal config -> {config_path}")
        return launch(cfg, driver, config_path, env=env,
                      note=note + ", active learning on the saved simulations")
