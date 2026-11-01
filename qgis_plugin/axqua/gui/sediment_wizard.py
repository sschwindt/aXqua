"""The *Sediment transport wizard* of a hydrology case.

The runoff of a catchment carries soil, and how much of it reaches the outlet, or a river that flows through the catchment, is often the question of the study. aXqua therefore strongly recommends sediment transport in the hydrology mode, and this window sets it up: GAIA coupled into the hydrology run. One window with four tabs, visited in any order:

1. **Soil layer**: how thick the soil is that can erode, above a bed that does not.
2. **Grain sizes**: what the soil is made of: the texture class of the case or another one, clay, sand, the shares of sand, silt and clay, or a median grain size.
3. **Recommendation**: what ``axqua hydrology sediment`` recommends for that soil and the slope of the catchment, with the reasons, and the check box *Transport sediment (strongly recommended)*.
4. **Save**: the settings of the block ``morphodynamics``, written into the case file through ``axqua case read`` / ``case write``. Unticked, the case is saved with ``enabled: false``, which is a decision the checkup respects.

The window decides nothing about the transport itself: the recommendation is the library's answer, the same in a terminal.
"""

from __future__ import annotations

import html
from pathlib import Path

from qgis.PyQt.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog,
                                 QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                                 QPushButton, QRadioButton, QScrollArea, QTabWidget,
                                 QVBoxLayout, QWidget)

from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import help as help_pages
from .style import AXQUA_PURPLE

TABS = ("Soil layer", "Grain sizes", "Recommendation", "Save")

INTRO = (
    "<b>Sediment transport is strongly recommended in the hydrology mode.</b> The "
    "runoff of a catchment carries soil, and how much of it reaches the outlet, or a "
    "river that flows through the catchment, is often the question of the study. "
    "GAIA computes it together with the runoff, in the same hydrology run.")

LAYER_HINT = (
    "The soil that can erode lies on a bed that does not: on a field, the plough "
    "layer (about 0.3 m) on the subsoil; on a hillslope, the topsoil on the rock or a "
    "compacted layer. Where the flow has removed the whole layer, it stops eroding.")

TEXTURES = ("sand", "loamy sand", "sandy loam", "loam", "silt loam", "silt",
            "sandy clay loam", "clay loam", "silty clay loam", "sandy clay",
            "silty clay", "clay")

#: ``(soil, text)`` of the choices of the tab Grain sizes, in their order.
SOILS = (("texture", "A texture class"), ("clay", "Clay"), ("sand", "Sand"),
         ("shares", "Shares of sand, silt and clay"),
         ("grain", "Median grain size d50"))

KEEP_OFF = ("Without sediment transport the case computes water only. The checkup "
            "no longer asks for it once this is saved.")


