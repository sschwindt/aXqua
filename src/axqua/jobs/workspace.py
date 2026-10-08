"""Jobs of one case run one after the other.

A TELEMAC job works in the simulation folder of its case. The build writes the mesh and
the steering files there, a run reads them and writes its results beside them, and a
calibration rewrites the friction table before every one of its runs. Two jobs in that
folder at the same time overwrite each other's files, and nothing prevented it: a steady
run submitted during a calibration started at once, read a friction table that the
calibration was in the middle of changing, and both jobs finished as COMPLETED.

So a job takes a lock in that folder for as long as it runs, and a second job of the same
case **waits** for it. Waiting rather than failing, because it is what the user means:
*Build* and then *Submit* can be clicked one after the other, and the run starts when the
build is done. A waiting job stays QUEUED, says whom it waits for, and can be cancelled.

Only TELEMAC jobs take part. An OpenFOAM job works in a case folder of its own
(``openfoam.case_dir``), several of which may share one TELEMAC folder and run side by
side on purpose.

The lock is the job lock of :mod:`axqua.jobs.lock` on another file, so the same rules
decide whether its holder is still alive: host, boot, process and process start time. A
holder that crashed therefore does not block the case forever, and a holder on another
computer is never judged.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from axqua.jobs.lock import JobLock, LockedError

log = logging.getLogger("axqua.jobs.workspace")

#: The lock file, in the simulation folder of the case.
LOCK_NAME = ".axqua-job.lock"

#: Seconds between two looks at a lock that somebody else holds.
WAIT_POLL = 3.0

__all__ = ["LOCK_NAME", "FolderLock", "applies", "held_for", "holder_of"]


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


@contextmanager
def held_for(folder: str | os.PathLike, spec: Any, *, cancel: Any = None,
             sink: Any = None, poll: float | None = None
             ) -> Iterator[FolderLock | None]:
    """Hold the simulation folder for one job, waiting while another job has it.

    *cancel* is the job's cancel token: a job that is cancelled while it waits leaves
    through it and never takes the lock. *sink* receives the reason for the wait as the
    phase of the job, which is what ``axqua status`` and the job table show.
    """
    if not applies(spec):
        yield None
        return
    lock = FolderLock(folder, spec.job_id)
    poll = WAIT_POLL if poll is None else poll
    announced = ""
    while True:
        try:
            lock.acquire(purpose=spec.job_id, break_stale=True)
            break
        except LockedError:
            if cancel is not None:
                cancel.check()
            record = lock.owner()
            who = (record.purpose if record else "") or "another job"
            if who != announced:
                log.info("waiting for %s, which is working in %s", who, folder)
                if sink is not None:
                    sink.step(f"waiting for {who}")
                announced = who
            time.sleep(poll)
    if announced:
        log.info("%s is done; starting", announced)
        if sink is not None:
            sink.step("", done=True)
    try:
        yield lock
    finally:
        lock.release()
