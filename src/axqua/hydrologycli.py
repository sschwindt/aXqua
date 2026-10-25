"""``axqua hydrology``: the data of a hydrology case, and its checkup.

``info``      where the catchment is, its countries and what the case has so far ``portals``   where rain, soil and weather data come from: national agencies, then global ``textures``  the Green-Ampt parameters of the USDA texture classes (Rawls et al., 1983) ``rainfall``  turn a rain record into the series the case reads ``design``    a design storm from a depth-duration table (Euler II, alternating, block) ``fetch``     download ``event`` (rain and weather), ``moisture``, ``soil`` or ``sealed`` ``check``     the hydrology checkup, the terrain analysis included

The data wizard of the plugin is a form over these commands. Each command that writes a file answers with ``settings``: the case keys that point at it, which the wizard puts into the case file through ``axqua case write``. Files go into ``<case folder>/user-sources/hydrology`` unless ``--out`` says otherwise.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from axqua.jobcli import _common, _setup_logging, emit, fail, parse_args

ACTIONS = ("info", "portals", "textures", "rainfall", "design", "fetch", "check")
FETCHES = ("event", "moisture", "soil", "sealed")


def _relative(path: Path, case: Path) -> str:
    try:
        return os.path.relpath(Path(path).resolve(), Path(case).resolve().parent)
    except ValueError:
        return str(path)


def _out_dir(args) -> Path:
    if args.out is not None:
        return Path(args.out)
    return Path(args.case).resolve().parent / "user-sources" / "hydrology"


def run_hydrology(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="axqua hydrology",
        description="The data of a hydrology case (rain, weather, soil) and its "
                    "checkup. Ask the national meteorological agency first; the "
                    "downloads are global data sets that fit any catchment roughly.")
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("case", nargs="?", type=Path, help="the case file")
    parser.add_argument("what", nargs="?", default="",
                        help="fetch: one of " + ", ".join(FETCHES))
    parser.add_argument("--out", type=Path, default=None,
                        help="folder for the files written (default: "
                             "user-sources/hydrology beside the case file)")
    parser.add_argument("--country", action="append", default=[],
                        help="portals: ISO code of a country (repeatable)")
    parser.add_argument("--file", type=Path, help="rainfall, design: the input table")
    parser.add_argument("--units", default="mm/h", choices=("mm/h", "mm"))
    parser.add_argument("--stamp", default="end", choices=("start", "end"))
    parser.add_argument("--start", default=None,
                        help="rainfall: time zero (date and time); fetch event: start")
    parser.add_argument("--end", default=None, help="fetch event: end (date and time)")
    parser.add_argument("--at", default=None, help="fetch moisture: date and time (UTC)")
    parser.add_argument("--pairs", default="",
                        help="design: duration:depth pairs in min:mm, e.g. 5:9.8,60:30.6")
    parser.add_argument("--duration", type=float, default=60.0,
                        help="design: storm duration [min]")
    parser.add_argument("--step", type=float, default=5.0,
                        help="design: block length [min]")
    parser.add_argument("--distribution", default="euler2",
                        choices=("euler2", "alternating", "block"))
    parser.add_argument("--peak", type=float, default=None,
                        help="design, alternating: position of the peak (0 to 1)")
    parser.add_argument("--no-terrain", action="store_true",
                        help="check: leave out the analysis of the terrain model")
    args = parse_args(_common(parser), argv, "hydrology")
    _setup_logging(args)
    command = f"hydrology.{args.action}"
    try:
        data, lines = _dispatch(args)
        return emit(command, data, as_json=args.as_json, lines=lines)
    except BaseException as exc:  # noqa: BLE001
        return fail(command, exc, as_json=args.as_json, verbose=args.verbose)


def _need_case(args):
    if args.case is None:
        raise ValueError(f"axqua hydrology {args.action} needs the case file")
    from axqua.config import load_config

    return load_config(args.case)


def _dispatch(args) -> tuple[dict, list[str]]:
    from axqua.hydrology import portals

    if args.action == "textures":
        from axqua.hydrology import soil

        rows = [{"texture": t.name, "ks_mm_h": round(t.ks_mm_h, 3),
                 "suction_m": round(t.suction_m, 4), "theta_s": t.porosity,
                 "theta_r": round(t.theta_r, 3)} for t in soil.RAWLS_1983.values()]
        return ({"source": "Rawls, Brakensiek and Miller (1983), J. Hydraulic Eng. "
                           "109(1), Table 2", "textures": rows},
                [f"{r['texture']:<16} Ks {r['ks_mm_h']:>7} mm/h  psi {r['suction_m']} m"
                 f"  theta_s {r['theta_s']}" for r in rows])
    if args.action == "portals":
        countries = list(args.country)
        if not countries and args.case is not None:
            countries = _countries(_need_case(args))
        data = portals.portals(countries)
        lines = [data["advice"], ""]
        for item in data["national"] + data["global"]:
            lines.append(f"{item['name']}: {item['url']}")
        return data, lines
    if args.action == "info":
        return _info(args)
    if args.action == "rainfall":
        return _rainfall(args)
    if args.action == "design":
        return _design(args)
    if args.action == "fetch":
        return _fetch(args)
    return _check(args)


def _countries(cfg) -> list[str]:
    from axqua.hydrology import fetch, portals

    lon, lat = fetch.catchment_lonlat(cfg)
    return portals.countries_at(lon, lat)


def _info(args) -> tuple[dict, list[str]]:
    from axqua.hydrology import fetch, portals
    from axqua.solvers.telemac.mesh import roi_polygon

    cfg = _need_case(args)
    lon, lat = fetch.catchment_lonlat(cfg)
    area = roi_polygon(cfg).area
    h = cfg.hydrology
    files = {name: (str(getattr(h, name)) if getattr(h, name) is not None else "")
             for name in ("rainfall", "weather", "soil_zones", "soil_table",
                          "sealed_surfaces")}
    data = {"enabled": cfg.hydrology_mode, "longitude": lon, "latitude": lat,
            "area_m2": area, "countries": portals.countries_at(lon, lat),
            "files": files, "outlet_condition": h.outlet_condition,
            "infiltration": h.infiltration, "green_ampt": h.green_ampt,
            "evaporation": h.evaporation, "wind": bool(h.wind),
            "initial_water_content": h.initial_water_content,
            "out_dir": str(_out_dir(args))}
    lines = [f"catchment at {lat:.4f} N {lon:.4f} E, {area / 1e6:.3f} km2, "
             f"countries {', '.join(data['countries']) or 'none listed'}"]
    lines += [f"{k}: {v or '(not set)'}" for k, v in files.items()]
    return data, lines


def _rainfall(args) -> tuple[dict, list[str]]:
    from axqua.hydrology import rainfall

    if args.file is None:
        raise ValueError("axqua hydrology rainfall needs --file, the rain record")
    series = rainfall.read_series(args.file, units=args.units, stamp=args.stamp,
                                  start=args.start)
    problems = series.problems()
    if problems:
        raise ValueError(f"{args.file.name}: {'; '.join(problems)}")
    path = series.write(_out_dir(args) / "rainfall.csv")
    # a record without dates clears the time zero of an earlier event: it would
    # otherwise date this rain, and put the weather of that event under it
    settings = {"hydrology.rainfall": _relative(path, args.case) if args.case
                else str(path),
                "hydrology.rainfall_units": "mm/h",
                "hydrology.rainfall_start": (series.origin.isoformat()
                                             if series.origin is not None else None)}
    data = {"rainfall": str(path), "total_mm": series.total_mm,
            "peak_mm_h": series.peak_mm_h, "duration_s": series.end,
            "settings": settings, "notes": series.notes}
    # what the wizard shows beside "done"; the notes stay in the answer
    caution = []
    if series.gaps:
        caution.append(f"{series.gaps} intervals ({series.gap_hours:g} h) have no value; "
                       "they stay gaps in the file and are simulated as dry.")
    if series.step_s >= 86400.0:
        caution.append("The record has daily values: a day is far too coarse to resolve "
                       "a storm. Use hourly or 10-minute data.")
    if caution:
        data["caution"] = " ".join(caution)
    return data, [f"{path}: {series.total_mm:.1f} mm, peak {series.peak_mm_h:.1f} mm/h"]


def _design(args) -> tuple[dict, list[str]]:
    from axqua.hydrology import rainfall

    if args.file is not None:
        durations, depths = rainfall.read_depth_duration(args.file)
    elif args.pairs:
        pairs = [p.split(":") for p in args.pairs.replace(" ", "").split(",") if p]
        durations = [float(a) for a, _ in pairs]
        depths = [float(b) for _, b in pairs]
    else:
        raise ValueError("a design storm needs --file (a depth-duration table) or "
                         "--pairs")
    storm = rainfall.design_storm(durations, depths, duration_min=args.duration,
                                  step_min=args.step, distribution=args.distribution,
                                  peak=args.peak)
    path = storm.write(_out_dir(args) / "design-storm.csv")
    # a design storm has no date: the time zero of an earlier event goes
    settings = {"hydrology.rainfall": _relative(path, args.case) if args.case
                else str(path),
                "hydrology.rainfall_units": "mm/h",
                "hydrology.rainfall_start": None}
    data = {"rainfall": str(path), "total_mm": storm.total_mm,
            "peak_mm_h": storm.peak_mm_h, "settings": settings,
            "blocks": storm.to_frame().to_dict(orient="list")}
    return data, [f"{path}: {storm.notes[-1]}, peak {storm.peak_mm_h:.1f} mm/h"]


def _fetch(args) -> tuple[dict, list[str]]:
    from axqua.hydrology import fetch

    cfg = _need_case(args)
    what = args.what
    if what not in FETCHES:
        raise ValueError(f"fetch one of {', '.join(FETCHES)}, not {what!r}")
    out = _out_dir(args)

    def rel(path) -> str:
        return _relative(Path(path), args.case)

    if what == "event":
        if not (args.start and args.end):
            raise ValueError("fetch event needs --start and --end (UTC)")
        event = fetch.fetch_event(cfg, args.start, args.end, out)
        settings = {"hydrology.rainfall": rel(event.rainfall),
                    "hydrology.rainfall_units": "mm/h",
                    "hydrology.rainfall_start": event.origin,
                    "hydrology.weather": rel(event.weather)}
        data = {"rainfall": str(event.rainfall), "weather": str(event.weather),
                "total_mm": event.total_mm, "peak_mm_h": event.peak_mm_h,
                "origin": event.origin, "settings": settings,
                "caution": "Reanalysis underestimates short rain intensities; prefer "
                           "gauge or radar data of the national agency."}
        return data, [f"rain {event.total_mm:.1f} mm (peak {event.peak_mm_h:.1f} mm/h) "
                      f"-> {event.rainfall}", f"weather -> {event.weather}"]
    if what == "moisture":
        at = args.at or args.start
        if not at:
            raise ValueError("fetch moisture needs --at (date and time, UTC)")
        data = fetch.fetch_moisture(cfg, at)
        data["settings"] = {"hydrology.initial_water_content":
                            data["initial_water_content"]}
        return data, [f"soil moisture {data['initial_water_content']} m3/m3 at "
                      f"{data['at_utc']}"]
    if what == "soil":
        data = fetch.fetch_soil(cfg, out)
        data["settings"] = {"hydrology.soil_zones": rel(data["soil_zones"]),
                            "hydrology.soil_zone_field": "Zone ID",
                            "hydrology.soil_table": rel(data["soil_table"])}
        return data, [f"soil zones -> {data['soil_zones']}: "
                      + ", ".join(f"{k} {100 * v:.0f} %"
                                  for k, v in data["textures"].items())]
    data = fetch.fetch_sealed(cfg, out)
    data["settings"] = {"hydrology.sealed_surfaces": rel(data["sealed_surfaces"])}
    return data, [f"sealed surfaces -> {data['sealed_surfaces']}: "
                  f"{100 * data['sealed_share']:.1f} % of the catchment"]


def _check(args) -> tuple[dict, list[str]]:
    from axqua.hydrology.checks import run_checks

    cfg = _need_case(args)
    findings = run_checks(cfg, terrain=not args.no_terrain)
    data = {"path": str(args.case), "enabled": cfg.hydrology_mode,
            "findings": [f.as_dict() for f in findings]}
    lines = [f.line() for f in findings] or [
        f"{args.case}: no findings" if cfg.hydrology_mode
        else f"{args.case}: the case is not in hydrology mode"]
    return data, lines
