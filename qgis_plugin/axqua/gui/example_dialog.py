"""Getting an example case: a ready-made case with its data, to try aXqua with.

An example is a folder that works on its own - the case file, a guide and the input
data - and it is a few megabytes. The window shows which examples there are
(``axqua example list``), asks where the folder is to be created, and has aXqua put
it there (``axqua example get``). The case is then added to the list of cases, so the
next click is *Build* on the Preprocessing tab.

Nothing is decided here: which examples exist, how large they are and where they come
from is the answer of the aXqua program.
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
                                 QLineEdit, QPushButton, QVBoxLayout)

from ..compat import open_in_file_manager
from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import help as help_pages

HELP_KEY = "example-case"

INTRODUCTION = ("An example case is a complete case with its input data and a guide "
                "that walks through every step. It is placed in a folder of its own, "
                "which can be deleted at any time.")


def describe(example: dict) -> str:
    """What the window says about one example."""
    size = f"{example.get('megabytes', 0):g} MB in {example.get('files', 0)} files"
    return f"{example.get('summary', '')}\n\nDownload: {size}."


class ExampleDialog(QDialog):
    """Choose an example and a folder, and get the example into that folder."""

    def __init__(self, client, *, folder: str = "", parent=None, on_done=None) -> None:
        super().__init__(parent)
        self.client = client
        self.on_done = on_done              # called with the path of the case file
        self.examples: list[dict] = []
        self.done: dict | None = None       # what the last download answered
        self.setWindowTitle("aXqua - Example case")
        self.resize(700, 470)

        layout = QVBoxLayout(self)
        introduction = QLabel(INTRODUCTION)
        introduction.setWordWrap(True)
        layout.addWidget(introduction)

        self.choice = QComboBox()
        self.choice.currentIndexChanged.connect(self._show_example)
        layout.addWidget(self.choice)
        self.description = QLabel("")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)

        row = QHBoxLayout()
        row.addWidget(QLabel("Create the folder of the example in:"))
        self.folder = QLineEdit(folder or str(Path.home()))
        row.addWidget(self.folder, 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        layout.addLayout(row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        help_button = QPushButton("Help")
        help_button.clicked.connect(lambda: help_pages.open_window_help(HELP_KEY))
        buttons.addWidget(help_button)
        buttons.addStretch(1)
        self.folder_button = QPushButton("Open folder")
        self.folder_button.setToolTip("Open the folder of the example. Its guide is "
                                      "the file README.md.")
        self.folder_button.clicked.connect(self.open_folder)
        self.folder_button.setEnabled(False)
        buttons.addWidget(self.folder_button)
        self.get_button = QPushButton("Download")
        self.get_button.clicked.connect(self.get)
        self.get_button.setEnabled(False)
        buttons.addWidget(self.get_button)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self.load()

    # -- what there is ------------------------------------------------------------
    def load(self) -> None:
        self.status.setText("Reading the list of example cases...")
        run_async("aXqua: example cases", self.client.example_list,
                  on_success=self.show_examples, on_error=self._failed, owner=self)

    def show_examples(self, answer: dict) -> None:
        self.examples = list((answer or {}).get("examples") or [])
        self.choice.blockSignals(True)
        self.choice.clear()
        for example in self.examples:
            self.choice.addItem(example.get("title") or example.get("name", ""),
                                example.get("name", ""))
        self.choice.blockSignals(False)
        self.status.setText("" if self.examples else "No example case was found.")
        self.get_button.setEnabled(bool(self.examples))
        self._show_example()

    def _show_example(self, _index: int = 0) -> None:
        index = self.choice.currentIndex()
        self.description.setText(describe(self.examples[index])
                                 if 0 <= index < len(self.examples) else "")

    # -- getting one --------------------------------------------------------------
    def browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Folder in which the example is created", self.folder.text())
        if chosen:
            self.folder.setText(chosen)

    def get(self) -> None:
        name = self.choice.currentData()
        folder = self.folder.text().strip()
        if not name or not folder:
            self.status.setText("Choose a folder first.")
            return
        self.get_button.setEnabled(False)
        self.status.setText("Downloading the example...")
        run_async("aXqua: downloading the example",
                  lambda: self.client.example_get(name, folder),
                  on_success=self._fetched, on_error=self._failed, owner=self)

    def _fetched(self, answer: dict) -> None:
        self.done = answer or {}
        self.get_button.setEnabled(True)
        self.folder_button.setEnabled(bool(self.done.get("folder")))
        # the folder is the one named above; a long path here would not fit the window
        self.status.setText(
            "The example was downloaded and added to the list of cases. Its guide is "
            "the file README.md in its folder (Open folder). Continue with Build on "
            "the tab Preprocessing.")
        if self.on_done is not None and self.done.get("case"):
            self.on_done(Path(self.done["case"]))

    def _failed(self, exc: Exception) -> None:
        self.get_button.setEnabled(bool(self.examples))
        self.status.setText(user_text(exc))

    def open_folder(self) -> None:
        if self.done and self.done.get("folder"):
            open_in_file_manager(self.done["folder"])
