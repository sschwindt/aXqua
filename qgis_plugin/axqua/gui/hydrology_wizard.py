"""The data wizard of a hydrology case: rain, soil, moisture, wind and sun, outlet.

One window with a tab per kind of data, visited in any order, with *Back* and *Next*
walking through them like the installation wizards. Every tab says first where good
data come from (the national meteorological agency, listed for the country of the
catchment) and then offers the routes aXqua has:

* **Rain**: import a record (gauge, radar, agency table), build a design storm from a
  depth-duration table (KOSTRA-DWD, NOAA Atlas 14, ...), or download a past event from
  Open-Meteo (ERA5), which underestimates short intensities;
* **Soil**: one's own soil map and table, one texture class for the whole catchment,
  or SoilGrids textures; sealed surfaces from one's own layer or ESA WorldCover;
* **Initial moisture**: a number, or ERA5-Land at the start of the event;
* **Wind and sun**: the weather series and what of it the run uses;
* **Outlet**: the condition of the outflow lines and the numerical kernel.

Nothing is written into the case file before *Save*; the files a route writes go into
``user-sources/hydrology`` at once. The window decides nothing: every route is a call
of ``axqua hydrology`` and its answer says which case settings point at the file.
"""

from __future__ import annotations

import html
from pathlib import Path

from qgis.PyQt.QtCore import QDateTime
from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QDateTimeEdit, QDialog,
                                 QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                                 QHBoxLayout, QLabel, QLineEdit, QPushButton,
                                 QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import help as help_pages
from .case_editor import stored_path

TABS = ("Rain", "Soil", "Initial moisture", "Wind and sun", "Outlet", "Save")

AGENCY_FIRST = (
    "<b>Check your national meteorological agency first.</b> Its rain gauges and radar "
    "resolve the minutes and kilometers of a storm, and its design rainfall is what "
    "engineering practice expects. The global data sets below fit any catchment, "
    "roughly.")

REANALYSIS = (
    "A reanalysis (ERA5) spreads a storm over cells of 10 to 30 km and an hour and "
    "underestimates short intensities, 1-hour extremes by about 40 %. Use it where "
    "nothing better exists, or to look at the antecedent conditions.")

TEXTURES = ("sand", "loamy sand", "sandy loam", "loam", "silt loam", "sandy clay loam",
            "clay loam", "silty clay loam", "sandy clay", "silty clay", "clay")

OUTLETS = (
    ("normal_depth", "normal depth (recommended): the level each outflow section "
                     "needs for its discharge"),
    ("free", "free: nothing prescribed; only for a steep, supercritical outlet"),
    ("elevation", "elevation: the fixed level of a lake or river the catchment "
                  "drains into"),
)


