"""The *Simulations > Hydrology* sub-tab: rainfall-runoff over a catchment.

Above, the box of the capability (Build, Submit, Load results), like every simulation. Below it, the data of the case: where the catchment is, which rain, soil and weather files it has, and the findings of the hydrology checkup. *Data wizard...* opens the window that guides through the data (:mod:`.hydrology_wizard`); *Check* runs the checkup with the analysis of the terrain model, which takes a few seconds.

The plugin decides nothing here: the answers are those of ``axqua hydrology info`` and ``axqua hydrology check``. Three rules keep them right and cheap. An answer is shown only when it is the newest one asked for the case that is still chosen (a checkup of the terrain takes seconds, and the user may have moved on to another case). A box that is not on the screen asks nothing until its tab is shown, so that one refresh of the case starts one checkup, not one per tab that carries the box. And a question that is still on its way is not asked again while the case file is the one it was asked about: a refresh then waits for its answer, which also keeps a checkup of the terrain the user started; data that have changed since are asked about again, with the terrain if that was the question.
"""

from __future__ import annotations

import weakref
from pathlib import Path
from typing import NamedTuple

from qgis.PyQt.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QPushButton,
                                 QVBoxLayout)

from ..compat import is_deleted
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


class _Question(NamedTuple):
    """One request of the box: its number, the case and the state of its file."""

    ticket: int
    case: Path | None
    stamp: tuple
    terrain: bool = False


def case_stamp(case: Path | None) -> tuple:
    """When and how large the case file was written; another value is other data."""
    try:
        state = case.stat()
    except (AttributeError, OSError):
        return ()
    return state.st_mtime_ns, state.st_size


#: Every box of the hydrology data (the Hydrology sub-tab and the Preprocessing tab).
_BOXES: "weakref.WeakSet[HydrologyDataBox]" = weakref.WeakSet()


def data_changed(context) -> None:
    """Tell every box of the dock of *context* that the data files of its case were written."""
    for box in list(_BOXES):
        if not is_deleted(box) and box.ctx is context:
            box.refresh(changed=True)


class HydrologyDataBox(QGroupBox):
    """The data of a hydrology case and its checkup."""

    def __init__(self, context, parent=None) -> None:
        super().__init__("Hydrology data", parent)
        _BOXES.add(self)
        self.ctx = context
        self.case: Path | None = None
        self.info: dict = {}
        self._stale = False                  # a refresh waits for the box to be shown
        self._changed = False                # ... and the data changed (the wizard)
        self._tickets = {"info": 0, "check": 0}   # the newest request of each kind
        #: the newest request of each kind while its answer is on its way
        self._waiting: dict[str, _Question | None] = {"info": None, "check": None}
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

    def refresh(self, *, changed: bool = False) -> None:
        """Show the case now chosen; *changed* says that its data files were written, which the case file does not show (the data wizard downloads into them)."""
        case = self.ctx.project.active_case_path()
        self.case = Path(case) if case is not None else None
        self._stale = True
        self._changed = self._changed or changed
        if self.isVisible():
            self._load()

    def showEvent(self, event) -> None:              # noqa: N802 - Qt's name
        super().showEvent(event)
        if self._stale:
            self._load()

    def _ask(self, kind: str, *, stamp: tuple = (), terrain: bool = False
             ) -> _Question:
        """A new request of *kind*; every older one is answered in vain."""
        self._tickets[kind] += 1
        question = _Question(self._tickets[kind], self.case, stamp, terrain)
        self._waiting[kind] = question
        return question

    def _on_its_way(self, kind: str) -> _Question | None:
        """The request of *kind* still to be answered for the case still chosen."""
        question = self._waiting[kind]
        if (question is None or question.ticket != self._tickets[kind]
                or question.case != self.case):
            return None
        return question

    def _answered(self, kind: str, question: _Question) -> bool:
        """Take the answer to *question*; is it the one to show?"""
        if self._waiting[kind] is question:
            self._waiting[kind] = None
        return question.ticket == self._tickets[kind] and question.case == self.case

    def _load(self) -> None:
        changed, self._stale, self._changed = self._changed, False, False
        if self.case is None or not self.case.exists():
            for kind in self._tickets:
                self._ask(kind)               # answers still on their way are void
                self._waiting[kind] = None
            self.show_info({})
            self.finding_list.set_findings([], checked=False)
            self.state.setText("")
            self.check_button.setEnabled(True)
            return
        stamp = case_stamp(self.case)
        info = self._on_its_way("info")
        if changed or info is None or info.stamp != stamp:
            self._read_info(stamp)
        check = self._on_its_way("check")
        if changed or check is None or check.stamp != stamp:
            # the terrain is checked again when that was the question
            self.check(terrain=bool(check and check.terrain), stamp=stamp)

    def _read_info(self, stamp: tuple) -> None:
        client, chosen = self.ctx.client, self.case
        question = self._ask("info", stamp=stamp)
        run_async("aXqua: reading the hydrology data of the case",
                  lambda: client.hydrology_info(chosen),
                  on_success=lambda info: (self.show_info(info)
                                           if self._answered("info", question)
                                           else None),
                  on_error=lambda exc: (self.show_info({}, note=user_text(exc))
                                        if self._answered("info", question) else None),
                  on_cancel=lambda: self._answered("info", question),
                  owner=self)

    def show_info(self, info: dict, *, note: str = "") -> None:
        self.info = info or {}
        self.summary.setText(note or summarize(self.info))

    def check(self, *, terrain: bool, stamp: tuple | None = None) -> None:
        if self.case is None:
            return
        client, chosen = self.ctx.client, self.case
        question = self._ask("check", terrain=terrain,
                             stamp=case_stamp(chosen) if stamp is None else stamp)
        self.state.setText("Checking the terrain model..." if terrain else "")
        self.check_button.setEnabled(False)
        run_async("aXqua: the hydrology checkup",
                  lambda: client.hydrology_check(chosen, terrain=terrain),
                  on_success=lambda answer: (self.show_findings(answer, terrain)
                                             if self._answered("check", question)
                                             else None),
                  on_error=lambda exc: (self.show_error(exc)
                                        if self._answered("check", question) else None),
                  on_cancel=lambda: (self.show_cancelled()
                                     if self._answered("check", question) else None),
                  owner=self)

    def show_findings(self, answer: dict, terrain: bool) -> None:
        self.check_button.setEnabled(True)
        self.state.setText("" if terrain else
                           "Check runs the analysis of the terrain model as well.")
        self.finding_list.set_findings(fnd.from_payload(answer), checked=True)

    def show_error(self, exc) -> None:
        self.check_button.setEnabled(True)
        self.state.setText(user_text(exc))

    def show_cancelled(self) -> None:
        self.check_button.setEnabled(True)
        self.state.setText("The checkup was cancelled.")

    def open_wizard(self) -> None:
        if self.case is None:
            self.ctx.warn("Add a case on the tab Case Setup first.")
            return
        from ..compat import exec_dialog
        from .hydrology_wizard import HydrologyWizard

        wizard = HydrologyWizard(self.ctx, self.case, self.info, parent=self)
        exec_dialog(wizard)
        if wizard.saved or wizard.wrote:
            # every box asks again itself (this one, and the one of the other tab): the
            # refresh of the page reaches a box only when the status of the case can be
            # read, and then finds these questions on their way
            data_changed(self.ctx)
        if wizard.saved:
            self.ctx.refresh_case()


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
