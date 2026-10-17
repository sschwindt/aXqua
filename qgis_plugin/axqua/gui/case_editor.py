"""The case settings: the settings of one free-surface water body, in a window of its own.

A case file (``*.axq-case``) has about 250 possible settings in 19 blocks. The window
shows them on pages in the order of the workflow (Project settings, Geodata, Liquid
boundaries, Initial conditions, Mesh configuration, Wall roughness, Hydraulic
simulation, Morphodynamic simulation, TELEMAC, OpenFOAM, Calibration and validation,
Post-processing), each page with one or more frames. A page shows the settings a modeler
meets first plus every setting the file already contains; any other setting of a frame
is one choice away (*Add setting...*). Nothing has to be typed that can be selected:
every file has a dialog, a layer that is open in QGIS can be picked from a list, and a
setting with a fixed set of answers is a list.

**What a case needs is marked in the purple of the aXqua icon**, bold: the settings a
minimal case cannot do without, and the settings an option makes necessary as soon as
it is switched on (OpenFOAM, morphodynamics, an unsteady simulation, calibration). The
pages are marked too while one of their required settings is empty. Which settings
these are comes from aXqua (``axqua schema --json``: ``required``, ``required_when``),
not from this plugin.

The pages and frames are built from ``axqua schema --json`` (its ``pages``; an older
aXqua that sends ``sections`` only gets one page per block), so the editor knows a
setting as soon as aXqua does. Reading, writing and checking the file go through
``axqua case`` and ``axqua check``: the plugin never imports aXqua.

**A setting that was not touched is written back exactly as it was read.** Only a row
whose text changed is converted from text, so that opening and saving a case cannot turn
``3`` into ``"3"`` or reorder a list.

Saving rewrites the file from its data, which does not keep the comments of a
hand-written file. aXqua keeps the original as ``<name>.bak`` the first time.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from qgis.PyQt.QtGui import QBrush, QColor, QIcon
from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout,
                                 QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                                 QListWidget, QListWidgetItem, QMenu, QMessageBox,
                                 QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
                                 QStackedWidget, QStyle, QTableWidget, QTableWidgetItem,
                                 QToolButton, QVBoxLayout, QWidget)

from ..compat import enum_value
from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import findings as fnd
from . import help as help_pages
from .editor_base import EditorDialog
from .style import AXQUA_PURPLE, mark_required

FILE_KINDS = ("file", "folder", "vector", "raster", "table")
FILTERS = {
    "vector": "Vector layers (*.gpkg *.shp *.geojson *.json);;All files (*)",
    "raster": "Rasters (*.tif *.tiff *.asc *.vrt);;All files (*)",
    "table": "Tables (*.csv *.xlsx *.txt);;All files (*)",
    "file": "All files (*)",
}
NOT_SET = "(not set)"

#: The settings the measurement table edits instead of a text row of each.
MEASUREMENTS = ("ground_truth.sources", "calibration.validation")

#: Where Help opens for a page, by the docs label of the page.
HELP_PAGES = {"help-calibration": "usage/calibration-validation",
              "help-postprocessing": "usage/postprocessing"}


def dump_text(value) -> str:
    """A list or a table as text. YAML where QGIS has it, which is nearly everywhere."""
    if value in (None, "", [], {}):
        return ""
    try:
        import yaml
        return yaml.safe_dump(value, sort_keys=False, default_flow_style=False,
                              allow_unicode=True).strip()
    except ImportError:                              # pragma: no cover - no PyYAML
        return json.dumps(value, indent=1)


def load_text(text: str):
    """The value a text field holds. Raises ``ValueError`` when it cannot be read."""
    if not text.strip():
        return None
    try:
        import yaml
    except ImportError:                              # pragma: no cover - no PyYAML
        return json.loads(text)
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(" ".join(str(exc).split())) from exc


def stored_path(chosen: str, folder: Path) -> str:
    """How a selected file is written into the case.

    Relative to the folder of the case file where the file is in that folder or close
    to it, so that the case can be moved together with its data. A file somewhere else
    entirely keeps its full path.
    """
    try:
        relative = os.path.relpath(chosen, folder)
    except ValueError:                               # another drive
        return chosen
    climbs = relative.replace("\\", "/").split("/").count("..")
    return relative.replace("\\", "/") if climbs <= 2 else chosen


def parse(kind: str, text: str):
    """The value of a row whose text was changed. ``None`` means "not set"."""
    text = text.strip()
    if text == "":
        return None
    if kind == "int":
        try:
            return int(text)
        except ValueError:
            pass
    if kind in ("int", "number"):
        try:
            return float(text)
        except ValueError:
            return text                              # the check says what is wrong
    if kind == "text":
        for convert in (int, float):                 # "auto" stays a word, 3 a number
            try:
                return convert(text)
            except ValueError:
                continue
    return text


def is_unset(value) -> bool:
    return value in (None, "", [], {})


def condition_met(condition: dict, value_of, block_on) -> bool:
    """Whether one condition of ``required_when`` holds.

    *value_of(key)* is the current value of a setting (its default when unset),
    *block_on(block)* whether a block is switched on.
    """
    if "block" in condition:
        return bool(block_on(condition["block"]))
    return value_of(condition.get("key", "")) in list(condition.get("in") or [])


class Row:
    """One setting: its widget, its triangle, and the value it was filled with."""

    def __init__(self, editor: "CaseEditor", meta: dict) -> None:
        self.editor, self.meta = editor, meta
        self.key, self.kind = meta["key"], meta["kind"]
        self.original = None                 # the value the row was filled with
        self.original_text = ""
        self.error = ""
        self.label_widget: QLabel | None = None
        self.container = QWidget()
        line = QHBoxLayout(self.container)
        line.setContentsMargins(0, 0, 0, 0)
        self.boxes: dict = {}
        if self.kind in ("bool", "choice"):
            self.widget = QComboBox()
            self.widget.addItem(NOT_SET, None)
            if self.kind == "bool":
                self.widget.addItem("yes", True)
                self.widget.addItem("no", False)
            else:
                for choice in meta.get("choices") or []:
                    self.widget.addItem(str(choice), choice)
            self.widget.currentIndexChanged.connect(lambda _=0: editor.changed())
        elif self.kind == "multichoice":
            self.widget = QWidget()
            boxes = QHBoxLayout(self.widget)
            boxes.setContentsMargins(0, 0, 0, 0)
            for choice in meta.get("choices") or []:
                box = QCheckBox(str(choice))
                box.toggled.connect(lambda _=False: editor.changed())
                boxes.addWidget(box)
                self.boxes[choice] = box
            boxes.addStretch(1)
        elif self.kind == "yaml":
            self.widget = QPlainTextEdit()
            self.widget.setMaximumHeight(96)
            self.widget.textChanged.connect(editor.changed)
        else:
            self.widget = QLineEdit()
            default = meta.get("default")
            if default not in (None, "", [], {}):
                self.widget.setPlaceholderText(f"default: {default}")
            self.widget.textChanged.connect(lambda _="": editor.changed())
        self.widget.setToolTip(self.tooltip())
        line.addWidget(self.widget, 1)
        if self.kind in FILE_KINDS:
            browse = QPushButton("...")
            browse.setMaximumWidth(36)
            browse.setToolTip("Select the " + ("folder" if self.kind == "folder"
                                               else "file"))
            browse.clicked.connect(self.browse)
            line.addWidget(browse)
        if self.kind in ("vector", "raster"):
            layers = QToolButton()
            layers.setText("Layers")
            layers.setToolTip("Use a layer that is open in QGIS")
            layers.clicked.connect(lambda: self.pick_layer(layers))
            line.addWidget(layers)
        self.triangle = fnd.TriangleButton()
        line.addWidget(self.triangle)

    def tooltip(self) -> str:
        return (self.meta.get("help") or self.meta["label"]) + f"\n({self.key})"

    def label(self) -> str:
        unit = self.meta.get("unit") or ""
        return self.meta["label"] + (f" [{unit}]" if unit and unit != "-" else "")

    # -- value ----------------------------------------------------------------
    def text(self) -> str:
        if self.kind == "multichoice":
            return ",".join(c for c, box in self.boxes.items() if box.isChecked())
        if isinstance(self.widget, QComboBox):
            return self.widget.currentText()
        if isinstance(self.widget, QPlainTextEdit):
            return self.widget.toPlainText()
        return self.widget.text()

    def fill(self, value) -> None:
        self.original = copy.deepcopy(value)
        if self.kind == "multichoice":
            chosen = list(value) if isinstance(value, (list, tuple)) else (
                list(self.meta.get("default") or []) if value is None else [value])
            for choice, box in self.boxes.items():
                box.blockSignals(True)
                box.setChecked(choice in chosen)
                box.blockSignals(False)
        elif isinstance(self.widget, QComboBox):
            index = self.widget.findData(value) if value is not None else 0
            if index < 0:                    # a value the list does not offer: keep it
                self.widget.addItem(str(value), value)
                index = self.widget.count() - 1
            self.widget.setCurrentIndex(index)
        elif isinstance(self.widget, QPlainTextEdit):
            self.widget.setPlainText(dump_text(value))
        else:
            self.widget.setText("" if value is None else str(value))
        self.original_text = self.text()

    def value(self):
        """The value to write. Untouched rows give back exactly what they were given."""
        self.error = ""
        if self.text() == self.original_text:
            return self.original
        if self.kind == "multichoice":
            return [c for c, box in self.boxes.items() if box.isChecked()]
        if isinstance(self.widget, QComboBox):
            return self.widget.currentData()
        if isinstance(self.widget, QPlainTextEdit):
            try:
                return load_text(self.widget.toPlainText())
            except ValueError as exc:
                self.error = str(exc)
                return self.original         # the rest of the case is still saved
        return parse(self.kind, self.widget.text())

    def peek(self):
        """The value the row holds now, without recording an error."""
        error = self.error
        try:
            return self.value()
        finally:
            self.error = error

    # -- selecting instead of typing ------------------------------------------
    def browse(self) -> None:
        folder = self.editor.folder
        current = self.widget.text().strip()
        start = str((folder / current) if current and not os.path.isabs(current)
                    else (current or folder))
        if self.kind == "folder":
            chosen = QFileDialog.getExistingDirectory(self.widget, self.meta["label"],
                                                      start)
        else:
            chosen, _ = QFileDialog.getOpenFileName(
                self.widget, self.meta["label"], start,
                FILTERS.get(self.kind, FILTERS["file"]))
        if chosen:
            self.widget.setText(stored_path(chosen, folder))

    def pick_layer(self, button) -> None:
        """A menu of the layers of the QGIS project that are of the right kind."""
        from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer

        wanted = QgsRasterLayer if self.kind == "raster" else QgsVectorLayer
        menu = QMenu(button)
        for layer in QgsProject.instance().mapLayers().values():
            source = layer.source().split("|")[0]
            if isinstance(layer, wanted) and os.path.isfile(source):
                action = menu.addAction(layer.name())
                action.triggered.connect(
                    lambda _=False, s=source: self.widget.setText(
                        stored_path(s, self.editor.folder)))
        if menu.isEmpty():
            menu.addAction("No layer of this kind is open in QGIS").setEnabled(False)
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))


class MeasurementTable(QWidget):
    """The measurement files of a case, each for the calibration or the validation.

    One row per file. A file used for the calibration is an entry of
    ``ground_truth.sources``; a file used for the validation belongs to a validation
    situation of ``calibration.validation``, which has a name and the discharge of
    that situation. A validation needs data of another flow situation, so a whole file
    is assigned, never a share of one (the user's rule of 2026-10-08).
    """

    COLUMNS = ("File", "Positions", "Kind", "Use for", "Situation", "Discharge [m3/s]")
    ROLES = (("calibration", "Calibration"), ("validation", "Validation"))
    KINDS = ("points", "flowtracker")
    #: The kind of a source that names none (``GroundTruthSource.kind`` of aXqua).
    DEFAULT_KIND = "flowtracker"

    def __init__(self, editor: "CaseEditor", sources: list, situations: list) -> None:
        super().__init__()
        self.editor = editor
        self.original = (copy.deepcopy(sources or []), copy.deepcopy(situations or []))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel("Each file of measurements is used for the calibration or for the "
                      "validation. A validation needs measurements of another flow "
                      "situation: name the situation and give its discharge.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(140)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        add = QPushButton("Add file...")
        add.setToolTip("A point layer or a table with coordinates of measured values")
        add.clicked.connect(self.add_file)
        row.addWidget(add)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_selected)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        self.triangle = fnd.TriangleButton()
        row.addWidget(self.triangle)
        self._rows: list[dict] = []
        for source in sources or []:
            self._append(source, "calibration", "", None)
        for situation in situations or []:
            for source in situation.get("sources") or []:
                self._append(source, "validation", str(situation.get("name") or ""),
                             situation)

    # -- rows -----------------------------------------------------------------------
    def _append(self, source: dict, role: str, name: str, situation) -> None:
        index = self.table.rowCount()
        self.table.insertRow(index)
        source = dict(source or {})
        entry = {"source": source}
        self._rows.append(entry)
        for column, key in ((0, "values"), (1, "positions")):
            item = QTableWidgetItem(str(source.get(key) or ""))
            item.setToolTip(str(source.get(key) or ""))
            self.table.setItem(index, column, item)
        kind = QComboBox()
        for value in self.KINDS:
            kind.addItem(value, value)
        if source.get("kind") and kind.findData(source["kind"]) < 0:
            kind.addItem(str(source["kind"]), source["kind"])
        kind.setCurrentIndex(max(kind.findData(source.get("kind") or self.DEFAULT_KIND),
                                 0))
        kind.currentIndexChanged.connect(lambda _=0: self.editor.changed())
        self.table.setCellWidget(index, 2, kind)
        use = QComboBox()
        for value, text in self.ROLES:
            use.addItem(text, value)
        use.setCurrentIndex(use.findData(role))
        self.table.setCellWidget(index, 3, use)
        situation_edit = QLineEdit(name)
        situation_edit.setPlaceholderText("for example High flow")
        self.table.setCellWidget(index, 4, situation_edit)
        flow = (situation or {}).get("prescribed_flowrate")
        discharge = QLineEdit("" if flow is None else str(flow))
        self.table.setCellWidget(index, 5, discharge)
        entry.update(kind=kind, use=use, situation=situation_edit, discharge=discharge)
        use.currentIndexChanged.connect(lambda _=0, e=entry: self._role_changed(e))
        situation_edit.textChanged.connect(lambda _="": self.editor.changed())
        # when the name is complete, not at every letter of it
        situation_edit.editingFinished.connect(lambda e=entry: self._joined(e))
        discharge.textChanged.connect(lambda text, e=entry: self._discharge_changed(e, text))
        self._role_changed(entry, quiet=True)

    def _role_changed(self, entry: dict, *, quiet: bool = False) -> None:
        validation = entry["use"].currentData() == "validation"
        entry["situation"].setEnabled(validation)
        entry["discharge"].setEnabled(validation)
        entry["discharge"].setToolTip("" if validation else
                                      "The calibration uses the discharge of the case.")
        if quiet:
            return
        # a validation situation is part of the calibration block: with its switch
        # off, saving would leave the situation out
        switch = getattr(self.editor, "switches", {}).get("calibration")
        if validation and switch is not None and not switch.isChecked():
            switch.setChecked(True)
        self._joined(entry)

    @staticmethod
    def _situation_of(entry: dict) -> str | None:
        """The situation a row belongs to, None for a file of the calibration."""
        if entry["use"].currentData() != "validation":
            return None
        return entry["situation"].text().strip() or "validation"

    def _others(self, entry: dict) -> list[dict]:
        """The other rows of the situation of *entry*."""
        name = self._situation_of(entry)
        return [other for other in self._rows
                if other is not entry and name is not None
                and self._situation_of(other) == name]

    @staticmethod
    def _show(entry: dict, text: str) -> None:
        entry["discharge"].blockSignals(True)
        entry["discharge"].setText(text)
        entry["discharge"].blockSignals(False)

    def _discharge_changed(self, entry: dict, text: str) -> None:
        """The discharge is one of the situation: the other files of the same situation show it too."""
        for other in self._others(entry):
            if other["discharge"].text() != text:
                self._show(other, text)
        self.editor.changed()

    def _joined(self, entry: dict) -> None:
        """A file that joins a validation situation, by its name or by its role, shows the discharge of the situation; a situation without one takes the file's."""
        given = next((o["discharge"].text() for o in self._others(entry)
                      if o["discharge"].text().strip()), None)
        if given is not None:
            self._show(entry, given)
        elif self._situation_of(entry) is not None and entry["discharge"].text().strip():
            for other in self._others(entry):
                self._show(other, entry["discharge"].text())
        self.editor.changed()

    def add_file(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Add a file of measurements", str(self.editor.folder),
            "Point layers and tables (*.gpkg *.shp *.geojson *.csv *.xlsx);;All files (*)")
        if chosen:
            self.add(stored_path(chosen, self.editor.folder))

    def add(self, path: str, role: str = "calibration") -> None:
        self._append({"category": "hydraulics", "kind": "points", "values": path},
                     role, "", None)
        self.editor.changed()

    def remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()},
                      reverse=True)
        for row in rows:
            self.table.removeRow(row)
            del self._rows[row]
        if rows:
            self.editor.changed()

    def set_role(self, row: int, role: str, situation: str = "",
                 discharge: str = "") -> None:
        entry = self._rows[row]
        before = self._situation_of(entry)
        after = (situation.strip() or "validation") if role == "validation" else None
        entry["situation"].setText(situation)
        if after != before:
            self._show(entry, "")     # the discharge of its former situation is not this one's
        if entry["use"].currentData() == role:
            self._joined(entry)
        else:
            entry["use"].setCurrentIndex(entry["use"].findData(role))   # joins as well
        if discharge:
            entry["discharge"].setText(discharge)

    # -- values ---------------------------------------------------------------------
    def values(self) -> tuple[list, list]:
        """``(ground_truth.sources, calibration.validation)`` as the table states them.

        A situation keeps what the table does not show (its discharges per inflow, its
        outflow level, its duration). An untouched table returns what it was given.
        """
        sources, situations, by_name = [], [], {}
        old_situations = {str(s.get("name") or ""): s for s in self.original[1]}
        flows: dict[str, float | None] = {}
        for index, entry in enumerate(self._rows):
            source = dict(entry["source"])
            for column, key in ((0, "values"), (1, "positions")):
                item = self.table.item(index, column)
                text = item.text().strip() if item is not None else ""
                if text != str(source.get(key) or ""):
                    if text:
                        source[key] = text
                    else:
                        source.pop(key, None)
            kind = entry["kind"].currentData()
            if kind and kind != (source.get("kind") or self.DEFAULT_KIND):
                source["kind"] = kind
            if entry["use"].currentData() == "calibration":
                sources.append(source)
                continue
            name = entry["situation"].text().strip() or "validation"
            if name not in by_name:
                old = old_situations.get(name) or old_situations.get(
                    "" if name == "validation" else name) or {"name": name}
                situation = {k: copy.deepcopy(v) for k, v in old.items() if k != "sources"}
                if "name" in old or name != "validation":
                    situation["name"] = name
                situation["sources"] = []
                by_name[name] = situation
                situations.append(situation)
            by_name[name]["sources"].append(source)
            # one file of the situation with a discharge is enough
            flow = parse("number", entry["discharge"].text())
            if flow is not None or name not in flows:
                flows[name] = flow if flow is not None else flows.get(name)
        for name, flow in flows.items():
            situation = by_name[name]
            if flow is not None:
                situation["prescribed_flowrate"] = flow
            elif not situation.get("inflows"):
                situation.pop("prescribed_flowrate", None)
        if (sources, situations) == (self.original[0], self.original[1]):
            return copy.deepcopy(self.original[0]), copy.deepcopy(self.original[1])
        return sources, situations

    def discharge_conflicts(self) -> list[str]:
        """Validation situations whose files show different discharges. The table keeps them equal; this is the guard for what it could not."""
        seen: dict[str, set[float]] = {}
        for entry in self._rows:
            name = self._situation_of(entry)
            flow = parse("number", entry["discharge"].text())
            if name is not None and flow is not None:
                seen.setdefault(name, set()).add(flow)
        return [name for name, flows in seen.items() if len(flows) > 1]

    def missing_discharge(self) -> list[str]:
        """Validation situations without a discharge: required for a validation."""
        _sources, situations = self.values()
        return [str(s.get("name") or "validation") for s in situations
                if s.get("prescribed_flowrate") is None and not s.get("inflows")]


