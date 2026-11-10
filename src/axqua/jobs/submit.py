"""Turning a case configuration into a job, and starting it detached.

Submission does three things, in an order chosen so that a failure never leaves a
half-started job behind:

1. **Freeze.** The resolved configuration is written into the job directory, both as the
   record in ``job.json`` and as a loadable ``input/case-config.yml``. From that moment
   the job is reproducible from its own directory and immune to later edits of the case -
   which is the whole reason ``job.json`` stores the configuration rather than a path to
   it (plan §3).
2. **Validate.** The solver environment is probed *before* anything is launched, because
   "the setup script is wrong" discovered at submit time is a message, while the same
   fact discovered inside a detached process is an archaeology exercise.
3. **Launch**, and only then record the handle.

The environment is captured here, once, and handed to the launcher as a dict - the first
real use of :meth:`SolverEnvironment.capture`. That is what keeps a shell out of the job's
process tree.
"""

from __future__ import annotations

import getpass
import logging
import os
import socket
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from axqua.core import errors
from axqua.core.environment import ENTERED_MARKER
from axqua.jobs import ids, index, launcher as launcher_mod, paths, profiles
from axqua.jobs.model import (KIND_META, JobKind, JobSpec, JobState, JobStatus,
                                  Resources, Workspace, parse_kind)
from axqua.jobs.paths import JobDir
from axqua.jobs.store import write_spec, write_status

log = logging.getLogger("axqua.jobs.submit")

__all__ = ["build_spec", "create_job", "submit_job"]


def build_spec(cfg: Any, kind: str | JobKind, *, profile: profiles.Profile | None = None,
               job_id: str | None = None, options: dict[str, Any] | None = None,
               np: int | None = None, workspace: str | None = None,
               source_job: str | None = None, launcher: str = "auto",
               source_config: str | os.PathLike | None = None) -> JobSpec:
    """Assemble the immutable submission record. Writes nothing."""
    from axqua.config import config_to_dict

    kind = kind if isinstance(kind, JobKind) else parse_kind(kind)
    meta = KIND_META[kind]
    profile = profile or profiles.implicit(cfg, meta.solver)
    job_id = job_id or ids.make_job_id(getattr(cfg, "name", "case"), meta.slug)

    # Process count, most specific wins, and the *resolved* number is frozen - so a job
    # rerun a year later uses what it actually used, not what the profile says today.
    configured = getattr(getattr(cfg, meta.solver, None), "n_processors", None)
    mpi = np or profile.mpi_processes or configured or 1

    env = profile.solver_environment()
    return JobSpec(
        job_id=job_id,
        kind=kind,
        solver=meta.solver,
        solver_profile=profile.name,
        created_by={
            "user": _user(), "host": socket.gethostname(),
            "axqua": _version(), "argv": list(sys.argv[1:]),
        },
        case={
            "name": getattr(cfg, "name", "case"),
            "source_config": str(source_config) if source_config else None,
            "config": config_to_dict(cfg),
        },
        environment={
            "kind": str(env.kind),
            "setup_script": str(env.setup_script) if env.setup_script else None,
            "shell": env.shell,
            "distro": env.distro,
            "mpi_launcher": env.mpi_launcher,
            "overrides": dict(env.overrides),
            "postprocess": list(profile.postprocess),
        },
        resources=Resources(mpi_processes=int(mpi),
                            working_root=str(profile.working_root)
                            if profile.working_root else None),
        workspace=Workspace.from_dict({"mode": workspace or meta.workspace_default,
                                       "source_job": source_job}),
        options=meta.options.from_dict(options or {}),
        launcher={"requested": launcher or "auto"},
    )


def create_job(cfg: Any, spec: JobSpec, *, job_root: str | os.PathLike | None = None
               ) -> JobDir:
    """Claim the directory and freeze the inputs. Nothing is launched."""
    from axqua.config import dump_config

    root = paths.job_root(explicit=job_root,
                          profile_root=spec.resources.working_root,
                          config_root=getattr(cfg, "job_root", None))
    jd = _claim(root, spec)
    jd.input.mkdir(parents=True, exist_ok=True)

    # A loadable copy beside the record. The record is what the job *was*; this is what
    # the executor actually loads, and a test asserts the two agree.
    dump_config(cfg, jd.frozen_config, base=jd.input)
    write_spec(jd, spec)
    write_status(jd, JobStatus.initial(spec))
    log.info("created job %s in %s", spec.job_id, jd.root)
    return jd


