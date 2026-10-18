"""``axqua cleanup``: files of a case that can go (:mod:`axqua.cleanup`).

``axqua cleanup <case-file>``                          what can go, by category, with sizes
``axqua cleanup <case-file> --remove ID [ID ...]``     remove these (identifiers of the list)
``axqua cleanup <case-file> --category KEY ...``       remove everything of these categories
``--keep last`` / ``--keep every:10``                   time steps a result keeps (default last)

*Clean Up* in the plugin lists with ``--json`` and removes what was ticked.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from axqua.jobcli import _common, _setup_logging, emit, fail, parse_args


def _parser() -> argparse.ArgumentParser:
    from axqua.cleanup import CATEGORIES

    p = argparse.ArgumentParser(
        prog="axqua cleanup",
        description="List the files of a case that nothing needs any more, and remove them. "
                    "Without --remove or --category nothing is removed. Inputs, the built model "
                    "and the last time step of every result are never offered.")
    p.add_argument("case", type=Path, help="the case file")
    p.add_argument("--remove", nargs="+", default=[], metavar="ID",
                   help="identifiers from the list (delete:... or thin:...)")
    p.add_argument("--category", nargs="+", default=[], choices=list(CATEGORIES), metavar="KEY",
                   help="remove every item of these categories: " + ", ".join(CATEGORIES))
    p.add_argument("--keep", default="last",
                   help="time steps a result keeps when its time steps are removed: 'last' "
                        "(default) or 'every:N' (every N-th and the last)")
    return _common(p)


def _lines(found: dict) -> list[str]:
    from axqua.cleanup import human

    lines = [f"case: {found['case']}"]
    if found["blocked"]:
        lines.append(f"nothing can be removed now: {found['blocked']}")
    if not found["categories"]:
        lines.append("nothing to clean up")
    for cat in found["categories"]:
        lines.append(f"\n{cat['title']} [{cat['key']}]: {human(cat['bytes'])}")
        for item in cat["items"]:
            lines.append(f"  {human(item['saves']):>9}  {item['id']}"
                         + (f"  ({item['detail']})" if item["detail"] else ""))
    lines.append(f"\nin all: {human(found['bytes'])} (time steps: keeping the last one)")
    return lines


def run_cleanup(argv: list[str]) -> int:
    args = parse_args(_parser(), argv, "cleanup")
    _setup_logging(args)
    try:
        from axqua import cleanup
        from axqua.config import load_config

        case = args.case.expanduser().resolve()
        cfg = load_config(case)
        if not args.remove and not args.category:
            found = cleanup.inventory(cfg, case)
            return emit("cleanup", found, as_json=args.as_json, lines=_lines(found))
        done = cleanup.apply(cfg, case, ids=args.remove, categories=args.category, keep=args.keep)
        lines = [f"removed {len(done['removed'])} items, freed {cleanup.human(done['freed_bytes'])}"]
        lines += [f"failed: {f['id']}: {f['error']}" for f in done["failed"]]
        lines += [f"not in the list (any more): {i}" for i in done["unknown"]]
        return emit("cleanup", done, as_json=args.as_json, lines=lines)
    except Exception as exc:  # noqa: BLE001
        return fail("cleanup", exc, as_json=args.as_json, verbose=args.verbose)