def _label(text: str, *, rich: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    if rich:
        label.setOpenExternalLinks(True)
    return label


def _scrolling(body: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(body)
    return area


def portal_html(portals: dict, offers: str) -> str:
    """The portals that offer *offers*, national ones first, as HTML links."""
    rows = []
    for group in ("national", "global"):
        for item in portals.get(group) or []:
            if offers in (item.get("offers") or []):
                note = f" - {html.escape(item['note'])}" if item.get("note") else ""
                where = (f" ({html.escape(item.get('country_name', ''))})"
                         if group == "national" else "")
                rows.append(f"<li><a href=\"{html.escape(item['url'])}\">"
                            f"{html.escape(item['name'])}</a>{where}{note}</li>")
    return "<ul>" + "".join(rows) + "</ul>" if rows else ""


class HydrologyWizard(QDialog):
    """Set up the data of the hydrology case at *case*. *info* is the answer of
    ``axqua hydrology info``; the window asks for it again when it is empty."""

    def __init__(self, context, case: Path, info: dict | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.ctx = context
        self.client = context.client
        self.case = Path(case)
        self.info = dict(info or {})
        self.pending: dict[str, object] = {}       # dotted key -> value, for Save
        self.saved = False
        self.wrote = False                   # a download or an import wrote data files
        self.portals: dict = {}
        self._portal_labels: list[tuple[QLabel, str]] = []
        self.setWindowTitle(f"Hydrology data: {self.case.name}")
        self.resize(820, 720)
        self._build()
        self._load()

    # -- construction -------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        self.where = _label("")
        outer.addWidget(self.where)
        self.tabs = QTabWidget()
        for title, page in zip(TABS, (self._rain_page(), self._soil_page(),
                                      self._moisture_page(), self._weather_page(),
                                      self._outlet_page(), self._save_page())):
            self.tabs.addTab(_scrolling(page), title)
        self.tabs.currentChanged.connect(self._tab_changed)
        outer.addWidget(self.tabs, 1)
        self.status = _label("")
        outer.addWidget(self.status)
        buttons = QHBoxLayout()
        help_button = QPushButton("Help")
        help_button.clicked.connect(
            lambda: help_pages.open_window_help("hydrology-wizard"))
        buttons.addWidget(help_button)
        buttons.addStretch(1)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(
            lambda: self.tabs.setCurrentIndex(max(self.tabs.currentIndex() - 1, 0)))
        buttons.addWidget(self.back_button)
        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(
            lambda: self.tabs.setCurrentIndex(min(self.tabs.currentIndex() + 1,
                                                  len(TABS) - 1)))
        buttons.addWidget(self.next_button)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        outer.addLayout(buttons)
        self._tab_changed(0)

    def _portal_box(self, layout: QVBoxLayout, offers: str) -> None:
        layout.addWidget(_label(AGENCY_FIRST, rich=True))
        label = _label("Loading the list of data portals...", rich=True)
        self._portal_labels.append((label, offers))
        layout.addWidget(label)

    def _file_row(self, caption: str, filters: str) -> tuple[QLineEdit, QWidget]:
        line = QLineEdit()
        browse = QPushButton("...")
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(line, 1)
        row.addWidget(browse)

        def choose() -> None:
            chosen, _ = QFileDialog.getOpenFileName(self, caption,
                                                    str(self.case.parent), filters)
            if chosen:
                line.setText(chosen)
        browse.clicked.connect(choose)
        return line, holder

    def _rain_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._portal_box(layout, "rain")

        record = QGroupBox("A rain record (gauge, radar, agency table)")
        form = QFormLayout(record)
        self.rain_file, row = self._file_row("Rain record",
                                             "Tables (*.csv *.txt *.xlsx *.xls)")
        form.addRow("File", row)
        self.rain_units = QComboBox()
        self.rain_units.addItem("intensity, mm/h", "mm/h")
        self.rain_units.addItem("depth per interval, mm", "mm")
        form.addRow("Values are", self.rain_units)
        self.rain_stamp = QComboBox()
        self.rain_stamp.addItem("the end of the interval (agencies do this)", "end")
        self.rain_stamp.addItem("the start of the interval", "start")
        form.addRow("A time stamp is", self.rain_stamp)
        self.rain_start = QLineEdit()
        self.rain_start.setPlaceholderText("e.g. 2024-06-02T14:00 (empty: first row)")
        form.addRow("Time zero", self.rain_start)
        use = QPushButton("Use this record")
        use.clicked.connect(self.import_rain)
        form.addRow(use)
        layout.addWidget(record)

        design = QGroupBox("A design storm from a depth-duration table")
        form = QFormLayout(design)
        form.addRow(_label("Depths of one return period, from KOSTRA-DWD, NOAA Atlas "
                           "14 or the product of your country: duration in minutes "
                           "and depth in mm, as pairs or as a table."))
        self.design_pairs = QLineEdit()
        self.design_pairs.setPlaceholderText("5:9.8, 10:14.9, 15:18.3, 30:24.4, "
                                             "60:30.6")
        form.addRow("Duration:depth", self.design_pairs)
        self.design_file, row = self._file_row("Depth-duration table",
                                               "Tables (*.csv *.txt *.xlsx *.xls)")
        form.addRow("or a table", row)
        self.design_duration = QDoubleSpinBox()
        self.design_duration.setRange(5, 10080)
        self.design_duration.setValue(60)
        self.design_duration.setSuffix(" min")
        form.addRow("Storm duration", self.design_duration)
        self.design_step = QDoubleSpinBox()
        self.design_step.setRange(1, 1440)
        self.design_step.setValue(5)
        self.design_step.setSuffix(" min")
        form.addRow("Block length", self.design_step)
        self.design_kind = QComboBox()
        for value, text in (("euler2", "Euler type II (peak at 30 %, DWA-A 118)"),
                            ("alternating", "alternating block (peak in the middle)"),
                            ("block", "one block (constant intensity)")):
            self.design_kind.addItem(text, value)
        form.addRow("Distribution", self.design_kind)
        build = QPushButton("Build the design storm")
        build.clicked.connect(self.build_design)
        form.addRow(build)
        layout.addWidget(design)

        event = QGroupBox("A past event, downloaded (Open-Meteo, ERA5; global)")
        form = QFormLayout(event)
        form.addRow(_label(REANALYSIS))
        self.event_start = QDateTimeEdit(QDateTime.currentDateTimeUtc().addDays(-30))
        self.event_end = QDateTimeEdit(QDateTime.currentDateTimeUtc().addDays(-29))
        for edit in (self.event_start, self.event_end):
            edit.setDisplayFormat("yyyy-MM-dd HH:00")
            edit.setCalendarPopup(True)
        form.addRow("Start (UTC)", self.event_start)
        form.addRow("End (UTC)", self.event_end)
        fetch = QPushButton("Download rain and weather")
        fetch.clicked.connect(self.fetch_event)
        form.addRow(fetch)
        layout.addWidget(event)
        layout.addStretch(1)
        return page

    def _soil_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._portal_box(layout, "soil")

        own = QGroupBox("Your soil map")
        form = QFormLayout(own)
        self.soil_zones, row = self._file_row("Soil zones", "Layers (*.gpkg *.shp)")
        form.addRow("Soil zones (polygons)", row)
        self.soil_field = QLineEdit("Zone ID")
        form.addRow("Field of the zone number", self.soil_field)
        self.soil_table, row = self._file_row("Soil table", "Tables (*.csv *.xlsx)")
        form.addRow("Soil table", row)
        form.addRow(_label("The table has the columns zone_id, texture, ks_mm_h, "
                           "suction_m, theta_s, theta_i; what a row leaves empty comes "
                           "from its texture (Rawls et al., 1983)."))
        use = QPushButton("Use this soil map")
        use.clicked.connect(self.use_soil_map)
        form.addRow(use)
        layout.addWidget(own)

        uniform = QGroupBox("One texture for the whole catchment")
        form = QFormLayout(uniform)
        self.texture = QComboBox()
        for name in TEXTURES:
            self.texture.addItem(name, name)
        form.addRow("Texture class", self.texture)
        self.texture_values = _label("")
        form.addRow(self.texture_values)
        self.texture.currentIndexChanged.connect(self._show_texture)
        use = QPushButton("Use this texture")
        use.clicked.connect(lambda: self._set({
            "hydrology.soil_texture": self.texture.currentData(),
            "hydrology.soil_table": None, "hydrology.soil_zones": None},
            f"soil: {self.texture.currentData()} everywhere"))
        form.addRow(use)
        layout.addWidget(uniform)

        grids = QGroupBox("Soil textures downloaded (ISRIC SoilGrids, 250 m; global)")
        form = QFormLayout(grids)
        form.addRow(_label("Sand, silt and clay of the top 15 cm, classified to the "
                           "USDA texture classes; Green-Ampt values from Rawls et al. "
                           "(1983). A soil map of the agency is more detailed."))
        fetch = QPushButton("Download soil textures")
        fetch.clicked.connect(lambda: self.fetch("soil", "soil textures"))
        form.addRow(fetch)
        layout.addWidget(grids)

        sealed = QGroupBox("Sealed surfaces (no infiltration)")
        form = QFormLayout(sealed)
        self.sealed_file, row = self._file_row("Sealed surfaces", "Layers (*.gpkg *.shp)")
        form.addRow("Your layer", row)
        use = QPushButton("Use this layer")
        use.clicked.connect(lambda: self._set_file("hydrology.sealed_surfaces",
                                                   self.sealed_file.text()))
        form.addRow(use)
        fetch = QPushButton("Download built-up areas (ESA WorldCover, 10 m)")
        fetch.clicked.connect(lambda: self.fetch("sealed", "sealed surfaces"))
        form.addRow(fetch)
        layout.addWidget(sealed)
        layout.addStretch(1)
        return page

    def _moisture_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(
            "The water content of the soil at the start decides most of the runoff of a "
            "storm: a wet soil takes up little. A measurement in the catchment is best; "
            "the ERA5-Land value is a mean over about 10 km and the top 7 cm."))
        form = QFormLayout()
        self.moisture = QDoubleSpinBox()
        self.moisture.setRange(0.0, 0.6)
        self.moisture.setDecimals(3)
        self.moisture.setSingleStep(0.01)
        self.moisture.setSuffix(" m³/m³")
        form.addRow("Water content at the start", self.moisture)
        use = QPushButton("Use this value")
        use.clicked.connect(lambda: self._set(
            {"hydrology.initial_water_content": round(self.moisture.value(), 4)},
            f"water content at the start {self.moisture.value():.3f}"))
        form.addRow(use)
        self.moisture_at = QDateTimeEdit(QDateTime.currentDateTimeUtc().addDays(-30))
        self.moisture_at.setDisplayFormat("yyyy-MM-dd HH:00")
        self.moisture_at.setCalendarPopup(True)
        form.addRow("Start of the event (UTC)", self.moisture_at)
        fetch = QPushButton("Download ERA5-Land soil moisture at that hour")
        fetch.clicked.connect(lambda: self.fetch(
            "moisture", "soil moisture",
            at=self.moisture_at.dateTime().toString("yyyy-MM-ddTHH:00")))
        form.addRow(fetch)
        layout.addLayout(form)
        layout.addStretch(1)
        return page

    def _weather_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._portal_box(layout, "weather")
        layout.addWidget(_label(
            "Wind acts on water deeper than its threshold (1 m), so on ponds and lakes, "
            "not on the sheet flow of a hillslope. Evaporation is taken from surface "
            "water. Both matter little during a storm and more in the hours after it. "
            "A downloaded event (tab Rain) brings its weather along."))
        form = QFormLayout()
        self.weather_file, row = self._file_row("Weather series",
                                                "Tables (*.csv *.txt *.xlsx *.xls)")
        form.addRow("Weather series", row)
        self.wind = QCheckBox("Apply the wind")
        form.addRow(self.wind)
        self.evaporation = QComboBox()
        for value, text in (("none", "none"),
                            ("et0", "the et0 column (FAO-56 reference) times 1.05"),
                            ("penman", "FAO-56 Penman-Monteith from radiation, "
                                       "temperature, humidity and wind"),
                            ("series", "the evaporation column as it is")):
            self.evaporation.addItem(text, value)
        form.addRow("Evaporation", self.evaporation)
        use = QPushButton("Use these settings")
        use.clicked.connect(self.use_weather)
        form.addRow(use)
        layout.addLayout(form)
        layout.addStretch(1)
        return page

    def _outlet_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(
            "The outline of the case is the drainage divide, a wall by definition: no "
            "surface water crosses a divide. Water leaves through the outflow lines, "
            "drawn on the outline across the outlet of the catchment (page Liquid "
            "boundaries of the case settings). The checkup shows where the terrain "
            "drains across a wall instead."))
        form = QFormLayout()
        self.outlet = QComboBox()
        for value, text in OUTLETS:
            self.outlet.addItem(text, value)
        form.addRow("Outlet", self.outlet)
        self.kernel = QComboBox()
        self.kernel.addItem("finite volumes (recommended for rainfall-runoff)",
                            "finite-volumes")
        self.kernel.addItem("finite elements", "finite-elements")
        form.addRow("Numerical kernel", self.kernel)
        use = QPushButton("Use these settings")
        use.clicked.connect(lambda: self._set(
            {"hydrology.outlet_condition": self.outlet.currentData(),
             "hydrology.kernel": self.kernel.currentData()},
            f"outlet {self.outlet.currentData()}, {self.kernel.currentData()}"))
        form.addRow(use)
        layout.addLayout(form)
        layout.addWidget(_label(
            "normal depth needs the roughness of the outlet section: 20 % on Manning's "
            "n is about 12 % on the depth there, and the influence fades upstream over "
            "about depth / slope. Put the outlet a little downstream of where results "
            "are read."))
        layout.addStretch(1)
        return page

    def _save_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label("What Save writes into the case file. Hydrology mode is "
                                "switched on with it."))
        self.summary = _label("Nothing chosen yet.")
        layout.addWidget(self.summary)
        save = QPushButton("Save")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        layout.addStretch(1)
        return page

    # -- loading ------------------------------------------------------------------
    def _load(self) -> None:
        client, case = self.client, self.case
        run_async("aXqua: where the data of the catchment come from",
                  lambda: (client.hydrology_portals(case), client.hydrology_textures()),
                  on_success=self._show_portals, on_error=self._failed, owner=self)
        if not self.info:
            run_async("aXqua: reading the hydrology data of the case",
                      lambda: client.hydrology_info(case),
                      on_success=self._show_info, on_error=self._failed, owner=self)
        else:
            self._show_info(self.info)

    def _show_info(self, info: dict) -> None:
        self.info = info or {}
        if not self.info:
            return
        countries = ", ".join(self.info.get("countries") or []) or "none listed"
        self.where.setText(
            f"Catchment at {self.info.get('latitude', 0):.4f} N, "
            f"{self.info.get('longitude', 0):.4f} E, "
            f"{float(self.info.get('area_m2') or 0) / 1e6:.3f} km²; countries "
            f"{countries}. Files go to {self.info.get('out_dir', '')}.")
        index = self.outlet.findData(self.info.get("outlet_condition") or "normal_depth")
        self.outlet.setCurrentIndex(max(index, 0))
        index = self.evaporation.findData(self.info.get("evaporation") or "none")
        self.evaporation.setCurrentIndex(max(index, 0))
        self.wind.setChecked(bool(self.info.get("wind")))
        if self.info.get("initial_water_content") is not None:
            self.moisture.setValue(float(self.info["initial_water_content"]))
        weather = (self.info.get("files") or {}).get("weather")
        if weather:
            self.weather_file.setText(weather)

    def _show_portals(self, answer) -> None:
        self.portals, textures = answer
        self.textures = {row["texture"]: row for row in textures.get("textures") or []}
        for label, offers in self._portal_labels:
            label.setText(portal_html(self.portals, offers))
        self._show_texture()

    def _show_texture(self) -> None:
        row = (getattr(self, "textures", {}) or {}).get(self.texture.currentData())
        if row:
            self.texture_values.setText(
                f"Ks {row['ks_mm_h']} mm/h, suction head {row['suction_m']} m, water "
                f"content at saturation {row['theta_s']} (Rawls et al., 1983)")

    # -- the routes ---------------------------------------------------------------
    def _set(self, settings: dict, what: str) -> None:
        """Keep *settings* for Save, and say so."""
        for key, value in settings.items():
            if value is None:
                self.pending[key] = None
            elif isinstance(value, str) and value and key in (
                    "hydrology.rainfall", "hydrology.weather", "hydrology.soil_zones",
                    "hydrology.soil_table", "hydrology.sealed_surfaces"):
                self.pending[key] = stored_path(value, self.case.parent)
            else:
                self.pending[key] = value
        self.status.setText(f"Chosen: {what}. Save writes it into the case.")
        self._show_summary()

    def _set_file(self, key: str, text: str) -> None:
        if not text.strip():
            self.status.setText("Choose a file first.")
            return
        self._set({key: text.strip()}, f"{key.split('.', 1)[1]} {Path(text).name}")

    def _failed(self, exc) -> None:
        self.status.setText(user_text(exc))

    def _run(self, title: str, work, what: str) -> None:
        self.status.setText(f"{title}...")
        run_async(f"aXqua: {title}", work,
                  on_success=lambda answer: self._fetched(answer, what),
                  on_error=self._failed, owner=self)

    def _fetched(self, answer: dict, what: str) -> None:
        self.wrote = True
        settings = dict(answer.get("settings") or {})
        for key, value in settings.items():
            self.pending[key] = value
        note = answer.get("caution") or ""
        self.status.setText(f"Done: {what}. Save writes it into the case. {note}")
        if "hydrology.weather" in settings:
            self.weather_file.setText(str(answer.get("weather") or ""))
        if "hydrology.initial_water_content" in settings:
            self.moisture.setValue(float(settings["hydrology.initial_water_content"]))
        self._show_summary()

    def import_rain(self) -> None:
        if not self.rain_file.text().strip():
            self.status.setText("Choose the rain record first.")
            return
        client, case = self.client, self.case
        units, stamp = self.rain_units.currentData(), self.rain_stamp.currentData()
        source, start = self.rain_file.text().strip(), self.rain_start.text().strip()
        self._run("reading the rain record",
                  lambda: client.hydrology_rainfall(case, source, units=units,
                                                    stamp=stamp, start=start),
                  "rain record")

    def build_design(self) -> None:
        pairs, table = self.design_pairs.text().strip(), self.design_file.text().strip()
        if not (pairs or table):
            self.status.setText("Enter the depth-duration pairs or choose a table.")
            return
        client, case = self.client, self.case
        duration, step = self.design_duration.value(), self.design_step.value()
        kind = self.design_kind.currentData()
        self._run("building the design storm",
                  lambda: client.hydrology_design(case, pairs=pairs, file=table or None,
                                                  duration=duration, step=step,
                                                  distribution=kind),
                  "design storm")

    def fetch_event(self) -> None:
        start = self.event_start.dateTime().toString("yyyy-MM-ddTHH:00")
        end = self.event_end.dateTime().toString("yyyy-MM-ddTHH:00")
        self.moisture_at.setDateTime(self.event_start.dateTime())
        client, case = self.client, self.case
        self._run("downloading rain and weather",
                  lambda: client.hydrology_fetch(case, "event", start=start, end=end),
                  "rain and weather of the event")

    def fetch(self, what: str, title: str, **options) -> None:
        client, case = self.client, self.case
        self._run(f"downloading {title}",
                  lambda: client.hydrology_fetch(case, what, **options), title)

    def use_soil_map(self) -> None:
        zones, table = self.soil_zones.text().strip(), self.soil_table.text().strip()
        if not table:
            self.status.setText("Choose the soil table first.")
            return
        settings = {"hydrology.soil_table": table, "hydrology.soil_texture": None,
                    "hydrology.soil_zones": zones or None}
        if zones:
            settings["hydrology.soil_zone_field"] = self.soil_field.text().strip() \
                or "Zone ID"
        self._set(settings, "your soil map")

    def use_weather(self) -> None:
        settings = {"hydrology.wind": self.wind.isChecked(),
                    "hydrology.evaporation": self.evaporation.currentData()}
        if self.weather_file.text().strip():
            settings["hydrology.weather"] = self.weather_file.text().strip()
        self._set(settings, "wind and evaporation")

    # -- saving -------------------------------------------------------------------
    def _show_summary(self) -> None:
        if not self.pending:
            self.summary.setText("Nothing chosen yet.")
            return
        self.summary.setText("\n".join(
            f"{key}: {'(removed)' if value is None else value}"
            for key, value in sorted(self.pending.items())))

    def _tab_changed(self, index: int) -> None:
        self.back_button.setVisible(index > 0)
        self.next_button.setVisible(index < len(TABS) - 1)
        if index == len(TABS) - 1:
            self._show_summary()

    def merged(self, data: dict) -> dict:
        """The case file *data* with the chosen settings, in hydrology mode."""
        data = dict(data or {})
        for key, value in self.pending.items():
            block, name = key.split(".", 1)
            section = dict(data.get(block) or {})
            if value is None:
                section.pop(name, None)
            else:
                section[name] = value
            data[block] = section
        hydrology = dict(data.get("hydrology") or {})
        hydrology["enabled"] = True
        data["hydrology"] = hydrology
        return data

    def save(self) -> None:
        client, case = self.client, self.case
        self.status.setText("Saving...")

        def work():
            data = (client.case_read(case) or {}).get("data") or {}
            return client.case_write(case, self.merged(data))

        def done(answer: dict) -> None:
            self.saved = True
            self.pending = {}
            count = len(answer.get("findings") or [])
            self.status.setText("Saved. " + (f"The check of the case found {count} "
                                             "thing(s) to look at on the tab "
                                             "Case Setup." if count else
                                             "The check of the case found nothing."))
            self._show_summary()

        run_async("aXqua: saving the hydrology data", work, on_success=done,
                  on_error=self._failed, owner=self)
