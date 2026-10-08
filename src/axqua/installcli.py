"""``axqua install``: install TELEMAC, OpenFOAM and the postprocessors.

``overview``   what this computer is, and what is installed, running or was tried
``plan``       what an installation would do and what is missing; changes nothing
``packages``   the system packages an installation needs; ``--elevate`` installs them
``start``      run an installation, detached unless ``--foreground``
``status``     how far an installation is, with the end of its log (``--tail``)
``cancel``     stop an installation; what is already built stays
``list``       every installation on record

The installation wizards of the QGIS plugin are these commands with a window around
them. ``run`` is what a detached installation executes and is not meant to be typed.
"""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

from axqua.jobcli import _common, _setup_logging, emit, fail, parse_args

log = logging.getLogger("axqua")

ACTIONS = ("overview", "plan", "packages", "start", "run", "status", "cancel", "list")


def _parser() -> argparse.ArgumentParser:
    from axqua.install import host, recipes

    p = argparse.ArgumentParser(
        prog="axqua install",
        description="Install the simulation programs with the published installer "
                    "scripts. 'plan' shows what would happen before anything is "
                    "changed. An installation runs as the ordinary user; system "
                    "packages are the one part that needs an administrator.")
    p.add_argument("action", nargs="?", default="overview", choices=ACTIONS)
    p.add_argument("target", nargs="?",
                   help="what to install: " + ", ".join(recipes.TARGETS)
                        + "; for status and cancel: the installation")
    p.add_argument("--folder", type=Path, help="where to install")
    p.add_argument("--tag", default="", help="TELEMAC: the version to build")
    p.add_argument("--salome", type=Path,
                   help="TELEMAC: a downloaded SALOME archive to install with it")
    p.add_argument("--no-telemac-examples", action="store_true",
                   help="TELEMAC: do not download its example cases and manuals "
                        "(1.6 GB in 1,500 files, most of the installation time)")
    p.add_argument("--reuse-openfoam", default="auto", metavar="BASHRC",
                   help="OpenFOAM: the etc/bashrc of an installed v2406, 'auto' to "
                        "look for one (default), or 'no' to compile OpenFOAM")
    p.add_argument("--jobs", type=int, help="processes for compiling")
    p.add_argument("--no-visualization", action="store_true",
                   help="OpenFOAM: do not install ParaView and VisIt with it")
    p.add_argument("--examples", action="store_true",
                   help="OpenFOAM: also download the example case of the solver")
    p.add_argument("--no-smoke-test", action="store_true",
                   help="OpenFOAM: skip the short test run at the end")
    p.add_argument("--installers", type=Path,
                   help="a local copy of the installer scripts, instead of a download")
    p.add_argument("--ref", default="",
                   help="another commit or branch of the installer scripts")
    p.add_argument("--base", choices=sorted(host.BASES),
                   help="the release a derivative system is built on")
    p.add_argument("--no-bind", action="store_true",
                   help="do not enter the installation in the profile")
    p.add_argument("--foreground", action="store_true",
                   help="start: run here and print the log, instead of detached")
    p.add_argument("--elevate", action="store_true",
                   help="packages: install the missing ones, asking for the "
                        "administrator password in a dialog of the desktop")
    p.add_argument("--tail", type=int, default=0, metavar="N",
                   help="status: include the last N lines of the log")
    return _common(p)


def _options(args):
    from axqua.install import recipes
    return recipes.Options(
        folder=args.folder, tag=args.tag or "", salome=args.salome,
        telemac_examples=not args.no_telemac_examples,
        reuse_openfoam=args.reuse_openfoam or "auto", jobs=args.jobs,
        visualization=not args.no_visualization, examples=args.examples,
        smoke_test=not args.no_smoke_test, installers=args.installers,
        ref=args.ref or "", base=args.base or "", bind=not args.no_bind)


def _need_target(args) -> str:
    from axqua.core.errors import ConfigError
    from axqua.install import recipes

    if args.target not in recipes.TARGETS:
        raise ConfigError(
            f"'{args.action}' needs one of: " + ", ".join(recipes.TARGETS),
            subject="target")
    return args.target


