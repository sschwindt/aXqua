"""A ground-based velocity survey by wading: the verticals, the walk and the timetable.

The plan answers three questions for an acoustic Doppler velocimeter on a wading rod (a FlowTracker, for instance): where the verticals are, in which order they are visited, and when the survey is done.

**Where.** The verticals are placed for the calibration parameters (:mod:`axqua.survey.placement`), among the water that can be waded and reached on foot from the access point and back.

**How many depths.** Shallow verticals are measured at one depth, deeper ones at two or three (the thresholds are settings). All heights count from the riverbed: 0.6 h for one depth, 0.2 h and 0.8 h for two, and 0.2 h, 0.6 h and 0.8 h for three. aXqua compares every vertical with the model at 0.6 h above the riverbed, from a logarithmic profile through the readings where there are two or three.

**How long.** A vertical takes the measuring time per depth, the time to set the probe to its height above the riverbed for every depth, and the time to measure the water depth. The pressure sensor of the probe is adjusted to the atmosphere at the start of every day and then at the interval of the settings. Every vertical is also recorded by a second surveyor with a DGPS rover, which costs no time as long as the rover receives its correction data.

With more than one surveyor the tour is cut into one run per surveyor, and each DGPS rover (one for up to three surveyors by default) gets a walk of its own through the verticals of its surveyors in the order they are measured.
"""

from __future__ import annotations

import math

import numpy as np

from axqua.survey import placement, walk
from axqua.survey.field import FlowField, SurveyError
from axqua.survey.schedule import Breaks, day_window, fmt_clock, fmt_duration
from axqua.survey.settings import SurveySettings

#: Days after which a plan stops counting and says the survey is too large.
MAX_DAYS = 30


