"""Jobs of one case run one after the other, in the order they were submitted
(:mod:`axqua.jobs.workspace`).

A steady run submitted during a calibration used to start at once in the same folder,
read a friction table the calibration was changing, and both finished as COMPLETED.
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from axqua.jobs import workspace
from axqua.jobs.lock import LockRecord


def _spec(job_id, solver="telemac"):
    return SimpleNamespace(job_id=job_id, solver=solver)


class _Sink:
    def __init__(self):
        self.phases = []

    def step(self, name, **kwargs):
        self.phases.append(name)


class _Cancel:
    def __init__(self, after=None):
        self.after, self.looks = after, 0

    def check(self):
        self.looks += 1
        if self.after is not None and self.looks >= self.after:
            raise KeyboardInterrupt("cancelled while waiting")


def test_a_job_holds_the_folder_while_it_runs_and_frees_it_afterwards(tmp_path):
    with workspace.held_for(tmp_path, _spec("job-a")):
        assert workspace.holder_of(tmp_path) == "job-a"
        record = json.loads((tmp_path / workspace.LOCK_NAME).read_text())
        assert record["purpose"] == "job-a"
        assert workspace.waiting_in(tmp_path) == []        # it left the queue
    assert workspace.holder_of(tmp_path) == ""
    assert not (tmp_path / workspace.LOCK_NAME).exists()
    assert not (tmp_path / workspace.QUEUE_NAME).exists()


def test_a_second_job_waits_for_the_first_and_says_so(tmp_path):
    order, sink = [], _Sink()
    first_in, release_first = threading.Event(), threading.Event()

    def first():
        with workspace.held_for(tmp_path, _spec("build")):
            order.append("build starts")
            first_in.set()
            release_first.wait(10)
            order.append("build ends")

    def second():
        with workspace.held_for(tmp_path, _spec("run"), sink=sink, poll=0.01):
            order.append("run starts")

    a = threading.Thread(target=first)
    a.start()
    first_in.wait(10)
    b = threading.Thread(target=second)
    b.start()
    for _ in range(500):                      # until the second job has said it waits
        if sink.phases:
            break
        release_first.wait(0.01)
    release_first.set()
    a.join(10)
    b.join(10)
    assert order == ["build starts", "build ends", "run starts"]
    assert sink.phases == ["waiting for build", ""]


def test_jobs_start_in_the_order_they_were_submitted(tmp_path):
    """Three jobs submitted in a row are started by three processes that reach the
    folder in any order. A lock alone let the run start before its build."""
    order = []
    for name in ("build", "run", "calibration"):          # the order of submission
        workspace.enqueue(tmp_path, _spec(name))
    assert workspace.waiting_in(tmp_path) == ["build", "run", "calibration"]

    def job(name):
        with workspace.held_for(tmp_path, _spec(name), poll=0.01):
            order.append(name)
            time.sleep(0.03)

    threads = [threading.Thread(target=job, args=(name,))
               for name in ("calibration", "run", "build")]   # the runners, reversed
    for thread in threads:
        thread.start()
        time.sleep(0.05)                      # each has looked before the next starts
    for thread in threads:
        thread.join(10)
    assert order == ["build", "run", "calibration"]
    assert not (tmp_path / workspace.QUEUE_NAME).exists()


def test_a_job_cancelled_while_it_waits_never_takes_the_folder(tmp_path):
    with workspace.held_for(tmp_path, _spec("calibration")):
        with pytest.raises(KeyboardInterrupt):
            with workspace.held_for(tmp_path, _spec("run"), cancel=_Cancel(after=2),
                                    poll=0.01):
                raise AssertionError("the run must not start")
        assert workspace.holder_of(tmp_path) == "calibration"
        assert workspace.waiting_in(tmp_path) == []        # and it left the queue


def test_a_job_that_died_does_not_block_the_case(tmp_path):
    """Its lock names a process that is gone, which the staleness rules recognise."""
    dead = LockRecord.for_self("crashed-job").as_dict()
    dead["pid"] = 2 ** 22 + 12345             # no such process
    (tmp_path / workspace.LOCK_NAME).write_text(json.dumps(dead))
    assert workspace.holder_of(tmp_path) == ""
    with workspace.held_for(tmp_path, _spec("next"), cancel=_Cancel(after=1)):
        assert workspace.holder_of(tmp_path) == "next"


def test_the_ticket_of_a_job_that_will_never_run_is_dropped(tmp_path, monkeypatch):
    """Its folder is gone, or the submission was interrupted before the runner started.
    Either way nobody behind it may wait for ever."""
    gone = workspace.enqueue(tmp_path, _spec("deleted"), tmp_path / "no-such-job")
    assert gone.is_file()
    assert workspace.waiting_in(tmp_path) == []
    assert not gone.exists()

    job_dir = tmp_path / "jobs" / "interrupted"
    job_dir.mkdir(parents=True)
    workspace.enqueue(tmp_path, _spec("interrupted"), job_dir)
    assert workspace.waiting_in(tmp_path) == ["interrupted"]      # within the grace
    monkeypatch.setattr(workspace, "TICKET_GRACE", 0.0)
    assert workspace.waiting_in(tmp_path) == []


def test_an_openfoam_job_does_not_take_part(tmp_path):
    """Its legs work in case folders of their own and run side by side on purpose."""
    assert workspace.applies(_spec("leg", solver="openfoam")) is False
    assert workspace.enqueue(tmp_path, _spec("leg", solver="openfoam")) is None
    with workspace.held_for(tmp_path, _spec("telemac-run")):
        with workspace.held_for(tmp_path, _spec("leg", solver="openfoam"),
                                cancel=_Cancel(after=1)) as lock:
            assert lock is None
    assert not (tmp_path / workspace.LOCK_NAME).exists()
