"""The Survey planner without its window: the form as a table, and the texts it shows.

The window (:mod:`..gui.survey_wizard`) decides nothing. Its forms are the tables of :data:`FORMS`, one row per setting of ``axqua survey``, keyed by the dotted name the setting has in the JSON document (``ground.max_depth``). What the library answers (``survey info``, ``survey conditions``, ``survey plan``) is turned into the lines the window shows by the functions below, which need no QGIS and are tested without one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

KINDS = ("ground", "airborne", "kayak", "temperature")

KIND_TITLES = {
    "ground": "Ground-based velocimetry (wading)",
    "airborne": "Airborne velocimetry (drone video)",
    "kayak": "Kayak drifter velocimetry",
    "temperature": "Water temperature",
}

#: The layers of ``survey-plan.gpkg`` a plan fills, in the order they are drawn (the last on top), with the kind of survey each belongs to and its name in the layer tree.
PLAN_LAYERS = (
    ("temperature_patches", "temperature", "Temperature: patches of water"),
    ("kayak_avoid", "kayak", "Kayak: water to avoid"),
    ("drone_footprints", "airborne", "Drone: filmed areas"),
    ("drone_route", "airborne", "Drone: route"),
    ("kayak_tracks", "kayak", "Kayak: drift lines"),
    ("walk_paths", "", "Walk paths"),
    ("drone_positions", "airborne", "Drone: hover positions"),
    ("temperature_points", "temperature", "Temperature: points"),
    ("survey_events", "", "Breaks and adjustments"),
    ("flowtracker_points", "ground", "Velocity verticals"),
)

#: What the table of the parameters calls a placement rule. The library's sentence is the tool tip; a rule this list does not know is shown with that sentence.
RULE_TITLES = {
    "zone": "inside a roughness zone",
    "everywhere": "over the wetted model",
    "shear": "in shear zones",
    "bends": "in bends",
    "banks": "along the banks",
    "margins": "in shallow margins",
}


def rule_title(key: str, description: str = "") -> str:
    return RULE_TITLES.get(key) or description or key


#: The layer of ``survey-plan.gpkg`` the user sets points in, and the roles a point can have.
ACCESS_LAYER = "access_points"
ROLES = ("access", "leave", "takeoff")
ACCESS_NAME = "Access points (survey plan)"
GROUP_NAME = "Survey plan"


@dataclass(frozen=True)
class Field:
    """One setting of the plan: its dotted key, what the form calls it, and how it is edited.

    ``kind`` is ``float``, ``int``, ``bool``, ``choice`` (``choices`` are ``(value, text)``), ``pairs`` (a list of two numbers each, written ``0.6: 2; 0.9: 1.4``) or ``numbers`` (a list of numbers, written ``0.2; 0.6; 0.8``).
    """

    key: str
    label: str
    kind: str = "float"
    unit: str = ""
    low: float = 0.0
    high: float = 1.0e6
    decimals: int = 2
    step: float = 0.1
    tip: str = ""
    choices: tuple = ()


@dataclass(frozen=True)
class Group:
    title: str
    fields: tuple
    note: str = ""


GROUND = (
    Group("Where a person can wade", (
        Field("ground.max_depth", "Deepest water", unit="m", high=3.0, tip="No vertical and no walk path lies in deeper water."),
        Field("ground.velocity_limits", "Largest velocity up to a depth", kind="pairs", unit="depth in m: velocity in m/s", tip="Up to the depth before the colon, water faster than the velocity after it is not waded. The default: up to 0.6 m at most 2 m/s, up to 0.9 m at most 1.4 m/s, up to 1.2 m at most 1 m/s."),
        Field("ground.min_depth", "Shallowest water for a reading", unit="m", high=1.0, tip="The probe needs this much water."),
    )),
    Group("Walking", (
        Field("ground.speed_land", "On land", unit="km/h", low=0.1, high=10.0, decimals=1),
        Field("ground.speed_with_current", "In the water, with the current", unit="km/h", low=0.05, high=10.0, decimals=1),
        Field("ground.speed_against_current", "In the water, against the current", kind="pairs", unit="deeper than m: km/h", tip="Against the current the speed falls with the depth. The default: deeper than 0.3 m 1 km/h, deeper than 0.5 m 0.5 km/h, deeper than 0.8 m 0.1 km/h. In shallower water the speed with the current applies."),
    )),
    Group("Time at a vertical", (
        Field("ground.time_per_depth", "Measuring at one depth", unit="s", low=1.0, high=3600.0, decimals=0, step=10.0, tip="At least 60 s for a mean velocity."),
        Field("ground.time_probe_height", "Setting the probe to a depth", unit="s", high=3600.0, decimals=0, step=10.0),
        Field("ground.time_water_depth", "Measuring the water depth", unit="s", high=3600.0, decimals=0, step=10.0),
    )),
    Group("Depths of a vertical", (
        Field("ground.depth_two_points", "Two depths from a water depth of", unit="m", high=5.0, tip="Shallower verticals are measured at one depth."),
        Field("ground.depth_three_points", "Three depths from a water depth of", unit="m", high=5.0),
        Field("ground.heights_one", "Height of the one reading", kind="numbers", unit="share of the water depth above the riverbed"),
        Field("ground.heights_two", "Heights of the two readings", kind="numbers", unit="shares of the water depth above the riverbed"),
        Field("ground.heights_three", "Heights of the three readings", kind="numbers", unit="shares of the water depth above the riverbed"),
    ), note="The three kinds of vertical get a marker of their own on the map: a circle for one depth, a triangle for two, a square for three."),
    Group("Pressure sensor of the probe", (
        Field("ground.pressure_interval_h", "Adjust to the atmosphere every", unit="h", low=0.25, high=24.0, decimals=1, step=0.5, tip="At the start, and again after this time. The plan shows where."),
        Field("ground.pressure_minutes", "One adjustment takes", unit="min", high=60.0, decimals=0, step=1.0),
    )),
    Group("Surveyors and DGPS", (
        Field("ground.surveyors", "Surveyors with a velocity probe", kind="int", low=1, high=12, tip="Every surveyor gets a walk path of their own."),
        Field("ground.surveyors_per_dgps", "Probes one DGPS surveyor serves", kind="int", low=1, high=6, tip="A second surveyor records every position with the DGPS rover while the probe measures. At most three probes per rover are recommended."),
        Field("ground.dgps_seconds", "Recording one position", unit="s", low=1.0, high=600.0, decimals=0, step=5.0, tip="With data reception. The DGPS limits the survey only where the corrections are not received."),
    ), note="With more than one probe the DGPS surveyor walks from vertical to vertical, and the plan draws that path too."),
)

AIRBORNE = (
    Group("Flight", (
        Field("airborne.altitude", "Height above the water", unit="m", low=5.0, high=500.0, decimals=0, step=5.0),
        Field("airborne.max_altitude", "Largest height allowed", unit="m", low=5.0, high=500.0, decimals=0, step=10.0, tip="120 m above the ground in the open category of the European Union."),
        Field("airborne.hover_seconds", "Video at one position", unit="s", low=10.0, high=3600.0, decimals=0, step=30.0),
        Field("airborne.overlap", "Overlap of neighboring frames", low=0.0, high=0.9, tip="As a share of the frame."),
        Field("airborne.cruise_speed", "Speed between the positions", unit="m/s", low=0.5, high=25.0, decimals=1),
        Field("airborne.battery_minutes", "Flight time of one battery", unit="min", low=1.0, high=120.0, decimals=0, step=1.0),
    )),
    Group("Camera", (
        Field("airborne.hfov_deg", "Horizontal field of view", unit="°", low=10.0, high=150.0, decimals=1, step=1.0, tip="Of the video mode, which is often narrower than that of a photograph."),
        Field("airborne.width_px", "Frame width", kind="int", unit="px", low=320, high=16000),
        Field("airborne.height_px", "Frame height", kind="int", unit="px", low=240, high=16000),
        Field("airborne.fps", "Frames per second", low=1.0, high=240.0, decimals=2, step=1.0),
        Field("airborne.tracer_size", "Size of a tracer", unit="m", low=0.001, high=1.0, decimals=3, step=0.005, tip="A tracer should cover at least two pixels."),
    )),
    Group("How much is filmed", (
        Field("airborne.stations_per_parameter", "Hover positions per calibration parameter", kind="int", low=1, high=50),
        Field("airborne.max_stations", "Most hover positions", kind="int", low=1, high=500),
    )),
)

KAYAK = (
    Group("Kayaks", (
        Field("kayak.kayakers", "Kayakers", kind="int", low=1, high=12, tip="Two are the default, for safety. One kayaker drifts down the reach twice."),
        Field("kayak.floats", "Drifts of every kayaker", kind="int", low=1, high=20),
        Field("kayak.shuttle_minutes", "Bringing a kayak back up", unit="min", high=600.0, decimals=0, step=5.0, tip="Between two drifts down the reach."),
    )),
    Group("Recording", (
        Field("kayak.log_interval", "One DGPS position every", unit="s", low=0.1, high=120.0, decimals=1, step=1.0),
    )),
    Group("Water a kayak drifts in", (
        Field("kayak.max_velocity", "Avoid water faster than", unit="m/s", low=0.2, high=10.0, decimals=1),
        Field("kayak.min_depth", "Shallowest water", unit="m", low=0.05, high=2.0),
    )),
    Group("Breaks", (
        Field("kayak.break_every_h", "A break every", unit="h", low=0.25, high=24.0, decimals=1, step=0.5),
        Field("kayak.break_minutes", "A break takes", unit="min", high=120.0, decimals=0, step=5.0),
    )),
)

TEMPERATURE = (
    Group("Instrument", (
        Field("temperature.method", "Method", kind="choice", choices=(("loggers", "fixed loggers that stay in the river"), ("probe", "a mobile probe carried from point to point"))),
        Field("temperature.loggers", "Loggers", kind="int", low=1, high=500),
        Field("temperature.install_minutes", "Fixing one logger", unit="min", high=240.0, decimals=0, step=1.0),
        Field("temperature.points", "Readings of a mobile probe", kind="int", low=1, high=2000),
        Field("temperature.reading_seconds", "One reading takes", unit="s", low=1.0, high=3600.0, decimals=0, step=10.0, tip="The time a probe needs to settle."),
    )),
    Group("Kinds of water", (
        Field("temperature.slow_velocity", "Slow is slower than", unit="m/s", low=0.01, high=5.0),
        Field("temperature.shallow_depth", "Shallow is shallower than", unit="m", low=0.01, high=5.0),
    ), note="The points are shared among slow and shallow, slow and deep, fast and shallow, and fast and deep water, because these patches warm and cool differently."),
)

TIME = (
    Group("Working day", (
        Field("time.day_hours", "Longest working day", unit="h", low=0.5, high=24.0, decimals=1, step=0.5),
        Field("time.use_daylight", "End before sunset when the day is known", kind="bool"),
    )),
    Group("Breaks", (
        Field("time.short_break_minutes", "Short break", unit="min", high=120.0, decimals=0, step=5.0),
        Field("time.short_break_every_h", "every", unit="h", low=0.25, high=24.0, decimals=1, step=0.5),
        Field("time.long_break_minutes", "Long break", unit="min", high=240.0, decimals=0, step=5.0),
        Field("time.long_break_every_h", "every", unit="h", low=0.25, high=24.0, decimals=1, step=0.5),
    )),
)

FORMS = {"ground": GROUND, "airborne": AIRBORNE, "kayak": KAYAK, "temperature": TEMPERATURE, "time": TIME}


def fields(form: str | None = None) -> list[Field]:
    """Every field of *form*, or of all forms."""
    groups = FORMS[form] if form else [group for groups in FORMS.values() for group in groups]
    return [item for group in groups for item in group.fields]


# -- the settings document ----------------------------------------------------------
def get(settings: dict, key: str, default=None):
    """The value at the dotted *key*."""
    node = settings
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def put(settings: dict, key: str, value) -> None:
    """Set the value at the dotted *key*, creating the blocks on the way."""
    node = settings
    parts = key.split(".")
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _number(text: str) -> float:
    return float(str(text).strip().replace(",", "."))


def number_text(value) -> str:
    return f"{float(value):g}"


def pairs_text(pairs) -> str:
    """``[[0.6, 2.0], [0.9, 1.4]]`` as ``0.6: 2; 0.9: 1.4``."""
    return "; ".join(f"{number_text(a)}: {number_text(b)}" for a, b in pairs or [])


def parse_pairs(text: str) -> list[list[float]]:
    """The reverse of :func:`pairs_text`. Raises ValueError with words a person can act on."""
    out = []
    for piece in str(text).split(";"):
        if not piece.strip():
            continue
        left, colon, right = piece.partition(":")
        if not colon:
            raise ValueError(f"'{piece.strip()}' lacks the colon between its two numbers")
        try:
            out.append([_number(left), _number(right)])
        except ValueError:
            raise ValueError(f"'{piece.strip()}' is not two numbers") from None
    if not out:
        raise ValueError("at least one pair of numbers is needed")
    return sorted(out)


def numbers_text(values) -> str:
    return "; ".join(number_text(v) for v in values or [])


def parse_numbers(text: str) -> list[float]:
    out = []
    for piece in str(text).replace(",", ".").split(";"):
        if not piece.strip():
            continue
        try:
            out.append(float(piece))
        except ValueError:
            raise ValueError(f"'{piece.strip()}' is not a number") from None
    if not out:
        raise ValueError("at least one number is needed")
    return out


def heights_problem(key: str, values: list[float]) -> str:
    """What is wrong with the reading heights of *key*, or an empty text."""
    wanted = {"ground.heights_one": 1, "ground.heights_two": 2, "ground.heights_three": 3}.get(key)
    if wanted is None:
        return ""
    if len(values) != wanted:
        return f"{wanted} height{'s' if wanted > 1 else ''} needed, {len(values)} given"
    if any(not 0.0 < v < 1.0 for v in values):
        return "a height is a share of the water depth, between 0 and 1"
    return ""


# -- the parameters -----------------------------------------------------------------
def parameter_rows(info: dict) -> list[dict]:
    """The rows of the parameter table: what the case declares, then what the user added."""
    rows = []
    for item in info.get("parameters") or []:
        rows.append({"name": str(item.get("name") or ""), "rule": str(item.get("rule") or "everywhere"), "zone": item.get("zone"), "points": int(item.get("points") or 0), "use": bool(item.get("use", True)), "declared": bool(item.get("declared")), "note": str(item.get("note") or "")})
    return rows


def parameter_setting(row: dict) -> dict:
    """A row of the table as the entry of ``parameters`` in the settings."""
    out = {"name": row["name"], "rule": row["rule"], "points": int(row["points"]), "use": bool(row["use"])}
    if row.get("zone") is not None and row["rule"] == "zone":
        out["zone"] = int(row["zone"])
    return out


# -- what the library answers, in lines ---------------------------------------------
def access_lines(access: dict, roles: dict | None = None) -> list[str]:
    """Which points of the layer ``access_points`` the plan will use."""
    access = access or {}
    names = access.get("names") or {}
    lines = []
    for role in ("access", "leave", "takeoff"):
        point = access.get(role)
        if point:
            name = f" \"{names[role]}\"" if names.get(role) else ""
            lines.append(f"{role}{name}: {float(point[0]):.1f}, {float(point[1]):.1f}")
    if not lines:
        lines.append("No point is set yet.")
    elif not access.get("access"):
        lines.append("A point with the role access is missing: the wading survey and the kayaks start there.")
    lines += [str(note) for note in access.get("notes") or []]
    return lines


def conditions_lines(answer: dict, *, days: int = 3) -> list[str]:
    """Daylight, weather and coverage as the lines of the tab *Time and weather*."""
    lines = []
    for day in (answer.get("days") or [])[:days]:
        line = f"{day.get('date', '')}: sunrise {day.get('sunrise_text', '')}, sunset {day.get('sunset_text', '')}"
        if day.get("weather"):
            line += f"; {day['weather']}"
            if day.get("t_min") is not None and day.get("t_max") is not None:
                line += f", {float(day['t_min']):.0f} to {float(day['t_max']):.0f} °C"
            if day.get("precipitation") is not None:
                line += f", {float(day['precipitation']):.1f} mm of rain"
            if day.get("gusts") is not None:
                line += f", gusts up to {float(day['gusts']):.0f} m/s"
        lines.append(line)
    if answer.get("timezone"):
        lines.append(f"Times are local times, {answer['timezone']}.")
    suggestion = answer.get("suggestion") or {}
    if suggestion.get("start"):
        lines.append(f"Suggested working day: {suggestion['start']} to {suggestion.get('end', '')}" + (f" ({suggestion['reason']})" if suggestion.get("reason") else "") + ".")
    elif suggestion.get("reason"):
        lines.append(f"No start is suggested: {suggestion['reason']}.")
    statement = (answer.get("coverage") or {}).get("statement")
    if statement:
        lines.append(str(statement))
    lines += [f"Note: {note}" for note in answer.get("notes") or []]
    lines += [str(source) for source in answer.get("sources") or []]
    return lines


def suggested_start(answer: dict) -> str:
    """The start the library suggests, ``HH:MM``, or an empty text."""
    return str((answer.get("suggestion") or {}).get("start") or "")


def _days(summary: dict) -> str:
    days = int(summary.get("days") or 1)
    return "1 day" if days == 1 else f"{days} days"


def _span(summary: dict) -> str:
    per_day = summary.get("per_day") or []
    if per_day:
        return f"{per_day[0].get('start', '')} to {per_day[-1].get('end', '')}" + (f" on day {len(per_day)}" if len(per_day) > 1 else "")
    return f"{summary.get('start', '')} to {summary.get('end', '')}"


def kind_line(kind: str, plan: dict) -> str:
    """One sentence on what a plan of *kind* holds."""
    s = plan.get("summary") or {}
    if kind == "ground":
        people = int(s.get("surveyors") or 1)
        return (f"{s.get('verticals', 0)} verticals ({s.get('one_depth', 0)} at one depth, {s.get('two_depths', 0)} at two, {s.get('three_depths', 0)} at three), "
                f"{people} surveyor{'s' if people > 1 else ''} with a probe, {float(s.get('walk_length') or 0) / 1000:.1f} km on foot, {_days(s)}, {_span(s)}")
    if kind == "airborne":
        batteries = int(s.get("batteries") or 1)
        return (f"{s.get('stations', 0)} hover positions at {float(s.get('altitude') or 0):.0f} m above the water, {float(s.get('flight_minutes') or 0):.0f} min in the air with "
                f"{batteries} batter{'ies' if batteries > 1 else 'y'}, {_span(s)}")
    if kind == "kayak":
        kayakers = int(s.get("kayakers") or 1)
        return (f"{s.get('floats', 0)} drifts of {float(s.get('length') or 0) / 1000:.2f} km by {kayakers} kayaker{'s' if kayakers > 1 else ''}, "
                f"about {s.get('fixes', 0)} positions, {_span(s)}")
    if kind == "temperature":
        what = "loggers" if s.get("method") == "loggers" else "readings of a mobile probe"
        return f"{s.get('points', 0)} {what}, {_days(s)}, {_span(s)}"
    return ""


def plan_lines(answer: dict) -> list[str]:
    """What a plan holds, kind by kind, with its warnings, what was not planned and the notes."""
    lines = []
    plans = answer.get("plans") or {}
    for kind in KINDS:
        if kind not in plans:
            continue
        lines.append(f"{KIND_TITLES[kind]}: {kind_line(kind, plans[kind])}.")
        lines += [f"    Warning: {text}" for text in plans[kind].get("warnings") or []]
    for entry in (answer.get("skipped") or {}).values():
        lines.append(f"Not planned: {entry.get('title', '')}: {entry.get('reason', '')}." + (f" {entry['remedy']}" if entry.get("remedy") else ""))
    lines += [f"Note: {text}" for text in answer.get("notes") or []]
    if answer.get("folder"):
        lines.append(f"Maps, factsheets and survey-plan.gpkg are in {answer['folder']}.")
    return lines


def plan_files(answer: dict) -> list[tuple[str, str, str]]:
    """``(kind, map, factsheet)`` of the kinds a plan wrote files for; the map is the PDF where there is one."""
    out = []
    files = answer.get("files") or {}
    for kind in KINDS:
        entry = files.get(kind)
        if not entry:
            continue
        out.append((kind, str(entry.get("map_pdf") or entry.get("map") or ""), str(entry.get("factsheet") or "")))
    return out


def layers_to_load(answer: dict) -> list[tuple[str, str]]:
    """``(layer, title)`` of the layers of the plan that hold something, in drawing order."""
    counts = answer.get("layers") or {}
    return [(name, title) for name, _kind, title in PLAN_LAYERS if int(counts.get(name) or 0) > 0]


def layer_uri(gpkg: str | Path, layer: str) -> str:
    return f"{gpkg}|layername={layer}"
