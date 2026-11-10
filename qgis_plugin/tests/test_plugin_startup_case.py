"""The case of the last session fills its tabs after QGIS starts.

On the Windows test computer (2026-11-10), *Case Setup* named the Isar case as active after a restart of QGIS, but *Simulations > Baseline (Telemac)* said "Add a case on the tab Case Setup first." until *Refresh* was clicked: the panel read the list of cases from the settings and never asked axqua what the active case can do.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("qgis.core", reason="PyQGIS is not importable in this interpreter")

READ = "aXqua: reading what this case can do"


class _Info:
    def describe(self):
        return "axqua 1.3.1"


class _Iface:
    def messageBar(self):                        # noqa: N802 - QGIS naming
        class _Bar:
            def pushMessage(self, *args, **kwargs):  # noqa: N802 - QGIS naming
                pass
        return _Bar()

    def mainWindow(self):                        # noqa: N802 - QGIS naming
        return None


@pytest.fixture
def panel(qgis_app, monkeypatch, tmp_path):
    """A panel whose stored list has two cases, the first one active; nothing runs in the background, and no axqua of this computer is started."""
    from axqua_plugin.core import runner_client, tasks
    from axqua_plugin.gui import configuration_tab
    from axqua_plugin.gui import dock as dock_module

    def no_program(*args, **kwargs):
        raise AssertionError("this test runs no axqua")

    monkeypatch.setattr(runner_client.subprocess, "run", no_program)
    monkeypatch.setattr(runner_client.subprocess, "Popen", no_program)
    cases = [tmp_path / "isar.axq-case", tmp_path / "inn.axq-case"]
    for case in cases:
        case.write_text("project: {}\n")
    stored = json.dumps({"cases": [str(case) for case in cases], "active_case": str(cases[0])})
    monkeypatch.setattr(dock_module, "_stored_cases", lambda: stored)
    monkeypatch.setattr(dock_module.AxquaDock, "_remember_cases", lambda self: None)
    queued = []

    def queue(title, work=None, **kwargs):
        queued.append((title, kwargs.get("on_success")))

    # every module that imported run_async by name answers to the test
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("axqua_plugin") and getattr(module, "run_async", None) is tasks.run_async:
            monkeypatch.setattr(module, "run_async", queue)
    monkeypatch.setattr(tasks, "run_async", queue)
    monkeypatch.setattr(configuration_tab.help_pages, "ask", lambda client: None)
    widget = dock_module.AxquaDock(_Iface())
    yield widget, queued, cases
    widget.deleteLater()


def _reads(queued):
    return [callback for title, callback in queued if title == READ]


def test_the_active_case_is_read_once_axqua_has_answered(panel):
    from axqua_plugin.gui.section_pages import NOT_READ

    widget, queued, cases = panel
    assert widget.ctx.project.active_case_path() == cases[0]
    assert not _reads(queued)                                   # not before axqua answers
    # until then a page says what it waits for, not "Add a case"
    page = next(p for p in widget.pages.values() if hasattr(p, "note"))
    assert page.note.text() == NOT_READ
    widget.configuration_tab._runner_checked(_Info())
    assert len(_reads(queued)) == 1
    _reads(queued)[0]({"solvers": []})                          # the answer arrives
    assert widget.ctx.case_view is not None
    widget.configuration_tab._runner_checked(_Info())           # the Check button: read already
    assert len(_reads(queued)) == 1


def test_the_answer_for_a_case_that_is_no_longer_active_is_dropped(panel):
    widget, queued, cases = panel
    widget.configuration_tab._runner_checked(_Info())
    late = _reads(queued)[0]                                    # asked for the first case
    widget.ctx.project.active_case = str(cases[1])              # the user chose another one
    late({"solvers": []})
    assert widget.ctx.case_view is None