def run_install(argv: list[str]) -> int:
    args = parse_args(_parser(), argv, "install")
    _setup_logging(args)
    command = f"install.{args.action}"
    try:
        from axqua.install import recipes, runner

        if args.action == "run":
            return runner.execute(Path(args.target or "."))

        if args.action == "overview":
            data = _overview()
            return emit(command, data, as_json=args.as_json, lines=_overview_lines(data))

        if args.action == "list":
            found = runner.list_all()
            return emit(command, {"installs": found}, as_json=args.as_json,
                        lines=[_status_line(s) for s in found] or ["no installations"])

        if args.action == "status":
            if args.target in recipes.TARGETS:
                status = runner.latest(args.target)
                if status is None:
                    return emit(command, {}, as_json=args.as_json,
                                lines=[f"{args.target} was not installed with aXqua"])
                status = runner.read(status["folder"], lines=args.tail)
            elif args.target:
                status = runner.read(args.target, lines=args.tail)
            else:
                found = [runner.latest(t) for t in recipes.TARGETS]
                found = [s for s in found if s]
                return emit(command, {"installs": found}, as_json=args.as_json,
                            lines=[_status_line(s) for s in found]
                            or ["no installations"])
            return emit(command, status, as_json=args.as_json,
                        lines=_status_lines(status))

        if args.action == "cancel":
            from axqua.core.errors import ConfigError
            if not args.target:
                raise ConfigError("'cancel' needs the installation to stop",
                                  subject="target",
                                  remedy="List them with 'axqua install list'.")
            ident = args.target
            if ident in recipes.TARGETS:
                running = runner.latest(ident, active_only=True)
                if running is None:
                    return emit(command, {}, as_json=args.as_json,
                                lines=[f"no installation of {ident} is running"])
                ident = running["folder"]
            status = runner.cancel(ident)
            return emit(command, status, as_json=args.as_json,
                        lines=_status_lines(status))

        target = _need_target(args)
        plan = recipes.plan(target, _options(args))

        if args.action == "plan":
            return emit(command, plan.as_dict(), as_json=args.as_json,
                        lines=_plan_lines(plan))

        if args.action == "packages":
            data = plan.as_dict()["packages"]
            if args.elevate and plan.missing:
                data["installation"] = recipes.install_packages(
                    target, plan.missing, runner.installs_root())
                data["missing"], data["unavailable"] = recipes.packages_state(plan.needed)
                data["command"] = recipes.admin_command(target, data["missing"])
            lines = [f"{len(plan.needed)} system packages are needed, "
                     f"{len(data['missing'])} are not installed"]
            if data["missing"]:
                lines += ["An administrator installs them with:", "  " + data["command"]]
            if data.get("installation"):
                lines.append(data["installation"]["message"])
            return emit(command, data, as_json=args.as_json, lines=lines)

        # start
        status = runner.start(plan, detach=not args.foreground, echo=args.foreground)
        lines = _status_lines(status)
        if not args.foreground:
            lines += ["The installation runs by itself. Follow it with:",
                      f"  axqua install status {status['id']} --tail 20"]
        emit(command, status, as_json=args.as_json, lines=lines)
        return 0 if status.get("state") in ("queued", "running", "succeeded") else 1
    except BaseException as exc:  # noqa: BLE001
        return fail(command, exc, as_json=args.as_json, verbose=args.verbose)


# ------------------------------------------------------------------------- reporting


def _overview() -> dict:
    """What a person needs to see before deciding to install anything."""
    from axqua.core import machine
    from axqua.install import host, recipes, runner

    here = host.detect()
    keys = {"telemac": ("telemac",), "openfoam": ("openfoam",),
            "postprocessors": ("paraview", "visit")}
    targets = []
    for target in recipes.TARGETS:
        installed = {}
        for key in keys[target]:
            try:
                found = machine.resolve(key)
            except Exception:                   # noqa: BLE001 - a hint, never fatal
                found = None
            if found:
                installed[key] = str(found.path)
            elif key in ("paraview", "visit") and shutil.which(key):
                installed[key] = shutil.which(key)
        targets.append({
            "target": target, "title": recipes.TITLES[target], "installed": installed,
            "running": runner.latest(target, active_only=True),
            "last": runner.latest(target),
            "default_folder": str(recipes.default_folder(target)),
        })
    return {"host": here.as_dict(), "targets": targets,
            "repository": recipes.REPOSITORY, "ref": recipes.PINNED,
            "openfoam_found": str(recipes.find_openfoam() or ""),
            "default_jobs": recipes.default_jobs()}


def _overview_lines(data: dict) -> list[str]:
    host = data["host"]
    lines = [host["description"] + ("" if host["supported"] else
                                    "  (no installer for this system)")]
    for entry in data["targets"]:
        found = ", ".join(f"{k}: {v}" for k, v in entry["installed"].items())
        state = entry["running"]
        lines.append(f"{entry['title']}: " + (found or "not found on this computer")
                     + (f"  [installation {state['id']} is running]" if state else ""))
    lines.append("Show what an installation would do: axqua install plan <target>")
    return lines


def _plan_lines(plan) -> list[str]:
    from axqua.install import recipes

    data = plan.as_dict()
    lines = [f"{data['title']} on {data['host']['description']}",
             f"folder: {data['folder']}"]
    lines += [f"  {note}" for note in data["notes"]]
    if data["estimate"]:
        lines.append(f"time: {data['estimate']}")
    for number, step in enumerate(data["steps"], start=1):
        lines.append(f"step {number}: {step['name']}")
        lines.append(f"  $ {step['command']}")
    for key, value in data["outputs"].items():
        lines.append(f"enters in the profile: {key} = {value}")
    lines += [f.line() for f in plan.findings]
    lines.append("ready to start: axqua install start " + plan.target if plan.ready
                 else "this installation cannot be started as it is")
    del recipes
    return lines


def _status_line(status: dict) -> str:
    step = ""
    if status.get("state") == "running" and status.get("steps"):
        step = (f"  step {status.get('step', 0)}/{len(status['steps'])}: "
                f"{status.get('step_name', '')}")
    minutes = float(status.get("elapsed") or 0.0) / 60.0
    return (f"{status.get('id', '?')}  {status.get('state', '?')}"
            f"  {minutes:.0f} min{step}")


def _status_lines(status: dict) -> list[str]:
    lines = [_status_line(status)]
    if status.get("message") and status.get("state") != "running":
        lines.append(status["message"])
    for key, value in (status.get("outputs") or {}).items():
        lines.append(f"  {key} = {value}")
    if status.get("bound"):
        lines.append(f"entered in the profile {status['bound']}")
    for finding in status.get("findings") or []:
        lines.append(f"{finding['severity'].upper()} [{finding['code']}]: "
                     f"{finding['message']} {finding.get('remedy', '')}".rstrip())
    if status.get("log_tail"):
        lines += ["--- " + str(status.get("log", "")), status["log_tail"]]
    return lines


__all__ = ["run_install"]
