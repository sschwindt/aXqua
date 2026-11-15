"""The Survey planner: where field data are needed for a calibration, and how a survey collects them.

A calibration can only settle a parameter where a measurement feels it: the roughness of a zone inside that zone, a turbulence parameter where the velocity changes across the flow. The planner reads the calibration parameters of the case and the result of its steady run, places the measurements where each parameter acts, and plans the survey that collects them, as one or several of four kinds:

* **Ground-based**: point velocities by wading with a probe (FlowTracker or similar), with the walk of every surveyor and of the DGPS surveyor;
* **Airborne**: the video of a hovering drone, evaluated by particle tracking or particle image velocimetry (pXv);
* **Kayak**: drifting kayaks that carry a DGPS rover (Barker et al., 2018);
* **Water temperature**: fixed loggers or a mobile probe, in slow and shallow as well as fast and deep water.

One window with a tab per step, visited in any order, with *Back* and *Next* walking through them: Survey (kinds and parameters), Access (the points where the surveyors reach the river, set on the map), one tab of settings per kind, Time and weather, Plan. The window is not modal, because the access points are set on the map while it is open. It decides nothing: the forms are the tables of :mod:`..core.survey`, and every step is a call of ``axqua survey``. The plan is written to ``survey-plan.gpkg``, one map and one factsheet per kind in the folder ``survey-plan`` of the case.
"""

from __future__ import annotations

import copy
from pathlib import Path

from qgis.PyQt.QtCore import QDate, Qt, QTime, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPlainTextEdit,
                                 QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QTimeEdit, QVBoxLayout, QWidget)

from ..compat import CHECKED, UNCHECKED, USER_ROLE as _ROLE, enum_value, open_in_file_manager
from ..core import survey
from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import help as help_pages
from . import survey_layers
from .style import REQUIRED_STYLE

TABS = ("Survey", "Access", "Ground-based", "Airborne", "Kayak", "Temperature", "Time and weather", "Plan")
#: The tab of the settings of each kind of survey.
KIND_TABS = {"ground": 2, "airborne": 3, "kayak": 4, "temperature": 5}
PLAN_TAB = len(TABS) - 1

INTRO = ("A calibration can only settle a parameter where a measurement feels it. The planner places measurements where each calibration parameter of the case acts, in the water of the steady run, "
         "and plans the survey that collects them. Run the steady simulation at the discharge you expect on the survey day first.")

KIND_NOTES = {
    "ground": "Point velocities and depths by wading with a probe (FlowTracker or similar). A second surveyor records every position with a DGPS rover.",
    "airborne": "The video of a hovering drone, evaluated by particle tracking or particle image velocimetry (pXv). It gives the velocity at the water surface.",
    "kayak": "Kayaks that drift with the flow and carry a DGPS rover. Efficient on long reaches; it needs a pick-up at the lower end.",
    "temperature": "Fixed loggers or a mobile probe, in slow and shallow as well as fast and deep water.",
}

ACCESS_INTRO = ("Set the points where the surveyors reach the river in the layer <b>access_points</b> of survey-plan.gpkg. <i>Set points on the map</i> creates the layer, adds it to the map and "
                "starts the tool that adds a point: click on the map, choose the role of the point and give it a name if you like. <i>Save the points</i> writes them into the file.")

GROUND_INTRO = ("The plan keeps every vertical and every walk path in water a person can wade, orders the verticals into the shortest walk from and back to the access point, and adds up the time: "
                "walking, measuring, breaks and the adjustment of the pressure sensor.")
AIRBORNE_INTRO = ("The plan assumes a DJI drone that hovers over one position after the other with the camera pointing straight down, and frames wide enough for the banks. "
                  "The height above the water sets the frame, the ground resolution and how far a tracer moves between two frames.")
KAYAK_INTRO = ("After Barker et al. (2018): a kayak that is not paddled floats at the velocity of the flow, so the distance between two DGPS positions divided by the time between them is the velocity at the surface. "
               "The kayaks are put in at the access point and taken out at the point with the role leave.")
