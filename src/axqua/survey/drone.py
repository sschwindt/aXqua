"""An airborne surface-velocity survey: where a camera drone hovers, how high, and for how long.

Particle tracking and particle image velocimetry (PTV and PIV, evaluated with pXv) turn the video of a hovering drone into the surface velocity of everything in the frame. One hover station therefore replaces many wading verticals, wherever the water is too deep or too fast to wade, but it sees the surface only. The plan places the stations where the calibration parameters need data:

* The frame on the water follows from the flight altitude and the field of view of the camera: ``width = 2 H tan(HFOV / 2)``, and the ground sampling distance is that width over the pixels of the frame.
* Every used parameter has the water where it acts (:func:`axqua.survey.placement.rule_mask`) and gets a number of stations there (three by default), spread over the reach, the velocities and the depths like the verticals of a wading survey. A station that also films the water of another parameter counts for that one too.
* A frame is turned with the flow. Its long side lies across the river where the river is wider than the short side, so that both banks, which the video is stabilized on, are in view.
* The stations are flown in the shortest order from the take-off point, and the flight is cut into batteries.

The camera looks straight down. The altitude counts from the water surface, because the scale of the video does: a height error of 2 m at 40 m is a velocity error of 5 %. The remote control of the drone shows the height above the take-off point, so the plan gives that too for every station where the ground elevation of the take-off point is known (from the mesh or the terrain model of the case).
"""

from __future__ import annotations

import math

import numpy as np

from axqua.survey import placement, walk
from axqua.survey.field import FlowField
from axqua.survey.schedule import Breaks, day_window, fmt_clock
from axqua.survey.settings import SurveySettings

#: Tracer displacement between two evaluated frames that pXv tracks well [px].
GOOD_DISPLACEMENT = (4.0, 24.0)
#: Slower surface water moves the tracers too little to track [m/s].
MIN_VELOCITY = 0.05
#: Seconds to settle at a station before the video counts, and to change a battery [min].
SETTLE_SECONDS = 15.0
BATTERY_SWAP_MINUTES = 5.0
#: Smallest tracer image pXv detects reliably [px].
MIN_TRACER_PX = 2.0
#: Nodes of the water of a parameter a frame must hold to count as a station for it.
MIN_NODES = 5


def footprint_size(altitude: float, hfov_deg: float, width_px: int, height_px: int) -> tuple[float, float, float]:
    """``(width, height, gsd)`` in meters of the frame on the water at *altitude* above it."""
    width = 2.0 * float(altitude) * math.tan(math.radians(float(hfov_deg)) / 2.0)
    return width, width * height_px / width_px, width / width_px


