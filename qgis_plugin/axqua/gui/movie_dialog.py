"""Exporting an unsteady result as a movie.

One frame per timestep of a mesh dataset, rendered through the map canvas's own layers,
extent and colour scale, then encoded. The canvas is what supplies everything except
*which* variable and *how big*: the user has already chosen the styling and the view, and
a second set of controls for those would be a second place for them to be wrong.

**Format.** WebM/VP9 - the most storage-efficient of the formats a browser and every
modern player can open without a codec hunt. A 300-frame depth animation lands in a few
megabytes where the equivalent animated GIF is tens.

**ffmpeg is optional.** If it is not on ``PATH`` the frames are kept and the dialog says
where they are, with the exact command to run later. Deleting a user's frames because the
encoder is missing would throw away the expensive half of the work.

**Nothing blocks the interface.** Frames are chained through the render job's ``finished``
signal rather than by spinning a nested event loop per frame, and the encode runs as a
background task, so Cancel is a button that works rather than a decoration.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
from pathlib import Path

from qgis.core import (QgsMapRendererParallelJob, QgsMapSettings, QgsMeshLayer,
                       QgsProject)
from qgis.PyQt.QtCore import QSize, QTimer
from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                                 QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                                 QProgressBar, QPushButton, QSpinBox, QVBoxLayout,
                                 QWidget)

DEFAULT_FPS = 8
DEFAULT_WIDTH = 1920
#: A long encode of a long animation, but still bounded.
ENCODE_TIMEOUT_S = 1800


def animatable_layers() -> list[QgsMeshLayer]:
    """Loaded mesh layers that have more than one timestep in some dataset group.

    A static bed-level mesh is a mesh layer too, and offering to animate it is how a
    dialog wastes somebody's afternoon.
    """
    out = []
    for layer in QgsProject.instance().mapLayers().values():
        if not isinstance(layer, QgsMeshLayer):
            continue
        if any(_timestep_count(layer, index) > 1
               for index in layer.datasetGroupsIndexes()):
            out.append(layer)
    return out


def _timestep_count(layer: QgsMeshLayer, index: int) -> int:
    """How many timesteps group *index* has.

    Both spellings take a ``QgsMeshDatasetIndex``, not the plain int that
    ``datasetGroupsIndexes()`` hands out - see ``result_loader.group_metadata`` for what
    that mismatch cost the styling code.
    """
    from qgis.core import QgsMeshDatasetIndex
    try:
        if hasattr(layer, "datasetCount"):
            return int(layer.datasetCount(QgsMeshDatasetIndex(int(index))))
        return int(_metadata(layer, index).datasetCount())
    except Exception:  # noqa: BLE001 - a provider that will not say means "not animatable"
        return 0


def _metadata(layer: QgsMeshLayer, index: int):
    from ..core.result_loader import group_metadata
    return group_metadata(layer, index)


class MovieDialog(QDialog):
    """Frame-by-frame export of a mesh layer's dataset over time."""

    def __init__(self, iface, layers: list[QgsMeshLayer] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.iface = iface
        self._layers = list(layers) if layers else animatable_layers()
        self.setWindowTitle("aXqua - export a movie")
        self.resize(560, 340)

        # export state, all of it, so cancelling has one thing to look at
        self._job = None
        self._step = 0
        self._count = 0
        self._frames_dir: Path | None = None
        self._output: Path | None = None
        self._settings: QgsMapSettings | None = None
        self._dataset_index: int | None = None
        self._cancelled = False
        self._running = False

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.layer_combo = QComboBox()
        for layer in self._layers:
            self.layer_combo.addItem(layer.name(), layer.id())
        self.layer_combo.currentIndexChanged.connect(self._layer_changed)
        form.addRow("Mesh layer", self.layer_combo)

        self.dataset = QComboBox()
        form.addRow("Variable", self.dataset)

        self.fps = QSpinBox()
        self.fps.setRange(1, 60)
        self.fps.setValue(DEFAULT_FPS)
        self.fps.setSuffix(" frames/s")
        form.addRow("Frame rate", self.fps)

        self.width = QSpinBox()
        self.width.setRange(320, 7680)
        self.width.setSingleStep(160)
        self.width.setValue(DEFAULT_WIDTH)
        self.width.setSuffix(" px")
        form.addRow("Width", self.width)

        row = QHBoxLayout()
        self.output = QLineEdit(str(Path.home() / "axqua-animation.webm"))
        row.addWidget(self.output, 1)
        browse = QPushButton("...")
        browse.setMaximumWidth(36)
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        form.addRow("Output", _wrap(row))
        layout.addLayout(form)

        self.note = QLabel(self._encoder_note())
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._export)
        self.buttons.rejected.connect(self._cancel)
        layout.addWidget(self.buttons)

        self._layer_changed()

    # -- the chosen layer ---------------------------------------------------------
    @property
    def layer(self) -> QgsMeshLayer | None:
        index = self.layer_combo.currentIndex()
        return self._layers[index] if 0 <= index < len(self._layers) else None

    def _layer_changed(self) -> None:
        """Re-fill the variable list for the layer now selected."""
        self.dataset.clear()
        layer = self.layer
        if layer is None:
            return
        for index in layer.datasetGroupsIndexes():
            if _timestep_count(layer, index) <= 1:
                continue                    # nothing to animate in this group
            meta = _metadata(layer, index)
            self.dataset.addItem((meta.name() or f"dataset {index}").strip(), index)
        active = layer.rendererSettings().activeScalarDatasetGroup()
        position = self.dataset.findData(active)
        if position >= 0:
            self.dataset.setCurrentIndex(position)

    # -- helpers ------------------------------------------------------------------
    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save the animation",
                                              self.output.text(), "WebM video (*.webm)")
        if path:
            self.output.setText(path)

    @staticmethod
    def _encoder_note() -> str:
        if shutil.which("ffmpeg"):
            return "ffmpeg found: the frames will be encoded to WebM/VP9 and removed."
        return ("ffmpeg was not found on PATH. The PNG frames will be written and kept, "
                "with the command to encode them shown when the export finishes.")

    # -- the work -----------------------------------------------------------------
    def _export(self) -> None:
        layer = self.layer
        index = self.dataset.currentData()
        if layer is None or index is None:
            self.note.setText("Choose a mesh layer and a variable with several "
                              "timesteps.")
            return
        count = _timestep_count(layer, index)
        if count <= 1:
            self.note.setText("This dataset has a single timestep, so there is nothing "
                              "to animate.")
            return

        output = Path(self.output.text()).expanduser()
        frames_dir = output.parent / f"{output.stem}-frames"
        try:
            frames_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.note.setText(f"Cannot write frames to {frames_dir}: {exc}")
            return

        self._dataset_index = int(index)
        self._count, self._step = count, 0
        self._frames_dir, self._output = frames_dir, output
        self._settings = self._map_settings()
        self._cancelled, self._running = False, True

        renderer = layer.rendererSettings()
        renderer.setActiveScalarDatasetGroup(self._dataset_index)
        layer.setRendererSettings(renderer)

        self.progress.setVisible(True)
        self.progress.setRange(0, count)
        self.progress.setValue(0)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.note.setText(f"Rendering {count} frames. Cancel stops after the current "
                          "one and keeps what has been written.")
        self._render_next()

    def _render_next(self) -> None:
        """Start the next frame.

        Chained through the render job's ``finished`` signal rather than a nested
        ``QEventLoop`` per frame: a nested loop re-enters Qt's event dispatch inside a
        button handler, which makes Cancel unreachable, lets a second export start on top
        of the first, and turns a closed dialog into a render into a deleted object.
        """
        layer = self.layer
        if self._cancelled or layer is None or self._step >= self._count:
            self._finish()
            return
        from qgis.core import QgsMeshDatasetIndex
        layer.setStaticScalarDatasetIndex(
            QgsMeshDatasetIndex(self._dataset_index, self._step))
        job = QgsMapRendererParallelJob(self._settings)
        job.finished.connect(self._frame_rendered)
        self._job = job
        job.start()

    def _frame_rendered(self) -> None:
        job, self._job = self._job, None
        if job is None or self._frames_dir is None:
            return
        try:
            job.renderedImage().save(str(self._frames_dir /
                                         f"frame-{self._step:05d}.png"))
        except Exception as exc:  # noqa: BLE001
            self.note.setText(f"Frame {self._step} could not be written: {exc}")
            self._cancelled = True
        self._step += 1
        self.progress.setValue(self._step)
        # Back through the event loop before the next frame, so the interface stays
        # answerable during a long export.
        QTimer.singleShot(0, self._render_next)

    def _cancel(self) -> None:
        if not self._running:
            self.reject()
            return
        self._cancelled = True
        self.note.setText("Cancelling after the current frame...")

    def _finish(self) -> None:
        self._running = False
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        if self._frames_dir is None or self._output is None:
            return
        if self._cancelled:
            self.note.setText(f"Cancelled. {self._step} frame(s) kept in "
                              f"{self._frames_dir}.")
            return
        self._encode(self._frames_dir, self._output, self._step)

    def _map_settings(self) -> QgsMapSettings:
        canvas = self.iface.mapCanvas()
        settings = QgsMapSettings()
        settings.setLayers(canvas.layers())
        settings.setExtent(canvas.extent())
        settings.setDestinationCrs(QgsProject.instance().crs())
        width = int(self.width.value())
        aspect = canvas.extent().height() / max(canvas.extent().width(), 1e-9)
        # An even height, because VP9 refuses odd dimensions - a failure that would
        # otherwise appear only at the very end, after every frame had been rendered.
        height = max(2, int(round(width * aspect)) // 2 * 2)
        settings.setOutputSize(QSize(width, height))
        settings.setBackgroundColor(canvas.canvasColor())
        return settings

    def _encode(self, frames_dir: Path, output: Path, count: int) -> None:
        command = encode_command(frames_dir, output, int(self.fps.value()))
        if shutil.which("ffmpeg") is None:
            self.note.setText(
                f"{count} frames written to {frames_dir}.\n\n"
                "ffmpeg is not installed, so nothing was encoded. To make the video "
                "later:\n\n" + " ".join(command))
            return
        self.note.setText(f"Encoding {count} frames with ffmpeg...")
        # Off the GUI thread: VP9 on a few hundred 1920px frames is minutes, and a
        # frozen QGIS at the end of a successful export is a poor reward for waiting.
        from ..core.tasks import run_async
        run_async("aXqua: encoding the animation", lambda: _run_ffmpeg(command),
                  on_success=lambda proc: self._encoded(proc, frames_dir, output, count),
                  on_error=lambda exc: self.note.setText(
                      f"Encoding failed ({exc}). The frames are kept in {frames_dir}."),
                  owner=self)

    def _encoded(self, proc, frames_dir: Path, output: Path, count: int) -> None:
        if proc.returncode != 0:
            self.note.setText(
                f"ffmpeg exited with {proc.returncode}; the frames are kept in "
                f"{frames_dir}.\n\n{(proc.stderr or '')[-800:]}")
            return
        shutil.rmtree(frames_dir, ignore_errors=True)
        self.note.setText(f"Wrote {output} ({count} frames).")

    def closeEvent(self, event):        # noqa: N802 - Qt naming
        self._cancelled = True
        super().closeEvent(event)


def encode_command(frames_dir: Path, output: Path, fps: int) -> list[str]:
    """The ffmpeg invocation, as an argument list.

    Resolved to an absolute path rather than relying on a PATH lookup at exec time, so
    what runs is what was found - and returned rather than run so the dialog can print
    it verbatim when ffmpeg is missing.
    """
    return [shutil.which("ffmpeg") or "ffmpeg", "-y", "-framerate", str(int(fps)),
            "-i", str(frames_dir / "frame-%05d.png"), "-c:v", "libvpx-vp9",
            "-b:v", "0", "-crf", "32", "-pix_fmt", "yuv420p", str(output)]


def _run_ffmpeg(command: list[str]):
    # A list with no shell: the only caller-supplied element is the output path the user
    # picked in a save dialog, and as an argv entry it cannot be interpreted as anything
    # but a filename.
    return subprocess.run(  # nosec B603
        command, capture_output=True, text=True, timeout=ENCODE_TIMEOUT_S)


def _wrap(layout) -> QWidget:
    widget = QWidget()
    widget.setLayout(layout)
    return widget
