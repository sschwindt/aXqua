"""Rain series: reading measured records and building design storms.

A rain series is a sequence of intervals with a constant intensity, which is how rain gauges, radar products and design storms all report it, and how TELEMAC applies it (the generated routine holds each intensity over its interval). The one thing a series must say, and a plain table often does not, is whether the time stamp of a row is the **start** or the **end** of its interval: a gauge that reports the rain of the past hour at 13:00 means 12:00 to 13:00. Agencies (DWD station hours, RADOLAN, Open-Meteo) stamp the end, so ``end`` is the default; the files aXqua writes itself carry both ends (``start_s, end_s, rain_mm_h``), so nothing has to be assumed for them.

Design storms are built from a depth-duration table of one return period, which is what KOSTRA-DWD (Germany), NOAA Atlas 14 (USA) and comparable national products publish:

``block``
    one intensity for the whole duration;
``alternating``
    the alternating block method of Chow, Maidment and Mays (1988): the increments of the depth-duration curve, largest in the middle, the others alternately right and left of it;
``euler2``
    Euler type II, the German standard (DWA-A 118): the largest increment at 30 % of the duration, the following ones in decreasing order after it, and the smallest ones in increasing order before it. Every window that starts at the peak holds the depth of the table for its duration, which is the property the tests check.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

#: Design-storm distributions.
DISTRIBUTIONS = ("euler2", "alternating", "block")

#: Column names of the series aXqua writes.
CANONICAL = ("start_s", "end_s", "rain_mm_h")

#: Rain columns of the DWD Climate Data Center by their exact names: the stamp of their ``MESS_DATUM``, the period a value covers in seconds (``None`` for the day, :func:`dwd_day_windows`), and that period in words. Each is a depth in mm (the DWD's descriptions of the hourly, 10-minute and daily products), so the units and stamp settings do not apply to them. Matched before the words of :func:`read_series`, because the other columns of those files (``QN_8``, ``RS_IND``, ``WRTR``, ``eor``) hold quality flags and indicators.
AGENCY_COLUMNS = {"r1": ("end", 3600.0, "the hour before MESS_DATUM"),
                  "rws_10": ("end", 600.0, "the 10 minutes before MESS_DATUM"),
                  "rs": ("start", None, "the DWD's precipitation day, from 05:50 UTC of "
                                        "the date MESS_DATUM names to 05:50 UTC of the "
                                        "next")}

#: Since this date the DWD's precipitation day runs from 05:50 UTC to 05:50 UTC.
DWD_UTC_DAYS_FROM = pd.Timestamp("2001-04-01")

#: Values agencies write for "no measurement" (DWD: -999).
MISSING_AT_OR_BELOW = -999.0

#: Dates written as one integer, by number of digits (DWD ``MESS_DATUM``).
_INTEGER_DATES = {8: "%Y%m%d", 10: "%Y%m%d%H", 12: "%Y%m%d%H%M"}

#: How the first line of TELEMAC's hyetograph file begins (``#HYETOGRAPH FILE``); a comment that only names a hyetograph (``# Hyetograph, station 3668``) is no such file.
TELEMAC_HYETOGRAPH = "#HYETOGRAPH"


@dataclass
class RainSeries:
    """Rain as intervals of constant intensity, in seconds from the model's time zero."""

    start_s: np.ndarray
    end_s: np.ndarray
    intensity_mm_h: np.ndarray
    origin: pd.Timestamp | None = None        # calendar time of t = 0, when known
    notes: list[str] = field(default_factory=list)
    #: intervals the record had no value for; simulated as dry, written empty
    missing: np.ndarray | None = None

    def __post_init__(self) -> None:
        self.start_s = np.asarray(self.start_s, dtype=float)
        self.end_s = np.asarray(self.end_s, dtype=float)
        self.intensity_mm_h = np.asarray(self.intensity_mm_h, dtype=float)
        self.missing = (np.zeros(self.start_s.shape, bool) if self.missing is None
                        else np.asarray(self.missing, dtype=bool))

    @property
    def gaps(self) -> int:
        """Number of intervals without a value."""
        return int(self.missing.sum())

    @property
    def gap_hours(self) -> float:
        """Their length [h]."""
        return float(((self.end_s - self.start_s) / 3600.0)[self.missing].sum())

    @property
    def step_s(self) -> float:
        """The typical length of an interval [s]."""
        return float(np.median(self.end_s - self.start_s)) if self.end_s.size else 0.0

    @property
    def depth_mm(self) -> np.ndarray:
        """Rain depth of each interval [mm]."""
        return self.intensity_mm_h * (self.end_s - self.start_s) / 3600.0

    @property
    def total_mm(self) -> float:
        return float(self.depth_mm.sum())

    @property
    def peak_mm_h(self) -> float:
        return float(self.intensity_mm_h.max()) if self.intensity_mm_h.size else 0.0

    @property
    def end(self) -> float:
        """End of the last interval [s]."""
        return float(self.end_s[-1]) if self.end_s.size else 0.0

    @property
    def last_rain(self) -> float:
        """End of the last interval with rain [s]; 0 for a dry series."""
        wet = np.flatnonzero(self.intensity_mm_h > 0)
        return float(self.end_s[wet[-1]]) if wet.size else 0.0

    def rate_at(self, t: float) -> float:
        """Intensity [mm/h] in force at time *t* (zero outside the series)."""
        k = np.searchsorted(self.start_s, t, side="right") - 1
        if k < 0 or t >= self.end_s[k]:
            return 0.0
        return float(self.intensity_mm_h[k])

    def to_frame(self) -> pd.DataFrame:
        """The canonical table; an interval without a value has no intensity."""
        return pd.DataFrame({"start_s": self.start_s, "end_s": self.end_s,
                             "rain_mm_h": np.where(self.missing, np.nan,
                                                   self.intensity_mm_h)})

    def write(self, path: str | Path) -> Path:
        """Write the canonical CSV (both interval ends, intensity in mm/h; a gap stays empty, so that whoever reads the file again still sees it)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.to_frame().to_csv(path, index=False, float_format="%.6g")
        return path

    def problems(self) -> list[str]:
        """What is wrong with the series, in words; empty when it is usable."""
        out = []
        if self.start_s.size == 0:
            return ["the series has no rows"]
        if np.any(self.end_s <= self.start_s):
            out.append("an interval ends before it starts")
        if np.any(self.start_s[1:] < self.end_s[:-1] - 1e-6):
            out.append("intervals overlap or the times do not increase")
        if np.any(self.intensity_mm_h < 0):
            out.append("negative rain")
        if not np.all(np.isfinite(self.intensity_mm_h)):
            out.append("rain values that are not numbers")
        return out


def _read_table(path: Path) -> pd.DataFrame:
    """A CSV (any separator) or Excel table; decimal commas become numbers."""
    if path.suffix.lower() in (".xlsx", ".xls"):
        frame = pd.read_excel(path)
    else:
        frame = pd.read_csv(path, comment="#", sep=None, engine="python")
        if frame.shape[1] < 2:
            frame = pd.read_csv(path, comment="#", sep=";", decimal=",")
    for col in frame.columns:
        if frame[col].dtype == object or pd.api.types.is_string_dtype(frame[col]):
            as_is = pd.to_numeric(frame[col], errors="coerce")
            comma = pd.to_numeric(frame[col].astype(str).str.strip()
                                  .str.replace(",", ".", regex=False), errors="coerce")
            if comma.notna().sum() > as_is.notna().sum():
                frame[col] = comma
    return frame


def _column(frame: pd.DataFrame, *words: str, exclude: tuple = ()) -> str | None:
    for col in frame.columns:
        low = str(col).lower().strip()
        if col in exclude:
            continue
        if any(w in low for w in words):
            return col
    return None


def _integer_dates(numeric: pd.Series) -> pd.Series | None:
    """Dates of a column of integers like 2024060213 (``YYYYMMDDHH``), else ``None``.

    Every value has to have the same number of digits, parse as a date between 1900 and 2100 and increase. Ten digits are also what Unix seconds have, and from 2030 on those parse as ``YYYYMMDDHH`` too. They are told apart by their steps. ``YYYYMMDDHH`` read as a number steps by 1 to 23 within a day, by 100 from one day to the next and by 77 or more over midnight, never by whole minutes, which is what a logger writes; and read as dates its steps are hours, while seconds a logger writes every few minutes, with or without jitter, read as dates step by days (600 s is 6 days). So ten digits are seconds when every step is a whole number of minutes, or when, read as dates, the rows lie more than a day apart in the median.
    """
    values = numeric.to_numpy(dtype=float)
    if not np.all(values == np.round(values)) or np.any(values <= 0):
        return None
    text = pd.Series(values.astype(np.int64).astype(str))
    digits = set(text.str.len())
    if len(digits) != 1 or next(iter(digits)) not in _INTEGER_DATES:
        return None
    n_digits = next(iter(digits))
    steps = np.diff(values)
    if n_digits == 10 and steps.size and np.all(steps > 0) and np.all(steps % 60 == 0):
        return None
    stamps = pd.to_datetime(text, format=_INTEGER_DATES[n_digits], errors="coerce")
    if stamps.isna().any() or not stamps.dt.year.between(1900, 2100).all():
        return None
    if not stamps.is_monotonic_increasing:
        return None
    if n_digits == 10 and steps.size and float(
            np.median(np.diff(stamps).astype("timedelta64[s]").astype(float))) > 86400.0:
        return None
    return stamps


def _parse_dates(values: pd.Series) -> pd.Series:
    with warnings.catch_warnings():         # "could not infer format": parsed one by one
        warnings.simplefilter("ignore", UserWarning)
        return pd.to_datetime(values, errors="coerce", utc=False)


def _parse_times(times: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Each time as a number and as a date, ``NaN`` and ``NaT`` where it is not one.

    A column that pandas already holds as dates (date cells of an Excel table) is dates: read as numbers they would be nanoseconds since 1970. A time zone is turned into UTC without one, like the other dates.
    """
    if pd.api.types.is_datetime64_any_dtype(times):
        dated = pd.to_datetime(times)
        if dated.dt.tz is not None:
            dated = dated.dt.tz_convert("UTC").dt.tz_localize(None)
        return pd.Series(np.nan, index=times.index), dated
    numeric = pd.to_numeric(times, errors="coerce")
    return numeric, _parse_dates(times.where(numeric.isna()))


