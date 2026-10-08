"""The profile editor: the settings of this computer, in a window of its own.

The profile (``*.axq-profile``) names the Python that runs aXqua, where TELEMAC and
OpenFOAM are installed, the postprocessing programs and where jobs are kept. The editor
fills it in by clicking: every path has a button that opens a file dialog, and typing
stays possible.

Three buttons, at the bottom right:

* **Save** writes the profile and checks it. The window stays open and shows what the
  check found, as a triangle next to the item concerned.
* **Cancel** discards what was changed since the last save and closes the window.
* **Exit** closes the window, and asks first when there are changes that were not saved.

A check never prevents saving. A profile is normally incomplete while it is filled in,
and a half-finished profile that cannot be saved is lost work.
"""

from __future__ import annotations

import copy

from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                                 QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                                 QMessageBox, QPushButton, QScrollArea, QSpinBox,
                                 QVBoxLayout, QWidget)

from ..compat import enum_value, exec_dialog
from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import findings as fnd

#: ``(dotted key, label, kind, group)``. *kind* is file | folder | text | int | choice |
#: number. The dotted key is also the subject a finding names, which is how a triangle
#: finds its row.
FIELDS = (
    ("name", "Name of this computer", "text", "Profile"),
    ("python.executable", "Python interpreter", "file", "Python"),
    ("python.axqua", "aXqua program", "file", "Python"),
    ("solvers.telemac.setup_script", "TELEMAC environment script", "file", "TELEMAC"),
    ("solvers.telemac.mpi_processes", "Processor cores", "int", "TELEMAC"),
    ("solvers.openfoam.setup_script", "OpenFOAM environment script", "file", "OpenFOAM"),
    ("solvers.openfoam.mpi_processes", "Processor cores", "int", "OpenFOAM"),
    ("postprocessors.paraview", "ParaView program", "file", "Postprocessors"),
    ("postprocessors.visit", "VisIt program", "file", "Postprocessors"),
    ("jobs.root", "Folder for jobs", "folder", "Jobs"),
    ("jobs.launcher", "How jobs are detached", "choice", "Jobs"),
    ("display.min_depth", "Smallest water depth shown [m]", "number", "Map display"),
    ("display.velocity_cap", "Upper limit of the velocity scale [m/s]", "number",
     "Map display"),
)

LAUNCHERS = ("auto", "systemd", "posix", "windows", "wsl")

HINTS = {
    "solvers.telemac.setup_script": "for example .../telemac-mascaret/configs/pysource.sh",
    "solvers.openfoam.setup_script": "for example .../openfoam2406/etc/bashrc",
    "jobs.root": "leave empty for the default folder of aXqua",
    "python.executable": "leave empty to use the Python that runs aXqua",
}


def get(data: dict, dotted: str, default=None):
    """Read ``solvers.telemac.setup_script`` out of a nested dictionary."""
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def put(data: dict, dotted: str, value) -> None:
    """Write a nested value; an empty value removes the entry and empty parents."""
    parts = dotted.split(".")
    trail = [data]
    for part in parts[:-1]:
        trail.append(trail[-1].setdefault(part, {}))
    if value in ("", None):
        trail[-1].pop(parts[-1], None)
        for depth in range(len(parts) - 1, 0, -1):     # drop parents left empty
            if not trail[depth]:
                trail[depth - 1].pop(parts[depth - 1], None)
    else:
        trail[-1][parts[-1]] = value


