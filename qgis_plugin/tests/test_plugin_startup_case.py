"""The case of the last session fills its tabs after QGIS starts.

On the Windows test computer (2026-11-10), *Case Setup* named the Isar case as active after a restart of QGIS, but *Simulations > Baseline (Telemac)* said "Add a case on the tab Case Setup first." until *Refresh* was clicked: the panel read the list of cases from the settings and never asked axqua what the active case can do.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("qgis.core", reason="PyQGIS is not importable in this interpreter")


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


def test_the_active_case_is_read_once_axqua_has_answered(qgis_app, monkeypatch, tmp_path):
    from axqua_plugin.core import tasks
    from axqua_plugin.gui import configuration_tab
    from axqua_plugin.gui import dock as dock_module

    case = tmp_path / "isar.axq-case"
    case.write_text("project: {}\n")
    stored = json.dumps({"cases": [str(case)], "active_case": str(case)})
    monkeypatch.setattr(dock_module, "_stored_cases", lambda: stored)
    monkeypatch.setattr(dock_module.AxquaDock, "_remember_cases", lambda self: None)
    queued = []
    # nothing runs: the test answers in place of the background calls
    monkeypatch.setattr(tasks, "run_async", lambda title, *a, **k: queued.append(title))
    monkeypatch.setattr(configuration_tab, "run_async", lambda title, *a, **k: queued.append(title))
    monkeypatch.setattr(configuration_tab.help_pages, "ask", lambda client: None)

    panel = dock_module.AxquaDock(_Iface())
    try:
        assert panel.ctx.project.active_case_path() == case
        assert "aXqua: reading what this case can do" not in queued   # not before axqua answers
        panel.configuration_tab._runner_checked(_Info())
        assert "aXqua: reading what this case can do" in queued
    finally:
        panel.deleteLater()
