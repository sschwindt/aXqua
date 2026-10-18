"""The installation wizards: TELEMAC, OpenFOAM, and the postprocessors.

One window with three tabs, the same for each program. The tabs can be visited in any
order, and the buttons *Back* and *Next* walk through them:

1. **Settings.** Where to install, and the few choices the installer offers. Every path
   has a dialog; nothing has to be typed. Opened for a program that is installed
   already, the tab starts with what happens to the existing installation.
2. **Check.** What ``axqua install plan`` answers: the commands that will run, how long
   it takes, and what is not in order. System packages are the one thing that needs an
   administrator: the tab shows them as the required user actions in a terminal, with
   the one command that installs them. *Run fixes* runs that command (the password is
   asked by the dialog of the desktop, or in a terminal window, and never passes
   through this plugin), checks again and repeats while the fixes make progress. When
   they do not, *Report issue* writes a report to send. *Install* starts the
   installation.
3. **Installation.** The state, the step and the end of the log, refreshed every two
   seconds. The installation is a process of its own: this window and QGIS can be
   closed, and the wizard shows the running installation again when it is reopened.

The wizard decides nothing about the installation itself. What is installed, from
where and with which commands is the answer of ``axqua install``, and the same command
does the same in a terminal.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                                 QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                                 QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                                 QPushButton, QScrollArea, QSpinBox, QTabWidget,
                                 QVBoxLayout, QWidget)

from ..compat import enum_value, exec_dialog
from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import findings as fnd
from . import help as help_pages
from . import report_issue
from .style import AXQUA_PURPLE, DONE_BUTTON_STYLE, REQUIRED_STYLE, mark_required

VIEWERS_HINT = ("aXqua recommends VisIt: its predefined figures of three-dimensional "
                "results are rendered with it. VisIt is a download of 600 MB from its "
                "publisher. ParaView is a package of the operating system. Both can "
                "be installed later.")

SCOPE_HINT = ("For all users, an administrator creates the folder before the build and "
              "hands it over to root at its end; the desktop asks for the password "
              "both times. The build itself runs as you.")

#: ``(option, label, kind, hint)`` per program. Kinds: folder, file, text, int, bool,
#: choice (one of CHOICES), openfoam (an installed OpenFOAM, or compiling one).
FIELDS = {
    "telemac": (
        ("scope", "Install for", "choice", SCOPE_HINT),
        ("folder", "Installation folder", "folder",
         "TELEMAC is installed in the sub-folder telemac-mascaret of this folder."),
        ("tag", "TELEMAC version", "text",
         "Leave empty for the version the installer was written for."),
        ("telemac_examples", "Example cases of TELEMAC", "choice",
         "TELEMAC comes with about 830 example cases. Their steering files are always "
         "installed. The input files are what a run of an example reads, mainly its "
         "mesh. The reference results serve the validation system of TELEMAC and "
         "take most of an hour to download. aXqua needs none of these files."),
        ("salome", "SALOME archive", "file",
         "Optional: a downloaded SALOME archive. aXqua uses Gmsh and does not need "
         "SALOME."),
    ),
    "openfoam": (
        ("scope", "Install for", "choice", SCOPE_HINT),
        ("folder", "Installation folder", "folder",
         "A folder that does not exist yet, or an empty one. The sediment solvers, "
         "the outflow boundary and VisIt are installed in it."),
        ("reuse_openfoam", "OpenFOAM v2406", "openfoam", ""),
        ("jobs", "Processes for compiling", "int", ""),
        ("viewers", "Postprocessors", "choice", VIEWERS_HINT),
        ("examples", "Download the example case of the sediment solver", "bool", ""),
        ("smoke_test", "Run a short test at the end", "bool", ""),
    ),
    "postprocessors": (
        ("scope", "Install for", "choice", SCOPE_HINT),
        ("folder", "Installation folder", "folder",
         "VisIt is installed in this folder. ParaView is a package of the operating "
         "system."),
        ("viewers", "Postprocessors", "choice", VIEWERS_HINT),
    ),
}

#: The settings an installation cannot do without, marked in aXqua purple.
REQUIRED = ("folder",)

#: The switches that are on unless the user turns them off.
ON_BY_DEFAULT = ("smoke_test", "bind")

#: ``option -> ((value, text), ...)`` of the settings with a fixed set of answers. The
#: first entry is the default.
CHOICES = {
    "scope": (
        ("user", "Me only, in my home folder (no administrator rights)"),
        ("system", "All users of this computer, below /opt (administrator password)"),
    ),
    "telemac_examples": (
        ("inputs", "Input files of examples only (about 360 files, 460 MB)"),
        ("all", "Everything, with reference results and manuals (1,500 files, 1.65 GB)"),
        ("none", "Steering files only"),
    ),
    "viewers": (
        ("both", "ParaView and VisIt (recommended)"),
        ("paraview", "ParaView only"),
        ("none", "None"),
    ),
}

#: What happens to an installation that exists, when the wizard is opened for a
#: program that is installed already. ``new`` sends no option: it is a new folder.
REINSTALL = (
    ("replace", "Remove the existing installation and install it again in its folder"),
    ("continue", "Continue in the existing folder, keeping what is downloaded and built"),
    ("new", "Install it again in a new folder; the existing installation stays"),
)

#: ``(program, option) -> answers that are not offered there``. The installation of the
#: postprocessors installs at least one of them.
NOT_OFFERED = {("postprocessors", "viewers"): ("none",)}

BASES = (("", "as detected"), ("debian12", "Debian 12"), ("ubuntu22", "Ubuntu 22.04"),
         ("ubuntu24", "Ubuntu 24.04"))

USE_INSTALLED = "Use the OpenFOAM v2406 that is installed on this computer"
COMPILE = "Compile OpenFOAM v2406 from its source code (several hours)"

KEEPS_RUNNING = ("The installation runs by itself. This window and QGIS can be closed; "
                 "open the wizard again to see how far it is.")

#: The heading of the commands the user has to run, in the words of the user's order.
ACTIONS_HEADING = "REQUIRED USER ACTIONS in TERMINAL:"

NOT_STARTED = ("No installation has been started yet. Check this computer on the tab "
               "Check and click Install there.")

STATES = {"queued": "starting", "running": "running", "succeeded": "finished",
          "failed": "failed", "cancelled": "cancelled"}
ACTIVE = ("queued", "running")
TABS = ("Settings", "Check", "Installation")

POLL_MS = 2000
LOG_LINES = 200
#: Rounds of *Run fixes* before the wizard stops repeating them and offers a report.
MAX_FIX_ROUNDS = 3
#: How often and how many times the check is repeated while a terminal runs the fixes.
TERMINAL_POLL_MS = 15000
TERMINAL_POLLS = 40

#: Terminal programs that can run a command, with the arguments that hand it over.
TERMINALS = (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]),
             ("konsole", ["-e"]), ("xfce4-terminal", ["-x"]), ("mate-terminal", ["-x"]),
             ("tilix", ["-e"]), ("xterm", ["-e"]))


def terminal_argv(command: str) -> list[str]:
    """The argv that opens a terminal window running *command*, or ``[]``."""
    script = (command + "; echo; read -r -p 'Done. Press Enter to close this "
              "window. ' _")
    for program, flags in TERMINALS:
        found = shutil.which(program)
        if found:
            return [found, *flags, "bash", "-c", script]
    return []


def new_folder(folder: str) -> str:
    """A folder beside *folder* that does not exist yet: ``<folder>-2``, ``-3``, ..."""
    base = folder.rstrip("/")
    for number in range(2, 100):
        candidate = f"{base}-{number}"
        if not Path(candidate).exists():
            return candidate
    return base + "-new"


class InstallWizard(QDialog):
    """Install one program. *overview* is the answer of ``axqua install overview``.

    *reinstall*: the program is installed already, and the user confirmed to install
    it again. The Settings tab then starts with what happens to that installation.
    """

    def __init__(self, client, target: str, overview: dict | None = None, *,
                 parent=None, on_done=None, reinstall: bool = False) -> None:
        super().__init__(parent)
        self.client = client
        self.target = target
        self.overview = overview or {}
        self.on_done = on_done
        self.entry = next((t for t in self.overview.get("targets") or []
                           if t.get("target") == target), {})
        self.existing = dict(self.entry.get("existing") or {})
        self.reinstall = bool(reinstall)
        self.plan: dict | None = None
        self.status: dict = {}
        self.install_id = ""
        self._asking = False
        self._planned_with: dict | None = None    # the options of the plan shown
        self._widgets: dict[str, QWidget] = {}
        self._triangles: dict[str, fnd.TriangleButton] = {}
        # Run fixes
        self._fixing = ""                          # "", "desktop" or "terminal"
        self._fix_rounds = 0
        self._missing_before: list[str] = []
        self._terminal_polls = 0
        self.setWindowTitle(("Install again: " if self.reinstall else "Install ")
                            + str(self.entry.get("title") or target))
        self.resize(780, 680)
        self._build()
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.poll)
        self.fix_timer = QTimer(self)
        self.fix_timer.setInterval(TERMINAL_POLL_MS)
        self.fix_timer.timeout.connect(self._terminal_poll)
        running = self.entry.get("running")
        if running:
            self.attach(running)

    # -- construction -------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        host = self.overview.get("host") or {}
        self.host_label = QLabel(
            "This computer: " + str(host.get("description") or "not detected yet"))
        self.host_label.setWordWrap(True)
        outer.addWidget(self.host_label)

        self.tabs = QTabWidget()
        self.tabs.addTab(_scrolling(self._settings_page()), TABS[0])
        self.tabs.addTab(self._check_page(), TABS[1])
        self.tabs.addTab(self._run_page(), TABS[2])
        self.tabs.currentChanged.connect(self._tab_changed)
        outer.addWidget(self.tabs, 1)

        buttons = QHBoxLayout()
        self.help_button = QPushButton("Help")
        self.help_button.clicked.connect(self.open_help)
        buttons.addWidget(self.help_button)
        buttons.addStretch(1)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.go_back)
        buttons.addWidget(self.back_button)
        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(self.go_next)
        buttons.addWidget(self.next_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.close_button)
        outer.addLayout(buttons)
        self._update_buttons()

    @property
    def pages(self) -> QTabWidget:
        """The tabs; the name of the pages they replaced."""
        return self.tabs

    def _settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        if self.reinstall:
            self._reinstall_row(form)
        for key, label, kind, hint in FIELDS[self.target]:
            form.addRow("" if kind == "bool" else label, self._row(key, label, kind))
            if key in REQUIRED:
                mark_required(form.labelForField(form.itemAt(form.rowCount() - 1,
                                                             _FIELD_ROLE).widget()),
                              True)
            if hint:
                form.addRow("", _note(hint))
        layout.addLayout(form)

        self.advanced = QGroupBox("Further settings")
        self.advanced.setCheckable(True)
        self.advanced.setChecked(False)
        more = QFormLayout(self.advanced)
        more.addRow("", self._row("bind", "Enter the installation in the profile of "
                                          "this computer", "bool"))
        base = QComboBox()
        for value, text in BASES:
            base.addItem(text, value)
        self._widgets["base"] = base
        more.addRow("Operating system", base)
        more.addRow("", _note("Only for a system that is built on one of these and is "
                              "not recognized by itself."))
        more.addRow("Installer scripts", self._row("installers", "", "folder"))
        more.addRow("", _note("Optional: a folder with a copy of the installer "
                              "scripts, for a computer without a connection to the "
                              "internet."))
        self._toggle_advanced(False)
        self.advanced.toggled.connect(self._toggle_advanced)
        layout.addWidget(self.advanced)
        layout.addStretch(1)
        self._fill_defaults()
        return page

    def _reinstall_row(self, form: QFormLayout) -> None:
        """What happens to the installation that exists."""
        folder = str(self.existing.get("folder") or "")
        choice = QComboBox()
        for value, text in REINSTALL:
            if value in ("replace", "continue") and not self.existing.get("removable"):
                continue        # not made by an installer of aXqua: never touched
            choice.addItem(text, value)
        self._widgets["reinstall"] = choice
        form.addRow("Existing installation", choice)
        where = (f"The existing installation is in {folder}." if folder else
                 "aXqua found the program but not the folder of its installation.")
        if folder and not self.existing.get("removable"):
            where += (" It was not made by an installer of aXqua, so aXqua leaves it "
                      "as it is and installs into a new folder.")
        self.reinstall_note = _note(where)
        form.addRow("", self.reinstall_note)
        choice.currentIndexChanged.connect(lambda _=0: self._reinstall_changed())

    def _reinstall_changed(self) -> None:
        """The folder follows the choice for the existing installation."""
        folder = self._widgets.get("folder")
        choice = self._widgets.get("reinstall")
        if folder is None or choice is None:
            return
        existing = str(self.existing.get("folder") or "")
        if choice.currentData() == "new":
            folder.setText(new_folder(existing) if existing
                           else self._default_folder(self._scope()))
        elif existing:
            folder.setText(existing)

    def _toggle_advanced(self, shown: bool) -> None:
        for child in self.advanced.findChildren(QWidget):
            child.setVisible(shown)

    def _row(self, key: str, label: str, kind: str) -> QWidget:
        holder = QWidget()
        line = QHBoxLayout(holder)
        line.setContentsMargins(0, 0, 0, 0)
        if kind == "bool":
            widget = QCheckBox(label)
            widget.setChecked(key in ON_BY_DEFAULT)
            line.addWidget(widget, 1)
        elif kind == "int":
            widget = QSpinBox()
            widget.setRange(1, 512)
            line.addWidget(widget)
            line.addStretch(1)
        elif kind == "choice":
            widget = QComboBox()
            for value, text in CHOICES[key]:
                if value not in NOT_OFFERED.get((self.target, key), ()):
                    widget.addItem(text, value)
            line.addWidget(widget, 1)
        elif kind == "openfoam":
            widget = QComboBox()
            widget.addItem(USE_INSTALLED, "installed")
            widget.addItem(COMPILE, "no")
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(widget)
            path = QLineEdit()
            path.setPlaceholderText("the file etc/bashrc of OpenFOAM v2406; empty: "
                                    "aXqua looks for it")
            self._widgets["openfoam_path"] = path
            browse = QPushButton("Browse...")
            browse.clicked.connect(lambda _=False: self._browse("openfoam_path", False))
            below = QHBoxLayout()
            below.addWidget(path, 1)
            below.addWidget(browse)
            column.addLayout(below)
            widget.currentIndexChanged.connect(
                lambda _=0: (path.setEnabled(widget.currentData() == "installed"),
                             browse.setEnabled(widget.currentData() == "installed")))
            line.addLayout(column, 1)
        else:
            widget = QLineEdit()
            line.addWidget(widget, 1)
            if kind in ("folder", "file"):
                browse = QPushButton("Browse...")
                browse.clicked.connect(
                    lambda _=False, k=key, f=(kind == "folder"): self._browse(k, f))
                line.addWidget(browse)
        triangle = fnd.TriangleButton()
        line.addWidget(triangle)
        self._widgets[key] = widget
        self._triangles[key] = triangle
        return holder

    def _scope(self) -> str:
        scope = self._widgets.get("scope")
        return (scope.currentData() if scope is not None else "") or "user"

    def _default_folder(self, scope: str) -> str:
        folders = self.entry.get("default_folders") or {}
        if folders.get(scope):
            return str(folders[scope])
        return str(self.entry.get("default_folder") or "") if scope == "user" else ""

    def _scope_changed(self) -> None:
        """Move the folder along with the scope, unless the user typed one."""
        folder = self._widgets.get("folder")
        scope = self._scope()
        other = "system" if scope == "user" else "user"
        if folder is not None and folder.text().strip() in ("", self._default_folder(other)):
            folder.setText(self._default_folder(scope))

    def _fill_defaults(self) -> None:
        folder = self._widgets.get("folder")
        if folder is not None:
            folder.setText(str(self.entry.get("default_folder") or ""))
        if "scope" in self._widgets:
            existing = str(self.existing.get("folder") or "")
            if self.reinstall and existing.startswith("/opt/"):
                self._widgets["scope"].setCurrentIndex(1)   # it was for all users
            self._widgets["scope"].currentIndexChanged.connect(
                lambda _=0: self._scope_changed())
        if "jobs" in self._widgets:
            self._widgets["jobs"].setValue(int(self.overview.get("default_jobs") or 1))
        if "reuse_openfoam" in self._widgets:
            found = str(self.overview.get("openfoam_found") or "")
            self._widgets["openfoam_path"].setText(found)
            # without an OpenFOAM to build on, compiling is what will happen
            self._widgets["reuse_openfoam"].setCurrentIndex(0 if found else 1)
        if self.reinstall:
            self._reinstall_changed()

    def _browse(self, key: str, folder: bool) -> None:
        edit = self._widgets[key]
        start = edit.text() or ""
        if folder:
            chosen = QFileDialog.getExistingDirectory(self, "Select a folder", start)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, "Select a file", start)
        if chosen:
            edit.setText(chosen)

    def _check_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.plan_label = QLabel("")
        self.plan_label.setWordWrap(True)
        layout.addWidget(self.plan_label)
        self.commands = QPlainTextEdit()
        self.commands.setReadOnly(True)
        self.commands.setFont(_fixed_font())
        self.commands.setMaximumHeight(110)
        layout.addWidget(self.commands)

        self.package_box = QGroupBox("System packages")
        package_layout = QVBoxLayout(self.package_box)
        self.actions_heading = QLabel(ACTIONS_HEADING)
        self.actions_heading.setStyleSheet(REQUIRED_STYLE)
        package_layout.addWidget(self.actions_heading)
        self.package_command = QPlainTextEdit()
        self.package_command.setReadOnly(True)
        self.package_command.setFont(_fixed_font())
        self.package_command.setMinimumHeight(120)
        self.package_command.setMaximumHeight(170)
        self.package_command.setStyleSheet(f"QPlainTextEdit {{ color: {AXQUA_PURPLE}; }}")
        package_layout.addWidget(self.package_command)
        self.package_label = QLabel("")
        self.package_label.setWordWrap(True)
        package_layout.addWidget(self.package_label)
        row = QHBoxLayout()
        self.fix_button = QPushButton("Run fixes")
        self.fix_button.setToolTip("Run the command above, check again, and repeat "
                                   "while that helps. The password is asked by the "
                                   "system, not by aXqua.")
        self.fix_button.clicked.connect(self.run_fixes)
        row.addWidget(self.fix_button)
        self.copy_button = QPushButton("Copy the command")
        self.copy_button.clicked.connect(self.copy_command)
        row.addWidget(self.copy_button)
        row.addStretch(1)
        self.recheck_button = QPushButton("Check again")
        self.recheck_button.clicked.connect(self.load_plan)
        row.addWidget(self.recheck_button)
        package_layout.addLayout(row)
        layout.addWidget(self.package_box)

        self.finding_list = fnd.FindingList("")
        layout.addWidget(self.finding_list)
        layout.addStretch(1)
        bottom = QHBoxLayout()
        self.install_button = QPushButton("Install")
        self.install_button.setStyleSheet("QPushButton { font-weight: bold; }")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.start)
        bottom.addWidget(self.install_button)
        self.report_button = QPushButton("Report issue")
        self.report_button.setToolTip("The checks and fixes did not solve it: send a "
                                      "report on GitHub or by e-mail")
        self.report_button.clicked.connect(self.report)
        self.report_button.setVisible(False)
        bottom.addWidget(self.report_button)
        bottom.addStretch(1)
        layout.addLayout(bottom)
        return page

    def _run_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.state_label = QLabel(NOT_STARTED)
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(_fixed_font())
        self.log_view.setLineWrapMode(enum_value(
            QPlainTextEdit, "LineWrapMode.NoWrap", "NoWrap"))
        layout.addWidget(self.log_view, 1)
        self.log_path = QLabel("")
        self.log_path.setWordWrap(True)
        self.log_path.setTextInteractionFlags(_selectable())
        layout.addWidget(self.log_path)
        self.result_list = fnd.FindingList("")
        layout.addWidget(self.result_list)
        row = QHBoxLayout()
        self.keeps_label = QLabel(KEEPS_RUNNING)
        self.keeps_label.setWordWrap(True)
        row.addWidget(self.keeps_label, 1)
        self.run_report_button = QPushButton("Report issue")
        self.run_report_button.clicked.connect(self.report)
        self.run_report_button.setVisible(False)
        row.addWidget(self.run_report_button)
        self.cancel_button = QPushButton("Cancel the installation")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_or_close)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        return page

    # -- tabs ---------------------------------------------------------------------
    def show_page(self, index: int) -> None:
        self.tabs.setCurrentIndex(index)
        self._update_buttons()

    def _tab_changed(self, index: int) -> None:
        # a check of other settings than those on the screen is made again
        if index == 1 and (self.plan is None or self._planned_with != self.options()):
            self.load_plan()
        self._update_buttons()

    def _update_buttons(self) -> None:
        index = self.tabs.currentIndex()
        self.back_button.setVisible(index > 0)
        self.next_button.setVisible(index < 2)

    def go_back(self) -> None:
        self.show_page(max(self.tabs.currentIndex() - 1, 0))

    def go_next(self) -> None:
        self.show_page(min(self.tabs.currentIndex() + 1, 2))

    def open_help(self) -> None:
        help_pages.open_window_help(f"install-{self.target}")

    # -- the choices --------------------------------------------------------------
    def options(self) -> dict:
        """What the user chose, as the options of ``axqua install``."""
        out: dict = {}
        for key, widget in self._widgets.items():
            if key == "openfoam_path":
                continue
            if isinstance(widget, QCheckBox):
                out[key] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                out[key] = widget.value()
            elif key == "reuse_openfoam":
                path = self._widgets["openfoam_path"].text().strip()
                out[key] = "no" if widget.currentData() == "no" else (path or "auto")
            elif key == "reinstall":
                value = widget.currentData() or ""
                out[key] = "" if value == "new" else value
            elif isinstance(widget, QComboBox):
                out[key] = widget.currentData() or ""
            else:
                out[key] = widget.text().strip()
        return out

    # -- the check ----------------------------------------------------------------
    def load_plan(self) -> None:
        self.plan = None
        self.plan_label.setText("Checking this computer. The first check downloads "
                                "the installer scripts.")
        self.commands.setPlainText("")
        self.package_box.setVisible(False)
        self.finding_list.set_findings([])
        self.install_button.setEnabled(False)
        self.recheck_button.setEnabled(False)
        options = self.options()
        self._planned_with = dict(options)
        run_async(f"aXqua: checking the installation of {self.target}",
                  lambda: self.client.install_plan(self.target, options),
                  on_success=self.show_plan,
                  on_error=lambda exc: self.show_plan(None, error=user_text(exc)),
                  owner=self)

    def show_plan(self, plan: dict | None, *, error: str = "") -> None:
        self.plan = plan
        self.recheck_button.setEnabled(True)
        if plan is None:
            self.plan_label.setText("The check could not be made: " + error)
            self.install_button.setEnabled(False)
            self.report_button.setVisible(True)
            return
        host = plan.get("host") or {}
        if host.get("description"):
            self.host_label.setText("This computer: " + str(host["description"]))
        lines = [f"<b>{plan.get('title', '')}</b> is installed in "
                 f"{plan.get('folder', '')}."]
        lines += [str(note) for note in plan.get("notes") or []]
        if plan.get("estimate"):
            lines.append("Time: " + str(plan["estimate"]) + ".")
        self.plan_label.setText("<br>".join(lines))
        self.commands.setPlainText("\n".join(
            f"{number}. {step.get('name', '')}\n   $ {step.get('command', '')}"
            for number, step in enumerate(plan.get("steps") or [], start=1)))

        packages = plan.get("packages") or {}
        missing = self.missing()
        needed = list(packages.get("needed") or [])
        self.package_box.setVisible(bool(needed))
        if missing:
            self.package_label.setText(
                f"{len(missing)} of {len(needed)} system packages are not installed. "
                + (str(packages.get("note")) + " " if packages.get("note") else "")
                + "Installing them needs the rights of an administrator: click Run "
                "fixes, or copy the command into a terminal, wait until it has "
                "finished, and click Check again.")
        else:
            self.package_label.setText(f"All {len(needed)} system packages that the "
                                       "installation needs are installed.")
        self.package_command.setPlainText(str(packages.get("command") or ""))
        for widget in (self.actions_heading, self.package_command, self.copy_button,
                       self.fix_button):
            widget.setVisible(bool(missing))
        desktop = packages.get("elevation") == "pkexec"
        self.fix_button.setEnabled(bool(missing) and not self._fixing
                                   and (desktop or bool(terminal_argv("true"))))
        self.fix_button.setToolTip(
            "Run the command above, check again, and repeat while that helps. "
            + ("The desktop asks for the password of an administrator." if desktop else
               "A terminal window opens and asks for the password."))

        found = fnd.from_payload(plan)
        self.finding_list.set_findings(found, checked=True)
        prefix = f"install.{self.target}."
        for key, triangle in self._triangles.items():
            triangle.set_findings(fnd.for_subject(found, prefix + key))
        ready = bool(plan.get("ready")) and not self._active()
        self.install_button.setEnabled(ready)
        self.install_button.setToolTip(
            "" if plan.get("ready") else
            "The installation cannot be started as it is. The list says why.")
        if self._fixing:
            self._after_fix()
        elif not plan.get("ready") and self._fix_rounds:
            self.report_button.setVisible(True)

    def missing(self) -> list[str]:
        """The system packages the last check found missing."""
        return list(((self.plan or {}).get("packages") or {}).get("missing") or [])

    def copy_command(self) -> None:
        QApplication.clipboard().setText(self.package_command.toPlainText())
        self.package_label.setText(
            "The command is in the clipboard. Paste it into a terminal, wait until it "
            "has finished, and click Check again.")

    # -- Run fixes ----------------------------------------------------------------
    def run_fixes(self) -> None:
        """Run the command of the missing packages, then check again."""
        packages = (self.plan or {}).get("packages") or {}
        command = str(packages.get("command") or "")
        if not command or not self.missing():
            return
        self._fix_rounds += 1
        self._missing_before = self.missing()
        self.fix_button.setEnabled(False)
        self.report_button.setVisible(False)
        if packages.get("elevation") == "pkexec":
            self._fixing = "desktop"
            self.install_packages()
            return
        argv = terminal_argv(command)
        if not argv:
            self._fixing = ""
            self.package_label.setText("No terminal program was found. Copy the "
                                       "command into a terminal and click Check again.")
            return
        from qgis.PyQt.QtCore import QProcess

        self._fixing = "terminal"
        QProcess.startDetached(argv[0], argv[1:])
        self._terminal_polls = 0
        self.fix_timer.start()
        self.package_label.setText("A terminal window runs the command and asks for "
                                   "the password. aXqua checks again every 15 seconds "
                                   "until the packages are installed.")

    def install_packages(self) -> None:
        """Let the desktop ask for the password and install the missing packages."""
        self.fix_button.setEnabled(False)
        self.package_label.setText("Installing the packages. The desktop asks for the "
                                   "password of an administrator.")
        options = self.options()
        run_async("aXqua: installing system packages",
                  lambda: self.client.install_packages(self.target, options,
                                                       elevate=True),
                  on_success=self._packages_installed,
                  on_error=lambda exc: self._packages_installed(
                      {"installation": {"message": user_text(exc)}}),
                  owner=self)

    def _packages_installed(self, answer: dict) -> None:
        message = str((answer.get("installation") or {}).get("message") or "")
        self._fix_message = message
        self.load_plan()
        if message and not self._fixing:
            self.plan_label.setText(message[:1].upper() + message[1:] + ".")

    def _terminal_poll(self) -> None:
        self._terminal_polls += 1
        if self._terminal_polls > TERMINAL_POLLS:
            self.fix_timer.stop()
            self._fixing = "terminal-ended"
        self.load_plan()

    def _after_fix(self) -> None:
        """A check after a round of fixes: done, repeat, or offer a report."""
        missing = self.missing()
        mode = self._fixing
        if not missing:
            self.fix_timer.stop()
            self._fixing = ""
            self.package_label.setText("The fixes worked: all system packages that the "
                                       "installation needs are installed.")
            return
        if mode == "terminal":
            return                                 # the terminal may still be running
        self._fixing = ""
        progress = missing != self._missing_before
        if mode == "desktop" and progress and self._fix_rounds < MAX_FIX_ROUNDS:
            self.run_fixes()                       # it helped: once more
            return
        self.fix_timer.stop()
        self.report_button.setVisible(True)
        message = getattr(self, "_fix_message", "")
        self.package_label.setText(
            f"The fixes did not install {len(missing)} package(s): " + " ".join(missing)
            + "." + (f" The system answered: {message}." if message else "")
            + " Click Report issue to send what happened.")

    # -- report -------------------------------------------------------------------
    def report(self) -> None:
        title, body = report_issue.installation_report(
            self.target, self.plan, self.status if self.install_id else None,
            rounds=self._fix_rounds)
        exec_dialog(report_issue.report_dialog(title, body, parent=self))

    # -- the installation ---------------------------------------------------------
    def _active(self) -> bool:
        return str(self.status.get("state") or "") in ACTIVE

    def start(self) -> None:
        self.install_button.setEnabled(False)
        options = self.options()
        run_async(f"aXqua: starting the installation of {self.target}",
                  lambda: self.client.install_start(self.target, options),
                  on_success=self.attach, on_error=self._start_failed, owner=self)

    def _start_failed(self, exc: Exception) -> None:
        self.install_button.setEnabled(True)
        QMessageBox.warning(self, self.windowTitle(), user_text(exc))

    def attach(self, status: dict) -> None:
        """Show an installation that is running, or one that was just started."""
        self.install_id = str(status.get("id") or "")
        self.show_page(2)
        self.show_status(status)
        if status.get("state") in ACTIVE:
            self.timer.start()

    def poll(self) -> None:
        if self._asking or not self.install_id:
            return
        self._asking = True
        ident = self.install_id

        def done(status=None) -> None:
            self._asking = False
            if status:
                self.show_status(status)

        run_async("aXqua: reading the state of the installation",
                  lambda: self.client.install_status(ident, tail=LOG_LINES),
                  on_success=done, on_error=lambda _exc: done(), owner=self)

    def show_status(self, status: dict) -> None:
        self.status = status
        state = str(status.get("state") or "")
        steps = list(status.get("steps") or [])
        minutes = float(status.get("elapsed") or 0.0) / 60.0
        text = f"<b>{STATES.get(state, state)}</b>"
        if state == "running" and steps:
            text += (f", step {status.get('step', 0)} of {len(steps)}: "
                     f"{status.get('step_name', '')}")
        if status.get("elapsed") is not None:
            text += f" ({minutes:.0f} min)"
        if state in ("failed", "cancelled") and status.get("message"):
            text += "<br>" + str(status["message"])
        if state == "succeeded":
            text += "<br>" + self._result_text(status)
        self.state_label.setText(text)
        if "log_tail" in status:
            self._set_log(str(status["log_tail"]))
        self.log_path.setText("Complete log: " + str(status.get("log") or ""))
        active = state in ACTIVE
        self._show_cancel(state)
        self.keeps_label.setVisible(active)
        self.run_report_button.setVisible(state == "failed")
        self.result_list.set_findings(fnd.from_payload(status), checked=not active)
        self.result_list.setVisible(not active)
        if self.plan is not None:
            self.install_button.setEnabled(bool(self.plan.get("ready")) and not active)
        if not active and self.timer.isActive():
            self.timer.stop()
        if not active and self.on_done is not None and not status.get("_reported"):
            status["_reported"] = True
            self.on_done(status)

    def _show_cancel(self, state: str) -> None:
        """The button at the bottom: cancel while it runs, done when it succeeded."""
        if state == "succeeded":
            self.cancel_button.setText("Installation complete")
            self.cancel_button.setStyleSheet(DONE_BUTTON_STYLE)
            self.cancel_button.setToolTip("Close this window")
            self.cancel_button.setEnabled(True)
            return
        self.cancel_button.setText("Cancel the installation")
        self.cancel_button.setStyleSheet("")
        self.cancel_button.setToolTip("")
        self.cancel_button.setEnabled(state in ACTIVE)

    def _result_text(self, status: dict) -> str:
        outputs = status.get("outputs") or {}
        if status.get("bound"):
            where = f"It is entered in the profile {status['bound']}"
        elif outputs:
            where = "It was not entered in the profile"
        else:
            return "Installed."
        entries = ", ".join(f"{key} = {value}" for key, value in outputs.items())
        return f"Installed. {where}: {entries}."

    def _set_log(self, text: str) -> None:
        bar = self.log_view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 2
        self.log_view.setPlainText(text)
        if at_end:
            bar.setValue(bar.maximum())

    def _cancel_or_close(self) -> None:
        if str(self.status.get("state") or "") == "succeeded":
            self.close()
        else:
            self.cancel_installation()

    def cancel_installation(self) -> None:
        answer = QMessageBox.question(
            self, self.windowTitle(),
            "Stop the installation? What is already downloaded and built stays, and "
            "a new installation into the same folder continues with it.")
        if answer != enum_value(QMessageBox, "StandardButton.Yes", "Yes"):
            return
        self.cancel_button.setEnabled(False)
        ident = self.install_id
        run_async("aXqua: stopping the installation",
                  lambda: self.client.install_cancel(ident),
                  on_success=self.show_status,
                  on_error=lambda exc: QMessageBox.warning(
                      self, self.windowTitle(), user_text(exc)), owner=self)

    def closeEvent(self, event) -> None:         # noqa: N802 - Qt's name
        self.timer.stop()
        self.fix_timer.stop()
        super().closeEvent(event)


_FIELD_ROLE = enum_value(QFormLayout, "ItemRole.FieldRole", "FieldRole")


def _note(text: str) -> QLabel:
    note = QLabel(text)
    note.setWordWrap(True)
    note.setEnabled(False)
    return note


def _scrolling(body: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(body)
    return area


def _fixed_font():
    from qgis.PyQt.QtGui import QFontDatabase
    return QFontDatabase.systemFont(enum_value(QFontDatabase, "SystemFont.FixedFont",
                                               "FixedFont"))


def _selectable():
    from qgis.PyQt.QtCore import Qt
    return enum_value(Qt, "TextInteractionFlag.TextSelectableByMouse",
                      "TextSelectableByMouse")
