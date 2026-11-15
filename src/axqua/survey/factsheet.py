"""The factsheet of a survey plan: one A4 page per kind of survey, as an OpenDocument text (``.odt``).

A factsheet is what the surveyors take to the river: the plan in numbers, the timetable with the start, the breaks and the end, daylight and weather, what is known about the mobile network and the DGPS corrections, the assumptions the times rest on, and what to mind in the field. The page is A4 with narrow margins (1.27 cm) and set in Arial 10 pt.

The file is written with the standard library, because an ``.odt`` is a zip archive of four XML files. **One page is a promise**, so the sheet counts its own lines while it is written (:attr:`Sheet.units`), breaking every paragraph and table cell with the character widths of Arial (:func:`wrapped_lines`), and :func:`write_factsheet` leaves out the least important content, step by step, until the page holds it: first the single steps of the timetable (the layers of ``survey-plan.gpkg`` and ``survey-plan.json`` keep every one), then the timetable shrinks to one row per surveyor and working day, then the later hints go, then all warnings but the first. ``tests/test_survey_planner.py`` converts factsheets with LibreOffice, where it is installed, and counts the pages.
"""

from __future__ import annotations

import datetime
import math
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from axqua.survey.conditions import GUST_LIMIT_DRONE
from axqua.survey.schedule import fmt_duration
from axqua.survey.settings import KIND_TITLES, SurveySettings

#: The text width of the page [cm]: A4 with margins of 1.27 cm.
WIDTH = 21.0 - 2 * 1.27
_PT = 72.0 / 2.54
#: The height of one line of Arial 10 pt [pt], and the lines the page holds (66.9).
LINE = 11.5
PAGE_LINES = (29.7 - 2 * 1.27) * _PT / LINE
#: Lines the page is filled with at most; the rest is the reserve for a font that runs wider than Arial.
PAGE_UNITS = 64.0
#: Advance widths of Arial in 1/1000 of the font size, by character; Liberation Sans and Helvetica have the same.
_ADVANCE = dict(zip(
    " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~",
    (278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015,
     667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333,
     556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584)))
_ADVANCE.update({"•": 350, "°": 400, "²": 333, "³": 333})
#: Bold Arial runs about this much wider than regular.
_BOLD = 1.08
#: The levels of detail of a factsheet: 0 holds everything, every further one leaves out more (see :func:`write_factsheet`).
LEVELS = 6
#: A timetable with more steps than this becomes one row per surveyor and working day from level 2 on.
OVERVIEW_FROM = 12

_NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
       'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
       'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" xmlns:dc="http://purl.org/dc/elements/1.1/" office:version="1.2"')
_FONTS = '<office:font-face-decls><style:font-face style:name="Arial" svg:font-family="Arial" style:font-family-generic="swiss" style:font-pitch="variable"/></office:font-face-decls>'
_MIME = "application/vnd.oasis.opendocument.text"


def text_width(text: str, *, bold: bool = False, size: float = 10.0) -> float:
    """The width of *text* in Arial [pt]. A character the table does not know counts as a wide one."""
    return sum(_ADVANCE.get(ch, 667) for ch in str(text)) * size / 1000.0 * (_BOLD if bold else 1.0)