def _claim(root: Path, spec: JobSpec) -> JobDir:
    """Create the directory, redrawing the id on the (very unlikely) collision."""
    for attempt in range(ids.MAX_REDRAWS):
        candidate = JobDir(root / spec.job_id)
        try:
            return candidate.create()
        except FileExistsError:
            if attempt + 1 >= ids.MAX_REDRAWS:
                break
            date, slug, _tail = ids.parse_job_id(spec.job_id)
            spec.job_id = ids.make_job_id(slug, when=date)
            log.debug("job id collision; redrawing as %s", spec.job_id)
    raise errors.ConfigError(
        f"could not claim a job directory under {root}",
        subject="job_root",
        remedy="Check that the job root is writable.",
    )


def submit_job(cfg: Any, kind: str | JobKind, *,
               profile: profiles.Profile | None = None,
               options: dict[str, Any] | None = None,
               job_root: str | os.PathLike | None = None,
               np: int | None = None, workspace: str | None = None,
               source_job: str | None = None, launcher: str = "auto",
               source_config: str | os.PathLike | None = None,
               validate_env: bool = True, dry_run: bool = False) -> JobSpec | JobDir:
    """Freeze, validate, launch. Returns the :class:`JobDir`, or the spec on a dry run."""
    spec = build_spec(cfg, kind, profile=profile, options=options, np=np,
                      workspace=workspace, source_job=source_job, launcher=launcher,
                      source_config=source_config)
    if dry_run:
        # A preview: the plugin shows exactly what would be submitted without leaving a
        # directory behind for the user to clean up.
        return spec

    resolved = profile or profiles.implicit(cfg, spec.solver)
    env = resolved.solver_environment()
    captured: dict[str, str] = {}
    with collect_warnings() as warned:
        if validate_env and spec.meta.needs_environment:
            captured = _validate_and_capture(resolved, env, spec)
            _fit_processes_to_installation(spec, captured, explicit_np=np)

    jd = create_job(cfg, spec, job_root=job_root)
    _note_in_runner_log(jd, warned)
    if spec.workspace.mode == "case":
        # The ticket that fixes this job's place among the jobs of its case. Written
        # here and not by the runner, so that the order is the order of submission and
        # not the order in which the detached runners happen to start.
        from axqua.jobs import workspace as case_queue

        try:
            case_queue.enqueue(cfg.model_dir, spec, jd.root)
        except OSError as exc:
            log.debug("no ticket written (%s); the runner writes it when it starts", exc)
    try:
        _launch(jd, spec, env, captured, resolved)
    except Exception as exc:                        # noqa: BLE001
        # The directory exists and is a valid record of a job that failed to start, so
        # it is marked FAILED rather than deleted - a job that vanishes gives the user
        # nothing to read.
        _record_launch_failure(jd, spec, exc)
        raise
    _index(jd)
    return jd


@contextmanager
def collect_warnings() -> Iterator[list[str]]:
    """The warnings the ``axqua`` logger gives while the block runs, as text.

    A warning of the submit (a process count fitted to the installation, solver variables from the ambient shell) went only to the stderr of the submitting process, which the plugin does not show: on the Windows test computer (2026-11-09) the job ran on 1 process and nothing in QGIS said why.
    """
    found: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                found.append(record.getMessage())
            except Exception:                       # noqa: BLE001
                self.handleError(record)

    handler = _Collect(logging.WARNING)
    root = logging.getLogger("axqua")
    root.addHandler(handler)
    try:
        yield found
    finally:
        root.removeHandler(handler)


def _note_in_runner_log(jd: JobDir, warned: list[str]) -> None:
    """The warnings of the submit at the top of the job's ``runner.log``, where ``axqua logs`` and the job list find them later.

    The runner appends to the same file when it starts.
    """
    if not warned:
        return
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        jd.runner_log.parent.mkdir(parents=True, exist_ok=True)
        with open(jd.runner_log, "a", encoding="utf-8") as fh:
            for text in warned:
                fh.write(f"{stamp} WARNING axqua.jobs.submit: {text}\n")
    except OSError as exc:
        log.debug("the warnings of the submit were not written to %s (%s)",
                  jd.runner_log, exc)


