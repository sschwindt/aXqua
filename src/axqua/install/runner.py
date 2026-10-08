"""Run an installation as a process of its own, and say how far it is.

Building TELEMAC takes most of an hour and compiling OpenFOAM several, so an
installation must not live inside the window that started it. It is a folder below
aXqua's data folder, like a job:

``plan.json``     what was started, written once
``status.json``   how far it is; written only by the process that runs it
``install.log``   everything the installer printed
``runner.log``    what aXqua itself had to say

:func:`start` writes the folder and starts ``axqua install run <folder>`` detached, so
that QGIS can be closed. :func:`read` reports the state and notices by itself when the
process is gone without a result (the computer was restarted): an installation never
stays "running" forever.

When the installer has finished, the runner does what the scripts cannot: it checks that
the promised files exist (the TELEMAC script ends with success even when its own check of
the build failed), enters the installation into the profile of this computer, and enters
the environment once to see whether the program is really there.

Each command runs in a session of its own. Cancelling therefore reaches the whole tree of
a build (``make`` and its compilers) and nothing else, whether the runner was started
detached or in a terminal.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from axqua.core.diagnostics import ERROR, WARNING, Finding
from axqua.install import recipes

log = logging.getLogger("axqua.install")

__all__ = ["ACTIVE", "bind", "cancel", "execute", "installs_root", "latest", "list_all",
           "read", "start", "tail"]

ACTIVE = ("queued", "running")
TERMINAL = ("succeeded", "failed", "cancelled")

#: Keys of ``outputs`` whose file may be missing without failing the installation.
OPTIONAL = ("postprocessors.paraview",)

#: Seconds a queued installation may take to start before it counts as lost.
START_GRACE = 60.0


def installs_root() -> Path:
    from axqua.jobs import paths
    return paths.data_dir() / "installs"


# ---------------------------------------------------------------------------- files


def _write(folder: Path, status: dict[str, Any]) -> None:
    tmp = folder / ".status.json.tmp"
    tmp.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, folder / "status.json")


def _load(folder: Path) -> dict[str, Any]:
    for _ in range(5):
        try:
            return json.loads((folder / "status.json").read_text(encoding="utf-8"))
        except ValueError:
            time.sleep(0.05)                    # caught between two writes
    return {}


def _folder(ident: str | os.PathLike) -> Path:
    path = Path(ident)
    if path.is_dir():
        return path
    return installs_root() / str(ident)


def tail(path: str | os.PathLike, lines: int = 40) -> str:
    """The last *lines* of a log, without reading a file of many megabytes whole."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 64_000))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
    # A build draws its progress with carriage returns; keep what was shown last.
    rows = [row.rsplit("\r", 1)[-1] for row in text.splitlines()]
    return "\n".join(rows[-lines:])


# --------------------------------------------------------------------------- reading


def _alive(status: dict[str, Any]) -> bool | None:
    """Whether the runner of *status* still exists; ``None`` when it cannot be told."""
    from axqua.jobs import procs

    if status.get("host") and status["host"] != procs.host_id():
        return None                             # another computer's installation
    pid = status.get("pid")
    if not pid:
        return None
    if not procs.pid_alive(int(pid)):
        return False
    began, now = status.get("proc_started"), procs.process_start_time(int(pid))
    if began is not None and now is not None and abs(float(now) - float(began)) > 1e-6:
        return False                            # the number belongs to another process
    return True


def read(ident: str | os.PathLike, *, lines: int = 0) -> dict[str, Any]:
    """The state of one installation, corrected if its process has vanished."""
    from axqua.core.errors import ConfigError

    folder = _folder(ident)
    status = _load(folder)
    if not status:
        raise ConfigError(f"no installation {Path(ident).name}", subject=str(ident),
                          remedy="List them with 'axqua install list'.")
    if status.get("state") in ACTIVE:
        alive = _alive(status)
        lost = alive is False or (
            alive is None and not status.get("pid")
            and time.time() - float(status.get("created") or 0) > START_GRACE)
        if lost:
            cancelled = (folder / "cancel.request").exists()
            status.update(
                state="cancelled" if cancelled else "failed",
                finished=time.time(),
                message=("cancelled" if cancelled else
                         "the installation ended without a result; the computer may "
                         "have been restarted"))
            if not cancelled:
                status.setdefault("findings", []).append(Finding(
                    ERROR, "axqua.install.interrupted", status["message"],
                    subject=f"install.{status.get('target', '')}",
                    remedy="Start the installation again. It continues with what is "
                           "already downloaded and built.").as_dict())
            _write(folder, status)
    status["folder"] = str(folder)
    status["log"] = str(folder / "install.log")
    end = status.get("finished") or time.time()
    if status.get("started"):
        status["elapsed"] = max(0.0, float(end) - float(status["started"]))
    if lines:
        status["log_tail"] = tail(folder / "install.log", lines)
    return status


