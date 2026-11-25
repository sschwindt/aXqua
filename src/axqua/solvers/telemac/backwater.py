"""How far upstream a change of the outflow water level reaches (the backwater reach).

The water level at the outflow boundary of a model is an assumption: the rating of the outflow section, a measured rating curve or a fixed level. A change of that level spreads upstream and decays. Where measurement points lie within its reach, a calibration that keeps the outflow level fixed assigns an error of that level to the roughness. This module measures the reach on a steady result, cross-section by cross-section along the channel centerline, and warns when measurement points lie within it while the outflow level is not a calibration parameter.

**The decay.** Steady flow in a cross-section obeys ``dE/dx = -S_f``, with the energy level ``E = z + Q^2 / (2 g A^2)`` and the friction slope ``S_f = Q^2 / K^2``. A small change ``eta`` of the water level ``z`` changes ``E`` by ``eta (1 - Fr^2)`` and ``S_f`` by ``-2 S_f (d ln K / dz) eta``, so that, with ``x`` running upstream,

    d eta / dx = -kappa eta,   kappa = 2 S_f (d ln K / dz) / (1 - Fr^2)

and the share of a change at the outlet that remains at a distance ``x`` is ``exp(-int kappa dx)``. ``K`` is the conveyance of the section with the Nikuradse law of TELEMAC (``U / u* = ln(11.036 h / ks) / kappa_vK``), ``Fr^2 = Q^2 B / (g A^3)``. In a wide channel, ``kappa = (3 + 2 / ln(11.036 h / ks)) S_f / (h (1 - Fr^2))``: the change decays fast where the flow is shallow and steep, and passes almost undiminished through a deep, slow pool. A section at critical flow passes no change upstream.

**Two estimates.** The friction slope from the conveyance holds the bed friction only; a 2D result also loses energy to turbulence, bends and the form of the bed, which on the Inn KB15 riffle was three to four times the bed friction. The second estimate takes the larger of the two slopes, the friction slope and the slope of the energy line of the result; the first therefore gives the longer reach and the second the shorter one. The warning rests on the second estimate, because the model itself loses that energy; the first is an upper bound. Both assume a change small enough to leave the flow otherwise as it is: a rise that drowns a riffle changes the reach itself, and needs the check on a result with that level.

**Where a point lies.** A measurement point takes the place along the centerline where the main channel has the water level of the point in the steady result. For a point in the main channel, that is its own cross-section. A point in a patch, a side arm or a pool behind a bar has the water level of the place upstream where that water body joins the main channel, and a change of the outflow level reaches it only through that place. On the Inn KB15 reach, 71 of the 90 points lie up to 0.95 m above the main channel beside them, with the water level that the main channel has 835 to 885 m above the outlet, above the upper riffle. Two validation runs at 47.8 m3/s whose outflow levels differ by 1.21 m (and whose roughness differs slightly) changed the water level at these points by -3 to 0 cm, and at the 19 points in the main channel by 0.27 to 0.38 m. A point that is dry in the result takes the water level of the nearest wet node.

**What it does not cover.** The width of a section is held at its wetted width (a rising level that wets a bank makes the change decay faster, so the reach is overestimated), and the result is that of one discharge, the case's steady run.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger("axqua.backwater")

#: A point lies within the reach of the outflow level where at least this share of a
#: change of the level at the outlet remains, that is, where the change has decayed by
#: less than one order of magnitude.
REACH_SHARE = 0.1
#: The change of the outflow level that the report translates into centimeters at the
#: points, as a share of the depth at the outlet ("a few percent").
OUTLET_CHANGE = 0.05
#: Distances [m]: between cross-sections, between samples on a section, the furthest
#: a section reaches to each side of the centerline, and the half window over which the
#: slope of the energy line is fitted.
SECTION_SPACING = 10.0
SAMPLE_STEP = 0.5
SECTION_HALF_WIDTH = 300.0
SLOPE_WINDOW = 50.0
#: A sample counts as wet above this depth [m].
WET_DEPTH = 0.02
#: The shares at which :meth:`BackwaterReach.summary` names the distance.
SHARES = (0.5, 0.1, 0.01)
#: The summary names the points that lie more than this above the main channel beside
#: them [m] (where each point lies does not depend on it).
RAISED = 0.10

_G = 9.81
_KARMAN = 0.41
_NIKURADSE = 11.036          # TELEMAC's law 5, friction_calc.f
FILE_STEM = "backwater-reach"


@dataclass
class PointShares:
    """The measurement points of one survey along the centerline."""

    name: str
    distance: np.ndarray      # m from the outlet: where the main channel has the level
    projected: np.ndarray     # m from the outlet: the projection onto the centerline
    above: np.ndarray         # m, level of the point above the main channel beside it
    long: np.ndarray          # share left, bed friction only (the upper bound)
    short: np.ndarray         # share left, energy slope of the result
    depth_change: np.ndarray  # m at the point for a change of OUTLET_CHANGE x outlet depth

    @property
    def within(self) -> np.ndarray:
        return self.short >= REACH_SHARE

    @property
    def by_level(self) -> np.ndarray:
        """Points more than :data:`RAISED` above the main channel beside them."""
        return self.above > RAISED


@dataclass
class BackwaterReach:
    """The reach of a change of the outflow level, along the centerline of a result."""

    distance: np.ndarray      # m from the outlet, one per cross-section
    bed: np.ndarray           # lowest bed elevation of the section [m]
    surface: np.ndarray       # water level, weighted by the unit discharge [m]
    depth: np.ndarray         # mean depth A / B [m]
    froude: np.ndarray        # Q / (A sqrt(g A / B))
    friction_slope: np.ndarray
    energy_slope: np.ndarray  # of the result, fitted over +-SLOPE_WINDOW
    long: np.ndarray          # share of a change at the outlet left, bed friction only
    short: np.ndarray         # ... with the larger of friction and energy slope
    centerline_length: float
    outlet_depth: float
    outflow_calibrated: bool
    points: list[PointShares] = field(default_factory=list)

    def reach(self, share: float = REACH_SHARE, *, long: bool = False) -> float | None:
        """Distance from the outlet [m] at which the change is down to *share*."""
        left = self.long if long else self.short
        below = np.flatnonzero(left <= share)
        return float(self.distance[below[0]]) if below.size else None

    def _distance_text(self, share: float) -> str:
        values = [self.reach(share, long=False), self.reach(share, long=True)]
        text = [f"{v:,.0f} m" if v is not None else
                f"beyond the inflow ({self.centerline_length:,.0f} m)" for v in values]
        return text[0] if text[0] == text[1] else f"{text[0]} to {text[1]}"

    def summary(self) -> list[str]:
        lines = ["backwater reach: a change of the outflow water level is down to "
                 + ", ".join(f"{100 * s:g} % at {self._distance_text(s)}"
                             for s in SHARES)
                 + " from the outlet (mean depth there "
                 f"{self.outlet_depth:.2f} m)"]
        for group in self.points:
            if not group.distance.size:
                continue
            moved = group.by_level
            where = (f", {int(moved.sum())} of them with the water level the main channel "
                     f"has {group.distance[moved].min():,.0f} to "
                     f"{group.distance[moved].max():,.0f} m from the outlet (up to "
                     f"{group.above[moved].max():.2f} m above the main channel beside "
                     "them)") if moved.any() else ""
            lines.append(
                f"  {group.name}: {group.distance.size} points "
                f"{group.projected.min():,.0f} to {group.projected.max():,.0f} m from the "
                f"outlet{where}; {100 * group.short.min():.0f} to "
                f"{100 * group.short.max():.0f} % of the change left (bed friction "
                f"alone: up to {100 * group.long.max():.0f} %); "
                f"{100 * OUTLET_CHANGE:g} % of the outlet depth "
                f"({100 * OUTLET_CHANGE * self.outlet_depth:.1f} cm) moves the level "
                f"there by up to {100 * group.depth_change.max():.1f} cm")
        if self.outflow_calibrated and self.points:
            lines.append("  the outflow level is a calibration parameter")
        return lines

    def warnings(self) -> list[str]:
        """One warning per survey with points within the reach of a fixed outflow level."""
        if self.outflow_calibrated:
            return []
        found = []
        for group in self.points:
            inside = group.within
            if not inside.any():
                continue
            found.append(
                f"{int(inside.sum())} of {inside.size} points of {group.name} lie where "
                f"up to {100 * group.short.max():.0f} % of a change of the outflow water "
                f"level remains (the change is down to {100 * REACH_SHARE:g} % at "
                f"{self._distance_text(REACH_SHARE)} from the outlet): a change of "
                f"{100 * OUTLET_CHANGE:g} % of the outlet depth "
                f"({100 * OUTLET_CHANGE * self.outlet_depth:.1f} cm) moves the water "
                f"level there by up to {100 * group.depth_change.max():.1f} cm. The "
                "outflow level is not calibrated, so an error in it goes into the "
                "roughness: calibrate it (calibration.outflow_slope or "
                "calibration.outflow_offset), or move the outflow boundary downstream.")
        return found

    def as_dict(self) -> dict:
        return {
            "centerline_length_m": self.centerline_length,
            "outlet_depth_m": self.outlet_depth,
            "outflow_calibrated": self.outflow_calibrated,
            "reach_share": REACH_SHARE,
            "outlet_change": OUTLET_CHANGE,
            "distance_m": {f"{100 * s:g} %": {"energy_slope": self.reach(s, long=False),
                                             "bed_friction": self.reach(s, long=True)}
                           for s in SHARES},
            "points": [{"name": g.name, "points": int(g.distance.size),
                        "within": int(g.within.sum()),
                        "placed_by_level": int(g.by_level.sum()),
                        "distance_m": [float(g.distance.min()), float(g.distance.max())]
                        if g.distance.size else None,
                        "share": [float(g.short.min()), float(g.long.max())]
                        if g.distance.size else None,
                        "depth_change_m": float(g.depth_change.max())
                        if g.distance.size else None}
                       for g in self.points],
            "warnings": self.warnings(),
        }

    def write(self, folder: Path, *, figure: bool = True) -> list[Path]:
        """``backwater-reach.csv``, ``.json`` and (with *figure*) ``.png`` into *folder*."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        table = folder / f"{FILE_STEM}.csv"
        with open(table, "w", encoding="utf-8") as fh:
            fh.write("distance_from_outlet_m,bed_m,water_level_m,depth_m,froude,"
                     "friction_slope,energy_slope,share_bed_friction,"
                     "share_energy_slope\n")
            for row in zip(self.distance, self.bed, self.surface, self.depth,
                           self.froude, self.friction_slope, self.energy_slope,
                           self.long, self.short):
                fh.write(",".join(f"{v:.6g}" for v in row) + "\n")
        summary = folder / f"{FILE_STEM}.json"
        summary.write_text(json.dumps(self.as_dict(), indent=2), encoding="utf-8")
        written = [table, summary]
        if figure:
            written.append(plot(self, folder / f"{FILE_STEM}.png"))
        return written


