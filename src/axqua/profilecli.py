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
``locate``   ``locate <program> <folder>``: what aXqua runs of a program in that folder
``inspect``  ``inspect <telemac|openfoam> <script>``: whether the installation that script sets up is usable, and which aXqua modules it lacks; writes nothing
``use``      ``use <telemac|openfoam> <script>``: ``inspect``, and enter the script and its installation folder in the profile unless the installation is unusable
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from axqua.jobcli import _common, _setup_logging, emit, fail, parse_args

log = logging.getLogger("axqua")

ACTIONS = ("show", "path", "detect", "init", "check", "write", "locate", "inspect", "use")

#: The codes whose installation ``inspect`` and ``use`` look into.
INSPECTED = ("telemac", "openfoam")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axqua profile",
        description="The profile of this computer: where Python, TELEMAC, OpenFOAM and "
                    "the postprocessors are, and where jobs are written. 'check' probes "
                    "whether the simulation software can really be reached - do this "
                    "when the profile is written, not when a long job is submitted.")
    p.add_argument("action", nargs="?", default="show", choices=ACTIONS)
    p.add_argument("file", nargs="?", type=Path,
                   help="a profile file (default: the active profile); for locate: "
                        "the program, one of telemac, openfoam, paraview, visit; for "
                        "inspect and use: telemac or openfoam")
    p.add_argument("folder", nargs="?", type=Path,
                   help="locate: the folder the program is installed in; inspect and "
                        "use: the environment script of the installation")
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

        if args.action == "locate":
            program = str(args.file or "")
            if program not in profiles.PROGRAMS or args.folder is None:
                raise ConfigError(
                    "'locate' needs a program and the folder it is installed in",
                    subject="program",
                    remedy="For example: axqua profile locate telemac "
                           "/opt/telemac-mascaret. Programs: "
                           + ", ".join(profiles.PROGRAMS))
            folder = args.folder.expanduser()
            found = profiles.candidates(program, folder)
            chosen = found[0] if len(found) == 1 else None
            # The folder of the program itself, which may lie below the one given.
            home = profiles.installation_of(program, chosen) if chosen else None
            lines = [str(chosen)] if chosen else (
                [f"{len(found)} files in {folder} could be meant:"]
                + [f"  {path}" for path in found] if found
                else [f"no {program} was found in {folder}"])
            return emit(command, {"program": program, "folder": str(home or folder),
                                  "script": str(chosen or ""),
                                  "candidates": [str(path) for path in found]},
                        as_json=args.as_json, lines=lines)

        if args.action in ("inspect", "use"):
            return _inspect(args, command)

        if args.action == "detect":
            found = profiles.detect()
            return emit(command, found.as_dict(), as_json=args.as_json,
                        lines=_yaml_lines(found))

        if args.action == "init":
            # The file that will be written, which is not always the name as typed:
            # 'init office' writes office.axq-profile.
            target = profiles.file_path(args.file) if args.file \
                else profiles.default_path()
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
            kept = [f"the file that was there could not be read and was kept as "
                    f"{profile.backup}"] if profile.backup else []
            return emit(command, {"path": str(written),
                                  "backup": str(profile.backup or ""),
                                  "findings": [f.as_dict() for f in findings]},
                        as_json=args.as_json,
                        lines=[f"wrote {written}"] + kept + [f.line() for f in findings])

        profile = profiles.load_active(args.file, strict=True)
        if profile is None:
            raise ConfigError(
                "this computer has no profile yet", subject=str(profiles.default_path()),
                remedy="Create one with 'axqua profile init'.")

        if args.action == "show":
            # "programs" is what aXqua runs for each program: the entry of the profile,
            # or the file found in its installation folder. It is not part of the file.
            return emit(command, {"path": str(profile.path),
                                  "programs": profile.programs(), **profile.as_dict()},
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


def _inspect(args, command: str) -> int:
    """``inspect`` and ``use``: what an installation can do, and entering it."""
    from axqua.core.errors import ConfigError
    from axqua.core.installation import inspect

    solver = str(args.file or "")
    if solver not in INSPECTED or args.folder is None:
        raise ConfigError(
            f"'{args.action}' needs a simulation code and its environment script",
            subject="program",
            remedy=f"For example: axqua profile {args.action} telemac "
                   "/opt/telemac/telemac-mascaret/configs/pysource.debian12.sh. Codes: "
                   + ", ".join(INSPECTED))
    from axqua.core import profile as profiles

    # one file named two ways (relative, with ~) is one file, in the check and in the profile
    script = profiles.absolute_script(args.folder)
    binding = None
    if args.action == "use":
        # the check enters the script as the jobs will after bind: with the entry of the profile, without what belonged to another installation (profiles.rebound)
        current = profiles.load_active()
        if current is not None and current.solvers.get(solver) is not None:
            binding = profiles.rebound(current, solver, script)
    found = inspect(solver, script, binding=binding)
    answer = found.as_dict()
    lines = [found.verdict()] + [
        f"  {'ok     ' if m.available else 'missing' if m.available is False else '?      '}"
        f" {m.title}: {m.detail}" for m in found.modules]
    if args.action == "use":
        answer["entered"] = False
        if found.valid:
            from axqua.install.runner import bind

            # the inspection has just entered the environment: bind checks the files only
            written, findings, _known = bind(
                {f"solvers.{solver}.setup_script": str(found.script)}, probe=False)
            # all findings of the inspection, the ambient one too: a script that only works because the terminal set the code up already is what a detached job then cannot reach
            seen = {(f.code, f.message) for f in found.findings}
            answer.update(entered=True, path=str(written),
                          findings=[f.as_dict() for f in found.findings]
                          + [f.as_dict() for f in findings
                             if (f.code, f.message) not in seen])
            lines.append(f"entered in {written}")
        else:
            lines.append("not entered in the profile: the installation is not usable")
    return emit(command, answer, as_json=args.as_json, lines=lines)


def _yaml_lines(profile) -> list[str]:
    import yaml
    text = yaml.safe_dump(profile.as_dict(), sort_keys=False, default_flow_style=False,
                          allow_unicode=True)
    return text.rstrip("\n").split("\n")


__all__ = ["run_profile"]
