"""The box *Survey planner* on the *Calibration & validation* tab.

It stands above the calibration, because that is the order of the work: plan the survey, measure, calibrate, validate. The box only opens the window (:mod:`.survey_wizard`). The window is not modal: the access points of the survey are set on the map while it is open, so the box keeps it and brings it to the front when it is asked for again.
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..compat import is_deleted

INTRO = ("Before the field work: where does the calibration need measurements, and how long does the survey take? The planner places measurements in every roughness zone and where the other "
         "calibration parameters act, and plans a ground-based survey with a velocity probe, drone videos, drifting kayaks or a water temperature survey, "
         "with a map and a one-page factsheet for the field.")
NEEDS = "It plans in the water of the steady run, so build the model and run the steady simulation at the discharge you expect on the survey day first."


class SurveyBox(QGroupBox):
    """Opens the Survey planner for the active case."""

    def __init__(self, context, parent=None) -> None:
        super().__init__("Survey planner", parent)
        self.ctx = context
        self.wizard = None
        layout = QVBoxLayout(self)
        for text in (INTRO, NEEDS):
            label = QLabel(text)
            label.setWordWrap(True)
            layout.addWidget(label)
        row = QHBoxLayout()
        self.open_button = QPushButton("Survey planner...")
        self.open_button.clicked.connect(self.open_wizard)
        row.addWidget(self.open_button)
        row.addStretch(1)
        layout.addLayout(row)

    def _case(self) -> Path | None:
        path = self.ctx.project.active_case_path()
        return Path(path) if path else None

    def open_wizard(self):
        """Show the planner of the active case; the one that is open already where it is for that case."""
        from .survey_wizard import SurveyWizard

        case = self._case()
        if case is None:
            self.ctx.warn("Add a case on the tab Case Setup first.")
            return None
        if self.wizard is not None and not is_deleted(self.wizard):
            if self.wizard.case == case and self.wizard.isVisible():
                self.wizard.raise_()
                self.wizard.activateWindow()
                return self.wizard
            self.wizard.close()
            self.wizard.deleteLater()
        iface = getattr(self.ctx, "iface", None)
        try:
            parent = iface.mainWindow() if iface is not None else None
        except (AttributeError, RuntimeError):
            parent = None
        self.wizard = SurveyWizard(self.ctx, case, parent or self.window())
        self.wizard.show()
        return self.wizard
