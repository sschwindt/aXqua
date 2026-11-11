"""``axqua install``: install TELEMAC, OpenFOAM and the postprocessors.

``overview``   what this computer is, and what is installed, running or was tried
``plan``       what an installation would do and what is missing; changes nothing
``packages``   the system packages an installation needs; ``--elevate`` installs them
``start``      run an installation, detached unless ``--foreground``
``status``     how far an installation is, with the end of its log (``--tail``)
``cancel``     stop an installation; what is already built stays
``list``       every installation on record
``telemac-examples``  download the files that TELEMAC's own example cases read

The installation wizards of the QGIS plugin are these commands with a window around
them. ``run`` is what a detached installation executes and is not meant to be typed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from axqua.jobcli import _common, _setup_logging, emit, fail, parse_args

log = logging.getLogger("axqua")

ACTIONS = ("overview", "plan", "packages", "start", "run", "status", "cancel", "list",
           "telemac-examples")


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
    p.add_argument("--for-all-users", action="store_true",
                   help="install below /opt for every user of this computer; needs "
                        "the password of an administrator before and after the build. "
                        "Without it, the program is installed for you alone in your "
                        "home folder and needs no administrator")
    p.add_argument("--tag", default="", help="TELEMAC: the version to build")
    p.add_argument("--salome", type=Path,
                   help="TELEMAC: a downloaded SALOME archive to install with it")
    p.add_argument("--telemac-examples", choices=recipes.TELEMAC_EXAMPLES,
                   default="inputs",
                   help="TELEMAC: what to download of its example cases. inputs "
                        "(default): the files a run reads, about 460 MB; all: "
                        "reference results and manuals as well, 1.65 GB in 1,500 "
                        "files, which takes about an hour; none: steering files only")
    p.add_argument("--no-telemac-examples", action="store_true",
                   help="the same as --telemac-examples none")
    p.add_argument("--reuse-openfoam", default="auto", metavar="BASHRC",
                   help="OpenFOAM: the etc/bashrc of an installed v2406, 'auto' to "
                        "look for one (default), or 'no' to compile OpenFOAM")
    p.add_argument("--jobs", type=int, help="processes for compiling")
    p.add_argument("--no-visualization", action="store_true",
                   help="OpenFOAM: do not install ParaView and VisIt with it")
    p.add_argument("--no-visit", action="store_true",
                   help="OpenFOAM and postprocessors: do not install VisIt. aXqua "
                        "recommends VisIt, because its predefined figures of "
                        "three-dimensional results are rendered with it")
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
    p.add_argument("--reinstall", choices=("continue", "replace"), default="",
                   help="what happens to an earlier installation in the folder: "
                        "continue it, keeping what is downloaded and built (what the "
                        "installers do by themselves), or replace it: remove it first "
                        "and install anew")
    p.add_argument("--foreground", action="store_true",
                   help="start: run here and print the log, instead of detached")
    p.add_argument("--elevate", action="store_true",
                   help="packages: install the missing ones, asking for the "
                        "administrator password in a dialog of the desktop")
    p.add_argument("--tail", type=int, default=0, metavar="N",
                   help="status: include the last N lines of the log")
    p.add_argument("--native", action="store_true",
                   help="Windows, TELEMAC: build it as a Windows program in your user folder, without WSL and without "
                        "an administrator; it runs on one processor core unless --mpi is given. The default where "
                        "WSL is not set up")
    p.add_argument("--mpi", action="store_true",
                   help="TELEMAC built on Windows: with parallel runs (MPI); downloads MS-MPI and METIS into the "
                        "installation folder, no administrator")
    p.add_argument("--modules", choices=recipes.MODULES, default="2d",
                   help="TELEMAC built on Windows: 2d (TELEMAC-2D with GAIA, the tested build; default) or all "
                        "(also TELEMAC-3D)")
    p.add_argument("--distro", default="",
                   help="Windows: the WSL distribution to install into (default: the default distribution)")
    p.add_argument("--installed", default="", help=argparse.SUPPRESS)   # overview inside WSL: what the Windows profile names
    return _common(p)


def _options(args):
    from axqua.install import recipes
    return recipes.Options(
        folder=args.folder, scope="system" if args.for_all_users else "user",
        tag=args.tag or "", salome=args.salome,
        telemac_examples="none" if args.no_telemac_examples else args.telemac_examples,
        reuse_openfoam=args.reuse_openfoam or "auto", jobs=args.jobs,
        visualization=not args.no_visualization, visit=not args.no_visit,
        examples=args.examples,
        smoke_test=not args.no_smoke_test, installers=args.installers,
        ref=args.ref or "", base=args.base or "", bind=not args.no_bind,
        reinstall=args.reinstall or "", native=bool(getattr(args, "native", False)),
        modules=getattr(args, "modules", "2d") or "2d", mpi=bool(getattr(args, "mpi", False)))


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

        if os.name == "nt" and args.action in WINDOWS_TO_WSL:
            return _on_windows(args, argv, command)

        if args.action == "run":
            return runner.execute(Path(args.target or "."))

        if args.action == "telemac-examples":
            from axqua.core.errors import ConfigError
            from axqua.install import telemac_examples

            home = Path(args.target or "").expanduser()
            if not (home / "sources").is_dir() or not (home / "examples").is_dir():
                raise ConfigError(
                    f"{home} is not the folder of a TELEMAC installation",
                    subject="target",
                    remedy="Name the folder telemac-mascaret of the installation.")
            # progress belongs on stderr when stdout carries the JSON document
            stream = sys.stderr if args.as_json else sys.stdout
            done = telemac_examples.fetch_inputs(
                home, say=lambda text: print(text, file=stream, flush=True))
            emit(command, done, as_json=args.as_json, lines=[])
            return 0 if not done["missing"] else 1

        if args.action == "overview":
            data = _overview(json.loads(args.installed) if args.installed else None)
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


KEYS = {"telemac": ("telemac",), "openfoam": ("openfoam",), "postprocessors": ("paraview", "visit")}


def _installed_here() -> dict[str, str]:
    """``program -> file`` aXqua runs for it on this computer (profile, settings, discovery)."""
    from axqua.core import machine

    installed = {}
    for key in (k for keys in KEYS.values() for k in keys):
        try:
            found = machine.resolve(key)
        except Exception:                       # noqa: BLE001 - a hint, never fatal
            found = None
        if found:
            installed[key] = str(found.path)
        elif key in ("paraview", "visit") and shutil.which(key):
            installed[key] = shutil.which(key)
    return installed


def _overview(installed_all: dict[str, str] | None = None) -> dict:
    """What a person needs to see before deciding to install anything.

    *installed_all* replaces what is found here: inside WSL on behalf of Windows, what the profile of Windows names.
    """
    from axqua.install import host, recipes, runner

    here = host.detect()
    found_all = _installed_here() if installed_all is None else installed_all
    targets = []
    for target in recipes.TARGETS:
        installed = {key: found_all[key] for key in KEYS[target] if key in found_all}
        targets.append({
            "target": target, "title": recipes.TITLES[target], "installed": installed,
            "running": runner.latest(target, active_only=True),
            "last": runner.latest(target),
            "existing": recipes.existing_installation(target, installed),
            "default_folder": str(recipes.default_folder(target)),
            "default_folders": {scope: str(recipes.default_folder(target, scope))
                                for scope in recipes.SCOPES},
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
    data = plan if isinstance(plan, dict) else plan.as_dict()
    lines = [f"{data['title']} on {data['host']['description']}"] + ([f"folder: {data['folder']}"] if data["folder"] else [])
    lines += [f"  {note}" for note in data["notes"]]
    if data["estimate"]:
        lines.append(f"time: {data['estimate']}")
    for number, step in enumerate(data["steps"], start=1):
        lines.append(f"step {number}: {step['name']}")
        lines.append(f"  $ {step['command']}")
    for key, value in data["outputs"].items():
        lines.append(f"enters in the profile: {key} = {value}")
    lines += [f"{f['severity'].upper()} [{f['code']}]: {f['message']} {f.get('remedy', '')}".rstrip()
              for f in data["findings"]]
    scope = " --for-all-users" if data["options"].get("scope") == "system" else ""
    lines.append("ready to start: axqua install start " + data["target"] + scope if data["ready"]
                 else "this installation cannot be started as it is")
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


# ------------------------------------------------------------------------- Windows

#: What Windows hands over to the WSL distribution; status, list and cancel read the folders on this side.
WINDOWS_TO_WSL = ("overview", "plan", "packages", "start", "telemac-examples")


def _flags() -> set[str]:
    """The options of `axqua install` that take no value, as the parser defines them."""
    return {name for action in _parser()._actions if action.nargs == 0 for name in action.option_strings}


def _without(argv: list[str], *names: str) -> list[str]:
    """*argv* without the options *names* and, for an option that takes one, its value."""
    out, skip, flags = [], False, _flags()
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg in names:
            skip = arg not in flags
            continue
        if any(arg.startswith(name + "=") for name in names):
            continue
        out.append(arg)
    return out


#: Options whose value is a path: a Windows dialog gives C:\... or \\wsl.localhost\..., the distribution needs its Linux spelling.
PATH_OPTIONS = ("--folder", "--salome", "--installers", "--reuse-openfoam")


def _linux_paths(argv: list[str]) -> list[str]:

    out = list(argv)
    for index, arg in enumerate(out):
        name, sep, value = arg.partition("=")
        if sep and name in PATH_OPTIONS:
            out[index] = f"{name}={_to_linux(value)}"
        elif index and out[index - 1] in PATH_OPTIONS:
            out[index] = _to_linux(arg)
    return out


def _to_linux(value: str) -> str:
    from axqua.core import wsl
    return value if value in ("auto", "no", "") else wsl.to_linux(value)


def _windows_host(data: dict, distro: str | None, status) -> None:
    """Say in an answer of WSL that it is about this Windows computer and its distribution."""
    import platform

    inside = data.get("host") or {}
    windows = f"Windows {platform.release()}".strip()
    data["host"] = {**inside, "system": "windows", "windows": windows, "distro_wsl": distro or "",
                    # without WSL, TELEMAC is built on Windows itself; OpenFOAM still needs WSL
                    "supported": distro is None or bool(inside.get("supported")),
                    "description": (f"{windows}, TELEMAC and OpenFOAM in WSL: {inside.get('description', '')}"
                                    if distro else f"{windows}, without WSL: TELEMAC is built in Windows itself")}
    data["wsl"] = status.as_dict()


def _on_windows(args, argv: list[str], command: str) -> int:
    """``axqua install`` on Windows: the installer code of this aXqua runs inside the WSL distribution (:mod:`axqua.install.wslproxy`)."""
    from axqua.core import wsl
    from axqua.core.errors import ConfigError
    from axqua.install import recipes, runner, wslproxy

    distro, status = wslproxy.choose_distro(args.distro or None)
    if args.distro and distro is None:
        raise ConfigError(f"there is no WSL 2 distribution {args.distro} on this computer", subject="install.distro",
                          remedy="Installed: " + (", ".join(d.name for d in status.distros) or "none"))
    if args.action == "overview":
        if distro is None:
            data = _overview()
            for entry in data.get("targets") or []:
                entry["default_folder"], entry["default_folders"] = "", {}   # a folder of Windows would never be used
                if entry["target"] == "telemac":    # except for TELEMAC built on Windows itself
                    entry["default_folder"] = str(recipes.windows_folder())
                    entry["default_folders"] = {"user": entry["default_folder"]}
        else:
            here = _installed_here()
            linux = {key: wsl.to_linux(value) for key, value in here.items() if wsl.is_unc(value)}
            data = wslproxy.require(wslproxy.call(["install", "overview", "--installed", json.dumps(linux)], distro))
            for entry in data.get("targets") or []:
                entry["installed"] = {key: here[key] for key in KEYS[entry["target"]] if key in here}
        _windows_host(data, distro, status)
        for entry in data.get("targets") or []:
            if entry["target"] == "telemac":        # the folder of TELEMAC built on Windows, which the wizard offers too
                entry["native_folder"] = str(recipes.windows_folder())
                entry["native_existing"] = recipes.existing_installation("telemac", entry.get("installed") or {})
        return emit(command, data, as_json=args.as_json, lines=_overview_lines(data))

    target = args.target if args.action != "telemac-examples" else None
    if target is not None:
        target = _need_target(args)
    if target == "telemac" and (args.native or args.mpi or distro is None):      # --mpi is the parallel build in Windows
        return _native_windows(args, command, distro, status)
    if distro is None:
        # no WSL: the plan of this computer says so (axqua.install.windows_needs_wsl)
        if args.action == "start":
            raise ConfigError("TELEMAC and OpenFOAM run in the Windows Subsystem for Linux (WSL 2), which is not set up on this computer",
                              subject="install.distro", remedy=recipes.WINDOWS_REMEDY)
        from axqua.install import host as hosts
        data = recipes.plan(target or "telemac", _options(args), host=hosts.detect(system="win32")).as_dict()
        data["folder"] = ""        # no distribution, so no folder to name (Windows showed C:\\Users\\<name>\\opt)
        _windows_host(data, None, status)
        if args.action == "packages":
            data = {**data["packages"], "wsl": data["wsl"]}
        return emit(command, data, as_json=args.as_json, lines=_plan_lines(data) if args.action == "plan" else [])
    if target == "postprocessors":
        raise ConfigError("on Windows, ParaView and VisIt are Windows programs, not part of the installation into WSL",
                          subject="install.postprocessors",
                          remedy="Install ParaView (paraview.org; its ZIP needs no administrator) and VisIt for Windows, "
                                 "and enter them in the profile on the Configuration tab.")

    forwarded = ["install", *_linux_paths(_without(argv, "--distro", "--json"))]
    if args.action == "telemac-examples":
        home = args.target or ""
        forwarded = [a if a != home else wsl.to_linux(home) for a in forwarded]
        answer = wslproxy.call(forwarded, distro, timeout=None, stream_stderr=True)
        return emit(command, wslproxy.require(answer), as_json=args.as_json, lines=[])
    planned = (["install", "plan"] + forwarded[forwarded.index(args.action, 1) + 1:]) if args.action == "start" else list(forwarded)
    # the profile is the one of Windows (bound by runner.read); the viewers are Windows programs
    if "--no-bind" not in planned:
        planned.append("--no-bind")
    if target == "openfoam" and "--no-visualization" not in planned:
        planned.append("--no-visualization")
    data = wslproxy.require(wslproxy.call(planned, distro))
    if args.action == "packages":
        _windows_host(data, distro, status)
        lines = [f"{len(data.get('needed') or [])} system packages are needed, {len(data.get('missing') or [])} are not installed"]
        return emit(command, data, as_json=args.as_json, lines=lines)
    _windows_host(data, distro, status)
    data["distro"] = distro
    # entered in the Windows profile by runner.read; options.bind stays False, so the runner inside WSL does not enter it in a profile of its own
    data["bind_windows"] = not args.no_bind
    if args.action == "plan":
        return emit(command, data, as_json=args.as_json, lines=_plan_lines(data))
    started = runner.start_in_wsl(data, distro, detach=not args.foreground, echo=args.foreground,
                                  bind_here=not args.no_bind)
    lines = _status_lines(started)
    if not args.foreground:
        lines += ["The installation runs by itself. Follow it with:", f"  axqua install status {started['id']} --tail 20"]
    emit(command, started, as_json=args.as_json, lines=lines)
    return 0 if started.get("state") in ("queued", "running", "succeeded") else 1


def _native_windows(args, command: str, distro: str | None, status) -> int:
    """``axqua install plan|packages|start telemac`` for TELEMAC built on Windows itself (:func:`axqua.install.recipes._plan_telemac_windows`): the plan is made and run on this side, the result entered in the profile by the runner itself."""
    from axqua.install import host as hosts
    from axqua.install import recipes, runner

    options = _options(args)
    options.native = True
    plan = recipes.plan("telemac", options, host=hosts.detect(system="win32"))
    if args.action == "packages":
        data = {**plan.as_dict()["packages"], "wsl": status.as_dict()}
        return emit(command, data, as_json=args.as_json,
                    lines=["TELEMAC built on Windows needs no system packages: its compilers come from conda-forge"])
    if args.action == "plan":
        data = plan.as_dict()
        _windows_host(data, distro, status)
        data["host"]["description"] = f"{data['host']['windows']}: TELEMAC is built in Windows itself"
        data["native"] = True
        return emit(command, data, as_json=args.as_json, lines=_plan_lines(data))
    started = runner.start(plan, detach=not args.foreground, echo=args.foreground)
    lines = _status_lines(started)
    if not args.foreground:
        lines += ["The installation runs by itself. Follow it with:", f"  axqua install status {started['id']} --tail 20"]
    emit(command, started, as_json=args.as_json, lines=lines)
    return 0 if started.get("state") in ("queued", "running", "succeeded") else 1


__all__ = ["run_install"]