def plan_ground(flow: FlowField, settings: SurveySettings, needs, access, *, sun=None) -> dict:
    """Plan the wading survey from the access point ``(x, y)``."""
    g = settings.ground
    if access is None:
        raise SurveyError("a ground-based survey needs an access point", remedy="Set one point with the role 'access' in the layer access_points of survey-plan.gpkg: the place where the surveyors reach the river.")
    ax, ay = float(access[0]), float(access[1])
    wet = flow.wet
    reach = math.hypot(float(np.ptp(flow.x[wet])), float(np.ptp(flow.y[wet]))) if wet.any() else 0.0
    nearest = float(np.min(np.hypot(flow.x[wet] - ax, flow.y[wet] - ay))) if wet.any() else math.inf
    if nearest > max(2000.0, reach):
        raise SurveyError(f"the access point lies {nearest:.0f} m from the water of the model", remedy="Move the access point to the river bank of the modeled reach.")
    warnings: list[str] = []
    grid = walk.build_grid(flow, g, [(ax, ay)])
    wadeable = g.wadeable(flow.depth, flow.speed)
    allowed = placement.reachable_mask(flow, g.min_depth) & wadeable & walk.reachable_nodes(grid, flow, ax, ay)
    points = placement.place(flow, needs, allowed, min_spacing=settings.min_spacing, what="wading measurement")
    warnings += points.warnings
    for need in needs:
        if need.use and need.rule == "zone" and points.served.get(need.name, 0) == 0:
            in_zone = wet & (flow.zone == int(need.zone or 0))
            if in_zone.any() and not (in_zone & wadeable).any():
                warnings.append(f"roughness zone {need.zone} is too deep or too fast to wade at this discharge (depth up to {flow.depth[in_zone].max():.1f} m, velocity up to {flow.speed[in_zone].max():.1f} m/s): plan it at a lower discharge, or cover it with an airborne or a kayak survey")
    if not len(points):
        return {"points": [], "paths": [], "events": [], "timeline": [], "warnings": warnings, "summary": {"verticals": 0}, "grid": grid, "access": (ax, ay)}

    nodes = np.asarray(points.node, dtype=int)
    sites = [(ax, ay)] + [(float(flow.x[n]), float(flow.y[n])) for n in nodes]
    router = walk.Router(grid, sites)
    n_depths = [g.depths_at(float(flow.depth[n])) for n in nodes]
    service = np.array([0.0] + [g.vertical_seconds(n) for n in n_depths])
    order = walk.order_sites(router.seconds, list(range(1, len(sites))))
    # one team per DGPS rover works a stretch of the tour; within a team the surveyors leapfrog along it
    surveyors, per = max(1, int(g.surveyors)), max(1, int(g.surveyors_per_dgps))
    runs, team_of = [], {}
    sizes = [min(per, surveyors - first) for first in range(0, surveyors, per)]
    stretches = walk.split_tour(router.seconds, order, service, sizes)     # the work of a stretch in proportion to its team
    for team, (stretch, size) in enumerate(zip(stretches, sizes), start=1):
        for run in walk.deal_tour(router.seconds, stretch, service, size):
            runs.append(run)
            team_of[len(runs)] = team
    if len(runs) < surveyors:
        warnings.append(f"{len(runs)} of the {surveyors} surveyors have verticals to measure; there are not more verticals than that")

    events: list[dict] = []
    for number, run in enumerate(runs, start=1):
        events += timetable(router, run, service, settings, sun, number, warnings, pressure_interval_h=g.pressure_interval_h, pressure_minutes=g.pressure_minutes)
    measured = sorted((e for e in events if e["kind"] == "measure"), key=lambda e: (e["day"], e["start"], e["surveyor"]))
    several = len(runs) > 1
    rows, label_of = [], {}
    for sequence, event in enumerate(measured, start=1):
        site = event["site"]
        node = int(nodes[site - 1])
        depth, n = float(flow.depth[node]), n_depths[site - 1]
        heights = g.heights(n)
        label = f"{event['surveyor']}.{event['order']:02d}" if several else f"{event['order']:02d}"
        label_of[site] = label
        rows.append({
            "label": label, "sequence": sequence, "surveyor": event["surveyor"], "day": event["day"] + 1, "order": event["order"],
            "x": sites[site][0], "y": sites[site][1],
            "depth": round(depth, 3), "velocity": round(float(flow.speed[node]), 3), "n_depths": n,
            "heights": ", ".join(f"{h:g} h" for h in heights),
            "heights_m": ", ".join(f"{h * depth:.2f}" for h in heights),
            "zone": int(flow.zone[node]) if flow.zone is not None else 0,
            "serves": ", ".join(points.purposes[site - 1]),
            "start": fmt_clock(event["start"]), "end": fmt_clock(event["end"]), "minutes": round(event["end"] - event["start"], 1),
            "start_min": event["start"], "end_min": event["end"],
        })

    paths = []
    for event in events:
        if event["kind"] not in ("walk", "return"):
            continue
        a, b = event["from"], event["site"]
        coords = router.line(a, b)
        paths.append({
            "who": "surveyor", "number": event["surveyor"], "day": event["day"] + 1, "order": event["order"], "kind": event["kind"],
            "from": "access" if a == 0 else label_of.get(a, ""), "to": "access" if b == 0 else label_of.get(b, ""),
            "start": fmt_clock(event["start"]), "minutes": round(event["end"] - event["start"], 1), "length": round(walk.length(coords), 1),
            "in_water": round(router.wet_share(a, b), 2), "coords": coords,
        })

    dgps = _dgps(router, team_of, measured, settings, sun) if several else {"paths": [], "rovers": 1, "lag": 0.0}
    for path in dgps["paths"]:
        path["from"] = "access" if path["from"] == 0 else label_of.get(path["from"], "")
        path["to"] = "access" if path["to"] == 0 else label_of.get(path["to"], "")
    paths += dgps["paths"]

    marks = []
    for event in events:
        if event["kind"] in ("pressure", "break", "long break"):
            x, y = sites[event["site"]]
            marks.append({"kind": "pressure adjustment" if event["kind"] == "pressure" else event["kind"], "surveyor": event["surveyor"], "day": event["day"] + 1, "at": "access" if event["site"] == 0 else label_of.get(event["site"], ""), "start": fmt_clock(event["start"]), "end": fmt_clock(event["end"]), "minutes": round(event["end"] - event["start"], 1), "x": x, "y": y})

    return {"points": rows, "paths": paths, "events": marks, "timeline": _timeline(events, label_of), "warnings": warnings, "summary": _summary(events, rows, paths, runs, dgps, settings, sun), "grid": grid, "access": (ax, ay)}


