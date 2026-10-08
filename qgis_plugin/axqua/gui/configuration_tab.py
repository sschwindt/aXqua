"""The *Configuration* tab: what belongs to this computer.

Three things, in the order they have to work: the ``axqua`` program the plugin calls,
the profile of this computer (``*.axq-profile``: where TELEMAC, OpenFOAM, ParaView and
VisIt are, and where jobs are kept), and the installation of the simulation programs,
each with a wizard (:mod:`.install_wizard`) that ends by entering the program in the
profile.

The profile is edited in a window of its own (:mod:`.profile_editor`). This tab shows
what the profile says and what its check found.
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                                 QPushButton, QVBoxLayout, QWidget)

from ..compat import exec_dialog
from ..core.runner_client import RunnerError, user_text
from ..core.tasks import run_async
from . import findings as fnd
from .install_wizard import InstallWizard
from .profile_editor import ProfileEditor, get

#: What the summary shows: ``(dotted key, label)``.
SUMMARY = (
    ("name", "Name"),
    ("solvers.telemac.setup_script", "TELEMAC"),
    ("solvers.openfoam.setup_script", "OpenFOAM"),
    ("postprocessors.paraview", "ParaView"),
    ("postprocessors.visit", "VisIt"),
    ("jobs.root", "Folder for jobs"),
)

NO_PROFILE = ("This computer has no profile yet. A profile tells aXqua where TELEMAC, "
              "OpenFOAM and the postprocessing programs are installed. Without one, "
              "aXqua uses what it finds by itself.")

SOFTWARE = ("A wizard installs each program and enters it in the profile. Install "
            "TELEMAC first: aXqua starts every OpenFOAM simulation from a short "
            "TELEMAC run.")

#: What the wizards install: ``(target, name on the button)``.
PROGRAMS = (("telemac", "TELEMAC"), ("openfoam", "OpenFOAM"),
            ("postprocessors", "ParaView and VisIt"))

#: Milliseconds between two looks at a running installation.
INSTALL_POLL_MS = 15000


class ConfigurationTab(QWidget):
    def __init__(self, context, parent=None) -> None:
        super().__init__(parent)
        self.ctx = context
        self.profile: dict | None = None
        self.findings: list[fnd.Finding] = []
        self.on_findings = None            # the dock marks the tab with a triangle
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        runner_box = QGroupBox("aXqua program")
        runner_form = QFormLayout(runner_box)
        self.runner_label = QLabel("not checked")
        self.runner_label.setWordWrap(True)
        runner_form.addRow("Program", self.runner_label)
        row = QHBoxLayout()
        for text, slot in (("Check", self.check_runner),
                           ("Settings...", self.ctx.open_settings)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        runner_form.addRow("", _wrap(row))
        layout.addWidget(runner_box)

        profile_box = QGroupBox("Profile of this computer")
        profile_layout = QVBoxLayout(profile_box)
        self.profile_path = QLabel("")
        self.profile_path.setWordWrap(True)
        profile_layout.addWidget(self.profile_path)
        form = QFormLayout()
        self._summary: dict[str, QLabel] = {}
        self._triangles: dict[str, fnd.TriangleButton] = {}
        for key, label in SUMMARY:
            value = QLabel("")
            value.setWordWrap(True)
            triangle = fnd.TriangleButton()
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(value, 1)
            line.addWidget(triangle)
            form.addRow(label, _wrap(line))
            self._summary[key] = value
            self._triangles[key] = triangle
        self.summary_box = _wrap(form)
        profile_layout.addWidget(self.summary_box)
        buttons = QHBoxLayout()
        self.edit_button = QPushButton("Edit profile...")
        self.edit_button.clicked.connect(self.edit_profile)
        buttons.addWidget(self.edit_button)
        self.check_button = QPushButton("Check")
        self.check_button.setToolTip("Start TELEMAC and OpenFOAM once and report what "
                                     "does not work")
        self.check_button.clicked.connect(self.check_profile)
        buttons.addWidget(self.check_button)
        buttons.addStretch(1)
        profile_layout.addLayout(buttons)
        self.finding_list = fnd.FindingList("")
        profile_layout.addWidget(self.finding_list)
        layout.addWidget(profile_box)

        software_box = QGroupBox("Simulation software")
        software_layout = QVBoxLayout(software_box)
        note = QLabel(SOFTWARE)
        note.setWordWrap(True)
        software_layout.addWidget(note)
        self.host_label = QLabel("")
        self.host_label.setWordWrap(True)
        software_layout.addWidget(self.host_label)
        programs = QFormLayout()
        self._program_labels: dict[str, QLabel] = {}
        self._program_buttons: dict[str, QPushButton] = {}
        for target, name in PROGRAMS:
            state = QLabel("")
            state.setWordWrap(True)
            button = QPushButton(f"Install {name}...")
            button.setEnabled(False)
            button.clicked.connect(lambda _=False, which=target: self.open_wizard(which))
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(state, 1)
            line.addWidget(button)
            programs.addRow(name, _wrap(line))
            self._program_labels[target] = state
            self._program_buttons[target] = button
        software_layout.addLayout(programs)
        layout.addWidget(software_box)
        layout.addStretch(1)
        self.overview: dict = {}
        self.install_timer = QTimer(self)
        self.install_timer.setInterval(INSTALL_POLL_MS)
        self.install_timer.timeout.connect(self.load_software)

    # -- the axqua program --------------------------------------------------------
    def check_runner(self) -> None:
        """Probe axqua, off the GUI thread.

        The dock's constructor calls this while QGIS starts. ``axqua --version`` takes
        seconds, and three candidate paths with one on an unreachable network mount take
        a minute and a half of frozen QGIS, so the probe is queued like every other call.
        """
        self.runner_label.setText("checking...")
        run_async("aXqua: checking the axqua program", self.ctx.client.validate,
                  on_success=self._runner_checked,
                  on_error=self._runner_failed, owner=self)

    def _runner_checked(self, info) -> None:
        self.runner_label.setText(info.describe())
        self.ctx.set_runner_ok(True)
        self.load_kinds()
        self.load_profile()
        self.load_software()

    def _runner_failed(self, exc: Exception) -> None:
        text = exc.user_text() if isinstance(exc, RunnerError) else str(exc)
        self.runner_label.setText(text)
        self.ctx.warn(text)
        self.ctx.set_runner_ok(False, text)

    def load_kinds(self) -> None:
        """Ask axqua what it can run, for the list of the Processing algorithm, which
        is built while QGIS starts and cannot ask for itself."""
        def store(kinds):
            from ..processing.algorithms.submit import set_kinds
            set_kinds(kinds)

        run_async("aXqua: reading the job kinds", self.ctx.client.kinds,
                  on_success=store, on_error=lambda _exc: None, owner=self)

    # -- the profile --------------------------------------------------------------
    def load_profile(self) -> None:
        client = self.ctx.client

        def read():
            where = client.profile_path()
            if not where.get("exists"):
                return where, None
            return where, client.profile_show()

        run_async("aXqua: reading the profile of this computer", read,
                  on_success=lambda answer: self.show_profile(*answer),
                  on_error=lambda exc: self.show_profile({}, None, error=user_text(exc)),
                  owner=self)

    def show_profile(self, where: dict, profile: dict | None, *, error: str = "") -> None:
        self.profile = profile
        self.ctx.profile = profile
        path = str((where or {}).get("path") or "")
        if error:
            self.profile_path.setText("The profile could not be read: " + error)
        elif profile is None:
            self.profile_path.setText(NO_PROFILE + (f"\nIt will be saved as {path}."
                                                    if path else ""))
        else:
            self.profile_path.setText(path)
        for key, label in self._summary.items():
            value = get(profile or {}, key)
            label.setText("not set" if value in (None, "") else str(value))
        self.summary_box.setVisible(profile is not None)    # nothing to show without one
        self.edit_button.setText("Edit profile..." if profile is not None
                                 else "Create profile...")
        self.check_button.setEnabled(profile is not None)
        if profile is None:
            self.show_findings([], checked=False)
        if getattr(self.ctx, "profile_changed", None):
            self.ctx.profile_changed(profile)

    def check_profile(self) -> None:
        self.check_button.setEnabled(False)
        self.finding_list.set_findings([])
        client = self.ctx.client
        run_async("aXqua: checking the profile of this computer",
                  lambda: client.profile_check(probe=True),
                  on_success=lambda answer: self.show_findings(
                      fnd.from_payload(answer), checked=True),
                  on_error=lambda exc: self.ctx.error(user_text(exc)), owner=self)

    def show_findings(self, findings, *, checked: bool) -> None:
        self.findings = list(findings)
        self.check_button.setEnabled(self.profile is not None)
        self.finding_list.set_findings(self.findings, checked=checked)
        for key, triangle in self._triangles.items():
            triangle.set_findings(fnd.for_subject(self.findings, key))
        if self.on_findings is not None:
            self.on_findings(fnd.worst(self.findings))

    def edit_profile(self) -> None:
        """Open the editor on the profile, or on what aXqua finds on this computer."""
        if self.profile is not None:
            self._open_editor(self.profile, "")
            return
        run_async("aXqua: looking for the simulation programs",
                  self.ctx.client.profile_detect,
                  on_success=lambda found: self._open_editor(
                      found, "aXqua has entered what it found on this computer. "
                             "Check the entries and click Save."),
                  on_error=lambda exc: self._open_editor({}, user_text(exc)), owner=self)

    def _open_editor(self, profile: dict, title: str) -> None:
        editor = ProfileEditor(self.ctx.client, profile, title=title, parent=self)
        if self.findings:
            editor.show_findings(self.findings)
        exec_dialog(editor)
        if editor.saved_once:
            self.show_findings(editor.finding_list.findings, checked=True)
            self.load_profile()


    # -- the simulation programs ---------------------------------------------------
    def load_software(self) -> None:
        """Ask what is installed and whether an installation is running."""
        run_async("aXqua: looking for the simulation programs",
                  self.ctx.client.install_overview,
                  on_success=self.show_software,
                  on_error=lambda exc: self.host_label.setText(user_text(exc)),
                  owner=self)

    def show_software(self, overview: dict) -> None:
        self.overview = overview or {}
        host = self.overview.get("host") or {}
        text = "This computer: " + str(host.get("description") or "unknown")
        if host and not host.get("supported"):
            text += ". There is no installer for this system; the wizards say what to do."
        self.host_label.setText(text)
        running = False
        for entry in self.overview.get("targets") or []:
            target = entry.get("target")
            if target not in self._program_labels:
                continue
            self._program_labels[target].setText(describe_program(entry))
            button = self._program_buttons[target]
            button.setEnabled(True)
            name = dict(PROGRAMS)[target]
            active = bool(entry.get("running"))
            running = running or active
            button.setText("Show the installation..." if active
                           else f"Install {name}...")
        # an installation outlives the wizard, so the tab keeps looking by itself
        if running and not self.install_timer.isActive():
            self.install_timer.start()
        elif not running and self.install_timer.isActive():
            self.install_timer.stop()

    def open_wizard(self, target: str) -> None:
        wizard = InstallWizard(self.ctx.client, target, self.overview, parent=self,
                               on_done=lambda _status: self._installed())
        exec_dialog(wizard)
        self.load_software()

    def _installed(self) -> None:
        """An installation has ended: the profile may name a new program now."""
        self.load_profile()
        self.load_software()


def describe_program(entry: dict) -> str:
    """One line on a program: where it is, or how far its installation is."""
    running = entry.get("running") or {}
    if running:
        steps = running.get("steps") or []
        minutes = float(running.get("elapsed") or 0.0) / 60.0
        step = (f", step {running.get('step', 0)} of {len(steps)}" if steps else "")
        return f"The installation is running{step} ({minutes:.0f} min)."
    installed = entry.get("installed") or {}
    last = entry.get("last") or {}
    if installed:
        return "; ".join(str(path) for path in installed.values())
    if last.get("state") == "failed":
        return "Not found on this computer. The last installation failed."
    return "Not found on this computer."


def _wrap(layout) -> QWidget:
    widget = QWidget()
    widget.setLayout(layout)
    return widget