def wrapped_lines(text: str, width: float, *, lead: str = "", bold: bool = False) -> int:
    """How many lines *text* takes in a column of *width* [pt], broken at spaces and after hyphens as a word processor does. *lead* is set bold before it."""
    space = text_width(" ", bold=bold)
    lines, used = 1, 0.0
    words = [(word, True) for word in str(lead).split()] + [(word, bold) for word in str(text).split()]
    for word, strong in words:
        pieces = [piece + "-" for piece in word.split("-")]
        pieces[-1] = pieces[-1][:-1]
        first = True
        for piece in pieces:
            if not piece:
                continue
            w = text_width(piece, bold=strong)
            gap = space if first and used > 0 else 0.0
            if used + gap + w > width and used > 0:
                lines, used, gap = lines + 1, 0.0, 0.0
            if w > width:                             # longer than the line: broken inside the word
                lines += int(w // width)
                w = w % width
            used += gap + w
            first = False
    return lines


class Sheet:
    """The page while it is written: its content, and how many lines it takes."""

    def __init__(self) -> None:
        self.body: list[str] = []
        self.auto: list[str] = []
        self.units = 0.0
        self._tables = 0

    def title(self, text: str, sub: str = "") -> None:
        self.body.append(f'<text:p text:style-name="Title">{escape(text)}</text:p>')
        self.units += 1.7
        if sub:
            self.body.append(f'<text:p text:style-name="Body">{escape(sub)}</text:p>')
            self.units += wrapped_lines(sub, WIDTH * _PT) + 0.13

    def heading(self, text: str) -> None:
        self.body.append(f'<text:p text:style-name="Heading">{escape(text)}</text:p>')
        self.units += 1.5

    def para(self, text: str, lead: str = "", style: str = "Body") -> None:
        bold = f'<text:span text:style-name="B">{escape(lead)}</text:span> ' if lead else ""
        self.body.append(f'<text:p text:style-name="{style}">{bold}{escape(text)}</text:p>')
        self.units += wrapped_lines(text, (WIDTH - (0.35 if style == "Bullet" else 0.0)) * _PT, lead=lead) + 0.13

    def bullet(self, text: str) -> None:
        self.para(text, "•", style="Bullet")

    def table(self, rows: list[list[str]], widths: list[float], *, header: list[str] | None = None, bold: tuple = ()) -> None:
        """A table of the full text width; *widths* are shares of it. Columns named in *bold* are set bold."""
        if not rows:
            return
        self._tables += 1
        name = f"T{self._tables}"
        total = float(sum(widths))
        cm = [WIDTH * w / total for w in widths]
        self.auto.append(f'<style:style style:name="{name}" style:family="table"><style:table-properties style:width="{WIDTH:.3f}cm" table:align="margins" fo:margin-top="0cm" fo:margin-bottom="0.08cm"/></style:style>')
        for i, width in enumerate(cm):
            self.auto.append(f'<style:style style:name="{name}.{i}" style:family="table-column"><style:table-column-properties style:column-width="{width:.3f}cm"/></style:style>')
        out = [f'<table:table table:name="{name}" table:style-name="{name}">']
        out += [f'<table:table-column table:style-name="{name}.{i}"/>' for i in range(len(cm))]

        def row_xml(cells, style: str, strong: tuple) -> str:
            parts = []
            for i, cell in enumerate(cells):
                text = escape(str(cell))
                if i in strong and text:
                    text = f'<text:span text:style-name="B">{text}</text:span>'
                parts.append(f'<table:table-cell table:style-name="{style}" office:value-type="string"><text:p text:style-name="Cell">{text}</text:p></table:table-cell>')
            return "<table:table-row>" + "".join(parts) + "</table:table-row>"

        for cells, style, strong in ([(header, "CellHead", tuple(range(len(cm))))] if header else []) + [(r, "CellBody", bold) for r in rows]:
            cells = list(cells) + [""] * (len(cm) - len(cells))
            out.append(row_xml(cells, style, strong))
            self.units += 0.1 + max(wrapped_lines(str(cell), (width - 0.16) * _PT - 1.0, bold=i in strong) for i, (cell, width) in enumerate(zip(cells, cm)))
        out.append("</table:table>")
        self.body.append("".join(out))
        self.units += 0.25

    # ------------------------------------------------------------------ #

    def content(self) -> str:
        auto = (
            '<style:style style:name="B" style:family="text"><style:text-properties fo:font-weight="bold"/></style:style>'
            '<style:style style:name="CellBody" style:family="table-cell"><style:table-cell-properties fo:padding-top="0.01cm" fo:padding-bottom="0.01cm" fo:padding-left="0.08cm" fo:padding-right="0.08cm" fo:border-left="none" fo:border-right="none" fo:border-top="none" fo:border-bottom="0.5pt solid #b5b5b5"/></style:style>'
            '<style:style style:name="CellHead" style:family="table-cell"><style:table-cell-properties fo:padding-top="0.01cm" fo:padding-bottom="0.01cm" fo:padding-left="0.08cm" fo:padding-right="0.08cm" fo:border-left="none" fo:border-right="none" fo:border-top="none" fo:border-bottom="0.75pt solid #000000"/></style:style>'
            + "".join(self.auto))
        return (f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-content {_NS}>{_FONTS}<office:automatic-styles>{auto}</office:automatic-styles>'
                f'<office:body><office:text>{"".join(self.body)}</office:text></office:body></office:document-content>')

    @staticmethod
    def styles() -> str:
        font = 'style:font-name="Arial" fo:font-family="Arial" style:font-family-generic="swiss" fo:font-size="10pt" fo:language="en" fo:country="US"'

        def paragraph(name: str, properties: str, text: str = "") -> str:
            return (f'<style:style style:name="{name}" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties {properties}/>'
                    + (f"<style:text-properties {text}/>" if text else "") + "</style:style>")

        return (
            f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-styles {_NS}>{_FONTS}<office:styles>'
            f'<style:default-style style:family="paragraph"><style:paragraph-properties fo:orphans="2" fo:widows="2" style:writing-mode="lr-tb"/><style:text-properties {font}/></style:default-style>'
            f'<style:default-style style:family="table"><style:table-properties table:border-model="collapsing"/></style:default-style>'
            f'<style:style style:name="Standard" style:family="paragraph" style:class="text"><style:paragraph-properties fo:margin-top="0cm" fo:margin-bottom="0cm" fo:line-height="100%"/><style:text-properties {font}/></style:style>'
            + paragraph("Title", 'fo:margin-top="0cm" fo:margin-bottom="0.06cm" fo:keep-with-next="always"', 'fo:font-size="13pt" fo:font-weight="bold"')
            + paragraph("Heading", 'fo:margin-top="0.16cm" fo:margin-bottom="0.04cm" fo:keep-with-next="always"', 'fo:font-weight="bold"')
            + paragraph("Body", 'fo:margin-top="0cm" fo:margin-bottom="0.05cm"')
            + paragraph("Bullet", 'fo:margin-left="0.35cm" fo:text-indent="-0.35cm" fo:margin-top="0cm" fo:margin-bottom="0.05cm"')
            + paragraph("Cell", 'fo:margin-top="0cm" fo:margin-bottom="0cm"')
            + '</office:styles><office:automatic-styles><style:page-layout style:name="pm1"><style:page-layout-properties fo:page-width="21cm" fo:page-height="29.7cm" style:print-orientation="portrait" '
              'fo:margin-top="1.27cm" fo:margin-bottom="1.27cm" fo:margin-left="1.27cm" fo:margin-right="1.27cm"/></style:page-layout></office:automatic-styles>'
              '<office:master-styles><style:master-page style:name="Standard" style:page-layout-name="pm1"/></office:master-styles></office:document-styles>')

    def save(self, path: Path, title: str = "") -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now().replace(microsecond=0).isoformat()
        meta = (f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-meta {_NS}><office:meta><dc:title>{escape(title)}</dc:title>'
                f'<meta:generator>aXqua</meta:generator><meta:creation-date>{now}</meta:creation-date><dc:date>{now}</dc:date></office:meta></office:document-meta>')
        manifest = ('<?xml version="1.0" encoding="UTF-8"?>\n<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
                    f'<manifest:file-entry manifest:full-path="/" manifest:version="1.2" manifest:media-type="{_MIME}"/>'
                    + "".join(f'<manifest:file-entry manifest:full-path="{name}" manifest:media-type="text/xml"/>' for name in ("content.xml", "styles.xml", "meta.xml"))
                    + "</manifest:manifest>")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(zipfile.ZipInfo("mimetype"), _MIME, compress_type=zipfile.ZIP_STORED)     # first and stored, as the format asks
            archive.writestr("META-INF/manifest.xml", manifest)
            archive.writestr("content.xml", self.content())
            archive.writestr("styles.xml", self.styles())
            archive.writestr("meta.xml", meta)
        return path


# --------------------------------------------------------------------------- #
# what every kind of survey shares
# --------------------------------------------------------------------------- #


def _g(value, digits: int = 2) -> str:
    """A number as short as it reads: ``1.2``, ``40``, ``0.35``."""
    try:
        return f"{round(float(value), digits):g}"
    except (TypeError, ValueError):
        return str(value)


def _count(number, noun: str) -> str:
    """``1 patch``, ``3 patches``."""
    number = int(number or 0)
    plural = noun[:-1] + "ies" if noun.endswith("y") else noun + ("es" if noun.endswith(("ch", "s", "x")) else "s")
    return f"{number} {noun if number == 1 else plural}"


def _span(row: dict, several_days: bool) -> str:
    clock = row["start"] if row["start"] == row["end"] else f"{row['start']}-{row['end']}"
    return f"D{row['day']} {clock}" if several_days else clock


_SHORT = {"pressure": "Pressure adjustment of the probe", "long break": "Long break"}
_GROUPS = {"ground": ("measure", "Verticals"), "temperature": ("measure", "Points"), "airborne": ("hover", "Stations")}


def _label(kind: str, what: str) -> str:
    return what.split(":")[0].split()[-1] if kind == "airborne" else what.split()[-1]


def timetable_rows(kind: str, plan: dict, *, condensed: bool = False) -> list[tuple[str, str]]:
    """``(time, step)`` rows of the timetable. *condensed* joins the points measured in a row into one step."""
    timeline = plan.get("timeline") or []
    several_days = len({r["day"] for r in timeline}) > 1
    several = len({r.get("surveyor", 1) for r in timeline}) > 1
    merge, plural = _GROUPS.get(kind, ("", ""))
    rows: list[tuple[str, str]] = []
    run: list[dict] = []

    def who(row: dict) -> str:
        return f"S{row['surveyor']}: " if several else ""

    def flush() -> None:
        if not run:
            return
        first, last = run[0], run[-1]
        if len(run) == 1:
            rows.append((_span(first, several_days), who(first) + first["what"]))
        else:
            tail = ": hover, with the flights between" if kind == "airborne" else ", with the walks between"
            rows.append((_span({"day": first["day"], "start": first["start"], "end": last["end"]}, several_days), f"{who(first)}{plural} {_label(kind, first['what'])} to {_label(kind, last['what'])}{tail}"))
        run.clear()

    for row in timeline:
        if condensed and row["kind"] == merge and (not run or (run[-1]["day"], run[-1].get("surveyor", 1)) == (row["day"], row.get("surveyor", 1))):
            run.append(row)
            continue
        flush()
        if condensed and row["kind"] == merge:
            run.append(row)
            continue
        rows.append((_span(row, several_days), who(row) + _SHORT.get(row["kind"], row["what"])))
    flush()
    return rows


def overview_rows(kind: str, plan: dict) -> list[list[str]]:
    """The timetable as one row per surveyor and working day: ``[who, from-to, what is measured, breaks and adjustments]``."""
    timeline = plan.get("timeline") or []
    several_days = len({r["day"] for r in timeline}) > 1
    several = len({r.get("surveyor", 1) for r in timeline}) > 1
    merge, plural = _GROUPS.get(kind, ("", "Steps"))
    groups: dict[tuple, list[dict]] = {}
    for row in timeline:
        groups.setdefault((row.get("surveyor", 1), row["day"]), []).append(row)
    out = []
    for (surveyor, day), rows in sorted(groups.items()):
        measured = [row for row in rows if row["kind"] == merge]
        if not measured:
            what = "; ".join(row["what"] for row in rows if row["kind"] not in ("start", "end", "break", "long break", "pressure", "return"))
        elif len(measured) == 1:
            what = measured[0]["what"]
        else:
            what = f"{plural} {_label(kind, measured[0]['what'])} to {_label(kind, measured[-1]['what'])}"
        stops = [f"{'pressure' if row['kind'] == 'pressure' else 'battery' if row['kind'] == 'battery' else 'break'} {row['start']}" for row in rows if row["kind"] in ("break", "long break", "pressure", "battery")]
        who = " ".join(text for text in (f"S{surveyor}" if several else "", f"Day {day}" if several_days or not several else "") if text)
        out.append([who, f"{rows[0]['start']}-{rows[-1]['end']}", what, ", ".join(stops)])
    return out


def _timetable(sheet: Sheet, kind: str, plan: dict, level: int) -> None:
    rows = timetable_rows(kind, plan, condensed=level >= 1)
    if not rows:
        return
    sheet.heading("Timetable")
    if level >= 2 and len(rows) > OVERVIEW_FROM:     # too many steps for the page: one row per surveyor and day, never a cut list
        overview = overview_rows(kind, plan)
        if len(overview) > OVERVIEW_FROM:
            left = len(overview) - (OVERVIEW_FROM - 1)
            overview = overview[:OVERVIEW_FROM - 1] + [["", "", f"and {left} more working days (see survey-plan.json)", ""]]
        sheet.table(overview, [1.9, 2.4, 6.2, 7.96], header=["Who" if "S" in overview[0][0] else "Day", "Time", "Measures", "Stops (start)"])
        return
    if len(rows) <= 9:
        sheet.table([list(r) for r in rows], [2.6, 15.86], header=["Time", "Step"])
        return
    half = math.ceil(len(rows) / 2)
    left, right = rows[:half], rows[half:] + [("", "")] * (2 * half - len(rows))
    sheet.table([[*a, *b] for a, b in zip(left, right)], [2.5, 6.73, 2.5, 6.73], header=["Time", "Step", "Time", "Step"])


def _facts(sheet: Sheet, facts: list[tuple[str, str]]) -> None:
    facts = [(k, v) for k, v in facts if v]
    half = math.ceil(len(facts) / 2)
    left, right = facts[:half], facts[half:] + [("", "")] * (2 * half - len(facts))
    sheet.heading("The plan in numbers")
    sheet.table([[*a, *b] for a, b in zip(left, right)], [2.9, 6.33, 2.9, 6.33], bold=(0, 2))


def _days_used(plan: dict) -> int:
    return max(int((plan.get("summary") or {}).get("days") or 1), 1)


def _daylight(sheet: Sheet, plan: dict, context: dict) -> None:
    conditions, settings = context.get("conditions") or {}, context["settings"]
    days = conditions.get("days") or []
    sheet.heading("Daylight and weather")
    if not days:
        start = settings.time.start or "00:00"
        sheet.para(f"No survey day is set, so the plan has no sunrise, sunset or weather. Its clock starts at {start}" + ("" if settings.time.start else " with the first step, because no start time is set either") + f"; a working day has at most {_g(settings.time.day_hours)} h.")
        return
    for i, day in enumerate(days[:min(_days_used(plan), 3)]):
        text = f"sunrise {day.get('sunrise_text') or 'unknown'}, sunset {day.get('sunset_text') or 'unknown'}"
        if day.get("weather") or day.get("t_max") is not None:
            parts = [str(day.get("weather") or "").strip()]
            if day.get("t_min") is not None and day.get("t_max") is not None:
                parts.append(f"{_g(day['t_min'], 0)} to {_g(day['t_max'], 0)} °C")
            if day.get("precipitation") is not None:
                chance = f" (probability {_g(day['probability'], 0)} %)" if day.get("probability") is not None else ""
                parts.append(f"precipitation {_g(day['precipitation'], 1)} mm{chance}")
            if day.get("wind") is not None:
                gusts = f", gusts up to {_g(day['gusts'], 0)} m/s" if day.get("gusts") is not None else ""
                parts.append(f"wind up to {_g(day['wind'], 0)} m/s{gusts}")
            text += ". Forecast: " + ", ".join(p for p in parts if p)
        sheet.para(text + ".", f"{day.get('date', '')}:" if day.get("date") else f"Day {i + 1}:")
    tail = []
    suggestion = conditions.get("suggestion") or {}
    if suggestion.get("start"):
        tail.append(f"Suggested working window of the first day: {suggestion['start']} to {suggestion['end']} ({suggestion.get('reason', '')}).")
    elif suggestion.get("reason"):
        tail.append(suggestion["reason"][:1].upper() + suggestion["reason"][1:] + ".")
    if conditions.get("timezone"):
        tail.append(f"Times are in {conditions['timezone']}.")
    if not conditions.get("weather"):
        tail += [note[:1].upper() + note[1:] + "." for note in conditions.get("notes") or [] if "forecast" in note]
    if tail:
        sheet.para(" ".join(tail))


def _network(sheet: Sheet, context: dict, extra: str = "") -> None:
    cover = (context.get("conditions") or {}).get("coverage") or {}
    sheet.heading("Mobile network and DGPS corrections")
    statement = cover.get("statement") or "Nothing is known about the mobile network at the reach. The surveyors should check the DGPS network coverage before the survey."
    sheet.para(statement + (" " + extra if extra else ""))


def _warnings(sheet: Sheet, plan: dict, level: int) -> None:
    warnings = list(plan.get("warnings") or [])
    if not warnings:
        return
    keep = len(warnings) if level < 4 else 1
    sheet.heading("Warnings of the plan")
    for text in warnings[:keep]:
        named = text.split(" ", 1)[0].endswith(":")       # the name of a parameter stays as it is written
        sheet.bullet((text if named else text[:1].upper() + text[1:]) + ("" if text.endswith((".", "?")) else "."))
    if keep < len(warnings):
        sheet.bullet(f"{len(warnings) - keep} more in survey-plan.json.")


def _hints(sheet: Sheet, hints: list[str], level: int) -> None:
    keep = (len(hints), len(hints), len(hints), max(len(hints) - 2, 3), 3, 2)[min(level, LEVELS - 1)]
    sheet.heading("In the field")
    for text in hints[:keep]:
        sheet.bullet(text)


def _foot(sheet: Sheet, kind: str, context: dict, level: int) -> None:
    sources = (context.get("conditions") or {}).get("sources") or []
    made = context.get("made") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    text = f"Planned with aXqua {context.get('version', '')} on {made} on the flow field of {context.get('source', 'the model')} (printout at t = {_g(context.get('time', 0), 0)} s); the plan holds for the discharge of that run. Map: {context.get('map', f'survey-map-{kind}.pdf')}. Layers for QGIS and the field tablet: {context.get('gpkg', 'survey-plan.gpkg')}."
    if sources and level < LEVELS - 1:
        text += " Sources: " + "; ".join(sources) + "."
    sheet.para(text)


def _breaks(settings: SurveySettings) -> str:
    t = settings.time
    parts = []
    if t.short_break_minutes > 0 and t.short_break_every_h > 0:
        parts.append(f"{_g(t.short_break_minutes)} min every {_g(t.short_break_every_h)} h")
    if t.long_break_minutes > 0 and t.long_break_every_h > 0:
        parts.append(f"{_g(t.long_break_minutes)} min every {_g(t.long_break_every_h)} h")
    daylight = ", and not past sunset" if t.use_daylight else ""
    return ("Breaks: " + " and ".join(parts) + ". " if parts else "No breaks are planned. ") + f"A working day has at most {_g(t.day_hours)} h{daylight}."


def _period(summary: dict) -> str:
    per_day = summary.get("per_day")
    if per_day:
        if len(per_day) == 1:
            return f"{per_day[0]['start']} to {per_day[0]['end']}"
        if len(per_day) > 3:
            return f"{len(per_day)} days, from {per_day[0]['start']} on day 1 to {per_day[-1]['end']} on day {per_day[-1]['day']}"
        return f"{len(per_day)} days: " + ", ".join(f"day {d['day']} {d['start']} to {d['end']}" for d in per_day)
    if summary.get("start"):
        days = int(summary.get("days") or 1)
        return f"{summary['start']} to {summary['end']}" + (f" (on day {days})" if days > 1 else "")
    return ""


def _served(rows: list[dict]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        for name in str(row.get("serves") or "").split(","):
            if name.strip():
                counts[name.strip()] = counts.get(name.strip(), 0) + 1
    return ", ".join(f"{name} ({count})" for name, count in counts.items())


# --------------------------------------------------------------------------- #
# the four kinds
# --------------------------------------------------------------------------- #


def _ground(sheet: Sheet, plan: dict, context: dict, level: int) -> None:
    s, settings = plan.get("summary") or {}, context["settings"]
    g = settings.ground
    rovers = int(s.get("dgps_rovers") or 1)
    _facts(sheet, [
        ("Verticals", f"{s.get('verticals', 0)}: {s.get('one_depth', 0)} at one depth, {s.get('two_depths', 0)} at two, {s.get('three_depths', 0)} at three ({s.get('readings', 0)} velocity readings)"),
        ("Time", _period(s)),
        ("Surveyors", f"{s.get('surveyors', 1)} with a probe, {rovers} with a DGPS rover"),
        ("Measuring", fmt_duration(s.get("measuring_minutes", 0))),
        ("Walking", f"{fmt_duration(s.get('walking_minutes', 0))} over {_g(s.get('walk_length', 0) / 1000.0)} km, {_g(s.get('walk_in_water', 0) / 1000.0)} km of it in water"),
        ("Breaks", f"{fmt_duration(s.get('break_minutes', 0))}; {_count(s.get('pressure_adjustments', 0), 'pressure adjustment')} of {_g(g.pressure_minutes)} min"),
        ("Deepest, fastest", f"{_g(s.get('max_depth', 0))} m, {_g(s.get('max_velocity', 0))} m/s at a vertical"),
        ("Verticals for", _served(plan.get("points") or [])),
    ])
    _timetable(sheet, "ground", plan, level)
    _daylight(sheet, plan, context)
    lag = float(s.get("dgps_lag_minutes") or 0.0)
    _network(sheet, context, f"A second surveyor records the position of every vertical with the DGPS rover while the velocity is measured ({_g(g.dgps_seconds)} s per position), so the DGPS costs time only where it loses its data reception." + (f" With one rover for up to {g.surveyors_per_dgps} probes, the rover reaches a vertical up to {_g(lag, 0)} min after its surveyor." if lag >= 1 else ""))
    sheet.heading("Assumptions")
    limits = ", ".join(f"up to {_g(d)} m depth not faster than {_g(v)} m/s" for d, v in sorted(g.velocity_limits))
    against = ", ".join(f"{_g(v)} km/h where deeper than {_g(d)} m" for d, v in sorted(g.speed_against_current))
    sheet.para(f"not deeper than {_g(g.max_depth)} m; {limits}.", "Wading:")
    sheet.para(f"{_g(g.speed_land)} km/h on land, {_g(g.speed_with_current)} km/h in water with the current; against the current {against}.", "Walking:")
    sheet.para(f"{_g(g.time_per_depth)} s per measured depth, {_g(g.time_probe_height)} s to set the probe to each height and {_g(g.time_water_depth)} s for the water depth. One depth ({', '.join(_g(h) + ' h' for h in g.heights_one)}) below {_g(g.depth_two_points)} m of water, two ({', '.join(_g(h) + ' h' for h in g.heights_two)}) from {_g(g.depth_two_points)} m, three ({', '.join(_g(h) + ' h' for h in g.heights_three)}) from {_g(g.depth_three_points)} m; h is the water depth, and the heights count from the riverbed.", "At a vertical:")
    sheet.para(f"The pressure sensor of the probe is adjusted to the atmosphere at the start and every {_g(g.pressure_interval_h)} h ({_g(g.pressure_minutes)} min, star on the map). {_breaks(settings)} No vertical lies closer than 15 mesh nodes to the inflow or the outflow of the model.", "Day:")
    _warnings(sheet, plan, level)
    _hints(sheet, [
        "Measure where the map shows the vertical and record the position of the probe, not of the bank. Where the water is deeper or faster than planned, do not wade: move the vertical and record where it was measured.",
        "Read the gauge or note the discharge at the start and at the end. The plan and the wadeable area hold for the discharge of the model run.",
        "The probe records the measuring depth from the water surface; aXqua converts it into the height above the riverbed.",
        "Hold the probe upstream of your legs, facing the flow, and stand still while it averages.",
        "Charge the rover and the data logger, and take the correction service login into the field.",
    ], level)


def _airborne(sheet: Sheet, plan: dict, context: dict, level: int) -> None:
    s, settings = plan.get("summary") or {}, context["settings"]
    a = settings.airborne
    above = s.get("above_takeoff")
    height = f"{_g(s.get('altitude', a.altitude))} m above the water surface"
    if above:
        height += f" ({_g(above[0], 1)} m above the take-off point)" if abs(above[1] - above[0]) < 0.05 else f" ({_g(above[0], 1)} to {_g(above[1], 1)} m above the take-off point)"
    else:
        height += "; the level of the take-off point is not known"
    _facts(sheet, [
        ("Hover stations", f"{s.get('stations', 0)}, each filmed for {_g(a.hover_seconds)} s"),
        ("Time", _period(s)),
        ("Flight height", height),
        ("Flight", f"{_g(s.get('flight_minutes', 0), 1)} min in the air, {_count(s.get('batteries', 1), 'battery')} of {_g(a.battery_minutes)} min, {_g(s.get('route_length', 0) / 1000.0)} km"),
        ("Frame on the water", f"{_g(s.get('frame_width', 0), 1)} m x {_g(s.get('frame_height', 0), 1)} m, {_g(s.get('gsd_cm', 0))} cm per pixel"),
        ("Camera", f"straight down, {a.width_px} x {a.height_px} pixels, {_g(a.fps)} frames/s, {_g(a.hfov_deg)} deg horizontal field of view"),
        ("Tracers", f"{_g(a.tracer_size * 100)} cm, {_g(s.get('tracer_px', 0), 1)} pixels in the video"),
        ("Farthest station", f"{_g(s.get('longest_distance', 0), 0)} m from the take-off point; about {_g(s.get('video_gb', 0), 1)} GB of video"),
        ("Stations for", ", ".join(f"{name} ({count})" for name, count in (s.get("stations_of") or {}).items())),
        ("Filmed", f"{_g(100 * float(s.get('filmed_share') or 0), 0)} % of the wetted area of the model"),
    ])
    stations = plan.get("stations") or []
    several_days = len({st["day"] for st in stations}) > 1
    if stations:
        sheet.heading("Drone positions")
        if above:
            last, value = "Above take-off", lambda st: (_g(st["above_takeoff"], 1) + " m") if st.get("above_takeoff") is not None else ""
        else:
            last, value = "Water level", lambda st: (f"{float(st['water_level']):.2f} m") if st.get("water_level") is not None else ""
        cells = [[str(st["station"]), _span(st, several_days), f"{st['x']:.0f}", f"{st['y']:.0f}", f"{_g(st['heading'], 0)} deg", value(st), str(st["battery"])] for st in stations]
        if len(cells) <= 10 and level == 0:
            sheet.table(cells, [1.0, 2.6, 2.4, 2.6, 2.0, 3.3, 1.6], header=["No.", "Hover", "Easting", "Northing", "Heading", last, "Battery"])
        else:
            short = [[c[0], c[1].rsplit("-", 1)[0], c[2], c[3], c[4]] for c in cells]
            half = math.ceil(len(short) / 2)
            right = short[half:] + [[""] * 5] * (2 * half - len(short))
            sheet.table([[*x, *y] for x, y in zip(short[:half], right)], [1.0, 2.2 if several_days else 1.7, 2.0, 2.2, 1.9] * 2, header=["No.", "Start", "Easting", "Northing", "Heading"] * 2)
        sheet.para(f"Coordinates in EPSG:{context.get('epsg', '')}. Heading: the compass direction of the top of the frame, which looks downstream (layer drone_positions).")
    if level < 2:
        _timetable(sheet, "airborne", plan, 1)
    elif level < 4:                                  # the table of the positions has the hover times: the other steps fit a sentence
        steps = [f"{time} {step[:1].lower() + step[1:]}" for time, step in timetable_rows("airborne", {"timeline": [row for row in plan.get("timeline") or [] if row["kind"] != "hover"]})]
        if steps:
            sheet.para("; ".join(steps) + ".", "Timetable:")
    _daylight(sheet, plan, context)
    _network(sheet, context, "The drone flies without the mobile network; the DGPS of the ground control points needs it.")
    sheet.heading("Assumptions")
    sheet.para(f"A DJI camera drone hovers at every station for {_g(a.hover_seconds)} s with the camera straight down and flies between the stations at {_g(a.cruise_speed)} m/s. One battery gives {_g(a.battery_minutes)} min of flight; the plan lands for a new one before that. The flight height counts from the water surface, because the scale of the video does. Every calibration parameter gets {a.stations_per_parameter} stations in its water, at most {a.max_stations} in all. {_breaks(settings)}")
    _warnings(sheet, plan, level)
    _hints(sheet, [
        "Switch on the video captions of the DJI drone: pXv reads the position and the height of the camera from the flight record (SRT file) of the video. Without it, lay out at least four ground control points per frame on both banks, at the level of the water surface, and survey them with the DGPS.",
        "Film a calibration panel (checkerboard) with the resolution and the zoom of the flight before take-off, so that the lens distortion can be corrected.",
        ("Set the height above the take-off point that the plan lists, or check the height above the water on the first station" if above else "The controller shows the height above the take-off point: add the height of the take-off point above the water, or check the flight height on the first station") + f"; an error of 2 m at {_g(s.get('altitude', a.altitude))} m is {_g(200.0 / max(float(s.get('altitude', a.altitude)), 1.0), 0)} % in every velocity.",
        "Seed tracers upstream of the frame where the surface carries no foam or debris, across the whole width, and keep both banks in the frame: the video is stabilized on them.",
        "The video shows the velocity of the water surface, which is faster than the depth-averaged velocity of a 2D model. A few verticals measured by wading in the same frames give the ratio.",
        f"Do not fly in rain or in gusts above {_g(GUST_LIMIT_DRONE)} m/s, keep the drone in sight, and check the airspace and the permits of the reach (nature reserves, people, bridges, power lines).",
    ], (0, 0, 0, 3, 4, 5)[min(level, LEVELS - 1)])


def _kayak(sheet: Sheet, plan: dict, context: dict, level: int) -> None:
    s, settings = plan.get("summary") or {}, context["settings"]
    k = settings.kayak
    tracks = plan.get("tracks") or []
    spacing = f"{_g(min(t['spacing_min'] for t in tracks), 1)} to {_g(max(t['spacing_max'] for t in tracks), 1)} m apart" if tracks else ""
    kayakers = int(s.get("kayakers") or k.kayakers)
    _facts(sheet, [
        ("Kayakers", f"{kayakers}" + (" (each reach is floated twice)" if kayakers == 1 else ", who float together for safety")),
        ("Time", _period(s)),
        ("Floats", f"{s.get('floats', 0)} on {_count(s.get('lines', 0), 'drift line')}, {_g(s.get('length', 0) / 1000.0)} km each"),
        ("One float", f"about {fmt_duration(s.get('float_minutes_max', 0))} of drifting"),
        ("DGPS positions", f"one every {_g(k.log_interval)} s, about {s.get('fixes', 0)} in all, {spacing}"),
        ("Velocity", f"up to {_g(s.get('velocity_max', 0))} m/s on the drift lines; limit {_g(k.max_velocity)} m/s"),
        ("Shallowest", f"{_g(s.get('depth_min', 0))} m on the drift lines (a kayak needs {_g(k.min_depth)} m)"),
        ("Avoid", f"{_count(s.get('avoid_patches', 0), 'patch')} faster than {_g(k.max_velocity)} m/s, {_g(s.get('avoid_area', 0), 0)} m², hatched on the map" if s.get("avoid_patches") else f"no patch is faster than {_g(k.max_velocity)} m/s"),
        ("Positions in", ", ".join(f"{name} ({count})" for name, count in (s.get("fixes_in") or {}).items())),
    ])
    _timetable(sheet, "kayak", plan, level)
    _daylight(sheet, plan, context)
    _network(sheet, context, "Only a DGPS that loses its connection delays a float: wait in an eddy until the fix is back, and note the time.")
    sheet.heading("Assumptions")
    sheet.para(f"A kayak that is not paddled drifts at the velocity of the water surface, so a float takes the length of its drift line over the velocity of the model along it. The DGPS on the kayak logs a position every {_g(k.log_interval)} s; only the plan position (x, y) and the distance between two consecutive positions matter, which is the velocity over that interval. Breaks: {_g(k.break_minutes)} min every {_g(k.break_every_h)} h of drifting" + (f"; carrying the kayaks back for another float takes {_g(k.shuttle_minutes)} min" if int(s.get("rounds") or 1) > 1 else "") + f". Patches faster than {_g(k.max_velocity)} m/s are avoided. Method: Barker et al. (2018), River Research and Applications 34(2), doi 10.1002/rra.3238.")
    _warnings(sheet, plan, level)
    _hints(sheet, [
        "Mount the DGPS antenna on the kayak, clear of the paddler, and check the fix and the logging interval at the put-in.",
        "Paddle with light strokes only, to keep the kayak on its line and to take it up to or down to the velocity of the water, above all where a riffle runs into a glide. Then let it drift: the kayak floats at flow velocity.",
        "The method pays on long reaches. It needs a pick-up at the take-out and the transport of the kayaks, so arrange both before the survey.",
        "Take enough food and drink, a life jacket and a helmet, and scout the hatched patches from the bank before the first float.",
        "The kayak measures the surface velocity. Velocities from an ADV or an ADCP, or other ground truth, at a few points of the reach give the ratio to the depth-averaged velocity of the model (Barker et al. found 0.71 at their site) and support the validation.",
    ], level)


def _temperature(sheet: Sheet, plan: dict, context: dict, level: int) -> None:
    s, settings = plan.get("summary") or {}, context["settings"]
    t = settings.temperature
    loggers = (s.get("method") or t.method) == "loggers"
    by_class, share = s.get("by_class") or {}, s.get("area_share") or {}
    _facts(sheet, [
        ("Method", f"{s.get('points', 0)} fixed loggers" if loggers else f"mobile probe, {s.get('points', 0)} readings"),
        ("Time", _period(s)),
        ("Slow water", f"slower than {_g(t.slow_velocity)} m/s"),
        ("Shallow water", f"shallower than {_g(t.shallow_depth)} m"),
        ("Points by water", ", ".join(f"{name}: {count}" for name, count in by_class.items())),
        ("Wetted area", ", ".join(f"{name}: {_g(100 * float(value), 0)} %" for name, value in share.items())),
        ("Walking", f"{fmt_duration(s.get('walking_minutes', 0))} over {_g(s.get('walk_length', 0) / 1000.0)} km" if s.get("walk_length") else ""),
        ("Not by wading", f"{_count(s.get('not_on_foot', 0), 'point')} from a boat or on a line from the bank" if s.get("not_on_foot") else ""),
    ])
    _timetable(sheet, "temperature", plan, level)
    _daylight(sheet, plan, context)
    _network(sheet, context, "The position of every point is recorded with the DGPS.")
    sheet.heading("Assumptions")
    work = f"Installing a logger takes {_g(t.install_minutes)} min; the same walk collects the loggers at the end of the campaign." if loggers else f"The probe settles for {_g(t.reading_seconds)} s at every point."
    sheet.para(f"The wetted reach of the model is sorted into slow or fast water (limit {_g(t.slow_velocity)} m/s) and shallow or deep water (limit {_g(t.shallow_depth)} m). Every kind of water that exists gets at least one point; the others are dealt by wetted area. {work} The walk follows the wading rules of a ground-based survey (not deeper than {_g(settings.ground.max_depth)} m). {_breaks(settings)}")
    _warnings(sheet, plan, level)
    _hints(sheet, [
        "Slow, shallow water warms in the sun within an hour, while deep, fast water carries the temperature of the river upstream: measure both kinds on the same day, and note the time of every reading.",
        ("Shade every logger from direct sun (a white tube open to the flow), fix it to a stone or a steel bar, and note its depth below the surface." if loggers else "Hold the probe in flowing water upstream of your legs and wait until the reading is steady."),
        ("Set all loggers to the same clock and the same interval, and leave them together in a bucket of water for an hour before and after the campaign to compare them." if loggers else "Compare the probe with a reference thermometer before and after the round."),
        "A slow, deep pool can be stratified or fed by groundwater: measure near the bed and near the surface there.",
        "Record the position of every point with the DGPS, and take a photo of the place.",
    ], level)


_WRITERS = {"ground": _ground, "airborne": _airborne, "kayak": _kayak, "temperature": _temperature}


def build(kind: str, plan: dict, context: dict, level: int = 0) -> Sheet:
    """The factsheet of one kind of survey at a level of detail (0 is everything)."""
    sheet = Sheet()
    conditions = context.get("conditions") or {}
    where = [context.get("case") or "", conditions.get("date") or "no survey day set"]
    if conditions.get("lat") is not None:
        where.append(f"{abs(conditions['lat']):.4f}° {'N' if conditions['lat'] >= 0 else 'S'}, {abs(conditions['lon']):.4f}° {'E' if conditions['lon'] >= 0 else 'W'}")
    sheet.title(f"Survey plan: {KIND_TITLES[kind]}", ", ".join(w for w in where if w))
    _WRITERS[kind](sheet, plan, context, level)
    _foot(sheet, kind, context, level)
    return sheet


def write_factsheet(path: Path, kind: str, plan: dict, context: dict) -> dict:
    """Write the one-page factsheet of *kind*. Returns ``{"path", "level", "units", "fits"}``; a level above zero says that content was left out to keep the page, and ``fits`` is False where even the last level counts more lines than the page holds."""
    context = dict(context)
    context["settings"] = context.get("settings") or SurveySettings()
    for level in range(LEVELS):
        sheet = build(kind, plan, context, level)
        if sheet.units <= PAGE_UNITS:
            break
    sheet.save(path, f"Survey plan: {KIND_TITLES[kind]}")
    return {"path": str(path), "level": level, "units": round(sheet.units, 1), "fits": sheet.units <= PAGE_LINES}


__all__ = ["LEVELS", "PAGE_LINES", "PAGE_UNITS", "Sheet", "build", "overview_rows", "text_width", "timetable_rows", "wrapped_lines", "write_factsheet"]
