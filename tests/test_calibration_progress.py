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
    return log, seen, hbc.RunCounter(log, seen.append, interval=3600.0)


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
    def broken(run):
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
    with hbc.RunCounter(log, seen.append, interval=0.02) as counter:
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
