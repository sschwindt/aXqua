"""The installation wizards: TELEMAC, OpenFOAM, and the postprocessors.

One window with three pages, the same for each program:

1. **Settings.** Where to install, and the few choices the installer offers. Every path
   has a dialog; nothing has to be typed.
2. **Check.** What ``axqua install plan`` answers: the commands that will run, how long
   it takes, and what is not in order. System packages are the one thing that needs an
   administrator: the page shows how many are missing and the one command that
   installs them, which can be copied or, on a desktop, run from here. The password is
   then asked by the dialog of the desktop and never passes through this plugin.
3. **Installation.** The state, the step and the end of the log, refreshed every two
   seconds. The installation is a process of its own: this window and QGIS can be
   closed, and the wizard shows the running installation again when it is reopened.

The wizard decides nothing about the installation itself. What is installed, from
where and with which commands is the answer of ``axqua install``, and the same command
does the same in a terminal.
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                                 QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                                 QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                                 QPushButton, QSpinBox, QStackedWidget, QVBoxLayout,
                                 QWidget)

from ..compat import enum_value
from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import findings as fnd
from . import help as help_pages

#: ``(option, label, kind, hint)`` per program. Kinds: folder, file, text, int, bool,
#: choice (one of CHOICES), openfoam (an installed OpenFOAM, or compiling one).
FIELDS = {
    "telemac": (
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
         "Optional: a downloaded SALOME archive. aXqua does not need SALOME."),
    ),
    "openfoam": (
        ("folder", "Installation folder", "folder",
         "A folder that does not exist yet. The sediment solvers, the outflow "
         "boundary and VisIt are installed in it."),
        ("reuse_openfoam", "OpenFOAM v2406", "openfoam", ""),
        ("jobs", "Processes for compiling", "int", ""),
        ("visualization", "Install ParaView and VisIt", "bool", ""),
        ("examples", "Download the example case of the sediment solver", "bool", ""),
        ("smoke_test", "Run a short test at the end", "bool", ""),
    ),
    "postprocessors": (
        ("folder", "Installation folder", "folder",
         "VisIt is installed in this folder. ParaView is a package of the operating "
         "system."),
    ),
}

#: The switches that are on unless the user turns them off.
ON_BY_DEFAULT = ("visualization", "smoke_test", "bind")

#: ``option -> ((value, text), ...)`` of the settings with a fixed set of answers. The
#: first entry is the default.
CHOICES = {
    "telemac_examples": (
        ("inputs", "Input files only (about 360 files, 460 MB)"),
        ("all", "Everything, with reference results and manuals (1,500 files, 1.65 GB)"),
        ("none", "Steering files only"),
    ),
}

BASES = (("", "as detected"), ("debian12", "Debian 12"), ("ubuntu22", "Ubuntu 22.04"),
         ("ubuntu24", "Ubuntu 24.04"))

USE_INSTALLED = "Use the OpenFOAM v2406 that is installed on this computer"
COMPILE = "Compile OpenFOAM v2406 from its source code (several hours)"

KEEPS_RUNNING = ("The installation runs by itself. This window and QGIS can be closed; "
                 "open the wizard again to see how far it is.")

STATES = {"queued": "starting", "running": "running", "succeeded": "finished",
          "failed": "failed", "cancelled": "cancelled"}
ACTIVE = ("queued", "running")

POLL_MS = 2000
LOG_LINES = 200


class InstallWizard(QDialog):
    """Install one program. *overview* is the answer of ``axqua install overview``."""

    def __init__(self, client, target: str, overview: dict | None = None, *,
                 parent=None, on_done=None) -> None:
        super().__init__(parent)
        self.client = client
        self.target = target
        self.overview = overview or {}
        self.on_done = on_done
        self.entry = next((t for t in self.overview.get("targets") or []
                           if t.get("target") == target), {})
        self.plan: dict | None = None
        self.status: dict = {}
        self.install_id = ""
        self._asking = False
        self._widgets: dict[str, QWidget] = {}
        self._triangles: dict[str, fnd.TriangleButton] = {}
        self.setWindowTitle("Install " + str(self.entry.get("title") or target))
        self.resize(760, 620)
        self._build()
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.poll)
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
        self.step_label = QLabel("")
        outer.addWidget(self.step_label)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._settings_page())
        self.pages.addWidget(self._check_page())
        self.pages.addWidget(self._run_page())
        outer.addWidget(self.pages, 1)

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
        self.show_page(0)

    def _settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        for key, label, kind, hint in FIELDS[self.target]:
            form.addRow("" if kind == "bool" else label, self._row(key, label, kind))
            if hint:
                note = QLabel(hint)
                note.setWordWrap(True)
                note.setEnabled(False)
                form.addRow("", note)
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
        note = QLabel("Only for a system that is built on one of these and is not "
                      "recognized by itself.")
        note.setWordWrap(True)
        note.setEnabled(False)
        more.addRow("", note)
        more.addRow("Installer scripts", self._row("installers", "", "folder"))
        note = QLabel("Optional: a folder with a copy of the installer scripts, for a "
                      "computer without a connection to the internet.")
        note.setWordWrap(True)
        note.setEnabled(False)
        more.addRow("", note)
        self._toggle_advanced(False)
        self.advanced.toggled.connect(self._toggle_advanced)
        layout.addWidget(self.advanced)
        layout.addStretch(1)
        self._fill_defaults()
        return page

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

    def _fill_defaults(self) -> None:
        folder = self._widgets.get("folder")
        if folder is not None:
            folder.setText(str(self.entry.get("default_folder") or ""))
        if "jobs" in self._widgets:
            self._widgets["jobs"].setValue(int(self.overview.get("default_jobs") or 1))
        if "reuse_openfoam" in self._widgets:
            found = str(self.overview.get("openfoam_found") or "")
            self._widgets["openfoam_path"].setText(found)
            # without an OpenFOAM to build on, compiling is what will happen
            self._widgets["reuse_openfoam"].setCurrentIndex(0 if found else 1)

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
        self.commands.setMaximumHeight(120)
        layout.addWidget(self.commands)

        self.package_box = QGroupBox("System packages")
        package_layout = QVBoxLayout(self.package_box)
        self.package_label = QLabel("")
        self.package_label.setWordWrap(True)
        package_layout.addWidget(self.package_label)
        self.package_command = QPlainTextEdit()
        self.package_command.setReadOnly(True)
        self.package_command.setFont(_fixed_font())
        self.package_command.setMaximumHeight(70)
        package_layout.addWidget(self.package_command)
        row = QHBoxLayout()
        self.copy_button = QPushButton("Copy the command")
        self.copy_button.clicked.connect(self.copy_command)
        row.addWidget(self.copy_button)
        self.elevate_button = QPushButton("Install the packages...")
        self.elevate_button.setToolTip("The desktop asks for the password of an "
                                       "administrator")
        self.elevate_button.clicked.connect(self.install_packages)
        row.addWidget(self.elevate_button)
        row.addStretch(1)
        self.recheck_button = QPushButton("Check again")
        self.recheck_button.clicked.connect(self.load_plan)
        row.addWidget(self.recheck_button)
        package_layout.addLayout(row)
        layout.addWidget(self.package_box)

        self.finding_list = fnd.FindingList("")
        layout.addWidget(self.finding_list)
        layout.addStretch(1)
        return page

    def _run_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.state_label = QLabel("")
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
        self.cancel_button = QPushButton("Cancel the installation")
        self.cancel_button.clicked.connect(self.cancel_installation)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        return page

    # -- pages --------------------------------------------------------------------
    def show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        names = ("1 of 3: Settings", "2 of 3: Check", "3 of 3: Installation")
        self.step_label.setText(f"<b>{names[index]}</b>")
        self.back_button.setVisible(index == 1)
        self.next_button.setVisible(index < 2)
        self.next_button.setText("Install" if index == 1 else "Next")
        if index == 0:
            self.next_button.setEnabled(True)

    def go_back(self) -> None:
        self.show_page(0)

    def go_next(self) -> None:
        if self.pages.currentIndex() == 0:
            self.show_page(1)
            self.load_plan()
        elif self.pages.currentIndex() == 1:
            self.start()

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
        self.next_button.setEnabled(False)
        self.recheck_button.setEnabled(False)
        options = self.options()
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
            self.next_button.setEnabled(False)
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
        missing = list(packages.get("missing") or [])
        needed = list(packages.get("needed") or [])
        self.package_box.setVisible(bool(needed))
        if missing:
            self.package_label.setText(
                f"{len(missing)} of {len(needed)} system packages are not installed. "
                + (str(packages.get("note")) + " " if packages.get("note") else "")
                + "Installing them needs the rights of an administrator. This is the "
                "command:")
        else:
            self.package_label.setText(f"All {len(needed)} system packages that the "
                                       "installation needs are installed.")
        self.package_command.setPlainText(str(packages.get("command") or ""))
        self.package_command.setVisible(bool(missing))
        self.copy_button.setVisible(bool(missing))
        self.elevate_button.setVisible(bool(missing))
        self.elevate_button.setEnabled(packages.get("elevation") == "pkexec")
        if missing and packages.get("elevation") != "pkexec":
            self.elevate_button.setToolTip(
                "This session cannot open the password dialog of the desktop. Copy "
                "the command and run it in a terminal.")

        found = fnd.from_payload(plan)
        self.finding_list.set_findings(found, checked=True)
        prefix = f"install.{self.target}."
        for key, triangle in self._triangles.items():
            triangle.set_findings(fnd.for_subject(found, prefix + key))
        self.next_button.setEnabled(bool(plan.get("ready")))
        self.next_button.setToolTip(
            "" if plan.get("ready") else
            "The installation cannot be started as it is. The list says why.")

    def copy_command(self) -> None:
        QApplication.clipboard().setText(self.package_command.toPlainText())
        self.package_label.setText(
            "The command is in the clipboard. Paste it into a terminal, wait until it "
            "has finished, and click Check again.")

    def install_packages(self) -> None:
        """Let the desktop ask for the password and install the missing packages."""
        self.elevate_button.setEnabled(False)
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
        self.load_plan()
        if message:
            self.plan_label.setText(message[:1].upper() + message[1:] + ".")

    # -- the installation ---------------------------------------------------------
    def start(self) -> None:
        self.next_button.setEnabled(False)
        options = self.options()
        run_async(f"aXqua: starting the installation of {self.target}",
                  lambda: self.client.install_start(self.target, options),
                  on_success=self.attach, on_error=self._start_failed, owner=self)

    def _start_failed(self, exc: Exception) -> None:
        self.next_button.setEnabled(True)
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
        self.cancel_button.setEnabled(active)
        self.keeps_label.setVisible(active)
        self.result_list.set_findings(fnd.from_payload(status), checked=not active)
        self.result_list.setVisible(not active)
        if not active and self.timer.isActive():
            self.timer.stop()
        if not active and self.on_done is not None and not status.get("_reported"):
            status["_reported"] = True
            self.on_done(status)

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
        super().closeEvent(event)


def _fixed_font():
    from qgis.PyQt.QtGui import QFontDatabase
    return QFontDatabase.systemFont(enum_value(QFontDatabase, "SystemFont.FixedFont",
                                               "FixedFont"))


def _selectable():
    from qgis.PyQt.QtCore import Qt
    return enum_value(Qt, "TextInteractionFlag.TextSelectableByMouse",
                      "TextSelectableByMouse")
