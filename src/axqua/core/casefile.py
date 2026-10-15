"""The case file: one river reach in one file that can travel (``*.axq-case``).

A case file is the YAML that :func:`axqua.config.load_config` has always read. What the
file type adds is a name a file dialog can filter on and a rule about content: a case
file written by aXqua carries **no machine settings**. Where TELEMAC is installed is a
fact about a computer and lives in its profile (:mod:`axqua.core.profile`), so a case
can be copied to another computer, or attached to a publication, without naming
anybody's home directory.

Nothing is taken away from existing cases. ``case-config.yml`` keeps loading, machine
settings included, and ``axqua migrate --to-case`` writes the new file beside it.

Standard library only.
"""

from __future__ import annotations

import os
from pathlib import Path

from axqua.core.errors import ConfigError

SUFFIX = ".axq-case"

#: What the case file was called before the file type existed. Still found, and never
#: preferred over a file of the new type.
LEGACY_NAMES = ("case-config.yml",)

__all__ = ["LEGACY_NAMES", "SUFFIX", "find_case_file", "is_case_file"]


def is_case_file(path: str | os.PathLike) -> bool:
    """Whether *path* is named like a case file of the current type."""
    return str(path).endswith(SUFFIX)


def find_case_file(folder: str | os.PathLike) -> Path:
    """The case file of a case folder.

    The scripts of a case folder call this instead of naming their case file, so a case
    can be renamed without editing nine scripts. One ``*.axq-case`` in the folder is the
    answer. Several are a question only the user can settle - a case with variants - and
    the error names them, because picking the first one would run a different model
    than the user thinks. With none, the pre-file-type name is used where it exists.
    """
    folder = Path(folder)
    found = sorted(folder.glob(f"*{SUFFIX}"))
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise ConfigError(
            f"{folder} holds {len(found)} case files: "
            + ", ".join(path.name for path in found),
            subject=str(folder),
            remedy="Pass the one to use as the first argument of the script.")
    for name in LEGACY_NAMES:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    raise ConfigError(
        f"no case file in {folder}", subject=str(folder),
        remedy=f"Expected one file ending in {SUFFIX}, or a {LEGACY_NAMES[0]}.")
