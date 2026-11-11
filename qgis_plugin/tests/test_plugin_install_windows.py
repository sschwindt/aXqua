"""The TELEMAC installation wizard on Windows: built in Windows itself, or inside WSL (the user's decision of 2026-11-10).

Without WSL, TELEMAC is built in Windows itself and the wizard offers nothing else; with WSL, it asks, and the settings follow the answer.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("qgis.core", reason="PyQGIS is not importable in this interpreter")

from axqua_plugin.core.runner_client import install_flags  # noqa: E402

NATIVE = r"C:\Users\student\opt\telemac"


def _overview(distro: str = "") -> dict:
    telemac = {"target": "telemac", "title": "TELEMAC", "installed": {}, "running": None, "last": None,
               "default_folder": "/home/student/opt" if distro else NATIVE,
               "default_folders": {"user": "/home/student/opt" if distro else NATIVE}}
    if distro:
        telemac["native_folder"] = NATIVE
    return {"host": {"system": "windows", "distro_wsl": distro, "supported": True,
                     "description": "Windows 11"},
            "default_jobs": 4, "targets": [telemac]}


class _Client:
    def install_overview(self):
        return {}


def _hidden(wizard, key) -> bool:
    return all(widget.isHidden() for widget in wizard._rows[key])


def test_without_wsl_telemac_is_built_in_windows_itself(qgis_app):
    from axqua_plugin.gui import install_wizard

    wizard = install_wizard.InstallWizard(_Client(), "telemac", _overview())
    route = wizard._widgets["route"]
    assert [route.itemData(i) for i in range(route.count())] == ["windows", "windows-mpi"]
    assert route.currentData() == "windows"                     # one core stays the default
    assert wizard._native() and wizard._widgets["folder"].text() == NATIVE
    assert all(_hidden(wizard, key) for key in ("scope", "telemac_examples", "salome"))
    assert not _hidden(wizard, "modules")
    wizard._widgets["modules"].setCurrentIndex(wizard._widgets["modules"].findData("all"))
    flags = install_flags(wizard.options())
    assert "--native" in flags and flags[flags.index("--modules") + 1] == "all"
    assert "--for-all-users" not in flags and "--telemac-examples" not in flags and "--mpi" not in flags
    route.setCurrentIndex(route.findData("windows-mpi"))
    assert wizard._native() and not _hidden(wizard, "modules") and _hidden(wizard, "scope")
    flags = install_flags(wizard.options())
    assert "--native" in flags and "--mpi" in flags and flags[flags.index("--modules") + 1] == "all"
    wizard.deleteLater()


def test_with_wsl_the_wizard_asks_and_the_settings_follow(qgis_app):
    from axqua_plugin.gui import install_wizard

    wizard = install_wizard.InstallWizard(_Client(), "telemac", _overview("Ubuntu"))
    route = wizard._widgets["route"]
    assert route.currentData() == "wsl" and not wizard._native()
    assert wizard._widgets["folder"].text() == "/home/student/opt" and _hidden(wizard, "modules")
    assert wizard._distro() == "Ubuntu" and "--native" not in install_flags(wizard.options())
    route.setCurrentIndex(route.findData("windows"))
    assert wizard._native() and wizard._distro() == ""          # a folder of Windows, no \\wsl.localhost
    assert wizard._widgets["folder"].text() == NATIVE and _hidden(wizard, "scope")
    assert "--native" in install_flags(wizard.options())
    route.setCurrentIndex(route.findData("wsl"))
    assert wizard._widgets["folder"].text() == "/home/student/opt" and not _hidden(wizard, "scope")
    wizard.deleteLater()


def test_other_programs_and_other_systems_have_no_such_rows(qgis_app):
    from axqua_plugin.gui import install_wizard

    linux = {"host": {"system": "linux", "supported": True}, "targets": []}
    assert "route" not in install_wizard.InstallWizard(_Client(), "telemac", linux)._widgets
    assert "route" not in install_wizard.InstallWizard(_Client(), "openfoam", _overview())._widgets
