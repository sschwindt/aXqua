"""The calibration report (calibration_report): posteriors, zone map, evidence, zones layer."""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from test_calibration_zones import LEFT, Y0, H, _case


def _calibrated_case(tmp_path):
    from axqua.bayescal import settle_zone_plan

    cfg = _case(tmp_path)
    cfg.ensure_dirs()
    folder = Path(cfg.calibration_dir)
    points = pd.DataFrame({"id": [1, 2, 3], "x": [LEFT, LEFT, LEFT],
                           "y": [Y0 + H / 2] * 3, "z": 0.0,
                           "WATER DEPTH_DATA": [0.8, 0.9, 1.0]})
    points.to_csv(folder / "measurements-calibration.csv", index=False)
    settle_zone_plan(cfg, [points], folder)            # zone 2 has no point: left out
    rng = np.random.default_rng(0)
    data = {"calibration_parameters": ["zone1"], "param_values": [[0.01, 0.3]],
            "posterior": [rng.uniform(0.01, 0.3, (500, 1)),
                          rng.normal(0.12, 0.02, (400, 1))],
            "marginal_optima": [np.array([0.1]), np.array([0.118])],
            "marginal_peak_spread": [np.array([0.01]), np.array([0.004])],
            "marginal_hdi": [np.array([[0.02, 0.29]]), np.array([[0.08, 0.16]])],
            "variance_reduction": [np.array([0.1]), np.array([0.94])],
            "identifiability_flags": [[[]], [[]]],
            "joint_optimum": [np.array([0.1]), np.array([0.125])],
            "log_BME": np.array([-60.0, -59.0]), "RE": np.array([1.0, 2.0]),
            "N_tp": np.array([16.0, 17.0])}
    out = folder / "auto-saved-results-HydroBayesCal" / "calibration-data" / "WATER DEPTH"
    out.mkdir(parents=True)
    with open(out / "BAL_dictionary.pkl", "wb") as handle:
        pickle.dump(data, handle)
    return cfg, folder


def test_the_report_has_the_posteriors_the_zone_map_and_the_evidence(tmp_path):
    import geopandas as gpd

    from axqua.calibration_report import write_report

    cfg, folder = _calibrated_case(tmp_path)
    written = write_report(cfg, folder)
    assert set(written) == {"posteriors", "evidence", "map", "zones", "summary"}
    for path in written.values():
        assert Path(path).stat().st_size > 0
    summary = json.loads(written["summary"].read_text())
    (zone1,) = summary["parameters"]
    # the per-parameter maximum, not the joint one
    assert zone1["value"] == 0.118 and zone1["joint_maximum"] == 0.125
    assert summary["estimate"] == "per-parameter posterior maximum"
    assert zone1["points"] == 3 and "well determined" in zone1["verdict"]
    assert summary["zone_plan"]["excluded_zones"] == [2]
    zones = gpd.read_file(written["zones"]).set_index("zone")
    assert zones.loc[1, "status"] == "calibrated" and zones.loc[1, "ks_cm"] == 11.8
    assert zones.loc[2, "status"] == "left out"
    assert zones.loc[2, "label"].startswith("Zone 2\nks ")


def test_a_folder_without_a_finished_calibration_has_no_report(tmp_path):
    from axqua.calibration_report import write_report

    cfg = _case(tmp_path)
    cfg.ensure_dirs()
    assert write_report(cfg, Path(cfg.calibration_dir)) == {}


def test_a_failing_figure_leaves_the_rest_of_the_report(tmp_path, monkeypatch, caplog):
    from axqua import calibration_report

    cfg, folder = _calibrated_case(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("no display")

    monkeypatch.setattr(calibration_report, "plot_zone_map", broken)
    written = calibration_report.write_report(cfg, folder)
    assert "map" not in written and "posteriors" in written and "summary" in written
    assert "map not written: no display" in caplog.text