def _seconds(values: pd.Series, parsed: tuple[pd.Series, pd.Series] | None = None
             ) -> tuple[np.ndarray, pd.Timestamp | None]:
    """Seconds of a time column, and the calendar time of its first row if dated.

    A column of integers may hold dates like ``YYYYMMDDHH`` (:func:`_integer_dates`). *parsed* is what :func:`_parse_times` made of *values*, when the caller has it already.
    """
    numeric, dated = parsed if parsed is not None else _parse_times(values)
    if numeric.notna().all():
        stamps = _integer_dates(numeric)
        if stamps is None:
            return numeric.to_numpy(dtype=float), None
        first = stamps.iloc[0]
        return (stamps - first).dt.total_seconds().to_numpy(dtype=float), first
    if dated.isna().any():
        # the first value that is neither; else the column mixes seconds and dates
        neither = values[numeric.isna() & dated.isna()]
        bad = (neither if len(neither) else values[dated.isna()]).iloc[0]
        raise ValueError(f"time value {bad!r} is neither seconds nor a date")
    first = dated.iloc[0]
    return (dated - first).dt.total_seconds().to_numpy(dtype=float), first


def _data_rows(frame: pd.DataFrame, tcol, vcols, name: str
               ) -> tuple[pd.DataFrame, tuple[pd.Series, pd.Series], list[str]]:
    """The rows of *frame* that are data, their times as :func:`_parse_times` reads them, and notes on the rows left out.

    A row is data when its time (column *tcol*) is seconds or a date. A row that repeats the header (its time is the name of *tcol*) is never data. Before the first and after the last data row, a row is also left out when it cannot be one with a broken time: its time holds no digit (``Summe``, an empty cell) or none of the value columns *vcols* holds a number (a units row). Inside the data, a row with neither a time nor a value (an empty row) is left out. Any other row without a time stops the import with its value: a stamp that cannot be read, or a value that cannot be placed in time.
    """
    times = frame[tcol]
    text = times.astype(str).str.strip().where(times.notna(), "")
    numeric, dated = _parse_times(times)
    is_time = (numeric.notna() | dated.notna()).to_numpy()
    header = (text.str.lower() == str(tcol).strip().lower()).to_numpy()
    has_value = np.zeros(len(frame), bool)
    for col in vcols:
        has_value |= pd.to_numeric(frame[col], errors="coerce").notna().to_numpy()
    has_digit = text.str.contains(r"\d", regex=True).to_numpy()
    if not is_time.any():
        raise ValueError(f"{name}: no row has a time")
    first_row, last_row = np.flatnonzero(is_time)[[0, -1]]
    inside = np.zeros(len(frame), bool)
    inside[first_row:last_row + 1] = True
    broken = ~is_time & ~header & np.where(inside, has_value | has_digit,
                                           has_value & has_digit)
    if broken.any():
        k = int(np.flatnonzero(broken)[0])
        if text.iloc[k]:
            raise ValueError(f"{name}: time value {text.iloc[k]!r} is neither seconds "
                             "nor a date")
        before = text.iloc[:k][is_time[:k]]
        where = f"after {before.iloc[-1]!r}" if len(before) else "before the first time"
        raise ValueError(f"{name}: the row {where} has a value but no time")
    notes = []
    edges = int((~inside).sum())
    if edges:
        notes.append(f"{_count(edges, 'row')} before or after the data left out as no "
                     "data (a units row, a sum)")
    blank = int((inside & ~is_time).sum())
    if blank:
        notes.append(f"{_count(blank, 'row')} inside the data left out as no data (an "
                     "empty row, a repeated header)")
    keep = np.flatnonzero(is_time)
    parsed = (numeric.iloc[keep].reset_index(drop=True),
              dated.iloc[keep].reset_index(drop=True))
    return frame.iloc[keep].reset_index(drop=True), parsed, notes


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def _no_measurement(values: pd.Series) -> pd.Series:
    """*values* as numbers, with the agencies' code for no measurement (-999) and every cell that is not a number as ``NaN``."""
    numbers = pd.to_numeric(values, errors="coerce")
    return numbers.mask(numbers <= MISSING_AT_OR_BELOW)