class ProfileEditor(QDialog):
    """Edit the profile of this computer."""

    def __init__(self, client, profile: dict, *, title: str = "", parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("aXqua profile of this computer")
        self.resize(720, 640)
        profile = {k: v for k, v in (profile or {}).items() if k != "path"}
        self._saved = copy.deepcopy(profile)       # what is on disk, as far as we know
        self._base = copy.deepcopy(profile)        # carries the entries without a row
        self._widgets: dict[str, QWidget] = {}
        self._triangles: dict[str, fnd.TriangleButton] = {}
        self.saved_once = False
        self._build(title)
        self._fill(profile)

    # -- construction -------------------------------------------------------------
    def _build(self, title: str) -> None:
        outer = QVBoxLayout(self)
        self.header = QLabel(title)
        self.header.setWordWrap(True)
        self.header.setVisible(bool(title))
        outer.addWidget(self.header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        forms: dict[str, QFormLayout] = {}
        for key, label, kind, group in FIELDS:
            if group not in forms:
                box = QGroupBox(group)
                forms[group] = QFormLayout(box)
                layout.addWidget(box)
            forms[group].addRow(label, self._row(key, kind))
        layout.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.finding_list = fnd.FindingList("The profile is checked when it is saved.")
        outer.addWidget(self.finding_list)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.save_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Discard the changes since the last save and close")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.exit_button = QPushButton("Exit")
        self.exit_button.setToolTip("Close; asks first when changes are not saved")
        self.exit_button.clicked.connect(self.exit)
        buttons.addWidget(self.exit_button)
        outer.addLayout(buttons)

    def _row(self, key: str, kind: str) -> QWidget:
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        if kind == "int":
            widget = QSpinBox()
            widget.setRange(0, 4096)
            widget.setSpecialValueText("not set")
        elif kind == "number":
            widget = QDoubleSpinBox()
            widget.setDecimals(3)
            widget.setRange(0.0, 1000.0)
            widget.setSingleStep(0.01)
        elif kind == "choice":
            widget = QComboBox()
            widget.addItems(LAUNCHERS)
        else:
            widget = QLineEdit()
            widget.setPlaceholderText(HINTS.get(key, ""))
        line.addWidget(widget, 1)
        if kind in ("file", "folder"):
            browse = QPushButton("...")
            browse.setMaximumWidth(36)
            browse.setToolTip("Select the " + ("folder" if kind == "folder" else "file"))
            browse.clicked.connect(lambda _=False, k=key, f=(kind == "folder"):
                                   self._browse(k, f))
            line.addWidget(browse)
        triangle = fnd.TriangleButton()
        line.addWidget(triangle)
        self._widgets[key] = widget
        self._triangles[key] = triangle
        return row

    def _browse(self, key: str, folder: bool) -> None:
        edit = self._widgets[key]
        start = edit.text() or ""
        if folder:
            chosen = QFileDialog.getExistingDirectory(self, "Select a folder", start)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, "Select a file", start)
        if chosen:
            edit.setText(chosen)

    # -- values -------------------------------------------------------------------
    def _fill(self, profile: dict) -> None:
        for key, _label, kind, _group in FIELDS:
            widget = self._widgets[key]
            value = get(profile, key)
            if kind == "int":
                widget.setValue(int(value or 0))
            elif kind == "number":
                widget.setValue(float(value if value is not None else
                                      (0.01 if key.endswith("min_depth") else 5.0)))
            elif kind == "choice":
                index = widget.findText(str(value or "auto"))
                widget.setCurrentIndex(max(index, 0))
            else:
                widget.setText("" if value is None else str(value))

    def values(self) -> dict:
        """The profile as the rows state it, with every entry that has no row kept."""
        data = copy.deepcopy(self._base)
        data.setdefault("schema_version", 1)
        for key, _label, kind, _group in FIELDS:
            widget = self._widgets[key]
            if kind == "int":
                put(data, key, widget.value() or None)
            elif kind == "number":
                put(data, key, round(widget.value(), 6))
            elif kind == "choice":
                put(data, key, widget.currentText())
            else:
                put(data, key, widget.text().strip())
        return data

    @property
    def dirty(self) -> bool:
        return self.values() != _normal(self._saved)

    # -- the three buttons --------------------------------------------------------
    def save(self) -> None:
        """Write the profile, then check it. Never refused because of a finding."""
        data = self.values()
        self.save_button.setEnabled(False)
        self.status.setText("Saving and checking the profile...")
        client = self.client

        def work():
            written = client.profile_write(data)
            checked = client.profile_check(probe=True)
            return written, checked

        run_async("aXqua: saving the profile", work,
                  on_success=lambda answer: self._saved_ok(data, *answer),
                  on_error=self._save_failed, owner=self)

    def _saved_ok(self, data: dict, written: dict, checked: dict) -> None:
        self._saved = copy.deepcopy(data)
        self.saved_once = True
        self.save_button.setEnabled(True)
        self.status.setText(f"Saved {written.get('path', '')}.")
        self.show_findings(fnd.from_payload(checked))

    def _save_failed(self, exc: Exception) -> None:
        self.save_button.setEnabled(True)
        self.status.setText("The profile was not saved: " + user_text(exc))

    def show_findings(self, findings) -> None:
        findings = list(findings)
        self.finding_list.set_findings(findings, checked=True)
        for key, triangle in self._triangles.items():
            triangle.set_findings(fnd.for_subject(findings, key))

    def exit(self) -> None:
        """Close. Changes that were not saved are offered for saving first."""
        if not self.dirty:
            self.accept()
            return
        box = QMessageBox(self)
        box.setWindowTitle("aXqua profile")
        box.setText("The profile has changes that are not saved.")
        save = box.addButton("Save", enum_value(QMessageBox, "ButtonRole.AcceptRole",
                                                "AcceptRole"))
        discard = box.addButton("Close without saving",
                                enum_value(QMessageBox, "ButtonRole.DestructiveRole",
                                           "DestructiveRole"))
        box.addButton("Keep editing", enum_value(QMessageBox, "ButtonRole.RejectRole",
                                                 "RejectRole"))
        exec_dialog(box)
        if box.clickedButton() is save:
            self.save()                    # stays open, so that the check can be read
        elif box.clickedButton() is discard:
            self.accept()


def _normal(profile: dict) -> dict:
    """A stored profile in the shape :meth:`ProfileEditor.values` produces."""
    data = copy.deepcopy(profile)
    data.setdefault("schema_version", 1)
    put(data, "jobs.launcher", get(data, "jobs.launcher") or "auto")
    put(data, "display.min_depth", round(float(get(data, "display.min_depth", 0.01)), 6))
    put(data, "display.velocity_cap",
        round(float(get(data, "display.velocity_cap", 5.0)), 6))
    for key, _label, kind, _group in FIELDS:
        if kind in ("text", "file", "folder"):
            value = get(data, key)
            put(data, key, "" if value is None else str(value).strip())
    return data
