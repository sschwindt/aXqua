"""The panel: the tabs of the workflow, and the job list below them.

The tabs are fixed and follow the documentation section by section
(:mod:`.sections`): Configuration, Case Setup, Preprocessing, Hydraulic simulation,
Mesh convergence, Morphodynamic simulation, Calibration & validation, Postprocessing and
Batch-processing. *Help* opens the documentation at the section of the tab that is
showing.

What a tab offers still comes from ``axqua case-status --json``: the plugin does not
know what TELEMAC or OpenFOAM can do, it *asks*, so a capability added to aXqua appears
here without a plugin release. The capability matrix decides which boxes a tab shows and
which of their buttons are enabled.

The job list is below the tabs and stays visible from every one of them, because a job
is submitted on one tab and its result is used on another.

The panel also carries the :class:`PluginContext` - the small object every widget uses
to reach the runner client, the project and the message bar. Passing one context beats
threading four constructor arguments through every widget, and it keeps the widgets free
of solver knowledge.
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (QDockWidget, QGroupBox, QPushButton, QSplitter,
                                 QTabWidget, QVBoxLayout, QWidget)

from ..compat import (CRITICAL, INFO, WARNING, enum_value, exec_dialog, log_message,
                      push_message)
from ..core import project as project_io
from ..core.runner_client import RunnerClient, user_text
from . import findings as fnd
from . import help as help_pages
from . import sections
from .capability_tabs import CaseView
from .case_tab import CaseTab
from .configuration_tab import ConfigurationTab
from .jobs_widget import JobsWidget
from .section_pages import (BatchPage, PreprocessingPage, ProgramPage, QgisPage,
                            SectionPage)
from .settings_dialog import SettingsDialog, read_settings

#: What a sub-tab says when the active case has nothing for it.
EMPTY = {
    ("hydraulics", "telemac"): "This case does not use TELEMAC. Add a telemac: block to "
                               "its case file.",
    ("hydraulics", "openfoam"): "This case does not use OpenFOAM. Add an openfoam: "
                                "block to its case file to simulate the free surface "
                                "in three dimensions.",
    ("mesh", ""): "This case has no simulation for which a mesh study can be run.",
    ("morphodynamics", "telemac"): "This case does not use TELEMAC.",
    ("morphodynamics", "openfoam"): "Morphodynamic simulations with OpenFOAM are not "
                                    "yet available in this version.",
    ("calibration", ""): "This case has no simulation that can be calibrated.",
}


class PluginContext:
    """What every widget needs, in one place."""

    def __init__(self, iface, dock) -> None:
        self.iface = iface
        self.dock = dock
        settings = read_settings()
        self.client = RunnerClient(settings["executable"] or None)
        self.min_depth = settings["min_depth"]
        self.velocity_cap = settings["velocity_cap"]
        self.project = project_io.AxquaProject()
        self.case_view: CaseView | None = None
        self.profile: dict | None = None
        self.actions: dict = {}             # what the plugin menu can do, by name
        self.runner_ok = False
        self.runner_checked = False
        self.runner_error = ""

    # -- messaging ----------------------------------------------------------------
    def _message(self, text: str, level) -> None:
        # Both places, deliberately: the message bar is the one line the user needs now
        # and disappears after eight seconds, while the log tab is the durable record
        # the bug-reporting instructions ask people to copy.
        push_message(self.iface.messageBar(), "aXqua", text, level)
        log_message(text, level)

    def info(self, text: str) -> None:
        self._message(text, INFO)

    def warn(self, text: str) -> None:
        self._message(text, WARNING)

    def error(self, text: str) -> None:
        self._message(text, CRITICAL)

    # -- the runner ---------------------------------------------------------------
    def set_runner_ok(self, ok: bool, detail: str = "") -> None:
        """Record what the Configuration tab's background probe found."""
        self.runner_ok = ok
        self.runner_checked = True
        self.runner_error = detail

    def client_or_warn(self) -> RunnerClient | None:
        """The client, or a message saying exactly what to fix.

        Every action goes through this, so "axqua is not installed" is reported once,
        clearly, rather than as a different traceback per button.

        It answers from what the Configuration tab's probe already found and **never
        probes here**: this runs inside a button handler, a probe is a subprocess, and
        one against an unreachable path costs 30 s per candidate before the click does
        anything at all. While the first probe is still in flight the action proceeds -
        the background call resolves the executable itself and reports any failure
        through its own error path, which is where that message belongs anyway.
        """
        if self.runner_checked and not self.runner_ok:
            self.error(self.runner_error or "axqua could not be found. Set its path in "
                                            "aXqua > Settings.")
            return None
        return self.client

    def active_case_or_warn(self) -> Path | None:
        case = self.project.active_case_path()
        if case is None:
            self.warn("Add a case on the tab Case Setup first.")
            return None
        if not case.exists():
            self.warn(f"{case} is listed in the project but is not on disk.")
            return None
        return case

    # -- delegated to the dock ----------------------------------------------------
    def open_settings(self) -> None:
        self.dock.open_settings()

    def open_project(self, path: Path) -> None:
        self.dock.open_project(path)

    def save_project(self, path: Path | None = None) -> None:
        self.dock.save_project(path)

    def add_case(self, path: Path) -> None:
        self.dock.add_case(path)

    def set_active_case(self, stored: str) -> None:
        self.dock.set_active_case(stored)

    def refresh_case(self) -> None:
        self.dock.refresh_case()

    def job_submitted(self, job_id: str) -> None:
        self.dock.job_submitted(job_id)

    def load_latest_results(self, kind: str) -> None:
        self.dock.load_latest_results(kind)

    def load_selected_results(self) -> None:
        self.dock.jobs_tab._load_results()

    def submit(self, kind: str, options: dict | None = None) -> None:
        self.dock.submit([kind], options)

    def submit_sequence(self, kinds) -> None:
        self.dock.submit(list(kinds))

    def profile_changed(self, profile) -> None:
        self.dock.profile_changed(profile)

    def run_action(self, name: str) -> None:
        action = self.actions.get(name)
        if action is None:
            self.warn("This function is not available.")
            return
        action()


