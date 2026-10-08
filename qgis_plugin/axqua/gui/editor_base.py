"""What the two editors have in common: the three buttons, and never losing work.

The profile editor and the case editor are windows of their own with **Save**, **Cancel**
and **Exit** at the bottom right:

* *Save* writes the file and then checks it. The window stays open and shows what the
  check found.
* *Cancel* discards what was changed since the last save and closes the window.
* *Exit* closes the window, and asks first when there are changes that were not saved.

A check never prevents saving: a file is normally incomplete while it is filled in.
"""

from __future__ import annotations

from qgis.PyQt.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                                 QVBoxLayout)

from ..compat import enum_value, exec_dialog
from . import findings as fnd


class EditorDialog(QDialog):
    """A window that edits one file. Subclasses provide the form, ``dirty`` and ``save``."""

    #: what the file is called in the question Exit asks: "The profile has changes ..."
    what = "file"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.saved_once = False
        self.outer = QVBoxLayout(self)
        self.header = QLabel("")
        self.header.setWordWrap(True)
        self.outer.addWidget(self.header)

    def finish_layout(self, check_note: str) -> None:
        """Add the status line, the list of findings and the three buttons."""
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.outer.addWidget(self.status)
        self.finding_list = fnd.FindingList(check_note)
        self.outer.addWidget(self.finding_list)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.save_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Discard the changes since the last save and close")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.exit_button = QPushButton("Exit")
        self.exit_button.setToolTip("Close; asks first when changes are not saved")
        self.exit_button.clicked.connect(self.exit)
        buttons.addWidget(self.exit_button)
        self.outer.addLayout(buttons)

    def set_header(self, text: str) -> None:
        self.header.setText(text)
        self.header.setVisible(bool(text))

    # -- for the subclass ---------------------------------------------------------
    @property
    def dirty(self) -> bool:                     # pragma: no cover - abstract
        raise NotImplementedError

    def save(self) -> None:                      # pragma: no cover - abstract
        raise NotImplementedError

    # -- Exit ---------------------------------------------------------------------
    def exit(self) -> None:
        """Close. Changes that were not saved are offered for saving first."""
        if not self.dirty:
            self.accept()
            return
        box = QMessageBox(self)
        box.setWindowTitle(self.windowTitle())
        box.setText(f"The {self.what} has changes that are not saved.")
        save = box.addButton("Save", enum_value(QMessageBox, "ButtonRole.AcceptRole",
                                                "AcceptRole"))
        discard = box.addButton("Close without saving",
                                enum_value(QMessageBox, "ButtonRole.DestructiveRole",
                                           "DestructiveRole"))
        box.addButton("Keep editing", enum_value(QMessageBox, "ButtonRole.RejectRole",
                                                 "RejectRole"))
        exec_dialog(box)
        if box.clickedButton() is save:
            self.save()                    # stays open, so that the check can be read
        elif box.clickedButton() is discard:
            self.accept()
