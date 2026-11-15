"""Where measurements are needed: from the calibration parameters to points on the mesh.

Every calibration parameter acts somewhere. The roughness of a zone acts inside that zone, an eddy viscosity where the velocity changes across the flow, the secondary-current coefficients in bends. A measurement taken where a parameter does not act says nothing about it, which is why **a roughness zone without a measurement point is not calibrated** (:mod:`axqua.calibration_zones`). This module places a number of points per parameter where that parameter acts, among the nodes a survey can reach:

* wet at the discharge of the plan and deep enough for the instrument;
* at least 15 mesh nodes from the inflow and the outflow, where the boundary condition and not the river decides the flow (the rule of :func:`axqua.bayescal.away_from_liquid_boundaries`);
* for a roughness zone, where the water is deeper than the largest ks of the zone whenever the zone has such water, because the logarithmic friction law holds only there.

Within what a rule allows, the points are spread by farthest-point sampling over the plan position, the velocity and the depth, so that they cover the reach and the range of the hydraulics instead of crowding in one place. A point placed for one parameter also serves every other parameter whose rule it fulfills, so a second parameter does not always cost more points.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from axqua.survey.field import FlowField
from axqua.survey.settings import ParameterNeed, SurveySettings

_ZONE = re.compile(r"^zone(\d+)$", re.I)
#: Share of the candidates a ranking rule (shear, bends) keeps.
TOP_SHARE = 0.2
#: Nodes from the wetted edge that count as "along the banks".
BANK_NODES = (1, 3)
#: Depth below which a node counts as a margin that wets and dries [m].
MARGIN_DEPTH = 0.15

ZONE_NOTE = "a roughness zone of the zone map that the calibration of the case does not name yet"
SEDIMENT_NOTE = "a sediment parameter: velocities and depths say little about it; survey grain sizes and the fine sediment of the riverbed for it"


def guess_rule(name: str) -> tuple[str, int | None, str]:
    """``(rule, zone, note)`` for a calibration parameter by its name."""
    text = str(name).strip()
    match = _ZONE.match(text)
    if match:
        return "zone", int(match.group(1)), ""
    upper = text.upper()
    if upper.startswith("GAIA") or "SEDIMENT" in upper or "SHIELDS" in upper or "MPM" in upper:
        return "everywhere", None, SEDIMENT_NOTE
    if "SECONDARY" in upper:
        return "bends", None, ""
    if any(word in upper for word in ("DIFFUS", "DISPERSION", "VISCOS", "TURBUL", "CMU", "NUT")):
        return "shear", None, ""
    if "BOUNDARIES" in upper or "WALL" in upper:
        return "banks", None, ""
    if "VALUE OF DEPTH" in upper or "VALUE FOR DEPTH" in upper:
        return "margins", None, ""
    return "everywhere", None, ""


def map_zones(cfg) -> dict[int, tuple[float, float] | None]:
    """The roughness zones of the case, each with the range of its roughness where the roughness table gives one: the zones of the table, or of the zone layer where the case has no table. Empty where the case has no zones or they cannot be read."""
    zones: dict[int, tuple[float, float] | None] = {}
    try:
        if cfg.geodata.roughness_table is not None:
            from axqua.core.geodata import read_roughness_zones

            for zone, record in read_roughness_zones(cfg.geodata.roughness_table).items():
                zones[int(zone)] = (float(record.ks_min), float(record.ks_max)) if record.ks_min is not None and record.ks_max is not None else None
        elif cfg.geodata.roughness_zones is not None:
            from axqua.solvers.telemac.mesh import roughness_zone_frame

            zones = {int(zone): None for zone in roughness_zone_frame(cfg)["zone_id"].dropna().unique()}
    except Exception:                                # noqa: BLE001 - a table or a layer that is missing or unreadable offers no zones; the checkup of the case names it
        return {}
    return dict(sorted(zones.items()))


def case_needs(cfg, settings: SurveySettings) -> list[ParameterNeed]:
    """The parameters a plan serves: those the case declares, the roughness zones of its zone map that the calibration does not name, both as the settings changed them, then the ones the user added.

    A zone that the calibration does not name is selected where the case declares no calibration parameter at all, because the zones are then what a calibration will most likely adjust, and offered without being selected otherwise.
    """
    declared: list[ParameterNeed] = []
    limits: dict[str, tuple[float, float]] = {}
    try:
        from axqua.calibration import telemac_parameters

        parameters = list(telemac_parameters(cfg))
    except Exception:                                # noqa: BLE001 - a case without a calibration block
        parameters = []
    for p in parameters:
        rule, zone, note = guess_rule(p.name)
        declared.append(ParameterNeed(name=str(p.name), rule=rule, zone=zone, points=settings.points_per_parameter, use=not note, declared=True, note=note))
        limits[str(p.name)] = (float(p.min), float(p.max))
    named = {need.zone for need in declared if need.rule == "zone"}
    for zone, span in map_zones(cfg).items():
        if zone in named:
            continue
        declared.append(ParameterNeed(name=f"zone{zone}", rule="zone", zone=zone, points=settings.points_per_parameter, use=not parameters, declared=True, note=ZONE_NOTE))
        if span is not None:
            limits[f"zone{zone}"] = span
    by_name = {need.name: need for need in declared}
    for wish in settings.needs():
        if wish.name in by_name:
            known = by_name[wish.name]
            known.use, known.points = wish.use, max(int(wish.points), 0)
            if wish.rule in _RULES and known.rule != "zone":
                known.rule = wish.rule
        else:
            rule, zone, note = guess_rule(wish.name)
            if wish.rule in _RULES and rule != "zone":
                rule = wish.rule
                zone = wish.zone                     # a parameter of the user's own name, tied to a zone by its number
            if rule == "zone" and zone is None:
                rule = "everywhere"                  # a zone rule without a zone number says nothing
            wish.rule, wish.zone, wish.note = rule, (zone if rule == "zone" else None), note
            declared.append(wish)
            by_name[wish.name] = wish
    for need in declared:
        need.limits = list(limits[need.name]) if need.name in limits else None
    return declared


@dataclass
class PointSet:
    """The points of a plan, by mesh node."""

    node: list = field(default_factory=list)
    purposes: list = field(default_factory=list)       # per point, the parameter names it serves
    warnings: list = field(default_factory=list)
    served: dict = field(default_factory=dict)         # parameter -> number of points

    def __len__(self) -> int:
        return len(self.node)


_RULES = ("zone", "everywhere", "shear", "bends", "banks", "margins")
_ORDER = {rule: i for i, rule in enumerate(("zone", "shear", "bends", "banks", "margins", "everywhere"))}


def reachable_mask(flow: FlowField, min_depth: float, min_boundary_nodes: int | None = None) -> np.ndarray:
    """Nodes a measurement may be taken at: wet, deep enough, away from the liquid boundaries."""
    if min_boundary_nodes is None:
        from axqua.bayescal import MIN_BOUNDARY_NODES as min_boundary_nodes
    mask = flow.wet & (flow.depth >= float(min_depth))
    if flow.hops is not None:
        mask &= ~((flow.hops >= 0) & (flow.hops < int(min_boundary_nodes)))
    return mask


def rule_mask(flow: FlowField, need: ParameterNeed, allowed: np.ndarray) -> tuple[np.ndarray, str]:
    """The nodes of *allowed* that the rule of *need* accepts, and a remark where the rule had to give."""
    remark = ""
    if need.rule == "zone":
        mask = allowed & (flow.zone == int(need.zone or 0))
        limits = need.limits
        if limits and mask.any():
            deep = mask & (flow.depth >= float(limits[1]))
            if deep.sum() >= max(int(need.points), 1):
                mask = deep
            else:
                remark = (f"roughness zone {need.zone}: the reachable water is mostly shallower than its largest ks ({limits[1]:g} m), where the logarithmic friction law does not apply; a calibration will lower the largest ks to the median measured depth")
        return mask, remark
    if need.rule == "banks":
        hops = flow.wet_edge_distance()
        return allowed & (hops >= BANK_NODES[0]) & (hops <= BANK_NODES[1]), remark
    if need.rule == "margins":
        return allowed & (flow.depth <= MARGIN_DEPTH), remark
    if need.rule in ("shear", "bends"):
        score = shear_score(flow) if need.rule == "shear" else bend_score(flow)
        values = score[allowed]
        if not len(values) or not np.any(values > 0):
            return np.zeros_like(allowed), remark
        return allowed & (score >= np.quantile(values, 1.0 - TOP_SHARE)) & (score > 0), remark
    return allowed.copy(), remark


def shear_score(flow: FlowField) -> np.ndarray:
    """The change of the velocity across the flow [1/s]."""
    speed = flow.speed
    gx, gy = flow.gradient(speed)
    safe = np.where(speed > 0.05, speed, np.inf)
    return np.abs(gx * (-flow.v / safe) + gy * (flow.u / safe))


def bend_score(flow: FlowField) -> np.ndarray:
    """The curvature of the streamlines times the velocity [1/s]: the turning rate of the flow."""
    speed = flow.speed
    moving = speed > 0.1
    safe = np.where(moving, speed, 1.0)
    ex, ey = np.where(moving, flow.u / safe, 0.0), np.where(moving, flow.v / safe, 0.0)
    exx, exy = flow.gradient(ex)
    eyx, eyy = flow.gradient(ey)
    curvature = np.hypot(exx * ex + exy * ey, eyx * ex + eyy * ey)
    return np.where(moving, curvature * speed, 0.0)


def spread(features: np.ndarray, n: int, taken: np.ndarray | None = None, *, xy: np.ndarray | None = None, spacing: float = 0.0) -> list[int]:
    """Farthest-point sampling: up to *n* rows of *features* that lie far from each other and from *taken*. With *xy* and *spacing*, no two chosen rows are closer than *spacing* in the plan."""
    count = len(features)
    if n <= 0 or not count:
        return []
    alive = np.ones(count, dtype=bool)
    chosen: list[int] = []

    def take(index: int) -> None:
        chosen.append(index)
        alive[index] = False
        if xy is not None and spacing > 0:
            alive[np.hypot(xy[:, 0] - xy[index, 0], xy[:, 1] - xy[index, 1]) < spacing] = False

    if taken is not None and len(taken):
        from scipy.spatial import cKDTree

        distance, _ = cKDTree(taken).query(features)
    else:
        first = int(np.argmin(np.linalg.norm(features - np.median(features, axis=0), axis=1)))
        take(first)
        distance = np.linalg.norm(features - features[first], axis=1)
    while len(chosen) < n and alive.any():
        nxt = int(np.argmax(np.where(alive, distance, -1.0)))
        take(nxt)
        distance = np.minimum(distance, np.linalg.norm(features - features[nxt], axis=1))
    return chosen


def _features(flow: FlowField, nodes: np.ndarray, scale: float) -> np.ndarray:
    speed, depth = flow.speed[nodes], flow.depth[nodes]
    u95 = max(float(np.quantile(flow.speed[flow.wet], 0.95)) if flow.wet.any() else 1.0, 1e-6)
    h95 = max(float(np.quantile(flow.depth[flow.wet], 0.95)) if flow.wet.any() else 1.0, 1e-6)
    return np.column_stack([flow.x[nodes] / scale, flow.y[nodes] / scale, 0.5 * speed / u95, 0.5 * depth / h95])


def place(flow: FlowField, needs: list[ParameterNeed], allowed: np.ndarray, *, min_spacing: float = 0.0, what: str = "measurement") -> PointSet:
    """Place the points of *needs* among the nodes of *allowed*."""
    from scipy.spatial import cKDTree

    points = PointSet()
    spacing = float(min_spacing) if min_spacing and min_spacing > 0 else 2.0 * flow.edge_length
    everywhere = np.flatnonzero(allowed)
    if not len(everywhere):
        points.warnings.append(f"no node of the model can take a {what}: it is dry, too shallow or too close to the inflow and outflow everywhere")
        return points
    extent = max(float(np.ptp(flow.x[everywhere])), float(np.ptp(flow.y[everywhere])), 1.0)
    for need in sorted((n for n in needs if n.use and n.points > 0), key=lambda n: (_ORDER.get(n.rule, 9), n.name)):
        mask, remark = rule_mask(flow, need, allowed)
        if remark:
            points.warnings.append(remark)
        have = [i for i, node in enumerate(points.node) if mask[node]]
        for i in have:
            points.purposes[i].append(need.name)
        missing = int(need.points) - len(have)
        candidates = np.flatnonzero(mask)
        if missing > 0 and len(candidates) and points.node:
            # keep the spacing to the points that are there already
            near, _ = cKDTree(np.column_stack([flow.x[points.node], flow.y[points.node]])).query(np.column_stack([flow.x[candidates], flow.y[candidates]]))
            candidates = candidates[near >= spacing]
        placed = 0
        if missing > 0 and len(candidates):
            features = _features(flow, candidates, extent)
            taken = _features(flow, np.asarray(points.node, dtype=int), extent) if points.node else None
            picks = spread(features, missing, taken, xy=np.column_stack([flow.x[candidates], flow.y[candidates]]), spacing=spacing)
            for index in picks:
                points.node.append(int(candidates[index]))
                points.purposes.append([need.name])
            placed = len(picks)
        total = len(have) + placed
        points.served[need.name] = total
        if total == 0:
            where = f"roughness zone {need.zone}" if need.rule == "zone" else f"the rule '{need.rule}'"
            points.warnings.append(f"{need.name}: no {what} can be placed for {where}: none of its water can be reached. Without a point the parameter is not supported by data" + (" and the zone is not calibrated." if need.rule == "zone" else "."))
        elif total < int(need.points):
            points.warnings.append(f"{need.name}: {total} of {int(need.points)} points could be placed; the water its rule allows is too small for more at a spacing of {spacing:.1f} m")
    return points


__all__ = ["PointSet", "bend_score", "case_needs", "guess_rule", "map_zones", "place", "reachable_mask", "rule_mask", "shear_score", "spread"]
