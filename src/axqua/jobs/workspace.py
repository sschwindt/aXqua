"""Jobs of one case run one after the other, in the order they were submitted.

A TELEMAC job works in the simulation folder of its case. The build writes the mesh and
the steering files there, a run reads them and writes its results beside them, and a
calibration rewrites the friction table before every one of its runs. Two jobs in that
folder at the same time overwrite each other's files, and nothing prevented it: a steady
run submitted during a calibration started at once, read a friction table that the
calibration was in the middle of changing, and both jobs finished as COMPLETED.

So a job takes a lock in that folder for as long as it runs, and a second job of the same
case **waits** for it. Waiting rather than failing, because it is what the user means:
*Build* and then *Submit* can be clicked one after the other, and the run starts when the
build is done. A waiting job says whom it waits for and can be cancelled.

**The order is the order of submission.** A lock alone does not give that: of two waiting
jobs, whichever looks first when the folder becomes free would take it, and three jobs
submitted in a row are started by three processes that reach the lock in any order - the
run before its build. Each job therefore leaves a ticket in the folder when it is
*submitted*, and only the job with the oldest ticket may take the lock.

Only TELEMAC jobs take part. An OpenFOAM job works in a case folder of its own
(``openfoam.case_dir``), several of which may share one TELEMAC folder and run side by
side on purpose.

The lock is the job lock of :mod:`axqua.jobs.lock` on another file, so the same rules
decide whether its holder is still alive: host, boot, process and process start time. A
holder that crashed therefore does not block the case forever, and a holder on another
computer is never judged. A ticket is dropped when its job has ended, when the folder of
its job is gone, or when its runner never appeared.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from axqua.jobs.lock import JobLock, LockedError

log = logging.getLogger("axqua.jobs.workspace")

#: The lock file and the folder of the tickets, in the simulation folder of the case.
LOCK_NAME = ".axqua-job.lock"
QUEUE_NAME = ".axqua-queue"

#: Seconds between two looks at a folder that somebody else holds.
WAIT_POLL = 3.0

#: Seconds after which a ticket whose job has still not started is taken for abandoned:
#: the submission was interrupted between writing the ticket and starting the runner.
TICKET_GRACE = 120.0

__all__ = ["LOCK_NAME", "QUEUE_NAME", "FolderLock", "applies", "enqueue", "held_for",
           "holder_of", "waiting_in"]


class FolderLock(JobLock):
    """The job lock, on a file in a folder that is not a job directory."""

    def __init__(self, folder: str | os.PathLike, label: str) -> None:
        self.path = Path(folder) / LOCK_NAME
        self._held = False
        # what JobLock names in the message of a refused lock
        self.job = SimpleNamespace(job_id=label)


def applies(spec: Any) -> bool:
    """Whether a job of this kind works in the simulation folder of its case."""
    return getattr(spec, "solver", "") == "telemac"


def holder_of(folder: str | os.PathLike) -> str:
    """The job that holds *folder* and is still alive; empty when it is free."""
    lock = FolderLock(folder, "")
    record = lock.owner()
    if record is None or lock.is_stale()[0]:
        return ""
    return record.purpose or "another job"


# ----------------------------------------------------------------------------- tickets


def _ticket_of(folder: Path, job_id: str) -> Path | None:
    queue = folder / QUEUE_NAME
    if not queue.is_dir():
        return None
    return next(iter(sorted(queue.glob(f"*-{job_id}.json"))), None)


def enqueue(folder: str | os.PathLike, spec: Any,
            job_dir: str | os.PathLike | None = None) -> Path | None:
    """Leave the ticket of a job in *folder*. Called when the job is submitted.

    The name of the ticket starts with the time it was written, so that sorting the
    names gives the order of submission. A job that already has a ticket keeps it.
    """
    if not applies(spec):
        return None
    folder = Path(folder)
    existing = _ticket_of(folder, spec.job_id)
    if existing is not None:
        return existing
    queue = folder / QUEUE_NAME
    queue.mkdir(parents=True, exist_ok=True)
    ticket = queue / f"{time.time_ns():020d}-{spec.job_id}.json"
    ticket.write_text(json.dumps({"job_id": spec.job_id,
                                  "job_dir": str(job_dir) if job_dir else "",
                                  "written": time.time()}), encoding="utf-8")
    return ticket


def _abandoned(ticket: Path) -> bool:
    """Whether the job of *ticket* will never take its turn."""
    try:
        data = json.loads(ticket.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True                                   # unreadable: nobody can own it
    job_dir = data.get("job_dir") or ""
    if not job_dir:
        return False                                  # a job run in this process
    if not Path(job_dir).is_dir():
        return True
    from axqua.jobs import reaper

    # The reaper and not a plain read: a job whose runner died while it waited still
    # says STARTING, and nobody may be looking at it who would correct that.
    try:
        status = reaper.reconcile(job_dir)
    except Exception:                                 # noqa: BLE001 - judged below
        status = None
    if status is not None and status.state.is_terminal:
        return True
    started = bool(status is not None and status.launcher)
    age = time.time() - float(data.get("written") or 0.0)
    return not started and age > TICKET_GRACE


def waiting_in(folder: str | os.PathLike) -> list[str]:
    """The jobs that hold a ticket in *folder*, oldest first. Drops abandoned tickets."""
    queue = Path(folder) / QUEUE_NAME
    if not queue.is_dir():
        return []
    alive = []
    for ticket in sorted(queue.glob("*.json")):
        if _abandoned(ticket):
            ticket.unlink(missing_ok=True)
        else:
            alive.append(ticket.stem.split("-", 1)[1])
    return alive


def _leave(folder: Path, job_id: str) -> None:
    ticket = _ticket_of(folder, job_id)
    if ticket is not None:
        ticket.unlink(missing_ok=True)
    try:
        (folder / QUEUE_NAME).rmdir()                 # only when it is empty
    except OSError:
        pass


# -------------------------------------------------------------------------------- hold


@contextmanager
def held_for(folder: str | os.PathLike, spec: Any, *, cancel: Any = None,
             sink: Any = None, poll: float | None = None,
             job_dir: str | os.PathLike | None = None
             ) -> Iterator[FolderLock | None]:
    """Hold the simulation folder for one job, waiting for its turn.

    *cancel* is the job's cancel token: a job that is cancelled while it waits leaves
    through it and never takes the lock. *sink* receives the reason for the wait as the
    phase of the job, which is what ``axqua status`` and the job table show.
    """
    if not applies(spec):
        yield None
        return
    folder = Path(folder)
    poll = WAIT_POLL if poll is None else poll
    lock = FolderLock(folder, spec.job_id)
    enqueue(folder, spec, job_dir)                    # a job that was not submitted
    announced = ""
    try:
        while True:
            ahead = waiting_in(folder)
            ahead = ahead[:ahead.index(spec.job_id)] if spec.job_id in ahead else []
            who = ahead[0] if ahead else ""
            if not who:
                try:
                    lock.acquire(purpose=spec.job_id, break_stale=True)
                    break
                except LockedError:
                    record = lock.owner()
                    who = (record.purpose if record else "") or "another job"
            if cancel is not None:
                cancel.check()
            if who != announced:
                log.info("waiting for %s, which is working in %s", who, folder)
                if sink is not None:
                    sink.step(f"waiting for {who}")
                announced = who
            time.sleep(poll)
    except BaseException:
        _leave(folder, spec.job_id)
        raise
    _leave(folder, spec.job_id)
    if announced:
        log.info("%s is done; starting", announced)
        if sink is not None:
            sink.step("", done=True)
    try:
        yield lock
    finally:
        lock.release()
