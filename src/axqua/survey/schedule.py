"""The working day of a survey: when it starts and ends, and when the breaks fall.

A plan counts in minutes after midnight of its first day. Where the user gave no start time, the day starts at 00:00 and every time of the plan reads as the time since the start. A day ends after the working hours of the settings, breaks included, and not later than sunset when the calendar day and its sunset are known.

Breaks follow two intervals: a short one after every two hours and a long one after every four (the defaults), taken between two activities and never in the middle of a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass

from axqua.survey.settings import TimeSettings, clock_minutes

DAY = 24 * 60.0


def fmt_clock(minutes: float) -> str:
    """Minutes after midnight as ``HH:MM`` (past midnight the hours keep counting)."""
    total = int(round(float(minutes)))
    return f"{total // 60:02d}:{total % 60:02d}"


def fmt_duration(minutes: float) -> str:
    """A duration as ``2 h 05 min`` or ``35 min``."""
    total = int(round(float(minutes)))
    if total < 60:
        return f"{total} min"
    return f"{total // 60} h {total % 60:02d} min"


@dataclass
class Window:
    """One working day: its start and its latest end in minutes after midnight, and what set the end."""

    start: float
    end: float
    limit: str          # "working hours" | "sunset"
    date: str = ""
    sunrise: float | None = None
    sunset: float | None = None


def day_window(time: TimeSettings, sun: list | None = None, day: int = 0) -> Window:
    """The working day number *day* (0 is the first). *sun* holds one ``{"date", "sunrise", "sunset"}`` per day in minutes after midnight."""
    start = clock_minutes(time.start) if time.start else 0.0
    start = 0.0 if start is None else start
    end, limit = start + float(time.day_hours) * 60.0, "working hours"
    entry = (sun[min(day, len(sun) - 1)] if sun else None) or {}
    sunrise, sunset = entry.get("sunrise"), entry.get("sunset")
    if time.use_daylight and time.start and sunset is not None and sunset < end:
        end, limit = float(sunset), "sunset"
    return Window(start=start, end=max(end, start), limit=limit, date=str(entry.get("date") or (time.date if day == 0 else "")), sunrise=sunrise, sunset=sunset)


class Breaks:
    """Which break is due. ``due(now)`` returns ``(minutes, kind)`` or ``(0, "")``; ``taken(now_after)`` records it."""

    def __init__(self, time: TimeSettings, start: float) -> None:
        self.time = time
        self.last = float(start)
        self.last_long = float(start)

    def due(self, now: float) -> tuple[float, str]:
        t = self.time
        long_due = t.long_break_minutes > 0 and t.long_break_every_h > 0 and now - self.last_long >= t.long_break_every_h * 60.0 - 1e-6
        short_due = t.short_break_minutes > 0 and t.short_break_every_h > 0 and now - self.last >= t.short_break_every_h * 60.0 - 1e-6
        if long_due:
            return float(t.long_break_minutes), "long break"
        if short_due:
            return float(t.short_break_minutes), "break"
        return 0.0, ""

    def taken(self, now: float, kind: str) -> None:
        self.last = float(now)
        if kind == "long break":
            self.last_long = float(now)


__all__ = ["Breaks", "DAY", "Window", "day_window", "fmt_clock", "fmt_duration"]