def _dwd_mornings(dates: pd.Series, regime: pd.Series) -> pd.Series:
    """The morning observation of each of *dates* in UTC, as the DWD made it on the day *regime* names."""
    recent = (regime >= DWD_UTC_DAYS_FROM).to_numpy()
    mornings = dates + pd.Timedelta(hours=5, minutes=50)
    if not recent.all():
        mornings[~recent] = (dates[~recent] + pd.Timedelta(hours=7, minutes=30)).dt \
            .tz_localize("Europe/Berlin").dt.tz_convert("UTC").dt.tz_localize(None)
    return mornings


def dwd_day_windows(days) -> tuple[pd.Series, pd.Series, list[str]]:
    """When the DWD's precipitation day of each date begins and ends, in UTC, and a note.

    The parameter metadata in each station archive give the day of ``RS`` as running from the morning observation of the date to the one of the next: 05:51 to 05:50 UTC since April 2001 (:data:`DWD_UTC_DAYS_FROM`), 07:30 to 07:30 legal time before, and 07:00 at the stations of the new federal states until 1990, which a file of values alone does not tell. A day value is therefore placed about six hours later than the calendar day its date names. Each day ends with the observation of its own rule, so the day before the change ends at 05:30 UTC and the 20 minutes until 05:50 belong to no day.
    """
    days = pd.Series(pd.to_datetime(days)).dt.normalize().reset_index(drop=True)
    starts = _dwd_mornings(days, days)
    ends = _dwd_mornings(days + pd.Timedelta(days=1), days)
    notes = []
    if (days < DWD_UTC_DAYS_FROM).any():
        notes.append("before April 2001 the DWD's day began at 07:30 legal time (07:00 "
                     "at stations in the new federal states until 1990, taken as 07:30)")
    return starts, ends, notes


