"""Validation: the calibrated model against measurements of another flow situation.

A calibration finds the parameters with which the model reproduces one set of
measurements. Whether those parameters describe the reach, or only that one day, shows
when the model is run for **another situation** - another discharge, measured in
another survey - and compared with what was measured then. That is what this module
does, and it is the only kind of validation aXqua offers: the measurements of one
survey are taken within hours in one flow field, so holding some of them back
(leave-one-out, k-fold, a random share) tests how well the model interpolates within
that flow field and says nothing about another discharge.

The validation run is **the calibrated model with other boundary values**. Its steering
file is derived from the built steady case and differs in exactly these lines: the
prescribed discharges and water levels of the situation, the names of its own result
and friction files, and the calibrated values. Mesh, bed, numerical settings and
initial state are untouched, so a difference between model and measurement cannot come
from a model that was built differently.

The calibrated values are the **joint posterior maximum** of the newest calibration
(:func:`calibrated_parameters`), read from what HydroBayesCal saved. They are written
into copies of the files HydroBayesCal changed during the calibration, by the same
rules: ``zone<N>`` is the coefficient of friction zone N in the friction table,
``f.<NAME>`` a constant in the user Fortran file, and any other name a keyword of the
steering file.

The model is read at the measurement points on the **geometry file** of the model
(see :mod:`axqua.postproc.selafin_vtk` for why not on the result), with the mesh's
own triangles. A point outside the mesh has no model value; it is counted and left out
of the statistics.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("axqua")

__all__ = ["CalibratedSet", "ValidationReport", "ValidationSetup", "boundaries_of",
           "build", "calibrated_parameters", "compare", "evaluate", "inside_model",
           "situation_named", "slug", "statistics"]

RESULTS = "auto-saved-results-HydroBayesCal"
FOLDER = "validation"

#: Units of the quantities a hydraulic validation compares, for the figure.
UNITS = {"SCALAR VELOCITY": "m/s", "WATER DEPTH": "m", "FREE SURFACE": "m",
         "VELOCITY U": "m/s", "VELOCITY V": "m/s"}


def slug(name: str) -> str:
    """A name that can be part of a file name."""
    return re.sub(r"[^A-Za-z0-9]+", "-", str(name)).strip("-").lower() or "validation"


def situation_named(cfg, name: str | None = None):
    """The validation situation called *name*; the only one when no name is given."""
    from axqua.core.errors import ConfigError

    situations = list(cfg.calibration.validation)
    if not situations:
        raise ConfigError(
            "the case names no validation data", subject="calibration.validation",
            remedy="Enter a point layer with measurements of another flow situation "
                   "and the discharge of that situation.")
    if name:
        for situation in situations:
            if situation.name == name or slug(situation.name) == slug(name):
                return situation
        raise ConfigError(
            f"the case has no validation situation {name!r}",
            subject="calibration.validation",
            remedy="It has: " + ", ".join(s.name for s in situations))
    if len(situations) > 1:
        raise ConfigError(
            "the case has several validation situations; name one",
            subject="calibration.validation",
            remedy="They are: " + ", ".join(s.name for s in situations))
    return situations[0]


# ------------------------------------------------------------------ calibrated values


@dataclass
class CalibratedSet:
    """The parameter values a calibration ended with."""

    names: list[str]
    values: list[float]
    source: Path
    estimate: str = "joint posterior maximum"

    def as_dict(self) -> dict[str, float]:
        return {name: float(value) for name, value in zip(self.names, self.values)}


def calibrated_parameters(cfg) -> CalibratedSet:
    """The joint posterior maximum of the newest calibration of this case.

    HydroBayesCal saves one dictionary per calibration quantity; the newest is read.
    An older HydroBayesCal that did not save the joint maximum gives the mean of the
    posterior sample instead, and :attr:`CalibratedSet.estimate` says so.
    """
    import pickle

    from axqua.core.errors import ConfigError

    root = Path(cfg.calibration_dir) / RESULTS / "calibration-data"
    found = sorted(root.glob("*/BAL_dictionary.pkl"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise ConfigError(
            f"no finished calibration in {Path(cfg.calibration_dir)}",
            subject="calibration",
            remedy="Run the calibration first; the validation uses its result.")
    source = found[-1]
    with open(source, "rb") as handle:
        data = pickle.load(handle)          # nosec B301 - written by the calibration
    names = [str(name) for name in data.get("calibration_parameters") or []]
    optimum = data.get("joint_optimum") or []
    estimate = "joint posterior maximum"
    values = None
    if optimum and optimum[-1] is not None:
        values = np.asarray(optimum[-1], dtype=float).ravel()
    if values is None or values.size != len(names):
        posterior = data.get("posterior") or []
        if not posterior:
            raise ConfigError(f"{source} holds no posterior", subject="calibration",
                              remedy="Run the calibration again.")
        values = np.asarray(posterior[-1], dtype=float).mean(axis=0).ravel()
        estimate = "posterior mean"
    if values.size != len(names) or not names:
        raise ConfigError(f"{source} does not name its parameters",
                          subject="calibration")
    return CalibratedSet(names=names, values=[float(v) for v in values], source=source,
                         estimate=estimate)


# --------------------------------------------------------------------- the situation


def boundaries_of(cfg) -> list[dict[str, Any]]:
    """The liquid boundaries of the built case, as a person needs them to state the
    discharges of another situation: number, inflow or outflow, calibrated discharge."""
    from axqua.solvers.telemac import boundary

    meta = cfg.model_path(cfg.liquid_boundaries_json)
    if not meta.is_file():
        return []
    return [{"index": lb.index, "kind": lb.kind, "discharge": lb.discharge,
             "n_nodes": lb.n_nodes}
            for lb in sorted(boundary.load_liquid_boundaries(meta), key=lambda b: b.index)]


def _liquids_for(cfg, situation) -> tuple[list, float]:
    """The liquid boundaries with the discharges of *situation*, and their total."""
    from axqua.core.errors import ConfigError
    from axqua.solvers.telemac import boundary

    meta = cfg.model_path(cfg.liquid_boundaries_json)
    if not meta.is_file():
        raise ConfigError(f"{meta.name} is missing in {cfg.model_dir}",
                          subject="preprocessing",
                          remedy="Build the case first (tab Preprocessing).")
    liquids = boundary.load_liquid_boundaries(meta)
    inflows = [lb for lb in liquids if lb.kind == "inflow"]
    subject = "calibration.validation"
    if situation.inflows:
        known = {lb.index for lb in inflows}
        wrong = sorted(set(situation.inflows) - known)
        missing = sorted(known - set(situation.inflows))
        if wrong or missing:
            raise ConfigError(
                "the discharges of the validation situation do not match the inflows "
                "of the model, which are the liquid boundaries "
                + ", ".join(str(i) for i in sorted(known))
                + (f"; not an inflow: {wrong}" if wrong else "")
                + (f"; without a discharge: {missing}" if missing else ""),
                subject=f"{subject}.inflows")
        for lb in inflows:
            lb.discharge = float(situation.inflows[lb.index])
        return liquids, float(sum(situation.inflows.values()))
    if situation.prescribed_flowrate is None:
        raise ConfigError(
            f"the validation situation {situation.name!r} names no discharge",
            subject=f"{subject}.prescribed_flowrate",
            remedy="Enter the discharge at which the validation data were measured.")
    total = float(situation.prescribed_flowrate)
    if inflows and all(lb.discharge is not None for lb in inflows):
        before = sum(float(lb.discharge) for lb in inflows)
        for lb in inflows:                      # the same split, at another total
            lb.discharge = float(lb.discharge) * total / before if before else None
    return liquids, total


# --------------------------------------------------------------------- applying values


def _friction_rows(text: str, zones: dict[str, float]) -> tuple[str, list[str]]:
    """The friction table with the coefficients of *zones* replaced."""
    wanted = {str(name)[4:].strip(): value for name, value in zones.items()}
    done: list[str] = []
    lines = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and not line.lstrip().startswith("*") and parts[0] in wanted:
            parts[2] = f"{wanted[parts[0]]:.6g}"
            done.append(parts[0])
            line = "\t".join(parts)
        lines.append(line)
    return "\n".join(lines) + "\n", done


def _keyword(text: str, key: str) -> str | None:
    found = re.search(rf"^{re.escape(key)}\s*:\s*(.*)$", text, flags=re.M)
    return found.group(1).strip().strip("'\"") if found else None


@dataclass
class ValidationSetup:
    """The files of one validation run."""

    name: str
    cas: Path
    results: Path
    parameters: dict[str, float]
    flowrate: float
    flowrates: str
    elevations: str
    friction: Path | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "cas": str(self.cas), "results": str(self.results),
                "parameters": dict(self.parameters), "flowrate": self.flowrate,
                "prescribed_flowrates": self.flowrates,
                "prescribed_elevations": self.elevations,
                "friction": str(self.friction) if self.friction else "",
                "notes": list(self.notes)}


def build(cfg, situation, parameters: dict[str, float]) -> ValidationSetup:
    """Write the steering file of the validation run into the model folder."""
    from axqua import bayescal
    from axqua.core.errors import ConfigError
    from axqua.hydraulics import resolve_outflow_wse
    from axqua.solvers.telemac import steering

    bayescal.restore_as_built(cfg)              # a killed calibration restores nothing
    base = cfg.model_path(cfg.cas_file)
    if not base.is_file():
        raise ConfigError(f"{base.name} is missing in {cfg.model_dir}",
                          subject="preprocessing",
                          remedy="Build the case first (tab Preprocessing).")
    tag = slug(situation.name)
    model = Path(cfg.model_dir)
    text = base.read_text(encoding="utf-8")
    notes: list[str] = []

    # ---- the boundary values of the situation --------------------------------------
    liquids, total = _liquids_for(cfg, situation)
    there = copy.deepcopy(cfg)
    there.boundaries.prescribed_flowrate = total
    condition = cfg.boundaries.outflow_condition
    if situation.prescribed_elevation is not None:
        there.boundaries.prescribed_elevation = float(situation.prescribed_elevation)
        if condition != "elevation":
            notes.append("The outflow water level of the validation situation is not "
                         f"used: the case computes it ({condition}).")
    elif condition == "elevation":
        raise ConfigError(
            "the case prescribes the water level at the outflow, and the validation "
            f"situation {situation.name!r} names none",
            subject="calibration.validation.prescribed_elevation",
            remedy="Enter the outflow water level at which the validation data were "
                   "measured. The level of the calibration belongs to its discharge.")
    given = cfg.boundaries.stage_discharge
    if condition == "stage_discharge" and (given is None or not Path(given).exists()):
        # The case has no measured rating curve: its outflow level was derived from
        # the channel geometry for the calibrated discharge. The level of this
        # situation is derived the same way, for its own discharge.
        from axqua.workflow import synthesize_rating_if_missing

        own = Path(cfg.calibration_dir) / FOLDER / f"rating-{tag}.csv"
        own.parent.mkdir(parents=True, exist_ok=True)
        own.unlink(missing_ok=True)
        there.boundaries.stage_discharge = own
        synthesize_rating_if_missing(there, total)
    stage = None if condition == "free" else float(resolve_outflow_wse(there, total))
    flow, elev, _profiles = steering._prescribed_arrays(there, liquids, total, stage)
    text = bayescal._patch_cas(text, "PRESCRIBED FLOWRATES", flow)
    text = bayescal._patch_cas(text, "PRESCRIBED ELEVATIONS", elev)
    text = bayescal._patch_cas(text, "TITLE", f"'{cfg.name} validation {tag}'")
    results = f"{Path(cfg.results_slf).stem}-validation-{tag}.slf"
    text = bayescal._patch_cas(text, "RESULTS FILE", results)
    if situation.duration:
        text = bayescal._patch_cas(text, "DURATION", f"{float(situation.duration)}")

    # ---- the calibrated values -----------------------------------------------------
    zones = {name: value for name, value in parameters.items()
             if name.lower().startswith("zone")}
    constants = {name[2:].strip(): value for name, value in parameters.items()
                 if name.lower().startswith("f.")}
    unsupported = [name for name in parameters if name.lower().startswith("gaia")]
    if unsupported:
        raise ConfigError(
            "the validation run cannot yet set the calibrated sediment transport "
            "parameters: " + ", ".join(unsupported), subject="calibration.parameters")
    keywords = {name: value for name, value in parameters.items()
                if name not in zones and not name.lower().startswith("f.")}

    friction_path = None
    if zones:
        table = _keyword(text, "FRICTION DATA FILE")
        if not table or not (model / table).is_file():
            raise ConfigError("the steering file names no friction table, but the "
                              "calibration ended with values for friction zones",
                              subject="friction")
        new_table, done = _friction_rows((model / table).read_text(encoding="utf-8"),
                                         zones)
        missing = sorted(set(name[4:].strip() for name in zones) - set(done))
        if missing:
            raise ConfigError("the friction table has no zone " + ", ".join(missing),
                              subject="friction")
        friction_path = model / f"{Path(table).stem}-validation-{tag}.tbl"
        friction_path.write_text(new_table, encoding="utf-8")
        text = bayescal._patch_cas(text, "FRICTION DATA FILE", friction_path.name)
    if constants:
        folder = _keyword(text, "FORTRAN FILE")
        if not folder or not (model / folder).is_dir():
            raise ConfigError("the steering file names no Fortran folder, but the "
                              "calibration ended with values for constants in it",
                              subject="calibration.parameters")
        copied = model / f"{folder}-validation-{tag}"
        shutil.rmtree(copied, ignore_errors=True)
        shutil.copytree(model / folder, copied)
        for name, value in constants.items():
            hit = False
            for source in sorted(copied.glob("*.[fF]*")):
                code = source.read_text(encoding="utf-8", errors="replace")
                new, count = re.subn(rf"^(\s*){re.escape(name)}\s*=.*$",
                                     rf"\g<1>{name} = {value:.8g}", code, flags=re.M)
                if count:
                    source.write_text(new, encoding="utf-8")
                    hit = True
            if not hit:
                raise ConfigError(f"no line '{name} = ...' in {folder}",
                                  subject="calibration.parameters")
        text = bayescal._patch_cas(text, "FORTRAN FILE", f"'{copied.name}'")
    for name, value in keywords.items():
        text = bayescal._set_cas(text, name, f"{value:.8g}")

    cas = model / f"validation-{tag}.cas"
    cas.write_text(text, encoding="utf-8")
    log.info("validation %s: %s at %.4g m3/s (PRESCRIBED FLOWRATES %s; ELEVATIONS %s), "
             "calibrated values %s", situation.name, cas.name, total, flow, elev,
             ", ".join(f"{k}={v:.4g}" for k, v in parameters.items()))
    return ValidationSetup(name=situation.name, cas=cas, results=model / results,
                           parameters=dict(parameters), flowrate=total, flowrates=flow,
                           elevations=elev, friction=friction_path, notes=notes)


# ------------------------------------------------------------------- measured values


def measurements(cfg, situation):
    """The measurements of *situation* as calibration points are written:
    ``id, x, y, z, <QUANTITY>_DATA, <QUANTITY>_ERROR``. Returns ``(path, table)``.

    Compiled by the same code as the calibration data, so that a velocity means the
    same in both, into files of their own in the calibration folder.
    """
    from axqua import bayescal
    from axqua.core.errors import ConfigError

    if not situation.sources:
        raise ConfigError(
            f"the validation situation {situation.name!r} names no measurements",
            subject="calibration.validation.sources",
            remedy="Enter the point layer with the validation data.")
    tag = slug(situation.name)
    there = copy.deepcopy(cfg)
    folder = Path(cfg.calibration_dir) / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    there.ground_truth.sources = list(situation.sources)
    there.ground_truth.targets = None
    there.ground_truth.measurements = folder / f"ground-truth-{tag}.xlsx"
    there.calibration_csv = f"{FOLDER}/measurements-{tag}.csv"
    return bayescal.build_velocity_csv(there)


def inside_model(cfg, x, y) -> np.ndarray:
    """Whether each point lies on the mesh of the built model."""
    from matplotlib.tri import Triangulation

    from axqua.core.selafin import SelafinFile

    geometry = SelafinFile(cfg.model_path(cfg.geometry_slf))
    finder = Triangulation(geometry.x, geometry.y, geometry.ikle).get_trifinder()
    return np.asarray(finder(np.asarray(x, float), np.asarray(y, float))) >= 0


# ------------------------------------------------------------------------ comparison


@dataclass
class ValidationReport:
    """Model against measurement, point by point and in summary."""

    name: str
    table: Any                                   # pandas DataFrame, one row per point
    summary: dict[str, dict[str, float]]         # per quantity
    n_points: int = 0
    n_outside: int = 0
    time: float = 0.0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "n_points": self.n_points,
                "n_outside": self.n_outside, "time": self.time,
                "summary": self.summary, "notes": list(self.notes)}


def statistics(measured, modelled, error=None) -> dict[str, float]:
    """The numbers a validation is judged by.

    ``bias`` is the mean of model minus measurement, so a negative bias is a model
    that is too slow or too shallow. ``within_error`` is the share of points at which
    the two differ by no more than the error of the measurement, and
    ``within_two_errors`` by no more than twice that; for a model without systematic
    deviation and a correctly stated error they are about 0.68 and 0.95.
    """
    measured = np.asarray(measured, dtype=float)
    modelled = np.asarray(modelled, dtype=float)
    keep = np.isfinite(measured) & np.isfinite(modelled)
    m, s = measured[keep], modelled[keep]
    if m.size == 0:
        return {"n": 0}
    diff = s - m
    mean = float(m.mean())
    out = {
        "n": int(m.size),
        "mean_measured": mean,
        "mean_modelled": float(s.mean()),
        "bias": float(diff.mean()),
        "relative_bias": float(diff.mean() / mean) if mean else float("nan"),
        "mae": float(np.abs(diff).mean()),
        "rmse": float(np.sqrt((diff ** 2).mean())),
        "correlation": float(np.corrcoef(m, s)[0, 1])
        if m.size > 2 and m.std() > 0 and s.std() > 0 else float("nan"),
    }
    if error is not None:
        sigma = np.asarray(error, dtype=float)[keep]
        usable = sigma > 0
        if usable.any():
            ratio = np.abs(diff[usable]) / sigma[usable]
            out["within_error"] = float((ratio <= 1.0).mean())
            out["within_two_errors"] = float((ratio <= 2.0).mean())
    return out


def compare(points, geometry: str | Path, result: str | Path, *, name: str = "validation",
            measurement_error: float = 0.10, wet_depth: float = 0.01
            ) -> ValidationReport:
    """Read the model at *points* and compare it with what was measured there.

    *points* is a table in the form of the calibration points. The error of a
    measurement is its own (``<QUANTITY>_ERROR``) combined with *measurement_error*
    times the measured value, which is how the calibration weighs a measurement.
    """
    import pandas as pd
    from matplotlib.tri import LinearTriInterpolator

    from axqua.core.selafin import SelafinFile
    from axqua.model_column import safe_triangulation

    model = SelafinFile(geometry)
    values = SelafinFile(result)
    if values.npoin2 != model.npoin2:
        raise ValueError(f"{Path(result).name} has {values.npoin2} nodes and "
                         f"{Path(geometry).name} has {model.npoin2}; they are not the "
                         "same mesh")
    frame = values.frame(-1)
    tri = safe_triangulation(model.x, model.y, model.ikle, source=Path(geometry).name)
    x, y = points["x"].to_numpy(float), points["y"].to_numpy(float)
    inside = np.asarray(tri.get_trifinder()(x, y)) >= 0
    table = pd.DataFrame({"id": points["id"] if "id" in points else range(1, len(x) + 1),
                          "x": x, "y": y, "inside_model": inside})
    summary: dict[str, dict[str, float]] = {}
    notes: list[str] = []
    quantities = [column[:-5] for column in points.columns if column.endswith("_DATA")]
    depth = frame.get("WATER DEPTH")
    for quantity in quantities:
        if quantity not in frame:
            notes.append(f"The result has no {quantity}; it was not compared.")
            continue
        field_values = np.asarray(frame[quantity], dtype=float)[:model.npoin2]
        modelled = np.asarray(LinearTriInterpolator(tri, field_values)(x, y)
                              .filled(np.nan), dtype=float)
        measured = points[f"{quantity}_DATA"].to_numpy(float)
        own = points[f"{quantity}_ERROR"].to_numpy(float) \
            if f"{quantity}_ERROR" in points else np.zeros_like(measured)
        error = np.sqrt(own ** 2 + (measurement_error * np.abs(measured)) ** 2)
        key = quantity.lower().replace(" ", "_")
        table[f"{key}_measured"] = measured
        table[f"{key}_error"] = error
        table[f"{key}_modelled"] = modelled
        table[f"{key}_difference"] = modelled - measured
        summary[quantity] = statistics(measured[inside], modelled[inside], error[inside])
    if depth is not None:
        wet = np.asarray(LinearTriInterpolator(
            tri, np.asarray(depth, dtype=float)[:model.npoin2])(x, y).filled(np.nan))
        table["dry_in_model"] = inside & (wet <= wet_depth)
        dry = int(table["dry_in_model"].sum())
        if dry:
            notes.append(f"{dry} of the {int(inside.sum())} points on the mesh are dry "
                         "in the model, although water was measured there.")
    outside = int((~inside).sum())
    if outside:
        notes.append(f"{outside} of {len(x)} points lie outside the model and are not "
                     "part of the statistics.")
    return ValidationReport(name=name, table=table, summary=summary, n_points=len(x),
                            n_outside=outside, time=float(values.times[-1]), notes=notes)


def evaluate(cfg, situation, setup: ValidationSetup | None = None) -> ValidationReport:
    """Compare the validation result of *situation* with its measurements and write
    the report: a table per point, a summary, and a figure of model against
    measurement. Everything goes into ``calibration-validation/validation/``."""
    tag = slug(situation.name)
    result = setup.results if setup is not None else \
        Path(cfg.model_dir) / f"{Path(cfg.results_slf).stem}-validation-{tag}.slf"
    _path, points = measurements(cfg, situation)
    report = compare(points, cfg.model_path(cfg.geometry_slf), result,
                     name=situation.name,
                     measurement_error=float(cfg.calibration.measurement_error),
                     wet_depth=float(cfg.hydrodynamics.wet_depth))
    folder = Path(cfg.calibration_dir) / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    report.table.to_csv(folder / f"validation-{tag}.csv", index=False)
    document = report.as_dict()
    if setup is not None:
        document["run"] = setup.as_dict()
    (folder / f"validation-{tag}.json").write_text(
        json.dumps(document, indent=2, default=str), encoding="utf-8")
    try:
        _figure(report, folder / f"validation-{tag}.png")
    except Exception as exc:                    # noqa: BLE001 - the numbers are written
        log.warning("the validation figure could not be drawn: %s", exc)
    for quantity, numbers in report.summary.items():
        if numbers.get("n"):
            log.info("validation %s, %s: n = %d, bias %+.3f (%+.0f %%), RMSE %.3f, "
                     "within the measurement error %.0f %%", situation.name, quantity,
                     numbers["n"], numbers["bias"], 100 * numbers["relative_bias"],
                     numbers["rmse"], 100 * numbers.get("within_error", float("nan")))
    return report


def _figure(report: ValidationReport, path: Path) -> None:
    """Model against measurement, one panel per quantity, with the line of equality."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    quantities = [q for q, numbers in report.summary.items() if numbers.get("n")]
    if not quantities:
        return
    fig, axes = plt.subplots(1, len(quantities), figsize=(5.2 * len(quantities), 4.8),
                             squeeze=False)
    inside = report.table["inside_model"].to_numpy(bool)
    for axis, quantity in zip(axes[0], quantities):
        key = quantity.lower().replace(" ", "_")
        measured = report.table[f"{key}_measured"].to_numpy(float)[inside]
        modelled = report.table[f"{key}_modelled"].to_numpy(float)[inside]
        error = report.table[f"{key}_error"].to_numpy(float)[inside]
        top = float(np.nanmax(np.concatenate([measured + error, modelled]))) * 1.05
        axis.errorbar(measured, modelled, xerr=error, fmt="o", ms=4, lw=0.8,
                      color="#1f4e79", ecolor="#9db7d1", capsize=2)
        axis.plot([0, top], [0, top], color="0.3", lw=1)
        numbers = report.summary[quantity]
        axis.set_xlim(0, top)
        axis.set_ylim(0, top)
        axis.set_aspect("equal")
        unit = UNITS.get(quantity, "")
        unit = f" ({unit})" if unit else ""
        axis.set_xlabel(f"measured {quantity.lower()}{unit}")
        axis.set_ylabel(f"modelled {quantity.lower()}{unit}")
        axis.set_title(f"n = {numbers['n']}, bias {numbers['bias']:+.2f} "
                       f"({100 * numbers['relative_bias']:+.0f} %), "
                       f"RMSE {numbers['rmse']:.2f}", fontsize=9)
        axis.grid(True, lw=0.3)
    fig.suptitle(f"Validation: {report.name}", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
