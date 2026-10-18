"""``axqua docs``: where the documentation is, for the *Help* button of the plugin.

``axqua docs``           the local copy, if there is one, and the published address
``axqua docs --json``    the same for the plugin

*Help* opens a copy of the documentation that was built on this computer when there is one (the
user's decision of 2026-10-18), because it matches the installed version of aXqua, and the
published documentation at ``https://axqua.eu`` otherwise. A local copy is the folder named by
``$AXQUA_DOCS``, else the build of the source checkout that aXqua runs from:
``docs/_build/site`` (English, German and French, ``docs/i18n/build_i18n.sh``) before
``docs/_build/html`` (English, ``sphinx-build``).

**A browser in a sandbox cannot open every local file.** Firefox and Chromium as snap packages
(the default on Ubuntu) and browsers installed with Flatpak read files in the home folder only, and
none in a hidden folder there (``~/.local``) or in ``/tmp``. Where the default browser of a Linux
desktop is one of them and the copy lies outside of what it may read, the copy is reported as
unreadable and *Help* opens the published documentation instead.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

from axqua.jobcli import _common, emit, fail, parse_args

ENV_VAR = "AXQUA_DOCS"
PUBLISHED = "https://axqua.eu/"
LANGUAGES = ("en", "de", "fr")

#: Desktop files of browsers installed with Flatpak (their names are reverse domain names).
_FLATPAK = re.compile(r"^(org|com|io|net)\.[\w.-]+\.desktop$")
#: Desktop files of snap packages are named <snap>_<app>.desktop.
_SNAP = re.compile(r"^[a-z0-9-]+_[\w-]+\.desktop$")


def checkout() -> Path | None:
    """The source checkout aXqua runs from (an editable installation), None for a wheel."""
    import axqua

    root = Path(axqua.__file__).resolve().parents[2]
    return root if (root / "docs" / "conf.py").is_file() else None


def local_copy() -> Path | None:
    """The folder of a documentation built on this computer, None if there is none."""
    candidates: list[Path] = []
    named = os.environ.get(ENV_VAR, "").strip()
    if named:
        candidates.append(Path(named).expanduser())
    root = checkout()
    if root is not None:
        candidates += [root / "docs" / "_build" / "site", root / "docs" / "_build" / "html"]
    for folder in candidates:
        if (folder / "index.html").is_file():
            return folder.resolve()
    return None


def default_browser() -> str:
    """The desktop file of the default web browser of a Linux desktop, '' where unknown."""
    if not sys.platform.startswith("linux"):
        return ""
    program = shutil.which("xdg-settings")
    if program is None:
        return ""
    try:
        # a fixed argument list, no shell, a program found on PATH
        done = subprocess.run([program, "get", "default-web-browser"],  # nosec B603
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def sandboxed(browser: str) -> bool:
    """Whether a browser (its desktop file) runs in a snap or Flatpak sandbox."""
    return bool(browser) and bool(_SNAP.match(browser) or _FLATPAK.match(browser))


def readable_by_sandbox(folder: Path, home: Path | None = None) -> bool:
    """Whether a snap or Flatpak browser may read files in *folder*: in the home folder, and in
    no hidden folder there."""
    home = (home or Path.home()).resolve()
    try:
        inside = folder.resolve().relative_to(home)
    except ValueError:
        return False
    return not any(part.startswith(".") for part in inside.parts)


def describe(browser: str | None = None) -> dict:
    """What ``axqua docs --json`` answers."""
    folder = local_copy()
    browser = default_browser() if browser is None else browser
    answer = {"published": PUBLISHED, "local": None, "languages": [], "browser": browser,
              "readable": False, "reason": ""}
    if folder is None:
        answer["reason"] = "no documentation was built on this computer"
        return answer
    answer["local"] = str(folder)
    answer["languages"] = [lang for lang in LANGUAGES
                           if (folder / ("" if lang == "en" else lang) / "index.html").is_file()]
    if sandboxed(browser) and not readable_by_sandbox(folder):
        answer["reason"] = (f"the default browser ({browser}) runs in a sandbox that cannot read "
                            f"{folder}")
    else:
        answer["readable"] = True
    return answer


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axqua docs",
        description="Where the documentation is: a copy built on this computer, if there is "
                    f"one, and the published documentation ({PUBLISHED}).")
    return _common(p)


def run_docs(argv: list[str]) -> int:
    args = parse_args(_parser(), argv, "docs")
    try:
        answer = describe()
    except Exception as exc:  # noqa: BLE001
        return fail("docs", exc, as_json=args.as_json, verbose=args.verbose)
    lines = [f"published: {answer['published']}"]
    if answer["local"]:
        lines.append(f"local:     {answer['local']} ({', '.join(answer['languages'])})")
    if answer["reason"]:
        lines.append(f"           {answer['reason']}")
    return emit("docs", answer, as_json=args.as_json, lines=lines)
