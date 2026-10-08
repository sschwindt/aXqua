"""Check a case file and say everything that is wrong with it, without raising.

Loading a case stops at the first problem, which is right for a build: there is no point
in meshing a case whose terrain model does not exist. An editor needs the opposite. It
has to show **all** problems at once, each at the setting it concerns, and it has to
accept a case that is half filled in, because that is what a case is while somebody is
filling it in. So :func:`check_case` returns findings (:mod:`axqua.core.diagnostics`) and
never raises, whatever the file contains.

Three tiers, each only reached when the one before it leaves something to check:

1. **the file**: readable, valid YAML, a mapping;
2. **files it names**: every terrain model, layer and table exists;
3. **facts**: the case loads, and :meth:`~axqua.config.Config.validate` accepts it.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from axqua.core.diagnostics import ERROR, WARNING, Finding

__all__ = ["check_case", "read_raw"]

_FILE_KINDS = ("vector", "raster", "table", "file")
_UNKNOWN = re.compile(r"unknown config keys? \[(.*?)\]")
_DOTTED = re.compile(r"\b([a-z_]+\.[a-z_0-9]+)\b")


def read_raw(path: str | Path) -> dict[str, Any]:
    """The case file as it is written: a mapping of blocks. Raises on a broken file."""
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("the file does not contain the blocks of a case")
    return data


def check_case(path: str | Path) -> list[Finding]:
    """Every problem of the case file at *path*, as findings. Never raises."""
    import yaml

    path = Path(path)
    try:
        raw = read_raw(path)
    except OSError as exc:
        return [Finding(ERROR, "axqua.config.unreadable",
                        f"the case file cannot be read: {exc}", subject=str(path))]
    except yaml.YAMLError as exc:
        return [Finding(ERROR, "axqua.config.yaml_syntax",
                        "the case file is not valid YAML: " + " ".join(str(exc).split()),
                        subject=str(path),
                        remedy="Correct the line the message names, in a text editor.")]
    except ValueError as exc:
        return [Finding(ERROR, "axqua.config.not_a_case", f"{exc}", subject=str(path))]

    from axqua.core import schema_meta

    kinds = schema_meta.field_kinds()
    findings = _unknown_blocks(raw, kinds)
    findings += _missing_files(raw, kinds, path.parent)
    # what is reported at its setting is not reported again by its block
    already = {f.subject for f in findings} | {
        Path(f.message.rsplit(": ", 1)[-1]).name for f in findings
        if f.code == "axqua.config.missing_file"}
    findings += _facts(path, raw, kinds, already=already)
    findings += _advice(raw)
    return findings


def _unknown_blocks(raw: dict, kinds: dict[str, str]) -> list[Finding]:
    """A block the case file has and aXqua does not know. Keys inside a known block are
    settled by the loader, which also accepts the older spellings."""
    blocks = {key.split(".", 1)[0] for key in kinds} | {"percolation"}
    out = []
    for block in raw:
        if block in blocks:
            continue
        close = difflib.get_close_matches(str(block), sorted(blocks), n=1)
        out.append(Finding(
            ERROR, "axqua.config.unknown_key", f"{block} is not a block of a case file",
            subject=str(block),
            remedy=f"Did you mean {close[0]}?" if close else "Remove the block."))
    return out


def _missing_files(raw: dict, kinds: dict[str, str], folder: Path) -> list[Finding]:
    out = []
    for key, kind in kinds.items():
        if kind not in _FILE_KINDS:
            continue
        block, name = key.split(".", 1)
        value = (raw.get(block) or {}).get(name) if isinstance(raw.get(block), dict) \
            else None
        if not value or not isinstance(value, str):
            continue
        target = Path(value).expanduser()
        target = target if target.is_absolute() else folder / target
        if not target.exists():
            out.append(Finding(
                ERROR, "axqua.config.missing_file",
                f"the file of {key} does not exist: {target}", subject=key,
                remedy="Select the file again. A path is read relative to the folder "
                       "of the case file."))
    return out


def _facts(path: Path, raw: dict, kinds: dict[str, str], *, already: set[str]
           ) -> list[Finding]:
    """Load the case and run every step of its validation; what stops one becomes a
    finding. Steps about the computer are left to the check of the profile."""
    from axqua.config import load_config
    from axqua.core import schema_meta

    try:
        cfg = load_config(path)
    except TypeError as exc:
        # a block class that was not given what it cannot do without
        block = _block_of(str(exc), schema_meta.block_classes())
        missing = re.findall(r"'([a-z_]+)'", str(exc))
        return [Finding(ERROR, "axqua.config.missing_value",
                        f"{block}.{name} is required", subject=f"{block}.{name}",
                        remedy="Enter it in the case editor.")
                for name in missing] or [_invalid(exc, kinds, already, "")]
    except Exception as exc:                        # noqa: BLE001 - whatever stops it
        unknown = _UNKNOWN.search(str(exc))
        if not unknown:
            return [_invalid(exc, kinds, already, "")]
        block = _block_of(str(exc), schema_meta.block_classes())
        known = [k.split(".", 1)[1] for k in kinds if k.startswith(block + ".")]
        out = []
        for name in re.findall(r"'([^']+)'", unknown.group(1)):
            close = difflib.get_close_matches(name, known, n=1)
            out.append(Finding(
                ERROR, "axqua.config.unknown_key",
                f"{block}.{name} is not a setting of a case",
                subject=f"{block}.{name}",
                remedy=f"Did you mean {close[0]}?" if close else
                "Remove it, or correct its spelling."))
        return out

    out = []
    for about, step in cfg.checks():
        if about == "machine":
            continue
        try:
            step()
        except Exception as exc:                    # noqa: BLE001 - one fact each
            out += _required(str(exc), about, kinds, already) or [
                finding for finding in [_invalid(exc, kinds, already, about)]
                if finding is not None]
    return [finding for finding in out if finding.subject != _SKIP]


#: Marks a failed step that says nothing new: all it names is reported already.
_SKIP = "\x00reported"
_REQUIRED = re.compile(r"Missing required \w+: \[(.*?)\]")
_NOT_SET = re.compile(r"^([a-z_]+\.[a-z_0-9]+) not found: None")


def _required(message: str, about: str, kinds: dict[str, str], already: set[str]
              ) -> list[Finding]:
    """A block that says what it lacks: one finding per setting that is not set yet.

    ``Missing required geodata: ['dem_initial']`` is also what the block says when the
    *file* of that setting is missing, which was reported at the setting already.
    """
    names = []
    listed = _REQUIRED.search(message)
    if listed:
        names = [f"{about}.{name}" for name in re.findall(r"'([^']+)'", listed.group(1))]
    unset = _NOT_SET.match(" ".join(message.split()))
    if unset:
        names = [unset.group(1)]
    if not names:
        return []
    new = [Finding(ERROR, "axqua.config.missing_value", f"{key} is not set",
                   subject=key, remedy="Select it in the case editor.")
           for key in names if key in kinds and key not in already]
    return new or [Finding(ERROR, "axqua.config.missing_value", "", subject=_SKIP)]


def _block_of(message: str, classes: dict[str, str]) -> str:
    """``Geodata.__init__() missing ...`` and ``MeshConfig: unknown config keys`` name a
    class; the block of the case file it reads is what the user can act on."""
    head = re.match(r"\s*([A-Za-z]+)", message)
    name = head.group(1).lower() if head else ""
    return classes.get(name, name or "case")


def _invalid(exc: BaseException, kinds: dict[str, str], already: set[str],
             about: str) -> Finding | None:
    message = " ".join(str(exc).split())
    # the setting the message names, else the block the failed step is about
    subject = next((key for key in _DOTTED.findall(message) if key in kinds), about)
    if isinstance(exc, FileNotFoundError):
        if any(name and name in message for name in already):
            return None                              # reported with its own setting
        return Finding(ERROR, "axqua.config.missing_file", message, subject=subject)
    return Finding(ERROR, "axqua.config.invalid_value", message, subject=subject)


def _advice(raw: dict) -> list[Finding]:
    """What is not wrong yet, and will stop the next step."""
    out = []
    if "telemac" not in raw and "openfoam" not in raw:
        out.append(Finding(
            WARNING, "axqua.config.no_solver",
            "the case names no simulation program, so nothing can be built or run",
            subject="telemac",
            remedy="Add the TELEMAC block (Solver: telemac2d), or the OpenFOAM block."))
    boundaries = raw.get("boundaries") if isinstance(raw.get("boundaries"), dict) else {}
    if boundaries.get("prescribed_flowrate") in (None, "") and not boundaries.get("inflow"):
        out.append(Finding(
            WARNING, "axqua.config.incomplete",
            "no discharge is set, so the steady simulation has no inflow",
            subject="boundaries.prescribed_flowrate",
            remedy="Enter the discharge of the steady simulation."))
    return out
