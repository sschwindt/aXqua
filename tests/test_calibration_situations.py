"""A calibration against measurements of several flow situations (``calibration.situations``).

Inn KB15, 2026-11-08: the campaigns of August 2026, November 2025 and March 2026 were
measured at 55.4, 47.8 and 45.8 m3/s; one set of roughness values is calibrated
against all of them with HydroBayesCal's multi-flow driver, and September 2025 is the
validation.
"""

from __future__ import annotations

import json
import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from axqua import bayescal, validation
from axqua.config import (CalibrationParameter, GroundTruthSource, ValidationSituation,
                          config_to_dict, dump_config, load_config)
from axqua.core.errors import ConfigError
from test_validation import CAS, LIQUIDS, TABLE


def _source(name):
    return GroundTruthSource(category="hydraulics", kind="points",
                             positions=Path(f"{name}.gpkg"))


@pytest.fixture
def built(fake_case):
    cfg = fake_case
    cfg.model_path(cfg.cas_file).write_text(CAS)
    cfg.model_path("friction.tbl").write_text(TABLE)
    cfg.model_path(cfg.liquid_boundaries_json).write_text(json.dumps(LIQUIDS))
    cfg.calibration.situations = [
        ValidationSituation(name="August 2026", prescribed_flowrate=4.8,
                            prescribed_elevation=100.9, sources=[_source("aug")]),
        ValidationSituation(name="March 2026", prescribed_flowrate=1.2,
                            prescribed_elevation=99.7, sources=[_source("mar")])]
    return cfg


def test_the_situations_are_those_of_the_case_file_and_the_case_s_own_data(built):
    assert [s.name for s in bayescal.calibration_situations(built)] == [
        "August 2026", "March 2026"]
    built.ground_truth.sources = [_source("sep")]
    own, *_ = bayescal.calibration_situations(built)
    assert not own.sources and own.prescribed_flowrate == 2.4
    built.calibration.situations = []
    assert bayescal.calibration_situations(built) == []     # the single-flow calibration


def test_a_situation_s_run_is_the_built_case_with_other_boundary_values(built):
    name, q = bayescal.write_situation_cas(built, built.calibration.situations[0],
                                           Path(built.calibration_dir))
    assert (name, q) == ("steady2d-august-2026.cas", pytest.approx(4.8))
    before = CAS.splitlines()
    after = built.model_path(name).read_text().splitlines()
    changed = {a.split(":")[0].strip() for a, b in zip(before, after) if a != b}
    assert changed == {"TITLE", "RESULTS FILE", "PRESCRIBED FLOWRATES",
                       "PRESCRIBED ELEVATIONS"}
    text = "\n".join(after)
    assert "RESULTS FILE : r2d-august-2026.slf" in text
    assert "PRESCRIBED FLOWRATES : 1.6000;0.;3.2000" in text     # the case's split
    assert "PRESCRIBED ELEVATIONS : 0.;100.9000;0." in text
    assert built.model_path(built.cas_file).read_text() == CAS


def test_a_hotstarted_situation_continues_from_the_steady_result(built, monkeypatch):
    built.model_path(built.cas_file).write_text(
        CAS + "PREVIOUS COMPUTATION FILE : initial-conditions.slf\n"
              "INITIAL TIME SET TO ZERO : YES\n")
    built.calibration.hotstart = True
    seen = {}
    monkeypatch.setattr(bayescal, "_steady_result", lambda cfg: None)

    def duration(cfg):
        seen["q"] = cfg.boundaries.prescribed_flowrate
        return 3500.0

    monkeypatch.setattr(bayescal, "calibration_hotstart_duration", duration)
    name, _q = bayescal.write_situation_cas(built, built.calibration.situations[1],
                                            Path(built.calibration_dir))
    text = built.model_path(name).read_text()
    assert seen["q"] == 1.2                       # residence time at its own discharge
    assert "PREVIOUS COMPUTATION FILE : r2d.slf" in text
    assert "DURATION : 3500.0" in text
    assert "RESULTS FILE : r2d-march-2026.slf" in text            # never its seed
    assert "PRESCRIBED FLOWRATES : 0.4000;0.;0.8000" in text


