"""Reading and writing the two job files, safely.

Two properties matter more than anything else here, because QGIS may be polling
``status.json`` at 1 Hz while the runner rewrites it:

**Writes are atomic.** Temporary file, ``fsync``, ``os.replace``. Not ``os.rename`` -
that fails on Windows when the destination exists, and a "write then rename" that
silently falls back to "unlink then rename" has a window where the file does not exist at
all. ``os.replace`` is atomic on both platforms.

**Reads tolerate a torn moment.** Even with an atomic replace, a reader on a network
filesystem can observe an incomplete file, and the correct response is to look again
rather than to report the job as broken. So the reader retries a few times over a few
tens of milliseconds before giving up.

Schema versioning follows plan §5 exactly: older migrates **in memory** and is never
rewritten on read (a read must not mutate a record the user may still want to inspect);
equal proceeds; newer is refused with a message naming both versions, because running a
job description you do not fully understand is how a job that looks submitted quietly
runs something else.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Callable

from axqua.core.errors import ConfigError
from axqua.jobs.model import (JOB_SCHEMA_VERSION, STATUS_SCHEMA_VERSION, JobSpec,
                                  JobStatus)
from axqua.jobs.paths import JobDir

__all__ = ["read_json_tolerant", "read_spec", "read_status", "write_json_atomic",
           "write_spec", "write_status"]

#: Migrations from version *n* to *n+1*. Empty at version 1, which is the point of
#: declaring the mechanism now: adding version 2 is a function, not a redesign.
JOB_MIGRATIONS: dict[int, Callable[[dict], dict]] = {}
STATUS_MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


#: How long a replace waits for a reader on Windows, in seconds.
REPLACE_PATIENCE = 2.0


def _replace(tmp: Path, path: Path) -> None:
    """``os.replace``, which on Windows waits for a reader that holds *path* open.

    Windows refuses to replace a file another process has open without delete sharing: the
    plugin polling ``status.json``, the reaper, a virus scanner looking at the new file, or
    a folder that OneDrive synchronizes. Raised once, the error ended a running job in the
    middle of a progress update (review of 2026-11-06); such a hold lasts milliseconds.
    """
    if os.name != "nt":
        os.replace(tmp, path)
        return
    deadline = time.monotonic() + REPLACE_PATIENCE
    pause = 0.01
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(pause)
            pause = min(pause * 2, 0.2)


def write_json_atomic(path: str | os.PathLike, payload: dict[str, Any], *,
                      fsync: bool = True) -> Path:
    """Replace *path* with *payload* atomically. Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=False)
            fh.write("\n")
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        _replace(tmp, path)
        if fsync:
            _fsync_dir(path.parent)
    finally:
        # A crash between open() and replace() must not leave litter that a directory
        # scan would later mistake for a job artifact.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:      # pragma: no cover
                pass
    return path


def _fsync_dir(directory: Path) -> None:
    """Make the rename itself durable. POSIX only; Windows has no directory handle."""
    if os.name == "nt":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:              # pragma: no cover
        return
    try:
        os.fsync(fd)
    except OSError:              # pragma: no cover - some filesystems refuse
        pass
    finally:
        os.close(fd)


def read_json_tolerant(path: str | os.PathLike, *, retries: int = 4,
                       delay: float = 0.05) -> dict[str, Any] | None:
    """Read a JSON file, retrying through a concurrent replace.

    Returns ``None`` when the file is absent - an absent ``status.json`` is a normal
    state (the job directory exists but nothing has run yet), not an error.
    """
    path = Path(path)
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(delay)
    raise ConfigError(
        f"{path} could not be read as JSON",
        subject=str(path),
        remedy="The file may be corrupt; the job directory itself is authoritative, "
               "so 'axqua list --rebuild' will re-index what is readable.",
        cause=str(last),
    )


def migrate(payload: dict[str, Any], *, current: int,
            migrations: dict[int, Callable[[dict], dict]], what: str) -> dict[str, Any]:
    """Bring *payload* up to *current*, or refuse."""
    version = int(payload.get("schema_version", 0) or 0)
    if version > current:
        raise ConfigError(
            f"{what} was written by a newer axqua (schema_version {version}); "
            f"this axqua understands up to {current}",
            subject="schema_version",
            remedy="Upgrade axqua, or read this job with the version that wrote it.",
            found=version,
            supported=current,
        )
    while version < current:
        step = migrations.get(version)
        if step is None:
            raise ConfigError(
                f"{what} is at schema_version {version} and axqua has no migration "
                f"to {version + 1}",
                subject="schema_version",
            )
        payload = step(dict(payload))
        version += 1
        payload["schema_version"] = version
    return payload


# ------------------------------------------------------------------------- job.json


def write_spec(job_dir: JobDir | str | os.PathLike, spec: JobSpec, *,
               freeze: bool = True) -> Path:
    """Write ``job.json`` once, then make it read-only.

    The read-only bit is not security - it is a statement. ``job.json`` is the record of
    what was asked for; anything that wants to change it wants a new job.
    """
    jd = job_dir if isinstance(job_dir, JobDir) else JobDir(job_dir)
    if jd.spec.exists():
        raise ConfigError(
            f"{jd.spec} already exists; a submission record is written once",
            subject="job.json",
            remedy="Submit a new job rather than editing an existing one.",
        )
    path = write_json_atomic(jd.spec, spec.as_dict())
    if freeze:
        try:
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        except OSError:          # pragma: no cover - exotic filesystem
            pass
    return path


def read_spec(job_dir: JobDir | str | os.PathLike) -> JobSpec:
    jd = job_dir if isinstance(job_dir, JobDir) else JobDir(job_dir)
    payload = read_json_tolerant(jd.spec)
    if payload is None:
        raise ConfigError(
            f"no job.json in {jd.root}",
            subject=str(jd.root),
            remedy="That directory is not a axqua job.",
        )
    payload = migrate(payload, current=JOB_SCHEMA_VERSION, migrations=JOB_MIGRATIONS,
                      what="job.json")
    return JobSpec.from_dict(payload)


# ---------------------------------------------------------------------- status.json


def write_status(job_dir: JobDir | str | os.PathLike, status: JobStatus) -> Path:
    """Replace ``status.json``.

    Callers must hold ``job.lock``; that is enforced by convention and by the executor's
    structure rather than by a check here, because ``reaper.reconcile`` legitimately
    writes after proving the previous owner is gone.
    """
    jd = job_dir if isinstance(job_dir, JobDir) else JobDir(job_dir)
    return write_json_atomic(jd.status, status.as_dict())


def read_status(job_dir: JobDir | str | os.PathLike) -> JobStatus | None:
    """The last complete status, or ``None`` if nothing has been written yet."""
    jd = job_dir if isinstance(job_dir, JobDir) else JobDir(job_dir)
    payload = read_json_tolerant(jd.status)
    if payload is None:
        return None
    payload = migrate(payload, current=STATUS_SCHEMA_VERSION,
                      migrations=STATUS_MIGRATIONS, what="status.json")
    return JobStatus.from_dict(payload)
