"""The *Setup Wizard* of TELEMAC and of OpenFOAM.

The first tab asks how aXqua is to find the program:

* **Select existing config file**: the program is installed on this computer already. The tab *Existing installation* says which file that is (the environment script of the installation, with the folder and file-name pattern of each program), the user selects it, and ``axqua profile inspect`` checks whether the installation is usable for aXqua. An installation that runs the program but lacks a part of it is valid; the answer names the aXqua modules that will not work (GAIA, TELEMAC-3D, sediDriftFoam2, ...). *Use this installation* enters it in the profile (``axqua profile use``).
* **Install TELEMAC/OpenFOAM**: the installation wizard (:mod:`.install_wizard`) takes over, as the button of the Configuration tab used to open it.

The window decides nothing about an installation: what is usable is the answer of ``axqua profile inspect``, the same in a terminal.
"""

from __future__ import annotations

import html
import os

from qgis.PyQt.QtWidgets import (QButtonGroup, QDialog, QFileDialog, QFormLayout,
                                 QHBoxLayout, QLabel, QLineEdit, QPushButton,
                                 QRadioButton, QScrollArea, QTabWidget, QVBoxLayout,
                                 QWidget)

from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import findings as fnd
from . import help as help_pages
from .style import AXQUA_PURPLE, DONE_BUTTON_STYLE, FOUND_GREEN

#: The programs that have a Setup Wizard, with the name the window shows.
NAMES = {"telemac": "TELEMAC", "openfoam": "OpenFOAM"}

TABS = ("Setup", "Existing installation")

#: The two answers of the first tab.
EXISTING = "existing"
INSTALL = "install"

EXISTING_LABEL = "Select existing config file"

#: What the first tab says about each answer.
EXISTING_HINT = ("{name} is installed on this computer already. Select the file that "
                 "sets up its environment; aXqua checks whether the installation is "
                 "usable and enters it in the profile of this computer.")
INSTALL_HINT = ("aXqua installs {name} with its installation wizard, which ends by "
                "entering the installation in the profile of this computer.")

#: Which file to select, per program: what the tab *Existing installation* explains.
PATTERNS = {
    "telemac": (
        "Select the <b>environment script of TELEMAC</b>. It is in the folder "
        "<code>configs</code> of the TELEMAC installation, and its name starts with "
        "<code>pysource.</code> and ends with <code>.sh</code>:"
        "<pre>&lt;installation folder&gt;/telemac-mascaret/configs/pysource.&lt;configuration&gt;.sh</pre>"
        "for example <code>/opt/telemac/telemac-mascaret/configs/pysource.debian12.sh</code>. "
        "A TELEMAC installation has one such script per configuration of its build; "
        "select the one of the build you use. A file whose name contains "
        "<code>template</code> is an example and sets up nothing."),
    "openfoam": (
        "Select the <b>environment script of OpenFOAM</b>, one of:"
        "<ul><li><code>&lt;installation folder&gt;/shell-rc.sh</code> of the OpenFOAM "
        "installation of aXqua (recommended), for example "
        "<code>~/.local/openfoam-sediment-v2406/shell-rc.sh</code>. It sets up "
        "OpenFOAM v2406 with Olsen's sediment solver sediDriftFoam2 and the "
        "stage-discharge outflow boundary of the BAW.</li>"
        "<li><code>&lt;OpenFOAM folder&gt;/etc/bashrc</code> of an OpenFOAM v2406, a "
        "file without an ending, for example "
        "<code>/usr/lib/openfoam/openfoam2406/etc/bashrc</code>.</li></ul>"
        "An OpenFOAM without the sediment solvers runs the free-surface simulations; "
        "the check says which aXqua modules do not work with it."),
}

#: On Windows, TELEMAC and OpenFOAM are installed inside WSL (the user's decision of 2026-11-06), and the file dialog of Windows reaches them below Linux.
WINDOWS_NOTE = ("<p>On Windows, the installation is inside the WSL distribution: in the file dialog, "
                "open <b>Linux</b> (or type <code>\\\\wsl.localhost</code>), then the distribution, for "
                "example <code>Ubuntu-24.04</code>, then the folders above. aXqua enters the script "
                "with its distribution.</p>")
#: TELEMAC built on Windows itself (no WSL; the Windows test computer, 2026-11-09) is entered by a batch file.
WINDOWS_TELEMAC_NOTE = ("<p>A TELEMAC built on Windows itself, without WSL, is entered by its batch file "
                        "<code>configs\\pysource.&lt;configuration&gt;.bat</code>.</p>")
if os.name == "nt":
    PATTERNS = {key: text + WINDOWS_NOTE + (WINDOWS_TELEMAC_NOTE if key == "telemac" else "")
                for key, text in PATTERNS.items()}