def _prepared(built, monkeypatch, tmp_path):
    def points(cfg, situation, folder, **_):
        csv = Path(folder) / f"points-{validation.slug(situation.name)}.csv"
        table = pd.DataFrame({"id": [1], "x": [0.0], "y": [0.0], "z": [0.0],
                              "WATER DEPTH_DATA": [1.0], "WATER DEPTH_ERROR": [0.1]})
        table.to_csv(csv, index=False)
        return csv, table

    monkeypatch.setattr(bayescal, "situation_points", points)
    monkeypatch.setattr(bayescal, "stage_driver",
                        lambda folder, name, **_: tmp_path / name)
    return bayescal.run_situation_calibration(built, prepare_only=True)


def test_the_calibration_fits_all_situations_with_a_multi_output_emulator(
        built, monkeypatch, tmp_path):
    built.calibration.parameters = [CalibrationParameter("zone4", 0.01, 0.3),
                                    CalibrationParameter("ks", 0.02, 0.3)]   # OpenFOAM's
    assert _prepared(built, monkeypatch, tmp_path) == 0
    config = (built.calibration_path(bayescal.MULTIFLOW)
              / "config_Telemac_multiflow.py").read_text()
    assert "'control_file': 'steady2d-august-2026.cas'" in config
    assert "'control_file': 'steady2d-march-2026.cas'" in config
    assert "'results_filename_base': 'r2d-march-2026'" in config
    assert "'multitask_selection': 'variables'" in config
    assert "'parameters': ['zone4']" in config               # not the OpenFOAM ks


def test_a_situation_without_measurements_or_a_name_of_its_own_is_refused(
        built, monkeypatch, tmp_path):
    built.calibration.situations[1].sources = []
    with pytest.raises(ConfigError, match="without measurements"):
        _prepared(built, monkeypatch, tmp_path)
    built.calibration.situations[1].sources = [_source("mar")]
    built.calibration.situations[1].name = "august-2026"
    with pytest.raises(ConfigError, match="same name"):
        _prepared(built, monkeypatch, tmp_path)


def test_the_validation_takes_the_newer_calibration_also_at_several_discharges(built):
    def result(root, values, age):
        folder = root / validation.RESULTS / "calibration-data" / "Q"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "BAL_dictionary.pkl"
        with open(path, "wb") as handle:
            pickle.dump({"calibration_parameters": ["zone4"],
                         "joint_optimum": [np.array(values)]}, handle)
        os.utime(path, (age, age))

    root = Path(built.calibration_dir)
    result(root, [0.1], 1_000_000)
    result(root / bayescal.MULTIFLOW, [0.2], 2_000_000)
    assert validation.calibrated_parameters(built).as_dict() == {"zone4": 0.2}


def test_situations_come_back_from_a_dump(built, tmp_path):
    built.calibration.multitask_selection = "SO_sequential"
    dump_config(built, tmp_path / "again.axq-case")
    again = load_config(tmp_path / "again.axq-case")
    assert [(s.name, s.prescribed_flowrate) for s in again.calibration.situations] == [
        ("August 2026", 4.8), ("March 2026", 1.2)]
    assert again.calibration.multitask_selection == "SO_sequential"
    assert config_to_dict(again)["calibration"]["situations"][0]["name"] == "August 2026"


def test_the_calibration_job_runs_the_situations(built, monkeypatch):
    from axqua.solvers.telemac.backend import TelemacBackend

    seen = {}

    def situations(cfg, **options):
        seen.update(options)
        return 0

    monkeypatch.setattr(bayescal, "run_situation_calibration", situations)
    monkeypatch.setattr(bayescal, "run_single_flow_calibration",
                        lambda *a, **k: pytest.fail("ran the single-flow calibration"))
    assert TelemacBackend()._calibration(built, prepare_only=True) == 0
    assert seen == {"prepare_only": True, "resume": False, "force": False}
    # the options of the job reach the situations as they reach a single flow
    seen.clear()
    TelemacBackend()._calibration(built, force=True,
                                  calibration_quantities=["SCALAR VELOCITY"],
                                  extraction_quantities=["WATER DEPTH"],
                                  vel_err_floor=0.05, depth_err_floor=0.02)
    assert seen["calibration_quantities"] == ("SCALAR VELOCITY",)
    assert seen["extraction_quantities"] == ("WATER DEPTH",)
    assert (seen["vel_err_floor"], seen["depth_err_floor"], seen["force"]) == (
        0.05, 0.02, True)


def test_every_calibration_target_is_extracted(built):
    targets = bayescal._set_quantities(built, ["WATER DEPTH", "SCALAR VELOCITY"],
                                       ["WATER DEPTH"])
    assert targets == ["WATER DEPTH", "SCALAR VELOCITY"]
    assert built.calibration.extraction_quantities == ["WATER DEPTH", "SCALAR VELOCITY"]


