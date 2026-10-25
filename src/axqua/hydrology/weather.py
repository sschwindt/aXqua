"""Wind and sun: the weather a hydrology case is driven with besides rain.

**Wind** goes to TELEMAC as it is (``WIND : YES``, ``OPTION FOR WIND : 2``): a time
series of the two components at 10 m in the ``ASCII ATMOSPHERIC DATA FILE``. Weather
services give speed and the direction the wind comes *from*, clockwise from north; the
components are ``u = -s sin(d)`` (east) and ``v = -s cos(d)`` (north), computed here so
that no convention of the solver has to be trusted. TELEMAC applies wind stress only
where the water is deeper than ``THRESHOLD DEPTH FOR WIND`` (1 m by default), which is
right for sheet flow on hillslopes: wind matters on ponds and lakes in the catchment.

**Sun** enters as evaporation. TELEMAC-2D removes no water for radiation (its heat
exchange in WAQTEL changes the temperature only), so aXqua computes an evaporation rate
and its runoff routine takes it from the surface water left after infiltration. The
rate is the FAO-56 reference evapotranspiration ``ET0`` (Allen et al., 1998) times
``open_water_factor`` (1.05, the crop coefficient FAO-56 gives for open water less than
2 m deep). ``ET0`` comes from the weather table (``et0``, as Open-Meteo provides it) or
is computed hourly from shortwave radiation, air temperature, relative humidity and
wind with the Penman-Monteith equation (FAO-56 eq. 53). Water in the soil does not
evaporate in this model: Green-Ampt keeps no soil water budget that could give it up.

The forcing tables written here are what the generated routine and TELEMAC read:
``forcing.txt`` (rain and evaporation in mm/h, piecewise constant) and
``atmosphere.txt`` (wind components, interpolated by TELEMAC between rows).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from axqua.hydrology.rainfall import (RainSeries, _column, _count, _data_rows,
                                     _no_measurement, _read_table, _seconds)

#: Evaporation sources of the ``hydrology.evaporation`` setting.
EVAPORATION_MODES = ("none", "et0", "penman", "series")

#: Weather variables a table may carry, and the words that find them in a header.
VARIABLES = {
    "wind_speed_10m": ("wind_speed", "windspeed", "wind speed", "ff", "u10"),
    "wind_direction_10m": ("wind_direction", "winddirection", "wind direction", "dd",
                           "dir"),
    "shortwave_radiation": ("shortwave", "radiation", "globalstrahlung", "rs", "ghi"),
    "temperature": ("temperature", "temp", "tair", "t2m", "tt"),
    "relative_humidity": ("relative_humidity", "humidity", "rh", "rf"),
    "et0": ("et0", "evapotranspiration", "reference_et"),
    "evaporation": ("evaporation", "evap", "verdunstung"),
}


@dataclass
class WeatherSeries:
    """Weather as intervals, in seconds from the model's time zero.

    Every variable is held constant over its interval, like rain. Units: wind m/s and
    degrees, radiation W/m2 (mean over the interval), temperature deg C, humidity %,
    ``et0`` and ``evaporation`` mm/h.
    """

    start_s: np.ndarray
    end_s: np.ndarray
    values: dict[str, np.ndarray]
    origin: pd.Timestamp | None = None
    notes: list[str] = field(default_factory=list)
    #: readings the table had no value for, by variable; interpolated (:func:`fill_gaps`)
    gaps: dict[str, int] = field(default_factory=dict)

    def has(self, name: str) -> bool:
        return name in self.values and np.isfinite(self.values[name]).any()

    def to_frame(self) -> pd.DataFrame:
        frame = pd.DataFrame({"start_s": self.start_s, "end_s": self.end_s})
        for name, values in self.values.items():
            frame[name] = values
        return frame

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.to_frame().to_csv(path, index=False, float_format="%.6g")
        return path


def fill_gaps(times, values, *, circular: bool = False) -> tuple[np.ndarray, int]:
    """*values* with each missing one (``NaN``) interpolated in time, and their number.

    Between two readings the value is interpolated linearly, before the first and after
    the last the nearest reading holds. A direction in degrees (*circular*) is
    interpolated as a unit vector, so that 350 and 10 give 0 and not 180. A variable
    without any reading stays empty and counts no gap: the series does not have it.
    """
    times = np.asarray(times, dtype=float)
    values = np.asarray(values, dtype=float)
    ok = np.isfinite(values)
    if ok.all() or not ok.any():
        return values, 0
    if circular:
        angle = np.radians(values[ok])
        east = np.interp(times, times[ok], np.sin(angle))
        north = np.interp(times, times[ok], np.cos(angle))
        between = np.round(np.degrees(np.arctan2(east, north)), 9) % 360.0
    else:
        between = np.interp(times, times[ok], values[ok])
    return np.where(ok, values, between), int((~ok).sum())


def read_weather(path: str | Path, *, stamp: str = "end",
                 origin: pd.Timestamp | None = None) -> WeatherSeries:
    """Read a weather table: ``start_s, end_s`` and named columns, or a time column.

    Columns are found by the words in :data:`VARIABLES`. Rows that are no data, a
    units row or a sum, are left out as for rain, and a row with a value but no
    readable time stops the import. A reading without a value (empty, or -999 as the
    DWD writes it) is interpolated between the readings around it (:func:`fill_gaps`):
    wind, sun and air change gradually, and -999 would otherwise reach the solver as a
    temperature or a wind speed. With dates in the table and an *origin* (the time zero
    of the rain), the weather is put on the rain's clock.
    """
    path = Path(path)
    frame = _read_table(path)
    cols = {str(c).lower().strip(): c for c in frame.columns}
    canonical = "start_s" in cols and "end_s" in cols
    if canonical:
        tcol = None
        skip = (cols["start_s"], cols["end_s"])
    else:
        tcol = _column(frame, "time", "date", "datum", "zeit")
        if tcol is None:
            raise ValueError(f"{path.name}: no time column (time, date, datum or zeit)")
        skip = (tcol,)
    found = {}
    for name, words in VARIABLES.items():
        exact = cols.get(name)
        col = exact if exact is not None else _column(frame, *words, exclude=skip)
        if col is not None and col not in skip:
            found[name] = col
    notes = []
    first = None
    if canonical:
        starts = pd.to_numeric(frame[cols["start_s"]]).to_numpy(dtype=float)
        ends = pd.to_numeric(frame[cols["end_s"]]).to_numpy(dtype=float)
    else:
        frame, notes = _data_rows(frame, tcol, list(found.values()), path.name)
        seconds, first = _seconds(frame[tcol])
        if seconds.size < 2:
            raise ValueError(f"{path.name}: at least two rows are needed")
        steps = np.diff(seconds)
        if np.any(steps <= 0):
            raise ValueError(f"{path.name}: the times do not increase")
        if stamp == "end":
            ends = seconds
            starts = np.concatenate([[seconds[0] - steps[0]], seconds[:-1]])
        else:
            starts = seconds
            ends = np.concatenate([seconds[1:], [seconds[-1] + steps[-1]]])
    values, gaps = {}, {}
    middle = (starts + ends) / 2.0
    for name, col in found.items():
        values[name], missing = fill_gaps(middle, _no_measurement(frame[col]),
                                          circular=name == "wind_direction_10m")
        if missing:
            gaps[name] = missing
    shift = 0.0
    start_time = None
    if first is not None:
        # the calendar time of the start of the first interval
        start_time = first + pd.Timedelta(seconds=float(starts[0] - (
            ends[0] if stamp == "end" else starts[0])))
        if origin is not None:
            shift = (start_time - origin).total_seconds()
    zero = starts[0]
    series = WeatherSeries(starts - zero + shift, ends - zero + shift, values,
                           origin=origin if origin is not None else start_time,
                           gaps=gaps)
    series.notes.append(f"read {path.name}: {', '.join(sorted(values)) or 'no columns'}")
    series.notes.extend(notes)
    for name, missing in gaps.items():
        series.notes.append(f"{_count(missing, 'reading')} of {name} without a value, "
                            "interpolated between the readings around them")
    return series


# --------------------------------------------------------------------------- #
# wind
# --------------------------------------------------------------------------- #


def wind_components(speed, direction_from):
    """East and north components [m/s] of a wind blowing *from* the given direction."""
    s = np.asarray(speed, dtype=float)
    d = np.radians(np.asarray(direction_from, dtype=float))
    return -s * np.sin(d), -s * np.cos(d)


def wind_at_2m(speed_10m):
    """FAO-56 eq. 47: wind speed at 2 m from a measurement at 10 m."""
    return np.asarray(speed_10m, dtype=float) * 4.87 / math.log(67.8 * 10.0 - 5.42)


# --------------------------------------------------------------------------- #
# evaporation (FAO-56)
# --------------------------------------------------------------------------- #


def saturation_vapour_pressure(t_c):
    """kPa, FAO-56 eq. 11."""
    t_c = np.asarray(t_c, dtype=float)
    return 0.6108 * np.exp(17.27 * t_c / (t_c + 237.3))


def extraterrestrial_radiation_hourly(when: pd.DatetimeIndex, latitude: float,
                                      longitude: float, hours: float = 1.0):
    """Ra [MJ m-2 per period] for periods that END at *when* (UTC), FAO-56 eq. 28.

    *longitude* in degrees east; the solar time follows from it directly, since the
    times are UTC (FAO-56 eq. 31 with Lz = 0).
    """
    when = pd.DatetimeIndex(when)
    doy = when.dayofyear.to_numpy(dtype=float)
    mid_hour = (when.hour + when.minute / 60.0).to_numpy(dtype=float) - hours / 2.0
    phi = math.radians(latitude)
    dr = 1.0 + 0.033 * np.cos(2.0 * np.pi / 365.0 * doy)
    delta = 0.409 * np.sin(2.0 * np.pi / 365.0 * doy - 1.39)
    b = 2.0 * np.pi * (doy - 81.0) / 364.0
    sc = 0.1645 * np.sin(2 * b) - 0.1255 * np.cos(b) - 0.025 * np.sin(b)
    # FAO-56 eq. 31: Lz is the longitude of the time zone's centre in degrees WEST of
    # Greenwich and Lm that of the site; for UTC, Lz = 0 and Lm = -longitude
    omega = np.pi / 12.0 * ((mid_hour + 0.06667 * (0.0 + longitude) + sc) - 12.0)
    w1 = omega - np.pi * hours / 24.0
    w2 = omega + np.pi * hours / 24.0
    ws = np.arccos(np.clip(-math.tan(phi) * np.tan(delta), -1.0, 1.0))
    w1 = np.clip(w1, -ws, ws)
    w2 = np.clip(w2, -ws, ws)
    ra = (12.0 * 60.0 / np.pi * 0.0820 * dr
          * ((w2 - w1) * math.sin(phi) * np.sin(delta)
             + math.cos(phi) * np.cos(delta) * (np.sin(w2) - np.sin(w1))))
    return np.maximum(ra, 0.0)


def penman_monteith_hourly(*, radiation_w_m2, temperature_c, humidity_pct,
                           wind_2m, ra_mj, elevation: float = 0.0,
                           hours: float = 1.0) -> np.ndarray:
    """FAO-56 hourly reference evapotranspiration ET0 [mm per period], eq. 53.

    *radiation_w_m2* is the mean shortwave radiation over the period, *ra_mj* the
    extraterrestrial radiation of the period [MJ m-2]. Night-time net longwave uses
    the cloudiness of the last daylight period, as FAO-56 recommends.
    """
    rs = np.asarray(radiation_w_m2, dtype=float) * 3600.0 * hours / 1.0e6  # MJ m-2
    t = np.asarray(temperature_c, dtype=float)
    rh = np.clip(np.asarray(humidity_pct, dtype=float), 0.0, 100.0)
    u2 = np.maximum(np.asarray(wind_2m, dtype=float), 0.0)
    ra = np.asarray(ra_mj, dtype=float)
    pressure = 101.3 * ((293.0 - 0.0065 * elevation) / 293.0) ** 5.26     # eq. 7
    gamma = 0.665e-3 * pressure                                            # eq. 8
    es = saturation_vapour_pressure(t)
    ea = es * rh / 100.0
    slope = 4098.0 * es / (t + 237.3) ** 2                                 # eq. 13
    rso = (0.75 + 2.0e-5 * elevation) * ra                                 # eq. 37
    day = rso > 0.05 * max(float(np.nanmax(rso)) if rso.size else 0.0, 1e-9)
    ratio = np.full(rs.shape, np.nan)
    ratio[day] = np.clip(rs[day] / rso[day], 0.25, 1.0)
    last = 0.8                                  # before the first daylight period
    for k in range(ratio.size):                 # night: carry the last daylight ratio
        if np.isnan(ratio[k]):
            ratio[k] = last
        else:
            last = ratio[k]
    sigma = 4.903e-9 / 24.0 * hours                                        # per period
    rnl = (sigma * (t + 273.16) ** 4 * (0.34 - 0.14 * np.sqrt(np.maximum(ea, 0.0)))
           * (1.35 * ratio - 0.35))                                        # eq. 39
    rn = (1.0 - 0.23) * rs - rnl
    g = np.where(day, 0.1 * rn, 0.5 * rn)                                  # eq. 45, 46
    et0 = ((0.408 * slope * (rn - g) + gamma * 37.0 / (t + 273.0) * u2 * (es - ea))
           / (slope + gamma * (1.0 + 0.34 * u2)))
    return np.maximum(et0, 0.0)


def evaporation_rate(weather: WeatherSeries | None, mode: str, *,
                     factor: float = 1.05, latitude: float | None = None,
                     longitude: float | None = None,
                     elevation: float = 0.0) -> np.ndarray | None:
    """Evaporation of open water [mm/h] per weather interval, or None for ``none``."""
    if mode not in EVAPORATION_MODES:
        raise ValueError(f"hydrology.evaporation must be one of {EVAPORATION_MODES}, "
                         f"got {mode!r}")
    if mode == "none":
        return None
    if weather is None:
        raise ValueError(f"evaporation '{mode}' needs hydrology.weather")
    if mode == "series":
        if not weather.has("evaporation"):
            raise ValueError("evaporation 'series' needs an evaporation column (mm/h)")
        return np.nan_to_num(weather.values["evaporation"], nan=0.0).clip(min=0.0)
    if mode == "et0":
        if not weather.has("et0"):
            raise ValueError("evaporation 'et0' needs an et0 column (mm/h)")
        return factor * np.nan_to_num(weather.values["et0"], nan=0.0).clip(min=0.0)
    missing = [name for name in ("shortwave_radiation", "temperature",
                                 "relative_humidity", "wind_speed_10m")
               if not weather.has(name)]
    if missing:
        raise ValueError(f"evaporation 'penman' needs the weather columns {missing}")
    if weather.origin is None or latitude is None or longitude is None:
        raise ValueError("evaporation 'penman' needs dated weather and the location "
                         "of the catchment (for the radiation of a clear sky)")
    hours = (weather.end_s - weather.start_s) / 3600.0
    if np.any(np.abs(hours - hours[0]) > 1e-6):
        raise ValueError("evaporation 'penman' needs weather at a constant step")
    ends = weather.origin + pd.to_timedelta(weather.end_s, unit="s")
    ra = extraterrestrial_radiation_hourly(ends, latitude, longitude, hours=hours[0])
    et0 = penman_monteith_hourly(
        radiation_w_m2=weather.values["shortwave_radiation"],
        temperature_c=weather.values["temperature"],
        humidity_pct=weather.values["relative_humidity"],
        wind_2m=wind_at_2m(weather.values["wind_speed_10m"]), ra_mj=ra,
        elevation=elevation, hours=hours[0])
    return factor * et0 / hours[0]


# --------------------------------------------------------------------------- #
# the files TELEMAC reads
# --------------------------------------------------------------------------- #


def forcing_table(rain: RainSeries, weather: WeatherSeries | None = None,
                  evaporation_mm_h: np.ndarray | None = None,
                  *, until: float | None = None) -> pd.DataFrame:
    """Rain and evaporation as one table of piecewise constant intervals.

    The rows start at every time either series changes; the last row ends the table
    with zeros at *until* (the end of the run; else the end of the longer series), so
    the routine holds no rain and no evaporation after it.
    """
    edges = set(rain.start_s.tolist()) | set(rain.end_s.tolist())
    if weather is not None and evaporation_mm_h is not None:
        edges |= set(weather.start_s.tolist()) | set(weather.end_s.tolist())
    end = max(edges)
    if until is not None:
        end = float(until)
        edges = {t for t in edges if t < end} | {end}
    times = np.array(sorted(t for t in edges if t >= 0.0 and t <= end))
    if times.size == 0 or times[0] > 0.0:
        times = np.concatenate([[0.0], times])
    rows = []
    for k, t in enumerate(times):
        if k == times.size - 1:
            rows.append((t, 0.0, 0.0))
            break
        mid = 0.5 * (t + times[k + 1])
        r = rain.rate_at(mid)
        e = 0.0
        if weather is not None and evaporation_mm_h is not None:
            j = np.searchsorted(weather.start_s, mid, side="right") - 1
            if 0 <= j < weather.end_s.size and mid < weather.end_s[j]:
                e = float(evaporation_mm_h[j])
        rows.append((t, r, e))
    return pd.DataFrame(rows, columns=["time_s", "rain_mm_h", "evaporation_mm_h"])


def write_forcing(path: str | Path, table: pd.DataFrame) -> Path:
    """``forcing.txt`` for the generated runoff routine (FORMATTED DATA FILE 1).

    Two comment lines, the number of rows, then ``time rain evaporation`` per row:
    the routine reads exactly that, so the layout is fixed.
    """
    path = Path(path)
    lines = ["# axqua forcing of runoff_greenampt.f (piecewise constant from each row)",
             "# time [s]  rain [mm/h]  evaporation [mm/h]",
             str(len(table))]
    for t, r, e in table[["time_s", "rain_mm_h", "evaporation_mm_h"]].itertuples(
            index=False):
        lines.append(f"{t:.3f} {r:.6g} {e:.6g}")
    path.write_text("\n".join(lines) + "\n")
    return path


def write_atmosphere(path: str | Path, weather: WeatherSeries) -> Path:
    """``atmosphere.txt``: wind components at 10 m for TELEMAC's meteo module.

    Written at the middle of each interval, since TELEMAC interpolates linearly between
    rows; the first and the last rows hold the values out to the ends of the series.
    """
    if not (weather.has("wind_speed_10m") and weather.has("wind_direction_10m")):
        raise ValueError("wind needs the columns wind_speed_10m and wind_direction_10m")
    u, v = wind_components(np.nan_to_num(weather.values["wind_speed_10m"]),
                           np.nan_to_num(weather.values["wind_direction_10m"]))
    mids = 0.5 * (weather.start_s + weather.end_s)
    times = np.concatenate([[min(0.0, weather.start_s[0])], mids, [weather.end_s[-1]]])
    u = np.concatenate([[u[0]], u, [u[-1]]])
    v = np.concatenate([[v[0]], v, [v[-1]]])
    keep = np.concatenate([[True], np.diff(times) > 0])
    lines = ["#REFDATE 1900-01-01 00:00:00", "T WINDX WINDY", "s m/s m/s"]
    for t, a, b in zip(times[keep], u[keep], v[keep]):
        lines.append(f"{t:.3f} {a:.4f} {b:.4f}")
    path = Path(path)
    path.write_text("\n".join(lines) + "\n")
    return path