class AxquaDock(QDockWidget):
    """The plugin's main window."""

    def __init__(self, iface, parent=None) -> None:
        super().__init__("aXqua", parent)
        self.iface = iface
        self.setObjectName("AxquaDock")
        self.ctx = PluginContext(iface, self)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(2, 2, 2, 2)
        splitter = QSplitter(enum_value(_qt(), "Orientation.Vertical", "Vertical"))
        layout.addWidget(splitter)
        self.setWidget(container)

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.help_button = QPushButton("Help")
        self.help_button.setToolTip("Open the documentation at the section of this tab")
        self.help_button.clicked.connect(self.open_help)
        self.tabs.setCornerWidget(self.help_button)
        splitter.addWidget(self.tabs)

        #: every page that follows the active case, by (section key, sub key)
        self.pages: dict[tuple[str, str], QWidget] = {}
        #: the tab widget inside a section that has sub-tabs
        self.subtabs: dict[str, QTabWidget] = {}
        self._build_tabs()

        jobs_box = QGroupBox("Jobs")
        jobs_layout = QVBoxLayout(jobs_box)
        jobs_layout.setContentsMargins(2, 2, 2, 2)
        self.jobs_tab = JobsWidget(self.ctx)
        jobs_layout.addWidget(self.jobs_tab)
        splitter.addWidget(jobs_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([600, 320])
        self.jobs_tab.setMinimumHeight(170)

        self.configuration_tab.check_runner()

    # -- construction -------------------------------------------------------------
    def _build_tabs(self) -> None:
        self.configuration_tab = ConfigurationTab(self.ctx)
        self.configuration_tab.on_findings = lambda level: self.mark("configuration",
                                                                     level)
        self.case_tab = CaseTab(self.ctx)
        self.case_tab.on_findings = lambda level: self.mark("case", level)
        special = {
            "configuration": self.configuration_tab,
            "case": self.case_tab,
            "preprocessing": PreprocessingPage(self.ctx),
            "batch": BatchPage(self.ctx),
        }
        for item in sections.SECTIONS:
            if item.key in special:
                widget = special[item.key]
                if hasattr(widget, "apply"):
                    self.pages[(item.key, "")] = widget
            elif item.subsections:
                widget = QTabWidget()
                self.subtabs[item.key] = widget
                for sub in item.subsections:
                    page = self._page(item.key, sub.key, sub.title)
                    self.pages[(item.key, sub.key)] = page
                    widget.addTab(page, sub.title)
            else:
                widget = self._page(item.key, "", item.title)
                self.pages[(item.key, "")] = widget
            self.tabs.addTab(widget, item.title.replace("&", "&&"))

    def _page(self, key: str, sub: str, title: str) -> QWidget:
        if key == "postprocessing":
            return (QgisPage(self.ctx) if sub == "qgis"
                    else ProgramPage(self.ctx, sub, title))
        # the build of the 2D model has a tab of its own
        hide = (sections.PREPROCESSING_CAPABILITY,) if key == "hydraulics" else ()
        return SectionPage(self.ctx, key, sub, empty_text=EMPTY.get((key, sub), ""),
                           hide_build=hide)

    def page(self, key: str, sub: str = "") -> QWidget | None:
        return self.pages.get((key, sub))

    def index_of(self, key: str) -> int:
        return [item.key for item in sections.SECTIONS].index(key)

    def show_section(self, key: str, sub: str = "") -> None:
        """Bring a tab, and one of its sub-tabs, to the front."""
        self.tabs.setCurrentIndex(self.index_of(key))
        inner = self.subtabs.get(key)
        if inner is not None and sub:
            keys = [s.key for s in sections.section(key).subsections]
            inner.setCurrentIndex(keys.index(sub))

    def current_section(self) -> tuple[str, str]:
        """``(section key, sub key)`` of what is showing."""
        item = sections.SECTIONS[max(self.tabs.currentIndex(), 0)]
        inner = self.subtabs.get(item.key)
        sub = item.subsections[inner.currentIndex()].key if inner is not None else ""
        return item.key, sub

    def mark(self, key: str, level: str) -> None:
        """Put a warning triangle on a tab, or take it off (level '')."""
        self.tabs.setTabIcon(self.index_of(key),
                             fnd.triangle(level) if level else QIcon())

    # -- help ---------------------------------------------------------------------
    def open_help(self) -> str:
        key, sub = self.current_section()
        return help_pages.open_help(key, sub)

    # -- project ------------------------------------------------------------------
    def open_project(self, path: Path) -> None:
        try:
            self.ctx.project = project_io.load(path)
        except (OSError, ValueError) as exc:
            self.ctx.error(f"Could not open {path.name}: {exc}")
            return
        self.case_tab.refresh()
        self.ctx.info(f"Opened {path.name} with {len(self.ctx.project.cases)} case(s).")
        # Rediscovery: the jobs are found with whatever state they have reached while
        # QGIS was closed.
        self.refresh_case()
        self.jobs_tab.refresh()

    def save_project(self, path: Path | None = None) -> None:
        try:
            written = project_io.save(self.ctx.project, path)
        except (OSError, ValueError) as exc:
            self.ctx.error(f"Could not save the project: {exc}")
            return
        self.case_tab.refresh()
        self.ctx.info(f"Saved {written.name}.")

    def add_case(self, path: Path) -> None:
        self.ctx.project.add_case(path)
        self.case_tab.refresh()
        self.refresh_case()

    def set_active_case(self, stored: str) -> None:
        self.ctx.project.active_case = stored
        self.refresh_case()

    # -- capabilities -------------------------------------------------------------
    def refresh_case(self) -> None:
        """Re-read the capability matrix and bring every tab into line with it.

        Nothing here touches the executable on the GUI thread. Resolving *where* axqua
        is can itself mean running ``--version`` against three candidate paths, so that
        happens inside the background call too, and a missing executable is reported
        through the same error path as any other failure.
        """
        case = self.ctx.project.active_case_path()
        if case is None:
            self._apply_case_view(None)
            return
        from ..core.tasks import run_async
        client = self.ctx.client
        run_async("aXqua: reading what this case can do",
                  lambda: client.case_status(case),
                  on_success=lambda payload: self._case_status_arrived(
                      case, CaseView.from_payload(payload or {})),
                  on_error=lambda exc: self.ctx.error(user_text(exc)), owner=self)

    def _case_status_arrived(self, case: Path, view: CaseView) -> None:
        self._apply_case_view(view)
        self._check_environments(case)

    def _check_environments(self, case: Path) -> None:
        """Ask again, this time with ``--check-env``.

        Two calls rather than one because they answer at different speeds: the capability
        matrix is a few file stats and must not wait, while probing a solver environment
        sources a shell profile and can take seconds. So the tabs fill immediately and
        "checking the environment..." is replaced when the answer arrives.
        """
        from ..core.tasks import run_async
        client = self.ctx.client
        run_async("aXqua: checking the solver environments",
                  lambda: client.case_status(case, check_env=True),
                  on_success=lambda payload: self._env_checked(
                      CaseView.from_payload(payload or {})),
                  # A failed environment probe is not worth a banner: the tabs are
                  # already filled and everything except the one status line works.
                  on_error=lambda exc: log_message(
                      f"environment check failed: {user_text(exc)}", WARNING),
                  owner=self)

    def _env_checked(self, view: CaseView) -> None:
        if self.ctx.case_view is not None:
            self.ctx.case_view = view
        self.case_tab.show_capabilities(view)

    def _apply_case_view(self, view: CaseView | None) -> None:
        self.ctx.case_view = view
        self.case_tab.show_capabilities(view)
        for page in self.pages.values():
            if hasattr(page, "apply"):
                page.apply(view)

    def profile_changed(self, profile) -> None:
        for page in self.pages.values():
            if hasattr(page, "show_profile"):
                page.show_profile(profile)

    # -- actions ------------------------------------------------------------------
    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        if exec_dialog(dialog):
            values = dialog.values()
            self.ctx.client = RunnerClient(values["executable"] or None)
            self.ctx.min_depth = values["min_depth"]
            self.ctx.velocity_cap = values["velocity_cap"]
            self.configuration_tab.check_runner()

    def submit(self, kinds, options: dict | None = None) -> None:
        """Submit one job, or several of the active case in the order given.

        Several jobs are handed over one after the other in **one** background call, so
        that their tickets are written in this order: jobs of one case start in the
        order they were submitted.
        """
        client = self.ctx.client_or_warn()
        config = self.ctx.active_case_or_warn()
        if client is None or config is None or not kinds:
            return
        project = self.ctx.project
        kinds = list(kinds)

        def work():
            submitted = []
            for kind in kinds:
                data = client.submit(config, kind, profile=project.profile or None,
                                     job_root=project.job_root or None,
                                     launcher=project.launcher,
                                     options=options if len(kinds) == 1 else None)
                submitted.append(str((data or {}).get("job_id") or ""))
            return submitted

        from ..core.tasks import run_async
        run_async("aXqua: submitting " + ", ".join(kinds), work,
                  on_success=self._submitted,
                  on_error=lambda exc: self.ctx.error(user_text(exc)), owner=self)

    def _submitted(self, job_ids) -> None:
        count = len(job_ids)
        self.ctx.info((f"Submitted {job_ids[0]}." if count == 1 else
                       f"Submitted {count} jobs. Each waits for the one before it.")
                      + " They keep running if you close QGIS.")
        self.job_submitted(job_ids[-1] if job_ids else "")

    def job_submitted(self, job_id: str) -> None:
        # The job list is below every tab, so there is nowhere to switch to.
        self.jobs_tab.refresh()

    def load_latest_results(self, kind: str) -> None:
        """Load the newest completed job of *kind* for the active case."""
        jobs = [j for j in self.jobs_tab.table_model
                if j.state == "COMPLETED" and (not kind or j.kind == kind)]
        if not jobs:
            self.ctx.warn("No completed job of that kind yet. Refresh the job list if "
                          "one finished while this was open.")
            return
        newest = max(jobs, key=lambda j: j.finished or j.updated or "")
        self.jobs_tab.select(newest.job_id)
        self.jobs_tab._load_results()


def _qt():
    from qgis.PyQt.QtCore import Qt
    return Qt
