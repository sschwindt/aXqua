"""How many runs a calibration makes (:func:`axqua.calibration.run_budget`).

The user's order of 2026-11-23: the number of initial runs follows a statistical rule,
not the 30 runs of one hydraulic study (Schwindt et al. 2023), which were too few. The
rule is ten runs per calibration parameter (Loeppky, Sacks and Welch 2009), rounded up
to a power of two because the initial design is a Sobol sequence.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_calibration_zones import _case


def _calibration(**values):
    from axqua.config import Calibration

    return Calibration(**values)


@pytest.mark.parametrize("n_parameters,runs", [(1, 16), (2, 32), (3, 32), (4, 64),
                                               (6, 64), (7, 128), (13, 256)])
def test_ten_runs_per_parameter_rounded_up_to_a_power_of_two(n_parameters, runs):
    from axqua.calibration import recommended_initial_runs

    assert recommended_initial_runs(n_parameters) == runs


def test_the_rule_is_that_of_hydrobayescal():
    initial_design = pytest.importorskip("hydroBayesCal.surrogate.initial_design")
    from axqua.calibration import recommended_initial_runs

    for n in range(1, 12):
        assert recommended_initial_runs(n) == \
            initial_design.recommended_init_runs(n)["recommended"]


def test_a_case_that_sets_nothing_gets_the_rule_and_no_limit_to_the_active_learning():
    """Inn KB15: three roughness zones and the energy slope at the outlet. The user's
    rule of 2026-11-23: enough iterations for the evidence and the relative entropy to
    converge, not a maximum fixed beforehand."""
    from axqua.calibration import default_iterations, pass_ceiling, run_budget
    from axqua.calibration_report import CONVERGENCE_MINIMUM

    assert _calibration().until_converged
    assert run_budget(_calibration(), 4) == (64, None, 64)
    # what a HydroBayesCal configuration states: the first pass of active learning
    assert pass_ceiling(64, None) == 64 + CONVERGENCE_MINIMUM and pass_ceiling(64, 70) == 70
    # a fixed number of iterations only where the case asks for one
    assert default_iterations() == 2 * CONVERGENCE_MINIMUM == 50
    assert run_budget(_calibration(until_converged=False), 4) == (64, 114, 64)
    assert run_budget(_calibration(until_converged=False), 2) == (32, 82, 32)


def test_the_numbers_of_the_case_are_kept():
    from axqua.calibration import run_budget

    assert run_budget(_calibration(init_runs=128, max_runs=200), 4) == (128, 200, 64)
    assert run_budget(_calibration(init_runs=128, init_runs_min=32), 4) == (128, None, 32)
    # the first block is never larger than the design
    assert run_budget(_calibration(init_runs=8, max_runs=15), 2) == (8, 15, 8)


def test_fewer_initial_runs_than_the_rule_are_named(caplog):
    from axqua.calibration import check_run_budget

    with caplog.at_level(logging.WARNING, logger="axqua.calibration_design"):
        assert check_run_budget(_calibration(init_runs=30, max_runs=85), 4) == (30, 85, 30)
    assert "below the 64 initial runs that 4 calibration parameters need" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="axqua.calibration_design"):
        check_run_budget(_calibration(), 4)
    assert caplog.text == ""


def test_a_budget_without_active_learning_is_refused():
    from axqua.calibration import check_run_budget
    from axqua.core.errors import ConfigError

    with pytest.raises(ConfigError) as caught:
        check_run_budget(_calibration(max_runs=50), 4)
    assert caught.value.subject == "calibration.max_runs"
    assert "until it converges" in caught.value.remedy


def test_the_budget_is_recorded_and_reaches_both_configurations(tmp_path):
    from axqua import bayescal
    from axqua.calibration import emit_hbc_config
    from axqua.config import CalibrationParameter

    cfg = _case(tmp_path)
    cfg.ensure_dirs()
    cfg.calibration.init_runs = None
    cfg.calibration.max_runs = None
    parameters = [CalibrationParameter(name=f"zone{k}", min=0.01, max=0.3)
                  for k in (1, 3, 5)] + [CalibrationParameter(name="zone6", min=0.1,
                                                                 max=0.5)]
    runs = bayescal.record_run_budget(cfg, tmp_path, parameters)
    assert runs == (64, None, 64)
    record = json.loads((tmp_path / bayescal.RUN_BUDGET).read_text())
    assert record["init_runs_from"] == "rule" and record["max_runs"] is None
    assert record["max_runs_from"] == "none: until the evidence converges"
    assert record["parameters"] == ["zone1", "zone3", "zone5", "zone6"]
    text = Path(emit_hbc_config(cfg, None, parameters=parameters)).read_text()
    assert "'init_runs': 64" in text and "'max_runs': 89" in text
    assert "'init_runs_min': 64" in text
    # a pass of the active learning names its own budget; the first block follows it
    text = Path(emit_hbc_config(cfg, None, parameters=parameters,
                                budget=(64, 89, False))).read_text()
    assert "'max_runs': 89" in text and "'adaptive_init_runs': False" in text
    flows = [bayescal.FlowSpec(name="a", discharge=1.0, kind="csv",
                               values=tmp_path / "a.csv")]
    path = bayescal.emit_multiflow_config(cfg, tmp_path, cfg.model_dir, flows,
                                          {"a": tmp_path / "a.csv"}, n_cpus=1,
                                          init_runs=64, max_runs=114,
                                          parameters=parameters)
    text = Path(path).read_text()
    assert "'init_runs': 64" in text and "'init_runs_min': 64" in text


def test_the_progress_falls_back_to_the_case_without_a_record(tmp_path):
    from axqua.solvers.telemac.backend import _calibration_progress

    reported = []
    cfg = SimpleNamespace(calibration_dir=tmp_path,
                          calibration=SimpleNamespace(max_runs=None))
    ctx = SimpleNamespace(progress=lambda **fields: reported.append(fields))
    from axqua import hbc

    with _calibration_progress(cfg, ctx) as counter:
        (tmp_path / hbc.DRIVER_LOG).write_text(
            "2026-11-23 08:08:40,833 - Running full complexity model 3\n")
        counter.poll()
    assert reported == [{"iteration": 3}]


def test_without_a_limit_the_job_shows_the_initial_design_and_then_no_total(tmp_path):
    """The status keeps a field reported as None, so 0 stands for no total."""
    from axqua import bayescal, hbc
    from axqua.solvers.telemac.backend import _calibration_progress

    reported = []
    cfg = SimpleNamespace(calibration_dir=tmp_path,
                          calibration=SimpleNamespace(max_runs=None))
    ctx = SimpleNamespace(progress=lambda **fields: reported.append(fields))
    (tmp_path / bayescal.RUN_BUDGET).write_text(json.dumps({"init_runs": 64,
                                                            "max_runs": None}))
    with _calibration_progress(cfg, ctx) as counter:
        log = tmp_path / hbc.DRIVER_LOG
        log.write_text("2026-11-23 - Running  full complexity model # 64  with collocation "
                       "point : [0.1] \n2026-11-23 - Running full complexity model 64\n")
        counter.poll()
        with log.open("a") as handle:
            handle.write("2026-11-30 - Running  full complexity model after BAL # 6 with "
                         "collocation point : [0.2] \n"
                         "2026-11-30 - Running full complexity model 70\n")
        counter.poll()
    assert [(r["iteration"], r["max_iterations"], r["stage"]) for r in reported] == [
        (64, 64, hbc.STAGE_DESIGN), (70, 0, hbc.STAGE_LEARNING)]
