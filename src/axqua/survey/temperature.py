"""A water temperature survey: fixed loggers or a mobile probe, by patches of slow, shallow and of fast, deep water.

Water temperature differs most between water that stays and water that passes: a shallow, slow margin warms in the sun within an hour, the deep, fast thalweg carries the temperature of the river upstream, and a slow, deep pool may be stratified or fed by groundwater. A survey that is to say something about temperature therefore has to sample these kinds of water separately. The plan sorts the wetted reach into four classes by two thresholds, a velocity and a depth:

* slow and shallow (margins, backwaters);
* slow and deep (pools);
* fast and shallow (riffles);
* fast and deep (runs, the thalweg).

It gives every class that exists at least one measuring point and deals the others by wetted area, places them inside the patches and away from the inflow and outflow, and, where an access point is set, plans the walk to them like that of a wading survey. With **fixed loggers** the walk installs them (and is walked again to collect them); with a **mobile probe**, such as the temperature sensor an acoustic Doppler velocimeter carries, it is one round of readings.
"""

from __future__ import annotations

import numpy as np

from axqua.survey import placement, walk
from axqua.survey.field import FlowField
from axqua.survey.schedule import fmt_clock
from axqua.survey.settings import SurveySettings

CLASSES = ("slow, shallow", "slow, deep", "fast, shallow", "fast, deep")
#: Smallest depth a logger or a probe stays submerged in [m].
MIN_DEPTH = 0.05


def classify(depth, speed, slow_velocity: float, shallow_depth: float) -> np.ndarray:
    """The class of water (an index into :data:`CLASSES`) by depth and velocity."""
    return (np.asarray(speed) >= float(slow_velocity)).astype(int) * 2 + (np.asarray(depth) >= float(shallow_depth)).astype(int)


def plan_temperature(flow: FlowField, settings: SurveySettings, access=None, *, sun=None) -> dict:
    from axqua.survey.ground import timetable

    t = settings.temperature
    loggers = t.method == "loggers"
    count = max(int(t.loggers if loggers else t.points), 1)
    warnings: list[str] = []
    cls = classify(flow.depth, flow.speed, t.slow_velocity, t.shallow_depth)
    allowed = placement.reachable_mask(flow, MIN_DEPTH)
    wadeable = settings.ground.wadeable(flow.depth, flow.speed)
    grid = None
    if access is not None:
        grid = walk.build_grid(flow, settings.ground, [tuple(access)])
        on_foot = wadeable & walk.reachable_nodes(grid, flow, access[0], access[1])
    else:
        on_foot = wadeable
        warnings.append("no access point is set: the points are placed, but no walk and no timetable are planned")

    present = [c for c in range(4) if (allowed & (cls == c)).any()]
    if not present:
        return {"points": [], "patches": [], "paths": [], "events": [], "timeline": [], "warnings": warnings + ["the model has no water away from the inflow and outflow to measure a temperature in"], "summary": {"points": 0}}
    area = np.array([(flow.wet & (cls == c)).sum() for c in present], dtype=float)
    share = _deal(area, count)
    adjacency = flow.neighbors()
    degree = np.asarray(adjacency.sum(axis=1)).ravel()
    nodes, classes, by_foot = [], [], []
    spacing = 2.0 * flow.edge_length
    for c, n in zip(present, share):
        if n <= 0:
            warnings.append(f"the class '{CLASSES[c]}' gets no point: there are fewer points than classes of water")
            continue
        inside = allowed & (cls == c)
        candidates = inside & on_foot
        reach_on_foot = True
        if not candidates.any():
            candidates, reach_on_foot = inside, False
            warnings.append(f"the '{CLASSES[c]}' water cannot be waded from the access point: its point needs a boat, or a logger on a line from the bank")
        core = candidates & (np.asarray(adjacency.dot((cls == c).astype(np.int32))).ravel() == degree)
        if core.sum() >= n:
            candidates = core
        index = np.flatnonzero(candidates)
        xy = np.column_stack([flow.x[index], flow.y[index]])
        taken = np.column_stack([flow.x[nodes], flow.y[nodes]]) if nodes else None
        for pick in placement.spread(xy, int(n), taken, xy=xy, spacing=spacing):
            nodes.append(int(index[pick]))
            classes.append(c)
            by_foot.append(reach_on_foot)

    service_seconds = t.install_minutes * 60.0 if loggers else t.reading_seconds
    sites = [tuple(access) if access is not None else (float(flow.x[nodes[0]]), float(flow.y[nodes[0]]))] + [(float(flow.x[n]), float(flow.y[n])) for n in nodes]
    order = list(range(1, len(sites)))
    events, paths, timeline, marks, when = [], [], [], [], {}
    if grid is not None:
        router = walk.Router(grid, sites)
        ok = router.reachable()
        # a point that needs a boat is not walked to: the router would lead to the nearest water that can be waded
        walkable = [i for i in order if ok[i] and by_foot[i - 1]]
        order = walk.order_sites(router.seconds, walkable) + [i for i in order if i not in walkable]
        service = np.array([0.0] + [service_seconds] * len(nodes))
        events = timetable(router, walkable and order[:len(walkable)], service, settings, sun, 1, warnings)
        label = {site: f"T{position:02d}" for position, site in enumerate(order, start=1)}
        for event in events:
            if event["kind"] == "measure":
                when[event["site"]] = event
            if event["kind"] in ("walk", "return"):
                a, b = event["from"], event["site"]
                coords = router.line(a, b)
                paths.append({"who": "temperature", "number": 1, "day": event["day"] + 1, "order": event["order"], "kind": event["kind"], "from": "access" if a == 0 else label[a], "to": "access" if b == 0 else label[b], "start": fmt_clock(event["start"]), "minutes": round(event["end"] - event["start"], 1), "length": round(walk.length(coords), 1), "in_water": round(router.wet_share(a, b), 2), "coords": coords})
            if event["kind"] in ("break", "long break"):
                marks.append({"kind": event["kind"], "survey": "temperature", "day": event["day"] + 1, "at": "access" if event["site"] == 0 else label[event["site"]], "start": fmt_clock(event["start"]), "end": fmt_clock(event["end"]), "minutes": round(event["end"] - event["start"], 1), "x": sites[event["site"]][0], "y": sites[event["site"]][1]})
        names = {"start": "Start at the access point", "break": "Break", "long break": "Long break", "end": "Back at the access point", "return": "Walk back to the access point"}
        for event in sorted(events, key=lambda e: (e["day"], e["start"], e["end"])):
            if event["kind"] == "walk":
                continue
            what = (f"{'Install logger' if loggers else 'Reading'} {label[event['site']]}" if event["kind"] == "measure" else names[event["kind"]])
            timeline.append({"day": event["day"] + 1, "start": fmt_clock(event["start"]), "end": fmt_clock(event["end"]), "what": what, "kind": event["kind"]})

    rows = []
    for position, site in enumerate(order, start=1):
        node = nodes[site - 1]
        event = when.get(site)
        rows.append({
            "label": f"T{position:02d}", "order": position, "method": "logger" if loggers else "probe", "water": CLASSES[classes[site - 1]],
            "x": sites[site][0], "y": sites[site][1], "depth": round(float(flow.depth[node]), 2), "velocity": round(float(flow.speed[node]), 2),
            "reach": "wading" if by_foot[site - 1] and (event is not None or grid is None) else "boat, or a line from the bank",
            "day": event["day"] + 1 if event else 0, "start": fmt_clock(event["start"]) if event else "", "end": fmt_clock(event["end"]) if event else "",
        })
    patches = class_patches(flow, t.slow_velocity, t.shallow_depth)
    total = lambda kinds: float(sum(e["end"] - e["start"] for e in events if e["kind"] in kinds))  # noqa: E731
    wet_nodes = max(int(flow.wet.sum()), 1)
    summary = {
        "points": len(rows), "method": t.method, "by_class": {CLASSES[c]: int(sum(k == c for k in classes)) for c in range(4)},
        "area_share": {CLASSES[c]: round(float((flow.wet & (cls == c)).sum() / wet_nodes), 2) for c in range(4)},
        "slow_velocity": float(t.slow_velocity), "shallow_depth": float(t.shallow_depth),
        "days": (max(e["day"] for e in events) + 1) if events else 0,
        "start": fmt_clock(min(e["start"] for e in events)) if events else "", "end": fmt_clock(max(e["end"] for e in events)) if events else "",
        "walking_minutes": round(total(("walk", "return")), 1), "working_minutes": round(total(("measure",)), 1), "break_minutes": round(total(("break", "long break")), 1),
        "walk_length": round(sum(p["length"] for p in paths)), "not_on_foot": int(sum(r["reach"] != "wading" for r in rows)),
    }
    return {"points": rows, "patches": patches, "paths": paths, "events": marks, "timeline": timeline, "warnings": warnings, "summary": summary}