def plan_airborne(flow: FlowField, settings: SurveySettings, needs, takeoff=None, *, sun=None, takeoff_level: float | None = None) -> dict:
    """Plan the hover stations. *takeoff* is ``(x, y)`` or None (then the pilot starts beside the first station); *takeoff_level* is the ground elevation there, where it is known."""
    from scipy.spatial import cKDTree

    a = settings.airborne
    warnings: list[str] = []
    altitude = float(a.altitude)
    if altitude > a.max_altitude:
        warnings.append(f"the altitude of {altitude:g} m is above the limit of {a.max_altitude:g} m of the settings; the plan uses {a.max_altitude:g} m")
        altitude = float(a.max_altitude)
    width, height, gsd = footprint_size(altitude, a.hfov_deg, a.width_px, a.height_px)
    tracer_px = a.tracer_size / gsd
    if tracer_px < MIN_TRACER_PX:
        lower = a.tracer_size / MIN_TRACER_PX * a.width_px / (2.0 * math.tan(math.radians(a.hfov_deg) / 2.0))
        warnings.append(f"a tracer of {a.tracer_size * 100:g} cm is {tracer_px:.1f} pixels wide at {altitude:g} m, too small to detect reliably: fly at {lower:.0f} m or lower, or seed larger tracers")

    allowed = placement.reachable_mask(flow, 0.0) & (flow.speed >= MIN_VELOCITY)
    used = [n for n in needs if n.use and n.points > 0]
    masks = {}
    for need in used:
        mask, _ = placement.rule_mask(flow, need, allowed)
        if mask.any():
            masks[need.name] = mask
        else:
            warnings.append(f"{need.name}: none of its water moves fast enough for tracers away from the inflow and outflow; the airborne survey does not serve it")
    if not masks and allowed.any():
        masks = {"wetted reach": allowed}
    union = np.zeros(len(flow.x), dtype=bool)
    for mask in masks.values():
        union |= mask
    target = np.flatnonzero(union)
    if not len(target):
        return {"stations": [], "route": [], "events": [], "timeline": [], "warnings": warnings + ["no water of the model moves fast enough for tracers: no station is planned"], "summary": {"stations": 0}}

    xy = np.column_stack([flow.x[target], flow.y[target]])
    tree = cKDTree(xy)
    wet_nodes = np.flatnonzero(flow.wet)
    wet_tree = cKDTree(np.column_stack([flow.x[wet_nodes], flow.y[wet_nodes]]))
    position = np.full(len(flow.x), -1, dtype=int)
    position[target] = np.arange(len(target))

    def frame_at(node: int):
        """The frame over *node*: the target nodes in it, and its center, flow direction and sides."""
        cx, cy = float(flow.x[node]), float(flow.y[node])
        near = wet_nodes[wet_tree.query_ball_point([cx, cy], 0.5 * height)]
        u, v = (float(flow.u[near].mean()), float(flow.v[near].mean())) if len(near) else (float(flow.u[node]), float(flow.v[node]))
        norm = math.hypot(u, v)
        ex, ey = (u / norm, v / norm) if norm > 1e-6 else (1.0, 0.0)
        along_side, across_side = (height, width) if _river_width(flow, cx, cy, ex, ey, width) > height else (width, height)
        local = np.asarray(tree.query_ball_point([cx, cy], 0.5 * math.hypot(width, height)), dtype=int)
        dx, dy = xy[local, 0] - cx, xy[local, 1] - cy
        inside = (np.abs(dx * ex + dy * ey) <= 0.5 * along_side) & (np.abs(-dx * ey + dy * ex) <= 0.5 * across_side)
        return local[inside], (cx, cy, ex, ey, along_side, across_side)

    # stations per parameter, spread like the verticals of a wading survey; a frame counts for every parameter it films
    extent = max(float(np.ptp(xy[:, 0])), float(np.ptp(xy[:, 1])), 1.0)
    spacing = (1.0 - min(max(float(a.overlap), 0.0), 0.8)) * math.hypot(width, height)
    frames: list[tuple] = []
    centers: list[int] = []
    filmed = np.zeros(len(target), dtype=bool)
    stations_of = {name: 0 for name in masks}
    capped = False
    for name, mask in sorted(masks.items(), key=lambda item: item[1].sum()):
        in_need = mask[target]
        enough = MIN_NODES
        have = sum(int(in_need[members].sum() >= enough) for members, _ in frames)
        candidates = np.flatnonzero(mask & (position >= 0))
        candidates = candidates[~filmed[position[candidates]]]
        if centers and len(candidates):
            near, _ = cKDTree(np.column_stack([flow.x[centers], flow.y[centers]])).query(np.column_stack([flow.x[candidates], flow.y[candidates]]))
            candidates = candidates[near >= spacing]
        missing = max(int(a.stations_per_parameter), 1) - have
        if missing > 0 and len(candidates):
            features = placement._features(flow, candidates, extent)
            taken = placement._features(flow, np.asarray(centers, dtype=int), extent) if centers else None
            for index in placement.spread(features, missing, taken, xy=np.column_stack([flow.x[candidates], flow.y[candidates]]), spacing=spacing):
                if len(frames) >= int(a.max_stations):
                    capped = True
                    break
                node = int(candidates[index])
                frames.append(frame_at(node))
                centers.append(node)
                filmed[frames[-1][0]] = True
    for name, mask in masks.items():
        in_need = mask[target]
        enough = MIN_NODES
        stations_of[name] = sum(int(in_need[members].sum() >= enough) for members, _ in frames)
        if stations_of[name] < max(int(a.stations_per_parameter), 1):
            why = f"the plan stops at {a.max_stations} stations" if capped else "its water has no room for more"
            warnings.append(f"{name}: {stations_of[name]} of {a.stations_per_parameter} stations film its water; {why}")
    per_need = {name: round(float((filmed & mask[target]).sum() / max(int(mask[target].sum()), 1)), 2) for name, mask in masks.items()}
    share = float(filmed.sum() / len(target))
    chosen = list(range(len(frames)))
    if not chosen:
        return {"stations": [], "route": [], "events": [], "timeline": [], "warnings": warnings, "summary": {"stations": 0}}

    # the order of the flight
    spots = [frames[i][1] for i in chosen]
    base = (float(takeoff[0]), float(takeoff[1])) if takeoff is not None else None
    sites = [base if base is not None else (spots[0][0], spots[0][1])] + [(s[0], s[1]) for s in spots]
    pts = np.asarray(sites)
    seconds = np.hypot(pts[:, None, 0] - pts[None, :, 0], pts[:, None, 1] - pts[None, :, 1]) / max(float(a.cruise_speed), 0.1)
    order = walk.order_sites(seconds, list(range(1, len(sites))))
    if base is None:
        warnings.append("no access point is set: the flight is planned from beside the first station; set the access point where the pilot stands")

    stations, route, events = [], [], []
    window = day_window(settings.time, sun, 0)
    breaks = Breaks(settings.time, window.start)
    now, here, battery, used_minutes, day = window.start, 0, 1, 0.0, 0
    hover = (a.hover_seconds + SETTLE_SECONDS) / 60.0
    events.append({"kind": "start", "start": now, "end": now, "day": day, "what": "Take-off point: lens calibration, ground control points, battery 1"})

    def leg(a_site: int, b_site: int, kind: str, start: float) -> float:
        minutes = seconds[a_site, b_site] / 60.0
        route.append({"order": len(route) + 1, "battery": battery, "day": day + 1, "kind": kind, "start": fmt_clock(start), "minutes": round(minutes, 1), "length": round(float(seconds[a_site, b_site] * a.cruise_speed), 1), "coords": [sites[a_site], sites[b_site]]})
        return minutes

    for number, site in enumerate(order, start=1):
        there, back = seconds[here, site] / 60.0, seconds[site, 0] / 60.0
        if used_minutes > 0 and used_minutes + there + hover + back > a.battery_minutes:
            home = leg(here, 0, "return", now)
            now += home
            pause, kind = breaks.due(now)
            swap = max(BATTERY_SWAP_MINUTES, pause)
            battery += 1
            if now + swap + 2 * back + hover > window.end and len(stations):
                events.append({"kind": "end", "start": now, "end": now, "day": day, "what": "End of the day"})
                day += 1
                window = day_window(settings.time, sun, day)
                breaks = Breaks(settings.time, window.start)
                now = window.start
                events.append({"kind": "start", "start": now, "end": now, "day": day, "what": f"Take-off point, battery {battery}"})
            else:
                events.append({"kind": kind or "battery", "start": now, "end": now + swap, "day": day, "what": f"Change to battery {battery}" + (f" and {kind}" if kind else "")})
                now += swap
                if kind:
                    breaks.taken(now, kind)
            here, used_minutes = 0, 0.0
            there = seconds[0, site] / 60.0
        now += leg(here, site, "flight", now)
        cx, cy, ex, ey, along_side, across_side = spots[site - 1]
        members = frames[chosen[site - 1]][0]
        nodes = target[members]
        speed = flow.speed[nodes]
        median = float(np.median(speed)) if len(speed) else 0.0
        fastest = float(speed.max()) if len(speed) else 0.0
        per_frame = median / gsd / a.fps
        frame_step = max(1, math.ceil(GOOD_DISPLACEMENT[0] / per_frame)) if per_frame > 0 else 1
        if fastest / gsd / a.fps * frame_step > GOOD_DISPLACEMENT[1]:
            frame_step = max(1, math.floor(GOOD_DISPLACEMENT[1] / (fastest / gsd / a.fps)))
        level = float(np.median(flow.bed[nodes] + flow.depth[nodes])) if len(nodes) else 0.0
        heading = (math.degrees(math.atan2(ex, ey)) + (0.0 if along_side == height else 90.0)) % 360.0
        stations.append({
            "station": number, "battery": battery, "day": day + 1, "x": float(cx), "y": float(cy),
            "altitude": round(altitude, 1), "water_level": round(level, 2), "above_takeoff": None if takeoff_level is None else round(level + altitude - float(takeoff_level), 1), "heading": round(heading), "flow_bearing": round(math.degrees(math.atan2(ex, ey)) % 360.0),
            "frame_along": round(along_side, 1), "frame_across": round(across_side, 1), "gsd_cm": round(gsd * 100, 2),
            "velocity": round(median, 2), "velocity_max": round(fastest, 2), "px_per_frame": round(per_frame, 2), "frame_step": int(frame_step),
            "hover_s": float(a.hover_seconds), "start": fmt_clock(now), "end": fmt_clock(now + hover), "start_min": now, "end_min": now + hover,
            "serves": ", ".join(name for name, mask in masks.items() if mask[nodes].any()),
            "polygon": _rectangle(cx, cy, ex, ey, along_side, across_side),
        })
        now += hover
        used_minutes += there + hover
        here = site
    now += leg(here, 0, "return", now)
    events.append({"kind": "end", "start": now, "end": now, "day": day, "what": "Landed; check the videos before leaving"})

    flight = sum(r["minutes"] for r in route) + len(stations) * hover
    summary = {
        "stations": len(stations), "altitude": altitude, "frame_width": round(width, 1), "frame_height": round(height, 1), "gsd_cm": round(gsd * 100, 2), "tracer_px": round(tracer_px, 1),
        "hover_seconds": float(a.hover_seconds), "flight_minutes": round(flight, 1), "batteries": battery, "days": day + 1,
        "start": fmt_clock(events[0]["start"]), "end": fmt_clock(now), "filmed_share": round(share, 2), "filmed": per_need, "stations_of": stations_of,
        "route_length": round(sum(r["length"] for r in route)), "takeoff": base is not None, "takeoff_level": None if takeoff_level is None else round(float(takeoff_level), 2),
        "above_takeoff": None if takeoff_level is None else [min(st["above_takeoff"] for st in stations), max(st["above_takeoff"] for st in stations)],
        "longest_distance": round(max(math.hypot(st["x"] - sites[0][0], st["y"] - sites[0][1]) for st in stations)),
        "video_gb": round(len(stations) * (a.hover_seconds + SETTLE_SECONDS) * 100.0 / 8.0 / 1000.0, 1),
    }
    timeline = [{"day": e["day"] + 1, "start": fmt_clock(e["start"]), "end": fmt_clock(e["end"]), "what": e["what"], "kind": e["kind"]} for e in events]
    timeline += [{"day": s["day"], "start": s["start"], "end": s["end"], "what": f"Station {s['station']}: hover at {s['altitude']:g} m, heading {s['heading']:g} deg", "kind": "hover"} for s in stations]
    timeline.sort(key=lambda r: (r["day"], r["start"], r["end"]))
    marks = [{"kind": e["kind"] if e["kind"] in ("break", "long break") else "battery change", "survey": "airborne", "day": e["day"] + 1, "at": "take-off point", "start": fmt_clock(e["start"]), "end": fmt_clock(e["end"]), "minutes": round(e["end"] - e["start"], 1), "x": sites[0][0], "y": sites[0][1]} for e in events if e["kind"] in ("battery", "break", "long break")]
    return {"stations": stations, "route": route, "events": marks, "timeline": timeline, "warnings": warnings, "summary": summary, "takeoff": sites[0]}


def _river_width(flow: FlowField, cx: float, cy: float, ex: float, ey: float, reach: float) -> float:
    """The wetted width across the flow through ``(cx, cy)``, looked for up to *reach* to either side."""
    step = max(flow.edge_length, reach / 60.0)
    offsets = np.arange(step, reach + step, step)
    total = step
    for sign in (1.0, -1.0):
        xs, ys = cx - sign * offsets * ey, cy + sign * offsets * ex
        depth, _, _, inside = flow.sample(xs, ys)
        wet = inside & (depth > flow.wet_depth)
        dry = np.flatnonzero(~wet)
        total += (dry[0] if len(dry) else len(offsets)) * step
    return float(total)


def _rectangle(cx, cy, ex, ey, along: float, across: float) -> list[tuple[float, float]]:
    corners = []
    for s, n in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5), (-0.5, -0.5)):
        corners.append((float(cx + s * along * ex - n * across * ey), float(cy + s * along * ey + n * across * ex)))
    return corners


__all__ = ["GOOD_DISPLACEMENT", "footprint_size", "plan_airborne"]
