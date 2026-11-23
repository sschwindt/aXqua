"""A calibration says which model run it is on (:class:`axqua.hbc.RunCounter`).

The HydroBayesCal driver is a separate process whose output aXqua does not parse, so a
calibration job stood at "iteration 0" for as long as it ran. The lines below are
copied from the ``logfile.log`` of the Isar example.
"""

from __future__ import annotations

from types import SimpleNamespace

from axqua import hbc
from axqua.solvers.telemac.backend import _calibration_progress

RUN = "2026-10-08 08:08:40,833 - Running full complexity model {n}\n"
#: the two lines that count within one phase only, and so must not be read as progress
NOISE = ("2026-10-08 07:59:11,472 -  Running  full complexity model # 2  with "
         "collocation point : [0.23, 0.275] \n"
         "2026-10-08 08:08:40,825 -  Running  full complexity model after BAL # 1 with "
         "collocation point : [0.0385, 0.7833] \n"
         "2026-10-08 08:08:40,825 - Results file name for this simulation: r2d_9.slf\n")


def _counter(tmp_path, text=""):
    log = tmp_path / hbc.DRIVER_LOG
    if text:
        log.write_text(text)
    seen: list[int] = []
    return log, seen, hbc.RunCounter(log, _runs(seen), interval=3600.0)


def _runs(seen):
    """A listener that keeps the run numbers only."""
    return lambda run, **context: seen.append(run)


def test_each_new_model_run_is_reported_once(tmp_path):
    log, seen, counter = _counter(tmp_path)
    with counter:
        log.write_text(RUN.format(n=1) + NOISE)
        counter.poll()
        counter.poll()                              # nothing new: nothing reported
        with log.open("a") as handle:
            handle.write(RUN.format(n=2) + RUN.format(n=3))
        counter.poll()
    assert seen == [1, 3]


def test_the_run_number_that_counts_through_both_phases_is_the_one_reported(tmp_path):
    """The ninth run is announced as "after BAL # 1" as well, which is not run 1."""
    log, seen, counter = _counter(tmp_path)
    with counter:
        log.write_text(RUN.format(n=8) + NOISE + RUN.format(n=9))
        counter.poll()
    assert seen == [9]


def test_the_log_of_an_earlier_calibration_is_not_replayed(tmp_path):
    """The results folder is reused, and the log of the last calibration is still there."""
    log, seen, counter = _counter(tmp_path, RUN.format(n=11) + RUN.format(n=12))
    with counter:
        counter.poll()
        assert seen == []
        with log.open("a") as handle:
            handle.write(RUN.format(n=1))
    assert seen == [1]                              # read once more on leaving


def test_a_log_started_afresh_is_read_from_its_beginning(tmp_path):
    log, seen, counter = _counter(tmp_path, RUN.format(n=11) * 20)
    with counter:
        log.write_text(RUN.format(n=1))             # shorter than what was there before
        counter.poll()
    assert seen == [1]


def test_a_missing_log_and_a_failing_listener_do_not_stop_anything(tmp_path):
    def broken(run, **context):
        raise RuntimeError("the dashboard is gone")

    counter = hbc.RunCounter(tmp_path / "no-such-folder" / hbc.DRIVER_LOG, broken,
                             interval=3600.0)
    with counter:
        counter.poll()
    log = tmp_path / hbc.DRIVER_LOG
    with hbc.RunCounter(log, broken, interval=3600.0) as counter:
        log.write_text(RUN.format(n=4))
        counter.poll()
    assert counter.run == 4


def test_the_thread_reports_without_being_asked(tmp_path):
    log = tmp_path / hbc.DRIVER_LOG
    seen: list[int] = []
    with hbc.RunCounter(log, _runs(seen), interval=0.02) as counter:
        log.write_text(RUN.format(n=5))
        for _ in range(200):
            if seen:
                break
            counter._stop.wait(0.01)
    assert seen == [5]


def test_a_job_reports_the_run_and_the_number_of_runs_planned(tmp_path):
    reported = []
    cfg = SimpleNamespace(calibration_dir=tmp_path,
                          calibration=SimpleNamespace(max_runs=12))
    ctx = SimpleNamespace(progress=lambda **fields: reported.append(fields))
    with _calibration_progress(cfg, ctx) as counter:
        (tmp_path / hbc.DRIVER_LOG).write_text(RUN.format(n=7))
        counter.poll()
    assert reported == [{"iteration": 7, "max_iterations": 12}]


def test_without_a_job_there_is_nothing_to_follow(tmp_path):
    cfg = SimpleNamespace(calibration_dir=tmp_path,
                          calibration=SimpleNamespace(max_runs=12))
    with _calibration_progress(cfg, None) as counter:
        assert counter is None


