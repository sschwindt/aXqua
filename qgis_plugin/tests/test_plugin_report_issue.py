"""*Report issue*: what the Windows test computer found on 2026-11-10.

A check that could not be made offered *Report issue*, but the report did not say what the check had answered; and a home folder written with forward slashes (``C:/Users/name``) stayed in the report while the same folder with backslashes was replaced by ``~``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("qgis.core", reason="PyQGIS is not importable in this interpreter")

from axqua_plugin.gui import report_issue  # noqa: E402


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


@pytest.mark.parametrize("written", [
    r"C:\Users\Student\opt\telemac", "C:/Users/Student/opt/telemac",
    r"c:\users\student\opt\telemac", r"C:\\Users\\Student\\opt\\telemac"])
def test_a_windows_home_folder_is_hidden_however_it_is_written(written):
    text = report_issue.anonymize(f"cannot write {written}.", home=r"C:\Users\Student")
    assert text.startswith("cannot write ~") and "Student" not in text


def test_only_the_home_folder_itself_is_hidden():
    home = r"C:\Users\Student"
    assert report_issue.anonymize(r"C:\Users\Student2\x", home=home) == r"C:\Users\Student2\x"
    assert report_issue.anonymize(r"C:\Users\Student", home=home) == "~"
    assert report_issue.anonymize("/home/x/opt and /home/xy", home="/home/x") == "~/opt and /home/xy"
    assert report_issue.anonymize("/home/X/opt", home="/home/x") == "/home/X/opt"   # POSIX case counts
    assert report_issue.anonymize("/a", home="/") == "/a"


def test_a_check_that_could_not_be_made_is_in_the_report(qgis_app, monkeypatch):
    from axqua_plugin.gui import install_wizard

    class _Client:
        def install_overview(self):
            return {"host": {"description": "Windows 11", "supported": False},
                    "default_jobs": 4, "targets": [
                        {"target": "telemac", "title": "TELEMAC", "installed": {},
                         "running": None, "last": None, "default_folder": "~/opt"}]}

        def install_plan(self, target, options):
            raise RuntimeError("the installer scripts could not be downloaded: HTTP 504")

    def now(title, call, on_success=None, on_error=None, owner=None):
        try:
            answer = call()
        except Exception as exc:                 # noqa: BLE001 - as the task would
            on_error(exc)
        else:
            on_success(answer)

    monkeypatch.setattr(install_wizard, "run_async", now)
    shown = []
    monkeypatch.setattr(install_wizard, "exec_dialog",
                        lambda dialog: shown.append(dialog) or 0)
    wizard = install_wizard.InstallWizard(_Client(), "telemac",
                                          _Client().install_overview())
    wizard.go_next()
    assert wizard.plan is None and not wizard.report_button.isHidden()
    wizard.report()
    text = shown[0].report_text.toPlainText()
    assert "**The check could not be made:**" in text and "HTTP 504" in text
    wizard.deleteLater()
