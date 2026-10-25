"""Rain series: reading measured records and building design storms.

A rain series is a sequence of intervals with a constant intensity, which is how rain
gauges, radar products and design storms all report it, and how TELEMAC applies it
(the generated routine holds each intensity over its interval). The one thing a series
must say, and a plain table often does not, is whether the time stamp of a row is the
**start** or the **end** of its interval: a gauge that reports the rain of the past hour
at 13:00 means 12:00 to 13:00. Agencies (DWD station hours, RADOLAN, Open-Meteo) stamp
the end, so ``end`` is the default; the files aXqua writes itself carry both ends
(``start_s, end_s, rain_mm_h``), so nothing has to be assumed for them.

Design storms are built from a depth-duration table of one return period, which is what
KOSTRA-DWD (Germany), NOAA Atlas 14 (USA) and comparable national products publish:

``block``
    one intensity for the whole duration;
``alternating``
    the alternating block method of Chow, Maidment and Mays (1988): the increments of
    the depth-duration curve, largest in the middle, the others alternately right and
    left of it;
``euler2``
    Euler type II, the German standard (DWA-A 118): the largest increment at 30 % of the
    duration, the following ones in decreasing order after it, and the smallest ones in
    increasing order before it. Every window that starts at the peak holds the depth of
    the table for its duration, which is the property the tests check.
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

#: Rain columns of the DWD Climate Data Center by their exact names, with the stamp of
#: their ``MESS_DATUM`` and the period a value covers. Each is a depth in mm (the DWD's
#: descriptions of the hourly, 10-minute and daily products), so the units and stamp
#: settings do not apply to them. Matched before the words of :func:`read_series`,
#: because the other columns of those files (``QN_8``, ``RS_IND``, ``WRTR``, ``eor``)
#: hold quality flags and indicators. The description of the daily product does not
#: say when its day begins; ``RS`` is placed on the calendar day ``MESS_DATUM`` names.
AGENCY_COLUMNS = {"r1": ("end", "the hour before MESS_DATUM"),
                  "rws_10": ("end", "the 10 minutes before MESS_DATUM"),
                  "rs": ("start", "the day MESS_DATUM names")}

#: Values agencies write for "no measurement" (DWD: -999).
MISSING_AT_OR_BELOW = -999.0

#: Dates written as one integer, by number of digits (DWD ``MESS_DATUM``).
_INTEGER_DATES = {8: "%Y%m%d", 10: "%Y%m%d%H", 12: "%Y%m%d%H%M"}


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
        """Write the canonical CSV (both interval ends, intensity in mm/h; a gap stays
        empty, so that whoever reads the file again still sees it)."""
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


def _named_like_a_date(column) -> bool:
    """Only a column named as a date (``MESS_DATUM``, ``date``) may hold integer dates:
    a column of Unix seconds is called ``time`` or ``timestamp``, and from 2030 its
    values also parse as ``YYYYMMDDHH``."""
    return "dat" in str(column).lower()


def _integer_dates(numeric: pd.Series) -> pd.Series | None:
    """Dates of a column of integers like 2024060213 (``YYYYMMDDHH``), else ``None``.

    Every value has to have the same number of digits, parse as a date between 1900
    and 2100 and increase; and the column has to be named as a date
    (:func:`_named_like_a_date`), which the caller decides."""
    values = numeric.to_numpy(dtype=float)
    if not np.all(values == np.round(values)) or np.any(values <= 0):
        return None
    text = pd.Series(values.astype(np.int64).astype(str))
    digits = set(text.str.len())
    if len(digits) != 1 or next(iter(digits)) not in _INTEGER_DATES:
        return None
    stamps = pd.to_datetime(text, format=_INTEGER_DATES[next(iter(digits))],
                            errors="coerce")
    if stamps.isna().any() or not stamps.dt.year.between(1900, 2100).all():
        return None
    if not stamps.is_monotonic_increasing:
        return None
    return stamps


def _seconds(values: pd.Series, *, integer_dates: bool = False
             ) -> tuple[np.ndarray, pd.Timestamp | None]:
    """Seconds of a time column, and the calendar time of its first row if dated.

    *integer_dates* lets a column of integers be read as ``YYYYMMDDHH`` dates and the
    like (:func:`_integer_dates`)."""
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().all():
        stamps = _integer_dates(numeric) if integer_dates else None
        if stamps is None:
            return numeric.to_numpy(dtype=float), None
        first = stamps.iloc[0]
        return (stamps - first).dt.total_seconds().to_numpy(dtype=float), first
    with warnings.catch_warnings():         # "could not infer format": parsed one by one
        warnings.simplefilter("ignore", UserWarning)
        stamps = pd.to_datetime(values, errors="coerce", utc=False)
    if stamps.isna().any():
        # the first value that is neither; else the column mixes seconds and dates
        neither = values[numeric.isna() & stamps.isna()]
        bad = (neither if len(neither) else values[stamps.isna()]).iloc[0]
        raise ValueError(f"time value {bad!r} is neither seconds nor a date")
    first = stamps.iloc[0]
    return (stamps - first).dt.total_seconds().to_numpy(dtype=float), first


def read_series(path: str | Path, *, units: str = "mm/h", stamp: str = "end",
                start: str | None = None) -> RainSeries:
    """Read a rain series from a CSV or Excel table.

    The canonical file (``start_s, end_s, rain_mm_h``) is read as it is. Otherwise the
    first time-like column (``time``, ``date``, ``datum``, ``zeit``, ``t``) gives the
    stamps, in seconds, as dates or as integers like 2024060213 (DWD), and the rain
    column of the DWD (:data:`AGENCY_COLUMNS`), else the first column that looks like
    rain (``rain``, ``precip``, ``niederschlag``, ``rr``, ``mm``), else the last column
    with numbers gives the values. *units* is ``mm/h`` (intensity) or ``mm`` (depth per
    interval); *stamp* says whether a time is the ``start`` or the ``end`` of its
    interval.

    A row without a value (empty, or -999 as the DWD writes it) keeps its interval and
    counts as dry; :attr:`RainSeries.gaps` says how many there were, so that the
    checkup can report them rather than the next row's interval being stretched over
    the gap.

    *start*, a date and time, sets the model's time zero for a dated series; without it
    time zero is the start of the first interval.
    """
    if units not in ("mm/h", "mm"):
        raise ValueError(f"rain units must be 'mm/h' or 'mm', got {units!r}")
    if stamp not in ("start", "end"):
        raise ValueError(f"the time stamp must be 'start' or 'end', got {stamp!r}")
    path = Path(path)
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
        stamp, period = AGENCY_COLUMNS[agency]
        units = "mm"
        notes.append(f"column {vcol!r} of the DWD: the depth of {period} in mm; the "
                     "units and stamp settings do not apply to it")
    # rows before the first and after the last time are not data: a units row under
    # the header, a sum at the end. A row without a time inside cannot be placed.
    times = frame[tcol]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        numeric = pd.to_numeric(times, errors="coerce")
        dated = pd.to_datetime(times.where(numeric.isna()), errors="coerce")
    is_time = (numeric.notna() | dated.notna()).to_numpy()
    if not is_time.any():
        raise ValueError(f"{path.name}: no row has a time")
    first_row, last_row = np.flatnonzero(is_time)[[0, -1]]
    skipped = len(frame) - (last_row - first_row + 1)
    frame = frame.iloc[first_row:last_row + 1]
    if skipped:
        notes.append(f"{skipped} rows before or after the data have no time and are "
                     "left out (a units row, a sum)")
    timed = frame[tcol].notna() & (frame[tcol].astype(str).str.strip() != "")
    frame = frame[timed]
    # a row without a value (empty, -999) keeps its interval as a gap
    values = pd.to_numeric(frame[vcol], errors="coerce")
    values = values.mask(values <= MISSING_AT_OR_BELOW)
    if not values.notna().any():
        raise ValueError(f"{path.name}: no rows with a rain value")
    seconds, first = _seconds(frame[tcol].reset_index(drop=True),
                              integer_dates=_named_like_a_date(tcol))
    missing = values.isna().to_numpy()
    values = values.fillna(0.0).to_numpy(dtype=float)
    if seconds.size == 1:
        raise ValueError(f"{path.name}: one row cannot say how long its interval is")
    steps = np.diff(seconds)
    if np.any(steps <= 0):
        raise ValueError(f"{path.name}: the times do not increase")
    if stamp == "end":
        # the first row's interval is as long as the step that follows it
        ends = seconds
        starts = np.concatenate([[seconds[0] - steps[0]], seconds[:-1]])
    else:
        starts = seconds
        ends = np.concatenate([seconds[1:], [seconds[-1] + steps[-1]]])
    hours = (ends - starts) / 3600.0
    intensity = values / hours if units == "mm" else values
    origin = None
    if first is not None:
        origin = first + pd.Timedelta(seconds=float(starts[0] - seconds[0]))
    zero = starts[0]
    if start:
        anchor = pd.Timestamp(start)
        if origin is None:
            raise ValueError("a start date needs a series with dates")
        zero = starts[0] + (anchor - origin).total_seconds()
        origin = anchor
    series = RainSeries(starts - zero, ends - zero, intensity, origin=origin,
                        missing=missing)
    series.notes.append(f"read {path.name}: column {vcol!r} in {units}, time stamps at "
                        f"the {stamp} of each interval")
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

    Below the shortest tabulated duration the intensity of that duration is kept; above
    the longest the depth is extrapolated along the last segment.
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

    *duration_min* is the storm duration and *step_min* the block length; the table may
    be KOSTRA-DWD, NOAA Atlas 14 or any other depth-duration-frequency product.
    *peak* moves the largest block (0 to 1 of the duration) for ``alternating``; the
    default is the middle, and ``euler2`` always has it at 30 %.
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

    Columns are found by name (``dur``/``dauer``/``min`` and ``depth``/``mm``/``hn``),
    else the first two numeric columns are taken in that order.
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
