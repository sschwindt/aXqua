"""The window *Calibration parameters* and its box on the tab *Calibration & validation*.

The calibration targets are the measured quantities (water depth, velocity); the calibration parameters are what the calibration varies to reproduce them. The user's order of 2026-11-20: the energy slope at the outlet of the Inn KB15 example could be set only in the case file, so this window sets it, and further parameters, in four tabs visited in any order:

1. **Roughness zones**: the zones of the roughness table and their ranges, read only; the table is where they are changed (*Open the roughness table*).
2. **Outflow level**: not calibrated, the energy slope at the outlet, or an offset of the level, with the reason where the case cannot use one; *Show the outflow levels* gives the level a range sets at the discharge of every calibration situation.
3. **Further parameters**: keywords that fit the case, with a typical range, ticked to calibrate them.
4. **Save**: what is written into the case file, through ``axqua case read`` / ``case write``.

The window decides nothing: what the case can calibrate is the answer of ``axqua calibration <case> --parameters``, and the merging is :mod:`..core.calibration_parameters`.
"""

from __future__ import annotations

import html
from pathlib import Path

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (QButtonGroup, QDialog, QFormLayout, QGroupBox,
                                 QHBoxLayout, QLabel, QLineEdit, QPushButton,
                                 QRadioButton, QScrollArea, QTableWidget,
                                 QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..compat import enum_value, is_deleted
from ..core import calibration_parameters as cp
from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import help as help_pages

TABS = ("Roughness zones", "Outflow level", "Further parameters", "Save")

BOX_TEXT = (
    "What the calibration varies so that the model reproduces the measurements: the "
    "roughness of the zones of the roughness table and, where the case needs it, the "
    "water level at the outflow and further parameters of the model.")

ZONES_TEXT = (
    "The calibration targets are the measured water depth and velocity; the calibration "
    "parameters are what the calibration varies to reproduce them. Most of them are the "
    "roughness heights ks of the roughness zones. Their ranges are in the roughness "
    "table of the case: a zone with a smallest and a largest ks is calibrated unless its "
    "column calibration says False. Change them there.")

OUTFLOW_TEXT = (
    "The water level at the outflow comes from the rating curve of the outflow "
    "cross-section, which assumes normal flow: an energy slope equal to the bed slope "
    "of the reach. Where a control downstream of the model holds the water up "
    "(backwater), the energy slope is much smaller, and the water stands higher at the "
    "outflow than the rating curve says. The calibration can find that energy slope, or "
    "an offset of the level. One value holds for all calibration situations, so the "
    "energy slope gives one stage-discharge curve, which the validation then uses at "
    "its own discharge.")

FURTHER_TEXT = (
    "Further parameters that fit this case, each with a typical range. Tick the ones "
    "the calibration should vary, and adjust the range. Every parameter costs runs: a "
    "rule of thumb asks for ten TELEMAC runs per parameter.")

#: ``(kind, text)`` of the choices of the tab Outflow level.
OUTFLOW_CHOICES = (("none", "Not calibrated: the level of the rating curve"),
                   ("slope", "Energy slope at the outlet (recommended for backwater)"),
                   ("offset", "Offset of the outflow level"))


def number(value) -> str:
    """A number as the window writes it into a field: 1.00e-5, 0.002, 1.5."""
    value = float(value)
    if value != 0 and (abs(value) < 1e-3 or abs(value) >= 1e4):
        return f"{value:.2e}".replace("e-0", "e-").replace("e+0", "e+")
    return f"{value:.6g}"


class ParametersBox(QGroupBox):
    """Opens the window of the calibration parameters for the active case."""

    def __init__(self, context, parent=None) -> None:
        super().__init__("Calibration parameters", parent)
        self.ctx = context
        self.wizard = None
        layout = QVBoxLayout(self)
        label = QLabel(BOX_TEXT)
        label.setWordWrap(True)
        layout.addWidget(label)
        row = QHBoxLayout()
        self.open_button = QPushButton("Calibration parameters...")
        self.open_button.clicked.connect(self.open_wizard)
        row.addWidget(self.open_button)
        row.addStretch(1)
        layout.addLayout(row)

    def open_wizard(self):
        """Show the window of the active case; the one that is open already where it is for that case."""
        path = self.ctx.project.active_case_path()
        if not path:
            self.ctx.warn("Add a case on the tab Case Setup first.")
            return None
        case = Path(path)
        if self.wizard is not None and not is_deleted(self.wizard):
            if self.wizard.case == case and self.wizard.isVisible():
                self.wizard.raise_()
                self.wizard.activateWindow()
                return self.wizard
            self.wizard.close()
            self.wizard.deleteLater()
        self.wizard = CalibrationParametersWizard(self.ctx, case, self.window())
        self.wizard.show()
        return self.wizard


class CalibrationParametersWizard(QDialog):
    """The calibration parameters of the case at *case*."""

    def __init__(self, context, case: Path, parent=None, *, load: bool = True) -> None:
        super().__init__(parent)
        self.ctx = context
        self.client = context.client
        self.case = Path(case)
        self.answer: dict | None = None
        self.saved = False
        #: the range typed for each way of calibrating the outflow level
        self._ranges: dict[str, list[str]] = {}
        self._kind = "none"
        self.setWindowTitle(f"Calibration parameters: {self.case.name}")
        self.resize(820, 640)
        self._build()
        if load:
            self.load()

    # -- construction -------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        for title, page in zip(TABS, (self._zones_page(), self._outflow_page(),
                                      self._further_page(), self._save_page())):
            self.tabs.addTab(_scrolling(page), title)
        self.tabs.currentChanged.connect(self._tab_changed)
        outer.addWidget(self.tabs, 1)
        self.status = _label("")
        outer.addWidget(self.status)
        buttons = QHBoxLayout()
        help_button = QPushButton("Help")
        help_button.clicked.connect(
            lambda: help_pages.open_window_help("calibration-parameters"))
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

    def _zones_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(ZONES_TEXT))
        self.zone_table = _table(("Zone", "Roughness height ks"))
        layout.addWidget(self.zone_table)
        self.table_label = _label("")
        layout.addWidget(self.table_label)
        row = QHBoxLayout()
        self.table_button = QPushButton("Open the roughness table")
        self.table_button.setEnabled(False)
        self.table_button.clicked.connect(self.open_table)
        row.addWidget(self.table_button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _outflow_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(OUTFLOW_TEXT))
        self.outflow_group = QButtonGroup(self)
        self.outflow_buttons: dict[str, QRadioButton] = {}
        self.outflow_reasons: dict[str, QLabel] = {}
        for kind, text in OUTFLOW_CHOICES:
            button = QRadioButton(text)
            self.outflow_group.addButton(button)
            self.outflow_buttons[kind] = button
            layout.addWidget(button)
            reason = _label("")
            reason.setVisible(False)
            self.outflow_reasons[kind] = reason
            layout.addWidget(reason)
        form = QFormLayout()
        self.low = QLineEdit()
        self.high = QLineEdit()
        form.addRow("Smallest value", self.low)
        form.addRow("Largest value", self.high)
        self.range_box = QWidget()
        self.range_box.setLayout(form)
        layout.addWidget(self.range_box)
        self.bed_slope_label = _label("")
        layout.addWidget(self.bed_slope_label)
        row = QHBoxLayout()
        self.preview_button = QPushButton("Show the outflow levels")
        self.preview_button.setToolTip("The water level at the outflow that the range sets "
                                       "at the discharge of every calibration situation")
        self.preview_button.clicked.connect(self.preview)
        row.addWidget(self.preview_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.preview_table = _table(("Situation", "Discharge (m³/s)",
                                     "Level of the rating curve (m)",
                                     "Level over the range (m)"))
        layout.addWidget(self.preview_table)
        layout.addStretch(1)
        # connected once the fields exist: checking a button switches them
        self.outflow_buttons["none"].setChecked(True)
        for kind, button in self.outflow_buttons.items():
            button.toggled.connect(lambda on, k=kind: on and self._choose(k))
        self._choose("none")
        return page

    def _further_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(FURTHER_TEXT))
        self.further_table = _table(("Calibrate", "Parameter", "Smallest", "Largest"),
                                    editable=True)
        self.further_table.currentCellChanged.connect(
            lambda row, *_: self._show_tip(row))
        layout.addWidget(self.further_table)
        self.tip_label = _label("")
        layout.addWidget(self.tip_label)
        layout.addStretch(1)
        return page

    def _save_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label("What Save writes into the block calibration of the case "
                                "file. The roughness zones stay as the roughness table "
                                "says."))
        self.summary = _label("")
        layout.addWidget(self.summary)
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        layout.addStretch(1)
        return page

    # -- the answer of aXqua ------------------------------------------------------
    def load(self) -> None:
        client, case = self.client, self.case
        self.status.setText("Asking aXqua what the case can calibrate...")
        run_async("aXqua: reading the calibration parameters",
                  lambda: client.calibration_parameters(case),
                  on_success=self.show_answer, on_error=self._failed, owner=self)

    def show_answer(self, answer: dict) -> None:
        self.answer = answer or {}
        self.status.setText("")
        zones = self.answer.get("zones") or []
        self.zone_table.setRowCount(len(zones))
        for row, zone in enumerate(zones):
            self.zone_table.setItem(row, 0, _item(f"Zone {zone['zone']}"))
            self.zone_table.setItem(row, 1, _item(cp.zone_text(zone)))
        table = self.answer.get("roughness_table") or ""
        self.table_label.setText(f"Roughness table: {table}" if table else
                                 "The case has no roughness table.")
        self.table_button.setEnabled(bool(table))
        flow = self.answer.get("outflow") or {}
        current = flow.get("current") or {}
        for kind in ("slope", "offset"):
            entry = flow.get(kind) or {}
            given = current.get("range") if current.get("kind") == kind else None
            values = given or entry.get("suggested") or ["", ""]
            self._ranges[kind] = [number(v) if v != "" else "" for v in values]
            available = bool(entry.get("available"))
            self.outflow_buttons[kind].setEnabled(available or current.get("kind") == kind)
            reason = self.outflow_reasons[kind]
            reason.setText("" if available else
                           f"Not possible for this case: {entry.get('reason', '')}")
            reason.setVisible(not available)
        bed = flow.get("bed_slope")
        self.bed_slope_label.setText(
            f"Bed slope of the reach, which the rating curve uses: {number(bed)}"
            if bed else "")
        chosen = current.get("kind") or "none"
        self._kind = "none"                 # the answer's ranges replace what was typed
        self.outflow_buttons[chosen].setChecked(True)
        self._choose(chosen)
        further = self.answer.get("further") or []
        self.further_table.setRowCount(len(further))
        for row, entry in enumerate(further):
            tick = QTableWidgetItem()
            tick.setFlags(enum_value(_Qt(), "ItemFlag.ItemIsUserCheckable", "ItemIsUserCheckable")
                          | enum_value(_Qt(), "ItemFlag.ItemIsEnabled", "ItemIsEnabled"))
            tick.setCheckState(enum_value(_Qt(), "CheckState.Checked", "Checked")
                               if entry.get("selected") else
                               enum_value(_Qt(), "CheckState.Unchecked", "Unchecked"))
            self.further_table.setItem(row, 0, tick)
            name = _item(entry.get("label") or entry["name"])
            name.setToolTip(entry.get("tips") or "")
            self.further_table.setItem(row, 1, name)
            low, high = entry.get("range") or (entry["min"], entry["max"])
            self.further_table.setItem(row, 2, QTableWidgetItem(number(low)))
            self.further_table.setItem(row, 3, QTableWidgetItem(number(high)))
        self._show_summary()

    def _failed(self, exc) -> None:
        self.status.setText(user_text(exc))

    # -- the outflow level --------------------------------------------------------
    def _choose(self, kind: str) -> None:
        """Switch the range fields to *kind*, keeping what was typed for the one before."""
        if self._kind in ("slope", "offset"):
            self._ranges[self._kind] = [self.low.text(), self.high.text()]
        self._kind = kind
        self.range_box.setVisible(kind != "none")
        self.preview_button.setVisible(kind != "none")
        self.preview_table.setVisible(kind != "none")
        if kind != "none":
            low, high = self._ranges.get(kind) or ["", ""]
            self.low.setText(low)
            self.high.setText(high)
        self.preview_table.setRowCount(0)

    def outflow_choice(self) -> tuple[str, tuple[float, float] | None]:
        """The way and the range chosen on the tab Outflow level (:class:`ValueError` with a sentence for a range that is not one)."""
        kind = self._kind
        if kind == "none":
            return kind, None
        return kind, cp.parse_range(self.low.text(), self.high.text(),
                                    positive=kind == "slope")

    def preview(self) -> None:
        try:
            kind, given = self.outflow_choice()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        if kind == "none":
            return
        client, case = self.client, self.case
        self.status.setText("Computing the outflow levels...")
        run_async("aXqua: outflow levels of the range",
                  lambda: client.calibration_parameters(case, **{kind: given}),
                  on_success=lambda answer: self.show_preview(kind, answer),
                  on_error=self._failed, owner=self)

    def show_preview(self, kind: str, answer: dict) -> None:
        rows = ((answer or {}).get("preview") or {}).get(kind) or []
        self.preview_table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            for column, text in enumerate((
                    entry["name"], f"{float(entry['discharge']):g}",
                    f"{float(entry['level']):.2f}",
                    f"{float(entry['low']):.2f} to {float(entry['high']):.2f}")):
                self.preview_table.setItem(row, column, _item(text))
        self.status.setText("" if rows else "The case names no discharge to show a level for.")

    # -- further parameters -------------------------------------------------------
    def further_choice(self) -> list[dict]:
        """The ticked parameters with their ranges (:class:`ValueError` names the row that is not a range)."""
        chosen = []
        further = (self.answer or {}).get("further") or []
        checked = enum_value(_Qt(), "CheckState.Checked", "Checked")
        for row, entry in enumerate(further):
            tick = self.further_table.item(row, 0)
            if tick is None or tick.checkState() != checked:
                continue
            try:
                low, high = cp.parse_range(self.further_table.item(row, 2).text(),
                                           self.further_table.item(row, 3).text())
            except ValueError as exc:
                raise ValueError(f"{entry.get('label') or entry['name']}: {exc}") from None
            chosen.append({"name": entry["name"], "min": low, "max": high})
        return chosen

    def _show_tip(self, row: int) -> None:
        further = (self.answer or {}).get("further") or []
        self.tip_label.setText(further[row].get("tips", "") if 0 <= row < len(further) else "")

    # -- saving -------------------------------------------------------------------
    def _tab_changed(self, index: int) -> None:
        self.back_button.setVisible(index > 0)
        self.next_button.setVisible(index < len(TABS) - 1)
        if TABS[index] == "Save":
            self._show_summary()

    def _show_summary(self) -> None:
        try:
            kind, given = self.outflow_choice()
            further = self.further_choice()
        except ValueError as exc:
            self.summary.setText(html.escape(str(exc)))
            self.save_button.setEnabled(False)
            return
        self.save_button.setEnabled(self.answer is not None)
        self.summary.setText("\n".join(cp.summary(kind, given, further)))

    def open_table(self) -> None:
        table = (self.answer or {}).get("roughness_table") or ""
        if table:
            QDesktopServices.openUrl(QUrl.fromLocalFile(table))

    def save(self) -> None:
        try:
            kind, given = self.outflow_choice()
            further = self.further_choice()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        client, case = self.client, self.case
        self.status.setText("Saving...")

        def work():
            data = (client.case_read(case) or {}).get("data") or {}
            return client.case_write(case, cp.merged(data, kind, given, further))

        def done(answer: dict) -> None:
            self.saved = True
            count = len((answer or {}).get("findings") or [])
            self.status.setText("Saved. The next calibration uses these parameters." + (
                f" The check of the case found {count} thing(s) to look at." if count
                else ""))
            refresh = getattr(self.ctx, "refresh_case", None)
            if callable(refresh):
                refresh()

        run_async("aXqua: saving the calibration parameters", work, on_success=done,
                  on_error=self._failed, owner=self)


def _Qt():
    from qgis.PyQt.QtCore import Qt

    return Qt


def _item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text))
    item.setFlags(enum_value(_Qt(), "ItemFlag.ItemIsEnabled", "ItemIsEnabled")
                  | enum_value(_Qt(), "ItemFlag.ItemIsSelectable", "ItemIsSelectable"))
    return item


def _table(headers, *, editable: bool = False) -> QTableWidget:
    from qgis.PyQt.QtWidgets import QAbstractItemView, QHeaderView

    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(
        enum_value(QHeaderView, "ResizeMode.ResizeToContents", "ResizeToContents"))
    table.horizontalHeader().setStretchLastSection(True)
    if not editable:
        table.setEditTriggers(enum_value(QAbstractItemView, "EditTrigger.NoEditTriggers",
                                         "NoEditTriggers"))
    return table


def _label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def _scrolling(body: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(body)
    return area
