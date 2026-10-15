"""``axqua example``: get an example case onto this computer.

``list``   the example cases there are, with their size
``get``    put one into a folder, ready to be opened in the plugin or run in a terminal

An example is a folder that works on its own: the case file, a guide and the input
data. See :mod:`axqua.core.examples` for where the examples are read from.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from axqua.jobcli import _common, _setup_logging, emit, fail, parse_args

ACTIONS = ("list", "get")


def run_example(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="axqua example",
        description="List the example cases of aXqua, or put one into a folder. An "
                    "example is a case file with a guide and its input data, a few "
                    "megabytes in all.")
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("name", nargs="?", default="",
                        help="get: the example; may be left out while there is one")
    parser.add_argument("--folder", type=Path, default=Path("."),
                        help="get: where the folder of the example is created "
                             "(default: the current folder)")
    parser.add_argument("--source", default="",
                        help="a folder or an address that holds the examples, "
                             "instead of the published ones")
    args = parse_args(_common(parser), argv, "example")
    _setup_logging(args)
    command = f"example.{args.action}"
    try:
        from axqua.core import examples

        if args.action == "list":
            source, found = examples.available(args.source)
            data = {"source": source, "examples": [item.as_dict() for item in found]}
            lines = [f"example cases of {source}"]
            for item in found:
                lines += [f"{item.name}  ({item.size / 1e6:.1f} MB, "
                          f"{len(item.files)} files)", f"  {item.title}",
                          f"  {item.summary}"]
            return emit(command, data, as_json=args.as_json, lines=lines)

        # progress belongs on stderr when stdout carries the JSON document
        stream = sys.stderr if args.as_json else sys.stdout
        done = examples.fetch(args.name, args.folder, source=args.source,
                              say=lambda text: print(text, file=stream, flush=True))
        lines = [f"{done['title']} is in {done['folder']}",
                 f"case file: {done['case']}"]
        if done["guide"]:
            lines.append(f"guide: {done['guide']}")
        return emit(command, done, as_json=args.as_json, lines=lines)
    except BaseException as exc:  # noqa: BLE001
        return fail(command, exc, as_json=args.as_json, verbose=args.verbose)
