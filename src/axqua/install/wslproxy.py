"""Installing TELEMAC and OpenFOAM into WSL from Windows.

On Windows, aXqua and its QGIS plugin run in Windows and the simulation programs inside a WSL distribution (the user's decision of 2026-11-06). The installers are Linux programs, so this module runs **the installer code of this very aXqua inside the distribution**: the distribution's ``python3`` imports the ``axqua`` package from where Windows has it (``/mnt/c/...``). :mod:`axqua.install` needs nothing but the standard library for that, which a test keeps true, so nothing has to be installed in the distribution and nothing of the Linux installation logic is written twice.

What stays on the Windows side: the folder of an installation (``%LOCALAPPDATA%\\axqua\\installs``, which the runner inside WSL reaches as ``/mnt/c/...``), the ``wsl.exe`` that keeps the runner and with it the WSL machine alive while it runs, and the profile, into which a finished installation is entered as ``environment: wsl`` with its distribution (:func:`axqua.install.runner.read`).

Before this, the documentation asked for a second aXqua inside the distribution, whose installer wrote the profile of that second aXqua, so the plugin in Windows never saw what had been installed (the student's report of 2026-11-06).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Sequence

from axqua.core import wsl
from axqua.core.errors import ConfigError
from axqua.core.errors import EnvironmentError as AxquaEnvironmentError

__all__ = ["choose_distro", "call", "package_root", "remote_command", "remote_env"]

#: The program the distribution's python3 runs: this aXqua's command line, from the Windows folder of the package, first on the path, so that an older aXqua installed in the distribution (as the documentation once asked) cannot answer in its place. The install code imports the standard library only, so nothing else of the Windows folder is read.
LAUNCH = ("import runpy, sys; sys.path.insert(0, {root!r}); sys.argv = ['axqua'] + sys.argv[1:]; "
          "runpy.run_module('axqua', run_name='__main__', alter_sys=True)")


def package_root() -> str:
    """The folder that holds the ``axqua`` package, as the distribution spells it."""
    import axqua
    return wsl.to_linux(str(Path(axqua.__file__).resolve().parents[1]))


def remote_env(distro: str) -> list[str]:
    """``VAR=value`` settings for the Linux side: where installations are recorded (the Windows folder), UTF-8, and the distribution's own name for the runner's administrator steps."""
    from axqua.jobs import paths
    return [f"AXQUA_DATA_DIR={wsl.to_linux(str(paths.data_dir()))}", "PYTHONDONTWRITEBYTECODE=1",
            "PYTHONUTF8=1", "PYTHONIOENCODING=utf-8", f"AXQUA_WSL_DISTRO={distro}"]


def remote_command(args: Sequence[str], distro: str, *, user: str | None = None) -> list[str]:
    """The ``wsl.exe`` argument list that runs ``axqua <args>`` inside *distro*."""
    code = LAUNCH.format(root=package_root())
    return wsl.command(["env", *remote_env(distro), "python3", "-c", code, *[str(a) for a in args]],
                       distro, user=user)


def choose_distro(requested: str | None = None) -> tuple[str | None, wsl.WslStatus]:
    """The distribution to install into (*requested*, else the default one) and what ``wsl.exe`` said."""
    status = wsl.status()
    usable = [d.name for d in status.distros if d.version != 1]
    if requested:
        return (requested if requested in usable else None), status
    default = status.default
    return (default if default in usable else (usable[0] if usable else None)), status


def call(args: Sequence[str], distro: str, *, timeout: float | None = 600.0,
         stream_stderr: bool = False) -> dict[str, Any]:
    """Run ``axqua <args> --json`` inside *distro* and return its answer (``ok``, ``data``, ``error``).

    Raises :class:`~axqua.core.errors.EnvironmentError` when no answer comes back: no ``python3`` in the distribution, or ``wsl.exe`` failing, with what it said on a line of its own.
    """
    argv = list(args) + ([] if "--json" in args else ["--json"])
    command = remote_command(argv, distro)
    kwargs: dict[str, Any] = {"env": wsl.environment(), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE,
                              stderr=None if stream_stderr else subprocess.PIPE,
                              timeout=timeout, **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AxquaEnvironmentError(f"wsl.exe could not be started for the distribution {distro}",
                                    subject="install.distro", remedy=str(exc)) from exc
    text = wsl.decode(done.stdout).strip()
    start = text.find("{")
    try:
        answer = json.loads(text[start:]) if start >= 0 else None
    except ValueError:
        answer = None
    if isinstance(answer, dict) and "ok" in answer:
        return answer
    said = (text + "\n" + wsl.decode(done.stderr or b"")).strip()
    if "python3" in said and ("not found" in said or "No such file" in said):
        raise AxquaEnvironmentError(
            f"the distribution {distro} has no python3, which the installers of TELEMAC and OpenFOAM need",
            subject="install.distro",
            remedy=f"Install it (no administrator of Windows needed): wsl -d {distro} -u root -- "
                   "sh -c 'apt-get update && apt-get install -y python3'")
    raise AxquaEnvironmentError(
        f"aXqua inside the distribution {distro} gave no answer (exit code {done.returncode})",
        subject="install.distro",
        remedy="System output:\n" + said[-1500:] if said else "")


def require(answer: dict[str, Any]) -> Any:
    """The ``data`` of a successful *answer*; a failure is raised with its own message and remedy."""
    if answer.get("ok"):
        return answer.get("data")
    error = answer.get("error") or {}
    raise ConfigError(str(error.get("message") or "the installation inside WSL failed"),
                      subject=str(error.get("subject") or "install"),
                      remedy=str(error.get("remedy") or ""))
