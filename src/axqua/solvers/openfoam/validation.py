"""The calibrated OpenFOAM model in another flow situation, against what was measured then.

The counterpart of :mod:`axqua.validation` for the OpenFOAM model. The situation comes with its discharge, its measurements and the 2D result of TELEMAC at that discharge (``seed``), prepared by :mod:`axqua.openfoam_situations` because this backend does not run TELEMAC. The model is built at that discharge with the calibrated values (one ``ks`` for the whole bed and the k-epsilon coefficients), run, and read at the measurement points, which are placed at their measured height above the bed in the model's own column, as in the calibration.

Two variants:

* the settings of the calibration runs (``free_surface=False``, the default): rigid lid, coarse lattice, the same run length and averaging, so the validation tests the calibrated values under the conditions they were calibrated in;
* the production model (``free_surface=True``): the case's own resolution and free surface (two phases), which shows what the rigid lid of the calibration left out. It costs hours.

The comparison is the horizontal speed at 0.6 h above the riverbed (``U_MAG``), for the reason given in :data:`axqua.solvers.openfoam.calibration.SITUATION_QUANTITIES`.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger("axqua")

#: Sub-folder of ``calibration-validation/openfoam`` for the validation.
FOLDER = "validation"
QUANTITY = "U_MAG"


def _apply(cfg, values: Mapping[str, float]) -> list[str]:
    """The calibrated values into *cfg*; notes for those at a limit of their range."""
    from axqua.solvers.openfoam.calibration import apply_posterior

    notes: list[str] = []
    try:
        apply_posterior(cfg, values)
    except SystemExit as exc:
        # at a limit of the prior: still the calibrated value, which the report says
        notes.append(str(exc).splitlines()[0] + " " + " ".join(
            line.strip() for line in str(exc).splitlines()[1:3]))
        apply_posterior(cfg, values, bound_margin=0.0)
    return notes


def case_config(cfg, situation: Mapping[str, Any], values: Mapping[str, float], *,
                folder: Path, free_surface: bool = False, cell_size_factor: float = 4.0,
                end_time: float = 600.0, write_interval: float = 20.0,
                n_avg_timesteps: int = 5, n_processors: int | None = None):
    """The configuration of the validation run, and notes about it."""
    from axqua.solvers.openfoam.calibration import campaign_config, situation_config

    there = situation_config(cfg, situation, folder)
    notes = _apply(there, values)
    ranks = int(n_processors or there.openfoam.n_processors or 1)
    if free_surface:
        run = copy.deepcopy(there)
        run.openfoam.n_processors = ranks
        run.openfoam_dir = Path(folder) / "case-free-surface"
    else:
        friction = there.openfoam.friction_ks
        run = campaign_config(there, cell_size_factor=cell_size_factor, end_time=end_time,
                              write_interval=write_interval, n_processors=ranks,
                              n_avg_timesteps=n_avg_timesteps)
        run.openfoam.friction_ks = friction
        run.openfoam_dir = Path(folder) / "case"
    return there, run, notes


def model_at(case_dir: Path, targets: pd.DataFrame, n_avg_timesteps: int) -> pd.DataFrame:
    """The velocity of the finished run at the targets (x, y, z), averaged over the last
    *n_avg_timesteps* write times, read as the calibration reads it (HydroBayesCal's
    reader, nearest cell center in the water)."""
    from scipy.spatial import cKDTree

    from axqua.hbc import require_hbc

    require_hbc()
    from hydroBayesCal.openfoam.control_openfoam import OpenFOAMController

    coords, velocity, tke, _alpha = OpenFOAMController(str(case_dir)).extract_fields_from_vtk(
        n_avg_timesteps=n_avg_timesteps)[:4]
    xyz = targets[["x", "y", "z"]].to_numpy(float)
    distance, index = cKDTree(coords).query(xyz)
    u = np.asarray(velocity)[index]
    out = pd.DataFrame({"u_x": u[:, 0], "u_y": u[:, 1], "u_z": u[:, 2],
                        "speed": np.hypot(u[:, 0], u[:, 1]),
                        "distance_to_cell": distance})
    out["tke"] = np.asarray(tke)[index] if tke is not None else np.nan
    return out


def run_validation(backend, cfg, situation: Mapping[str, Any], *,
                   values: Mapping[str, float], ctx: Any = None,
                   free_surface: bool = False, cell_size_factor: float = 4.0,
                   end_time: float = 600.0, write_interval: float = 20.0,
                   n_avg_timesteps: int = 5, n_processors: int | None = None,
                   point_filter=None, compare_only: bool = False):
    """Build, run and compare; the report goes to ``calibration-validation/openfoam/validation/``.

    *situation* is a mapping as :func:`axqua.openfoam_situations.situation_specs` makes it, *values* the calibrated values (``ks``, ``Cmu``, ...), *point_filter* that of :func:`axqua.openfoam_situations.point_filter`. With *compare_only* the run of an earlier validation is read again.
    """
    from axqua import validation as shared
    from axqua.core.errors import ConfigError
    from axqua.solvers.openfoam.calibration import CAMPAIGN_SUBDIR, _velocity_csv
    from axqua.solvers.openfoam.hotstart import load_hotstart

    tag = str(situation["tag"])
    variant = "free-surface" if free_surface else "calibration-settings"
    folder = Path(cfg.calibration_dir) / CAMPAIGN_SUBDIR / FOLDER / f"{tag}-{variant}"
    folder.mkdir(parents=True, exist_ok=True)
    seed = situation.get("seed")
    if not seed or not Path(seed).is_file():
        raise ConfigError(f"no 2D result at the discharge of {situation['name']}",
                          subject=str(seed or tag),
                          remedy="Submit the validation as a job: it runs TELEMAC at the "
                                 "discharge of the situation first.")
    there, run, notes = case_config(
        cfg, situation, values, folder=folder, free_surface=free_surface,
        cell_size_factor=cell_size_factor, end_time=end_time,
        write_interval=write_interval, n_avg_timesteps=n_avg_timesteps,
        n_processors=n_processors)
    state = load_hotstart(there, seed)
    case_dir = Path(run.openfoam_case_dir)
    if not compare_only:
        from axqua.solvers.openfoam.case import build_case

        log.info("OpenFOAM validation %s (%s) at %.4g m3/s with %s", situation["name"],
                 variant, float(situation["discharge"]),
                 ", ".join(f"{k} = {v:.4g}" for k, v in values.items()))
        built = build_case(run, state=state, uniform_bed_ks=True)
        try:
            # the columns of the lattice just built, so the points are placed without
            # building it a second time (and again on every compare_only)
            from axqua.model_column import write_column_cache

            write_column_cache(run, case_dir.parent / "column-geometry.npz",
                               mesh=built.mesh, state=state)
        except Exception as exc:  # noqa: BLE001 - the placer rebuilds if this is absent
            log.debug("column cache not written (%s: %s)", type(exc).__name__, exc)
        backend.run(run, None, ctx=ctx, n_processors=run.openfoam.n_processors)
    elif not (case_dir / "constant" / "polyMesh").is_dir():
        raise ConfigError(f"no earlier OpenFOAM validation run in {case_dir}",
                          subject=str(case_dir), remedy="Run the validation first.")

    keep = None if point_filter is None else (lambda df: point_filter(there, df))
    csv = _velocity_csv(there, run, out_dir=folder, calibration_quantities=(QUANTITY,),
                        extraction_quantities=(QUANTITY,),
                        vel_err_floor=0.0, vertical_err_floor=0.0, state=state,
                        path=folder / f"measurements-{tag}.csv", point_filter=keep,
                        # a validation compares at every point it has, as the TELEMAC
                        # one does; the floor of six belongs to a calibration
                        min_points=1)
    targets = pd.read_csv(csv)
    model = model_at(case_dir, targets, 1 if free_surface else n_avg_timesteps)
    measured = targets[f"{QUANTITY}_DATA"].to_numpy(float)
    own = targets[f"{QUANTITY}_ERROR"].to_numpy(float)
    error = np.sqrt(own ** 2 + (float(cfg.calibration.measurement_error) * measured) ** 2)
    table = pd.DataFrame({"id": targets["id"], "x": targets["x"], "y": targets["y"],
                          "z": targets["z"], "inside_model": True,
                          "u_mag_measured": measured, "u_mag_error": error,
                          "u_mag_modelled": model["speed"].to_numpy(float)})
    table["u_mag_difference"] = table["u_mag_modelled"] - table["u_mag_measured"]
    for column in ("u_x", "u_y", "u_z", "tke", "distance_to_cell"):
        table[f"model_{column}"] = model[column].to_numpy(float)
    summary = {QUANTITY: shared.statistics(measured, table["u_mag_modelled"], error)}
    report = shared.ValidationReport(name=f"{situation['name']} (OpenFOAM, {variant})",
                                     table=table, summary=summary, n_points=len(table),
                                     time=float(run.openfoam.end_time), notes=notes)
    stem = f"validation-openfoam-{tag}-{variant}"
    table.to_csv(folder.parent / f"{stem}.csv", index=False)
    document = report.as_dict()
    document["run"] = {"case": str(case_dir), "seed": str(seed), "values": dict(values),
                       "discharge": float(situation["discharge"]), "variant": variant}
    (folder.parent / f"{stem}.json").write_text(json.dumps(document, indent=2, default=str),
                                                encoding="utf-8")
    try:
        shared._figure(report, folder.parent / f"{stem}.png")
    except Exception as exc:                        # noqa: BLE001 - the numbers are written
        log.warning("the validation figure could not be drawn: %s", exc)
    numbers = summary[QUANTITY]
    if numbers.get("n"):
        log.info("OpenFOAM validation %s: n = %d, speed %+.3f m/s (%+.0f %%), RMSE %.3f, "
                 "correlation %.2f, within the measurement error %.0f %%",
                 situation["name"], numbers["n"], numbers["bias"],
                 100 * numbers["relative_bias"], numbers["rmse"],
                 numbers.get("correlation", float("nan")),
                 100 * numbers.get("within_error", float("nan")))
    if ctx is not None:
        ctx.record(f"Validation {situation['name']} (OpenFOAM): model against measurement",
                   folder.parent / f"{stem}.csv", kind="table")
        ctx.record(f"Validation {situation['name']} (OpenFOAM): figure",
                   folder.parent / f"{stem}.png", kind="figure")
        ctx.record(f"Validation {situation['name']} (OpenFOAM case)", case_dir / "case.foam",
                   kind="file", description="open in ParaView")
    return report