class CaseEditor(EditorDialog):
    """Edit one case file."""

    what = "case"

    def __init__(self, client, path, data: dict, schema: dict, *, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.path = Path(path)
        self.folder = self.path.parent
        self.setWindowTitle(f"Case settings: {self.path.name}")
        self.resize(980, 720)
        self.sections = list((schema or {}).get("sections") or [])
        self.pages = list((schema or {}).get("pages") or []) or self._pages_of_sections()
        self._meta_by_key = {meta["key"]: meta for section in self.sections
                             for meta in section["fields"]}
        self._saved = copy.deepcopy(data or {})
        self._base = copy.deepcopy(data or {})
        self.rows: dict[str, Row] = {}
        self._forms: dict[str, QFormLayout] = {}        # frame id -> form
        self._frame_of: dict[str, str] = {}             # key -> frame id
        self._frame_boxes: dict[str, QWidget] = {}
        self._adders: dict[str, QComboBox] = {}
        self._problems: dict[str, fnd.TriangleButton] = {}
        self.switches: dict[str, QCheckBox] = {}         # block -> switch
        self.measurements: MeasurementTable | None = None
        self.findings: list[fnd.Finding] = []
        self._building = True
        self._build()
        self._building = False
        self.update_required()

    def _pages_of_sections(self) -> list[dict]:
        """One page per block, for an aXqua that sends no pages."""
        return [{"key": section["block"], "title": section["title"],
                 "label": section.get("label", ""), "switch": {},
                 "frames": [{"title": "", "info": "",
                             "keys": [meta["key"] for meta in section["fields"]]}]}
                for section in self.sections]

    # -- construction -------------------------------------------------------------
    def _build(self) -> None:
        self.set_header(
            f"{self.path}\nSaving rewrites the file from its settings and does not keep "
            "the comments of a hand-written file. The original is kept as "
            f"{self.path.name}.bak the first time. Entries in bold purple are required "
            "for this case.")
        splitter = QSplitter()
        self.section_list = QListWidget()
        self.section_list.setMaximumWidth(250)
        self.stack = QStackedWidget()
        splitter.addWidget(self.section_list)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 1)
        self.outer.addWidget(splitter, 1)
        for page in self.pages:
            self._add_page(page)
        self.section_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        if self.pages:
            self.section_list.setCurrentRow(0)
        self.finish_layout("The case is checked when it is saved.")
        help_button = QPushButton("Help")
        help_button.setToolTip("Open the documentation of the page that is showing")
        help_button.clicked.connect(self.open_help)
        self.outer.itemAt(self.outer.count() - 1).layout().insertWidget(0, help_button)

    def _add_page(self, page: dict) -> None:
        self.section_list.addItem(QListWidgetItem(page["title"]))
        body = QWidget()
        layout = QVBoxLayout(body)
        top = QHBoxLayout()
        switch = page.get("switch") or {}
        if switch:
            box = QCheckBox(switch.get("label") or "Use")
            box.setChecked(page["key"] in self._base)
            box.setStyleSheet("QCheckBox { font-weight: bold; }")
            box.toggled.connect(lambda on, p=page: self._switched(p, on))
            self.switches[page["key"]] = box
            top.addWidget(box)
        problem = fnd.TriangleButton()
        self._problems[page["key"]] = problem
        top.addWidget(problem)
        top.addStretch(1)
        layout.addLayout(top)
        for frame in page["frames"]:
            layout.addWidget(self._frame(page, frame))
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        self.stack.addWidget(scroll)

    def _frame_id(self, page: dict, frame: dict) -> str:
        return page["key"] if not frame.get("title") else f"{page['key']}:{frame['title']}"

    def _frame(self, page: dict, frame: dict) -> QWidget:
        ident = self._frame_id(page, frame)
        holder = QGroupBox(frame["title"]) if frame.get("title") else QWidget()
        inner = QVBoxLayout(holder)
        if not frame.get("title"):
            inner.setContentsMargins(0, 0, 0, 0)
        if frame.get("info"):
            info = QHBoxLayout()
            button = QToolButton()
            button.setIcon(self.style().standardIcon(enum_value(
                QStyle, "StandardPixmap.SP_MessageBoxInformation",
                "SP_MessageBoxInformation")))
            button.setToolTip(frame["info"])
            button.setAutoRaise(True)
            title = frame.get("title") or page["title"]
            button.clicked.connect(
                lambda _=False, t=title, text=frame["info"]: QMessageBox.information(
                    self, t, text))
            info.addWidget(button)
            hint = QLabel("Why this matters")
            hint.setToolTip(frame["info"])
            info.addWidget(hint)
            info.addStretch(1)
            inner.addLayout(info)
        form = QFormLayout()
        self._forms[ident] = form
        inner.addLayout(form)
        keys = [key for key in frame["keys"] if key in self._meta_by_key]
        for key in keys:
            self._frame_of[key] = ident
        if any(key in MEASUREMENTS for key in keys) and self.measurements is None:
            self._add_measurements(form)
        adder = QComboBox()
        adder.addItem("Add setting...", None)
        for key in keys:
            meta = self._meta_by_key[key]
            if key in MEASUREMENTS:
                continue
            block, name = key.split(".", 1)
            present = self._base.get(block) if isinstance(self._base.get(block), dict) \
                else {}
            if meta["essential"] or name in present:
                self._add_row(meta, present.get(name))
            else:
                adder.addItem(f"{meta['label']}  ({meta['name']})", key)
        adder.activated.connect(lambda _index, f=ident: self._add_chosen(f))
        adder.setVisible(adder.count() > 1)
        self._adders[ident] = adder
        inner.addWidget(adder)
        self._frame_boxes[ident] = holder
        return holder

    def _add_measurements(self, form: QFormLayout) -> None:
        ground = self._base.get("ground_truth") or {}
        calibration = self._base.get("calibration") or {}
        validation = calibration.get("validation") or []
        if isinstance(validation, dict):
            validation = [validation]
        self.measurements = MeasurementTable(self, ground.get("sources") or [], validation)
        self.measurement_label = QLabel("Measurement files")
        form.addRow(self.measurement_label, self.measurements)

    def _meta(self, key: str) -> dict | None:
        return self._meta_by_key.get(key)

    def _add_row(self, meta: dict, value=None) -> Row:
        row = Row(self, meta)
        row.fill(value)
        label = QLabel(row.label())
        label.setToolTip(row.tooltip())
        row.label_widget = label
        ident = self._frame_of.get(meta["key"]) or next(iter(self._forms))
        self._forms[ident].addRow(label, row.container)
        self.rows[meta["key"]] = row
        return row

    def _add_chosen(self, ident: str) -> None:
        adder = self._adders[ident]
        key = adder.currentData()
        if key:
            self.ensure_row(key)
            adder.removeItem(adder.currentIndex())
        adder.setCurrentIndex(0)
        adder.setVisible(adder.count() > 1)

    def ensure_row(self, key: str) -> Row | None:
        """The row of a setting, added if the form does not show it yet."""
        if key in self.rows:
            return self.rows[key]
        meta = self._meta(key)
        if meta is None or key in MEASUREMENTS:
            return None
        row = self._add_row(meta)
        adder = self._adders.get(self._frame_of.get(key, ""))
        if adder is not None:
            index = adder.findData(key)
            if index > 0:
                adder.removeItem(index)
            adder.setVisible(adder.count() > 1)
        self.update_required()
        return row

    def show_block(self, block: str) -> None:
        """Bring the page with the settings of *block* to the front."""
        for index, page in enumerate(self.pages):
            for frame in page["frames"]:
                if any(key.split(".", 1)[0] == block for key in frame["keys"]):
                    self.section_list.setCurrentRow(index)
                    return

    def open_help(self) -> None:
        label = "help-case-setup"
        if self.pages:
            index = max(self.section_list.currentRow(), 0)
            label = self.pages[index].get("label") or label
        page = HELP_PAGES.get(label, "usage/case-setup")
        help_pages.open_url(f"{help_pages.PUBLISHED}{page}.html#{label}")

    # -- switches -------------------------------------------------------------------
    def _switched(self, page: dict, on: bool) -> None:
        """A page whose block exists only when the case asks for it."""
        block = page["key"]
        if on:
            for name, value in ((page.get("switch") or {}).get("seed") or {}).items():
                row = self.ensure_row(f"{block}.{name}")
                if row is not None and is_unset(row.peek()):
                    if row.kind == "yaml":
                        row.widget.setPlainText(dump_text(value))
                    elif isinstance(row.widget, QComboBox):
                        row.widget.setCurrentIndex(max(row.widget.findData(value), 0))
                    elif row.kind == "multichoice":
                        for choice, box in row.boxes.items():
                            box.setChecked(choice in value)
                    else:
                        row.widget.setText(str(value))
        self.changed()

    def block_on(self, block: str) -> bool:
        if block in self.switches:
            return self.switches[block].isChecked()
        return block in self._base

    def _apply_switches(self) -> None:
        """A block that is switched off has its settings grayed out wherever they are
        shown, the OpenFOAM lattice on the mesh page included."""
        for key, row in self.rows.items():
            block = key.split(".", 1)[0]
            if block in self.switches:
                on = self.switches[block].isChecked()
                row.container.setEnabled(on)
                if row.label_widget is not None:
                    row.label_widget.setEnabled(on)

    # -- what is required -----------------------------------------------------------
    def value_of(self, key: str):
        """The current value of a setting: its row, the case file, or its default."""
        row = self.rows.get(key)
        if row is not None:
            value = row.peek()
        else:
            block, _, name = key.partition(".")
            value = (self._base.get(block) or {}).get(name) \
                if isinstance(self._base.get(block), dict) else None
        if is_unset(value):
            meta = self._meta(key) or {}
            return meta.get("default")
        return value

    def is_required(self, key: str) -> bool:
        meta = self._meta(key) or {}
        block = key.split(".", 1)[0]
        if block in self.switches and not self.switches[block].isChecked():
            return False
        if meta.get("required"):
            return True
        return any(condition_met(c, self.value_of, self.block_on)
                   for c in meta.get("required_when") or [])

    def missing_required(self, required: dict[str, bool] | None = None) -> list[str]:
        """Required settings that are still empty. A default fills a setting: the
        purple label says that it matters, not that it has to be typed. *required* is
        what :meth:`is_required` said of each row, for a caller that asked already."""
        if required is None:
            required = {key: self.is_required(key) for key in self.rows}
        missing = [key for key in self.rows
                   if required.get(key) and is_unset(self.value_of(key))]
        if self.measurements is not None and self.is_required("ground_truth.sources"):
            sources, _ = self.measurements.values()
            if not sources:
                missing.append("ground_truth.sources")
        return missing

    def changed(self) -> None:
        if not getattr(self, "_building", True):
            self.update_required()

    def update_required(self) -> None:
        """Mark the required settings in aXqua purple, and the pages that still lack
        one of them."""
        self._apply_switches()
        required = {key: self.is_required(key) for key in self.rows}
        for key, row in self.rows.items():
            if row.label_widget is not None:
                mark_required(row.label_widget, required[key])
        if self.measurements is not None:
            mark_required(self.measurement_label,
                          self.is_required("ground_truth.sources"))
        missing = set(self.missing_required(required))
        for index, page in enumerate(self.pages):
            keys = {key for frame in page["frames"] for key in frame["keys"]}
            item = self.section_list.item(index)
            lacking = bool(keys & missing)
            font = item.font()
            font.setBold(lacking)
            item.setFont(font)
            item.setForeground(QBrush(QColor(AXQUA_PURPLE)) if lacking else QBrush())
            item.setToolTip("A required setting of this page is empty." if lacking
                            else "")

    # -- values -------------------------------------------------------------------
    def values(self) -> dict:
        """The case as the rows state it. Blocks and settings without a row are kept;
        a block that is switched off is left out."""
        data = copy.deepcopy(self._base)
        for key, row in self.rows.items():
            block, name = key.split(".", 1)
            value = row.value()
            section = data.get(block)
            if value in (None, ""):
                if isinstance(section, dict):
                    section.pop(name, None)
                    if not section:
                        data.pop(block, None)
                continue
            if not isinstance(section, dict):
                section = data[block] = {}
            section[name] = value
        if self.measurements is not None:
            sources, situations = self.measurements.values()
            for block, name, value in (("ground_truth", "sources", sources),
                                       ("calibration", "validation", situations)):
                section = data.get(block)
                if value:
                    if not isinstance(section, dict):
                        section = data[block] = {}
                    section[name] = value
                elif isinstance(section, dict):
                    section.pop(name, None)
                    if not section:
                        data.pop(block, None)
        for block, switch in self.switches.items():
            if not switch.isChecked():
                data.pop(block, None)
        return data

    @property
    def dirty(self) -> bool:
        return self.values() != self._saved

    # -- Save ---------------------------------------------------------------------
    def save(self) -> None:
        """Write the case, then check it. Never refused because of a finding."""
        data = self.values()
        unreadable = [fnd.Finding("error", "axqua.config.yaml_syntax",
                                  f"{row.meta['label']}: the entry cannot be read "
                                  f"({row.error}). Its previous content was kept.",
                                  subject=key)
                      for key, row in self.rows.items() if row.error]
        unreadable += [fnd.Finding(
            "warning", "axqua.config.incomplete",
            f"{(self._meta(key) or {}).get('label', key)}: required for this case and "
            "not set yet.", subject=key) for key in self.missing_required()]
        # what is said of the validation situations is said of situations that are saved
        if self.measurements is not None and (data.get("calibration") or {}).get("validation"):
            unreadable += [fnd.Finding(
                "warning", "axqua.config.incomplete",
                f"The validation situation {name!r} has no discharge.",
                subject="calibration.validation")
                for name in self.measurements.missing_discharge()]
            unreadable += [fnd.Finding(
                "warning", "axqua.config.discharge_conflict",
                f"The files of the validation situation {name!r} show different "
                "discharges; the last one in the table was saved.",
                subject="calibration.validation")
                for name in self.measurements.discharge_conflicts()]
        self.save_button.setEnabled(False)
        self.status.setText("Saving and checking the case...")
        client, path = self.client, self.path
        run_async("aXqua: saving the case", lambda: client.case_write(path, data),
                  on_success=lambda answer: self._saved_ok(data, answer, unreadable),
                  on_error=self._save_failed, owner=self)

    def _saved_ok(self, data: dict, answer: dict, unreadable: list) -> None:
        self._saved = copy.deepcopy(data)
        self._base = copy.deepcopy(data)
        self.saved_once = True
        self.save_button.setEnabled(True)
        backup = (answer or {}).get("backup")
        self.status.setText(f"Saved {self.path}."
                            + (f" The original is kept as {Path(backup).name}."
                               if backup else ""))
        found = fnd.from_payload(answer)
        # what the check of aXqua reports itself is not said twice
        reported = {f.subject for f in found}
        own = [f for f in unreadable
               if f.code != "axqua.config.incomplete" or f.subject not in reported]
        self.show_findings(own + found)

    def _save_failed(self, exc: Exception) -> None:
        self.save_button.setEnabled(True)
        self.status.setText("The case was not saved: " + user_text(exc))

    # -- findings -----------------------------------------------------------------
    def show_findings(self, findings) -> None:
        """A triangle at each setting concerned, at its page, and in the list."""
        self.findings = list(findings)
        self.finding_list.set_findings(self.findings, checked=True)
        for finding in self.findings:                 # a row for what has none yet
            if "." in finding.subject:
                self.ensure_row(finding.subject)
        for key, row in self.rows.items():
            row.triangle.set_findings(
                [item for item in self.findings if item.subject == key])
        if self.measurements is not None:
            self.measurements.triangle.set_findings(
                [item for item in self.findings if item.subject in MEASUREMENTS])
        for index, page in enumerate(self.pages):
            keys = {key for frame in page["frames"] for key in frame["keys"]}
            blocks = {key.split(".", 1)[0] for key in keys}
            concerned = [item for item in self.findings
                         if item.subject in keys or item.subject in blocks]
            level = fnd.worst(concerned)
            self.section_list.item(index).setIcon(
                fnd.triangle(level) if level else _no_icon())
            # what is about a block as a whole, not about one of its rows
            self._problems[page["key"]].set_findings(
                [item for item in concerned if item.subject not in self.rows
                 and item.subject not in MEASUREMENTS])
        self.update_required()


def _no_icon():
    return QIcon()
