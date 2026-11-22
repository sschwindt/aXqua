"""What a case can calibrate besides its roughness zones (calibration_parameters, the
answer of ``axqua calibration <case> --parameters`` for the plugin's wizard)."""

from __future__ import annotations

import json

import pytest

from axqua import calibration_parameters as cp
from axqua.config import CalibrationParameter, ValidationSituation
from axqua.core.errors import ConfigError
from axqua.solvers.telemac import outflow_offset
from test_outflow_offset import _section, sloped  # noqa: F401 - the fixture


def test_the_energy_slope_is_offered_with_the_bed_slope_as_its_upper_end(sloped):  # noqa: F811
    """The user's order of 2026-11-20: the slope of the Inn KB15 example, set in a wizard."""
    sloped.calibration.outflow_slope = None
    flow = cp.outflow(sloped)
    assert flow["current"] == {"kind": None, "range": None}
    assert flow["slope"]["available"] and flow["offset"]["available"]
    assert flow["bed_slope"] == pytest.approx(0.002)
    assert flow["slope"]["suggested"] == pytest.approx([cp.SLOPE_FLOOR, 0.002])
    assert flow["offset"]["suggested"] == list(cp.SUGGESTED_OFFSET)


def test_the_slope_is_not_offered_where_the_level_is_not_the_rating_of_the_section(
        sloped):  # noqa: F811
    sloped.boundaries.rating_method = "trapezoid"
    flow = cp.outflow(sloped)
    assert not flow["slope"]["available"] and "'trapezoid'" in flow["slope"]["reason"]
    assert flow["offset"]["available"]
    assert flow["current"] == {"kind": "slope", "range": [1.0e-5, 2.0e-3]}
    # the rules are those of the calibration itself
    assert outflow_offset.problem(sloped, "slope")[0] == flow["slope"]["reason"]
    with pytest.raises(ConfigError, match="'trapezoid'"):
        outflow_offset.check(sloped)


def test_the_preview_gives_the_level_of_each_situation(sloped):  # noqa: F811
    sloped.calibration.situations = [
        ValidationSituation(name="high", prescribed_flowrate=30.0),
        ValidationSituation(name="low", prescribed_flowrate=10.0)]
    rows = cp.preview(sloped, "slope", 1.0e-5, 2.0e-3)
    assert [row["name"] for row in rows] == ["high", "low"]
    section = _section()
    for row in rows:
        q = row["discharge"]
        assert row["level"] == pytest.approx(section.stage(q), abs=1e-3)
        # a smaller slope holds the water higher; the bed slope is the rating itself
        assert row["low"] == pytest.approx(section.stage(q, 2.0e-3), abs=1e-3)
        assert row["high"] == pytest.approx(section.stage(q, 1.0e-5), abs=1e-3)
        assert row["high"] > row["low"]
    offset = cp.preview(sloped, "offset", -0.5, 1.5)
    assert [r["high"] - r["level"] for r in offset] == pytest.approx([1.5, 1.5], abs=2e-3)
    with pytest.raises(ConfigError, match="larger than 0"):
        cp.preview(sloped, "slope", 0.0, 1.0e-3)
    with pytest.raises(ConfigError, match="not a range"):
        cp.preview(sloped, "offset", 1.0, 0.5)


def test_further_parameters_fit_the_case_and_keep_its_own(fake_case):
    fake_case.calibration.parameters = [
        CalibrationParameter(name="VELOCITY DIFFUSIVITY", min=0.01, max=0.05),
        CalibrationParameter(name="SOME KEYWORD", min=1.0, max=2.0, comment="mine"),
        CalibrationParameter(name="zone2", min=0.01, max=0.2)]
    found = {entry["name"]: entry for entry in cp.further(fake_case)}
    assert found["VELOCITY DIFFUSIVITY"]["selected"]
    assert found["VELOCITY DIFFUSIVITY"]["range"] == [0.01, 0.05]
    assert found["SOME KEYWORD"]["module"] == "case" and found["SOME KEYWORD"]["selected"]
    # zones belong to the roughness table, two-valued keywords and per-class entries out
    assert "zone2" not in found
    assert "NON-DIMENSIONAL DISPERSION COEFFICIENTS" not in found
    assert not any(entry["module"] in ("gaia", "gainlose", "telemac3d", "openfoam")
                   for entry in found.values())
    fake_case.morphodynamics.enabled = True
    assert any(entry["module"] == "gaia" for entry in cp.further(fake_case))


def test_the_command_answers_for_the_wizard(sloped, tmp_path, monkeypatch, capsys):  # noqa: F811
    from axqua import calibrationcli
    from axqua.config import dump_config

    path = tmp_path / "case.axq-case"
    dump_config(sloped, path)
    monkeypatch.setattr(outflow_offset, "section_of", lambda cfg: _section())
    assert calibrationcli.run_calibration(
        [str(path), "--parameters", "--slope", "1e-5", "2e-3", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert {"zones", "outflow", "further", "discharges", "preview"} <= set(data)
    assert data["outflow"]["current"]["kind"] == "slope"
    assert calibrationcli.run_calibration([str(path), "--slope", "1e-5", "2e-3",
                                           "--json"]) != 0
    assert "--parameters" in json.loads(capsys.readouterr().out)["error"]["remedy"]
