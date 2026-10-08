"""The case editor: the settings of one river reach, in a window of its own.

A case file (``*.axq-case``) has about 250 possible settings in 18 blocks. The editor
shows one block at a time, and in it the settings a modeler meets first plus every
setting the file already contains. Any other setting of the block is one choice away
(*Add setting...*). Nothing has to be typed that can be selected: every file has a
dialog, and a layer that is open in QGIS can be picked from a list.

The forms are built from ``axqua schema --json``, so the editor knows a setting as soon
as aXqua does. Reading, writing and checking the file go through ``axqua case`` and
``axqua check``: the plugin never imports aXqua.

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

from qgis.PyQt.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
                                 QLabel, QLineEdit, QListWidget, QListWidgetItem,
                                 QMenu, QPlainTextEdit, QPushButton, QScrollArea,
                                 QSplitter, QStackedWidget, QToolButton, QVBoxLayout,
                                 QWidget)

from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import findings as fnd
from .editor_base import EditorDialog

FILE_KINDS = ("file", "folder", "vector", "raster", "table")
FILTERS = {
    "vector": "Vector layers (*.gpkg *.shp *.geojson *.json);;All files (*)",
    "raster": "Rasters (*.tif *.tiff *.asc *.vrt);;All files (*)",
    "table": "Tables (*.csv *.xlsx *.txt);;All files (*)",
    "file": "All files (*)",
}
NOT_SET = "(not set)"


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


class Row:
    """One setting: its widget, its triangle, and the value it was filled with."""

    def __init__(self, editor: "CaseEditor", meta: dict) -> None:
        self.editor, self.meta = editor, meta
        self.key, self.kind = meta["key"], meta["kind"]
        self.original = None                 # the value the row was filled with
        self.original_text = ""
        self.error = ""
        self.container = QWidget()
        line = QHBoxLayout(self.container)
        line.setContentsMargins(0, 0, 0, 0)
        if self.kind in ("bool", "choice"):
            self.widget = QComboBox()
            self.widget.addItem(NOT_SET, None)
            if self.kind == "bool":
                self.widget.addItem("yes", True)
                self.widget.addItem("no", False)
            else:
                for choice in meta.get("choices") or []:
                    self.widget.addItem(str(choice), choice)
        elif self.kind == "yaml":
            self.widget = QPlainTextEdit()
            self.widget.setMaximumHeight(96)
        else:
            self.widget = QLineEdit()
            default = meta.get("default")
            if default not in (None, "", [], {}):
                self.widget.setPlaceholderText(f"default: {default}")
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
        if isinstance(self.widget, QComboBox):
            return self.widget.currentText()
        if isinstance(self.widget, QPlainTextEdit):
            return self.widget.toPlainText()
        return self.widget.text()

    def fill(self, value) -> None:
        self.original = copy.deepcopy(value)
        if isinstance(self.widget, QComboBox):
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
        if isinstance(self.widget, QComboBox):
            return self.widget.currentData()
        if isinstance(self.widget, QPlainTextEdit):
            try:
                return load_text(self.widget.toPlainText())
            except ValueError as exc:
                self.error = str(exc)
                return self.original         # the rest of the case is still saved
        return parse(self.kind, self.widget.text())

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


class CaseEditor(EditorDialog):
    """Edit one case file."""

    what = "case"

    def __init__(self, client, path, data: dict, schema: dict, *, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.path = Path(path)
        self.folder = self.path.parent
        self.setWindowTitle(f"aXqua case: {self.path.name}")
        self.resize(940, 700)
        self.sections = list((schema or {}).get("sections") or [])
        self._saved = copy.deepcopy(data or {})
        self._base = copy.deepcopy(data or {})
        self.rows: dict[str, Row] = {}
        self._forms: dict[str, QFormLayout] = {}
        self._adders: dict[str, QComboBox] = {}
        self._problems: dict[str, fnd.TriangleButton] = {}
        self.findings: list[fnd.Finding] = []
        self._build()

    # -- construction -------------------------------------------------------------
    def _build(self) -> None:
        self.set_header(
            f"{self.path}\nSaving rewrites the file from its settings and does not keep "
            "the comments of a hand-written file. The original is kept as "
            f"{self.path.name}.bak the first time.")
        splitter = QSplitter()
        self.section_list = QListWidget()
        self.section_list.setMaximumWidth(250)
        self.pages = QStackedWidget()
        splitter.addWidget(self.section_list)
        splitter.addWidget(self.pages)
        splitter.setStretchFactor(1, 1)
        self.outer.addWidget(splitter, 1)
        for section in self.sections:
            self._add_section(section)
        self.section_list.currentRowChanged.connect(self.pages.setCurrentIndex)
        if self.sections:
            self.section_list.setCurrentRow(0)
        self.finish_layout("The case is checked when it is saved.")

    def _add_section(self, section: dict) -> None:
        block = section["block"]
        self.section_list.addItem(QListWidgetItem(section["title"]))
        body = QWidget()
        layout = QVBoxLayout(body)
        problem_row = QHBoxLayout()
        problem = fnd.TriangleButton()
        self._problems[block] = problem
        problem_row.addWidget(problem)
        problem_row.addStretch(1)
        layout.addLayout(problem_row)
        form = QFormLayout()
        self._forms[block] = form
        layout.addLayout(form)
        present = self._base.get(block) if isinstance(self._base.get(block), dict) else {}
        for meta in section["fields"]:
            if meta["essential"] or meta["name"] in present:
                self._add_row(meta, present.get(meta["name"]))
        adder = QComboBox()
        adder.addItem("Add setting...", None)
        for meta in section["fields"]:
            if meta["key"] not in self.rows:
                adder.addItem(f"{meta['label']}  ({meta['name']})", meta["key"])
        adder.activated.connect(lambda _index, b=block: self._add_chosen(b))
        adder.setVisible(adder.count() > 1)
        self._adders[block] = adder
        layout.addWidget(adder)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        self.pages.addWidget(scroll)

    def _meta(self, key: str) -> dict | None:
        for section in self.sections:
            for meta in section["fields"]:
                if meta["key"] == key:
                    return meta
        return None

    def _add_row(self, meta: dict, value=None) -> Row:
        row = Row(self, meta)
        row.fill(value)
        label = QLabel(row.label())
        label.setToolTip(row.tooltip())
        self._forms[meta["block"]].addRow(label, row.container)
        self.rows[meta["key"]] = row
        return row

    def _add_chosen(self, block: str) -> None:
        adder = self._adders[block]
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
        return self._add_row(meta) if meta else None

    def show_block(self, block: str) -> None:
        blocks = [section["block"] for section in self.sections]
        if block in blocks:
            self.section_list.setCurrentRow(blocks.index(block))

    # -- values -------------------------------------------------------------------
    def values(self) -> dict:
        """The case as the rows state it. Blocks and settings without a row are kept."""
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
        self.show_findings(unreadable + fnd.from_payload(answer))

    def _save_failed(self, exc: Exception) -> None:
        self.save_button.setEnabled(True)
        self.status.setText("The case was not saved: " + user_text(exc))

    # -- findings -----------------------------------------------------------------
    def show_findings(self, findings) -> None:
        """A triangle at each setting concerned, at its block, and in the list."""
        self.findings = list(findings)
        self.finding_list.set_findings(self.findings, checked=True)
        for finding in self.findings:                 # a row for what has none yet
            if "." in finding.subject:
                self.ensure_row(finding.subject)
        for key, row in self.rows.items():
            row.triangle.set_findings(
                [item for item in self.findings if item.subject == key])
        for index, section in enumerate(self.sections):
            block = section["block"]
            concerned = fnd.for_subject(self.findings, block)
            level = fnd.worst(concerned)
            self.section_list.item(index).setIcon(
                fnd.triangle(level) if level else _no_icon())
            # what is about the block as a whole, not about one of its rows
            self._problems[block].set_findings(
                [item for item in concerned if item.subject not in self.rows])


def _no_icon():
    from qgis.PyQt.QtGui import QIcon
    return QIcon()
