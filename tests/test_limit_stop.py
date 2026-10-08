"""A run that TELEMAC-2D stops on its limits is a failed run.

``CONTROL OF LIMITS`` ends such a run as a "neat (programmed) stop" (``telemac2d.F``):
the solver prints ``CORRECT END OF RUN`` and exits with code 0. The first steady run of
the Isar example did exactly that at t = 0, with a velocity of -1002 m/s at the losing
line of the gravel bar, and its job was reported as completed.

The listing text below is copied from that run. Pure Python, no solver.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from axqua.core import errors
from axqua.solvers.telemac.sortie import (LIMIT_STOP, find_limit_stops,
                                           read_limit_stops)
from axqua.workflow import LIMIT_STOP_CODE, LIMIT_STOP_REMEDY, run_solver_streaming

#: what every processor prints, and so what reaches the terminal
MAIN = """\
 ITERATION        0    TIME:   0.0000 S
 TELEMAC2D INITIALIZED
 USING STREAMLINE VERSION 9.1 FOR CHARACTERISTICS

 LIMIT VALUES TRESPASSED, TELEMAC-2D IS STOPPED

 EXITING MPI

 CORRECT END OF RUN
"""

#: what only the subdomain that owns the node prints, into its own listing
OWNER = """\
 USING STREAMLINE VERSION 9.1 FOR CHARACTERISTICS

 LOWER LIMIT ON U REACHED AT POINT    859
 WITH COORDINATES    677083.9     AND     5267939.    
 THE VALUE OF U IS    -1002.505     THE LIMIT IS:    -1000.000    

 LIMIT VALUES TRESPASSED, TELEMAC-2D IS STOPPED
"""

STAMP = "steady2d.cas_2026-10-08-07h42min35s"


class _Runtime:
    """Stands in for ``TelemacRuntime``: writes listings, streams lines, exits 0."""

    def __init__(self, lines: str, listings: dict[str, str] | None = None):
        self.lines = lines.splitlines()
        self.listings = listings or {}

    def run_solver(self, cas_file, cwd, ncsize=None, solver=None, check=True,
                   on_line=None, should_stop=None):
        for name, text in self.listings.items():
            (Path(cwd) / name).write_text(text)
        for line in self.lines:
            on_line(line)
        return subprocess.CompletedProcess(["telemac2d.py", str(cas_file)], 0, "", "")


def _run(tmp_path, runtime):
    cfg = SimpleNamespace(model_dir=tmp_path)
    return run_solver_streaming(runtime, cfg, cas_file="steady2d.cas", ncsize=8,
                                duration=3000.0, show_progress=False)


def test_the_report_of_a_node_is_read_as_telemac_writes_it():
    (stop,) = read_limit_stops(OWNER)
    assert (stop.bound, stop.variable) == ("LOWER", "U")
    assert (stop.x, stop.y) == (677083.9, 5267939.0)
    assert (stop.value, stop.limit) == (-1002.505, -1000.0)
    assert stop.describe() == ("velocity U = -1002.5 m/s at x = 677083.9, "
                               "y = 5267939.0 (the lower limit is -1000)")


def test_a_value_too_large_for_its_field_does_not_break_the_reader():
    text = OWNER.replace("-1002.505    ", "****************")
    (stop,) = read_limit_stops(text)
    assert stop.value != stop.value          # nan, and the position is still known
    assert stop.x == 677083.9


def test_a_listing_without_a_stop_names_no_node():
    assert read_limit_stops("ITERATION 10 TIME: 1.0 S\n CORRECT END OF RUN\n") == []


def test_the_node_is_found_in_the_listing_of_the_subdomain_that_owns_it(tmp_path):
    (tmp_path / f"{STAMP}.sortie").write_text(MAIN)
    (tmp_path / f"{STAMP}_p00001.sortie").write_text(MAIN)
    (tmp_path / f"{STAMP}_p00004.sortie").write_text(OWNER)
    # an older run of the same case must not be read as this one's
    (tmp_path / "steady2d.cas_2026-10-01-00h00min00s_p00002.sortie").write_text(
        OWNER.replace("677083.9", "1.000000"))
    stops = find_limit_stops(tmp_path, "steady2d.cas")
    assert [stop.x for stop in stops] == [677083.9]


def test_a_run_stopped_on_its_limits_comes_back_as_failed(tmp_path, caplog):
    runtime = _Runtime(MAIN, {f"{STAMP}.sortie": MAIN, f"{STAMP}_p00004.sortie": OWNER})
    with caplog.at_level("ERROR", logger="axqua"):
        proc = _run(tmp_path, runtime)
    assert proc.returncode == LIMIT_STOP_CODE != 0
    assert proc.stop_reason == (
        "TELEMAC-2D stopped the run because a value left its limits: velocity U = "
        "-1002.5 m/s at x = 677083.9, y = 5267939.0 (the lower limit is -1000)")
    assert "numerical instability" in caplog.text


def test_the_stop_is_reported_even_when_no_listing_names_the_node(tmp_path):
    proc = _run(tmp_path, _Runtime(MAIN))
    assert proc.returncode == LIMIT_STOP_CODE
    assert "the listing does not name the node" in proc.stop_reason


def test_a_run_that_finished_is_left_alone(tmp_path):
    finished = MAIN.replace(f" {LIMIT_STOP}, TELEMAC-2D IS STOPPED\n", "")
    proc = _run(tmp_path, _Runtime(finished))
    assert proc.returncode == 0
    assert not hasattr(proc, "stop_reason")


def test_a_real_failure_keeps_its_own_return_code(tmp_path):
    class Crashed(_Runtime):
        def run_solver(self, *args, **kwargs):
            proc = super().run_solver(*args, **kwargs)
            proc.returncode = 134
            return proc

    proc = _run(tmp_path, Crashed(MAIN))
    assert proc.returncode == 134 and proc.stop_reason


def test_the_job_fails_with_the_position_and_what_to_do(tmp_path, monkeypatch):
    """The steady job of the plugin: FAILED with a reason, not COMPLETED."""
    import axqua.env
    from axqua.solvers.telemac.backend import TelemacBackend

    (tmp_path / "steady2d.cas").write_text("DURATION : 3000.0\n")
    runtime = _Runtime(MAIN, {f"{STAMP}.sortie": MAIN, f"{STAMP}_p00004.sortie": OWNER})
    monkeypatch.setattr(axqua.env, "TelemacRuntime", lambda telemac: runtime)
    cfg = SimpleNamespace(model_dir=tmp_path, telemac=SimpleNamespace(solver="telemac2d"),
                          model_path=lambda name: tmp_path / name,
                          calibration_path=lambda name: tmp_path / "calibration" / name)
    with pytest.raises(errors.SolverError) as raised:
        TelemacBackend()._launch(cfg, ctx=None, cas_file="steady2d.cas", ncsize=8,
                                 solver=None, name="telemac", duration=3000.0)
    assert "x = 677083.9, y = 5267939.0" in str(raised.value)
    assert raised.value.remedy == LIMIT_STOP_REMEDY