def timetable(router, run, service, settings: SurveySettings, sun, surveyor: int, warnings: list, *, pressure_interval_h: float = 0.0, pressure_minutes: float = 0.0) -> list[dict]:
    """The day, or days, of one surveyor as events with ``start`` and ``end`` in minutes after midnight. The sites of *run* are visited in their order from site 0 (the access point) and back; *service* is the time at every site in seconds."""
    seconds = router.seconds
    events: list[dict] = []
    day, here, order = 0, 0, 0
    window = day_window(settings.time, sun, day)
    state = {}

    def begin(day_number: int) -> float:
        nonlocal window
        window = day_window(settings.time, sun, day_number)
        state["breaks"] = Breaks(settings.time, window.start)
        state["done"] = 0
        events.append({"kind": "start", "surveyor": surveyor, "day": day_number, "site": 0, "start": window.start, "end": window.start})
        return _pressure(window.start, 0, day_number)

    def _pressure(now: float, site: int, day_number: int) -> float:
        state["pressure"] = now
        if pressure_minutes <= 0:
            return now
        events.append({"kind": "pressure", "surveyor": surveyor, "day": day_number, "site": site, "start": now, "end": now + pressure_minutes})
        return now + pressure_minutes

    def go_home(now: float, day_number: int) -> float:
        back = seconds[here, 0] / 60.0
        events.append({"kind": "return", "surveyor": surveyor, "day": day_number, "order": order + 1, "from": here, "site": 0, "start": now, "end": now + back})
        events.append({"kind": "end", "surveyor": surveyor, "day": day_number, "site": 0, "start": now + back, "end": now + back})
        return now + back

    now = begin(day)
    for site in run:
        while True:
            pause, kind = state["breaks"].due(now)
            adjust = pressure_minutes if pressure_interval_h > 0 and now + pause - state["pressure"] >= pressure_interval_h * 60.0 else 0.0
            there, work, back = seconds[here, site] / 60.0, service[site] / 60.0, seconds[site, 0] / 60.0
            fits = now + pause + there + adjust + work + back <= window.end + 1e-6
            if fits or not state["done"]:
                if not fits:
                    warnings.append(f"surveyor {surveyor}, day {day + 1}: the first vertical and the way back take longer than the working day ({fmt_duration(window.end - window.start)}); the day is planned longer than that")
                break
            go_home(now, day)
            day, here = day + 1, 0
            if day >= MAX_DAYS:
                raise SurveyError(f"the survey takes more than {MAX_DAYS} days per surveyor", remedy="Plan fewer points per parameter, more surveyors, or a longer working day.")
            now = begin(day)
        if pause:
            events.append({"kind": kind, "surveyor": surveyor, "day": day, "site": here, "start": now, "end": now + pause})
            now += pause
            state["breaks"].taken(now, kind)
        order += 1
        events.append({"kind": "walk", "surveyor": surveyor, "day": day, "order": order, "from": here, "site": site, "start": now, "end": now + there})
        now += there
        if adjust:
            now = _pressure(now, site, day)
        events.append({"kind": "measure", "surveyor": surveyor, "day": day, "order": order, "site": site, "start": now, "end": now + work})
        now += work
        here = site
        state["done"] += 1
    go_home(now, day)
    return events


