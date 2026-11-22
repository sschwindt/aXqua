"""The window *Calibration parameters* (the user's order of 2026-11-20): the merge into the
case file without QGIS, the window with a fake client under QGIS."""

from __future__ import annotations

import pytest

from axqua_plugin.core import calibration_parameters as cp

ANSWER = {
    "zones": [{"zone": 1, "name": "zone1", "ks": 0.155, "ks_min": 0.01, "ks_max": 0.3,
               "calibrated": True},
              {"zone": 2, "name": "zone2", "ks": 0.5, "ks_min": 0.5, "ks_max": 0.5,
               "calibrated": False}],
    "roughness_table": "/case/user-sources/geodata/roughness-table.csv",
    "outflow": {"current": {"kind": None, "range": None}, "bed_slope": 0.00198,
                "slope": {"available": True, "reason": "", "remedy": "",
                          "suggested": [1e-5, 0.00198]},
                "offset": {"available": True, "reason": "", "remedy": "",
                           "suggested": [-0.5, 1.5]}},
    "further": [{"name": "VELOCITY DIFFUSIVITY", "label": "VELOCITY DIFFUSIVITY",
                 "module": "telemac2d", "min": 1e-4, "max": 0.1,
                 "tips": "turbulent eddy viscosity", "selected": False, "range": None},
                {"name": "MINE", "label": "MINE", "module": "case", "min": 1.0,
                 "max": 2.0, "tips": "mine", "selected": True, "range": [1.0, 2.0]}],
    "discharges": [{"name": "August 2026", "discharge": 55.4}],
}


def test_the_choice_goes_into_the_calibration_block_and_keeps_the_rest():
    data = {"project": {"name": "c"},
            "calibration": {"outflow_offset": [0.0, 1.0], "init_runs": 30,
                            "parameters": [{"name": "zone4", "min": 0.05, "max": 0.5},
                                           {"name": "MINE", "min": 1, "max": 2,
                                            "comment": "mine"}]}}
    out = cp.merged(data, "slope", (1e-5, 2e-3),
                    [{"name": "MINE", "min": 1.5, "max": 3.0}])
    block = out["calibration"]
    # one way to move the outflow level, never both
    assert block["outflow_slope"] == [1e-5, 2e-3] and "outflow_offset" not in block
    assert block["init_runs"] == 30 and out["project"] == {"name": "c"}
    # a zone entry of the parameter list stays; a further one keeps its comment
    assert block["parameters"] == [{"name": "zone4", "min": 0.05, "max": 0.5},
                                   {"name": "MINE", "min": 1.5, "max": 3.0,
                                    "comment": "mine"}]
    none = cp.merged(out, "none", None, [])
    assert "outflow_slope" not in none["calibration"]
    assert none["calibration"]["parameters"] == [{"name": "zone4", "min": 0.05,
                                                  "max": 0.5}]
    assert "parameters" not in cp.merged({"calibration": {}}, "none", None, [])["calibration"]
    with pytest.raises(ValueError):
        cp.merged({}, "both", None, [])


@pytest.mark.parametrize("low, high, positive, message", [
    ("a", "1", False, "two numbers"), ("2", "1", False, "larger"),
    ("0", "1e-3", True, "larger than 0")])
def test_a_range_that_is_not_one_is_named(low, high, positive, message):
    with pytest.raises(ValueError, match=message):
        cp.parse_range(low, high, positive=positive)
    assert cp.parse_range("1,0e-5", "2e-3", positive=True) == (1e-5, 2e-3)


def test_the_summary_says_what_save_writes():
    lines = cp.summary("slope", (1e-5, 2e-3), [{"name": "MINE", "min": 1, "max": 2}])
    assert lines[0].startswith("Outflow level: energy slope at the outlet from 1e-05 to 0.002")
    assert "calibration.outflow_slope" in lines[0] and "MINE: 1 to 2" in lines[1]
    assert cp.zone_text(ANSWER["zones"][0]) == "calibrated between 1 and 30 cm"
    assert cp.zone_text(ANSWER["zones"][1]) == "fixed at 50 cm"
