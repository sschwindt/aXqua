"""A kayak drifter survey: the drift lines between the put-in and the take-out, and how long a float takes.

Barker et al. (2018) mounted an RTK GPS on a kayak and let it drift: the distance between two positions over the time between them is the surface velocity along the drift line, at the midpoint. One float gives hundreds of velocities over kilometers of river in the time a wading survey takes for a riffle. The method suits long reaches, and it needs a pick-up at the end.

The plan traces drift lines through the flow field of the model:

* They start across the river at the put-in, at positions that divide the discharge into equal parts, so that the lines spread over the flow and not over the width.
* Each is a streamline of the depth-averaged velocity, followed to the point closest to the take-out.
* A line that crosses water faster than the limit of the settings (3 m/s by default) or shallower than a loaded kayak floats is passed over where another line serves, and flagged where none does. The fast patches are drawn on the map.
* From what is left, the lines are chosen that pass through the water of the most calibration parameters.

A kayak is taken to drift at the flow velocity, so the time of a float is the integral of ``ds / U`` along its line, plus the breaks. Kayakers float in pairs for safety (the default); a single kayaker floats every reach twice. The surface is faster than the depth average: Barker et al. found the depth-averaged velocity to be 0.71 times the kayak velocity at their site, a ratio that a few fixed-point measurements (ADV, ADCP) establish for another river.

Barker, J.R., Pasternack, G.B., Bratovich, P.M., Massa, D.A., Wyrick, J.R., Johnson, T.R. (2018). Kayak drifter surface velocity observation for 2D hydraulic model validation. River Research and Applications 34(2), 124-134. https://doi.org/10.1002/rra.3238
"""

from __future__ import annotations

import math

import numpy as np

from axqua.survey import placement
from axqua.survey.field import FlowField, SurveyError
from axqua.survey.schedule import day_window, fmt_clock
from axqua.survey.settings import SurveySettings

#: Lines traced across the river, of which the floats are chosen.
CANDIDATES = 15
#: Slower than this a kayak does not drift on; the line ends [m/s].
STALL = 0.02
#: Ratio of depth-averaged to kayak velocity that Barker et al. (2018) used.
BARKER_RATIO = 0.71
#: Minutes between two kayaks that start on the same round.
STAGGER_MINUTES = 2.0


