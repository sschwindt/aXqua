"""The window *Calibration parameters* under QGIS, with a fake client (the user's order of
2026-11-20). The merge into the case file is tested without QGIS in
``test_plugin_calibration_parameters.py``."""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core", reason="PyQGIS is not importable in this interpreter")

from test_plugin_calibration_parameters import ANSWER  # noqa: E402


class _Client:
    def __init__(self):
        self.asked = []
        self.written = []

    def calibration_parameters(self, case, **given):
        self.asked.append(given)
        answer = dict(ANSWER)
        if given:
            kind, (low, high) = next(iter(given.items()))
            answer["preview"] = {kind: [{"name": "August 2026", "discharge": 55.4,
                                         "level": 374.5, "low": 374.5, "high": 376.69}]}
        return answer

    def case_read(self, case):
        return {"data": {"project": {"name": "c"}, "calibration": {"init_runs": 30}}}

    def case_write(self, case, data):
        self.written.append(data)
        return {"findings": []}


def test_the_window_sets_the_energy_slope_and_further_parameters(qgis_app, tmp_path,
                                                                 monkeypatch):
    from axqua_plugin.gui import calibration_parameters_wizard as wizard_module

    monkeypatch.setattr(wizard_module, "run_async",
                        lambda title, call, on_success=None, on_error=None, owner=None:
                        on_success(call()))

    class _Ctx:
        refreshed = 0

        def refresh_case(self):
            self.refreshed += 1

    ctx = _Ctx()
    ctx.client = _Client()
    wizard = wizard_module.CalibrationParametersWizard(ctx, tmp_path / "c.axq-case")
    assert [wizard.tabs.tabText(i) for i in range(wizard.tabs.count())] == [
        "Roughness zones", "Outflow level", "Further parameters", "Save"]
    # targets are what is measured, parameters what the calibration varies
    assert "calibration targets" in wizard_module.ZONES_TEXT
    assert wizard.zone_table.rowCount() == 2 and wizard.table_button.isEnabled()
    assert wizard.outflow_buttons["none"].isChecked()
    assert not wizard.range_box.isVisibleTo(wizard)
    assert "0.00198" in wizard.bed_slope_label.text()
    wizard.outflow_buttons["slope"].setChecked(True)
    assert (wizard.low.text(), wizard.high.text()) == ("1.00e-5", "0.00198")
    wizard.preview()
    assert ctx.client.asked[-1] == {"slope": (1e-5, 0.00198)}
    assert wizard.preview_table.item(0, 3).text() == "374.50 to 376.69"
    # what was typed for one way is kept while the other is looked at
    wizard.high.setText("2.0e-3")
    wizard.outflow_buttons["offset"].setChecked(True)
    assert (wizard.low.text(), wizard.high.text()) == ("-0.5", "1.5")
    wizard.outflow_buttons["slope"].setChecked(True)
    assert wizard.high.text() == "2.0e-3"
    # tick the diffusivity; the parameter of the case's own stays ticked
    checked = wizard_module.enum_value(wizard_module._Qt(), "CheckState.Checked", "Checked")
    wizard.further_table.item(0, 0).setCheckState(checked)
    assert [entry["name"] for entry in wizard.further_choice()] == [
        "VELOCITY DIFFUSIVITY", "MINE"]
    wizard.tabs.setCurrentIndex(3)
    assert "energy slope at the outlet from 1e-05 to 0.002" in wizard.summary.text()
    wizard.save()
    block = ctx.client.written[-1]["calibration"]
    assert block["outflow_slope"] == [1e-5, 2e-3] and block["init_runs"] == 30
    assert [p["name"] for p in block["parameters"]] == ["VELOCITY DIFFUSIVITY", "MINE"]
    assert wizard.saved and ctx.refreshed == 1
    # a range that is not one is named and nothing is written
    wizard.low.setText("3e-3")
    count = len(ctx.client.written)
    wizard.save()
    assert len(ctx.client.written) == count and "larger" in wizard.status.text()
    wizard.deleteLater()


def test_an_option_the_case_cannot_use_is_disabled_with_its_reason(qgis_app, tmp_path,
                                                                   monkeypatch):
    import copy

    from axqua_plugin.gui import calibration_parameters_wizard as wizard_module

    answer = copy.deepcopy(ANSWER)
    answer["outflow"]["slope"].update(available=False, reason="a rating by the method "
                                                              "'trapezoid'")
    wizard = wizard_module.CalibrationParametersWizard(type("C", (), {"client": None})(),
                                                       tmp_path / "c.axq-case", load=False)
    wizard.show_answer(answer)
    assert not wizard.outflow_buttons["slope"].isEnabled()
    assert "'trapezoid'" in wizard.outflow_reasons["slope"].text()
    assert wizard.outflow_buttons["offset"].isEnabled()
    wizard.deleteLater()