#: The filters of the file dialog, per program.
FILTERS = {
    "telemac": ("TELEMAC environment scripts (pysource.*.sh pysource.*.bat);;Shell scripts (*.sh);;"
                "Batch files (*.bat);;All files (*)") if os.name == "nt" else
               "TELEMAC environment scripts (pysource.*.sh);;Shell scripts (*.sh);;All files (*)",
    "openfoam": "OpenFOAM environment scripts (shell-rc.sh bashrc);;All files (*)",
}

NOT_CHECKED = "Select the file and click Check."


class SetupWizard(QDialog):
    """How aXqua finds TELEMAC or OpenFOAM: an existing installation, or installing it.

    *found* is a path aXqua knows of already (from ``axqua install overview``), which fills the file field. *on_entered* is called with the answer of ``profile use`` once an installation is entered in the profile. After the window is closed, :attr:`choice` is ``"install"`` when the user went on to the installation.
    """

    def __init__(self, client, target: str, *, found: str = "", parent=None,
                 on_entered=None) -> None:
        super().__init__(parent)
        self.client = client
        self.target = target
        self.name = NAMES[target]
        self.on_entered = on_entered
        self.choice = ""                      # "install" when the installation follows
        self.answer: dict | None = None       # the last answer of profile inspect
        self._answered_for = ""               # the file that answer is about
        self.entered: dict | None = None
        self.setWindowTitle(f"Setup Wizard: {self.name}")
        self.resize(760, 640)
        self._build(found)

    # -- construction -------------------------------------------------------------
    def _build(self, found: str) -> None:
        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._setup_page(found), TABS[0])
        self.tabs.addTab(_scrolling(self._existing_page(found)), TABS[1])
        self.tabs.currentChanged.connect(lambda _=0: self._update_buttons())
        outer.addWidget(self.tabs, 1)
        buttons = QHBoxLayout()
        self.help_button = QPushButton("Help")
        self.help_button.clicked.connect(self.open_help)
        buttons.addWidget(self.help_button)
        buttons.addStretch(1)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        buttons.addWidget(self.back_button)
        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(self.go_next)
        buttons.addWidget(self.next_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.close_button)
        outer.addLayout(buttons)
        self._update_buttons()

    def _setup_page(self, found: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(f"How should aXqua find {self.name}?"))
        self.group = QButtonGroup(self)
        self.existing_button = QRadioButton(EXISTING_LABEL)
        self.install_button = QRadioButton(f"Install {self.name}")
        for button, hint in ((self.existing_button, EXISTING_HINT),
                             (self.install_button, INSTALL_HINT)):
            self.group.addButton(button)
            layout.addWidget(button)
            note = _label(hint.format(name=self.name))
            note.setEnabled(False)
            note.setContentsMargins(24, 0, 0, 8)
            layout.addWidget(note)
        if found:
            seen = _label(f"aXqua found {self.name} on this computer: {found}")
            seen.setStyleSheet(f"color: {FOUND_GREEN};")
            layout.addWidget(seen)
        # what is found is most likely what is meant; otherwise it has to be installed
        (self.existing_button if found else self.install_button).setChecked(True)
        self.group.buttonToggled.connect(lambda *_: self._update_buttons())
        layout.addStretch(1)
        return page

    def _existing_page(self, found: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.pattern_label = _label(PATTERNS[self.target], rich=True)
        layout.addWidget(self.pattern_label)
        form = QFormLayout()
        row = QHBoxLayout()
        self.script_edit = QLineEdit(found)
        self.script_edit.setPlaceholderText("the environment script of the installation")
        self.script_edit.textChanged.connect(lambda _=None: self._file_changed())
        row.addWidget(self.script_edit, 1)
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.browse)
        row.addWidget(self.browse_button)
        form.addRow("Config file", _wrap(row))
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.check_button = QPushButton("Check")
        self.check_button.setToolTip("Load the environment script once and look for "
                                     "every part of the installation aXqua uses")
        self.check_button.clicked.connect(self.check)
        actions.addWidget(self.check_button)
        self.use_button = QPushButton("Use this installation")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self.use)
        actions.addWidget(self.use_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.verdict_label = _label(NOT_CHECKED)
        layout.addWidget(self.verdict_label)
        self.modules_label = _label("", rich=True)
        layout.addWidget(self.modules_label)
        self.finding_list = fnd.FindingList("")
        layout.addWidget(self.finding_list)
        layout.addStretch(1)
        return page

    # -- the tabs -----------------------------------------------------------------
    def selected(self) -> str:
        return EXISTING if self.existing_button.isChecked() else INSTALL

    def _update_buttons(self) -> None:
        first = self.tabs.currentIndex() == 0
        self.back_button.setVisible(not first)
        self.next_button.setVisible(first)
        # the tab of an existing installation belongs to that answer only
        self.tabs.setTabEnabled(1, self.selected() == EXISTING)
        self.next_button.setText("Next" if self.selected() == EXISTING
                                 else f"Install {self.name}...")

    def go_next(self) -> None:
        if self.selected() == EXISTING:
            self.tabs.setCurrentIndex(1)
            return
        self.choice = INSTALL                 # the Configuration tab opens the installation
        self.accept()

    def open_help(self) -> None:
        help_pages.open_window_help(f"setup-{self.target}")

    # -- the existing installation ------------------------------------------------
    def browse(self) -> None:
        start = self.script_edit.text().strip() or ("\\\\wsl.localhost" if os.name == "nt" else "")
        path, _ = QFileDialog.getOpenFileName(self, f"Environment script of {self.name}",
                                              start, FILTERS[self.target])
        if path:
            self.script_edit.setText(path)
            self.check()

    def _file_changed(self) -> None:
        current = self.script_edit.text().strip()
        if current != self._answered_for:
            self.use_button.setEnabled(False)
            if self.answer is not None:
                self.verdict_label.setText(NOT_CHECKED)
                self.verdict_label.setStyleSheet("")
                self.modules_label.setText("")
                self.finding_list.set_findings([])

    def check(self) -> None:
        script = self.script_edit.text().strip()
        if not script:
            self.verdict_label.setText(NOT_CHECKED)
            return
        self.check_button.setEnabled(False)
        self.use_button.setEnabled(False)
        self.verdict_label.setText(f"Checking {self.name}: aXqua loads the environment "
                                   "script once, which takes a few seconds.")
        self.verdict_label.setStyleSheet("")
        target = self.target
        run_async(f"aXqua: checking the installation of {self.name}",
                  lambda: self.client.profile_inspect(target, script),
                  on_success=lambda answer: self.show_answer(answer, script),
                  on_error=lambda exc: self._failed(user_text(exc)), owner=self)

    def _failed(self, text: str) -> None:
        self.check_button.setEnabled(True)
        self.verdict_label.setText("The check could not be made: " + text)
        self.verdict_label.setStyleSheet(f"color: {AXQUA_PURPLE}; font-weight: bold;")

    def show_answer(self, answer: dict, script: str) -> None:
        """What ``profile inspect`` found. An answer about a file that is no longer the one in the field is shown, but cannot be used."""
        self.check_button.setEnabled(True)
        self.answer = answer or {}
        self._answered_for = script
        valid = bool(self.answer.get("valid"))
        complete = valid and not any(
            m.get("available") is False for m in self.answer.get("modules") or [])
        self.verdict_label.setText(str(self.answer.get("verdict") or ""))
        self.verdict_label.setStyleSheet(
            f"color: {FOUND_GREEN};" if complete else
            f"color: {AXQUA_PURPLE}; font-weight: bold;")
        self.modules_label.setText(modules_html(self.answer.get("modules") or []))
        self.finding_list.set_findings(fnd.from_payload(self.answer), checked=True)
        self.use_button.setEnabled(valid and script == self.script_edit.text().strip())

    def use(self) -> None:
        script = self._answered_for
        self.use_button.setEnabled(False)
        target = self.target
        run_async(f"aXqua: entering {self.name} in the profile",
                  lambda: self.client.profile_use(target, script),
                  on_success=self._entered,
                  on_error=lambda exc: self._failed(user_text(exc)), owner=self)

    def _entered(self, answer: dict) -> None:
        answer = answer or {}
        if not answer.get("entered"):
            self.show_answer(answer, self._answered_for)
            self.use_button.setEnabled(False)
            return
        self.entered = answer
        self.verdict_label.setText(f"{self.name} is entered in the profile "
                                   f"{answer.get('path', '')}. "
                                   + str(answer.get("verdict") or ""))
        self.close_button.setText("Setup complete")
        self.close_button.setStyleSheet(DONE_BUTTON_STYLE)
        if self.on_entered is not None:
            self.on_entered(answer)


def modules_html(modules: list[dict]) -> str:
    """The parts of an installation as a table: found green, missing aXqua purple."""
    if not modules:
        return ""
    rows = []
    for module in modules:
        available = module.get("available")
        if available is True:
            state = f'<span style="color:{FOUND_GREEN};">found</span>'
        elif available is False:
            state = (f'<span style="color:{AXQUA_PURPLE}; font-weight:bold;">'
                     + ("missing (required)" if module.get("required") else "missing")
                     + "</span>")
        else:
            state = "not checked"
        rows.append(f"<tr><td>{html.escape(str(module.get('title', '')))}</td>"
                    f"<td>&nbsp;{state}&nbsp;</td>"
                    f"<td>{html.escape(str(module.get('needed_for', '')))}</td></tr>")
    return ("<table><tr><th align='left'>Part</th><th align='left'>&nbsp;State&nbsp;"
            "</th><th align='left'>Needed for</th></tr>" + "".join(rows) + "</table>")


def _label(text: str, *, rich: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    if rich:
        from qgis.PyQt.QtCore import Qt

        from ..compat import enum_value
        label.setTextFormat(enum_value(Qt, "TextFormat.RichText", "RichText"))
    return label


def _wrap(layout) -> QWidget:
    widget = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    widget.setLayout(layout)
    return widget


def _scrolling(body: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(body)
    return area