def plan_kayak(flow: FlowField, settings: SurveySettings, needs, put_in, take_out, *, sun=None) -> dict:
    """Plan the floats from *put_in* to *take_out* (both ``(x, y)``)."""
    k = settings.kayak
    if put_in is None or take_out is None:
        missing = "an access point (the put-in)" if put_in is None else "a leave point (the take-out)"
        raise SurveyError(f"a kayak survey needs {missing}", remedy="Set a point with the role 'access' where the kayaks enter the river and one with the role 'leave' where they are taken out, in the layer access_points of survey-plan.gpkg.")
    warnings: list[str] = []
    wet = np.flatnonzero(flow.wet & (flow.speed > STALL))
    if not len(wet):
        raise SurveyError("the model has no flowing water to drift on", remedy="Plan with the result of a steady run that has reached the reach.")
    start = wet[np.argmin(np.hypot(flow.x[wet] - put_in[0], flow.y[wet] - put_in[1]))]
    stop = wet[np.argmin(np.hypot(flow.x[wet] - take_out[0], flow.y[wet] - take_out[1]))]
    seeds = _section_seeds(flow, float(flow.x[start]), float(flow.y[start]), CANDIDATES)
    reach = math.hypot(flow.x[stop] - flow.x[start], flow.y[stop] - flow.y[start])
    step = max(flow.edge_length, 1.0)
    lines = []
    for quantile, (sx, sy) in seeds:
        line = _trace(flow, sx, sy, (float(flow.x[stop]), float(flow.y[stop])), step, max_length=max(4.0 * reach, 500.0))
        if line is not None:
            line["quantile"] = quantile
            lines.append(line)
    if not lines:
        raise SurveyError("no drift line leads from the put-in toward the take-out", remedy="Check that the take-out lies downstream of the access point, and that both lie on the modeled reach.")
    closest = min(line["miss"] for line in lines)
    if closest > max(0.25 * reach, 50.0):
        warnings.append(f"the drift lines pass the take-out at {closest:.0f} m: is it downstream of the put-in, on the same branch of the river?")

    used = [n for n in needs if n.use and n.points > 0]
    allowed = placement.reachable_mask(flow, k.min_depth)
    masks = {n.name: placement.rule_mask(flow, n, allowed)[0] for n in used}
    for line in lines:
        line.update(_judge(flow, line, k, masks))
    rounds = max(int(k.floats), 2) if int(k.kayakers) == 1 else max(int(k.floats), 1)
    wanted = int(k.kayakers) * rounds
    picks = _choose(lines, wanted, masks)
    clean = [line for line in picks if not line["fast_m"] and not line["shallow_m"]]
    if len(clean) < len(picks):
        flagged = len(picks) - len(clean)
        warnings.append(f"{flagged} of the {len(picks)} drift lines cross water faster than {k.max_velocity:g} m/s or shallower than {k.min_depth:g} m; no cleaner line exists. Scout these stretches, and line or carry the kayak around them.")
    if len(picks) < wanted:
        warnings.append(f"only {len(picks)} distinct drift lines could be traced for {wanted} floats; some floats repeat a line")
    fixes_in = {name: 0 for name in masks}
    for line in picks:
        for name, count in line["fixes_in"].items():
            fixes_in[name] += count
    for name, count in fixes_in.items():
        if count == 0:
            warnings.append(f"{name}: no drift line passes through its water; the kayak survey does not serve it")

    # the timetable: every round all kayakers float, then the kayaks are carried back
    window = day_window(settings.time, sun, 0)
    now, day = window.start, 0
    tracks, timeline, marks = [], [], []
    timeline.append({"day": 1, "start": fmt_clock(now), "end": fmt_clock(now), "what": "Put-in: mount and start the DGPS, check the fix and the logging interval", "kind": "start"})
    number = 0
    for round_number in range(1, rounds + 1):
        todo = [picks[(number + i) % len(picks)] for i in range(int(k.kayakers))]
        longest = max(_float_minutes(line, k)[0] for line in todo) + STAGGER_MINUTES * (len(todo) - 1)
        if round_number > 1 and now + longest > window.end + 1e-6:
            timeline.append({"day": day + 1, "start": fmt_clock(now), "end": fmt_clock(now), "what": "End of the day", "kind": "end"})
            day += 1
            window = day_window(settings.time, sun, day)
            now = window.start
            timeline.append({"day": day + 1, "start": fmt_clock(now), "end": fmt_clock(now), "what": "Put-in", "kind": "start"})
        elif round_number == 1 and now + longest > window.end + 1e-6:
            warnings.append(f"one float takes {longest:.0f} min, longer than the working day leaves; start earlier or shorten the reach")
        for kayaker, line in enumerate(todo, start=1):
            number += 1
            begin = now + STAGGER_MINUTES * (kayaker - 1)
            minutes, pauses = _float_minutes(line, k)
            for pause in pauses:
                x, y = _along(line, pause["drift_minutes"])
                marks.append({"kind": "break", "survey": "kayak", "day": day + 1, "at": f"float {number}", "start": fmt_clock(begin + pause["clock_minutes"]), "end": fmt_clock(begin + pause["clock_minutes"] + k.break_minutes), "minutes": float(k.break_minutes), "x": x, "y": y})
            tracks.append({
                "float": number, "round": round_number, "kayaker": kayaker, "day": day + 1, "share_of_discharge": round(line["quantile"], 2),
                "length": round(line["length"]), "minutes": round(minutes, 1), "drift_minutes": round(line["minutes"], 1), "start": fmt_clock(begin), "end": fmt_clock(begin + minutes),
                "fixes": int(line["minutes"] * 60.0 / k.log_interval), "velocity_mean": round(line["length"] / (line["minutes"] * 60.0), 2), "velocity_max": round(line["v_max"], 2), "depth_min": round(line["h_min"], 2),
                "spacing_min": round(line["v_min"] * k.log_interval, 2), "spacing_max": round(line["v_max"] * k.log_interval, 1),
                "fast_m": round(line["fast_m"]), "shallow_m": round(line["shallow_m"]), "miss_m": round(line["miss"]), "serves": ", ".join(name for name, count in line["fixes_in"].items() if count), "coords": line["coords"],
            })
            timeline.append({"day": day + 1, "start": fmt_clock(begin), "end": fmt_clock(begin + minutes), "what": f"Float {number} (kayaker {kayaker}): {line['length'] / 1000:.2f} km, about {tracks[-1]['fixes']} positions", "kind": "float"})
        now += longest
        if round_number < rounds:
            timeline.append({"day": day + 1, "start": fmt_clock(now), "end": fmt_clock(now + k.shuttle_minutes), "what": "Take-out: carry the kayaks back to the put-in", "kind": "shuttle"})
            now += k.shuttle_minutes
    timeline.append({"day": day + 1, "start": fmt_clock(now), "end": fmt_clock(now), "what": "Take-out: stop the logging, save the data, pick-up", "kind": "end"})

    drift = [t["drift_minutes"] for t in tracks]
    summary = {
        "floats": len(tracks), "kayakers": int(k.kayakers), "rounds": rounds, "lines": len({id(p) for p in picks}), "days": day + 1,
        "length": round(float(np.mean([t["length"] for t in tracks]))), "float_minutes": round(float(np.mean(drift)), 1), "float_minutes_max": round(float(max(t["minutes"] for t in tracks)), 1),
        "fixes": int(sum(t["fixes"] for t in tracks)), "start": timeline[0]["start"], "end": fmt_clock(now),
        "velocity_max": round(max(t["velocity_max"] for t in tracks), 2), "depth_min": round(min(t["depth_min"] for t in tracks), 2),
        "fixes_in": fixes_in, "avoid_area": 0.0, "log_interval": float(k.log_interval), "max_velocity": float(k.max_velocity),
        "velocity_precision": round(math.sqrt(2.0) * 0.02 / k.log_interval, 3),
    }
    avoid = fast_patches(flow, k.max_velocity)
    summary["avoid_area"] = round(sum(p["area"] for p in avoid))
    summary["avoid_patches"] = len(avoid)
    return {"tracks": tracks, "avoid": avoid, "events": marks, "timeline": timeline, "warnings": warnings, "summary": summary, "put_in": tuple(put_in), "take_out": tuple(take_out)}