def test_the_case_s_own_situation_has_the_case_s_outflow_level(built):
    built.ground_truth.sources = [_source("sep")]
    own = bayescal.calibration_situations(built)[0]
    assert (own.prescribed_flowrate, own.prescribed_elevation) == (2.4, 100.0)
    # its steering file is written under outflow_condition: elevation
    name, q = bayescal.write_situation_cas(built, own, Path(built.calibration_dir))
    assert q == pytest.approx(2.4)
    assert "PRESCRIBED ELEVATIONS : 0.;100.0000;0." in built.model_path(name).read_text()
    # a case with a rating curve prescribes no level of its own to the situation
    built.boundaries.outflow_condition = "stage_discharge"
    assert bayescal.calibration_situations(built)[0].prescribed_elevation is None


def test_the_case_s_own_situation_takes_the_discharges_of_its_inflow_lines(built):
    built.ground_truth.sources = [_source("sep")]
    built.boundaries.prescribed_flowrate = None
    own = bayescal.calibration_situations(built)[0]
    assert own.inflows == {1: 0.8, 3: 1.6} and own.total_flowrate == pytest.approx(2.4)
    assert bayescal.case_discharge(built) == pytest.approx(2.4)


def test_a_calibration_situation_without_a_discharge_is_refused(built, monkeypatch,
                                                               tmp_path):
    built.calibration.situations[1].prescribed_flowrate = None
    with pytest.raises(ConfigError, match="without a discharge") as caught:
        _prepared(built, monkeypatch, tmp_path)
    assert caught.value.subject == "calibration.situations.prescribed_flowrate"
    # the error of the boundary values names the role of the situation
    with pytest.raises(ConfigError, match="the calibration situation 'March 2026'"):
        validation.with_boundaries_of(built, built.calibration.situations[1],
                                      "", Path(built.calibration_dir),
                                      role="calibration")


def test_the_positional_error_is_read_only_at_the_case_s_discharge(built, monkeypatch,
                                                                  caplog):
    seen = []
    monkeypatch.setattr(bayescal, "build_velocity_csv",
                        lambda cfg, **k: seen.append(k["positional"]) or (None, None))
    built.ground_truth.position_quality = "rtk-float"
    folder = Path(built.calibration_dir)
    august = built.calibration.situations[0]                 # 4.8 m3/s, case 2.4
    bayescal.situation_points(built, august, folder)
    own = ValidationSituation(name="case discharge", prescribed_flowrate=2.4)
    bayescal.situation_points(built, own, folder)            # the case's measurements
    august.prescribed_flowrate = 2.4                         # measured at the case's Q
    bayescal.situation_points(built, august, folder)
    assert seen == [False, True, True]
    assert "positional error is left out" in caplog.text


def test_a_case_with_only_situations_counts_as_configured_for_calibration(built):
    from axqua.solvers.telemac.spec import _calibration_configured

    built.calibration.parameters = [CalibrationParameter("zone4", 0.01, 0.3)]
    assert built.ground_truth.sources == []
    assert _calibration_configured(built)


# --------------------------------------------------------------------------- #
# model error at points the model cannot reach (calibration.structural_error)
# --------------------------------------------------------------------------- #
def test_only_the_misfit_outside_the_simulated_range_is_added():
    points = pd.DataFrame({"id": [1, 2, 3], "x": [0.0] * 3, "y": [0.0] * 3,
                           "WATER DEPTH_DATA": [0.50, 0.45, 0.20],
                           "WATER DEPTH_ERROR": [0.02] * 3})
    runs = [{"WATER DEPTH": 0.40}, {"WATER DEPTH": 0.48}]
    outputs = {"1": runs, "2": runs, "3": runs}
    table = bayescal.unreachable_misfit(points, outputs, ["WATER DEPTH"])
    assert table["WATER DEPTH_UNREACHABLE"].tolist() == pytest.approx([0.02, 0.0, 0.20])
    assert table["WATER DEPTH_ERROR"].tolist() == pytest.approx(
        [np.hypot(0.02, 0.02), 0.02, np.hypot(0.02, 0.20)])
    assert table["WATER DEPTH_MEASUREMENT_ERROR"].tolist() == [0.02] * 3
    assert table["id"].tolist() == [1, 2, 3]          # integer ids stay integers