def _dgps(router, team_of: dict, measured, settings: SurveySettings, sun) -> dict:
    """The walk of every DGPS rover through the verticals of its surveyors, in the order they are measured."""
    g = settings.ground
    rovers = max(team_of.values())
    paths, worst = [], 0.0
    for rover in range(rovers):
        team = {surveyor for surveyor, number in team_of.items() if number == rover + 1}
        for day in sorted({e["day"] for e in measured if e["surveyor"] in team}):
            todo = [e for e in measured if e["surveyor"] in team and e["day"] == day]
            now, here = day_window(settings.time, sun, day).start, 0
            for order, event in enumerate(todo, start=1):
                there = router.seconds[here, event["site"]] / 60.0
                arrive = now + there
                worst = max(worst, arrive - event["end"])
                coords = router.line(here, event["site"])
                paths.append({"who": "DGPS", "number": rover + 1, "day": day + 1, "order": order, "kind": "walk", "from": here, "to": event["site"], "start": fmt_clock(now), "minutes": round(there, 1), "length": round(walk.length(coords), 1), "in_water": round(router.wet_share(here, event["site"]), 2), "coords": coords})
                now = max(arrive, event["start"]) + g.dgps_seconds / 60.0
                here = event["site"]
            back = router.seconds[here, 0] / 60.0
            coords = router.line(here, 0)
            paths.append({"who": "DGPS", "number": rover + 1, "day": day + 1, "order": len(todo) + 1, "kind": "return", "from": here, "to": 0, "start": fmt_clock(now), "minutes": round(back, 1), "length": round(walk.length(coords), 1), "in_water": round(router.wet_share(here, 0), 2), "coords": coords})
    return {"paths": paths, "rovers": rovers, "lag": max(worst, 0.0)}


def _timeline(events, label_of) -> list[dict]:
    """The timetable in words, one row per event, by surveyor and time."""
    names = {"start": "Start at the access point", "pressure": "Adjust the pressure sensor to the atmosphere", "break": "Break", "long break": "Long break", "end": "Back at the access point"}
    rows = []
    for event in sorted(events, key=lambda e: (e["surveyor"], e["day"], e["start"], e["end"])):
        kind = event["kind"]
        if kind == "walk":
            continue
        if kind == "measure":
            text = f"Vertical {label_of.get(event['site'], '')}"
        elif kind == "return":
            text = "Walk back to the access point"
        else:
            text = names[kind]
        rows.append({"surveyor": event["surveyor"], "day": event["day"] + 1, "start": fmt_clock(event["start"]), "end": fmt_clock(event["end"]), "what": text, "kind": kind})
    return rows


def _summary(events, rows, paths, runs, dgps, settings: SurveySettings, sun) -> dict:
    total = lambda kinds: float(sum(e["end"] - e["start"] for e in events if e["kind"] in kinds))  # noqa: E731
    days = max(e["day"] for e in events) + 1
    per_day = []
    for day in range(days):
        todays = [e for e in events if e["day"] == day]
        window = day_window(settings.time, sun, day)
        per_day.append({"day": day + 1, "date": window.date, "start": fmt_clock(min(e["start"] for e in todays)), "end": fmt_clock(max(e["end"] for e in todays)), "verticals": sum(e["kind"] == "measure" for e in todays), "limit": window.limit, "latest_end": fmt_clock(window.end)})
    own = [p for p in paths if p["who"] == "surveyor"]
    return {
        "verticals": len(rows), "one_depth": sum(r["n_depths"] == 1 for r in rows), "two_depths": sum(r["n_depths"] == 2 for r in rows), "three_depths": sum(r["n_depths"] == 3 for r in rows),
        "readings": int(sum(r["n_depths"] for r in rows)), "surveyors": len(runs), "dgps_rovers": dgps["rovers"], "dgps_lag_minutes": round(dgps["lag"], 1),
        "days": days, "per_day": per_day,
        "measuring_minutes": round(total(("measure",)), 1), "walking_minutes": round(total(("walk", "return")), 1), "break_minutes": round(total(("break", "long break")), 1), "pressure_minutes": round(total(("pressure",)), 1),
        "pressure_adjustments": sum(e["kind"] == "pressure" for e in events),
        "walk_length": round(sum(p["length"] for p in own), 0), "walk_in_water": round(sum(p["length"] * p["in_water"] for p in own), 0),
        "max_depth": max(r["depth"] for r in rows), "max_velocity": max(r["velocity"] for r in rows),
    }


__all__ = ["MAX_DAYS", "plan_ground", "timetable"]
