"""What the fixed tabs show: the pages of the workflow sections.

A page is filled from the capability matrix of the active case
(``axqua case-status``): each capability that :mod:`.sections` places on the page gets a
box with its state, its options and its buttons. The page itself knows nothing about
TELEMAC or OpenFOAM.
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QLabel,
                                 QListWidget, QListWidgetItem, QPushButton,
                                 QScrollArea, QTableWidget, QTableWidgetItem,
                                 QVBoxLayout, QWidget)

from ..compat import CHECKED, UNCHECKED, enum_value
from ..core import batch
from . import sections
from .capability_tab_widget import CapabilityTab
from .profile_editor import get

NO_CASE = "Add a case on the tab Case Setup first."


def _label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def _scrolling(body: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(body)
    return area


class SectionPage(QWidget):
    """The boxes of the capabilities that belong on one tab or sub-tab."""

    def __init__(self, context, section_key: str, sub_key: str = "", *,
                 empty_text: str = "", hide_build: tuple = (), parent=None) -> None:
        super().__init__(parent)
        self.ctx = context
        self.section_key, self.sub_key = section_key, sub_key
        self.empty_text = empty_text
        self.hide_build = set(hide_build)
        self._boxes: dict[tuple[str, str], tuple[QGroupBox, CapabilityTab]] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        self._layout = QVBoxLayout(body)
        self.note = _label(NO_CASE)
        self._layout.addWidget(self.note)
        self._layout.addStretch(1)
        outer.addWidget(_scrolling(body))

    def capabilities(self, case_view) -> list:
        """The capabilities of *case_view* that are shown here, in display order."""
        if case_view is None:
            return []
        found = []
        for solver in case_view.enabled_solvers:
            for capability in solver.visible_capabilities:
                if sections.placement(solver.name, capability.name) == (
                        self.section_key, self.sub_key):
                    found.append(capability)
        placed = sections.section(self.section_key)
        sub = placed.subsection(self.sub_key) if self.sub_key else None
        order = list(sub.capabilities if sub else placed.capabilities)
        return sorted(found, key=lambda c: (order.index((c.solver, c.name))
                                            if (c.solver, c.name) in order
                                            else len(order)))

    def apply(self, case_view) -> None:
        wanted = self.capabilities(case_view)
        keep = {(c.solver, c.name) for c in wanted}
        for key in [k for k in self._boxes if k not in keep]:
            box, _tab = self._boxes.pop(key)
            self._layout.removeWidget(box)
            box.deleteLater()
        several = case_view is not None and len(case_view.enabled_solvers) > 1
        for position, capability in enumerate(wanted):
            key = (capability.solver, capability.name)
            title = (f"{capability.title} ({capability.solver})"
                     if several and not self.sub_key else capability.title)
            if key in self._boxes:
                box, tab = self._boxes[key]
                tab.apply(capability)
            else:
                box = QGroupBox(title)
                tab = CapabilityTab(capability, self.ctx, titled=False)
                inner = QVBoxLayout(box)
                inner.setContentsMargins(4, 4, 4, 4)
                inner.addWidget(tab)
                self._boxes[key] = (box, tab)
                self._layout.insertWidget(position + 1, box)
            box.setTitle(title)
            if key in self.hide_build:
                tab.build_button.setVisible(False)
            # shown but not usable where aXqua does not implement it: a gap is worth
            # seeing, and the box says why
            tab.setEnabled(capability.enabled or bool(capability.reason))
        if case_view is None:
            self.note.setText(NO_CASE)
        else:
            self.note.setText("" if wanted else self.empty_text)
        self.note.setVisible(bool(self.note.text()))

    def tab(self, solver: str, capability: str) -> CapabilityTab | None:
        box = self._boxes.get((solver, capability))
        return box[1] if box else None


class PreprocessingPage(QWidget):
    """Build the model, and see what has been built."""

    COLUMNS = ("Capability", "Program", "Asked for", "Built", "Run")

    def __init__(self, context, parent=None) -> None:
        super().__init__(parent)
        self.ctx = context
        layout = QVBoxLayout(self)

        build_box = QGroupBox("Build the TELEMAC model")
        build_layout = QVBoxLayout(build_box)
        build_layout.addWidget(_label(
            "The build clips the terrain model, generates the mesh, assigns roughness "
            "and boundary conditions, and writes the TELEMAC input files. It runs as a "
            "job and takes from one minute to a quarter of an hour, depending on the "
            "mesh."))
        self.state = _label("")
        build_layout.addWidget(self.state)
        row = QHBoxLayout()
        self.build_button = QPushButton("Build")
        self.build_button.clicked.connect(lambda: self.ctx.submit("preprocessing"))
        row.addWidget(self.build_button)
        row.addStretch(1)
        build_layout.addLayout(row)
        layout.addWidget(build_box)

        self.openfoam_note = _label(
            "The OpenFOAM case is built on the tab Hydraulic simulation > OpenFOAM, "
            "because its build starts from the TELEMAC warm-up run.")
        layout.addWidget(self.openfoam_note)

        check_box = QGroupBox("Preprocessing checkup")
        check_layout = QVBoxLayout(check_box)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(enum_value(QTableWidget, "EditTrigger.NoEditTriggers",
                                              "NoEditTriggers"))
        check_layout.addWidget(self.table)
        layout.addWidget(check_box, 1)
        self.apply(None)

    def apply(self, case_view) -> None:
        rows = []
        steady = None
        for solver in (case_view.enabled_solvers if case_view is not None else []):
            for capability in solver.visible_capabilities:
                rows.append((capability.title, solver.name, capability.configured,
                             capability.built, capability.run))
                if (solver.name, capability.name) == sections.PREPROCESSING_CAPABILITY:
                    steady = capability
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, value in enumerate(row):
                text = value if isinstance(value, str) else _mark(value)
                self.table.setItem(index, column, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        self.build_button.setEnabled(steady is not None and bool(steady.configured))
        if case_view is None:
            self.state.setText(NO_CASE)
        elif steady is None:
            self.state.setText("This case does not use TELEMAC.")
        else:
            self.state.setText("The model is built." if steady.built
                               else "The model has not been built yet.")
        self.openfoam_note.setVisible(
            case_view is not None and any(s.name == "openfoam"
                                          for s in case_view.enabled_solvers))


def _mark(value) -> str:
    return "yes" if value else ("-" if value is None else "no")


class QgisPage(QWidget):
    """Results on the map: layers, the print layout and the movie."""

    def __init__(self, context, parent=None) -> None:
        super().__init__(parent)
        self.ctx = context
        layout = QVBoxLayout(self)
        box = QGroupBox("Results on the map")
        inner = QVBoxLayout(box)
        inner.addWidget(_label(
            "Select a completed job in the job list below and click Load results. The "
            "result is added as layers for water depth and flow velocity, in the "
            "coordinate reference system of the case."))
        row = QHBoxLayout()
        load = QPushButton("Load results of the selected job")
        load.clicked.connect(lambda: self.ctx.load_selected_results())
        row.addWidget(load)
        row.addStretch(1)
        inner.addLayout(row)
        layout.addWidget(box)

        print_box = QGroupBox("Map for a report and movie")
        print_layout = QVBoxLayout(print_box)
        row = QHBoxLayout()
        for text, name in (("Add the A3 print layout", "layout"),
                           ("Export movie...", "movie")):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, n=name: self.ctx.run_action(n))
            row.addWidget(button)
        row.addStretch(1)
        print_layout.addLayout(row)
        display = QPushButton("Display settings...")
        display.setToolTip("Smallest water depth shown and upper limit of the velocity "
                           "scale")
        display.clicked.connect(self.ctx.open_settings)
        row2 = QHBoxLayout()
        row2.addWidget(display)
        row2.addStretch(1)
        print_layout.addLayout(row2)
        layout.addWidget(print_box)
        layout.addStretch(1)


class ProgramPage(QWidget):
    """ParaView or VisIt: the program of the profile, and what can be done with it."""

    def __init__(self, context, key: str, title: str, parent=None) -> None:
        super().__init__(parent)
        self.ctx, self.key, self.title = context, key, title
        layout = QVBoxLayout(self)
        self.program = _label("")
        layout.addWidget(self.program)
        layout.addWidget(_label(
            f"The export of results to {title} is not yet available in this version. "
            f"OpenFOAM results can be opened in {title} directly from the case folder."))
        layout.addStretch(1)
        self.show_profile(getattr(context, "profile", None))

    def show_profile(self, profile) -> None:
        path = get(profile or {}, f"postprocessors.{self.key}")
        self.program.setText(
            f"{self.title} program: {path}" if path else
            f"No {self.title} program is entered in the profile of this computer "
            "(tab Configuration).")


class BatchPage(QWidget):
    """Several steps of one case in sequence, and the script that does the same."""

    def __init__(self, context, parent=None) -> None:
        super().__init__(parent)
        self.ctx = context
        layout = QVBoxLayout(self)

        box = QGroupBox("Steps for the active case")
        inner = QVBoxLayout(box)
        inner.addWidget(_label(
            "Tick the steps to run. They are submitted in the order of the list, and "
            "each waits until the one before it has ended."))
        self.steps = QListWidget()
        self.steps.setMaximumHeight(150)
        for item in batch.STEPS:
            entry = QListWidgetItem(item.title)
            entry.setData(enum_value(_qt(), "ItemDataRole.UserRole", "UserRole"),
                          item.kind)
            entry.setCheckState(CHECKED if item.default else UNCHECKED)
            self.steps.addItem(entry)
        inner.addWidget(self.steps)
        row = QHBoxLayout()
        self.submit_button = QPushButton("Submit the ticked steps")
        self.submit_button.clicked.connect(self.submit)
        row.addWidget(self.submit_button)
        self.script_button = QPushButton("Generate batch-processing script...")
        self.script_button.clicked.connect(self.write_script)
        row.addWidget(self.script_button)
        row.addStretch(1)
        inner.addLayout(row)
        layout.addWidget(box)

        detach_box = QGroupBox("Detach")
        detach_layout = QVBoxLayout(detach_box)
        self.detach = _label("")
        detach_layout.addWidget(self.detach)
        layout.addWidget(detach_box)
        layout.addStretch(1)
        self.show_profile(getattr(context, "profile", None))

    def ticked(self) -> list[str]:
        role = enum_value(_qt(), "ItemDataRole.UserRole", "UserRole")
        kinds = [self.steps.item(i).data(role) for i in range(self.steps.count())
                 if self.steps.item(i).checkState() == CHECKED]
        return batch.ordered(kinds)

    def apply(self, case_view) -> None:
        """Grey out the steps the active case does not ask for."""
        asked = set()
        for solver in (case_view.enabled_solvers if case_view is not None else []):
            asked |= {c.name for c in solver.visible_capabilities if c.configured}
        role = enum_value(_qt(), "ItemDataRole.UserRole", "UserRole")
        enabled = enum_value(_qt(), "ItemFlag.ItemIsEnabled", "ItemIsEnabled")
        for index in range(self.steps.count()):
            entry = self.steps.item(index)
            needed = batch.step(entry.data(role)).needs
            # 3D and calibration become possible once the steps before them have run,
            # so only what the case file itself rules out is greyed out
            usable = case_view is not None and (needed in asked or needed == "steady3d")
            entry.setFlags(entry.flags() | enabled if usable
                           else entry.flags() & ~enabled)

    def submit(self) -> None:
        kinds = self.ticked()
        if not kinds:
            self.ctx.warn("Tick at least one step.")
            return
        self.ctx.submit_sequence(kinds)

    def write_script(self) -> None:
        case = self.ctx.active_case_or_warn()
        kinds = self.ticked()
        if case is None:
            return
        if not kinds:
            self.ctx.warn("Tick at least one step.")
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save the batch-processing script",
            str(case.parent / f"{case.stem}-batch.sh"), "Shell script (*.sh)")
        if not chosen:
            return
        try:
            program = self.ctx.client.executable
        except Exception:                           # noqa: BLE001 - not found yet
            program = "axqua"
        Path(chosen).write_text(batch.script(str(case), kinds, axqua=program),
                                encoding="utf-8")
        self.ctx.info(f"Wrote {chosen}. Start it with: nohup bash {Path(chosen).name} &")

    def show_profile(self, profile) -> None:
        root = get(profile or {}, "jobs.root") or "the default folder of aXqua"
        how = get(profile or {}, "jobs.launcher") or "auto"
        self.detach.setText(
            "Every job is detached from QGIS when it is submitted: it keeps running "
            "when QGIS is closed, and the job list shows it with its current state "
            "when QGIS is opened again.\n\n"
            f"Jobs are kept in {root} and detached with the method '{how}'. Both are "
            "set in the profile of this computer (tab Configuration).")


def _qt():
    from qgis.PyQt.QtCore import Qt
    return Qt