def test_a_group_no_single_run_fits_is_widened_and_a_fitting_one_is_not():
    # each point of 'a' is reached by one run, but no run reaches both
    a = pd.DataFrame({"id": [1, 2], "x": [0.0] * 2, "y": [0.0] * 2,
                      "WATER DEPTH_DATA": [1.0, 1.0], "WATER DEPTH_ERROR": [0.01] * 2})
    b = pd.DataFrame({"id": [7], "x": [0.0], "y": [0.0],
                      "WATER DEPTH_DATA": [2.0], "WATER DEPTH_ERROR": [0.01]})
    outputs = {"a": {"1": [{"WATER DEPTH": 1.0}, {"WATER DEPTH": 0.5}],
                     "2": [{"WATER DEPTH": 0.5}, {"WATER DEPTH": 1.0}]},
               "b": {"7": [{"WATER DEPTH": 2.0}, {"WATER DEPTH": 2.0}]}}
    points = {"a": a, "b": b}
    tables = {tag: bayescal.unreachable_misfit(points[tag], outputs[tag], ["WATER DEPTH"])
              for tag in points}
    assert tables["a"]["WATER DEPTH_UNREACHABLE"].tolist() == [0.0, 0.0]
    best = bayescal.joint_misfit(tables, points, outputs, ["WATER DEPTH"], share=0.0)
    assert best == 1
    # chi-square per point at run 1: (0 + 0.5**2 / 0.01**2) / 2 = 1250
    assert tables["a"]["WATER DEPTH_VARIANCE_FACTOR"].tolist() == pytest.approx([1250] * 2)
    assert tables["a"]["WATER DEPTH_ERROR"].tolist() == pytest.approx(
        [np.sqrt(1250) * 0.01] * 2)
    assert tables["a"]["WATER DEPTH_BEST_FIT"].tolist() == [1.0, 0.5]
    assert tables["b"]["WATER DEPTH_VARIANCE_FACTOR"].tolist() == [1.0]
    assert tables["b"]["WATER DEPTH_ERROR"].tolist() == pytest.approx([0.01])


def test_the_flat_layout_of_hydrobayescal_and_ids_that_are_not_numbers_are_read():
    """HydroBayesCal writes the flat matrix where its detailed table has gaps."""
    points = pd.DataFrame({"id": ["P1", "P2"], "x": [0.0] * 2, "y": [0.0] * 2,
                           "WATER DEPTH_DATA": [1.0, 2.0], "WATER DEPTH_ERROR": [0.1] * 2,
                           "SCALAR VELOCITY_DATA": [0.5, 0.6],
                           "SCALAR VELOCITY_ERROR": [0.05] * 2})
    flat = {"model_evaluations": [[0.9, 0.4, 2.1, 0.7], [1.1, 0.5, 1.9, 0.6]],
            "calibration_quantities": ["WATER DEPTH", "SCALAR VELOCITY"], "n_runs": 2}
    outputs = bayescal.per_point_outputs(flat, points)
    assert outputs["P2"] == [{"WATER DEPTH": 2.1, "SCALAR VELOCITY": 0.7},
                             {"WATER DEPTH": 1.9, "SCALAR VELOCITY": 0.6}]
    table = bayescal.unreachable_misfit(points, outputs,
                                        ["WATER DEPTH", "SCALAR VELOCITY"])
    assert table["id"].tolist() == ["P1", "P2"]
    assert table["SCALAR VELOCITY_MODEL_MAX"].tolist() == pytest.approx([0.5, 0.7])
    per_point = {"P1": outputs["P1"], "P2": outputs["P2"]}
    assert bayescal.per_point_outputs(per_point, points) is per_point
    with pytest.raises(ValueError, match="values per run"):
        bayescal.per_point_outputs(dict(flat, calibration_quantities=["WATER DEPTH"]),
                                   points)


def test_the_share_hydrobayescal_adds_is_scaled_but_not_written_twice():
    points = {"a": pd.DataFrame({"id": [1], "x": [0.0], "y": [0.0],
                                 "WATER DEPTH_DATA": [1.0], "WATER DEPTH_ERROR": [0.0]})}
    outputs = {"a": {"1": [{"WATER DEPTH": 0.6}, {"WATER DEPTH": 1.2}]}}
    tables = {"a": bayescal.unreachable_misfit(points["a"], outputs["a"], ["WATER DEPTH"])}
    bayescal.joint_misfit(tables, points, outputs, ["WATER DEPTH"], share=0.1)
    # best run 2: chi-square 0.2**2 / 0.1**2 = 4; the variance HydroBayesCal then uses
    # (0.1 of the value plus the written error) is four times the measured one
    error = tables["a"]["WATER DEPTH_ERROR"].iloc[0]
    assert 0.1 ** 2 + error ** 2 == pytest.approx(4 * 0.1 ** 2)


