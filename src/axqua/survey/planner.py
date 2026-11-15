"""A survey plan from the case file to the folder a surveyor opens.

:func:`make_plan` reads the flow field of the steady run, the calibration parameters of the case and the points of the layer ``access_points``, plans every kind of survey the settings name, and writes the folder ``survey-plan`` of the calibration phase:

* ``survey-plan.gpkg`` with the layers QGIS shows (:data:`axqua.survey.output.LAYERS`),
* ``survey-map-<kind>.png`` and ``.pdf``, one map on an A4 sheet per kind of survey,
* ``survey-factsheet-<kind>.odt``, one page per kind of survey,
* ``survey-plan.json`` with every number of the plan, and ``survey-settings.json`` with the assumptions it rests on.

A kind of survey that cannot be planned (a kayak survey without a leave point, for example) does not stop the others: it is reported in ``skipped`` with its reason. Only when nothing can be planned does :func:`make_plan` raise.

The folder is nothing Clean Up offers: it holds no run, no listing and no result file.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

import numpy as np

from axqua.survey import conditions as conditions_module
from axqua.survey.field import FlowField, SurveyError, load_field, result_path
from axqua.survey.output import ACCESS_LAYER, GPKG_NAME, LAYERS, ROLES, draw_map, ensure_access_layer, read_access, write_layers
from axqua.survey.placement import case_needs
from axqua.survey.settings import KIND_TITLES, KINDS, RULES, ParameterNeed, SurveySettings

log = logging.getLogger("axqua")

FOLDER = "survey-plan"
PLAN_JSON = "survey-plan.json"
SETTINGS_JSON = "survey-settings.json"
#: Keys of a plan that hold geometry or working data; ``survey-plan.gpkg`` keeps the geometry.
_HEAVY = ("coords", "polygon", "geometry", "grid")
#: What a plan must hold to be worth a map and a factsheet, by kind of survey.
_CONTENT = {"ground": "points", "airborne": "stations", "kayak": "tracks", "temperature": "points"}


def folder(cfg) -> Path:
    """The folder of the survey plan of the case."""
    return Path(cfg.calibration_path(FOLDER))


def gpkg_path(cfg) -> Path:
    return folder(cfg) / GPKG_NAME


def saved_settings(cfg) -> SurveySettings:
    """The settings of the last plan of the case, or the defaults."""
    path = folder(cfg) / SETTINGS_JSON
    try:
        return SurveySettings.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return SurveySettings()


def plain(value, *, heavy: bool = False):
    """*value* as JSON can hold it: numpy numbers and arrays as plain Python, and without the geometry of a plan unless *heavy*."""
    if isinstance(value, dict):
        return {str(k): plain(v, heavy=heavy) for k, v in value.items() if heavy or k not in _HEAVY}
    if isinstance(value, (list, tuple, set)):
        return [plain(v, heavy=heavy) for v in value]
    if isinstance(value, np.ndarray):
        return plain(value.tolist(), heavy=heavy)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _needs_as_dicts(needs: list[ParameterNeed]) -> list[dict]:
    import dataclasses

    return [dataclasses.asdict(need) for need in needs]


def info(cfg) -> dict:
    """What the wizard shows before anything is planned. Reads no flow field."""
    settings = saved_settings(cfg)
    needs = case_needs(cfg, settings)
    gpkg = gpkg_path(cfg)
    result = result_path(cfg, settings.result)
    data = settings.as_dict()
    data["parameters"] = _needs_as_dicts(needs)
    last = None
    try:
        stored = json.loads((folder(cfg) / PLAN_JSON).read_text(encoding="utf-8"))
        last = {"made": stored.get("made", ""), "kinds": list((stored.get("plans") or {}).keys()), "files": stored.get("files") or {}}
    except (OSError, ValueError):
        pass
    return plain({
        "settings": data,
        "parameters": data["parameters"],
        "kinds": [{"key": kind, "title": KIND_TITLES[kind]} for kind in KINDS],
        "rules": RULES,
        "roles": ROLES,
        "folder": folder(cfg),
        "gpkg": gpkg,
        "gpkg_exists": gpkg.is_file(),
        "access_layer": ACCESS_LAYER,
        "access": read_access(gpkg, cfg.crs_epsg),
        "layers": {name: geometry for name, (geometry, _) in LAYERS.items()},
        "crs_epsg": cfg.crs_epsg,
        "has_zones": cfg.geodata.roughness_zones is not None,
        "built": Path(cfg.model_path(cfg.geometry_slf)).is_file(),
        "result": result,
        "result_exists": result.is_file(),
        "last_plan": last,
    })


def init(cfg) -> dict:
    """Create ``survey-plan.gpkg`` with the empty layer ``access_points`` unless the case has one."""
    gpkg = gpkg_path(cfg)
    try:
        created = ensure_access_layer(gpkg, cfg.crs_epsg)
    except Exception as exc:  # noqa: BLE001 - whatever the driver calls a folder it cannot write to
        raise SurveyError(f"{GPKG_NAME} could not be created in {gpkg.parent}: {exc}", subject=str(gpkg), remedy="Check that the folder of the case can be written to.") from exc
    return plain({"gpkg": gpkg, "layer": ACCESS_LAYER, "created": created, "roles": ROLES, "crs_epsg": cfg.crs_epsg, "access": read_access(gpkg, cfg.crs_epsg)})


def conditions(cfg, settings: SurveySettings, *, online: bool | None = None) -> dict:
    """Daylight, weather and network coverage at the reach for the day of *settings*."""
    _check(settings)
    flow = load_field(cfg, settings.result)
    return plain(conditions_module.look_up(cfg, flow, settings, online=online))


def _check(settings: SurveySettings) -> None:
    problems = settings.problems()
    if problems:
        raise SurveyError("the survey cannot be planned with these settings: " + "; ".join(problems), remedy="Correct the values in the Survey planner, then plan again.")


def ground_level(cfg, flow: FlowField, point) -> float | None:
    """The ground elevation at ``(x, y)``: the bed of the model inside the mesh, the terrain model of the case outside, None where neither knows."""
    if point is None:
        return None
    from matplotlib.tri import LinearTriInterpolator

    x, y = float(point[0]), float(point[1])
    value = LinearTriInterpolator(flow.triangulation, flow.bed)(x, y)
    if not np.ma.is_masked(value) and np.isfinite(float(value)):
        return float(value)
    dem_path = cfg.geodata.dem_initial
    if dem_path is None or not Path(dem_path).is_file():
        return None
    try:
        import rasterio
        from rasterio.warp import transform as warp_transform

        with rasterio.open(dem_path) as dem:
            xs, ys = ([x], [y]) if dem.crs is None or not cfg.crs_epsg else warp_transform(f"EPSG:{cfg.crs_epsg}", dem.crs, [x], [y])
            z = float(next(dem.sample([(xs[0], ys[0])]))[0])
            if not np.isfinite(z) or (dem.nodata is not None and z == dem.nodata):
                return None
            return z
    except Exception:  # noqa: BLE001 - a point outside the raster, or a raster that cannot be read: the level is unknown
        return None


def _plan_kind(kind: str, cfg, flow: FlowField, settings: SurveySettings, needs, access: dict, sun) -> dict:
    if kind == "ground":
        from axqua.survey.ground import plan_ground

        return plan_ground(flow, settings, needs, access["access"], sun=sun)
    if kind == "airborne":
        from axqua.survey.drone import plan_airborne

        takeoff = access["takeoff"] or access["access"]
        return plan_airborne(flow, settings, needs, takeoff, sun=sun, takeoff_level=ground_level(cfg, flow, takeoff))
    if kind == "kayak":
        from axqua.survey.kayak import plan_kayak

        return plan_kayak(flow, settings, needs, access["access"], access["leave"], sun=sun)
    from axqua.survey.temperature import plan_temperature

    return plan_temperature(flow, settings, access["access"], sun=sun)


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("%s of an earlier plan could not be removed (%s)", path.name, exc)


def make_plan(cfg, settings: SurveySettings, *, online: bool | None = None, case_name: str = "") -> dict:
    """Plan the survey of *settings* for the case and write its folder. Returns what was written, per kind of survey."""
    from axqua import __version__
    from axqua.survey.factsheet import write_factsheet

    _check(settings)
    flow = load_field(cfg, settings.result)
    needs = case_needs(cfg, settings)
    notes: list[str] = []
    if not any(need.use and need.points > 0 for need in needs):
        needs = list(needs) + [ParameterNeed(name="flow field", rule="everywhere", points=max(int(settings.points_per_parameter), 1))]
        notes.append("no calibration parameter is selected: the measurements are spread over the wetted model")
    notes += [f"{need.name}: {need.note}" for need in needs if need.note]

    out_dir = folder(cfg)
    gpkg = out_dir / GPKG_NAME
    init(cfg)
    access = read_access(gpkg, cfg.crs_epsg)
    notes += access["notes"]
    found = conditions_module.look_up(cfg, flow, settings, online=online)
    sun = conditions_module.sun_of(found)

    plans: dict[str, dict] = {}
    skipped: dict[str, dict] = {}
    for kind in KINDS:
        if kind not in settings.kinds:
            continue
        log.info("planning: %s", KIND_TITLES[kind])
        try:
            plan = _plan_kind(kind, cfg, flow, settings, needs, access, sun)
        except SurveyError as exc:
            skipped[kind] = {"title": KIND_TITLES[kind], "reason": exc.message, "remedy": exc.remedy or ""}
            continue
        if not plan.get(_CONTENT[kind]):
            warnings = plan.get("warnings") or []
            skipped[kind] = {"title": KIND_TITLES[kind], "reason": "nothing could be placed" + (": " + "; ".join(warnings) if warnings else ""), "remedy": "Change the discharge of the steady run, the limits of the survey or the calibration parameters it serves."}
            continue
        plans[kind] = plan
    if not plans:
        reasons = "; ".join(f"{entry['title']}: {entry['reason']}" for entry in skipped.values())
        remedy = next((entry["remedy"] for entry in skipped.values() if entry["remedy"]), None)
        raise SurveyError(f"no survey could be planned ({reasons})", remedy=remedy)

    counts = write_layers(gpkg, cfg.crs_epsg, plans)
    made = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    name = str(case_name or "")
    context = {"settings": settings, "conditions": found, "case": name, "version": __version__, "made": made, "source": Path(flow.source).name, "time": flow.time, "gpkg": GPKG_NAME, "epsg": cfg.crs_epsg}
    files: dict[str, dict] = {}
    for kind in KINDS:
        map_png, sheet = out_dir / f"survey-map-{kind}.png", out_dir / f"survey-factsheet-{kind}.odt"
        if kind not in plans:                        # nothing of an earlier plan stays beside a plan it does not belong to
            for path in (map_png, map_png.with_suffix(".pdf"), sheet):
                _remove(path)
            continue
        try:
            draw_map(flow, kind, plans[kind], map_png, access=access, subtitle=", ".join(text for text in (name, found.get("date") or "") if text), epsg=cfg.crs_epsg)
            written = write_factsheet(sheet, kind, plans[kind], dict(context, map=map_png.with_suffix(".pdf").name))
        except OSError as exc:
            raise SurveyError(f"the map or the factsheet of the plan could not be written: {exc}", subject=str(out_dir), remedy="Close the factsheet and the map of the earlier plan in other programs, then plan again.") from exc
        files[kind] = {"map": str(map_png), "map_pdf": str(map_png.with_suffix(".pdf")), "factsheet": written["path"], "factsheet_level": written["level"]}
        if not written["fits"]:
            notes.append(f"{KIND_TITLES[kind]}: the factsheet may run onto a second page; shorten the survey or plan fewer working days")
        elif written["level"] > 0:
            notes.append(f"{KIND_TITLES[kind]}: the factsheet leaves out detail to stay on one page; {PLAN_JSON} and the layers of {GPKG_NAME} hold all of it")

    result = {
        "axqua": __version__,
        "made": made,
        "case": name,
        "folder": str(out_dir),
        "gpkg": str(gpkg),
        "layers": counts,
        "crs_epsg": cfg.crs_epsg,
        "result": flow.source,
        "result_time": flow.time,
        "settings": settings.as_dict(),
        "parameters": _needs_as_dicts(needs),
        "access": access,
        "conditions": found,
        "plans": plans,
        "skipped": skipped,
        "files": files,
        "notes": notes,
    }
    result = plain(result)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / PLAN_JSON).write_text(json.dumps(result, indent=1), encoding="utf-8")
    (out_dir / SETTINGS_JSON).write_text(json.dumps(plain(settings.as_dict()), indent=1), encoding="utf-8")
    result["json"] = str(out_dir / PLAN_JSON)
    return result


__all__ = ["FOLDER", "PLAN_JSON", "SETTINGS_JSON", "conditions", "folder", "gpkg_path", "ground_level", "info", "init", "make_plan", "plain", "saved_settings"]
