"""Validation on the *Calibration & validation* tab.

A validation compares the calibrated model with measurements of **another flow
situation**, which the calibration has not seen: another discharge, another survey.
The box holds what describes that situation and nothing else:

* the point layer with its measurements, chosen in a file dialog or from the layers
  that are open in QGIS;
* the discharge of each inflow of the model on that day. The model's inflows are
  listed with the discharge they carry in the calibrated case, so that it is clear
  which is which;
* the water level at the outflow, where the case prescribes one.

*Save* writes the situation into the case file (``calibration.validation``).
*Validate* saves it and submits the validation job, which runs the calibrated model
with these boundary values and compares it with the measurements.

There is deliberately no control for holding back a share of the calibration data.
Measurements of one survey are taken in one flow field within hours, so a model that
predicts some of them from the others has shown that it interpolates, not that its
parameters hold at another discharge.
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                                 QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                                 QPushButton, QVBoxLayout, QWidget)

from ..core.runner_client import user_text
from ..core.tasks import run_async
from .case_editor import stored_path
from .section_pages import SectionPage

INTRO = ("A validation compares the calibrated model with measurements of another "
         "flow situation, for example another discharge. All data of the case under "
         "Ground truth are calibration data. Enter the validation data here.")

NOT_BUILT = ("The model is not built yet, so its inflows are not known. Build the "
             "case on the tab Preprocessing first.")

#: What a quantity is called in a sentence, and its unit.
QUANTITIES = {"SCALAR VELOCITY": ("flow velocity", "m/s"), "WATER DEPTH": ("water depth", "m")}


def summarize(report: dict) -> str:
    """The result of a validation in a few lines a person can read."""
    lines = []
    for quantity, numbers in (report.get("summary") or {}).items():
        if not numbers.get("n"):
            continue
        name, unit = QUANTITIES.get(quantity, (quantity.lower(), ""))
        unit = f" {unit}" if unit else ""
        line = (f"{name[:1].upper()}{name[1:]} at {numbers['n']} points: the model "
                f"deviates by {numbers['bias']:+.2f}{unit} on average "
                f"({100 * numbers['relative_bias']:+.0f} %), root mean square error "
                f"{numbers['rmse']:.2f}{unit}")
        if "within_error" in numbers:
            line += (f", {100 * numbers['within_error']:.0f} % of the points within "
                     "the measurement error")
        lines.append(line + ".")
    lines += [str(note) for note in report.get("notes") or []]
    return "\n".join(lines)


def point_layers() -> list[tuple[str, str]]:
    """``(name, file)`` of the point layers that are open in QGIS."""
    from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes

    found = []
    for layer in QgsProject.instance().mapLayers().values():
        if not isinstance(layer, QgsVectorLayer):
            continue
        if QgsWkbTypes.geometryType(layer.wkbType()) != QgsWkbTypes.PointGeometry:
            continue
        source = layer.source().split("|")[0]
        if source and Path(source).is_file():
            found.append((layer.name(), source))
    return found


class ValidationBox(QGroupBox):
    """The validation situation of the active case, and the button that runs it."""

    def __init__(self, context, parent=None) -> None:
        super().__init__("Validation with data of another flow situation", parent)
        self.ctx = context
        self.info: dict = {}
        self.case: Path | None = None
        self._inflows: dict[int, QDoubleSpinBox] = {}
        layout = QVBoxLayout(self)
        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.form = QFormLayout()
        self.name = QLineEdit("validation")
        self.form.addRow("Name of the situation", self.name)
        self.layer = QLineEdit()
        self.layer.setPlaceholderText("point layer with the measured velocities and "
                                      "depths")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.layer, 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        pick = QPushButton("From QGIS...")
        pick.setToolTip("Select a point layer that is open in QGIS")
        pick.clicked.connect(self.pick_layer)
        row.addWidget(pick)
        holder = QWidget()
        holder.setLayout(row)
        self.form.addRow("Validation data", holder)
        self.total = self._spin("m³/s")
        self.form.addRow("Discharge", self.total)
        self.level = self._spin("m", maximum=1.0e5, decimals=3)
        self.form.addRow("Outflow water level", self.level)
        layout.addLayout(self.form)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.setToolTip("Write the validation situation into the case file")
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.save_button)
        self.run_button = QPushButton("Validate")
        self.run_button.setToolTip("Run the calibrated model for this situation and "
                                   "compare it with the validation data")
        self.run_button.clicked.connect(self.validate)
        buttons.addWidget(self.run_button)
        self.figure_button = QPushButton("Open the figure")
        self.figure_button.clicked.connect(self.open_figure)
        buttons.addWidget(self.figure_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        self.show_info({})

    @staticmethod
    def _spin(unit: str, *, maximum: float = 1.0e6, decimals: int = 3) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setRange(0.0, maximum)
        spin.setSuffix(f" {unit}")
        return spin

    def _show_row(self, field: QWidget, visible: bool) -> None:
        """Show or hide a row of the form (Qt 5 has no call for it)."""
        field.setVisible(visible)
        label = self.form.labelForField(field)
        if label is not None:
            label.setVisible(visible)

    # -- reading -------------------------------------------------------------------
    def refresh(self) -> None:
        case = self.ctx.project.active_case_path()
        self.case = Path(case) if case is not None else None
        if self.case is None or not self.case.exists():
            self.show_info({})
            return
        client, chosen = self.ctx.client, self.case
        run_async("aXqua: reading the validation of the case",
                  lambda: client.validation_info(chosen),
                  on_success=self.show_info,
                  on_error=lambda exc: self.show_info({}, note=user_text(exc)),
                  owner=self)

    def show_info(self, info: dict, *, note: str = "") -> None:
        """Fill the form from ``axqua validation <case>``."""
        self.info = info or {}
        boundaries = [b for b in self.info.get("boundaries") or []
                      if b.get("kind") == "inflow"]
        situations = self.info.get("situations") or []
        situation = situations[0] if situations else {}
        #: what the case file holds; the form changes only what it shows
        self.stored = dict(situation)
        for spin in self._inflows.values():
            self.form.removeRow(spin)
        self._inflows = {}
        given = {int(k): float(v) for k, v in (situation.get("inflows") or {}).items()}
        position = self.form.getWidgetPosition(self.total)[0]
        for boundary in boundaries if len(boundaries) > 1 else []:
            spin = self._spin("m³/s")
            index = int(boundary["index"])
            was = boundary.get("discharge")
            label = f"Discharge of inflow {index}" + (
                f" ({float(was):g} m³/s in the calibrated case)" if was is not None
                else "")
            spin.setValue(given.get(index, 0.0))
            position += 1
            self.form.insertRow(position, label, spin)
            self._inflows[index] = spin
        # one inflow, or a model that is not built yet: the total is all there is.
        # Discharges per inflow that the case file holds for a model that is not built
        # cannot be shown yet; they are kept, and the total is not asked for.
        self.kept_inflows = bool(given) and not self._inflows
        self._show_row(self.total, not self._inflows and not self.kept_inflows)
        self.total.setValue(float(situation.get("prescribed_flowrate") or 0.0))
        needs_level = self.info.get("outflow_condition") == "elevation"
        self._show_row(self.level, needs_level)
        self.level.setValue(float(situation.get("prescribed_elevation") or 0.0))
        if situation:
            self.name.setText(str(situation.get("name") or "validation"))
            sources = situation.get("sources") or []
            self.layer.setText(str((sources[0] if sources else {}).get("positions")
                                   or ""))
        hints = []
        if note:
            hints.append(note)
        elif self.case is not None and not self.info.get("boundaries"):
            hints.append(NOT_BUILT)
            if self.kept_inflows:
                hints.append("The case file gives a discharge for each inflow ("
                             + ", ".join(f"inflow {index}: {value:g} m³/s"
                                         for index, value in sorted(given.items()))
                             + "). These are kept and can be changed here once the "
                               "model is built.")
        if self.case is not None and self.info and not self.info.get("calibrated"):
            hints.append("The case has no finished calibration yet. The validation "
                         "uses the parameter values the calibration ends with.")
        self.hint.setText(" ".join(hints))
        self.hint.setVisible(bool(hints))
        reports = self.info.get("reports") or []
        self.report = reports[0] if reports else {}
        self.result.setText(summarize(self.report) if self.report else "")
        self.figure_button.setEnabled(bool(self.report.get("figure")))
        ready = self.case is not None
        self.save_button.setEnabled(ready)
        self.run_button.setEnabled(ready and bool(self.info.get("calibrated")))

    # -- choosing the layer --------------------------------------------------------
    def browse(self) -> None:
        start = self.layer.text() or (str(self.case.parent) if self.case else "")
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Point layer with the validation data", start,
            "Vector layers (*.gpkg *.shp *.geojson);;All files (*)")
        if chosen:
            self.layer.setText(chosen)

    def pick_layer(self) -> None:
        layers = point_layers()
        if not layers:
            self.ctx.warn("No point layer from a file is open in QGIS.")
            return
        names = [name for name, _source in layers]
        chosen, accepted = QInputDialog.getItem(
            self, "Validation data", "Point layer", names, 0, False)
        if accepted and chosen in names:
            self.layer.setText(dict(layers)[chosen])

    # -- writing -------------------------------------------------------------------
    def situation(self) -> dict:
        """What the form says, as an entry of ``calibration.validation``."""
        folder = self.case.parent if self.case else Path(".")
        chosen = self.layer.text().strip()
        # Start from what the case file holds: an entry the form has no field for (a
        # duration, the discharges per inflow of a model that is not built yet) must
        # survive a save.
        out: dict = {key: value for key, value in getattr(self, "stored", {}).items()
                     if value not in (None, "", [], {})}
        out["name"] = self.name.text().strip() or "validation"
        if self._inflows:
            out["inflows"] = {index: spin.value()
                              for index, spin in self._inflows.items()}
            out.pop("prescribed_flowrate", None)
        elif not getattr(self, "kept_inflows", False):
            out.pop("inflows", None)
            out.pop("prescribed_flowrate", None)
            if self.total.value() > 0:
                out["prescribed_flowrate"] = self.total.value()
        if self.info.get("outflow_condition") == "elevation":
            out.pop("prescribed_elevation", None)
            if self.level.value() > 0:
                out["prescribed_elevation"] = self.level.value()
        sources = [dict(source) for source in out.get("sources") or []]
        if chosen:
            first = sources[0] if sources else {"category": "hydraulics",
                                                "kind": "points"}
            # the layer as the case file names it, unless another one was chosen
            if chosen != str(first.get("positions") or ""):
                first["positions"] = stored_path(chosen, folder)
            out["sources"] = [first] + sources[1:]
        else:
            out.pop("sources", None)
        return out

    def save(self, then=None) -> None:
        """Write the situation into the case file; *then* runs after it is written."""
        if self.case is None:
            return
        client, case, entry = self.ctx.client, self.case, self.situation()

        def write():
            data = dict(client.case_read(case).get("data") or {})
            calibration = dict(data.get("calibration") or {})
            existing = calibration.get("validation") or []
            if isinstance(existing, dict):          # one situation, written plainly
                existing = [existing]
            # the form edits the first situation; any further ones are kept as they are
            others = [s for s in existing[1:]
                      if isinstance(s, dict) and s.get("name") != entry["name"]]
            calibration["validation"] = [entry] + others
            data["calibration"] = calibration
            return client.case_write(case, data)

        self.save_button.setEnabled(False)
        run_async("aXqua: saving the validation situation", write,
                  on_success=lambda answer: self._saved(answer, then),
                  on_error=self._failed, owner=self)

    def _saved(self, _answer: dict, then) -> None:
        self.save_button.setEnabled(True)
        self.result.setText("The validation situation is saved in the case file.")
        if then is not None:
            then()

    def _failed(self, exc: Exception) -> None:
        self.save_button.setEnabled(True)
        self.ctx.error(user_text(exc))

    def validate(self) -> None:
        name = self.name.text().strip() or "validation"
        self.save(then=lambda: self.ctx.submit("validation", {"situation": name}))

    def open_figure(self) -> None:
        figure = str(self.report.get("figure") or "")
        if figure and Path(figure).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(figure))


class CalibrationPage(SectionPage):
    """The *Calibration & validation* tab: the calibration, and below it the
    validation with data of another situation."""

    def __init__(self, context, section_key: str, sub_key: str = "", **kwargs) -> None:
        super().__init__(context, section_key, sub_key, **kwargs)
        self.validation = ValidationBox(context)
        # above the stretch that keeps the boxes at the top
        self._layout.insertWidget(self._layout.count() - 1, self.validation)

    def apply(self, case_view) -> None:
        super().apply(case_view)
        self.validation.setVisible(case_view is not None)
        # the boxes of the capabilities are inserted from the top; keep this one last
        self._layout.removeWidget(self.validation)
        self._layout.insertWidget(self._layout.count() - 1, self.validation)
        self.validation.refresh()
