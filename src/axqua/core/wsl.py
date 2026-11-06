"""The Windows Subsystem for Linux, seen from Windows.

On Windows, aXqua and its QGIS plugin run in Windows, and TELEMAC and OpenFOAM run inside a WSL distribution (the user's decision of 2026-11-06). This module is the one place that talks to ``wsl.exe`` and that converts paths between the two sides:

* ``C:\\Users\\me\\case`` is ``/mnt/c/Users/me/case`` inside the distribution;
* ``/home/<user>/opt`` inside the distribution ``Ubuntu-24.04`` is ``\\\\wsl.localhost\\Ubuntu-24.04\\home\\<user>\\opt`` in Windows (``\\\\wsl$\\...`` on older Windows), which is also what a Windows file dialog returns for a file inside WSL.

``wsl.exe`` writes its own messages in UTF-16 and in the language of Windows. Every call here sets ``WSL_UTF8=1`` and still decodes UTF-16 when the output has the NUL bytes of it, so a message reaches the user readable rather than as text full of NUL characters.

Standard library only, and nothing here imports a solver (the rule of :mod:`axqua.core`).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Sequence

__all__ = ["Distro", "WslStatus", "command", "decode", "default_distro", "distros", "executable",
           "from_unc", "home", "is_unc", "read_file", "run", "status", "to_linux", "to_unc",
           "to_windows"]

#: The share names under which Windows shows the file system of a distribution.
SHARES = ("wsl.localhost", "wsl$")
#: The one Windows writes for a path aXqua builds itself (Windows 10 21H2 and newer).
SHARE = "wsl.localhost"

_UNC = re.compile(r"^[\\/]{2}(wsl\.localhost|wsl\$)[\\/]([^\\/]+)(?:[\\/](.*))?$", re.IGNORECASE)


def _no_window() -> dict:
    """No console window for a call from QGIS, whose own process has none."""
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def executable() -> str | None:
    """The ``wsl.exe`` of this computer, or ``None`` (not Windows, or WSL not present)."""
    found = shutil.which("wsl.exe") or shutil.which("wsl")
    if found:
        return found
    if os.name == "nt":
        candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wsl.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def decode(raw: bytes | str | None) -> str:
    """Text of what ``wsl.exe`` or a program inside WSL wrote: UTF-16 where it has its NUL bytes, UTF-8 otherwise."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.replace("\x00", "")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16", "replace")
    elif len(raw) >= 2 and raw.count(b"\x00") >= len(raw) // 4:
        text = raw.decode("utf-16-le", "replace")
    else:
        text = raw.decode("utf-8", "replace")
    return text.replace("\x00", "").replace("\r\n", "\n")


def environment(base: dict | None = None) -> dict:
    """The environment of a ``wsl.exe`` call: the given one (or this process's) with ``WSL_UTF8=1``."""
    env = dict(os.environ if base is None else base)
    env["WSL_UTF8"] = "1"
    return env


def command(argv: Sequence[str], distro: str | None = None, *, user: str | None = None,
            cd: str | None = None) -> list[str]:
    """The argument list that runs *argv* inside *distro* (the default distribution when ``None``).

    *user* ``root`` runs it as the root of the distribution, which needs no Windows administrator and no password. *cd* is a folder inside the distribution to start in.
    """
    out = [executable() or "wsl.exe"]
    if distro:
        out += ["-d", distro]
    if user:
        out += ["-u", user]
    if cd:
        out += ["--cd", cd]
    # --exec, not --: after --, wsl.exe hands the command line to the default shell of the distribution, which would expand $(...) and re-split the quoting of Windows before our own bash sees it
    return out + ["--exec", *[str(a) for a in argv]]


@dataclass
class Completed:
    """What a call into the distribution returned, decoded."""

    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def message(self) -> str:
        """The text a failure is reported with: what the program wrote, stderr first."""
        return (self.stderr or self.stdout).strip()


def run(argv: Sequence[str], distro: str | None = None, *, user: str | None = None,
        cd: str | None = None, timeout: float = 60.0, input_text: str | None = None,
        env: dict | None = None) -> Completed:
    """Run *argv* inside *distro* and return its decoded output. Never raises for a failing program; raises :class:`OSError` only when ``wsl.exe`` cannot be started at all."""
    args = command(argv, distro, user=user, cd=cd)
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, env=environment(env),
                              input=input_text.encode("utf-8") if input_text is not None else None,
                              stdin=None if input_text is not None else subprocess.DEVNULL,
                              **_no_window())
    except subprocess.TimeoutExpired as exc:
        return Completed(args, 124, decode(exc.stdout), decode(exc.stderr) or
                         f"wsl.exe did not answer within {timeout:.0f} s")
    return Completed(args, proc.returncode, decode(proc.stdout), decode(proc.stderr))


@dataclass(frozen=True)
class Distro:
    """One distribution of this computer."""

    name: str
    default: bool = False
    version: int | None = None      # 1 or 2; None where wsl.exe did not say


def _parse_list(text: str) -> list[Distro]:
    """The rows of ``wsl.exe -l -v``: a header in the language of Windows, then ``[*] NAME STATE VERSION``."""
    rows = [line.rstrip() for line in text.splitlines() if line.strip()]
    out: list[Distro] = []
    for line in rows[1:]:
        default = line.lstrip().startswith("*")
        parts = line.replace("*", " ", 1).split() if default else line.split()
        if not parts:
            continue
        version = int(parts[-1]) if parts[-1].isdigit() and len(parts) >= 3 else None
        out.append(Distro(parts[0], default=default, version=version))
    return out


