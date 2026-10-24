"""The *Simulations > Hydrology* sub-tab: rainfall-runoff over a catchment.

Above, the box of the capability (Build, Submit, Load results), like every simulation.
Below it, the data of the case: where the catchment is, which rain, soil and weather
files it has, and the findings of the hydrology checkup. *Data wizard...* opens the
window that guides through the data (:mod:`.hydrology_wizard`); *Check* runs the
checkup with the analysis of the terrain model, which takes a few seconds.

The plugin decides nothing here: the answers are those of ``axqua hydrology info``
and ``axqua hydrology check``.
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QPushButton,
                                 QVBoxLayout)

from ..core.runner_client import user_text
from ..core.tasks import run_async
from . import findings as fnd
from .section_pages import SectionPage

#: The files of a hydrology case, as the box names them.
FILES = (("rainfall", "Rain"), ("weather", "Weather (wind and sun)"),
         ("soil_zones", "Soil zones"), ("soil_table", "Soil table"),
         ("sealed_surfaces", "Sealed surfaces"))


def summarize(info: dict) -> str:
    """What ``axqua hydrology info`` says about a case, in a few lines."""
    if not info:
        return ""
    lines = [f"Catchment at {info.get('latitude', 0):.4f} N, "
             f"{info.get('longitude', 0):.4f} E, "
             f"{float(info.get('area_m2') or 0) / 1e6:.3f} km²."]
    files = info.get("files") or {}
    for key, label in FILES:
        value = files.get(key) or ""
        lines.append(f"{label}: {Path(value).name if value else 'not set'}")
    moisture = info.get("initial_water_content")
    lines.append("Water content at the start: "
                 + (f"{moisture:g} m³/m³" if moisture is not None else "not set"))
    return "\n".join(lines)


class HydrologyDataBox(QGroupBox):
    """The data of a hydrology case and its checkup."""

    def __init__(self, context, parent=None) -> None:
        super().__init__("Hydrology data", parent)
        self.ctx = context
        self.case: Path | None = None
        self.info: dict = {}
        layout = QVBoxLayout(self)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.state = QLabel("")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.finding_list = fnd.FindingList("")
        layout.addWidget(self.finding_list)
        row = QHBoxLayout()
        self.wizard_button = QPushButton("Data wizard...")
        self.wizard_button.setToolTip("Rain, soil, weather and the outlet, with the "
                                      "national agencies first and global data sets "
                                      "for anywhere")
        self.wizard_button.clicked.connect(self.open_wizard)
        row.addWidget(self.wizard_button)
        self.check_button = QPushButton("Check")
        self.check_button.setToolTip("The hydrology checkup, with the analysis of the "
                                     "terrain model: where the flow crosses the outline "
                                     "and which depressions hold water")
        self.check_button.clicked.connect(lambda: self.check(terrain=True))
        row.addWidget(self.check_button)
        row.addStretch(1)
        layout.addLayout(row)

    def refresh(self) -> None:
        case = self.ctx.project.active_case_path()
        self.case = Path(case) if case is not None else None
        if self.case is None or not self.case.exists():
            self.show_info({})
            return
        client, chosen = self.ctx.client, self.case
        run_async("aXqua: reading the hydrology data of the case",
                  lambda: client.hydrology_info(chosen),
                  on_success=self.show_info,
                  on_error=lambda exc: self.show_info({}, note=user_text(exc)),
                  owner=self)
        self.check(terrain=False)

    def show_info(self, info: dict, *, note: str = "") -> None:
        self.info = info or {}
        self.summary.setText(note or summarize(self.info))

    def check(self, *, terrain: bool) -> None:
        if self.case is None:
            return
        client, chosen = self.ctx.client, self.case
        self.state.setText("Checking the terrain model..." if terrain else "")
        self.check_button.setEnabled(False)
        run_async("aXqua: the hydrology checkup",
                  lambda: client.hydrology_check(chosen, terrain=terrain),
                  on_success=lambda answer: self.show_findings(answer, terrain),
                  on_error=self.show_error, owner=self)

    def show_findings(self, answer: dict, terrain: bool) -> None:
        self.check_button.setEnabled(True)
        self.state.setText("" if terrain else
                           "Check runs the analysis of the terrain model as well.")
        self.finding_list.set_findings(fnd.from_payload(answer), checked=True)

    def show_error(self, exc) -> None:
        self.check_button.setEnabled(True)
        self.state.setText(user_text(exc))

    def open_wizard(self) -> None:
        if self.case is None:
            self.ctx.warn("Add a case on the tab Case Setup first.")
            return
        from ..compat import exec_dialog
        from .hydrology_wizard import HydrologyWizard

        wizard = HydrologyWizard(self.ctx, self.case, self.info, parent=self)
        exec_dialog(wizard)
        if wizard.saved:
            self.ctx.refresh_case()
            self.refresh()


class HydrologyPage(SectionPage):
    """The capability box of the hydrology run, and below it the data of the case."""

    def __init__(self, context, section_key: str, sub_key: str = "", **kwargs) -> None:
        super().__init__(context, section_key, sub_key, **kwargs)
        self.data = HydrologyDataBox(context)
        self._layout.insertWidget(self._layout.count() - 1, self.data)

    def apply(self, case_view) -> None:
        super().apply(case_view)
        shown = any(c.configured for c in self.capabilities(case_view))
        self.data.setVisible(shown)
        self._layout.removeWidget(self.data)
        self._layout.insertWidget(self._layout.count() - 1, self.data)
        if shown:
            self.data.refresh()