def _fit_processes_to_installation(spec: JobSpec, variables: dict[str, str], *,
                                   explicit_np: int | None) -> None:
    """A TELEMAC built without MPI runs on one process: a count from the case or the profile becomes 1, a count asked for with the job is refused before anything starts.

    TELEMAC itself stops such a run only after reading the case ("Parallel inconsistency"), and the plugin's default (MPI processes 0, as set for the case) took the case's 8 on the Windows test computer (2026-11-09), whose TELEMAC was built without MPI.
    """
    if spec.solver != "telemac" or not hasattr(spec.options, "ncsize"):
        return
    option = spec.options.ncsize
    wanted = option or spec.resources.mpi_processes
    if not wanted or wanted <= 1:
        return
    from axqua.core import installation

    parallel = next((m for m in installation._telemac_modules(variables, checked=True)
                     if m.name == "parallel"), None)
    if parallel is None or parallel.available is not False:
        return                                      # parallel, or nothing aXqua can tell
    if option or (explicit_np and explicit_np > 1):
        raise errors.ConfigError(
            f"{wanted} processes were asked for, but this TELEMAC runs on one process only "
            f"({parallel.detail})",
            subject="ncsize" if option else "np",
            remedy="Run on one process (MPI processes 1 in the plugin, --np 1), or build "
                   "TELEMAC with MPI.")
    log.warning("this TELEMAC runs on one process only (%s): the job runs on 1 instead of "
                "the %d set for the case or the computer", parallel.detail, wanted)
    spec.resources.mpi_processes = 1


def _validate_and_capture(profile: profiles.Profile, env, spec: JobSpec
                          ) -> dict[str, str]:
    status = profile.validate()
    if not status.ok:
        raise errors.EnvironmentError(
            f"the {spec.solver} environment for profile {profile.name!r} is not usable: "
            f"{status.detail}",
            subject="profile.setup_script",
            remedy="Fix the setup script, then run 'axqua profiles validate'.",
            missing=list(status.missing),
        )
    if status.ambient:
        # Present is not the same as provided: a detached job does not inherit an
        # interactive shell, so a sentinel that only exists because of /etc/profile.d
        # will be absent when it matters.
        log.warning("these solver variables came from the ambient shell rather than the "
                    "setup script, and a detached job will not inherit them: %s",
                    ", ".join(status.ambient))
    return dict(status.variables)


def _launch(jd: JobDir, spec: JobSpec, env, captured: dict[str, str],
            profile: profiles.Profile) -> None:
    if os.name == "nt" and str(getattr(env, "kind", "")) == "wsl":
        # The runner is a Windows process and enters WSL for every solver command; the captured Linux variables (a Linux PATH) would only break the Windows side.
        captured = {}
        captured.update(os.environ)
    elif not captured:
        try:
            captured = env.capture()
        except Exception as exc:                    # noqa: BLE001
            log.warning("could not capture the solver environment (%s); the job will "
                        "source it itself", exc)
            captured = dict(os.environ)
        else:
            pass
    else:
        captured = {**os.environ, **captured}

    # The marker the child reads to know it must not source the setup script again -
    # which is what keeps a shell out of the job's process tree.
    captured[ENTERED_MARKER] = "1"
    captured["AXQUA_JOB_DIR"] = str(jd.root)
    captured["PYTHONUNBUFFERED"] = "1"
    if env.distro:
        captured["AXQUA_WSL_DISTRO"] = str(env.distro)

    # "auto" on the command line means "no opinion", so the profile's choice applies.
    # Reading it as a choice of its own - which `or` does, since the string is truthy -
    # meant a launcher set in a profile was never used.
    requested = spec.launcher.get("requested") or "auto"
    if requested == "auto":
        requested = profile.launcher or "auto"
    chosen = launcher_mod.select_launcher(env, prefer=requested)
    argv = launcher_mod.runner_argv(jd.root)
    handle = chosen.submit(jd, argv, env=captured, cwd=jd.root)

    from axqua.jobs.store import read_status
    status = read_status(jd) or JobStatus.initial(spec)
    status.launcher = handle.as_dict()
    status.transition(JobState.STARTING)
    write_status(jd, status)


def _record_launch_failure(jd: JobDir, spec: JobSpec, exc: BaseException) -> None:
    from axqua.jobs.store import read_status
    try:
        status = read_status(jd) or JobStatus.initial(spec)
        status.error = errors.ErrorRecord.from_exception(exc).as_dict()
        status.transition(JobState.FAILED)
        write_status(jd, status)
    except Exception:                               # noqa: BLE001 - pragma: no cover
        log.debug("could not record the launch failure", exc_info=True)


def _index(jd: JobDir) -> None:
    try:
        row = index.row_for(jd)
        if row is not None:
            index.JobIndex().upsert(row)
    except Exception as exc:                        # noqa: BLE001
        # Bookkeeping never fails a submission (plan §24).
        log.debug("could not index %s: %s", jd.root, exc)


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:                               # noqa: BLE001 - pragma: no cover
        return "unknown"


def _version() -> str:
    from axqua import __version__
    return __version__