def _two_passes(built, monkeypatch, tmp_path, *, resume=False):
    built.calibration.structural_error = "unreachable"
    built.calibration.calibration_quantities = ["WATER DEPTH"]
    launched = []

    def launch(cfg, driver, config_path, **kwargs):
        text = Path(config_path).read_text()
        launched.append(("'only_bal_mode': True" in text,
                         "'complete_bal_mode': True" in text))
        if not launched[-1][0]:                    # the first pass saves its runs
            for tag in ("august-2026", "march-2026"):
                restart = (Path(config_path).parent / f"flow-{tag}"
                           / bayescal.RESTART_OUTPUTS)
                restart.parent.mkdir(parents=True, exist_ok=True)
                restart.write_text(json.dumps({"1": [{"WATER DEPTH": 0.7},
                                                     {"WATER DEPTH": 0.8}]}))
        return 0

    monkeypatch.setattr(bayescal, "launch", launch)
    monkeypatch.setattr(bayescal, "situation_points",
                        lambda cfg, situation, folder, **_: _points(folder, situation))
    monkeypatch.setattr(bayescal, "stage_driver",
                        lambda folder, name, **_: tmp_path / name)
    code = bayescal.run_situation_calibration(built, resume=resume)
    return code, launched


def _points(folder, situation):
    csv = Path(folder) / f"points-{validation.slug(situation.name)}.csv"
    table = pd.DataFrame({"id": [1], "x": [0.0], "y": [0.0], "z": [0.0],
                          "WATER DEPTH_DATA": [1.0], "WATER DEPTH_ERROR": [0.02]})
    table.to_csv(csv, index=False)
    return csv, table


def test_the_active_learning_runs_with_the_errors_the_initial_runs_complete(
        built, monkeypatch, tmp_path):
    code, launched = _two_passes(built, monkeypatch, tmp_path)
    # initial simulations only, then the active learning on the saved ones
    assert (code, launched) == (0, [(False, False), (True, True)])
    folder = built.calibration_path(bayescal.MULTIFLOW)
    points = pd.read_csv(folder / "points-march-2026.csv")
    assert points["WATER DEPTH_ERROR"].tolist() == pytest.approx([np.hypot(0.02, 0.2)])
    record = pd.read_csv(folder / "model-error-march-2026.csv")
    assert record["WATER DEPTH_UNREACHABLE"].tolist() == pytest.approx([0.2])


def test_a_resumed_calibration_uses_the_saved_runs_and_needs_them(
        built, monkeypatch, tmp_path):
    with pytest.raises(ConfigError, match="no saved initial simulations"):
        _two_passes(built, monkeypatch, tmp_path, resume=True)
    _two_passes(built, monkeypatch, tmp_path)               # saves the runs
    code, launched = _two_passes(built, monkeypatch, tmp_path, resume=True)
    assert (code, launched) == (0, [(True, True)])


def test_an_unknown_structural_error_is_refused(built, monkeypatch, tmp_path):
    built.calibration.structural_error = "everywhere"
    with pytest.raises(ConfigError, match="structural_error"):
        _prepared(built, monkeypatch, tmp_path)


def test_the_structural_error_comes_back_from_a_dump(built, tmp_path):
    built.calibration.structural_error = "unreachable"
    dump_config(built, tmp_path / "again.axq-case")
    assert load_config(tmp_path / "again.axq-case").calibration.structural_error == \
        "unreachable"


def test_a_run_counts_as_one_of_the_case_only_where_it_works_in_its_folder(
        tmp_path, monkeypatch):
    """A calibration of another case on the same computer runs steady2d-*.cas too."""
    import subprocess

    from axqua import hbc

    model, other = tmp_path / "model", tmp_path / "other"
    (model / "steady2d.cas_2026-11-10").mkdir(parents=True)
    other.mkdir()
    line = f"{os.getpid()} python telemac2d.py steady2d-august-2026.cas\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, stdout=line, stderr=""))
    monkeypatch.chdir(other)
    assert not hbc.solver_running("telemac2d", model, extra=("steady2d",))
    monkeypatch.chdir(model / "steady2d.cas_2026-11-10")
    assert hbc.solver_running("telemac2d", model, extra=("steady2d",))