def _agency_windows(agency: str, seconds: np.ndarray, first: pd.Timestamp | None
                    ) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """The interval of each value of a DWD column, in seconds from the first stamp: its own period, however far the next row is."""
    _, period, _ = AGENCY_COLUMNS[agency]
    if period is not None:
        return seconds - period, seconds.copy(), []
    if first is None:
        return seconds.copy(), seconds + 86400.0, []
    starts, ends, notes = dwd_day_windows(first + pd.to_timedelta(seconds, unit="s"))
    return ((starts - first).dt.total_seconds().to_numpy(dtype=float),
            (ends - first).dt.total_seconds().to_numpy(dtype=float), notes)


def _with_holes(starts, ends, values, missing):
    """The intervals with one of no value in each hole between two of them: the rows a record does not have."""
    holes = np.flatnonzero(starts[1:] > ends[:-1] + 1e-6)
    if not holes.size:
        return starts, ends, values, missing
    at = holes + 1
    return (np.insert(starts, at, ends[holes]), np.insert(ends, at, starts[at]),
            np.insert(values, at, 0.0), np.insert(missing, at, True))


def telemac_hyetograph(path: str | Path) -> RainSeries | None:
    """TELEMAC's own hyetograph file, read as TELEMAC reads it; ``None`` for any other file.

    The file is ``FORMATTED DATA FILE 1`` of TELEMAC's ``RUNOFF_GREENAMPT`` with ``RAINDEF = 3`` (release 8.4 and later, example ``pluie``). TELEMAC skips its first two lines, ``#HYETOGRAPH FILE`` and ``#T (s) RAINFALL (mm)``, and reads a time in seconds and a depth in mm from each line after them. A depth falls in the interval that ends at its time, and the depth of the first line belongs to no interval: its time is where the series begins. The units and stamp settings therefore do not apply. Time zero is the time of the first line, which TELEMAC requires to be 0.
    """
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xls"):
        return None
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            first = stream.readline()
            if not first.strip().upper().startswith(TELEMAC_HYETOGRAPH):
                return None
            lines = [first, *stream.read().splitlines()]
    except OSError:
        return None
    rows = []
    for number, line in enumerate(lines[2:], start=3):
        if not line.strip():
            continue
        fields = line.replace(",", " ").split()
        try:
            # TELEMAC reads only the time of the first line (READ(UL,*) AT1)
            rows.append((float(fields[0]), float(fields[1]) if rows else 0.0))
        except (IndexError, ValueError):
            raise ValueError(f"{path.name}, line {number}: {line.strip()!r} is not a time "
                             "in s and a depth in mm, as TELEMAC's hyetograph file "
                             "has them") from None
    if len(rows) < 2:
        raise ValueError(f"{path.name}: TELEMAC's hyetograph file needs a line where the "
                         "series begins and at least one with a depth")
    times, depths = (np.array(column) for column in zip(*rows))
    if np.any(np.diff(times) <= 0):
        raise ValueError(f"{path.name}: the times do not increase")
    starts, ends = times[:-1] - times[0], times[1:] - times[0]
    series = RainSeries(starts, ends, depths[1:] * 3600.0 / (ends - starts))
    series.notes.append(f"read {path.name} as TELEMAC's hyetograph file: the depth in mm "
                        "of the interval that ends at each time; the units and stamp "
                        "settings do not apply to it")
    if times[0] != 0:
        series.notes.append(f"its first time is {times[0]:g} s; time zero is that time")
    return series


