"""The window *Import a TELEMAC case* of the tab Case Setup (the user's order of 2026-11-20).

A TELEMAC case that aXqua did not build is post-processed by aXqua through a case file that points at its steering file: its results go on the map in QGIS (*Postprocessing > QGIS*, *Results of the case*), into ParaView and VisIt, and into a movie. The window asks for the steering file, where the case file goes, a name and the coordinate reference system (a TELEMAC result names none), and has ``axqua case import`` write the case, which is then added to the list. It decides nothing: what the steering file names is read by the library.
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtWidgets import (QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                                 QLineEdit, QPushButton, QVBoxLayout, QWidget)

from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import help as help_pages

INTRO = (
    "A TELEMAC case that aXqua did not build: select its steering file (.cas). aXqua "
    "writes a case file that points at it, so that its results can be put on the map, "
    "exported for ParaView and VisIt, and made into a movie. aXqua does not build or run "
    "an imported case, and writes nothing into its folder but the case file, if you keep "
    "it there.")

CRS_TEXT = "A TELEMAC result does not say in which coordinate reference system it is."


def project_epsg(default: int = 25832) -> int:
    """The EPSG code of the QGIS project, or *default* where it has none."""
    try:
        from qgis.core import QgsProject

        authid = QgsProject.instance().crs().authid()
    except Exception:                               # noqa: BLE001 - outside QGIS
        return default
    if authid.upper().startswith("EPSG:"):
        try:
            return int(authid.split(":", 1)[1])
        except ValueError:
            return default
    return default


def default_case_file(steering: str) -> str:
    """The case file beside the steering file, named after it."""
    path = Path(steering)
    return str(path.with_suffix(".axq-case")) if steering else ""


class ImportDialog(QDialog):
    """Import the TELEMAC case of a steering file; *on_done(path)* gets the new case file."""

    def __init__(self, client, *, folder: str = "", parent=None, on_done=None) -> None:
        super().__init__(parent)
        self.client = client
        self.on_done = on_done
        self.folder = folder
        self.answer: dict | None = None
        self.setWindowTitle("Import a TELEMAC case")
        self.resize(720, 360)
        layout = QVBoxLayout(self)
        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.steering = QLineEdit()
        self.steering.textChanged.connect(self._steering_changed)
        form.addRow("Steering file (.cas)", self._with_button(self.steering, self.browse_steering))
        self.case_file = QLineEdit()
        form.addRow("Case file to write", self._with_button(self.case_file, self.browse_case))
        self.name = QLineEdit()
        form.addRow("Name of the case", self.name)
        self.crs = QLineEdit(str(project_epsg()))
        self.crs.setToolTip(CRS_TEXT + " The default is the one of the QGIS project.")
        form.addRow("Coordinate reference system (EPSG code)", self.crs)
        layout.addLayout(form)
        note = QLabel(CRS_TEXT + " The default is the one of the QGIS project.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        help_button = QPushButton("Help")
        help_button.clicked.connect(lambda: help_pages.open_window_help("import-case"))
        buttons.addWidget(help_button)
        buttons.addStretch(1)
        self.import_button = QPushButton("Import")
        self.import_button.clicked.connect(self.run_import)
        buttons.addWidget(self.import_button)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _with_button(self, field: QLineEdit, slot) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(field, 1)
        button = QPushButton("...")
        button.clicked.connect(slot)
        row.addWidget(button)
        return holder

    def _steering_changed(self, text: str) -> None:
        """Propose the case file and the name of a steering file just chosen, unless they were typed."""
        if not self.case_file.isModified():
            self.case_file.setText(default_case_file(text.strip()))
        if not self.name.isModified():
            self.name.setText(Path(text.strip()).stem if text.strip() else "")

    def browse_steering(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Steering file of the TELEMAC case", self.folder,
            "TELEMAC steering file (*.cas);;All files (*)")
        if chosen:
            self.steering.setText(chosen)

    def browse_case(self) -> None:
        start = self.case_file.text().strip() or self.folder
        chosen, _ = QFileDialog.getSaveFileName(self, "Case file to write", start,
                                                "aXqua case (*.axq-case)")
        if chosen:
            self.case_file.setText(chosen if chosen.endswith(".axq-case")
                                   else chosen + ".axq-case")
            self.case_file.setModified(True)

    def choices(self) -> tuple[str, str, str, int]:
        """``(steering, case file, name, EPSG)``; :class:`ValueError` with a sentence for what is missing."""
        steering = self.steering.text().strip()
        if not steering:
            raise ValueError("Select the steering file (.cas) of the TELEMAC case.")
        case = self.case_file.text().strip() or default_case_file(steering)
        try:
            epsg = int(self.crs.text().strip())
        except ValueError:
            raise ValueError("Enter the EPSG code of the coordinate reference system, "
                             "for example 25832.") from None
        return steering, case, self.name.text().strip(), epsg

    def run_import(self) -> None:
        try:
            steering, case, name, epsg = self.choices()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        client = self.client
        self.import_button.setEnabled(False)
        self.status.setText("Reading the steering file...")
        run_async("aXqua: importing a TELEMAC case",
                  lambda: client.case_import(case, steering, name=name, crs=epsg),
                  on_success=self._imported, on_error=self._failed, owner=self)

    def _imported(self, answer: dict) -> None:
        self.answer = answer or {}
        self.import_button.setEnabled(True)
        missing = self.answer.get("missing") or []
        results = ", ".join(r.get("name", "") for r in self.answer.get("results") or [])
        self.status.setText(
            f"Imported as {self.answer.get('case', '')} ({self.answer.get('solver', '')}; "
            f"results: {results})."
            + (" Not there yet: " + ", ".join(Path(m).name for m in missing)
               + ". Run the steering file with TELEMAC, then load the results." if missing
               else " Load its results on the tab Postprocessing > QGIS."))
        if self.on_done is not None and self.answer.get("case"):
            self.on_done(Path(self.answer["case"]))

    def _failed(self, exc) -> None:
        self.import_button.setEnabled(True)
        self.status.setText(user_text(exc))