def outflow_level_calibrated(cfg) -> bool:
    """Whether the calibration of *cfg* varies the outflow level."""
    calibration = getattr(cfg, "calibration", None)
    return bool(getattr(calibration, "outflow_slope", None)
                or getattr(calibration, "outflow_offset", None))


def _centerline(cfg):
    """The centerline as one line that starts at the outflow boundary."""
    import shapely

    from axqua.solvers.telemac.boundary import liquid_lines
    from axqua.solvers.telemac.steering import _channel_centerline

    line = _channel_centerline(cfg)
    lines = liquid_lines(cfg)
    if "outflow" not in lines:
        raise ValueError("the backwater reach needs an 'outflow' line in "
                         "boundaries.liquid_boundaries")
    outflow = lines["outflow"]
    start, end = shapely.Point(line.coords[0]), shapely.Point(line.coords[-1])
    if shapely.distance(start, outflow) > shapely.distance(end, outflow):
        line = shapely.reverse(line)
    return line


def _fields(results: Path, geometry: Path | None):
    """Coordinates, triangles, ks, and the last frame's depth, velocity and bed."""
    from axqua.core.selafin import SelafinFile

    res = SelafinFile(results)
    names = {n.upper(): n for n in res.var_names}
    need = ("WATER DEPTH", "VELOCITY U", "VELOCITY V")
    missing = [q for q in need if q not in names]
    if missing:
        raise ValueError(f"{Path(results).name} lacks {missing}")
    wanted = [names[q] for q in need] + [names[q] for q in ("BOTTOM", "FREE SURFACE")
                                         if q in names]
    frame = res.frame(-1, wanted)
    h = np.asarray(frame[names["WATER DEPTH"]], float)
    u = np.asarray(frame[names["VELOCITY U"]], float)
    v = np.asarray(frame[names["VELOCITY V"]], float)
    x, y, tri = res.x, res.y, res.ikle
    ks = None
    bottom = None
    if geometry is not None and Path(geometry).exists():
        geo = SelafinFile(geometry)
        if geo.npoin != res.npoin:
            raise ValueError(f"{Path(geometry).name} has {geo.npoin} nodes but "
                             f"{Path(results).name} has {res.npoin}: they come from "
                             "different builds")
        x, y, tri = geo.x, geo.y, geo.ikle
        gnames = {n.upper(): n for n in geo.var_names}
        gframe = geo.frame(0, [gnames[q] for q in ("BOTTOM", "BOTTOM FRICTION")
                               if q in gnames])
        if "BOTTOM FRICTION" in gnames:
            ks = np.asarray(gframe[gnames["BOTTOM FRICTION"]], float)
        if "BOTTOM" in gnames:
            bottom = np.asarray(gframe[gnames["BOTTOM"]], float)
    if "BOTTOM" in names:
        bottom = np.asarray(frame[names["BOTTOM"]], float)
    elif bottom is None and "FREE SURFACE" in names:
        bottom = np.asarray(frame[names["FREE SURFACE"]], float) - h
    if bottom is None:
        raise ValueError(f"{Path(results).name} has neither BOTTOM nor FREE SURFACE")
    return x, y, tri, h, u, v, bottom, ks


