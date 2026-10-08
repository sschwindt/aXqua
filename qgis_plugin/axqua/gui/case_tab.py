"""The *Case Setup* tab: which river reach is worked on.

A case is one file (``*.axq-case``) that describes a reach: its geodata, its boundary
conditions, its mesh and what is to be simulated. The tab lists the cases of the
project, selects the active one, and shows what aXqua reports this case can do. Every
other tab works on the active case.
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QLabel,
                                 QListWidget, QPushButton, QVBoxLayout, QWidget)

from ..compat import MATCH_EXACTLY, open_in_file_manager
from ..core import project as project_io

CASE_FILTER = "aXqua case (*.axq-case *.yml *.yaml)"


class CaseTab(QWidget):
    def __init__(self, context, parent=None) -> None:
        super().__init__(parent)
        self.ctx = context
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        case_box = QGroupBox("Cases")
        case_layout = QVBoxLayout(case_box)
        buttons = QHBoxLayout()
        for text, slot, tip in (
                ("Add case...", self.add_case, "Add an existing case file to the list"),
                ("Open folder", self.open_folder, "Open the folder of the active case"),
                ("Refresh", self.ctx.refresh_case, "Read the case file again")):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        case_layout.addLayout(buttons)
        self.case_list = QListWidget()
        self.case_list.setMaximumHeight(110)
        self.case_list.currentTextChanged.connect(self._case_selected)
        case_layout.addWidget(self.case_list)
        self.capability_label = QLabel("Add a case file to begin.")
        self.capability_label.setWordWrap(True)
        case_layout.addWidget(self.capability_label)
        layout.addWidget(case_box)

        project_box = QGroupBox("Project file")
        project_layout = QVBoxLayout(project_box)
        self.project_label = QLabel("")
        self.project_label.setWordWrap(True)
        project_layout.addWidget(self.project_label)
        row = QHBoxLayout()
        for text, slot in (("Open project...", self.open_project),
                           ("Save project", self.save_project),
                           ("Save as...", self.save_project_as)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        project_layout.addLayout(row)
        layout.addWidget(project_box)
        layout.addStretch(1)

    # -- actions ------------------------------------------------------------------
    def add_case(self) -> None:
        start = str(self.ctx.project.base()) if self.ctx.project.path else ""
        chosen, _ = QFileDialog.getOpenFileName(self, "Add a case", start, CASE_FILTER)
        if chosen:
            self.ctx.add_case(Path(chosen))

    def open_folder(self) -> None:
        case = self.ctx.active_case_or_warn()
        if case is not None:
            open_in_file_manager(case.parent)

    def open_project(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Open an aXqua project", "", f"aXqua project (*{project_io.SUFFIX})")
        if chosen:
            self.ctx.open_project(Path(chosen))

    def save_project(self) -> None:
        if self.ctx.project.path is None:
            self.save_project_as()
        else:
            self.ctx.save_project()

    def save_project_as(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save the aXqua project", "", f"aXqua project (*{project_io.SUFFIX})")
        if chosen:
            self.ctx.save_project(Path(chosen))

    def _case_selected(self, text: str) -> None:
        if text and text != self.ctx.project.active_case:
            self.ctx.set_active_case(text)

    # -- rendering ----------------------------------------------------------------
    def refresh(self) -> None:
        project = self.ctx.project
        self.project_label.setText(
            str(project.path) if project.path else
            "The list of cases is not saved yet. Save a project file to find the cases "
            "again after a restart of QGIS.")
        self.case_list.blockSignals(True)
        self.case_list.clear()
        for case in project.cases:
            self.case_list.addItem(case)
        if project.active_case:
            items = self.case_list.findItems(project.active_case, MATCH_EXACTLY)
            if items:
                self.case_list.setCurrentItem(items[0])
        self.case_list.blockSignals(False)

    def show_capabilities(self, case_view) -> None:
        """What the active case can do, in one line per simulation program."""
        if case_view is None:
            self.capability_label.setText(
                "Add a case file to begin." if not self.ctx.project.cases else "")
            return
        lines = []
        for solver in case_view.solvers:
            if not solver.enabled:
                continue
            env = ("environment ok" if solver.env_ok
                   else "environment UNAVAILABLE" if solver.env_ok is False
                   else "checking the environment...")
            if solver.env_ok is False and solver.env_detail:
                env += f" ({solver.env_detail})"
            available = [c.title for c in solver.visible_capabilities if c.can_submit]
            lines.append(f"<b>{solver.name}</b> - {env}. "
                         + (", ".join(available) if available
                            else "nothing configured yet"))
        if not lines:
            lines.append("This case enables no simulation program. Add a "
                         "<tt>telemac:</tt> or an <tt>openfoam:</tt> block to its case "
                         "file.")
        self.capability_label.setText("<br>".join(lines))
