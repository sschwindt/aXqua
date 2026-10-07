"""``axqua profile``: inspect, create and check the profile of this computer.

The profile (:mod:`axqua.core.profile`) is what the *Configuration* tab of the QGIS
plugin edits. These commands are the same operations for a terminal, and they are how
the plugin performs them: it never imports aXqua, so reading, writing and checking a
profile all go through here and come back as one JSON document.

``path``     where the active profile is, or would be
``show``     print it
``detect``   what a profile for this computer would contain; writes nothing
``init``     ``detect``, written to the default location (refuses to overwrite)
``check``    everything that is wrong with it, as findings; never fails on a finding
``write``    replace it with a JSON document read from standard input
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from axqua.jobcli import _common, _setup_logging, emit, fail, parse_args

log = logging.getLogger("axqua")

ACTIONS = ("show", "path", "detect", "init", "check", "write")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axqua profile",
        description="The profile of this computer: where Python, TELEMAC, OpenFOAM and "
                    "the postprocessors are, and where jobs are written. 'check' probes "
                    "whether the simulation software can really be reached - do this "
                    "when the profile is written, not when a long job is submitted.")
    p.add_argument("action", nargs="?", default="show", choices=ACTIONS)
    p.add_argument("file", nargs="?", type=Path,
                   help="a profile file (default: the active profile)")
    p.add_argument("--force", action="store_true",
                   help="init: overwrite an existing profile")
    p.add_argument("--no-probe", action="store_true",
                   help="check: only verify that the named files exist, without "
                        "entering the environments of the simulation software")
    return _common(p)


def run_profile(argv: list[str]) -> int:
    args = parse_args(_parser(), argv, "profile")
    _setup_logging(args)
    command = f"profile.{args.action}"
    try:
        from axqua.core import profile as profiles
        from axqua.core.errors import ConfigError

        if args.action == "path":
            target = profiles.active_path(args.file) or profiles.default_path()
            return emit(command, {"path": str(target), "exists": target.is_file()},
                        as_json=args.as_json, lines=[str(target)])

        if args.action == "detect":
            found = profiles.detect()
            return emit(command, found.as_dict(), as_json=args.as_json,
                        lines=_yaml_lines(found))

        if args.action == "init":
            target = Path(args.file) if args.file else profiles.default_path()
            if target.is_file() and not args.force:
                raise ConfigError(
                    f"{target} already exists", subject=str(target),
                    remedy="Edit it, or pass --force to replace it with a detected one.")
            written = profiles.save(profiles.detect(), target)
            return emit(command, {"path": str(written)}, as_json=args.as_json,
                        lines=[f"wrote {written}",
                               "check it with 'axqua profile check'"])

        if args.action == "write":
            target = Path(args.file) if args.file else \
                (profiles.active_path() or profiles.default_path())
            payload = json.loads(sys.stdin.read() or "{}")
            # Parsed before anything is written, so a document that is not a profile
            # cannot replace one that is.
            profile = profiles.AxquaProfile.from_dict(payload, path=target)
            written = profiles.save(profile, target)
            findings = profiles.check(profile, probe=False)
            return emit(command, {"path": str(written),
                                  "findings": [f.as_dict() for f in findings]},
                        as_json=args.as_json,
                        lines=[f"wrote {written}"] + [f.line() for f in findings])

        profile = profiles.load_active(args.file, strict=True)
        if profile is None:
            raise ConfigError(
                "this computer has no profile yet", subject=str(profiles.default_path()),
                remedy="Create one with 'axqua profile init'.")

        if args.action == "show":
            return emit(command, {"path": str(profile.path), **profile.as_dict()},
                        as_json=args.as_json,
                        lines=[f"# {profile.path}"] + _yaml_lines(profile))

        findings = profiles.check(profile, probe=not args.no_probe)
        lines = [f.line() for f in findings] or [f"{profile.path}: no findings"]
        # Findings are the RESULT of a check, so the command succeeded whatever they
        # say. An editor has to be able to show them all and still save.
        return emit(command, {"path": str(profile.path),
                              "findings": [f.as_dict() for f in findings]},
                    as_json=args.as_json, lines=lines)
    except BaseException as exc:  # noqa: BLE001
        return fail(command, exc, as_json=args.as_json, verbose=args.verbose)


def _yaml_lines(profile) -> list[str]:
    import yaml
    text = yaml.safe_dump(profile.as_dict(), sort_keys=False, default_flow_style=False,
                          allow_unicode=True)
    return text.rstrip("\n").split("\n")


__all__ = ["run_profile"]
