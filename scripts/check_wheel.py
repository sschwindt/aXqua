#!/usr/bin/env python3
"""Check a built wheel of aXqua against the checkout it was built from.

A wheel is what PyPI serves and cannot be replaced once uploaded, so what it carries is
checked before an upload rather than found missing by a user:

* the package holds exactly the tracked files of ``src/axqua`` (the notes for developers,
  ``CLAUDE.md``, aside): nothing left out of the package data, and nothing a working tree
  happened to contain, such as the ``__pycache__`` a run of the vendored installer's own
  tests leaves behind;
* the metadata names the license expression of ``pyproject.toml``, and every file of its
  ``license-files`` is in the wheel, so the vendored GPL sources travel with their license.

Usage::

    python scripts/check_wheel.py dist/axqua-<version>-py3-none-any.whl
"""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Tracked files of the package that are notes for developers, not part of the program.
NOT_SHIPPED = {"CLAUDE.md"}


def _pyproject() -> str:
    return (REPO / "pyproject.toml").read_text("utf-8")


def _license_expression() -> str:
    # A regex rather than tomllib, which is 3.11+ while aXqua supports 3.10.
    return re.search(r'^license\s*=\s*"([^"]+)"', _pyproject(), re.M).group(1)


def _license_files() -> list[str]:
    block = re.search(r"^license-files\s*=\s*\[(.*?)\]", _pyproject(), re.M | re.S).group(1)
    found: list[str] = []
    for pattern in re.findall(r'"([^"]+)"', block):
        matches = sorted(p.relative_to(REPO).as_posix() for p in REPO.glob(pattern))
        if not matches:
            found.append(pattern)          # reported as missing below
        found += matches
    return found


def _tracked_package_files() -> set[str]:
    out = subprocess.run(["git", "ls-files", "src/axqua"], cwd=REPO, check=True,
                         capture_output=True, text=True).stdout.split("\n")
    return {name[len("src/"):] for name in out
            if name and Path(name).name not in NOT_SHIPPED}


def check(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        info = next(n for n in names if n.endswith(".dist-info/METADATA"))
        metadata = archive.read(info).decode("utf-8")
    dist_info = info.rsplit("/", 1)[0]
    problems: list[str] = []

    package = {n for n in names if n.startswith("axqua/") and not n.endswith("/")}
    tracked = _tracked_package_files()
    for name in sorted(tracked - package):
        problems.append(f"missing from the wheel: {name}")
    for name in sorted(package - tracked):
        problems.append(f"in the wheel but not a tracked file of src/axqua: {name}")

    expression = _license_expression()
    if f"License-Expression: {expression}" not in metadata.splitlines():
        problems.append(f"the metadata does not carry License-Expression: {expression}")
    for name in _license_files():
        if f"{dist_info}/licenses/{name}" not in names:
            problems.append(f"license file missing from the wheel: {name}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].endswith(".whl"):
        print(__doc__.strip().rsplit("Usage::", 1)[-1].strip())
        return 2
    problems = check(Path(argv[0]))
    for line in problems:
        print(line)
    if problems:
        return 1
    print(f"{Path(argv[0]).name}: the package and its licenses are complete")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
