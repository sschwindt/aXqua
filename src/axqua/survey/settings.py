"""The assumptions of a survey plan, with the defaults a user can change.

Every number a plan rests on is a field here, so that the wizard of the QGIS plugin can show it, the factsheet can state it, and ``survey-settings.json`` can keep it with the plan. :meth:`SurveySettings.from_dict` reads what the plugin sends: unknown keys are ignored and missing ones keep their default, so that a plugin and a library of different versions understand each other.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

#: The kinds of survey a plan can hold, in the order they are reported.
KINDS = ("ground", "airborne", "kayak", "temperature")

KIND_TITLES = {
    "ground": "Ground-based velocimetry (wading)",
    "airborne": "Airborne velocimetry (drone video)",
    "kayak": "Kayak drifter velocimetry",
    "temperature": "Water temperature",
}

#: Where the measurements of a calibration parameter are placed.
RULES = {
    "zone": "inside one roughness zone, spread over its depths and velocities",
    "everywhere": "spread over the wetted model",
    "shear": "where the velocity changes most across the flow (shear zones, edges of recirculation)",
    "bends": "where the streamlines curve most (bends)",
    "banks": "along the banks, close to the wetted edge",
    "margins": "in the shallow margins that wet and dry",
}


@dataclass
class GroundSettings:
    """Point velocimetry by wading."""

    # wadeability: no wading deeper than max_depth; (depth up to [m], largest velocity [m/s])
    max_depth: float = 1.2
    velocity_limits: list = field(default_factory=lambda: [[0.6, 2.0], [0.9, 1.4], [1.2, 1.0]])
    # walking [km/h]; against the current: (deeper than [m], speed)
    speed_land: float = 3.0
    speed_with_current: float = 1.5
    speed_against_current: list = field(default_factory=lambda: [[0.3, 1.0], [0.5, 0.5], [0.8, 0.1]])
    # durations at a vertical [s]
    time_per_depth: float = 60.0
    time_probe_height: float = 40.0
    time_water_depth: float = 60.0
    # the pressure sensor of the probe is adjusted to the atmosphere
    pressure_interval_h: float = 4.0
    pressure_minutes: float = 5.0
    # how many depths a vertical is measured at
    min_depth: float = 0.05
    depth_two_points: float = 0.3
    depth_three_points: float = 0.5
    # heights of the readings, as shares of the water depth above the riverbed
    heights_one: list = field(default_factory=lambda: [0.6])
    heights_two: list = field(default_factory=lambda: [0.2, 0.8])
    heights_three: list = field(default_factory=lambda: [0.2, 0.6, 0.8])
    surveyors: int = 1
    surveyors_per_dgps: int = 3
    dgps_seconds: float = 30.0          # recording one position, with data reception

    def depths_at(self, depth: float) -> int:
        """How many depths a vertical of water depth *depth* is measured at."""
        if depth >= self.depth_three_points:
            return 3
        return 2 if depth >= self.depth_two_points else 1

    def heights(self, n: int) -> list[float]:
        return list({1: self.heights_one, 2: self.heights_two}.get(n, self.heights_three))

    def vertical_seconds(self, n: int) -> float:
        """The time one vertical with *n* depths takes."""
        return n * (self.time_per_depth + self.time_probe_height) + self.time_water_depth

    def wadeable(self, depth, velocity):
        """Whether water of *depth* and *velocity* (arrays) can be waded."""
        import numpy as np

        depth = np.asarray(depth, dtype=float)
        velocity = np.asarray(velocity, dtype=float)
        limit = np.zeros_like(depth)
        lower = -np.inf
        for upto, vmax in sorted(self.velocity_limits):
            limit = np.where((depth > lower) & (depth <= upto), float(vmax), limit)
            lower = upto
        return (depth <= self.max_depth) & (velocity <= limit)


@dataclass
class AirborneSettings:
    """Surface velocimetry from the video of a hovering drone, evaluated with pXv."""

    altitude: float = 40.0              # above the water surface [m]
    max_altitude: float = 120.0
    hfov_deg: float = 70.0              # horizontal field of view of the video
    width_px: int = 3840
    height_px: int = 2160
    fps: float = 30.0
    hover_seconds: float = 180.0
    overlap: float = 0.2
    cruise_speed: float = 5.0           # between the stations [m/s]
    battery_minutes: float = 25.0       # usable flight time of one battery
    stations_per_parameter: int = 3     # hover stations in the water of every parameter
    max_stations: int = 20
    tracer_size: float = 0.035          # [m], the corn tracers of the Isar and Inn surveys


@dataclass
class KayakSettings:
    """Drifting kayaks that carry a DGPS rover, after Barker et al. (2018)."""

    kayakers: int = 2
    floats: int = 1                     # how often every kayaker drifts down the reach
    log_interval: float = 5.0           # one DGPS position every [s]
    max_velocity: float = 3.0           # faster patches are avoided [m/s]
    min_depth: float = 0.2              # a kayak with its paddler floats [m]
    break_every_h: float = 2.0
    break_minutes: float = 10.0
    shuttle_minutes: float = 30.0       # carrying the kayak back for another float


@dataclass
class TemperatureSettings:
    """Fixed loggers or a mobile probe, by patches of slow, shallow and fast, deep water."""

    method: str = "loggers"             # loggers | probe
    loggers: int = 8
    points: int = 12                    # readings of a mobile probe
    slow_velocity: float = 0.2          # slower counts as slow [m/s]
    shallow_depth: float = 0.3          # shallower counts as shallow [m]
    reading_seconds: float = 60.0       # a probe needs this to settle
    install_minutes: float = 10.0       # fixing one logger


@dataclass
class TimeSettings:
    """The working day."""

    date: str = ""                      # YYYY-MM-DD; empty: no calendar day
    start: str = ""                     # HH:MM; empty: 00:00
    day_hours: float = 8.0
    use_daylight: bool = True           # do not work past sunset when the day is known
    short_break_minutes: float = 10.0
    short_break_every_h: float = 2.0
    long_break_minutes: float = 30.0
    long_break_every_h: float = 4.0


@dataclass
class ParameterNeed:
    """A calibration parameter and where its measurements are placed."""

    name: str
    rule: str = "everywhere"
    zone: int | None = None
    points: int = 6
    use: bool = True
    declared: bool = False              # of the case: a calibration parameter it declares, or a zone of its roughness zone map
    note: str = ""
    limits: list | None = None          # (smallest, largest) value of the calibration range


@dataclass
class SurveySettings:
    kinds: list = field(default_factory=lambda: ["ground"])
    parameters: list = field(default_factory=list)      # ParameterNeed as dicts
    points_per_parameter: int = 6
    min_spacing: float = 0.0            # between two points [m]; 0: two mesh edges
    result: str = ""                    # a result file other than the steady run's
    online: bool = True                 # look up weather, sun and network coverage
    ground: GroundSettings = field(default_factory=GroundSettings)
    airborne: AirborneSettings = field(default_factory=AirborneSettings)
    kayak: KayakSettings = field(default_factory=KayakSettings)
    temperature: TemperatureSettings = field(default_factory=TemperatureSettings)
    time: TimeSettings = field(default_factory=TimeSettings)

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "SurveySettings":
        return _fill(cls(), data or {})

    def needs(self) -> list[ParameterNeed]:
        return [_fill(ParameterNeed(name=str(p.get("name", ""))), p)
                for p in self.parameters if isinstance(p, dict) and p.get("name")]

    def problems(self) -> list[str]:
        """What is wrong with the numbers, in words; empty when they can be planned with."""
        out = []
        unknown = [k for k in self.kinds if k not in KINDS]
        if unknown:
            out.append(f"unknown kind of survey: {', '.join(map(str, unknown))} (one of {', '.join(KINDS)})")
        if not self.kinds:
            out.append("no kind of survey is chosen")
        g = self.ground
        if g.surveyors < 1:
            out.append("a ground-based survey needs at least one surveyor")
        if not g.depth_two_points <= g.depth_three_points:
            out.append("the depth from which three depths are measured lies below the one for two depths")
        for value, text in ((g.speed_land, "the walking speed on land"), (g.speed_with_current, "the walking speed with the current"), (self.time.day_hours, "the working day"), (self.airborne.altitude, "the flight altitude"), (self.airborne.hover_seconds, "the hover time"), (self.kayak.log_interval, "the DGPS interval of the kayak")):
            if not value > 0:
                out.append(f"{text} must be larger than zero")
        if any(float(speed) <= 0 for _, speed in g.speed_against_current):
            out.append("the walking speeds against the current must be larger than zero")
        if self.kayak.kayakers < 1:
            out.append("a kayak survey needs at least one kayaker")
        if self.temperature.method not in ("loggers", "probe"):
            out.append("the temperature method is 'loggers' or 'probe'")
        for text in (self.time.date,):
            if text and _date(text) is None:
                out.append(f"the date {text!r} is not of the form YYYY-MM-DD")
        if self.time.start and clock_minutes(self.time.start) is None:
            out.append(f"the start time {self.time.start!r} is not of the form HH:MM")
        return out


def _date(text: str):
    import datetime

    try:
        return datetime.date.fromisoformat(str(text).strip())
    except ValueError:
        return None


def clock_minutes(text: str) -> float | None:
    """``"07:30"`` or ``"07h30m"`` as minutes after midnight, or None."""
    import re

    match = re.fullmatch(r"\s*(\d{1,2})\s*[:h]\s*(\d{2})\s*m?\s*", str(text))
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    return hours * 60.0 + minutes if hours < 24 and minutes < 60 else None


def _fill(target, data: dict):
    """Copy the keys of *data* that *target* (a dataclass) has, keeping the type of each default."""
    for item in dataclasses.fields(target):
        if item.name not in data or data[item.name] is None:
            continue
        value, current = data[item.name], getattr(target, item.name)
        try:
            if dataclasses.is_dataclass(current):
                if isinstance(value, dict):
                    _fill(current, value)
            elif isinstance(current, bool):
                setattr(target, item.name, value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on"))
            elif isinstance(current, int):
                setattr(target, item.name, int(float(value)))
            elif isinstance(current, float):
                setattr(target, item.name, float(value))
            elif isinstance(current, str):
                setattr(target, item.name, str(value))
            elif isinstance(current, list):
                if isinstance(value, (list, tuple)):
                    setattr(target, item.name, [list(v) if isinstance(v, tuple) else v for v in value])
            elif item.name == "zone":
                setattr(target, item.name, None if value in (None, "") else int(float(value)))
            elif isinstance(value, (list, tuple)):
                setattr(target, item.name, list(value))
        except (TypeError, ValueError):
            continue                               # an unreadable value keeps the default
    return target


__all__ = ["AirborneSettings", "GroundSettings", "KINDS", "KIND_TITLES", "KayakSettings", "ParameterNeed", "RULES", "SurveySettings", "TemperatureSettings", "TimeSettings", "clock_minutes"]