def _section_seeds(flow: FlowField, cx: float, cy: float, count: int) -> list[tuple[float, tuple[float, float]]]:
    """Points across the river through ``(cx, cy)`` that divide the discharge into equal parts: ``[(share, (x, y)), ...]``."""
    depth, u, v, _ = flow.sample([cx], [cy])
    norm = math.hypot(u[0], v[0])
    ex, ey = (u[0] / norm, v[0] / norm) if norm > 1e-9 else (1.0, 0.0)
    step = max(0.5 * flow.edge_length, 0.25)
    reach = 400.0
    offsets = np.arange(-reach, reach + step, step)
    xs, ys = cx - offsets * ey, cy + offsets * ex
    depth, u, v, inside = flow.sample(xs, ys)
    wet = inside & (depth > flow.wet_depth)
    middle = int(np.argmin(np.abs(offsets)))
    lo = hi = middle
    while lo > 0 and wet[lo - 1]:
        lo -= 1
    while hi < len(offsets) - 1 and wet[hi + 1]:
        hi += 1
    xs, ys = xs[lo:hi + 1], ys[lo:hi + 1]
    q = np.maximum(depth[lo:hi + 1] * (u[lo:hi + 1] * ex + v[lo:hi + 1] * ey), 0.0)
    total = np.cumsum(q)
    if len(xs) < 3 or total[-1] <= 0:
        return [(0.5, (cx, cy))]
    out = []
    for share in (np.arange(count) + 0.5) / count:
        i = int(np.searchsorted(total, share * total[-1]))
        out.append((float(share), (float(xs[min(i, len(xs) - 1)]), float(ys[min(i, len(ys) - 1)]))))
    return out


def _trace(flow: FlowField, x: float, y: float, target, step: float, *, max_length: float) -> dict | None:
    """The streamline from ``(x, y)``, cut at its point closest to *target*."""
    xs, ys, hs, vs = [x], [y], [], []
    depth, u, v, inside = flow.sample([x], [y])
    travelled = 0.0
    while travelled < max_length:
        speed = math.hypot(u[0], v[0])
        if not inside[0] or depth[0] <= flow.wet_depth or speed < STALL:
            break
        hs.append(float(depth[0]))
        vs.append(speed)
        # midpoint rule: follow the velocity at half the step
        mx, my = x + 0.5 * step * u[0] / speed, y + 0.5 * step * v[0] / speed
        dm, um, vm, im = flow.sample([mx], [my])
        sm = math.hypot(um[0], vm[0])
        if im[0] and dm[0] > flow.wet_depth and sm >= STALL:
            x, y = x + step * um[0] / sm, y + step * vm[0] / sm
        else:
            x, y = x + step * u[0] / speed, y + step * v[0] / speed
        depth, u, v, inside = flow.sample([x], [y])
        xs.append(x)
        ys.append(y)
        travelled += step
    if len(hs) < 3:
        return None
    xs, ys = np.asarray(xs[:len(hs)]), np.asarray(ys[:len(hs)])
    distance = np.hypot(xs - target[0], ys - target[1])
    end = int(np.argmin(distance))
    if end < 2:
        return None
    xs, ys, hs, vs = xs[:end + 1], ys[:end + 1], np.asarray(hs[:end + 1]), np.asarray(vs[:end + 1])
    ds = np.hypot(np.diff(xs), np.diff(ys))
    mid = 0.5 * (vs[:-1] + vs[1:])
    seconds = np.concatenate([[0.0], np.cumsum(ds / mid)])
    return {"x": xs, "y": ys, "h": hs, "v": vs, "seconds": seconds, "length": float(ds.sum()), "minutes": float(seconds[-1] / 60.0), "miss": float(distance[end]), "v_max": float(vs.max()), "v_min": float(vs.min()), "h_min": float(hs.min())}