def distros(timeout: float = 30.0) -> list[Distro]:
    """The distributions installed for this user, the default one marked; empty without WSL."""
    if executable() is None:
        return []
    try:
        proc = subprocess.run([executable(), "-l", "-v"], capture_output=True, timeout=timeout,
                              env=environment(), stdin=subprocess.DEVNULL, **_no_window())
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return _parse_list(decode(proc.stdout))


def default_distro(found: Sequence[Distro] | None = None) -> str | None:
    """The name of the default distribution, else the first one, else ``None``."""
    found = distros() if found is None else found
    for distro in found:
        if distro.default:
            return distro.name
    return found[0].name if found else None


_DEFAULT: list[str | None] = []


def cached_default_distro() -> str | None:
    """:func:`default_distro`, asked once per process: a path conversion must not start a ``wsl.exe`` for every path."""
    if not _DEFAULT:
        _DEFAULT.append(default_distro())
    return _DEFAULT[0]


@dataclass
class WslStatus:
    """Whether TELEMAC and OpenFOAM can be installed and run in WSL on this computer."""

    present: bool                     # wsl.exe exists
    distros: list[Distro] = field(default_factory=list)
    message: str = ""                 # what wsl.exe said when no distribution answered

    @property
    def usable(self) -> bool:
        return self.present and any(d.version != 1 for d in self.distros)

    @property
    def default(self) -> str | None:
        return default_distro(self.distros) if self.distros else None

    def as_dict(self) -> dict:
        return {"present": self.present, "usable": self.usable, "default": self.default,
                "distros": [{"name": d.name, "default": d.default, "version": d.version}
                            for d in self.distros],
                "message": self.message}


def status(timeout: float = 30.0) -> WslStatus:
    """Ask ``wsl.exe`` (it installs nothing) which distributions this user has."""
    exe = executable()
    if exe is None:
        return WslStatus(present=False, message="wsl.exe was not found on this computer")
    found = distros(timeout=timeout)
    if found:
        return WslStatus(present=True, distros=found)
    try:
        proc = subprocess.run([exe, "--status"], capture_output=True, timeout=timeout,
                              env=environment(), stdin=subprocess.DEVNULL, **_no_window())
        said = (decode(proc.stdout) + "\n" + decode(proc.stderr)).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        said = str(exc)
    return WslStatus(present=True, message=said)


def home(distro: str | None = None, *, timeout: float = 60.0) -> PurePosixPath | None:
    """The home folder of the default user of *distro*, or ``None`` when it does not answer."""
    done = run(["sh", "-c", 'printf "%s" "$HOME"'], distro, timeout=timeout)
    text = done.stdout.strip()
    return PurePosixPath(text) if done.ok and text.startswith("/") else None


def read_file(path: str, distro: str | None = None, *, timeout: float = 60.0) -> str | None:
    """The text of a file inside *distro*, or ``None`` when it cannot be read."""
    done = run(["cat", path], distro, timeout=timeout)
    return done.stdout if done.ok else None


# ------------------------------------------------------------------- paths

def is_unc(path: str | os.PathLike) -> bool:
    """Whether *path* is a Windows path into a WSL distribution (``\\\\wsl.localhost\\<distro>\\...``)."""
    return _UNC.match(str(path)) is not None


def from_unc(path: str | os.PathLike) -> tuple[str, str] | None:
    """``\\\\wsl.localhost\\Ubuntu\\home\\<user>\\x.sh`` -> ``("Ubuntu", "/home/<user>/x.sh")``; ``None`` for another path."""
    match = _UNC.match(str(path))
    if match is None:
        return None
    rest = (match.group(3) or "").replace("\\", "/").strip("/")
    return match.group(2), "/" + rest if rest else "/"


def to_unc(distro: str, linux_path: str | os.PathLike) -> str:
    """``("Ubuntu", "/home/<user>/x.sh")`` -> ``\\\\wsl.localhost\\Ubuntu\\home\\<user>\\x.sh``."""
    rest = str(linux_path).replace("\\", "/").strip("/").replace("/", "\\")
    return f"\\\\{SHARE}\\{distro}\\{rest}" if rest else f"\\\\{SHARE}\\{distro}"


def to_linux(path: str | os.PathLike) -> str:
    """A path as a program inside WSL spells it.

    A drive path becomes ``/mnt/<drive>/...``, a path into a distribution (``\\\\wsl.localhost\\...``) its Linux path, and a path that is already a Linux path stays as it is, so the function can be applied twice.
    """
    text = str(path)
    if not text:
        return text
    unc = from_unc(text)
    if unc is not None:
        return unc[1]
    if text.startswith("/"):
        return text.replace("\\", "/")
    pure = PureWindowsPath(text)
    drive = pure.drive.rstrip(":").lower()
    if not drive or len(drive) != 1:
        return text.replace("\\", "/")
    rest = "/".join(pure.parts[1:])
    return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"


def to_windows(path: str | os.PathLike, distro: str | None = None) -> str:
    """A path inside WSL as Windows spells it: ``/mnt/c/x`` -> ``C:\\x``, ``/home/<user>/x`` -> ``\\\\wsl.localhost\\<distro>\\home\\<user>\\x``.

    Without a *distro*, a path outside ``/mnt/<drive>`` stays as it is (there is no Windows spelling for it).
    """
    text = str(path)
    if is_unc(text) or not text.startswith("/"):
        return text
    parts = PurePosixPath(text).parts
    if len(parts) >= 3 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha():
        drive = parts[2].upper()
        rest = "\\".join(parts[3:])
        return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
    return to_unc(distro, text) if distro else text