def read_series(path: str | Path, *, units: str = "mm/h", stamp: str = "end",
                start: str | None = None) -> RainSeries:
    """Read a rain series from a CSV or Excel table.

    The canonical file (``start_s, end_s, rain_mm_h``) is read as it is. Otherwise the first time-like column (``time``, ``date``, ``datum``, ``zeit``, ``t``) gives the stamps, in seconds, as dates or as integers like 2024060213 (DWD), and the rain column of the DWD (:data:`AGENCY_COLUMNS`), else the first column that looks like rain (``rain``, ``precip``, ``niederschlag``, ``rr``, ``mm``), else the last column with numbers gives the values. *units* is ``mm/h`` (intensity) or ``mm`` (depth per interval); *stamp* says whether a time is the ``start`` or the ``end`` of its interval.

    A row without a value (empty, or -999 as the DWD writes it) keeps its interval and counts as dry; :attr:`RainSeries.gaps` says how many there were, so that the checkup can report them rather than the next row's interval being stretched over the gap. A value of the DWD covers its own period, so a row such a file does not have is a gap as well. Rows that are no data, a units row under the header or a sum at the end, are left out, and a row with a value but no readable time stops the import (:func:`_data_rows`).

    *start*, a date and time, sets the model's time zero for a dated series; without it time zero is the start of the first interval.

    TELEMAC's own hyetograph file is read as TELEMAC reads it (:func:`telemac_hyetograph`).
    """
    if units not in ("mm/h", "mm"):
        raise ValueError(f"rain units must be 'mm/h' or 'mm', got {units!r}")
    if stamp not in ("start", "end"):
        raise ValueError(f"the time stamp must be 'start' or 'end', got {stamp!r}")
    path = Path(path)
    telemac = telemac_hyetograph(path)
    if telemac is not None:
        if start:
            raise ValueError(f"{path.name}: TELEMAC's hyetograph file has no dates, so "
                             "hydrology.rainfall_start cannot apply to it")
        return telemac
    frame = _read_table(path)
    cols = {str(c).lower().strip(): c for c in frame.columns}
    if all(name in cols for name in CANONICAL):
        rain = pd.to_numeric(frame[cols["rain_mm_h"]], errors="coerce")
        series = RainSeries(frame[cols["start_s"]], frame[cols["end_s"]],
                            rain.fillna(0.0), missing=rain.isna())
        if start:
            series.origin = pd.Timestamp(start)
        return series
    tcol = _column(frame, "time", "date", "datum", "zeit") or next(
        (c for c in frame.columns if str(c).lower().strip() == "t"), None)
    if tcol is None:
        raise ValueError(f"{path.name}: no time column (time, date, datum, zeit or t)")
    agency = next((name for name in AGENCY_COLUMNS if name in cols
                   and cols[name] != tcol), None)
    vcol = (cols[agency] if agency else None) or (
        _column(frame, "rain", "precip", "niederschlag", "rr", "mm", "intens",
                exclude=(tcol,))
        or next((c for c in reversed(frame.columns) if c != tcol
                 and pd.to_numeric(frame[c], errors="coerce").notna().any()), None))
    if vcol is None:
        raise ValueError(f"{path.name}: no column with rain values")
    notes = []
    if agency:
        stamp, _, period = AGENCY_COLUMNS[agency]
        units = "mm"
        notes.append(f"column {str(vcol).strip()!r} of the DWD: the depth in mm of "
                     f"{period}; the units and stamp settings do not apply to it")
    frame, parsed, skipped = _data_rows(frame, tcol, [vcol], path.name)
    notes.extend(skipped)
    # a row without a value (empty, -999) keeps its interval as a gap
    values = _no_measurement(frame[vcol])
    if not values.notna().any():
        raise ValueError(f"{path.name}: no rows with a rain value")
    seconds, first = _seconds(frame[tcol], parsed)
    missing = values.isna().to_numpy()
    values = values.fillna(0.0).to_numpy(dtype=float)
    if np.any(np.diff(seconds) <= 0):
        raise ValueError(f"{path.name}: the times do not increase")
    if agency:
        starts, ends, day_notes = _agency_windows(agency, seconds, first)
        notes.extend(day_notes)
        starts, ends, values, missing = _with_holes(starts, ends, values, missing)
    elif seconds.size == 1:
        raise ValueError(f"{path.name}: one row cannot say how long its interval is")
    elif stamp == "end":
        # the first row's interval is as long as the step that follows it
        ends = seconds
        starts = np.concatenate([[seconds[0] - (seconds[1] - seconds[0])],
                                 seconds[:-1]])
    else:
        starts = seconds
        ends = np.concatenate([seconds[1:], [seconds[-1] + (seconds[-1] - seconds[-2])]])
    hours = (ends - starts) / 3600.0
    intensity = values / hours if units == "mm" else values
    origin = None if first is None else first + pd.Timedelta(seconds=float(starts[0]))
    zero = starts[0]
    if start:
        anchor = pd.Timestamp(start)
        if origin is None:
            raise ValueError("a start date needs a series with dates")
        zero = starts[0] + (anchor - origin).total_seconds()
        origin = anchor
    series = RainSeries(starts - zero, ends - zero, intensity, origin=origin,
                        missing=missing)
    series.notes.append(f"read {path.name}: column {str(vcol).strip()!r} in {units}, "
                        f"time stamps at the {stamp} of each interval")
    series.notes.extend(notes)
    if series.gaps:
        series.notes.append(f"{series.gaps} intervals ({series.gap_hours:g} h) have no "
                            "value and are taken as dry")
    return series


