"""The example cases of TELEMAC, reduced to what a run reads.

TELEMAC is distributed with about 830 example cases. Their steering files, boundary
files and Fortran files are ordinary files of the repository and come with the clone.
Their binary files are kept in Git LFS: 1,375 files and 1.4 GB for version 9.1.1, plus
the manuals and notebooks. Git fetches these one by one when it checks the working tree
out, which on the development computer took more than an hour for an installation whose
build takes minutes.

Most of that is not needed to run an example. Two thirds of the files are **reference
results**, with which the validation system of TELEMAC compares a run, and files that
a run writes. What a run *reads* is the geometry, a previous computation file where an
example continues one, and a few data files: **363 files and 460 MB**.

Which files those are is not guessed from their names. TELEMAC states it itself: every
file keyword in its dictionaries (``sources/*/*.dico``) says whether the file is read
(``LIT``) or written (``ECR``). :func:`needed_inputs` reads the dictionaries of the
installation at hand, collects the files that the steering files of the examples name
under a keyword that is read, and keeps those that are still LFS pointers. A new
version of TELEMAC with other keywords or other examples therefore needs no change
here.

:func:`fetch_inputs` then asks Git LFS for exactly these files, in a few requests of
many files each. That is also much faster per megabyte than the checkout, because LFS
transfers several files at a time when it is asked for a list.

The installation runs the installer script with ``GIT_LFS_SKIP_SMUDGE=1`` first, so
that the checkout leaves every LFS file as a pointer of a few lines, and this module
afterwards. A failure here (the download server of TELEMAC limits requests) leaves a
working TELEMAC and examples without their geometry; it is reported and can be
repeated with ``axqua install examples <folder>``.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger("axqua.install")

__all__ = ["POINTER", "fetch_inputs", "is_pointer", "needed_inputs", "pointer_size",
           "read_keywords"]

#: How an LFS pointer file begins.
POINTER = b"version https://git-lfs.github.com/spec/v1"

#: Files per request to Git LFS; a command line has a limit.
CHUNK = 60

_ASSIGNMENT = re.compile(r"^\s*([^/:=][^:=]*?)\s*[:=]\s*(.*?)\s*$")


def is_pointer(path: Path) -> bool:
    """Whether *path* is a Git LFS pointer and not the file it stands for."""
    try:
        if path.stat().st_size > 400:
            return False
        with open(path, "rb") as handle:
            return handle.read(len(POINTER)) == POINTER
    except OSError:
        return False


def pointer_size(path: Path) -> int:
    """The size in bytes of the file an LFS pointer stands for (0 if unknown)."""
    try:
        found = re.search(rb"^size (\d+)", path.read_bytes(), re.M)
    except OSError:
        return 0
    return int(found.group(1)) if found else 0


def read_keywords(home: Path) -> set[str]:
    """The file keywords of this TELEMAC under which a run READS a file.

    English and French names, in upper case. From the ``SUBMIT`` entry of each keyword
    in the dictionaries: its fifth field is ``LIT`` for a file that is read and ``ECR``
    for one that is written.
    """
    names: set[str] = set()
    for dictionary in sorted((Path(home) / "sources").rglob("*.dico")):
        try:
            text = dictionary.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for block in re.split(r"(?m)^NOM\s*=", text)[1:]:
            submit = re.search(r"(?ms)^SUBMIT\s*=\s*'(.*?)'", block)
            if not submit or "LIT" not in submit.group(1).replace("\n", "").split(";"):
                continue
            french = re.match(r"\s*'((?:[^']|'')*)'", block)
            english = re.search(r"(?m)^NOM1\s*=\s*'((?:[^']|'')*)'", block)
            for name in (french, english):
                if name:
                    names.add(name.group(1).replace("''", "'").strip().upper())
    return names


def _values(text: str) -> Iterable[str]:
    """The file names on the right-hand side of a keyword."""
    for value in re.split(r"[;,]", text):
        value = value.strip().strip("'\"").strip()
        if value:
            yield value


def needed_inputs(home: Path, *, folder: str = "examples") -> list[Path]:
    """The LFS pointers below ``<home>/<folder>`` that a run of an example reads.

    Paths relative to *home*, sorted. Files that are already downloaded are not
    listed, so a second call after a download returns what is still missing.
    """
    home = Path(home)
    reads = read_keywords(home)
    found: set[Path] = set()
    for steering in sorted((home / folder).rglob("*.cas")):
        try:
            lines = steering.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if line.lstrip().startswith("/"):            # a comment
                continue
            match = _ASSIGNMENT.match(line)
            if not match or match.group(1).strip().upper() not in reads:
                continue
            for value in _values(match.group(2)):
                target = Path(os.path.normpath(steering.parent / value))
                if target.is_file() and is_pointer(target):
                    try:
                        found.add(target.relative_to(home))
                    except ValueError:
                        continue                         # points outside TELEMAC
    return sorted(found)


def _pull(home: Path, paths: list[Path]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(home), "lfs", "pull",
         "--include=" + ",".join(str(path).replace(os.sep, "/") for path in paths),
         "--exclude="],
        capture_output=True, text=True, check=False)


def fetch_inputs(home: str | os.PathLike, *, chunk: int = CHUNK, retries: int = 3,
                 wait: float = 45.0, pull: Callable[[Path, list[Path]], Any] = _pull,
                 say: Callable[[str], None] = print) -> dict[str, Any]:
    """Download the input files of the example cases. Returns what was done.

    ``{"wanted": n, "fetched": n, "megabytes": x, "missing": [paths]}``. A request that
    fails is repeated after *wait* seconds, up to *retries* times, because the server
    refuses requests for a while when it has received many.
    """
    home = Path(home)
    wanted = needed_inputs(home)
    megabytes = sum(pointer_size(home / path) for path in wanted) / 1e6
    say(f"[*] {len(wanted)} input files of the example cases are to be downloaded "
        f"({megabytes:.0f} MB)")
    if not wanted:
        return {"wanted": 0, "fetched": 0, "megabytes": 0.0, "missing": []}
    for start in range(0, len(wanted), chunk):
        pending = wanted[start:start + chunk]
        for attempt in range(retries + 1):
            answer = pull(home, pending)
            pending = [path for path in pending if is_pointer(home / path)]
            if not pending:
                break
            if attempt < retries:
                text = (getattr(answer, "stderr", "") or getattr(answer, "stdout", "")
                        or "").strip()
                reason = text.splitlines()[-1][:120] if text else "no message"
                say(f"[*] {len(pending)} files were not delivered ({reason}); trying "
                    f"again in {wait:.0f} s")
                time.sleep(wait)
        say(f"[*] example input files: {min(start + chunk, len(wanted))} of "
            f"{len(wanted)} requested")
    missing = [str(path) for path in wanted if is_pointer(home / path)]
    fetched = len(wanted) - len(missing)
    say(f"[*] {fetched} of {len(wanted)} input files of the example cases downloaded")
    return {"wanted": len(wanted), "fetched": fetched, "megabytes": round(megabytes, 1),
            "missing": missing}
