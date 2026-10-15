"""The example cases of aXqua: which there are, and how one gets onto a computer.

An example case is a folder that works on its own: a case file, a guide
(``README.md``) and the input data in ``user-sources/``. The paths in the case file are
relative to the file, so the folder can be placed anywhere. The data are reduced to
what the case reads (``scripts/build_example_data.py``), which keeps an example at a
few megabytes.

The examples are not part of the installed package, and aXqua does not contain their
data. They are read from a **source**, the first of these that has them:

1. the folder or address given with ``--source`` or in ``$AXQUA_EXAMPLES``,
2. the ``cases/`` folder of the source checkout that aXqua runs from, if it holds the
   data,
3. the repository on GitHub, at the release of this aXqua and, where no such release
   exists, at the main branch.

A source is a folder (or an address ending in one) that holds ``examples.json`` and
one folder per example. The index lists every file of an example with its size and
SHA-256 checksum. A file that does not arrive complete is not kept, and an example is
moved to its place only when all of its files are there, so a download that is
interrupted leaves nothing behind that looks like an example.

Nothing here imports a solver, numpy or a geodata library: listing and downloading
the examples works on a computer on which nothing else of aXqua runs yet.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable

from axqua.core.errors import ConfigError
from axqua.core.errors import EnvironmentError as AxquaEnvironmentError

log = logging.getLogger("axqua")

__all__ = ["ENV_SOURCE", "Example", "INDEX", "available", "fetch", "read_index",
           "sources"]

#: The environment variable that names the source of the examples.
ENV_SOURCE = "AXQUA_EXAMPLES"

#: The file of a source that lists its examples.
INDEX = "examples.json"

#: Where the examples are published: ``<GITHUB>/<release or branch>/cases``.
GITHUB = "https://raw.githubusercontent.com/sschwindt/aXqua"
BRANCH = "main"

TIMEOUT = 60.0          # seconds without an answer before a request is given up
RETRIES = 2             # further tries of a file after a failed transfer
BLOCK = 1 << 16

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_RELEASE = re.compile(r"\d+(\.\d+)*")


class Missing(Exception):
    """A source does not hold what was asked for."""


class Mismatch(OSError):
    """A file arrived, and it is not the file the index describes."""


@dataclass(frozen=True)
class Example:
    """One example case as the index of a source describes it."""

    name: str
    title: str
    summary: str
    case: str                                   # the case file, relative to the folder
    files: tuple[tuple[str, int, str], ...]     # (path, bytes, sha256)

    @property
    def size(self) -> int:
        return sum(item[1] for item in self.files)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "summary": self.summary,
                "case": self.case, "files": len(self.files), "bytes": self.size,
                "megabytes": round(self.size / 1e6, 1)}


# ------------------------------------------------------------------ sources


def checkout_cases() -> Path | None:
    """The ``cases/`` folder of the source checkout aXqua runs from, if there is one."""
    cases = Path(__file__).resolve().parents[3] / "cases"
    return cases if (cases / INDEX).is_file() else None


def sources(explicit: str = "") -> list[str]:
    """Where the examples are looked for, in this order."""
    chosen = explicit or os.environ.get(ENV_SOURCE, "")
    if chosen:
        return [chosen]
    found = []
    local = checkout_cases()
    if local is not None:
        found.append(str(local))
    from axqua import __version__

    if _RELEASE.fullmatch(__version__ or ""):
        found.append(f"{GITHUB}/v{__version__}/cases")
    found.append(f"{GITHUB}/{BRANCH}/cases")
    return found


def is_remote(source: str) -> bool:
    return source.startswith(("https://", "http://"))


def _open(source: str, relative: str) -> BinaryIO:
    """One file of a source, opened for reading. :class:`Missing` if it is not there."""
    if is_remote(source):
        from axqua import __version__

        address = source.rstrip("/") + "/" + urllib.parse.quote(relative)
        request = urllib.request.Request(
            address, headers={"User-Agent": f"aXqua/{__version__}"})
        try:
            # the address starts with http:// or https:// (is_remote), never file:
            return urllib.request.urlopen(request, timeout=TIMEOUT)  # noqa: S310
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise Missing(address) from exc
            raise
    path = Path(source).expanduser() / relative
    if not path.is_file():
        raise Missing(str(path))
    return open(path, "rb")


# ------------------------------------------------------------------ the index


def _relative(path: Any, source: str) -> str:
    """*path* of an index, checked: it must stay inside the folder of its example."""
    text = str(path or "")
    pure = PurePosixPath(text)
    if (not text or pure.is_absolute() or ".." in pure.parts or "\\" in text
            or ":" in text or text != pure.as_posix()):
        raise ConfigError(f"the index of {source} names a file outside its example: "
                          f"{text!r}", subject=INDEX)
    return text


def _parse(document: Any, source: str) -> list[Example]:
    if not isinstance(document, dict) or document.get("format") != 1:
        raise ConfigError(
            f"{INDEX} of {source} is not an index of example cases that this aXqua "
            "reads", subject=INDEX, remedy="Update aXqua, or name another source.")
    found = []
    for entry in document.get("examples") or []:
        name = str(entry.get("name") or "")
        if not _NAME.fullmatch(name):
            raise ConfigError(f"the index of {source} names an example {name!r}",
                              subject=INDEX)
        files = tuple((_relative(item.get("path"), source), int(item.get("bytes", 0)),
                       str(item.get("sha256") or "").lower())
                      for item in entry.get("files") or [])
        found.append(Example(name=name, title=str(entry.get("title") or name),
                             summary=str(entry.get("summary") or ""),
                             case=_relative(entry.get("case"), source), files=files))
    return found


def read_index(source: str) -> list[Example]:
    """The examples of one source. :class:`Missing` if it has no index."""
    with _open(source, INDEX) as handle:
        raw = handle.read()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ConfigError(f"{INDEX} of {source} cannot be read: {exc}",
                          subject=INDEX) from exc
    return _parse(document, source)


def _complete(source: str, example: Example) -> bool:
    """Whether a local source holds every file of *example* (a checkout may not)."""
    folder = Path(source).expanduser() / example.name
    return all((folder / path).is_file() for path, _, _ in example.files)


def _reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None) or exc
    return " ".join(str(reason).split()) or type(exc).__name__


def _unreachable(tried: list[str]) -> AxquaEnvironmentError:
    return AxquaEnvironmentError(
        "The example cases could not be read. Tried: " + "; ".join(tried),
        subject="source",
        remedy="Check the connection to the internet. The examples can also be read "
               f"from a folder: copy the folder 'cases' of the aXqua repository and "
               f"name it with --source or in the variable {ENV_SOURCE}.")


def available(explicit: str = "") -> tuple[str, list[Example]]:
    """``(source, examples)`` of the first source that has an index."""
    tried = []
    for source in sources(explicit):
        try:
            return source, read_index(source)
        except Missing:
            tried.append(f"{source} (no {INDEX})")
        except (OSError, urllib.error.URLError) as exc:
            tried.append(f"{source} ({_reason(exc)})")
    raise _unreachable(tried)


# ------------------------------------------------------------------ fetching


def _pick(examples: list[Example], name: str, source: str) -> Example:
    if not name and len(examples) == 1:
        return examples[0]
    for example in examples:
        if example.name == name:
            return example
    names = ", ".join(example.name for example in examples) or "none"
    raise ConfigError(
        f"there is no example case {name!r}" if name else "name the example case",
        subject="name", remedy=f"The examples of {source} are: {names}.")


def _transfer(source: str, relative: str, target: Path, size: int, digest: str) -> None:
    """Copy one file and compare it with the index. :class:`Mismatch` if they differ."""
    target.parent.mkdir(parents=True, exist_ok=True)
    check = hashlib.sha256()
    count = 0
    with _open(source, relative) as handle, open(target, "wb") as out:
        for block in iter(lambda: handle.read(BLOCK), b""):
            out.write(block)
            check.update(block)
            count += len(block)
    if count != size or (digest and check.hexdigest() != digest):
        target.unlink(missing_ok=True)
        raise Mismatch(f"{count} of {size} bytes" if count != size
                       else "the checksum differs")


def _copy(source: str, relative: str, target: Path, size: int, digest: str, *,
          wait: float = 3.0) -> None:
    """:func:`_transfer`, repeated when a transfer over the network fails."""
    tries = RETRIES + 1 if is_remote(source) else 1
    for attempt in range(tries):
        try:
            return _transfer(source, relative, target, size, digest)
        except Missing:
            raise
        except (OSError, urllib.error.URLError):
            if attempt + 1 == tries:
                raise
            time.sleep(wait)


def fetch(name: str, folder: str | os.PathLike, *, source: str = "",
          say: Callable[[str], None] = print) -> dict[str, Any]:
    """Put the example *name* into ``<folder>/<name>``. Returns what was done.

    ``{"name", "title", "folder", "case", "guide", "files", "megabytes", "source"}``.
    A folder that is there already is not touched: results of the user may be in it.
    """
    tried = []
    for candidate in sources(source):
        try:
            example = _pick(read_index(candidate), name, candidate)
        except Missing:
            tried.append(f"{candidate} (no {INDEX})")
            continue
        except (OSError, urllib.error.URLError) as exc:
            tried.append(f"{candidate} ({_reason(exc)})")
            continue
        if not is_remote(candidate) and not _complete(candidate, example):
            # a checkout carries the case file and the guide, not always the data
            tried.append(f"{candidate} (without the data of {example.name})")
            continue
        return _place(candidate, example, Path(folder), say)
    raise _unreachable(tried)


def _place(source: str, example: Example, folder: Path,
           say: Callable[[str], None]) -> dict[str, Any]:
    target = folder.expanduser().resolve() / example.name
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ConfigError(
            f"{target} is there already", subject="folder",
            remedy="Choose another folder, or remove this one to get the example anew.")
    partial = target.with_name(f".{example.name}.part")
    shutil.rmtree(partial, ignore_errors=True)
    megabytes = example.size / 1e6
    say(f"[*] {example.name}: {len(example.files)} files, {megabytes:.1f} MB, from "
        f"{source}")
    try:
        for number, (path, size, digest) in enumerate(example.files, start=1):
            try:
                _copy(source, f"{example.name}/{path}", partial / path, size, digest)
            except Missing as exc:
                raise AxquaEnvironmentError(
                    f"{source} lists {path} of {example.name} but does not have it",
                    subject="source") from exc
            except Mismatch as exc:
                # a broken transfer, or an index that is older than the files it lists
                raise AxquaEnvironmentError(
                    f"{path} of {example.name} did not arrive as the index of {source} "
                    f"describes it ({exc}). Nothing was kept.", subject="source",
                    remedy="Repeat the command. If the same file fails again, the "
                           "index of the source is out of date: 'python scripts/"
                           "build_example_data.py --index' writes it anew.") from exc
            except (OSError, urllib.error.URLError) as exc:
                raise AxquaEnvironmentError(
                    f"{path} of {example.name} could not be read from {source}: "
                    + _reason(exc), subject="source",
                    remedy="Repeat the command. Nothing was kept of this try.") from exc
            say(f"[*] {number} of {len(example.files)}: {path}")
        if target.exists():
            target.rmdir()                       # it is empty, see above
        os.replace(partial, target)
    finally:
        shutil.rmtree(partial, ignore_errors=True)
    guide = target / "README.md"
    return {"name": example.name, "title": example.title, "folder": str(target),
            "case": str(target / example.case),
            "guide": str(guide) if guide.is_file() else "",
            "files": len(example.files), "megabytes": round(megabytes, 1),
            "source": source}