def list_all() -> list[dict[str, Any]]:
    """Every installation on record, newest first."""
    root = installs_root()
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(root.iterdir(), reverse=True):
        if (folder / "status.json").is_file():
            try:
                out.append(read(folder))
            except Exception:                   # noqa: BLE001 - one bad folder
                continue
    return out


def latest(target: str, *, active_only: bool = False) -> dict[str, Any] | None:
    """The newest installation of *target*, or only one that is still running."""
    for status in list_all():
        if status.get("target") != target:
            continue
        if not active_only or status.get("state") in ACTIVE:
            return status
    return None


# -------------------------------------------------------------------------- starting


def start(plan: recipes.Plan, *, detach: bool = True, echo: bool = False
          ) -> dict[str, Any]:
    """Record *plan* and run it. Detached, this returns at once."""
    from axqua.core.errors import ConfigError
    from axqua.core.errors import EnvironmentError as AxquaEnvironmentError

    if not plan.ready:
        first = next((f for f in plan.findings if f.severity == ERROR), None)
        raise ConfigError(
            first.message if first else "this installation has nothing to run",
            subject=f"install.{plan.target}", remedy=first.remedy if first else "")
    running = latest(plan.target, active_only=True)
    if running is not None:
        raise AxquaEnvironmentError(
            f"an installation of {recipes.TITLES[plan.target]} is already running "
            f"({running['id']})", subject=running["id"],
            remedy="Wait for it, or cancel it with 'axqua install cancel "
                   f"{running['id']}'.")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    installs_root().mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 100):
        # two installations may be started within one second: the second gets a suffix
        ident = f"{stamp}-{plan.target}" + (f"-{attempt}" if attempt > 1 else "")
        folder = installs_root() / ident
        try:
            folder.mkdir()
            break
        except FileExistsError:
            continue
    else:                                       # pragma: no cover - 99 in one second
        raise AxquaEnvironmentError("no folder for this installation could be made",
                                    subject=str(installs_root()))
    (folder / "plan.json").write_text(json.dumps(plan.as_dict(), indent=2, default=str),
                                      encoding="utf-8")
    from axqua.jobs import procs
    _write(folder, {
        "id": ident, "target": plan.target, "title": recipes.TITLES[plan.target],
        "state": "queued", "created": time.time(), "host": procs.host_id(),
        "step": 0, "steps": [step.name for step in plan.steps], "step_name": "",
        "folder_installed": str(plan.folder), "detached": detach, "message": "",
        "findings": [], "outputs": {}, "bound": "",
    })
    if not detach:
        execute(folder, echo=echo)
        return read(folder)

    with open(folder / "runner.log", "ab") as out:
        subprocess.Popen(                       # the runner; it outlives this process
            [sys.executable, "-m", "axqua", "install", "run", str(folder)],
            cwd=str(folder), stdin=subprocess.DEVNULL, stdout=out,
            stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    return read(folder)


# ------------------------------------------------------------------------- executing


class _Stop(Exception):
    """The installation was asked to stop."""


def execute(folder: str | os.PathLike, *, echo: bool = False) -> int:
    """Run the commands of an installation folder. This is the runner process."""
    from axqua.jobs import procs

    folder = Path(folder)
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    steps = [recipes.Step.from_dict(step) for step in plan.get("steps") or []]
    status = _load(folder)
    status.update(state="running", started=time.time(), pid=os.getpid(),
                  proc_started=procs.self_start_time(), host=procs.host_id())
    _write(folder, status)

    stop = {"asked": False}

    def ask_to_stop(*_args) -> None:
        stop["asked"] = True

    previous = {}
    for number in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[number] = signal.signal(number, ask_to_stop)
        except ValueError:                      # not the main thread (a test)
            pass

    def finish(state: str, message: str = "", code: int = 0) -> int:
        status.update(state=state, finished=time.time(), message=message,
                      child_pid=None)
        _write(folder, status)
        for number, handler in previous.items():
            signal.signal(number, handler)
        return code

    def add(finding: Finding) -> None:
        status.setdefault("findings", []).append(finding.as_dict())

    subject = f"install.{plan.get('target', '')}"
    log_path = folder / "install.log"
    try:
        with open(log_path, "ab") as out:
            for number, step in enumerate(steps, start=1):
                status.update(step=number, step_name=step.name)
                _write(folder, status)
                header = (f"\n=== {number}/{len(steps)}: {step.name} ===\n"
                          f"$ {step.as_dict()['command']}\n")
                out.write(header.encode("utf-8"))
                out.flush()
                if echo:
                    print(header, end="", flush=True)
                code = _run_step(step, folder, out, status, stop, echo=echo,
                                 log_path=log_path)
                if code:
                    add(Finding(
                        ERROR, "axqua.install.failed",
                        f"'{step.name}' ended with an error (exit code {code}). The "
                        "last lines of the log say why: " + _last_words(log_path),
                        subject=subject,
                        remedy=f"The complete log is {log_path}. After correcting the "
                               "cause, start the installation again; it continues "
                               "with what is already there."))
                    return finish("failed", f"'{step.name}' failed", 1)
    except _Stop:
        return finish("cancelled", "cancelled", 130)
    except OSError as exc:
        add(Finding(ERROR, "axqua.install.failed",
                    f"a command of the installation could not be started: {exc}",
                    subject=subject))
        return finish("failed", str(exc), 1)

    # ---- what the installer promised -------------------------------------------
    outputs, absent = _resolve(plan.get("outputs") or {})
    for pattern in plan.get("expects") or []:
        if not glob.glob(pattern):
            absent.append(pattern)
    required = [item for item in absent if item not in
                [plan["outputs"].get(key) for key in OPTIONAL]]
    status["outputs"] = outputs
    if required:
        add(Finding(
            ERROR, "axqua.install.output_missing",
            "the installer finished, but did not produce " + ", ".join(required),
            subject=subject,
            remedy=f"The log {log_path} says which part of the build failed."))
        return finish("failed", "the installation is incomplete", 1)
    # ---- the profile -----------------------------------------------------------
    known: set[str] = set()
    if (plan.get("options") or {}).get("bind", True) and outputs:
        try:
            written, found, known = bind(outputs)
            status["bound"] = str(written)
            for finding in found:
                add(finding)
        except Exception as exc:                # noqa: BLE001 - the build succeeded
            add(Finding(
                WARNING, "axqua.install.not_bound",
                f"the installation could not be entered in the profile: {exc}",
                subject=subject,
                remedy="Enter it in the profile editor: "
                       + ", ".join(f"{key} = {value}" for key, value in outputs.items())))
    if "postprocessors.paraview" in (plan.get("outputs") or {}) \
            and "postprocessors.paraview" not in outputs \
            and "postprocessors.paraview" not in known:
        add(Finding(WARNING, "axqua.install.paraview_missing",
                    "ParaView is not installed, so it was not entered in the profile",
                    subject="postprocessors.paraview",
                    remedy="Install the system package paraview and enter "
                           "/usr/bin/paraview in the profile."))
    return finish("succeeded", "installed")


def _run_step(step: recipes.Step, folder: Path, out, status: dict[str, Any],
              stop: dict[str, bool], *, echo: bool, log_path: Path) -> int:
    proc = subprocess.Popen(
        step.argv, cwd=step.cwd or str(folder),
        env={**recipes.clean_environment(), **step.env},
        stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True)
    status["child_pid"] = proc.pid
    _write(folder, status)
    shown = log_path.stat().st_size if echo else 0
    while True:
        try:
            code = proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            code = None
        if echo:
            shown = _echo(log_path, shown)
        if code is not None:
            return code
        if stop["asked"] or (folder / "cancel.request").exists():
            _kill_group(proc.pid)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                _kill_group(proc.pid, signal.SIGKILL)
                proc.wait()
            raise _Stop()


def _echo(log_path: Path, shown: int) -> int:
    """Copy what the log has gained to the terminal of a foreground installation."""
    try:
        with open(log_path, "rb") as handle:
            handle.seek(shown)
            fresh = handle.read()
    except OSError:
        return shown
    if fresh:
        sys.stdout.write(fresh.decode("utf-8", "replace"))
        sys.stdout.flush()
    return shown + len(fresh)


def _kill_group(pid: int, sig: int = signal.SIGTERM) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _last_words(log_path: Path, lines: int = 3) -> str:
    # without the two lines the runner itself writes above each command
    rows = [row.strip() for row in tail(log_path, 30).splitlines()
            if row.strip() and not row.startswith(("=== ", "$ "))]
    return " | ".join(rows[-lines:])[:600] or "(the log is empty)"


def _resolve(outputs: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """The files an installation produced: ``(found, patterns without a file)``."""
    found: dict[str, str] = {}
    absent: list[str] = []
    for key, pattern in outputs.items():
        matches = sorted(glob.glob(pattern)) if glob.has_magic(pattern) else \
            ([pattern] if Path(pattern).exists() else [])
        if matches:
            found[key] = matches[-1]
        else:
            absent.append(pattern)
    return found, absent


# --------------------------------------------------------------------------- profile


def bind(outputs: dict[str, str], *, probe: bool = True
         ) -> tuple[Path, list[Finding], set[str]]:
    """Enter an installation into the profile of this computer and check it.

    Returns where the profile was written, what the check found about the new entries,
    and the keys of every program the profile names afterwards.

    A computer without a profile gets one, filled with what aXqua finds on it, exactly
    as *Create profile* does. A profile that exists but cannot be read is left alone,
    because writing over it would discard whatever its owner was in the middle of.
    """
    from axqua.core import profile as profiles

    path = profiles.active_path()
    if path is not None and path.is_file():
        current = profiles.load(path)           # raises for a profile with an error
    else:
        current = profiles.detect()
        path = path or profiles.default_path()
    subjects = []
    for key, value in outputs.items():
        section, name = key.split(".")[:2]
        if section == "solvers":
            binding = current.solvers.get(name) or profiles.SolverBinding()
            binding.setup_script = Path(value)
            current.solvers[name] = binding
            subjects.append(f"solvers.{name}")
        elif section == "postprocessors":
            current.postprocessors[name] = Path(value)
            subjects.append(f"postprocessors.{name}")
    written = profiles.save(current, path)
    found = [f for f in profiles.check(current, probe=probe)
             if any(f.subject.startswith(subject) for subject in subjects)
             and f.code != "axqua.environment.ambient"]
    known = {f"solvers.{name}" + ".setup_script" for name, entry in
             current.solvers.items() if entry.setup_script}
    known |= {f"postprocessors.{name}" for name in current.postprocessors}
    return written, found, known


# ------------------------------------------------------------------------ cancelling


def cancel(ident: str | os.PathLike, *, grace: float = 20.0) -> dict[str, Any]:
    """Stop an installation. What is already downloaded and built stays."""
    folder = _folder(ident)
    status = read(folder)
    if status.get("state") not in ACTIVE:
        return status
    (folder / "cancel.request").write_text(str(time.time()), encoding="utf-8")
    pid = status.get("pid")
    if pid and _alive(status):
        try:
            os.kill(int(pid), signal.SIGTERM)   # the runner stops its own command
        except OSError:
            pass
        deadline = time.time() + grace
        while time.time() < deadline and _alive(_load(folder) or status):
            time.sleep(0.2)
        if _alive(status):                      # it did not listen
            if status.get("child_pid"):
                _kill_group(int(status["child_pid"]), signal.SIGKILL)
            try:
                os.kill(int(pid), signal.SIGKILL)
            except OSError:
                pass
            time.sleep(0.3)
    return read(folder)
