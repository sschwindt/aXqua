"""Which roughness zones a calibration varies, decided from where the measurements are.

The roughness table declares which zones may be calibrated and within which range of ks
(:func:`axqua.calibration.roughness_zone_parameters`). Whether the measurements can say
anything about a zone is another question, and it is answered here, from the points the
calibration actually uses (the calibration CSVs, after points outside the model and near
a liquid boundary have been left out):

* **A zone without a measurement point is not calibrated** (the user's rule of
  2026-11-11). The inn-KB15 trial calibrated zone 4 without one: its posterior stayed
  nearly as wide as its prior, and the value reported for it was a number the data did
  not support. The zone keeps the value the built model gives it (the middle of its
  range) and a warning names it.
* **A zone whose water is shallower than its largest ks gets a smaller largest ks.**
  The Nikuradse law of TELEMAC (``friction_calc.f``, law 5) is the logarithmic law of a
  flow much deeper than its roughness; at a relative submergence h/ks below about 1 the
  resistance is the form drag of roughness elements that reach through much of the
  depth, which that law does not describe, and below h/ks 0.09 TELEMAC clips it (the
  warning ``EXTREMLY HIGH VALUE OF FRICTION``). In the inn-KB15 trial zone 5 had its
  August points at a median depth of 0.19 m and a range up to 0.30 m, and its posterior
  piled up at the upper limit. When the median measured depth of a zone's points lies
  below its ``ks_max``, ``ks_max`` is lowered to that depth (the user's decision of
  2026-11-11), and when that leaves no range above ``ks_min`` the zone is not
  calibrated. Both are warnings, written with the plan.

Zones the roughness table does not calibrate and parameters that are not ``zone<N>``
pass through unchanged. The plan is written as ``zone-plan.json`` next to the
HydroBayesCal configuration, read by the calibration report, and returned by
``axqua calibration <case> --json`` so that the QGIS plugin can ask before it submits.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger("axqua.calibration_zones")

#: Relative submergence h/ks below which the Nikuradse law is not the law of the flow.
MIN_RELATIVE_SUBMERGENCE = 1.0
#: File the plan is written to, next to the HydroBayesCal configuration.
PLAN_FILE = "zone-plan.json"
#: Column of the measured water depth in a calibration CSV.
DEPTH = "WATER DEPTH_DATA"

_ZONE = re.compile(r"^zone(\d+)$")

CALIBRATED, CAPPED, EXCLUDED = "calibrated", "capped", "excluded"


@dataclass
class ZoneDecision:
    """What the calibration does with one roughness zone the table calibrates."""

    name: str                    # zone<N>
    zone: int
    ks_min: float
    ks_max: float                # as declared by the roughness table
    points: int                  # calibration points in the zone
    median_depth: float | None   # median measured water depth at those points [m]
    shallow_share: float | None  # share of the points with h / ks_max below 1
    decision: str                # calibrated | capped | excluded
    ks_max_used: float | None    # the largest ks the calibration tries (None: excluded)
    reason: str = ""

    @property
    def calibrated(self) -> bool:
        return self.decision != EXCLUDED


@dataclass
class ZonePlan:
    """The parameters a calibration varies, with the decision for every zone."""

    parameters: list                        # CalibrationParameter, in order
    zones: list[ZoneDecision] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def excluded(self) -> list[ZoneDecision]:
        return [z for z in self.zones if z.decision == EXCLUDED]

    @property
    def capped(self) -> list[ZoneDecision]:
        return [z for z in self.zones if z.decision == CAPPED]

    @property
    def calibrated_zones(self) -> list[int]:
        return [z.zone for z in self.zones if z.calibrated]

    def as_dict(self) -> dict:
        return {
            "parameters": [{"name": p.name, "min": float(p.min), "max": float(p.max)}
                           for p in self.parameters],
            "zones": [asdict(z) for z in self.zones],
            "calibrated_zones": self.calibrated_zones,
            "excluded_zones": [z.zone for z in self.excluded],
            "capped_zones": [z.zone for z in self.capped],
            "warnings": list(self.warnings),
        }

    def write(self, folder: Path) -> Path:
        path = Path(folder) / PLAN_FILE
        path.write_text(json.dumps(self.as_dict(), indent=2), encoding="utf-8")
        return path


def _zone_number(name: str) -> int | None:
    match = _ZONE.match(str(name).strip())
    return int(match.group(1)) if match else None


def point_zones(cfg, tables) -> tuple[np.ndarray, np.ndarray]:
    """Zone id and measured depth (NaN where none) of every calibration point.

    *tables* are the calibration point tables (DataFrames with ``x``, ``y`` and, where
    depth is a calibration target, ``WATER DEPTH_DATA``) of all situations.
    """
    from axqua.solvers.telemac.mesh import roughness_zone_frame, zone_of_points

    xs, ys, depths = [], [], []
    for table in tables:
        if table is None or not len(table):
            continue
        xs.append(np.asarray(table["x"], dtype=float))
        ys.append(np.asarray(table["y"], dtype=float))
        depth = (np.asarray(table[DEPTH], dtype=float) if DEPTH in table
                 else np.full(len(table), np.nan))
        depths.append(depth)
    if not xs:
        return np.zeros(0, dtype=int), np.zeros(0)
    zones = roughness_zone_frame(cfg)
    ids = zone_of_points(zones, np.concatenate(xs), np.concatenate(ys), cfg.crs_epsg)
    return ids, np.concatenate(depths)


def zone_plan(cfg, tables, parameters=None) -> ZonePlan:
    """Decide for every calibrated roughness zone, from the calibration points.

    *parameters* defaults to :func:`axqua.calibration.telemac_parameters`. Without
    roughness zones in the case (no ``geodata.roughness_zones``), the parameters pass
    through unchanged.
    """
    from axqua.calibration import telemac_parameters

    parameters = list(telemac_parameters(cfg) if parameters is None else parameters)
    zone_parameters = [p for p in parameters if _zone_number(p.name) is not None]
    if not zone_parameters or cfg.geodata.roughness_zones is None:
        return ZonePlan(parameters=parameters)

    ids, depths = point_zones(cfg, tables)
    plan = ZonePlan(parameters=[])
    for p in parameters:
        zone = _zone_number(p.name)
        if zone is None:
            plan.parameters.append(p)
            continue
        inside = ids == zone
        n = int(inside.sum())
        measured = depths[inside]
        measured = measured[np.isfinite(measured)]
        median = float(np.median(measured)) if measured.size else None
        shallow = (float(np.mean(measured / float(p.max) < MIN_RELATIVE_SUBMERGENCE))
                   if measured.size else None)
        decision = ZoneDecision(name=p.name, zone=zone, ks_min=float(p.min),
                                ks_max=float(p.max), points=n, median_depth=median,
                                shallow_share=shallow, decision=CALIBRATED,
                                ks_max_used=float(p.max))
        if n == 0:
            decision.decision, decision.ks_max_used = EXCLUDED, None
            decision.reason = "no calibration point lies in the zone"
            plan.warnings.append(
                f"roughness zone {zone} is not calibrated: none of the calibration "
                f"points lies in it, so the measurements say nothing about its ks. It "
                f"keeps the value of the built model.")
        elif median is not None and median / float(p.max) < MIN_RELATIVE_SUBMERGENCE:
            cap = round(median, 3)
            if cap <= float(p.min):
                decision.decision, decision.ks_max_used = EXCLUDED, None
                decision.reason = (f"the median measured depth {median:.3f} m is not "
                                   f"above ks_min {float(p.min):g} m")
                plan.warnings.append(
                    f"roughness zone {zone} is not calibrated: the median measured depth "
                    f"of its {n} points ({median:.2f} m) is not above its smallest ks "
                    f"({float(p.min):g} m), so the water is no deeper than the roughness "
                    f"anywhere in its range and the logarithmic friction law does not "
                    f"apply. It keeps the value of the built model.")
            else:
                decision.decision, decision.ks_max_used = CAPPED, cap
                decision.reason = (f"largest ks lowered from {float(p.max):g} m to the "
                                   f"median measured depth {cap:g} m")
                plan.warnings.append(
                    f"roughness zone {zone}: the largest ks is lowered from "
                    f"{float(p.max):g} m to {cap:g} m, the median measured water depth "
                    f"of its {n} points; {100 * (shallow or 0):.0f} % of them are "
                    f"shallower than {float(p.max):g} m, where the water is no deeper "
                    f"than the roughness and the logarithmic friction law does not "
                    f"apply.")
                capped = copy.copy(p)
                capped.max = cap
                plan.parameters.append(capped)
        else:
            plan.parameters.append(p)
        plan.zones.append(decision)
    return plan


def log_plan(plan: ZonePlan) -> None:
    """Print the plan and send its warnings to the logger the job surfaces."""
    if not plan.zones:
        return
    print("roughness zones:")
    for z in plan.zones:
        depth = f", median depth {z.median_depth:.2f} m" if z.median_depth is not None else ""
        if z.decision == EXCLUDED:
            what = f"not calibrated ({z.reason})"
        elif z.decision == CAPPED:
            what = f"ks {z.ks_min:g}..{z.ks_max_used:g} m (capped from {z.ks_max:g})"
        else:
            what = f"ks {z.ks_min:g}..{z.ks_max:g} m"
        print(f"  zone {z.zone}: {z.points} points{depth}: {what}")
    for warning in plan.warnings:
        log.warning(warning)


def read_plan(folder: Path) -> dict | None:
    """The plan a calibration wrote into *folder*, or None."""
    path = Path(folder) / PLAN_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
