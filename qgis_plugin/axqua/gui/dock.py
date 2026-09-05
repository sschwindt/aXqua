"""The dock: Setup, the capability tabs, and the job dashboard.

The tab set is rebuilt whenever the case changes, from ``axqua case-status --json``.
That is the design point (plan §17): the plugin does not know what TELEMAC or OpenFOAM can
do, it *asks*, so a capability added to axqua appears here without a plugin release.

The dock also carries the :class:`PluginContext` - the small object every widget uses to
reach the runner client, the project and the message bar. Passing one context beats
threading four constructor arguments through every widget, and it keeps the widgets free
of solver knowledge (plan §31).
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtWidgets import (QDockWidget, QLabel, QTabWidget, QVBoxLayout, QWidget)

from ..compat import CRITICAL, INFO, WARNING, exec_dialog, log_message, push_message
from ..core import project as project_io
from ..core.runner_client import RunnerClient, user_text
from .capability_tab_widget import CapabilityTab
from .capability_tabs import CaseView
from .jobs_widget import JobsWidget
from .settings_dialog import SettingsDialog, read_settings
from .setup_tab import SetupTab


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
        """Record what the Setup tab's background probe found."""
        self.runner_ok = ok
        self.runner_checked = True
        self.runner_error = detail

    def client_or_warn(self) -> RunnerClient | None:
        """The client, or a message saying exactly what to fix.

        Every action goes through this, so "axqua is not installed" is reported once,
        clearly, rather than as a different traceback per button.

        It answers from what the Setup tab's probe already found and **never probes
        here**: this runs inside a button handler, a probe is a subprocess, and one
        against an unreachable path costs 30 s per candidate before the click does
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
            self.warn("Add a case configuration on the Setup tab first.")
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

    def job_submitted(self, job_id: str) -> None:
        self.dock.job_submitted(job_id)

    def load_latest_results(self, kind: str) -> None:
        self.dock.load_latest_results(kind)


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

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.setWidget(container)

        self.setup_tab = SetupTab(self.ctx)
        self.tabs.addTab(self.setup_tab, "Setup")

        self.jobs_tab = JobsWidget(self.ctx)
        self.tabs.addTab(self.jobs_tab, "Jobs")

        self._capability_tabs: dict[str, CapabilityTab] = {}
        self._placeholder = QLabel(
            "Choose a case on the Setup tab. The tabs available here are generated from "
            "what axqua reports this case can actually do.")
        self._placeholder.setWordWrap(True)

        self.setup_tab.check_runner()

    # -- project ------------------------------------------------------------------
    def open_project(self, path: Path) -> None:
        try:
            self.ctx.project = project_io.load(path)
        except (OSError, ValueError) as exc:
            self.ctx.error(f"Could not open {path.name}: {exc}")
            return
        self.setup_tab.refresh()
        self.ctx.info(f"Opened {path.name} with {len(self.ctx.project.cases)} case(s).")
        # Rediscovery (plan §19): the project names the job root, and the jobs under it
        # are found with whatever state they have reached while QGIS was closed.
        self.refresh_case()
        self.jobs_tab.refresh()

    def save_project(self, path: Path | None = None) -> None:
        try:
            written = project_io.save(self.ctx.project, path)
        except (OSError, ValueError) as exc:
            self.ctx.error(f"Could not save the project: {exc}")
            return
        self.setup_tab.refresh()
        self.ctx.info(f"Saved {written.name}.")

    def add_case(self, path: Path) -> None:
        self.ctx.project.add_case(path)
        self.setup_tab.refresh()
        self.refresh_case()

    def set_active_case(self, stored: str) -> None:
        self.ctx.project.active_case = stored
        self.refresh_case()

    # -- capabilities -------------------------------------------------------------
    def refresh_case(self) -> None:
        """Re-read the capability matrix and rebuild the tabs from it.

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
        sources a shell profile and can take seconds. So the tabs appear immediately and
        the Setup tab's "environment not checked" - which it used to print for ever,
        because nothing ever asked - is replaced when the answer arrives.
        """
        from ..core.tasks import run_async
        client = self.ctx.client
        run_async("aXqua: checking the solver environments",
                  lambda: client.case_status(case, check_env=True),
                  on_success=lambda payload: self._env_checked(
                      CaseView.from_payload(payload or {})),
                  # A failed environment probe is not worth a banner: the tabs are
                  # already up and everything except the one status line still works.
                  on_error=lambda exc: log_message(
                      f"environment check failed: {user_text(exc)}", WARNING),
                  owner=self)

    def _env_checked(self, view: CaseView) -> None:
        if self.ctx.case_view is not None:
            self.ctx.case_view = view
        self.setup_tab.show_capabilities(view)

    def _apply_case_view(self, view: CaseView | None) -> None:
        self.ctx.case_view = view
        self.setup_tab.show_capabilities(view)
        self._rebuild_tabs(view)

    def _rebuild_tabs(self, view: CaseView | None) -> None:
        """Bring the generated tabs into line with *view*, reusing what fits.

        A refresh happens on every case change and after every environment check, and a
        capability tab is not cheap to build. Tabs whose key survives are re-rendered
        through ``CapabilityTab.apply`` instead - which also keeps whatever the user had
        typed into the options form.
        """
        wanted: list[tuple[str, str, object]] = []
        if view is not None:
            multi = len(view.enabled_solvers) > 1
            for solver in view.enabled_solvers:
                for capability in solver.visible_capabilities:
                    label = (f"{capability.title} ({solver.name})" if multi
                             else capability.title)
                    wanted.append((f"{solver.name}:{capability.name}", label, capability))

        keep = {key for key, _, _ in wanted}
        for key, tab in list(self._capability_tabs.items()):
            if key not in keep:
                index = self.tabs.indexOf(tab)
                if index >= 0:
                    self.tabs.removeTab(index)
                tab.deleteLater()
                del self._capability_tabs[key]

        for position, (key, label, capability) in enumerate(wanted):
            tab = self._capability_tabs.get(key)
            if tab is None:
                tab = CapabilityTab(capability, self.ctx)
                self._capability_tabs[key] = tab
                self.tabs.insertTab(position + 1, tab, label)
            else:
                tab.apply(capability)
                index = self.tabs.indexOf(tab)
                if index != position + 1 and index >= 0:
                    self.tabs.removeTab(index)
                    self.tabs.insertTab(position + 1, tab, label)
                self.tabs.setTabText(self.tabs.indexOf(tab), label)
            # Shown but disabled where axqua has not implemented it, or implements it
            # with no job kind - a gap in axqua is worth seeing, and is different from a
            # category error, which is hidden entirely.
            tab.setEnabled(capability.enabled)
            self.tabs.setTabToolTip(self.tabs.indexOf(tab), capability.reason)

        self._show_placeholder(view)

    def _show_placeholder(self, view: CaseView | None) -> None:
        """Say why there are no tabs, rather than showing an empty dock.

        A case whose config has no ``telemac:`` block gets ``enabled: false`` for every
        solver, and so no tabs at all. Without this the dock is simply blank, which
        reads as a broken plugin rather than as an unconfigured case.
        """
        index = self.tabs.indexOf(self._placeholder)
        if self._capability_tabs:
            if index >= 0:
                self.tabs.removeTab(index)
            return
        if view is None:
            self._placeholder.setText(
                "Choose a case on the Setup tab. The tabs available here are generated "
                "from what axqua reports this case can actually do.")
        elif not view.solvers:
            self._placeholder.setText(
                "axqua reported no solvers for this case. Check that the file really is "
                "an aXqua case configuration.")
        else:
            names = ", ".join(s.name for s in view.solvers)
            self._placeholder.setText(
                f"This case enables none of the solvers axqua knows about ({names}).\n\n"
                "A solver is enabled by having its block in case-config.yml - a "
                "'telemac:' block for TELEMAC, an 'openfoam:' block for OpenFOAM. Add "
                "one (see the annotated template in the docs) and press Check on the "
                "Setup tab.")
        if index < 0:
            self.tabs.insertTab(1, self._placeholder, "Capabilities")

    # -- actions ------------------------------------------------------------------
    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        if exec_dialog(dialog):
            values = dialog.values()
            self.ctx.client = RunnerClient(values["executable"] or None)
            self.ctx.min_depth = values["min_depth"]
            self.ctx.velocity_cap = values["velocity_cap"]
            self.setup_tab.check_runner()

    def job_submitted(self, job_id: str) -> None:
        self.jobs_tab.refresh()
        self.tabs.setCurrentWidget(self.jobs_tab)

    def load_latest_results(self, kind: str) -> None:
        """Load the newest completed job of *kind* for the active case."""
        jobs = [j for j in self.jobs_tab.table_model
                if j.state == "COMPLETED" and (not kind or j.kind == kind)]
        if not jobs:
            self.ctx.warn("No completed job of that kind yet. Refresh the Jobs tab if "
                          "one finished while this was open.")
            return
        newest = max(jobs, key=lambda j: j.finished or j.updated or "")
        self.jobs_tab.select(newest.job_id)
        self.jobs_tab._load_results()
