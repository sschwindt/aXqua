"""A warning of a submit reaches the user.

On the Windows test computer (2026-11-09) a TELEMAC job ran on 1 process instead of the case's 8, because that TELEMAC was built without MPI; axqua said so on its stderr, which the plugin does not show, so nothing in QGIS said why.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("qgis.core", reason="PyQGIS is not importable in this interpreter")

from axqua_plugin.core.runner_client import submit_warnings  # noqa: E402


@pytest.fixture(scope="module")
def qgis_app():
    """The QGIS application of the session, or one of its own; a second one aborts the process."""
    from qgis.core import QgsApplication

    app = QgsApplication.instance()
    if app is None:
        app = QgsApplication([], False)
        QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
        app.initQgis()
    yield app

WARNED = {"job_id": "JOB", "warnings": [
    "this TELEMAC runs on one process only (no partel): the job runs on 1 instead of "
    "the 8 set for the case or the computer"]}


def test_the_warnings_of_an_answer_are_sentences():
    assert submit_warnings(WARNED) == [
        "This TELEMAC runs on one process only (no partel): the job runs on 1 instead of "
        "the 8 set for the case or the computer."]
    # an older axqua sends none, and an answer that is not a mapping carries none
    assert submit_warnings({"job_id": "JOB"}) == []
    assert submit_warnings(None) == [] and submit_warnings(["x"]) == []
    assert submit_warnings({"warnings": ["", "  ", "done."]}) == ["Done."]


class _Ctx:
    def __init__(self):
        self.messages = []

    def info(self, text):
        self.messages.append(("info", text))

    def warn(self, text):
        self.messages.append(("warn", text))

    def error(self, text):
        self.messages.append(("error", text))

    def client_or_warn(self):
        class _Client:
            def submit(self, config, kind, **kwargs):
                return dict(WARNED)
        return _Client()

    def active_case_or_warn(self):
        return "/c/reach.axq-case"

    def job_submitted(self, job_id):
        self.messages.append(("submitted", job_id))


def test_a_box_shows_the_warning_of_its_submit(qgis_app, monkeypatch):
    from axqua_plugin.gui import capability_tab_widget
    from axqua_plugin.gui.capability_tab_widget import CapabilityTab
    from axqua_plugin.gui.capability_tabs import CapabilityView

    monkeypatch.setattr(capability_tab_widget, "run_async",
                        lambda title, call, on_success=None, on_error=None, owner=None:
                        on_success(call()))
    ctx = _Ctx()
    view = CapabilityView(name="steady2d", solver="telemac", implemented="yes",
                          configured=True, built=True, run=False)
    tab = CapabilityTab(view, ctx)
    tab._submit(view.submit_kind)
    (level, text), submitted = ctx.messages
    assert level == "warn" and text.startswith("Submitted JOB.")
    assert "This TELEMAC runs on one process only" in text
    assert submitted == ("submitted", "JOB")
    tab.deleteLater()


def test_the_panel_shows_the_warnings_of_its_submits(qgis_app):
    from axqua_plugin.gui.dock import AxquaDock

    ctx = _Ctx()
    fake = type("D", (), {"ctx": ctx, "job_submitted": lambda self, job_id: None})()
    AxquaDock._submitted(fake, (["JOB"], submit_warnings(WARNED)))
    assert ctx.messages[0][0] == "warn" and "on one process only" in ctx.messages[0][1]
    ctx.messages.clear()
    AxquaDock._submitted(fake, (["A", "B"], []))
    assert ctx.messages == [("info", "Submitted 2 jobs. Each waits for the one before "
                                     "it. They keep running if you close QGIS.")]
