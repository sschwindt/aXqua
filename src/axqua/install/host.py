"""What kind of computer this is, as far as an installation is concerned.

The installer scripts exist per operating system release, so the first thing an
installation needs to know is which release it runs on. The answer is the *base* of the
system, the release whose packages it uses: ``debian12``, ``ubuntu22`` or ``ubuntu24``.
Linux Mint reports its Ubuntu base in ``UBUNTU_CODENAME`` and is mapped through it. A
derivative that is not recognized has no base, and a user who knows the base of their
system can name it (``--base``).

``ID_LIKE`` is deliberately not enough to assign a base: Ubuntu itself says it is like
Debian, and its packages are not Debian's. The rule and the three bases are the ones of
the installer scripts, so that aXqua never offers an installation the scripts refuse.

The Windows Subsystem for Linux is Linux here: the simulation programs are built and run
inside the distribution. It is reported separately because an installation must stay in
the Linux file system; a folder below ``/mnt/c`` is slow by an order of magnitude and the
OpenFOAM installer refuses it.

Standard library only.
"""

from __future__ import annotations

import os
import platform as _platform
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

__all__ = ["BASES", "Host", "base_of", "detect", "parse_os_release"]

#: The releases the installer scripts support, with the name a person reads.
BASES = {
    "debian12": "Debian 12",
    "ubuntu22": "Ubuntu 22.04",
    "ubuntu24": "Ubuntu 24.04",
}

_UBUNTU = {"22.04": "ubuntu22", "24.04": "ubuntu24"}
_UBUNTU_CODENAMES = {"jammy": "ubuntu22", "noble": "ubuntu24"}


@dataclass(frozen=True)
class Host:
    """The computer an installation would run on."""

    system: str             # linux | windows | darwin | other
    distro: str = ""        # debian | ubuntu | linuxmint | ...
    version: str = ""       # "12", "24.04"
    codename: str = ""
    base: str = ""          # a key of BASES, or "" when the system is not supported
    wsl: bool = False
    machine: str = ""       # x86_64, aarch64, ...
    pretty: str = ""        # what the system calls itself

    @property
    def supported(self) -> bool:
        """Whether the installer scripts cover this system at all."""
        return self.system == "linux" and self.base in BASES

    @property
    def x86_64(self) -> bool:
        return self.machine.lower() in ("x86_64", "amd64")

    def describe(self) -> str:
        """One line for a person."""
        name = self.pretty or self.distro or self.system
        parts = [name]
        if self.wsl:
            parts.append("in the Windows Subsystem for Linux")
        if self.machine:
            parts.append(self.machine)
        return ", ".join(parts)

    def as_dict(self) -> dict:
        return {**asdict(self), "supported": self.supported,
                "description": self.describe(),
                "base_name": BASES.get(self.base, "")}


def parse_os_release(text: str) -> dict[str, str]:
    """The ``KEY=value`` lines of ``/etc/os-release`` as a dictionary."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def base_of(os_release: Mapping[str, str]) -> str:
    """The supported release this system is built on, or ``""``."""
    distro = os_release.get("ID", "").lower()
    version = os_release.get("VERSION_ID", "")
    if distro == "debian":
        return "debian12" if version == "12" else ""
    if distro == "ubuntu":
        return _UBUNTU.get(version, "")
    if distro == "linuxmint":
        return _UBUNTU_CODENAMES.get(os_release.get("UBUNTU_CODENAME", "").lower(), "")
    return ""


def detect(*, os_release: str | None = None, system: str | None = None,
           release: str | None = None, machine: str | None = None,
           base: str | None = None) -> Host:
    """Describe this computer. Every argument replaces what would be read, for tests.

    *base* is the user's own statement about an unrecognized derivative and wins over
    what the system reports.
    """
    system = (system or sys.platform).lower()
    machine = machine if machine is not None else _platform.machine()
    if system.startswith("win") or system in ("cygwin", "msys"):
        return Host(system="windows", machine=machine,
                    pretty=f"Windows {_platform.release()}".strip())
    if system == "darwin":
        return Host(system="darwin", machine=machine,
                    pretty=f"macOS {_platform.mac_ver()[0]}".strip())
    if not system.startswith("linux"):
        return Host(system="other", machine=machine, pretty=system)

    if os_release is None:
        try:
            os_release = Path("/etc/os-release").read_text(encoding="utf-8")
        except OSError:
            os_release = ""
    fields = parse_os_release(os_release)
    kernel = (release if release is not None else _platform.release()).lower()
    found = base if base in BASES else base_of(fields)
    return Host(
        system="linux",
        distro=fields.get("ID", "").lower(),
        version=fields.get("VERSION_ID", ""),
        codename=(fields.get("VERSION_CODENAME") or fields.get("UBUNTU_CODENAME")
                  or "").lower(),
        base=found,
        wsl="microsoft" in kernel or bool(os.environ.get("WSL_DISTRO_NAME")),
        machine=machine,
        pretty=fields.get("PRETTY_NAME") or fields.get("NAME", ""),
    )
