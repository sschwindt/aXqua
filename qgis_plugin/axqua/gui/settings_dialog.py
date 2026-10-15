"""Plugin settings: where axqua is.

Deliberately small. Everything about the *model* belongs in the case file and
everything about the *machine* belongs in axqua's own profile; duplicating either here
would create a second place to look when something disagrees. That leaves one setting
that is genuinely this installation's: which executable to talk to.
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog,
                                 QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                                 QPushButton, QVBoxLayout, QWidget)

from ..core import runner_client
from ..core.runner_client import RunnerClient, RunnerError

KEY_EXECUTABLE = runner_client.SETTINGS_KEY


def read_settings() -> dict:
    settings = QSettings()
    return {"executable": str(settings.value(KEY_EXECUTABLE, "") or "")}


def write_settings(values: dict) -> None:
    settings = QSettings()
    settings.setValue(KEY_EXECUTABLE, values.get("executable", ""))


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("aXqua settings")
        self.resize(620, 130)
        values = read_settings()

        layout = QVBoxLayout(self)
        form = QFormLayout()

        row = QHBoxLayout()
        self.executable = QLineEdit(values["executable"])
        self.executable.setPlaceholderText(
            "leave empty to use $AXQUA_EXE, then PATH")
        row.addWidget(self.executable, 1)
        browse = QPushButton("...")
        browse.setMaximumWidth(36)
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        test = QPushButton("Test")
        test.clicked.connect(self._test)
        row.addWidget(test)
        form.addRow("axqua executable", _wrap(row))

        self.result = QLabel("")
        self.result.setWordWrap(True)
        form.addRow("", self.result)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Where is axqua?")
        if path:
            self.executable.setText(path)

    def _test(self) -> None:
        """Validate now, so a wrong executable is never discovered at submit time.

        This one probe stays synchronous: the dialog is modal, the user pressed a button
        called *Test* and is waiting for its answer, and a background result arriving
        after they have closed the dialog would be worse. But it can take 30 s per
        candidate path, so the cursor says the application is busy rather than hung.
        """
        from qgis.PyQt.QtWidgets import QApplication

        from ..compat import WAIT_CURSOR
        self.result.setText("checking...")
        QApplication.setOverrideCursor(WAIT_CURSOR)
        QApplication.processEvents()          # paint "checking..." before we block
        try:
            info = RunnerClient(self.executable.text().strip() or None).validate()
        except RunnerError as exc:
            self.result.setText(exc.user_text())
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.result.setText(info.describe())

    def values(self) -> dict:
        return {"executable": self.executable.text().strip()}

    def accept(self) -> None:      # noqa: D102 - Qt override
        write_settings(self.values())
        super().accept()


def _wrap(layout) -> QWidget:
    widget = QWidget()
    widget.setLayout(layout)
    return widget