def test_the_log_in_the_results_folder_of_hydrobayescal_1_12_is_followed(tmp_path):
    """HydroBayesCal 1.12.0 moved ``logfile.log`` into auto-saved-results-HydroBayesCal.

    The multi-flow calibration of Inn KB15 (2026-11-08, HydroBayesCal 1.12.1) stood at
    no iteration for 18 hours, because the counter read the working folder only.
    """
    reported = []
    cfg = SimpleNamespace(calibration_dir=tmp_path,
                          calibration=SimpleNamespace(max_runs=50))
    ctx = SimpleNamespace(progress=lambda **fields: reported.append(fields))
    results = tmp_path / "multiflow" / hbc.DRIVER_RESULTS
    with _calibration_progress(cfg, ctx, folder="multiflow") as counter:
        results.mkdir(parents=True)
        (results / hbc.DRIVER_LOG).write_text(RUN.format(n=3))
        counter.poll()
    assert reported == [{"iteration": 3, "max_iterations": 50}]


#: Lines of the multi-flow calibration of Inn KB15 (job 2026-11-22-...-e666db): the
#: initial design runs all parameter sets of one situation, each from 1, then the next.
def _flow(name):
    return f"2026-11-23 05:34:55,486 - multiflow: === flow '{name}' ===\n"


def _design(n):
    return (f"2026-11-23 05:34:55,486 -  Running  full complexity model # {n}  with "
            f"collocation point : [0.27, 0.09, 0.28, -4.92] \n" + RUN.format(n=n))


def _learning(k, n):
    return (f"2026-11-23 05:34:55,486 -  Running  full complexity model after BAL # {k} "
            f"with collocation point : [0.27, 0.09, 0.28, -4.92] \n" + RUN.format(n=n))


def test_each_situation_of_a_multiflow_calibration_counts_its_runs_afresh(tmp_path):
    """The status of the KB15 calibration stood at run 16 of the first situation for as
    long as the second ran, because only a growing number was reported."""
    log = tmp_path / hbc.DRIVER_LOG
    seen = []
    with hbc.RunCounter(log, lambda run, **context: seen.append((run, context)),
                        interval=3600.0) as counter:
        log.write_text(_flow("august-2026") + _design(15) + _design(16))
        counter.poll()
        with log.open("a") as handle:
            handle.write(_flow("september-2025") + _design(1))
        counter.poll()
        with log.open("a") as handle:
            handle.write(_design(2))
        counter.poll()
    design = hbc.STAGE_DESIGN
    assert seen == [(16, {"flow": "august-2026", "stage": design}),
                    (1, {"flow": "september-2025", "stage": design}),
                    (2, {"flow": "september-2025", "stage": design})]


def test_the_active_learning_is_reported_for_every_situation_it_runs(tmp_path):
    """Each iteration runs the new parameter set once per situation, all with one number."""
    log = tmp_path / hbc.DRIVER_LOG
    seen = []
    with hbc.RunCounter(log, lambda run, **context: seen.append((run, context)),
                        interval=3600.0) as counter:
        log.write_text(_flow("march-2026") + _design(64))
        counter.poll()
        with log.open("a") as handle:
            handle.write(_flow("august-2026") + _learning(1, 65)
                         + _flow("september-2025") + _learning(1, 65))
        counter.poll()
    learning = hbc.STAGE_LEARNING
    assert seen == [(64, {"flow": "march-2026", "stage": hbc.STAGE_DESIGN}),
                    (65, {"flow": "september-2025", "stage": learning})]
    assert (counter.flow, counter.stage, counter.run) == ("september-2025", learning, 65)


def test_a_job_names_the_situation_its_place_and_the_planned_total(tmp_path):
    """The total is the max_runs the calibration recorded when it started, which the
    case may leave to the rule, and the situation is shown by the name the case gives it."""
    import json

    from axqua.bayescal import RUN_BUDGET

    reported = []
    situations = [SimpleNamespace(name=name)
                  for name in ("August 2026", "September 2025", "March 2026")]
    cfg = SimpleNamespace(calibration_dir=tmp_path,
                          calibration=SimpleNamespace(max_runs=None, situations=situations),
                          ground_truth=SimpleNamespace(sources=[], measurements=None,
                                                       targets=None))
    ctx = SimpleNamespace(progress=lambda **fields: reported.append(fields))
    results = tmp_path / "multiflow" / hbc.DRIVER_RESULTS
    results.mkdir(parents=True)
    (tmp_path / "multiflow" / RUN_BUDGET).write_text(json.dumps({"max_runs": 114}))
    with _calibration_progress(cfg, ctx, folder="multiflow") as counter:
        (results / hbc.DRIVER_LOG).write_text(_flow("september-2025") + _design(11))
        counter.poll()
    assert reported == [{"iteration": 11, "max_iterations": 114,
                         "stage": hbc.STAGE_DESIGN, "flow": "September 2025",
                         "n_flows": 3, "flow_index": 2}]