def _conveyance(depth: np.ndarray, ks: np.ndarray, step: float):
    """Conveyance K of the wet samples of a section and its derivative dK/dz."""
    log_term = np.log(np.maximum(_NIKURADSE * depth / ks, 1e-12))
    rough = log_term < 1.0                 # depth within the roughness: C held finite
    log_term = np.where(rough, 1.0, log_term)
    scale = np.sqrt(_G) / _KARMAN * step
    k = float(np.sum(scale * depth ** 1.5 * log_term))
    dk = float(np.sum(scale * np.sqrt(depth) * (1.5 * log_term + np.where(rough, 0.0,
                                                                        1.0))))
    return k, dk


def backwater_reach(cfg, results, *, geometry=None, points=None, ks: float | None = None,
                    spacing: float = SECTION_SPACING, step: float = SAMPLE_STEP,
                    half_width: float = SECTION_HALF_WIDTH) -> BackwaterReach:
    """Measure how far a change of the outflow level reaches on the steady *results*.

    *geometry* is the double-precision geometry of the build (its coordinates, its bed
    and its ``BOTTOM FRICTION``); without a ``BOTTOM FRICTION`` the roughness is *ks*,
    else the channel ks of the case. *points* maps a survey name to a table (or
    anything with ``x`` and ``y``) of measurement points.
    """
    import matplotlib.tri as mtri
    import shapely

    line = _centerline(cfg)
    x, y, tri, h, u, v, bottom, node_ks = _fields(Path(results), geometry)
    if node_ks is None:
        if ks is None:
            from axqua.mesh_validity import channel_ks

            ks = channel_ks(cfg)
        if not ks:
            raise ValueError("no roughness: the geometry has no BOTTOM FRICTION and the "
                             "case no channel ks")
        node_ks = np.full(h.shape, float(ks))
    triang = mtri.Triangulation(x, y, tri)
    finder = triang.get_trifinder()
    fields = [mtri.LinearTriInterpolator(triang, a, trifinder=finder)
              for a in (h, u, v, bottom, node_ks)]

    length = float(line.length)
    stations = np.arange(spacing / 2.0, length, spacing)
    offsets = np.arange(-half_width, half_width + step / 2.0, step)
    middle = int(np.argmin(np.abs(offsets)))
    rows = []
    for s in stations:
        centre = line.interpolate(s)
        a = line.interpolate(max(s - spacing / 2.0, 0.0))
        b = line.interpolate(min(s + spacing / 2.0, length))
        tangent = np.array([b.x - a.x, b.y - a.y])
        norm = np.hypot(*tangent)
        if norm == 0:
            continue
        tangent /= norm
        normal = np.array([-tangent[1], tangent[0]])
        px = centre.x + offsets * normal[0]
        py = centre.y + offsets * normal[1]
        hh, uu, vv, zb, kk = (np.ma.filled(f(px, py), np.nan) for f in fields)
        inside = np.isfinite(hh)
        if not inside[middle]:
            continue
        # the part of the section that the mesh covers without a gap around the
        # centerline: a normal that leaves the mesh does not come back into the reach
        gaps = np.flatnonzero(~inside)
        lo = gaps[gaps < middle].max() + 1 if (gaps < middle).any() else 0
        hi = gaps[gaps > middle].min() if (gaps > middle).any() else offsets.size
        hh, uu, vv, zb, kk = (arr[lo:hi] for arr in (hh, uu, vv, zb, kk))
        wet = hh > WET_DEPTH
        if wet.sum() < 3:
            continue
        hh, uu, vv, zb, kk = (arr[wet] for arr in (hh, uu, vv, zb, kk))
        unit = hh * (uu * tangent[0] + vv * tangent[1])
        q = abs(float(np.sum(unit)) * step)
        area = float(np.sum(hh) * step)
        width = float(hh.size * step)
        if q <= 0 or area <= 0:
            continue
        weight = np.abs(unit)
        surface = float(np.sum((zb + hh) * weight) / np.sum(weight)) if weight.sum() \
            else float(np.mean(zb + hh))
        k, dk = _conveyance(hh, np.maximum(kk, 1e-4), step)
        friction = (q / k) ** 2
        froude2 = q ** 2 * width / (_G * area ** 3)
        rows.append((s, float(zb.min()), surface, area / width, np.sqrt(froude2),
                     friction, dk / k, q / area))
    if len(rows) < 3:
        raise ValueError("fewer than three wet cross-sections along the centerline")
    table = np.array(rows, dtype=float)
    distance, bed, surface, depth, froude, friction, dlnk, speed = table.T

    energy = surface + speed ** 2 / (2.0 * _G)
    slope = np.full(distance.shape, np.nan)
    for i, s in enumerate(distance):
        near = np.abs(distance - s) <= SLOPE_WINDOW
        if near.sum() >= 3:
            slope[i] = np.polyfit(distance[near], energy[near], 1)[0]
    energy_slope = np.clip(np.nan_to_num(slope, nan=0.0), 0.0, None)

    def shares(slopes):
        kappa = np.where(froude < 1.0,
                         2.0 * slopes * dlnk / np.maximum(1.0 - froude ** 2, 1e-9),
                         np.inf)
        steps = np.diff(np.concatenate([[0.0], distance]))
        with np.errstate(invalid="ignore"):
            return np.exp(-np.cumsum(kappa * steps))

    long = shares(friction)
    short = shares(np.maximum(friction, energy_slope))
    first = distance <= distance[0] + 2.0 * spacing
    outlet_depth = float(np.mean(depth[first]))

    from scipy.spatial import cKDTree

    wet_index = np.flatnonzero(h > WET_DEPTH)
    wet_nodes = cKDTree(np.column_stack([x[wet_index], y[wet_index]]))
    node_level = bottom + h
    groups = []
    for name, pts in _point_groups(points):
        px, py = pts
        projected = np.array([line.project(shapely.Point(a, b)) for a, b in zip(px, py)])
        point_depth = np.ma.filled(fields[0](px, py), np.nan)
        level = point_depth + np.ma.filled(fields[3](px, py), np.nan)
        dry = ~(np.isfinite(level) & (point_depth > WET_DEPTH))
        if dry.any():
            # a point that the model leaves dry belongs to the nearest water
            _, nearest = wet_nodes.query(np.column_stack([px[dry], py[dry]]))
            level[dry] = node_level[wet_index[nearest]]
        along = projected.copy()
        wet = np.isfinite(level)
        for k in np.flatnonzero(wet):
            # the first cross-section from the outlet whose water level reaches the
            # point's: where the water body of the point joins the main channel
            reached = np.flatnonzero(surface >= level[k])
            along[k] = distance[reached[0]] if reached.size else distance[-1]
        above = np.where(wet, level - np.interp(projected, distance, surface), 0.0)
        left_long = np.interp(along, distance, long)
        left_short = np.interp(along, distance, short)
        groups.append(PointShares(
            name=name, distance=along, projected=projected, above=above,
            long=left_long, short=left_short,
            depth_change=left_short * OUTLET_CHANGE * outlet_depth))
    return BackwaterReach(
        distance=distance, bed=bed, surface=surface, depth=depth, froude=froude,
        friction_slope=friction, energy_slope=energy_slope, long=long, short=short,
        centerline_length=length, outlet_depth=outlet_depth,
        outflow_calibrated=outflow_level_calibrated(cfg), points=groups)