# --------------------------------------------------------------------------- #
# design storms
# --------------------------------------------------------------------------- #


def depth_at(durations_min, depths_mm, duration_min: float) -> float:
    """Depth [mm] of a depth-duration table at *duration_min*, log-log interpolated.

    Below the shortest tabulated duration the intensity of that duration is kept; above the longest the depth is extrapolated along the last segment.
    """
    d = np.asarray(durations_min, dtype=float)
    p = np.asarray(depths_mm, dtype=float)
    order = np.argsort(d)
    d, p = d[order], p[order]
    if np.any(d <= 0) or np.any(p <= 0):
        raise ValueError("durations and depths of the table must be positive")
    if duration_min <= d[0]:
        return float(p[0] * duration_min / d[0])
    if d.size == 1:
        return float(p[0] * duration_min / d[0])
    ld, lp = np.log(d), np.log(p)
    if duration_min >= d[-1]:
        slope = (lp[-1] - lp[-2]) / (ld[-1] - ld[-2])
        return float(math.exp(lp[-1] + slope * (math.log(duration_min) - ld[-1])))
    return float(math.exp(np.interp(math.log(duration_min), ld, lp)))


def design_storm(durations_min, depths_mm, *, duration_min: float, step_min: float = 5.0,
                 distribution: str = "euler2", peak: float | None = None) -> RainSeries:
    """A design hyetograph from a depth-duration table of one return period.

    *duration_min* is the storm duration and *step_min* the block length; the table may be KOSTRA-DWD, NOAA Atlas 14 or any other depth-duration-frequency product. *peak* moves the largest block (0 to 1 of the duration) for ``alternating``; the default is the middle, and ``euler2`` always has it at 30 %.
    """
    if distribution not in DISTRIBUTIONS:
        raise ValueError(f"distribution must be one of {DISTRIBUTIONS}, got "
                         f"{distribution!r}")
    if step_min <= 0 or duration_min <= 0:
        raise ValueError("the duration and the block length must be positive")
    n = int(round(duration_min / step_min))
    if n < 1 or abs(n * step_min - duration_min) > 1e-6 * duration_min:
        raise ValueError(f"the duration {duration_min:g} min is not a whole number of "
                         f"blocks of {step_min:g} min")
    total = depth_at(durations_min, depths_mm, duration_min)
    if distribution == "block":
        blocks = np.full(n, total / n)
    else:
        cumulative = np.array([depth_at(durations_min, depths_mm, (k + 1) * step_min)
                               for k in range(n)])
        increments = np.diff(np.concatenate([[0.0], cumulative]))
        # a depth-duration curve whose increments grow (a table typo) would put the
        # largest block last; sorting keeps the shape the method describes
        ranked = np.sort(increments)[::-1]
        blocks = np.zeros(n)
        if distribution == "alternating":
            centre = int(round((0.5 if peak is None else float(peak)) * (n - 1)))
            centre = min(max(centre, 0), n - 1)
            positions = [centre]
            left, right = centre - 1, centre + 1
            toggle = True
            while len(positions) < n:
                if (toggle and right < n) or left < 0:
                    positions.append(right)
                    right += 1
                else:
                    positions.append(left)
                    left -= 1
                toggle = not toggle
            blocks[positions] = ranked
        else:                                   # euler2
            m = max(1, math.ceil(0.3 * n))      # 1-based position of the peak
            after = n - m + 1                   # peak and the blocks after it
            blocks[m - 1:] = ranked[:after]
            blocks[:m - 1] = np.sort(ranked[after:])
    starts = np.arange(n) * step_min * 60.0
    ends = starts + step_min * 60.0
    series = RainSeries(starts, ends, blocks / (step_min / 60.0))
    series.notes.append(f"design storm ({distribution}) of {duration_min:g} min in blocks "
                        f"of {step_min:g} min, {total:.1f} mm")
    return series


def read_depth_duration(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """A depth-duration table: a duration column in minutes and a depth column in mm.

    Columns are found by name (``dur``/``dauer``/``min`` and ``depth``/``mm``/``hn``), else the first two numeric columns are taken in that order.
    """
    frame = _read_table(Path(path))
    dcol = _column(frame, "dur", "dauer", "min")
    pcol = _column(frame, "depth", "hoehe", "höhe", "hn", "mm", exclude=(dcol,))
    if dcol is None or pcol is None:
        numeric = frame.apply(pd.to_numeric, errors="coerce").dropna(axis=1, how="all")
        if numeric.shape[1] < 2:
            raise ValueError(f"{Path(path).name}: a duration and a depth column are "
                             "needed")
        dcol, pcol = numeric.columns[:2]
    d = pd.to_numeric(frame[dcol], errors="coerce")
    p = pd.to_numeric(frame[pcol], errors="coerce")
    keep = d.notna() & p.notna()
    return d[keep].to_numpy(dtype=float), p[keep].to_numpy(dtype=float)
