"""``axqua docs``: where the documentation is, for the *Help* button of the plugin.

``axqua docs``           the local copy, if there is one, and the published address
``axqua docs --json``    the same for the plugin

*Help* opens a copy of the documentation that was built on this computer when there is one (the user's decision of 2026-10-18), because it matches the installed version of aXqua, and the published documentation at ``https://axqua.eu`` otherwise. A local copy is the folder named by ``$AXQUA_DOCS``, else the build of the source checkout that aXqua runs from: ``docs/_build/site`` (English, German and French, ``docs/i18n/build_i18n.sh``) before ``docs/_build/html`` (English, ``sphinx-build``).

**A browser in a sandbox cannot open every local file.** Firefox and Chromium as snap packages (the default on Ubuntu) and browsers installed with Flatpak read files in the home folder only, and none in a hidden folder there (``~/.local``) or in ``/tmp``. Where the default browser of a Linux desktop is one of them and the copy lies outside of what it may read, the copy is reported as unreadable and *Help* opens the published documentation instead. Which kind of package a browser is, is told by the folder its desktop file lies in (snap and Flatpak keep them in folders of their own); only where that file cannot be found does its name decide, and a name that may be one of a sandbox counts as one, because a published page always opens and a local one the browser may not read does not.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

from axqua.jobcli import _common, emit, fail, parse_args

ENV_VAR = "AXQUA_DOCS"
PUBLISHED = "https://axqua.eu/"
LANGUAGES = ("en", "de", "fr")

#: Desktop files of browsers installed with Flatpak have reverse domain names, but so have some of the operating system (org.gnome.Epiphany.desktop); used only where the file is not found.
_FLATPAK = re.compile(r"^(org|com|io|net)\.[\w.-]+\.desktop$")
#: Desktop files of snap packages are named <snap>_<app>.desktop.
_SNAP = re.compile(r"^[a-z0-9-]+_[\w-]+\.desktop$")
#: Folders in the path of a desktop file that a snap or a Flatpak exports.
_SANDBOX_FOLDERS = {"flatpak", "snapd"}
#: How a desktop file or a launcher script starts a snap (/snap/bin/firefox, snap run firefox) or a Flatpak (flatpak run org.chromium.Chromium).
_SANDBOX_EXEC = re.compile(r"/snap/bin/|\b(?:flatpak|snap)\s+run\b")
#: A launcher script is read up to this many bytes; a program is not.
_SCRIPT_BYTES = 65536


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


def application_folders() -> list[Path]:
    """Where desktop files are looked for, in the order of the XDG specification, then the export folders of Flatpak and snap (QGIS may run without them in ``XDG_DATA_DIRS``)."""
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    data = [Path(p) for p in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if p]
    exports = [home / "flatpak" / "exports" / "share", Path("/var/lib/flatpak/exports/share"),
               Path("/var/lib/snapd/desktop")]
    return list(dict.fromkeys(folder / "applications" for folder in [home, *data, *exports]))


def desktop_files(browser: str, folders: list[Path] | None = None) -> list[Path]:
    """The desktop files named *browser*, in the order the desktop looks for them (the first one is the one it uses)."""
    return [folder / browser for folder in (application_folders() if folders is None else folders)
            if (folder / browser).is_file()]


def _exec_line(path: Path) -> str:
    """The command a desktop file starts (``Exec`` of its group ``[Desktop Entry]``, where the key may have spaces around ``=``), '' where it cannot be read."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    group = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1]
        elif group == "Desktop Entry":
            key, sep, value = line.partition("=")
            if sep and key.strip() == "Exec":
                return value.strip()
    return ""


def _starts_sandbox(command: str) -> bool:
    """Whether a command of a desktop file starts a snap or a Flatpak: named so, or its program found on ``PATH`` is the snap launcher (``/snap/bin/firefox`` is a link to ``/usr/bin/snap``), lies in a Flatpak folder, or is a launcher script that starts one (Ubuntu's ``/usr/bin/firefox``)."""
    if _SANDBOX_EXEC.search(command):
        return True
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    while words and (Path(words[0]).name == "env" or re.fullmatch(r"\w+=.*", words[0])):
        words = words[1:]           # env VAR=value ... program
    if not words:
        return False
    found = words[0] if words[0].startswith("/") else shutil.which(words[0])
    if not found:
        return False
    real = Path(os.path.realpath(found))
    if real.name in ("snap", "flatpak") or "flatpak" in real.parts or real.parts[1:2] == ("snap",):
        return True
    try:
        with real.open("rb") as handle:
            head = handle.read(_SCRIPT_BYTES)
    except OSError:
        return False
    return head.startswith(b"#!") and bool(_SANDBOX_EXEC.search(head.decode("utf-8", "replace")))


def sandboxed(browser: str, folders: list[Path] | None = None) -> bool:
    """Whether a browser (its desktop file) runs in a snap or Flatpak sandbox.

    Told by the desktop files of that name: one in a folder that snap or Flatpak export, or the one the desktop uses (the first found) starting a snap or a Flatpak. A copy in ``~/.local/share/applications``, which a menu editor writes, loses the folder but keeps the command, and the file of the snap itself is still there; on Ubuntu even ``/usr/bin/firefox`` starts the snap, so a copy is never taken for a browser of the system while a sandboxed file of its name exists.
    """
    if not browser:
        return False
    found = desktop_files(browser, folders)
    if not found:
        return bool(_SNAP.match(browser) or _FLATPAK.match(browser))
    for path in found:
        parts = set(path.parts)
        try:
            parts |= set(path.resolve().parts)
        except OSError:
            pass
        if parts & _SANDBOX_FOLDERS:
            return True
    return _starts_sandbox(_exec_line(found[0]))


def readable_by_sandbox(folder: Path, home: Path | None = None) -> bool:
    """Whether a snap or Flatpak browser may read files in *folder*: in the home folder, and in no hidden folder there."""
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
