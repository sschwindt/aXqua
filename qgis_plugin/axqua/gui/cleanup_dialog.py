"""Clean Up: remove the files of a case that nothing needs any more, to free disk space.

*Clean Up* sits left of *Help* above the tabs (the user's order of 2026-10-18). The window lists,
for one case at a time, what ``axqua cleanup <case> --json`` offers, by category and with sizes:
the working folders of interrupted TELEMAC runs, the listings of the single processors, the
processor folders of reconstructed OpenFOAM runs, exports for ParaView and VisIt, and the time
steps of results, of which the last is always kept. The categories that are safe without a second
thought are ticked; the time steps are not, because an animation of a run loses them.

Nothing is decided here: what may go is the answer of the aXqua program, which never lists the
inputs of a case, the case file, the built model or the last time step of a result, removes only
what a new listing still offers, and refuses while a job of the case is waiting or running.
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
                                 QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem,
                                 QVBoxLayout)

from ..compat import CHECKED, UNCHECKED, USER_ROLE, enum_value
from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import help as help_pages

HELP_KEY = "cleanup"

INTRODUCTION = (
    "Remove files of a case that nothing needs any more, to free disk space. The input files, "
    "the case file, the built model and the last time step of every result are never listed. "
    "Removed files cannot be restored.")

#: What a result keeps of its time steps: (label, value of axqua cleanup --keep)
KEEP = [("The last time step", "last"),
        ("Every 10th time step and the last", "every:10"),
        ("Every 5th time step and the last", "every:5")]


def human(n: float) -> str:
    """Bytes for a person to read (1.2 GB)."""
    for unit in ("B", "kB", "MB", "GB"):
        if abs(n) < 1000:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def items(n: int) -> str:
    return f"{n} item" if n == 1 else f"{n} items"


def kept(frames: int, keep: str) -> int:
    """How many time steps a result of *frames* keeps."""
    if frames <= 0:
        return 0
    if keep == "last":
        return 1
    step = max(1, int(keep.split(":", 1)[1]))
    return len(set(range(0, frames, step)) | {frames - 1})


def saving(item: dict, keep: str) -> int:
    """What removing *item* frees, for the time steps a result keeps."""
    if item.get("action") != "thin":
        return int(item.get("saves", 0))
    frames = int(item.get("frames") or 0)
    left = int(item.get("header_bytes", 0)) + kept(frames, keep) * int(item.get("frame_bytes", 0))
    return max(0, int(item.get("bytes", 0)) - left)


def case_label(path: Path) -> str:
    return f"{path.name}  ({path.parent})"


class CleanupDialog(QDialog):
    """Choose a case, tick what may go, and remove it."""

    def __init__(self, client, cases: list[Path], *, active: Path | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.found: dict = {}
        self.pending = ""          # what the last removal did, shown above the new listing
        self.setWindowTitle("aXqua - Clean Up")
        self.resize(860, 560)
        self._checkable = enum_value(_qt(), "ItemFlag.ItemIsUserCheckable", "ItemIsUserCheckable")
        self._tristate = enum_value(_qt(), "ItemFlag.ItemIsAutoTristate", "ItemIsAutoTristate",
                                    default=None) or enum_value(_qt(), "ItemFlag.ItemIsTristate",
                                                                "ItemIsTristate")

        layout = QVBoxLayout(self)
        introduction = QLabel(INTRODUCTION)
        introduction.setWordWrap(True)
        layout.addWidget(introduction)

        row = QHBoxLayout()
        row.addWidget(QLabel("Case:"))
        self.case = QComboBox()
        for path in cases:
            self.case.addItem(case_label(path), str(path))
        if active is not None:
            index = self.case.findData(str(active))
            if index >= 0:
                self.case.setCurrentIndex(index)
        self.case.currentIndexChanged.connect(lambda _index: self.load())
        row.addWidget(self.case, 1)
        layout.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["What", "Frees", "Detail"])
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, enum_value(QHeaderView, "ResizeMode.Stretch", "Stretch"))
        for column in (1, 2):
            header.setSectionResizeMode(column, enum_value(QHeaderView,
                                                           "ResizeMode.ResizeToContents",
                                                           "ResizeToContents"))
        # the end of a path is what tells two rows apart
        self.tree.setTextElideMode(enum_value(_qt(), "TextElideMode.ElideMiddle", "ElideMiddle"))
        self.tree.itemChanged.connect(lambda _item, _column: self.update_total())
        layout.addWidget(self.tree, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel("A result whose time steps are removed keeps:"))
        self.keep = QComboBox()
        for label, value in KEEP:
            self.keep.addItem(label, value)
        self.keep.currentIndexChanged.connect(lambda _index: self.update_total())
        row.addWidget(self.keep)
        row.addStretch(1)
        layout.addLayout(row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        help_button = QPushButton("Help")
        help_button.clicked.connect(lambda: help_pages.open_window_help(HELP_KEY))
        buttons.addWidget(help_button)
        buttons.addStretch(1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.load)
        buttons.addWidget(self.refresh_button)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self.remove)
        self.remove_button.setEnabled(False)
        buttons.addWidget(self.remove_button)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        if cases:
            self.load()
        else:
            self.status.setText("There is no case in the list. Add one on the tab Case Setup.")

    # -- what there is ------------------------------------------------------------
    def current_case(self) -> str:
        return self.case.currentData() or ""

    def load(self) -> None:
        case = self.current_case()
        if not case:
            return
        self.tree.clear()
        self.remove_button.setEnabled(False)
        self.status.setText("Looking for files that can go...")
        run_async("aXqua: clean up", lambda: self.client.cleanup_list(case),
                  on_success=self.show_found, on_error=self._failed, owner=self)

    def show_found(self, found: dict) -> None:
        self.found = found or {}
        self.tree.blockSignals(True)
        self.tree.clear()
        for category in self.found.get("categories") or []:
            top = QTreeWidgetItem([category.get("title", ""), "", ""])
            top.setToolTip(0, category.get("explanation", ""))
            top.setFlags(top.flags() | self._checkable | self._tristate)
            top.setData(0, USER_ROLE, None)
            for item in category.get("items") or []:
                rel = item.get("id", "").split(":", 1)[-1]
                child = QTreeWidgetItem([rel, "", item.get("detail", "")])
                child.setToolTip(0, item.get("path", ""))
                child.setFlags(child.flags() | self._checkable)
                child.setData(0, USER_ROLE, item)
                child.setCheckState(0, CHECKED if category.get("default") else UNCHECKED)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setExpanded(len(category.get("items") or []) <= 6)
        self.tree.blockSignals(False)
        self.update_total()
        if self.pending:
            self.status.setText(f"{self.pending} {self.status.text()}")
            self.pending = ""

    def chosen(self) -> list[dict]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if child.checkState(0) == CHECKED:
                    out.append(child.data(0, USER_ROLE))
        return out

    def update_total(self) -> None:
        keep = self.keep.currentData() or "last"
        self.tree.blockSignals(True)
        total = 0
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            subtotal = 0
            for j in range(top.childCount()):
                child = top.child(j)
                frees = saving(child.data(0, USER_ROLE), keep)
                child.setText(1, human(frees))
                subtotal += frees
            top.setText(1, human(subtotal))
            total += subtotal
        self.tree.blockSignals(False)
        selected = self.chosen()
        frees = sum(saving(item, keep) for item in selected)
        blocked = self.found.get("blocked") or ""
        if blocked:
            self.status.setText(blocked)
        elif not self.found.get("categories"):
            self.status.setText("Nothing of this case can go.")
        else:
            self.status.setText(f"Selected: {items(len(selected))}, {human(frees)} "
                                f"(of {human(total)} that can go).")
        self.remove_button.setEnabled(bool(selected) and not blocked)

    # -- removing -----------------------------------------------------------------
    def remove(self) -> None:
        selected = self.chosen()
        if not selected:
            return
        keep = self.keep.currentData() or "last"
        frees = sum(saving(item, keep) for item in selected)
        name = Path(self.current_case()).name
        answer = QMessageBox.question(
            self, "aXqua - Clean Up",
            f"Remove {items(len(selected))} of the case {name} and free {human(frees)}?\n\n"
            "Removed files cannot be restored.")
        if answer != enum_value(QMessageBox, "StandardButton.Yes", "Yes"):
            return
        self.remove_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.status.setText("Removing... Results whose time steps are removed are rewritten, "
                            "which takes a while for large files.")
        case = self.current_case()
        ids = [item["id"] for item in selected]
        run_async("aXqua: clean up", lambda: self.client.cleanup_apply(case, ids, keep=keep),
                  on_success=self._removed, on_error=self._failed, owner=self)

    def _removed(self, done: dict) -> None:
        done = done or {}
        self.refresh_button.setEnabled(True)
        message = (f"Removed {items(len(done.get('removed') or []))} and freed "
                   f"{human(done.get('freed_bytes', 0))}.")
        failed = done.get("failed") or []
        if failed:
            message += " Not removed: " + "; ".join(f"{f['id']}: {f['error']}" for f in failed)
        self.pending = message
        self.load()

    def _failed(self, exc: Exception) -> None:
        self.refresh_button.setEnabled(True)
        self.status.setText(user_text(exc))


def _qt():
    from qgis.PyQt.QtCore import Qt
    return Qt


__all__ = ["CleanupDialog", "KEEP", "human", "kept", "saving"]