TEMPERATURE_INTRO = "Slow, shallow water follows the air temperature and the sun; deep, fast water follows the river. The points are shared among these patches so that both are measured."
TIME_INTRO = ("With a survey day, the plan looks up sunrise and sunset, the weather forecast and what is known about the mobile network and the DGPS correction service at the reach. "
              "Without a start, times count from 00:00.")
ONLINE_TEXT = "Look up the weather and the network coverage on the internet (Open-Meteo, OpenStreetMap)"

FORM_INTROS = {"ground": GROUND_INTRO, "airborne": AIRBORNE_INTRO, "kayak": KAYAK_INTRO, "temperature": TEMPERATURE_INTRO}
PARAMETER_COLUMNS = ("Use", "Calibration parameter", "Measure", "Zone", "Points", "")
_EDITABLE = enum_value(Qt, "ItemFlag.ItemIsEditable", "ItemIsEditable")
_CHECKABLE = enum_value(Qt, "ItemFlag.ItemIsUserCheckable", "ItemIsUserCheckable")
_TOOLTIP = enum_value(Qt, "ItemDataRole.ToolTipRole", "ToolTipRole")


def open_file(path: str) -> bool:
    """Open *path* with the program of the desktop. A function of its own so that a test can replace it."""
    return bool(path) and Path(path).is_file() and QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


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


def _resize_mode(header_view, name: str):
    return enum_value(header_view, f"ResizeMode.{name}", name)