def _point_groups(points):
    """``(name, (x, y))`` for each survey of *points* (a mapping or name-table pairs)."""
    if not points:
        return []
    items = points.items() if hasattr(points, "items") else points
    groups = []
    for name, table in items:
        try:
            px = np.asarray(table["x"], float)
            py = np.asarray(table["y"], float)
        except (KeyError, TypeError, IndexError):
            px, py = (np.asarray(c, float) for c in table)
        keep = np.isfinite(px) & np.isfinite(py)
        groups.append((str(name), (px[keep], py[keep])))
    return groups


def plot(reach: BackwaterReach, path: Path) -> Path:
    """The water level along the reach, and the share of an outlet change left."""
    from axqua.calibration_report import COLORS, GRID, INK, INK2, MUTED, SURFACE, _style
    from matplotlib.lines import Line2D

    plt = _style()
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10, 7.0), sharex=True,
                                      facecolor=SURFACE,
                                      gridspec_kw={"height_ratios": [1, 1.25]})
    for ax in (top, bottom):
        ax.set_facecolor(SURFACE)
        ax.grid(color=GRID, lw=0.8)
        ax.spines[["top", "right"]].set_visible(False)
    d = reach.distance
    top.fill_between(d, reach.bed, reach.surface, color=COLORS[0], alpha=0.15, lw=0)
    top.plot(d, reach.surface, color=COLORS[0], lw=2)
    top.plot(d, reach.bed, color=MUTED, lw=1.5)
    top.set_ylabel("elevation (m)")

    bottom.fill_between(d, 100 * reach.short, 100 * reach.long, color=COLORS[0],
                        alpha=0.25, lw=0)
    bottom.plot(d, 100 * reach.short, color=COLORS[0], lw=2)
    bottom.plot(d, 100 * reach.long, color=COLORS[0], lw=1.2, ls="--")
    bottom.axhline(100 * REACH_SHARE, color=INK2, lw=1, ls=":")
    bottom.text(d[-1], 100 * REACH_SHARE + 2, f"{100 * REACH_SHARE:g} %", color=INK2,
                ha="right", va="bottom", fontsize=10)
    handles = [Line2D([], [], color=COLORS[0], lw=2, label="water level"),
               Line2D([], [], color=MUTED, lw=1.5, label="deepest bed"),
               Line2D([], [], color=COLORS[0], lw=2,
                      label="share left (energy slope of the result)"),
               Line2D([], [], color=COLORS[0], lw=1.2, ls="--",
                      label="upper bound (bed friction alone)")]
    for k, group in enumerate(reach.points):
        if not group.distance.size:
            continue
        color = COLORS[(k + 1) % len(COLORS)]
        # surveys at the same place: the earlier ones larger, so that each one shows
        size = max(70.0 - 14.0 * k, 16.0)
        bottom.scatter(group.distance, 100 * group.short, s=size, color=color,
                       edgecolor=SURFACE, linewidth=1.0, zorder=4 + k)
        top.scatter(group.distance, np.interp(group.distance, d, reach.surface), s=size,
                    color=color, edgecolor=SURFACE, linewidth=1.0, zorder=4 + k)
        handles.append(Line2D([], [], ls="", marker="o", color=color, markersize=6,
                              label=f"points {group.name}"))
    bottom.set_ylim(0, 105)
    bottom.set_ylabel("share of an outlet change left (%)")
    bottom.set_xlabel("distance from the outlet along the centerline (m)")
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.01, 0.95), ncol=4,
               frameon=False, fontsize=9.5)
    top.invert_xaxis()                     # the river flows from left to right
    title = ("How far upstream a change of the outflow water level reaches: down to "
             f"{100 * REACH_SHARE:g} % at {reach._distance_text(REACH_SHARE)}")
    fig.suptitle(title, color=INK, fontsize=12, x=0.01, ha="left")
    note = ("Outflow level calibrated" if reach.outflow_calibrated
            else "Outflow level not calibrated")
    if reach.warnings():
        note += ": measurement points lie within the reach"
    fig.text(0.01, 0.01, note, color=INK2, fontsize=10, va="bottom")
    rows = -(-len(handles) // 4)
    fig.tight_layout(rect=(0, 0.03, 1, 0.95 - 0.035 * rows))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return Path(path)


def check(cfg, results, *, geometry=None, points=None, out: Path | None = None,
          figure: bool = True) -> BackwaterReach:
    """:func:`backwater_reach`, written into *out* and its warnings logged."""
    reach = backwater_reach(cfg, results, geometry=geometry, points=points)
    if out is not None:
        reach.write(out, figure=figure)
    for text in reach.warnings():
        log.warning(text)
    return reach


__all__ = ["BackwaterReach", "OUTLET_CHANGE", "PointShares", "REACH_SHARE",
           "backwater_reach", "check", "outflow_level_calibrated", "plot"]
