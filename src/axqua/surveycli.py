"""``axqua survey``: plan the field survey that a calibration of the case needs.

``axqua survey info <case-file>`` lists what a plan can rest on: the calibration parameters of the case with the rule that places their measurements, the settings of the last plan (or the defaults), the folder of the plan, and the points of the layer ``access_points``. ``axqua survey init <case-file>`` creates ``survey-plan.gpkg`` with the empty layer ``access_points``, in which the user sets where the surveyors reach the river. ``axqua survey conditions <case-file>`` looks up sunrise, sunset, the weather forecast and what is known about the mobile network and the DGPS corrections at the reach. ``axqua survey plan <case-file>`` plans the survey and writes the layers, the maps and the factsheets (:mod:`axqua.survey.planner`).

The settings are read from a JSON file (``--settings``) with the keys of :class:`axqua.survey.settings.SurveySettings`; without one, the settings of the last plan apply, or the defaults. The Survey planner of the QGIS plugin is filled from ``info`` and runs ``plan``.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from axqua.jobcli import _common, _setup_logging, emit, fail, parse_args

log = logging.getLogger("axqua")

ACTIONS = ("info", "init", "conditions", "plan")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axqua survey",
        description="Plan a field survey for the calibration of a case: where to measure by wading, from a drone, from a kayak or with temperature loggers, in which order, and how long it takes. "
                    "The plan is written to the folder survey-plan of the calibration phase.")
    p.add_argument("action", choices=ACTIONS, help="info: what a plan can rest on; init: create survey-plan.gpkg with the layer access_points; conditions: daylight, weather and network coverage; plan: plan the survey")
    p.add_argument("case", type=Path, help="the case file")
    p.add_argument("--settings", type=Path, default=None, help="a JSON file with the settings of the plan (default: those of the last plan, or the defaults)")
    p.add_argument("--result", default="", help="the result file of the steady run to plan with (default: the result of the case)")
    p.add_argument("--kinds", default="", help="the kinds of survey, separated by commas: ground, airborne, kayak, temperature")
    p.add_argument("--date", default="", help="the survey day, YYYY-MM-DD")
    p.add_argument("--start", default="", help="the start of the working day, HH:MM")
    p.add_argument("--offline", action="store_true", help="do not look up the weather and the network coverage in the internet")
    return _common(p)


def read_settings(cfg, args):
    """The settings of a call: the file of ``--settings`` or the saved ones, with the options of the command line on top."""
    from axqua.survey import planner
    from axqua.survey.field import SurveyError
    from axqua.survey.settings import SurveySettings

    if args.settings is not None:
        path = args.settings.expanduser()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SurveyError(f"the settings file {path.name} could not be read: {exc}", subject=str(path), remedy="Name a JSON file with the settings of the survey plan.") from exc
        if not isinstance(data, dict):
            raise SurveyError(f"the settings file {path.name} does not hold a JSON object", subject=str(path))
        settings = SurveySettings.from_dict(data)
    else:
        settings = planner.saved_settings(cfg)
    if args.result:
        settings.result = str(args.result)
    if args.kinds:
        settings.kinds = [word.strip() for word in args.kinds.split(",") if word.strip()]
    if args.date:
        settings.time.date = args.date
    if args.start:
        settings.time.start = args.start
    return settings


def _plan_lines(data: dict) -> list[str]:
    from axqua.survey.settings import KIND_TITLES

    lines = [f"survey plan in {data['folder']}"]
    for kind, files in data["files"].items():
        plan = data["plans"][kind]
        summary = plan.get("summary") or {}
        first = {"ground": f"{summary.get('verticals', 0)} verticals", "airborne": f"{summary.get('stations', 0)} hover stations", "kayak": f"{summary.get('floats', 0)} floats", "temperature": f"{summary.get('points', 0)} points"}[kind]
        lines.append(f"{KIND_TITLES[kind]}: {first}; factsheet {Path(files['factsheet']).name}, map {Path(files['map_pdf']).name}")
        lines += [f"  warning: {text}" for text in plan.get("warnings") or []]
    for entry in data["skipped"].values():
        lines.append(f"not planned: {entry['title']}: {entry['reason']}" + (f" {entry['remedy']}" if entry.get("remedy") else ""))
    lines += [f"note: {text}" for text in data.get("notes") or []]
    return lines


def run_survey(argv: list[str]) -> int:
    args = parse_args(_parser(), argv, "survey")
    _setup_logging(args)
    command = f"survey {args.action}"
    try:
        from axqua.config import load_config
        from axqua.survey import planner

        case = args.case.expanduser()
        cfg = load_config(case)
        online = False if args.offline else None
        if args.action == "info":
            data = planner.info(cfg)
            lines = [f"folder of the plan: {data['folder']}", f"result to plan with: {data['result']}" + ("" if data["result_exists"] else " (missing)")]
            lines += [f"parameter {p['name']}: {p['rule']}" + (f" {p['zone']}" if p.get("zone") is not None else "") + f", {p['points']} points" + ("" if p["use"] else " (not used)") + (f"; {p['note']}" if p.get("note") else "") for p in data["parameters"]]
            access = data["access"]
            lines.append("points of the layer access_points: " + (", ".join(role for role in ("access", "leave", "takeoff") if access.get(role)) or "none"))
        elif args.action == "init":
            data = planner.init(cfg)
            lines = [("created " if data["created"] else "exists: ") + data["gpkg"], f"Set a point with the role 'access' in the layer {data['layer']}."]
        elif args.action == "conditions":
            data = planner.conditions(cfg, read_settings(cfg, args), online=online)
            lines = [f"{day['date']}: sunrise {day.get('sunrise_text', '')}, sunset {day.get('sunset_text', '')}" + (f", {day['weather']}" if day.get("weather") else "") for day in data.get("days", [])[:3]]
            lines.append((data.get("coverage") or {}).get("statement", ""))
            lines += [f"note: {text}" for text in data.get("notes") or []]
        else:
            data = planner.make_plan(cfg, read_settings(cfg, args), online=online, case_name=case.stem)
            lines = _plan_lines(data)
        return emit(command, data, as_json=args.as_json, lines=lines)
    except BaseException as exc:  # noqa: BLE001
        return fail(command, exc, as_json=args.as_json, verbose=args.verbose)


__all__ = ["ACTIONS", "read_settings", "run_survey"]