class SurveyWizard(QDialog):
    """Plan the survey of the case at *case*."""

    def __init__(self, context, case: Path, parent=None) -> None:
        super().__init__(parent)
        self.ctx = context
        self.client = context.client
        self.case = Path(case)
        self.info: dict = {}
        self.settings: dict = {}
        self.answer: dict = {}                   # the last plan of this window
        self.weather: dict = {}                  # the last answer of survey conditions
        self.widgets: dict[str, tuple] = {}      # dotted key -> (field, widget)
        self.kind_boxes: dict[str, QCheckBox] = {}
        self.planning = False
        self.setWindowTitle(f"Survey planner: {self.case.name}")
        self.resize(980, 780)
        self._build()
        self._load()

    # -- construction -------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        pages = (self._survey_page(), self._access_page(), self._form_page("ground"), self._form_page("airborne"), self._form_page("kayak"), self._form_page("temperature"), self._time_page(), self._plan_page())
        for title, page in zip(TABS, pages):
            self.tabs.addTab(_scrolling(page), title)
        self.tabs.setUsesScrollButtons(False)        # the last tab is the plan; it must not hide behind an arrow
        self.tabs.currentChanged.connect(self._tab_changed)
        outer.addWidget(self.tabs, 1)
        self.status = _label("")
        outer.addWidget(self.status)
        buttons = QHBoxLayout()
        help_button = QPushButton("Help")
        help_button.clicked.connect(lambda: help_pages.open_window_help("survey-wizard"))
        buttons.addWidget(help_button)
        buttons.addStretch(1)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(lambda: self._step(-1))
        buttons.addWidget(self.back_button)
        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(lambda: self._step(1))
        buttons.addWidget(self.next_button)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        outer.addLayout(buttons)
        self._tab_changed(0)

    def _step(self, direction: int) -> None:
        """The next tab in *direction* that is not switched off."""
        index = self.tabs.currentIndex() + direction
        while 0 <= index < len(TABS) and not self.tabs.isTabEnabled(index):
            index += direction
        if 0 <= index < len(TABS):
            self.tabs.setCurrentIndex(index)

    def _tab_changed(self, index: int) -> None:
        self.back_button.setEnabled(index > 0)
        self.next_button.setEnabled(index < len(TABS) - 1)

    def _survey_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(INTRO))
        self.model_note = _label("")
        self.model_note.setStyleSheet(REQUIRED_STYLE)
        self.model_note.setVisible(False)
        layout.addWidget(self.model_note)

        kinds = QGroupBox("Kinds of survey")
        box = QVBoxLayout(kinds)
        for kind in survey.KINDS:
            check = QCheckBox(survey.KIND_TITLES[kind])
            check.setToolTip(KIND_NOTES[kind])
            check.toggled.connect(self._kinds_changed)
            self.kind_boxes[kind] = check
            box.addWidget(check)
            note = _label(KIND_NOTES[kind])
            note.setIndent(22)
            box.addWidget(note)
        layout.addWidget(kinds)

        parameters = QGroupBox("Calibration parameters and where they are measured")
        box = QVBoxLayout(parameters)
        box.addWidget(_label("Every roughness zone the calibration varies needs measurements inside it: a zone without a point is not calibrated. "
                             "Other parameters can be added with the rule that says where they act."))
        self.table = QTableWidget(0, len(PARAMETER_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(PARAMETER_COLUMNS))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, _resize_mode(header, "Stretch"))
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(190)
        box.addWidget(self.table)
        row = QHBoxLayout()
        add = QPushButton("Add parameter...")
        add.clicked.connect(self.add_parameter)
        row.addWidget(add)
        row.addStretch(1)
        box.addLayout(row)
        form = QFormLayout()
        self.points_default = QSpinBox()
        self.points_default.setRange(1, 200)
        self.points_default.setToolTip("For a parameter that is added, and for the parameters of a case that was not planned before.")
        form.addRow("Points per parameter", self.points_default)
        self.spacing = QDoubleSpinBox()
        self.spacing.setRange(0.0, 10000.0)
        self.spacing.setDecimals(1)
        self.spacing.setSuffix(" m")
        self.spacing.setSpecialValueText("two mesh edges")
        self.spacing.setToolTip("Two measurements are at least this far apart.")
        form.addRow("Smallest distance of two points", self.spacing)
        box.addLayout(form)
        layout.addWidget(parameters)

        result = QGroupBox("Flow field to plan with")
        form = QFormLayout(result)
        self.result_file = QLineEdit()
        self.result_file.setPlaceholderText("the result of the steady run of the case")
        browse = QPushButton("...")
        browse.clicked.connect(self.browse_result)
        holder = QWidget()
        inner = QHBoxLayout(holder)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(self.result_file, 1)
        inner.addWidget(browse)
        form.addRow("Result file", holder)
        self.result_note = _label("")
        form.addRow(self.result_note)
        layout.addWidget(result)
        layout.addStretch(1)
        return page

    def _access_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(ACCESS_INTRO, rich=True))
        self.roles_label = _label("", rich=True)
        layout.addWidget(self.roles_label)
        row = QHBoxLayout()
        self.set_points = QPushButton("Set points on the map")
        self.set_points.clicked.connect(self.start_access)
        row.addWidget(self.set_points)
        self.save_points = QPushButton("Save the points")
        self.save_points.clicked.connect(self.save_access)
        row.addWidget(self.save_points)
        row.addStretch(1)
        layout.addLayout(row)
        found = QGroupBox("Points the plan will use")
        box = QVBoxLayout(found)
        self.access_label = _label("")
        box.addWidget(self.access_label)
        layout.addWidget(found)
        layout.addStretch(1)
        return page

    def _widget(self, item: survey.Field) -> QWidget:
        if item.kind == "bool":
            widget = QCheckBox()
        elif item.kind == "int":
            widget = QSpinBox()
            widget.setRange(int(item.low), int(item.high))
            if item.unit:
                widget.setSuffix(f" {item.unit}")
        elif item.kind == "choice":
            widget = QComboBox()
            for value, text in item.choices:
                widget.addItem(text, value)
        elif item.kind in ("pairs", "numbers"):
            widget = QLineEdit()
            widget.setPlaceholderText(item.unit)
        else:
            widget = QDoubleSpinBox()
            widget.setDecimals(item.decimals)
            widget.setRange(item.low, item.high)
            widget.setSingleStep(item.step)
            if item.unit:
                widget.setSuffix(f" {item.unit}")
        tip = item.tip or (item.unit if item.kind in ("pairs", "numbers") else "")
        if item.kind in ("pairs", "numbers") and item.tip:
            tip = f"{item.tip} Written as {item.unit}, separated by semicolons."
        if tip:
            widget.setToolTip(tip)
        self.widgets[item.key] = (item, widget)
        return widget

    def _groups(self, layout: QVBoxLayout, form: str) -> None:
        for group in survey.FORMS[form]:
            box = QGroupBox(group.title)
            rows = QFormLayout(box)
            for item in group.fields:
                rows.addRow(item.label, self._widget(item))
            if group.note:
                rows.addRow(_label(group.note))
            layout.addWidget(box)

    def _form_page(self, form: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(FORM_INTROS[form]))
        self._groups(layout, form)
        layout.addStretch(1)
        return page

    def _time_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(TIME_INTRO))
        day = QGroupBox("Survey day")
        form = QFormLayout(day)
        self.has_date = QCheckBox("The survey day is known")
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setDate(QDate.currentDate().addDays(1))
        self.has_date.toggled.connect(self.date.setEnabled)
        self.date.setEnabled(False)
        form.addRow(self.has_date, self.date)
        self.has_start = QCheckBox("Start of the working day")
        self.start = QTimeEdit()
        self.start.setDisplayFormat("HH:mm")
        self.start.setTime(QTime(8, 0))
        self.has_start.toggled.connect(self.start.setEnabled)
        self.start.setEnabled(False)
        form.addRow(self.has_start, self.start)
        layout.addWidget(day)
        self._groups(layout, "time")
        weather = QGroupBox("Daylight, weather and network coverage")
        box = QVBoxLayout(weather)
        self.online = QCheckBox(ONLINE_TEXT)
        box.addWidget(self.online)
        row = QHBoxLayout()
        self.look_up = QPushButton("Look up")
        self.look_up.clicked.connect(self.ask_conditions)
        row.addWidget(self.look_up)
        self.use_start = QPushButton("Use the suggested start")
        self.use_start.setEnabled(False)
        self.use_start.clicked.connect(self.use_suggested_start)
        row.addWidget(self.use_start)
        row.addStretch(1)
        box.addLayout(row)
        self.weather_label = _label("", rich=True)
        box.addWidget(self.weather_label)
        layout.addWidget(weather)
        layout.addStretch(1)
        return page

    def _plan_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label("The plan is written to survey-plan.gpkg (points, walk paths, drone positions, drift lines), with one map and one factsheet per kind of survey. "
                                "A factsheet is one A4 page for the field. Planning again replaces the plan; the access points stay."))
        row = QHBoxLayout()
        self.plan_button = QPushButton("Plan the survey")
        self.plan_button.clicked.connect(self.plan)
        row.addWidget(self.plan_button)
        self.add_to_map = QCheckBox("Add the plan to the map")
        self.add_to_map.setChecked(True)
        row.addWidget(self.add_to_map)
        row.addStretch(1)
        layout.addLayout(row)
        self.plan_text = QPlainTextEdit()
        self.plan_text.setReadOnly(True)
        self.plan_text.setMinimumHeight(260)
        layout.addWidget(self.plan_text, 1)
        self.files_box = QGroupBox("Maps and factsheets")
        self.files_layout = QVBoxLayout(self.files_box)
        self.files_box.setVisible(False)
        layout.addWidget(self.files_box)
        return page

    # -- what the library knows ---------------------------------------------------
    def _load(self) -> None:
        client, case = self.client, self.case
        self.status.setText("Reading the case...")
        run_async("aXqua: reading the survey plan of the case", lambda: client.survey_info(case), on_success=self._show_info, on_error=self._failed, owner=self)

    def _failed(self, exc) -> None:
        self.status.setText(user_text(exc))

    def _show_info(self, info: dict) -> None:
        self.info = info or {}
        self.status.setText("")
        self.settings = copy.deepcopy(self.info.get("settings") or {})
        self.show_settings()
        self.show_parameters(survey.parameter_rows(self.info))
        self.show_access(self.info.get("access") or {})
        roles = self.info.get("roles") or {}
        self.roles_label.setText("<ul>" + "".join(f"<li><b>{role}</b>: {text}</li>" for role, text in roles.items()) + "</ul>" if roles else "")
        notes = []
        if not self.info.get("built"):
            notes.append("The model of the case is not built yet. Build it on the tab Preprocessing and run the steady simulation, then plan the survey.")
        elif not self.info.get("result_exists"):
            notes.append("The case has no result of a steady run yet. Run the steady simulation at the discharge you expect on the survey day, then plan the survey.")
        if not self.info.get("has_zones"):
            notes.append("The case has no roughness zones, so the measurements are spread over the wetted model.")
        self.model_note.setText(" ".join(notes))
        self.model_note.setVisible(bool(notes))
        result = str(self.info.get("result") or "")
        self.result_note.setText((f"Planning with {Path(result).name} in {Path(result).parent}." if self.info.get("result_exists") else f"{Path(result).name} is missing in {Path(result).parent}.") if result else "")
        last = self.info.get("last_plan") or {}
        if last.get("files") and not self.answer:
            self.show_files({"files": last["files"], "folder": self.info.get("folder")})
            self.plan_text.setPlainText(f"The last plan was made on {last.get('made', '')}: " + ", ".join(survey.KIND_TITLES.get(kind, kind) for kind in last.get("kinds") or []) + ".")

    def show_settings(self) -> None:
        """Fill the forms from :attr:`settings`."""
        chosen = self.settings.get("kinds") or []
        for kind, check in self.kind_boxes.items():
            check.blockSignals(True)
            check.setChecked(kind in chosen)
            check.blockSignals(False)
        self._kinds_changed()
        self.points_default.setValue(int(self.settings.get("points_per_parameter") or 6))
        self.spacing.setValue(float(self.settings.get("min_spacing") or 0.0))
        self.result_file.setText(str(self.settings.get("result") or ""))
        self.online.setChecked(bool(self.settings.get("online", True)))
        for key, (item, widget) in self.widgets.items():
            value = survey.get(self.settings, key)
            if value is None:
                continue
            if item.kind == "bool":
                widget.setChecked(bool(value))
            elif item.kind == "int":
                widget.setValue(int(value))
            elif item.kind == "choice":
                index = widget.findData(value)
                widget.setCurrentIndex(max(index, 0))
            elif item.kind == "pairs":
                widget.setText(survey.pairs_text(value))
            elif item.kind == "numbers":
                widget.setText(survey.numbers_text(value))
            else:
                widget.setValue(float(value))
        date = QDate.fromString(str(survey.get(self.settings, "time.date") or ""), "yyyy-MM-dd")
        self.has_date.setChecked(date.isValid())
        if date.isValid():
            self.date.setDate(date)
        start = QTime.fromString(str(survey.get(self.settings, "time.start") or ""), "HH:mm")
        self.has_start.setChecked(start.isValid())
        if start.isValid():
            self.start.setTime(start)

    def _kinds_changed(self, *_args) -> None:
        for kind, index in KIND_TABS.items():
            self.tabs.setTabEnabled(index, self.kind_boxes[kind].isChecked())

    # -- the parameter table ------------------------------------------------------
    def show_parameters(self, rows: list[dict]) -> None:
        self.table.setRowCount(0)
        for row in rows:
            self._add_row(row)
        self.table.resizeColumnsToContents()

    def _add_row(self, row: dict) -> None:
        index = self.table.rowCount()
        self.table.insertRow(index)
        use = QTableWidgetItem()
        use.setFlags((use.flags() | _CHECKABLE) & ~_EDITABLE)
        use.setCheckState(CHECKED if row.get("use", True) else UNCHECKED)
        self.table.setItem(index, 0, use)
        name = QTableWidgetItem(str(row["name"]))
        name.setData(_ROLE, {"declared": bool(row.get("declared")), "zone_fixed": row.get("rule") == "zone" and bool(row.get("declared"))})
        name.setFlags(name.flags() & ~_EDITABLE)
        if row.get("note"):
            name.setToolTip(str(row["note"]))
        self.table.setItem(index, 1, name)
        rule = QComboBox()
        rules = self.info.get("rules") or {}
        for position, (key, text) in enumerate(rules.items()):
            rule.addItem(survey.rule_title(key, text), key)
            rule.setItemData(position, text, _TOOLTIP)
        position = rule.findData(row.get("rule") or "everywhere")
        rule.setCurrentIndex(max(position, 0))
        zone = QSpinBox()
        zone.setRange(0, 9999)
        zone.setValue(int(row["zone"]) if row.get("zone") is not None else 0)
        declared_zone = row.get("rule") == "zone" and bool(row.get("declared"))
        rule.setEnabled(not declared_zone)          # the roughness of a zone acts in that zone
        zone.setEnabled(not declared_zone and row.get("rule") == "zone")
        rule.currentIndexChanged.connect(lambda _i, r=rule, z=zone: z.setEnabled(r.currentData() == "zone"))
        self.table.setCellWidget(index, 2, rule)
        self.table.setCellWidget(index, 3, zone)
        points = QSpinBox()
        points.setRange(0, 500)
        points.setValue(int(row.get("points") or 0))
        self.table.setCellWidget(index, 4, points)
        if row.get("declared"):
            last = QTableWidgetItem("of the case" + ("; " + str(row["note"]) if row.get("note") else ""))
            last.setFlags(last.flags() & ~_EDITABLE)
            self.table.setItem(index, 5, last)
        else:
            remove = QPushButton("Remove")
            remove.clicked.connect(lambda _checked=False, item=name: self.table.removeRow(self.table.row(item)))
            self.table.setCellWidget(index, 5, remove)

    def parameter_rows(self) -> list[dict]:
        """The table as rows of :func:`..core.survey.parameter_rows`."""
        rows = []
        for index in range(self.table.rowCount()):
            name = self.table.item(index, 1)
            rule = self.table.cellWidget(index, 2).currentData()
            zone = self.table.cellWidget(index, 3).value()
            rows.append({"name": name.text(), "rule": rule, "zone": zone if rule == "zone" else None, "points": self.table.cellWidget(index, 4).value(),
                         "use": self.table.item(index, 0).checkState() == CHECKED, "declared": bool((name.data(_ROLE) or {}).get("declared"))})
        return rows

    def add_parameter(self) -> None:
        name, ok = QInputDialog.getText(self, "Add a calibration parameter", "Name of the parameter, as the calibration will call it\n(a TELEMAC keyword, for example VELOCITY DIFFUSIVITY):")
        self.add_parameter_named(name if ok else "")

    def add_parameter_named(self, name: str) -> bool:
        name = str(name or "").strip()
        if not name:
            return False
        if any(row["name"].lower() == name.lower() for row in self.parameter_rows()):
            self.status.setText(f"The parameter {name} is in the table already.")
            return False
        self._add_row({"name": name, "rule": "everywhere", "zone": None, "points": self.points_default.value(), "use": True, "declared": False})
        self.status.setText(f"Added {name}. Choose where its measurements are placed.")
        return True

    def browse_result(self) -> None:
        start = str(Path(str(self.info.get("result") or self.case)).parent)
        chosen, _ = QFileDialog.getOpenFileName(self, "Result of the steady run", start, "TELEMAC results (*.slf)")
        if chosen:
            self.result_file.setText(chosen)

    # -- the forms as settings ----------------------------------------------------
    def read_settings(self) -> tuple[dict, list[tuple[int, str]]]:
        """The settings the forms hold, and what cannot be read: ``(tab, text)``."""
        settings = copy.deepcopy(self.settings)
        problems: list[tuple[int, str]] = []
        settings["kinds"] = [kind for kind in survey.KINDS if self.kind_boxes[kind].isChecked()]
        if not settings["kinds"]:
            problems.append((0, "Choose at least one kind of survey."))
        settings["parameters"] = [survey.parameter_setting(row) for row in self.parameter_rows()]
        settings["points_per_parameter"] = self.points_default.value()
        settings["min_spacing"] = self.spacing.value()
        settings["result"] = self.result_file.text().strip()
        settings["online"] = self.online.isChecked()
        tab_of = {form: KIND_TABS.get(form, len(TABS) - 2) for form in survey.FORMS}
        for form in survey.FORMS:
            for item in survey.fields(form):
                widget = self.widgets[item.key][1]
                if item.kind == "bool":
                    value = widget.isChecked()
                elif item.kind == "choice":
                    value = widget.currentData()
                elif item.kind in ("pairs", "numbers"):
                    try:
                        value = survey.parse_pairs(widget.text()) if item.kind == "pairs" else survey.parse_numbers(widget.text())
                        wrong = survey.heights_problem(item.key, value) if item.kind == "numbers" else ""
                        if wrong:
                            raise ValueError(wrong)
                    except ValueError as exc:
                        problems.append((tab_of[form], f"{item.label}: {exc}."))
                        continue
                else:
                    value = widget.value()
                survey.put(settings, item.key, value)
        survey.put(settings, "time.date", self.date.date().toString("yyyy-MM-dd") if self.has_date.isChecked() else "")
        survey.put(settings, "time.start", self.start.time().toString("HH:mm") if self.has_start.isChecked() else "")
        return settings, problems

    def _checked_settings(self) -> dict | None:
        settings, problems = self.read_settings()
        if problems:
            tab, text = problems[0]
            self.tabs.setCurrentIndex(tab)
            self.status.setText(text)
            return None
        self.settings = settings
        return settings

    # -- the access points --------------------------------------------------------
    def show_access(self, access: dict) -> None:
        self.access_label.setText("\n".join(survey.access_lines(access)))

    def start_access(self) -> None:
        client, case = self.client, self.case
        self.status.setText("Creating the layer of the access points...")
        self.set_points.setEnabled(False)
        run_async("aXqua: creating the layer of the access points", lambda: client.survey_init(case), on_success=self._access_ready, on_error=self._access_failed, owner=self)

    def _access_failed(self, exc) -> None:
        self.set_points.setEnabled(True)
        self._failed(exc)

    def _access_ready(self, answer: dict) -> None:
        self.set_points.setEnabled(True)
        gpkg = answer.get("gpkg")
        self.info["gpkg"] = gpkg
        layer = survey_layers.show_access(gpkg, answer.get("roles") or self.info.get("roles"), iface=self.ctx.iface) if gpkg else None
        if layer is None:
            self.status.setText("The layer of the access points could not be opened in QGIS.")
            return
        self.show_access(answer.get("access") or {})
        self.status.setText("Click on the map to set a point, choose its role (access, leave or takeoff), then click Save the points.")

    def save_access(self) -> None:
        gpkg = self.info.get("gpkg")
        wrong = survey_layers.commit_access(gpkg) if gpkg else ""
        if wrong:
            self.status.setText(f"The points could not be saved: {wrong}")
            return
        client, case = self.client, self.case
        self.status.setText("Reading the points...")

        def done(answer: dict) -> None:
            self.info["access"] = answer.get("access") or {}
            self.show_access(self.info["access"])
            self.status.setText("The points are saved." if self.info["access"].get("count") else "The layer holds no point yet.")

        run_async("aXqua: reading the access points", lambda: client.survey_init(case), on_success=done, on_error=self._failed, owner=self)

    # -- daylight, weather, coverage ----------------------------------------------
    def ask_conditions(self) -> None:
        settings = self._checked_settings()
        if settings is None:
            return
        client, case, offline = self.client, self.case, not settings.get("online", True)
        self.look_up.setEnabled(False)
        self.status.setText("Looking up daylight, weather and network coverage...")

        def failed(exc) -> None:
            self.look_up.setEnabled(True)
            self._failed(exc)

        run_async("aXqua: daylight, weather and network coverage of the survey", lambda: client.survey_conditions(case, settings, offline=offline), on_success=self.show_conditions, on_error=failed, owner=self)

    def show_conditions(self, answer: dict) -> None:
        import html

        self.look_up.setEnabled(True)
        self.weather = answer or {}
        self.status.setText("")
        self.weather_label.setText("<br>".join(html.escape(line) for line in survey.conditions_lines(self.weather)))
        self.use_start.setEnabled(bool(survey.suggested_start(self.weather)))

    def use_suggested_start(self) -> None:
        start = QTime.fromString(survey.suggested_start(self.weather), "HH:mm")
        if start.isValid():
            self.has_start.setChecked(True)
            self.start.setTime(start)
            self.status.setText(f"The working day starts at {start.toString('HH:mm')}.")

    # -- the plan -----------------------------------------------------------------
    def plan(self) -> None:
        if self.planning:
            return
        settings = self._checked_settings()
        if settings is None:
            return
        gpkg = self.info.get("gpkg")
        wrong = survey_layers.commit_access(gpkg) if gpkg else ""
        if wrong:
            self.tabs.setCurrentIndex(1)
            self.status.setText(f"The access points could not be saved: {wrong}")
            return
        if gpkg:
            survey_layers.remove_plan(gpkg)          # the library replaces these layers in the file
        client, case, offline = self.client, self.case, not settings.get("online", True)
        self.planning = True
        self.plan_button.setEnabled(False)
        self.tabs.setCurrentIndex(PLAN_TAB)
        self.plan_text.setPlainText("Planning the survey. A reach of some kilometers takes a few minutes...")
        self.status.setText("Planning the survey...")
        run_async("aXqua: planning the survey", lambda: client.survey_plan(case, settings, offline=offline), on_success=self.show_plan, on_error=self._plan_failed, owner=self)

    def _plan_failed(self, exc) -> None:
        self.planning = False
        self.plan_button.setEnabled(True)
        text = user_text(exc)
        self.plan_text.setPlainText(text)
        self.status.setText("The survey could not be planned.")

    def show_plan(self, answer: dict) -> None:
        self.planning = False
        self.plan_button.setEnabled(True)
        self.answer = answer or {}
        self.info["gpkg"] = self.answer.get("gpkg") or self.info.get("gpkg")
        self.plan_text.setPlainText("\n".join(survey.plan_lines(self.answer)))
        self.show_files(self.answer)
        if self.answer.get("access"):
            self.show_access(self.answer["access"])
        planned = [survey.KIND_TITLES[kind] for kind in survey.KINDS if kind in (self.answer.get("plans") or {})]
        self.status.setText(("Planned: " + ", ".join(planned) + ".") if planned else "Nothing could be planned; the reasons are listed above.")
        if self.add_to_map.isChecked() and self.answer.get("gpkg"):
            layers = survey_layers.show_plan(self.answer)
            access = survey_layers.show_access(self.answer["gpkg"], self.info.get("roles"), edit=False)
            survey_layers.zoom_to(self.ctx.iface, layers + ([access] if access is not None else []))

    def show_files(self, answer: dict) -> None:
        """One row of buttons per kind of survey that has a map and a factsheet."""
        while self.files_layout.count():
            item = self.files_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)              # a widget that only waits for its deletion is still drawn
                widget.deleteLater()
        self.file_buttons: dict[tuple[str, str], QPushButton] = {}
        rows = survey.plan_files(answer)
        for kind, map_file, factsheet in rows:
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QLabel(survey.KIND_TITLES[kind]), 1)
            for what, text, path in (("map", "Open map", map_file), ("factsheet", "Open factsheet", factsheet)):
                button = QPushButton(text)
                button.setEnabled(bool(path) and Path(path).is_file())
                button.setToolTip(path)
                button.clicked.connect(lambda _checked=False, p=path: open_file(p))
                row.addWidget(button)
                self.file_buttons[(kind, what)] = button
            self.files_layout.addWidget(holder)
        folder = str(answer.get("folder") or "")
        if rows and folder:
            button = QPushButton("Open the folder of the plan")
            button.clicked.connect(lambda _checked=False: open_in_file_manager(folder))
            self.files_layout.addWidget(button)
        self.files_box.setVisible(bool(rows))