class SedimentWizard(QDialog):
    """Set up sediment transport for the hydrology case at *case*. *info* is the answer of ``axqua hydrology info`` (its ``soil_texture`` and ``sediment``)."""

    def __init__(self, context, case: Path, info: dict | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.ctx = context
        self.client = context.client
        self.case = Path(case)
        self.info = dict(info or {})
        self.answer: dict | None = None        # the newest recommendation
        self._asked_for: dict | None = None    # the choices it answers
        self._slope: float | None = None       # the median slope, once it is known
        self.saved = False
        self.setWindowTitle(f"Sediment transport: {self.case.name}")
        self.resize(820, 680)
        self._build()

    # -- construction -------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        for title, page in zip(TABS, (self._layer_page(), self._grain_page(),
                                      self._recommendation_page(), self._save_page())):
            self.tabs.addTab(_scrolling(page), title)
        self.tabs.currentChanged.connect(self._tab_changed)
        outer.addWidget(self.tabs, 1)
        self.status = _label("")
        outer.addWidget(self.status)
        buttons = QHBoxLayout()
        help_button = QPushButton("Help")
        help_button.clicked.connect(
            lambda: help_pages.open_window_help("sediment-wizard"))
        buttons.addWidget(help_button)
        buttons.addStretch(1)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(
            lambda: self.tabs.setCurrentIndex(max(self.tabs.currentIndex() - 1, 0)))
        buttons.addWidget(self.back_button)
        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(
            lambda: self.tabs.setCurrentIndex(min(self.tabs.currentIndex() + 1,
                                                  len(TABS) - 1)))
        buttons.addWidget(self.next_button)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        outer.addLayout(buttons)
        self._tab_changed(0)

    def _layer_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(INTRO, rich=True))
        form = QFormLayout()
        self.thickness = QDoubleSpinBox()
        self.thickness.setDecimals(2)
        self.thickness.setRange(0.01, 10.0)
        self.thickness.setSingleStep(0.05)
        self.thickness.setSuffix(" m")
        state = self.info.get("sediment") or {}
        self.thickness.setValue(float(state.get("layer_thickness") or 0.3))
        form.addRow("Thickness of the soil that can erode", self.thickness)
        layout.addLayout(form)
        layout.addWidget(_label(LAYER_HINT))
        layout.addStretch(1)
        return page

    def _grain_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label(
            "What is the soil made of? A texture class stands for the shares of sand, "
            "silt and clay at the middle of its field in the USDA texture triangle; "
            "the shares or a median grain size from a sieve analysis are better."))
        self.soil_group = QButtonGroup(self)
        self.soil_buttons: dict[str, QRadioButton] = {}
        form = QFormLayout()
        for value, text in SOILS:
            button = QRadioButton(text)
            self.soil_group.addButton(button)
            self.soil_buttons[value] = button
            if value == "texture":
                self.texture = QComboBox()
                for name in TEXTURES:
                    self.texture.addItem(name, name)
                case_texture = self.info.get("soil_texture") or ""
                index = self.texture.findData(case_texture.lower())
                self.texture.setCurrentIndex(index if index >= 0
                                             else self.texture.findData("loam"))
                form.addRow(button, self.texture)
            elif value == "shares":
                holder = QWidget()
                row = QHBoxLayout(holder)
                row.setContentsMargins(0, 0, 0, 0)
                self.shares = {}
                for name, start in (("sand", 40.0), ("silt", 40.0), ("clay", 20.0)):
                    box = QDoubleSpinBox()
                    box.setRange(0.0, 100.0)
                    box.setSuffix(f" % {name}")
                    box.setValue(start)
                    self.shares[name] = box
                    row.addWidget(box)
                form.addRow(button, holder)
            elif value == "grain":
                self.d50 = QDoubleSpinBox()
                self.d50.setDecimals(3)
                self.d50.setRange(0.001, 100.0)
                self.d50.setValue(0.2)
                self.d50.setSuffix(" mm")
                form.addRow(button, self.d50)
            else:
                form.addRow(button, QLabel(""))
        self.soil_buttons["texture"].setChecked(True)
        self._restore((self.info.get("sediment") or {}).get("soil") or "")
        layout.addLayout(form)
        if self.info.get("soil_texture"):
            layout.addWidget(_label(
                f"The soil of the case for the infiltration is {self.info['soil_texture']}."))
        layout.addStretch(1)
        return page

    def _restore(self, soil: str) -> None:
        """Start from what the case says it was given (``morphodynamics.soil``, as the library writes it: ``texture silt loam``, ``clay``, ``sand``, ``sand 40 %, silt 40 %, clay 20 %``, ``d50 0.2 mm``)."""
        import re

        text = soil.strip().lower()
        if text.startswith("texture "):
            index = self.texture.findData(text[len("texture "):])
            if index >= 0:
                self.texture.setCurrentIndex(index)
                self.soil_buttons["texture"].setChecked(True)
        elif text in ("clay", "sand"):
            self.soil_buttons[text].setChecked(True)
        elif text.startswith("d50 "):
            found = re.search(r"([\d.]+)", text)
            if found:
                self.d50.setValue(float(found.group(1)))
                self.soil_buttons["grain"].setChecked(True)
        elif "silt" in text and "%" in text:
            shares = dict(re.findall(r"(sand|silt|clay) ([\d.]+)", text))
            if len(shares) == 3:
                for name, box in self.shares.items():
                    box.setValue(float(shares[name]))
                self.soil_buttons["shares"].setChecked(True)

    def _recommendation_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.title_label = _label("", rich=True)
        layout.addWidget(self.title_label)
        self.reasons_label = _label("", rich=True)
        layout.addWidget(self.reasons_label)
        self.warnings_label = _label("", rich=True)
        layout.addWidget(self.warnings_label)
        self.settings_label = _label("", rich=True)
        layout.addWidget(self.settings_label)
        self.transport = QCheckBox("Transport sediment (strongly recommended)")
        self.transport.setChecked(True)
        self.transport.toggled.connect(lambda _=False: self._show_summary())
        layout.addWidget(self.transport)
        self.off_note = _label(KEEP_OFF)
        self.off_note.setVisible(False)
        layout.addWidget(self.off_note)
        self.transport.toggled.connect(lambda on: self.off_note.setVisible(not on))
        layout.addStretch(1)
        return page

    def _save_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label("What Save writes into the block morphodynamics of the "
                                "case file."))
        self.summary = _label("")
        layout.addWidget(self.summary)
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        layout.addStretch(1)
        return page

    # -- the recommendation -------------------------------------------------------
    def choices(self) -> dict:
        """What the tabs Soil layer and Grain sizes say, as options of the client."""
        soil = next(value for value, button in self.soil_buttons.items()
                    if button.isChecked())
        out = {"soil": soil, "thickness": round(self.thickness.value(), 3)}
        if soil == "texture":
            out["texture"] = self.texture.currentData()
        elif soil == "shares":
            out["shares"] = tuple(self.shares[k].value() for k in ("sand", "silt", "clay"))
        elif soil == "grain":
            out["d50_mm"] = self.d50.value()
        return out

    def recommend(self) -> None:
        choices = self.choices()
        if choices.get("soil") == "shares" and not sum(choices["shares"]) > 0:
            self.status.setText("Enter the shares of sand, silt and clay.")
            return
        client, case = self.client, self.case
        self.title_label.setText("Asking aXqua for a recommendation...")
        self._asked_for = dict(choices)
        # the slope does not depend on the soil: the first answer's slope is passed on, so that the terrain model is read once and not at every change of the soil
        known = {"slope": self._slope} if self._slope is not None else {}
        run_async("aXqua: recommending a sediment transport setup",
                  lambda: client.hydrology_sediment(case, **choices, **known),
                  on_success=lambda answer: self.show_recommendation(answer, choices),
                  on_error=self._failed, owner=self)

    def show_recommendation(self, answer: dict, choices: dict) -> None:
        if choices != self._asked_for:
            return                               # an older question
        self.answer = answer or {}
        slope = self.answer.get("slope")
        if slope is not None:
            self._slope = float(slope)
        self.title_label.setText(
            f"<b>{html.escape(str(self.answer.get('title', '')))}</b>"
            + (f" &nbsp;(median slope of the catchment {100 * float(slope):.1f} %)"
               if slope is not None else ""))
        self.reasons_label.setText("<br>".join(
            html.escape(str(r)) for r in self.answer.get("reasons") or []))
        warnings = self.answer.get("warnings") or []
        self.warnings_label.setText("<br>".join(
            f'<span style="color:{AXQUA_PURPLE};">{html.escape(str(w))}</span>'
            for w in warnings))
        self.settings_label.setText(settings_html(self.answer.get("settings") or {}))
        self.status.setText("")
        self._show_summary()

    def _failed(self, exc) -> None:
        self.title_label.setText("")
        self.status.setText(user_text(exc))

    def _tab_changed(self, index: int) -> None:
        self.back_button.setVisible(index > 0)
        self.next_button.setVisible(index < len(TABS) - 1)
        if TABS[index] in ("Recommendation", "Save") and (
                self.answer is None or self._asked_for != self.choices()):
            self.recommend()
        if TABS[index] == "Save":
            self._show_summary()

    # -- saving -------------------------------------------------------------------
    def settings(self) -> dict:
        """The dotted settings Save writes: the recommendation, or switched off."""
        if not self.transport.isChecked():
            return {"morphodynamics.enabled": False}
        return dict((self.answer or {}).get("settings") or {})

    def _show_summary(self) -> None:
        if self.transport.isChecked() and not self.answer:
            self.summary.setText("No recommendation yet; open the tab Recommendation.")
            return
        self.summary.setText("\n".join(
            f"{key}: {'(removed)' if value is None else value}"
            for key, value in sorted(self.settings().items())))

    def merged(self, data: dict) -> dict:
        """The case file *data* with the block morphodynamics set as chosen."""
        data = dict(data or {})
        block = dict(data.get("morphodynamics") or {})
        for key, value in self.settings().items():
            name = key.split(".", 1)[1]
            if value is None:
                block.pop(name, None)
            else:
                block[name] = value
        data["morphodynamics"] = block
        return data

    def save(self) -> None:
        if self.transport.isChecked() and not self.answer:
            self.status.setText("Open the tab Recommendation first.")
            return
        client, case = self.client, self.case
        self.status.setText("Saving...")

        def work():
            data = (client.case_read(case) or {}).get("data") or {}
            return client.case_write(case, self.merged(data))

        def done(answer: dict) -> None:
            self.saved = True
            count = len(answer.get("findings") or [])
            what = ("Sediment transport is set up; build the case again on the tab "
                    "Preprocessing." if self.transport.isChecked() else
                    "Sediment transport is switched off.")
            self.status.setText(f"Saved. {what}" + (
                f" The check of the case found {count} thing(s) to look at." if count
                else ""))

        run_async("aXqua: saving the sediment transport", work, on_success=done,
                  on_error=self._failed, owner=self)


def settings_html(settings: dict) -> str:
    """The settings of a recommendation as a small table."""
    rows = []
    for key, value in settings.items():
        if value is None:
            continue
        name = key.split(".", 1)[1]
        if name == "sediment_classes":
            text = "; ".join(
                f"{c.get('type', '')} {1000 * float(c.get('diameter', 0)):g} mm"
                + (f" ({100 * float(c['fraction']):.0f} %)" if "fraction" in c else "")
                for c in value)
        else:
            text = str(value)
        rows.append(f"<tr><td>{html.escape(name)}</td><td>&nbsp;{html.escape(text)}"
                    "</td></tr>")
    return "<table>" + "".join(rows) + "</table>"


def _label(text: str, *, rich: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    if rich:
        from qgis.PyQt.QtCore import Qt

        from ..compat import enum_value
        label.setTextFormat(enum_value(Qt, "TextFormat.RichText", "RichText"))
    return label


def _scrolling(body: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(body)
    return area