def _deal(area: np.ndarray, count: int) -> list[int]:
    """*count* points over classes of *area*: one each where there are enough, the others by area (largest remainder)."""
    k = len(area)
    if count <= k:
        out = [0] * k
        for i in np.argsort(-area)[:count]:
            out[int(i)] = 1
        return out
    rest = count - k
    ideal = area / area.sum() * rest
    extra = np.floor(ideal).astype(int)
    for i in np.argsort(-(ideal - extra))[:rest - int(extra.sum())]:
        extra[i] += 1
    return [1 + int(e) for e in extra]


def class_patches(flow: FlowField, slow_velocity: float, shallow_depth: float, *, max_cells: int = 250_000, min_cells: int = 6) -> list[dict]:
    """The patches of every class of water as polygons ``{"geometry", "water", "area"}``, from a raster of the reach classified with the same two limits as the nodes."""
    from rasterio import features
    from rasterio.transform import from_origin
    from shapely.geometry import shape

    wet = flow.wet
    if not wet.any():
        return []
    x0, x1, y0, y1 = flow.x[wet].min(), flow.x[wet].max(), flow.y[wet].min(), flow.y[wet].max()
    cell = max(1.0, flow.edge_length, float(np.sqrt((x1 - x0) * (y1 - y0) / max_cells)))
    nx, ny = int(np.ceil((x1 - x0) / cell)) + 1, int(np.ceil((y1 - y0) / cell)) + 1
    gx, gy = np.meshgrid(x0 + (np.arange(nx) + 0.5) * cell, y1 - (np.arange(ny) + 0.5) * cell + cell)
    depth, u, v, inside = flow.sample(gx.ravel(), gy.ravel())
    speed = np.hypot(u, v)
    grid = np.full(nx * ny, 255, dtype=np.uint8)
    water = inside & (depth > flow.wet_depth)
    grid[water] = classify(depth[water], speed[water], slow_velocity, shallow_depth).astype(np.uint8)
    grid = grid.reshape(ny, nx)
    grid = features.sieve(grid, size=min_cells, connectivity=4)
    transform = from_origin(x0, y1 + cell, cell, cell)
    out = []
    for geometry, value in features.shapes(grid, mask=grid != 255, transform=transform, connectivity=4):
        polygon = shape(geometry).simplify(0.5 * cell)
        if not polygon.is_empty:
            out.append({"geometry": polygon, "water": CLASSES[int(value)], "area": round(float(polygon.area), 1)})
    return out


__all__ = ["CLASSES", "class_patches", "classify", "plan_temperature"]
