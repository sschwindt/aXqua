"""Showing a job's log, with the two streams kept apart.

axqua writes two different things (plan §23) and conflating them in one window would
undo that: ``runner.log`` is axqua's narration - state transitions, the commands it
issued, exit codes - and is where a failure is explained; the solver's listing is the
solver talking, and is where a *numerical* problem is diagnosed. The toggle is the whole
interface.

Only the tail is read. A multi-day interFoam listing runs to hundreds of megabytes, and
loading one into a text widget would freeze QGIS - which is exactly the "do not load
complete result datasets into plugin memory" rule (plan §26) applied to a log file.

**Which** file that is, is axqua's answer rather than this module's guess: ``axqua logs
<id> --path`` is asked once, when the window opens or the toggle flips, and the follow
timer then only reads the bytes that have appeared since the last tick. Re-reading a
quarter of a megabyte every two seconds and resetting the document - which is what this
used to do - also threw away the user's selection and scroll position on every tick.
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                                 QPushButton, QVBoxLayout)

from ..compat import open_in_file_manager
from ..core.tasks import run_async

#: Read at most this much from the end of the file. Generous for a human, trivial for a
#: text widget.
TAIL_BYTES = 256 * 1024
FOLLOW_INTERVAL_MS = 2000


class LogDialog(QDialog):
    def __init__(self, job, context, parent=None) -> None:
        super().__init__(parent)
        self.job = job
        self.ctx = context
        self.setWindowTitle(f"aXqua - {job.job_id}")
        self.resize(900, 600)

        #: What is currently being followed, and how far it has been read.
        self._path: Path | None = None
        self._offset = 0

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.solver_toggle = QCheckBox("Solver listing (instead of axqua's log)")
        self.solver_toggle.toggled.connect(self._resolve_path)
        controls.addWidget(self.solver_toggle)
        self.follow = QCheckBox("Follow")
        self.follow.setChecked(not job.is_terminal)
        self.follow.toggled.connect(self._follow_changed)
        controls.addWidget(self.follow)
        controls.addStretch(1)
        open_button = QPushButton("Open folder")
        open_button.clicked.connect(lambda: open_in_file_manager(self.job.root))
        controls.addWidget(open_button)
        layout.addLayout(controls)

        self.path_label = QLabel("")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Monospace")
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        self.view.setFont(font)
        layout.addWidget(self.view, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)

    def show_it(self) -> None:
        self._resolve_path()
        self._follow_changed(self.follow.isChecked())
        self.show()

    # -- which file ---------------------------------------------------------------
    def _resolve_path(self) -> None:
        """Ask axqua where the log is, and fall back to the job layout if it cannot say.

        Asked exactly once per file, off the GUI thread. The runner owns the job
        directory's layout, so a second implementation of it here is a second thing to
        keep in step - but a plugin that could not show a log because the CLI was busy
        would be worse than one that knows the usual answer.
        """
        self._path, self._offset = None, 0
        self.view.setPlainText("")
        self.path_label.setText("looking for the log...")
        solver = self.solver_toggle.isChecked()
        client = getattr(self.ctx, "client", None)
        job_root = str(self.job.root.parent) if self.job.root.parent.name else None
        if client is None:
            self._use_path(self._local_guess())
            return
        run_async(
            "aXqua: locating the job log",
            lambda: client.log_path(self.job.job_id, solver=solver, job_root=job_root),
            on_success=lambda path: self._use_path(
                Path(path) if path else self._local_guess()),
            on_error=lambda _exc: self._use_path(self._local_guess()),
            owner=self)

    def _local_guess(self) -> Path | None:
        """The job directory's usual layout, when the CLI could not be asked."""
        if self.solver_toggle.isChecked():
            folder = self.job.root / "solver"
            try:
                logs = sorted(folder.glob("*.log"),
                              key=lambda p: _mtime(p), reverse=True)
            except OSError:
                return None
            return logs[0] if logs else None
        runner = self.job.root / "runner.log"
        return runner if runner.exists() else None

    def _use_path(self, path: Path | None) -> None:
        self._path, self._offset = path, 0
        if path is None:
            self.path_label.setText("nothing written yet")
            self.view.setPlainText("")
            return
        self.path_label.setText(str(path))
        self._poll()

    # -- content ------------------------------------------------------------------
    def _poll(self) -> None:
        """Append whatever has been written since the last look.

        Runs from a timer, so every filesystem call here is guarded: a log that is
        rotated, truncated or deleted between two ticks is ordinary, and an exception out
        of a timer slot is not something the user could act on anyway.
        """
        path = self._path
        if path is None:
            return
        try:
            size = path.stat().st_size
        except OSError:
            self.path_label.setText(f"{path} (gone)")
            return
        if size == self._offset:
            return                          # nothing new; the common case
        if size < self._offset:
            # Truncated or rotated underneath us: start again rather than show a
            # nonsensical splice of two files.
            self._offset = 0
            self.view.setPlainText("")

        at_bottom = (self.view.verticalScrollBar().value()
                     >= self.view.verticalScrollBar().maximum() - 4)
        if self._offset == 0:
            text, truncated = _tail(path, TAIL_BYTES)
            prefix = (f"[showing the last {TAIL_BYTES // 1024} KiB of "
                      f"{size / 1024 / 1024:.1f} MiB]\n\n") if truncated else ""
            self.view.setPlainText(prefix + text)
        else:
            text = _read_from(path, self._offset)
            if text:
                self.view.appendPlainText(text.rstrip("\n"))
        self._offset = size

        if at_bottom:
            # Only auto-scroll if the user was already at the end; yanking the view back
            # while they are reading something further up is infuriating.
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _follow_changed(self, on: bool) -> None:
        if on:
            self._timer.start(FOLLOW_INTERVAL_MS)
        else:
            self._timer.stop()

    def closeEvent(self, event):        # noqa: N802 - Qt naming
        self._timer.stop()
        super().closeEvent(event)


def _mtime(path: Path) -> float:
    """A sort key that survives the file disappearing mid-sort."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _tail(path: Path, limit: int) -> tuple[str, bool]:
    """The last *limit* bytes, decoded forgivingly."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > limit:
                fh.seek(size - limit)
                # The seek almost certainly landed mid-line; drop the partial one rather
                # than showing a fragment.
                fh.readline()
                return fh.read().decode("utf-8", "replace"), True
            return fh.read().decode("utf-8", "replace"), False
    except OSError as exc:
        return f"could not read {path}: {exc}", False


def _read_from(path: Path, offset: int) -> str:
    """Everything after *offset*, for the follow timer."""
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
