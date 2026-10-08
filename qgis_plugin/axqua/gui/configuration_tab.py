"""The *Configuration* tab: what belongs to this computer.

Three things, in the order they have to work: the ``axqua`` program the plugin calls,
the profile of this computer (``*.axq-profile``: where TELEMAC, OpenFOAM, ParaView and
VisIt are, and where jobs are kept), and the installation of the simulation programs.

The profile is edited in a window of its own (:mod:`.profile_editor`). This tab shows
what the profile says and what its check found.
"""

from __future__ import annotations

from qgis.PyQt.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                                 QPushButton, QVBoxLayout, QWidget)

from ..compat import exec_dialog
from ..core.runner_client import RunnerError, user_text
from ..core.tasks import run_async
from . import findings as fnd
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

WIZARDS = ("The installation wizards for TELEMAC and OpenFOAM are not yet available in "
           "this version. Install the programs as described in the documentation "
           "(Help) and enter their environment scripts in the profile.")


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
        note = QLabel(WIZARDS)
        note.setWordWrap(True)
        software_layout.addWidget(note)
        layout.addWidget(software_box)
        layout.addStretch(1)

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


def _wrap(layout) -> QWidget:
    widget = QWidget()
    widget.setLayout(layout)
    return widget