def _judge(flow: FlowField, line: dict, k, masks: dict) -> dict:
    """What a line crosses: meters of too fast and too shallow water, and the positions it logs in the water of every parameter."""
    from scipy.spatial import cKDTree

    ds = np.concatenate([np.hypot(np.diff(line["x"]), np.diff(line["y"])), [0.0]])
    fast = float(ds[line["v"] > k.max_velocity].sum())
    shallow = float(ds[line["h"] < k.min_depth].sum())
    times = np.arange(0.0, line["seconds"][-1], max(float(k.log_interval), 0.1))
    fx, fy = np.interp(times, line["seconds"], line["x"]), np.interp(times, line["seconds"], line["y"])
    if "tree" not in flow._cache:
        flow._cache["tree"] = cKDTree(np.column_stack([flow.x, flow.y]))
    _, nearest = flow._cache["tree"].query(np.column_stack([fx, fy]))
    keep = max(1, int(len(line["x"]) // 400))
    coords = list(zip(line["x"][::keep].tolist(), line["y"][::keep].tolist()))
    if coords[-1] != (float(line["x"][-1]), float(line["y"][-1])):
        coords.append((float(line["x"][-1]), float(line["y"][-1])))
    return {"fast_m": fast, "shallow_m": shallow, "fixes_in": {name: int(mask[nearest].sum()) for name, mask in masks.items()}, "coords": coords}


def _choose(lines: list[dict], wanted: int, masks: dict) -> list[dict]:
    """*wanted* lines: clean ones first, those that serve parameters nobody serves yet, then spread across the river."""
    left = sorted(lines, key=lambda line: line["quantile"])
    picks: list[dict] = []
    served: set[str] = set()
    while left and len(picks) < wanted:
        def score(line):
            new = len({name for name, count in line["fixes_in"].items() if count} - served)
            apart = min((abs(line["quantile"] - p["quantile"]) for p in picks), default=1.0 - abs(line["quantile"] - 0.5))
            return (-(line["fast_m"] + line["shallow_m"] > 0), new, apart)
        best = max(range(len(left)), key=lambda i: score(left[i]))
        best = left.pop(best)
        picks.append(best)
        served |= {name for name, count in best["fixes_in"].items() if count}
    return picks


def _float_minutes(line: dict, k) -> tuple[float, list[dict]]:
    """The time of a float with its breaks, and where the breaks fall."""
    pauses = []
    if k.break_every_h > 0 and k.break_minutes > 0:
        every = k.break_every_h * 60.0
        count = int((line["minutes"] - 1e-6) // every)
        pauses = [{"drift_minutes": i * every, "clock_minutes": i * every + (i - 1) * k.break_minutes} for i in range(1, count + 1)]
    return line["minutes"] + len(pauses) * k.break_minutes, pauses


def _along(line: dict, minutes: float) -> tuple[float, float]:
    return float(np.interp(minutes * 60.0, line["seconds"], line["x"])), float(np.interp(minutes * 60.0, line["seconds"], line["y"]))


def fast_patches(flow: FlowField, limit: float, *, min_area: float = 4.0) -> list[dict]:
    """The water faster than *limit* as polygons ``{"coords": ..., "area", "velocity_max"}``."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    tri = flow.triangles
    hot = tri[(flow.speed[tri] > float(limit)).sum(axis=1) >= 2]
    hot = hot[flow.wet[hot].all(axis=1)]
    if not len(hot):
        return []
    merged = unary_union([Polygon(np.column_stack([flow.x[t], flow.y[t]])) for t in hot]).buffer(0)
    parts = list(getattr(merged, "geoms", [merged]))
    out = []
    for part in parts:
        if part.is_empty or part.area < min_area:
            continue
        inside = np.flatnonzero((flow.x >= part.bounds[0]) & (flow.x <= part.bounds[2]) & (flow.y >= part.bounds[1]) & (flow.y <= part.bounds[3]))
        out.append({"geometry": part, "area": round(float(part.area), 1), "velocity_max": round(float(flow.speed[inside].max()), 2) if len(inside) else float(limit)})
    return out


__all__ = ["BARKER_RATIO", "fast_patches", "plan_kayak"]
