"""The calibration report (calibration_report): posteriors, zone map, evidence, zones layer."""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

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
    # the per-parameter maximum, read off the histogram the report draws: neither the
    # joint maximum nor the peak HydroBayesCal stored (0.118)
    from axqua.validation import marginal_peak

    with open(next(folder.rglob("BAL_dictionary.pkl")), "rb") as handle:
        posterior = pickle.load(handle)["posterior"][-1]
    peak = marginal_peak(posterior[:, 0])[0]
    assert zone1["value"] == pytest.approx(peak) and zone1["joint_maximum"] == 0.125
    assert summary["estimate"] == "per-parameter posterior maximum"
    assert zone1["points"] == 3 and "well determined" in zone1["verdict"]
    assert summary["zone_plan"]["excluded_zones"] == [2]
    zones = gpd.read_file(written["zones"]).set_index("zone")
    assert zones.loc[1, "status"] == "calibrated"
    assert zones.loc[1, "ks_cm"] == pytest.approx(round(100 * peak, 2))
    assert zones.loc[2, "status"] == "left out"
    assert zones.loc[2, "label"].startswith("Zone 2\nks ")


def test_a_calibration_that_has_not_reached_its_last_iteration_has_a_report(tmp_path):
    """HydroBayesCal sets aside one entry per iteration and fills them in as it goes, so the
    last entry stays empty until the last iteration has run (the inn-KB15 trial after 18
    of 20). The report shows the last iteration that has run, and only the iterations
    that have run in its evidence figure."""
    from axqua.calibration_report import write_report

    cfg, folder = _calibrated_case(tmp_path)
    path = next(folder.rglob("BAL_dictionary.pkl"))
    with open(path, "rb") as handle:
        data = pickle.load(handle)
    for key, value in list(data.items()):
        if isinstance(value, list) and len(value) == 2:
            data[key] = value + [None]
    data["log_BME"] = np.array([-60.0, -59.0, 0.0])
    data["RE"] = [1.0, 2.0, None]
    data["N_tp"] = np.array([16.0, 17.0, 0.0])
    with open(path, "wb") as handle:
        pickle.dump(data, handle)
    written = write_report(cfg, folder)
    summary = json.loads(written["summary"].read_text())
    evidence = summary["evidence"]
    assert {key: evidence[key] for key in ("runs", "log_bme", "relative_entropy")} == {
        "runs": [16, 17], "log_bme": [-60.0, -59.0], "relative_entropy": [1.0, 2.0]}
    assert evidence["convergence"]["iterations"] == 1
    assert not evidence["convergence"]["converged"]
    assert summary["posterior_size"] == 400
    assert summary["parameters"][0]["joint_maximum"] == 0.125


def test_the_active_learning_has_converged_on_a_plateau_after_enough_iterations():
    """A plateau is five iterations over which the log evidence and the relative entropy
    each vary by at most 1 (Kass and Raftery's bare mention), after at least 25
    iterations of active learning; earlier plateaus are false (the inn-KB15 trial had one
    at iterations 5 to 9, then its evidence fell by 1.9)."""
    from axqua.calibration_report import (CONVERGENCE_MINIMUM, CONVERGENCE_WINDOW,
                                          _convergence_text, convergence)

    assert (CONVERGENCE_WINDOW, CONVERGENCE_MINIMUM) == (5, 25)
    runs = list(range(30, 61))
    # 30 iterations: wandering, then flat within 0.8 from iteration 21 on
    log_bme = [-60.0 - 2.0 * (i % 2) for i in range(21)] + [-68.0 + 0.8 * (i % 2)
                                                             for i in range(10)]
    entropy = [5.0 + (i % 3) for i in range(21)] + [4.5 + 0.1 * i for i in range(10)]
    state = convergence(log_bme, entropy, runs)
    assert state["converged"] and state["plateau"] and state["iterations"] == 30
    assert state["log_bme_range"] == pytest.approx(0.8)
    assert state["relative_entropy_range"] == pytest.approx(0.4)
    assert (state["plateau_since"], state["plateau_since_run"]) == (21, 51)
    assert _convergence_text(state).startswith("Converged:")
    # the same plateau after 20 iterations is not enough
    early = convergence(log_bme[:21 - 10] + log_bme[21:], entropy[:11] + entropy[21:])
    assert early["plateau"] and not early["converged"]
    assert "only 20 of the at least 25" in _convergence_text(early)
    # a jump of more than 1 in the last window
    jump = convergence(log_bme[:-1] + [-70.0], entropy)
    assert not jump["converged"] and jump["log_bme_range"] == pytest.approx(2.8)
    assert _convergence_text(jump).startswith("Not converged:")
    assert not convergence([-1.0, -1.0, -1.0], [1.0, 1.0, 1.0])["converged"]


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
