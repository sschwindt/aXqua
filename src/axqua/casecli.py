"""``axqua schema``, ``axqua case`` and ``axqua check``: what an editor of a case needs.

The case editor of the QGIS plugin never imports aXqua, so everything it does to a case
file goes through here and comes back as one JSON document:

``schema``        every setting of a case file: label, kind, unit, explanation
``case read``     the case file as it is written
``case write``    replace it with a JSON document read from standard input
``case new``      a new case file with the entries every case has
``check``         everything that is wrong with a case, as findings

``case write`` writes what it is given and *then* checks it. A case is incomplete while
it is being filled in, and an editor that refused to save an incomplete case would lose
the work. The original file is kept as ``<name>.bak`` the first time it is replaced,
because writing goes through the data and the comments of a hand-written file are not
kept.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from axqua.jobcli import _common, _setup_logging, emit, fail, parse_args

CASE_ACTIONS = ("read", "write", "new")


def run_schema(argv: list[str]) -> int:
    parser = _common(argparse.ArgumentParser(
        prog="axqua schema",
        description="Every setting of a case file with its label, kind, unit and "
                    "explanation, grouped by block. Read by the case editor."))
    args = parse_args(parser, argv, "schema")
    _setup_logging(args)
    try:
        from axqua.core import schema_meta

        sections = schema_meta.build()
        lines = []
        for section in sections:
            lines.append(f"[{section['block']}] {section['title']}")
            lines += [f"  {item['name']:<32} {item['kind']:<7} {item['label']}"
                      for item in section["fields"]]
        return emit("schema", {"sections": sections}, as_json=args.as_json, lines=lines)
    except BaseException as exc:  # noqa: BLE001
        return fail("schema", exc, as_json=args.as_json, verbose=args.verbose)


def run_check(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="axqua check",
        description="Everything that is wrong with a case file, as findings. The "
                    "command succeeds whatever it finds: the findings are its result.")
    parser.add_argument("case", type=Path, help="the case file")
    args = parse_args(_common(parser), argv, "check")
    _setup_logging(args)
    try:
        return emit("check", _checked(args.case), as_json=args.as_json,
                    lines=_lines(args.case))
    except BaseException as exc:  # noqa: BLE001
        return fail("check", exc, as_json=args.as_json, verbose=args.verbose)


def _checked(path: Path) -> dict:
    from axqua.core.casecheck import check_case

    return {"path": str(path), "findings": [f.as_dict() for f in check_case(path)]}


def _lines(path: Path) -> list[str]:
    from axqua.core.casecheck import check_case

    return [f.line() for f in check_case(path)] or [f"{path}: no findings"]


def run_case(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="axqua case",
        description="Read, write or create a case file. 'write' reads the new content "
                    "as JSON from standard input, keeps the original as <name>.bak the "
                    "first time, and reports what a check of the result finds.")
    parser.add_argument("action", choices=CASE_ACTIONS)
    parser.add_argument("case", type=Path, help="the case file")
    parser.add_argument("--name", default="", help="new: the name of the case")
    parser.add_argument("--crs", type=int, default=25832,
                        help="new: EPSG code of the coordinate reference system")
    args = parse_args(_common(parser), argv, "case")
    _setup_logging(args)
    command = f"case.{args.action}"
    try:
        from axqua.core.casecheck import read_raw
        from axqua.core.errors import ConfigError

        target = Path(args.case)
        if args.action == "read":
            if not target.is_file():
                raise ConfigError(f"there is no case file {target}", subject=str(target))
            try:
                data = read_raw(target)
            except Exception as exc:                # noqa: BLE001
                raise ConfigError(
                    f"{target.name} cannot be read as a case: "
                    + " ".join(str(exc).split()), subject=str(target),
                    remedy="Correct the file in a text editor first.") from exc
            return emit(command, {"path": str(target), "folder": str(target.parent),
                                  "data": data},
                        as_json=args.as_json, lines=[str(target)])

        if args.action == "new":
            if target.exists():
                raise ConfigError(f"{target} already exists", subject=str(target),
                                  remedy="Choose another name, or edit this case.")
            data = {"project": {"name": args.name or target.name.split(".")[0],
                                "crs_epsg": int(args.crs), "sim_dir": "axqua-case"},
                    "telemac": {"solver": "telemac2d"}}
            _write(target, data)
            return emit(command, {"path": str(target), "data": data,
                                  **_checked(target)},
                        as_json=args.as_json, lines=[f"wrote {target}"])

        payload = json.loads(sys.stdin.read() or "{}")
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, dict):
            raise ConfigError("a case is a mapping of blocks", subject=str(target))
        backup = _write(target, data)
        answer = {"backup": str(backup) if backup else "", **_checked(target)}
        return emit(command, answer, as_json=args.as_json,
                    lines=[f"wrote {target}"] + _lines(target))
    except BaseException as exc:  # noqa: BLE001
        return fail(command, exc, as_json=args.as_json, verbose=args.verbose)


def _clean(value):
    """Drop what was left empty: an empty entry means "not set", not "set to nothing"."""
    if isinstance(value, dict):
        kept = {key: _clean(item) for key, item in value.items()}
        return {key: item for key, item in kept.items()
                if item is not None and item != "" and item != {}}
    return value


def _write(target: Path, data: dict) -> Path | None:
    """Write *data* as the case file. Returns the backup that was made, if any."""
    import yaml

    text = yaml.safe_dump(_clean(data), sort_keys=False, allow_unicode=True,
                          default_flow_style=False)
    backup = None
    if target.is_file():
        candidate = target.with_name(target.name + ".bak")
        if not candidate.exists():
            shutil.copy2(target, candidate)
            backup = candidate
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(target)
    return backup
