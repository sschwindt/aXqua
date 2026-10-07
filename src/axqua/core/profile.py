"""The plugin profile: this computer, described in one file (``*.axq-profile``).

A case describes a river reach and has to travel. Where Python is, where TELEMAC and
OpenFOAM are installed, which program draws the figures and which drive takes the
results describe a *computer*, and they used to be spread over three places: the solver
settings of :mod:`axqua.core.machine` (``solvers.yml``), the solver profiles of
:mod:`axqua.jobs.profiles` (``profiles.yml``) and the QGIS settings of the plugin. A user
setting up a second computer had to know all three.

The profile is the one file that replaces them as the documented way. It is what the
*Configuration* tab of the plugin edits, and it is plain YAML, so it can be written by
hand on a machine without QGIS::

    schema_version: 1
    name: workstation
    python:
      executable: /home/user/miniforge3/envs/axqua-env/bin/python
      axqua: /home/user/miniforge3/envs/axqua-env/bin/axqua
    solvers:
      telemac:
        setup_script: /home/user/opt/telemac-mascaret/configs/pysource.debian12.sh
        mpi_processes: 12
      openfoam:
        setup_script: /usr/lib/openfoam/openfoam2406/etc/bashrc
        mpi_processes: 16
    postprocessors:
      paraview: /opt/ParaView/bin/paraview
      visit: /opt/visit/bin/visit
    jobs:
      root: /scratch/axqua-jobs
      launcher: auto
    display:
      min_depth: 0.01
      velocity_cap: 5.0

Which profile is active
-----------------------
First hit wins: an explicit path (``--profile``), the environment variable
``AXQUA_PROFILE``, then ``default.axq-profile`` in the aXqua configuration folder. No
profile at all is a supported state - the older mechanisms keep working, which is what
lets every existing installation continue unchanged.

The older files are not deleted and not rewritten. :func:`detect` reads them, so a
profile for an existing installation is one command (``axqua profile init``).

Standard library plus PyYAML, and nothing from a solver backend or the job system: the
profile is read by :func:`axqua.core.machine.resolve` on every case load.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from axqua.core.diagnostics import ERROR, WARNING, Finding
from axqua.core.errors import ConfigError

log = logging.getLogger("axqua")

SUFFIX = ".axq-profile"
ENV_VAR = "AXQUA_PROFILE"
DEFAULT_NAME = "default" + SUFFIX
SCHEMA_VERSION = 1

#: The codes a profile can bind, and the programs it can name for figures.
SOLVERS = ("telemac", "openfoam")
POSTPROCESSORS = ("paraview", "visit")
ENVIRONMENTS = ("posix", "windows", "wsl")
LAUNCHERS = ("auto", "systemd", "posix", "windows", "wsl")

#: The OpenFOAM release aXqua writes dictionaries for. Another one starts, reads a file
#: it does not understand, and fails far from the cause.
OPENFOAM_RELEASE = "2406"

__all__ = ["AxquaProfile", "SolverBinding", "active_path", "check", "default_path",
           "detect", "load", "load_active", "save"]


def _path(value: Any, base: Path | None, *, verbatim: bool = False) -> Path | None:
    """*value* as a path, relative entries resolved against the profile's folder.

    *verbatim* keeps the text as written. A path inside a WSL distribution is a Linux
    path, and resolving it against a Windows folder would produce something that exists
    on neither side.
    """
    if value in (None, ""):
        return None
    path = Path(str(value)).expanduser()
    if verbatim or path.is_absolute() or base is None:
        return path
    return (base / path).resolve()


def _reject_unknown(data: dict, known: set[str], where: str) -> None:
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(
            f"{where} has unknown key" + ("s" if len(unknown) > 1 else "") + ": "
            + ", ".join(unknown),
            subject=f"{where}.{unknown[0]}" if where else unknown[0],
            remedy="Known keys: " + ", ".join(sorted(known)),
        )


@dataclass
class SolverBinding:
    """How to reach one simulation code on this computer."""

    setup_script: Path | None = None
    environment: str | None = None      # posix | windows | wsl; None matches the host
    shell: str | None = None            # a bash that the installation ships
    distro: str | None = None           # WSL only
    config_name: str | None = None      # TELEMAC: the systel configuration
    mpi_launcher: str | None = None
    mpi_processes: int | None = None
    overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, solver: str, data: dict[str, Any] | None, *,
                  base: Path | None = None) -> "SolverBinding":
        data = dict(data or {})
        _reject_unknown(data, {f.name for f in fields(cls)}, f"solvers.{solver}")
        environment = data.get("environment")
        if environment is not None and environment not in ENVIRONMENTS:
            raise ConfigError(
                f"solvers.{solver}.environment must be one of "
                + ", ".join(ENVIRONMENTS) + f", got {environment!r}",
                subject=f"solvers.{solver}.environment")
        processes = data.get("mpi_processes")
        return cls(
            setup_script=_path(data.get("setup_script"), base,
                               verbatim=environment == "wsl"),
            environment=environment,
            shell=data.get("shell") or None,
            distro=data.get("distro") or None,
            config_name=data.get("config_name") or None,
            mpi_launcher=data.get("mpi_launcher") or None,
            mpi_processes=int(processes) if processes not in (None, "") else None,
            overrides={str(k): str(v) for k, v in (data.get("overrides") or {}).items()},
        )

    def as_dict(self) -> dict[str, Any]:
        """Only what is set: a written profile records decisions, not defaults."""
        out: dict[str, Any] = {}
        if self.setup_script is not None:
            out["setup_script"] = str(self.setup_script)
        for key in ("environment", "shell", "distro", "config_name", "mpi_launcher",
                    "mpi_processes"):
            value = getattr(self, key)
            if value not in (None, ""):
                out[key] = value
        if self.overrides:
            out["overrides"] = dict(self.overrides)
        return out


@dataclass
class AxquaProfile:
    """Everything aXqua needs to know about one computer."""

    name: str = ""
    path: Path | None = None            # where it was read from; never written out
    python: Path | None = None          # the interpreter that runs aXqua
    axqua: Path | None = None           # the program the plugin calls
    solvers: dict[str, SolverBinding] = field(default_factory=dict)
    postprocessors: dict[str, Path] = field(default_factory=dict)
    job_root: Path | None = None
    launcher: str = "auto"
    min_depth: float = 0.01             # [m] shallower water is drawn transparent
    velocity_cap: float = 5.0           # [m/s] upper end of the velocity color scale
    schema_version: int = SCHEMA_VERSION

    # -- reading -----------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, *, path: Path | None = None
                  ) -> "AxquaProfile":
        data = dict(data or {})
        _reject_unknown(data, {"schema_version", "name", "python", "solvers",
                               "postprocessors", "jobs", "display"}, "")
        version = int(data.get("schema_version") or SCHEMA_VERSION)
        if version > SCHEMA_VERSION:
            raise ConfigError(
                f"this profile was written by a newer aXqua (schema {version}; this "
                f"version understands {SCHEMA_VERSION})",
                subject="schema_version", remedy="Update aXqua.")
        base = Path(path).parent if path else None

        python = dict(data.get("python") or {})
        _reject_unknown(python, {"executable", "axqua"}, "python")
        solvers = dict(data.get("solvers") or {})
        _reject_unknown(solvers, set(SOLVERS), "solvers")
        post = dict(data.get("postprocessors") or {})
        _reject_unknown(post, set(POSTPROCESSORS), "postprocessors")
        jobs = dict(data.get("jobs") or {})
        _reject_unknown(jobs, {"root", "launcher"}, "jobs")
        display = dict(data.get("display") or {})
        _reject_unknown(display, {"min_depth", "velocity_cap"}, "display")

        return cls(
            name=str(data.get("name") or (Path(path).stem if path else "")),
            path=Path(path) if path else None,
            python=_path(python.get("executable"), base),
            axqua=_path(python.get("axqua"), base),
            solvers={name: SolverBinding.from_dict(name, entry, base=base)
                     for name, entry in solvers.items()},
            postprocessors={name: found for name, value in post.items()
                            if (found := _path(value, base)) is not None},
            job_root=_path(jobs.get("root"), base),
            launcher=str(jobs.get("launcher") or "auto"),
            min_depth=float(display.get("min_depth", 0.01)),
            velocity_cap=float(display.get("velocity_cap", 5.0)),
            schema_version=version,
        )

    # -- writing -----------------------------------------------------------------
    def as_dict(self) -> dict[str, Any]:
        """Plain data, shaped the way :meth:`from_dict` reads it back."""
        out: dict[str, Any] = {"schema_version": self.schema_version}
        if self.name:
            out["name"] = self.name
        python = {key: str(value) for key, value in (("executable", self.python),
                                                     ("axqua", self.axqua)) if value}
        if python:
            out["python"] = python
        solvers = {name: binding.as_dict() for name, binding in self.solvers.items()}
        if solvers:
            out["solvers"] = solvers
        if self.postprocessors:
            out["postprocessors"] = {name: str(path)
                                     for name, path in self.postprocessors.items()}
        jobs: dict[str, Any] = {}
        if self.job_root:
            jobs["root"] = str(self.job_root)
        if self.launcher and self.launcher != "auto":
            jobs["launcher"] = self.launcher
        if jobs:
            out["jobs"] = jobs
        out["display"] = {"min_depth": self.min_depth, "velocity_cap": self.velocity_cap}
        return out

    # -- use ---------------------------------------------------------------------
    def script(self, key: str) -> Path | None:
        """The setup script of a solver, or the launcher of a postprocessor."""
        if key in self.solvers:
            return self.solvers[key].setup_script
        return self.postprocessors.get(key)

    def describe(self) -> str:
        """One line for a status report."""
        bound = [name for name in SOLVERS if self.script(name)]
        return (f"{self.name or 'profile'} ({self.path or 'not saved'}): "
                + (", ".join(bound) if bound else "no simulation software bound"))


# ----------------------------------------------------------------------- locating


def default_path() -> Path:
    """Where the default profile lives (whether or not it exists)."""
    from axqua.core import machine
    return machine.settings_path().parent / DEFAULT_NAME


def active_path(explicit: str | os.PathLike | None = None) -> Path | None:
    """The profile in force, or ``None`` when this computer has none."""
    if explicit:
        return Path(explicit).expanduser()
    named = os.environ.get(ENV_VAR)
    if named:
        return Path(named).expanduser()
    default = default_path()
    return default if default.is_file() else None


# ------------------------------------------------------------------------ file I/O


def load(path: str | os.PathLike) -> AxquaProfile:
    """Read a profile. Raises :class:`ConfigError` for anything that is not one."""
    import yaml

    target = Path(path).expanduser()
    if not target.is_file():
        raise ConfigError(f"profile not found: {target}", subject=str(target),
                          remedy="Create one with 'axqua profile init'.")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{target} is not valid YAML", subject=str(target),
                          cause=str(exc)) from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{target} is not an aXqua profile", subject=str(target))
    return AxquaProfile.from_dict(raw, path=target)


#: path -> (modification time, profile). A case load asks for the active profile once
#: per solver, and a study loads the case once per mesh.
_CACHE: dict[Path, tuple[int, AxquaProfile]] = {}
_WARNED: set[tuple[Path, int]] = set()


def load_active(explicit: str | os.PathLike | None = None, *, strict: bool = False
                ) -> AxquaProfile | None:
    """The active profile, or ``None``.

    Not *strict* by default, because this runs inside every case load: a profile with a
    typing error must not stop a build that the older settings can still serve. It is
    reported once and skipped. The commands that are *about* the profile pass
    ``strict=True`` and get the error.
    """
    target = active_path(explicit)
    if target is None:
        return None
    try:
        stamp = target.stat().st_mtime_ns
    except OSError:
        if strict:
            raise ConfigError(f"profile not found: {target}", subject=str(target),
                              remedy=f"Check {ENV_VAR}, or create the profile with "
                                     "'axqua profile init'.") from None
        if (target, 0) not in _WARNED:
            _WARNED.add((target, 0))
            log.warning("the profile %s does not exist; ignoring it", target)
        return None
    cached = _CACHE.get(target)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        profile = load(target)
    except ConfigError as exc:
        if strict:
            raise
        if (target, stamp) not in _WARNED:
            _WARNED.add((target, stamp))
            log.warning("could not read the profile %s (%s); ignoring it", target, exc)
        return None
    _CACHE[target] = (stamp, profile)
    return profile


def save(profile: AxquaProfile, path: str | os.PathLike | None = None) -> Path:
    """Write the profile, atomically, and return where it went."""
    import yaml

    target = Path(path).expanduser() if path else (profile.path or default_path())
    if target.suffix != SUFFIX:
        target = target.with_name(target.name + SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(profile.as_dict(), sort_keys=False, default_flow_style=False,
                          allow_unicode=True)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    profile.path = target
    _CACHE.pop(target, None)
    return target


# ----------------------------------------------------------------------- detecting


def detect(name: str | None = None) -> AxquaProfile:
    """A profile for this computer, from what is already known and what can be found.

    Reads the older settings (the environment variables, ``solvers.yml``, the usual
    installation folders) through :func:`axqua.core.machine.resolve`, so a computer on
    which aXqua already works gets a profile that describes exactly that. Nothing is
    probed and nothing is written; :func:`check` and :func:`save` do those.
    """
    import socket

    from axqua.core import machine

    profile = AxquaProfile(name=name or socket.gethostname(), python=Path(sys.executable))
    program = shutil.which("axqua")
    sibling = Path(sys.executable).with_name("axqua")
    if sibling.is_file():
        profile.axqua = sibling
    elif program:
        profile.axqua = Path(program)

    for solver in SOLVERS:
        found = machine.resolve(solver, use_profile=False)
        if found:
            profile.solvers[solver] = SolverBinding(setup_script=found.path)
    for program_name in POSTPROCESSORS:
        found = machine.resolve(program_name, use_profile=False)
        if found:
            profile.postprocessors[program_name] = found.path
        elif shutil.which(program_name):
            profile.postprocessors[program_name] = Path(shutil.which(program_name))
    return profile


# ------------------------------------------------------------------------ checking


def check(profile: AxquaProfile, *, probe: bool = True, timeout: float = 120.0
          ) -> list[Finding]:
    """Everything that is wrong with *profile*. Never raises.

    Three tiers. *Files*: every named path exists. *Factual*: the values are possible.
    *Functional* (only with *probe*, because it starts a shell per code): each
    environment script can actually be entered and yields the code it claims to. The
    last tier is the one that tells a working installation from a path that merely
    exists, and it takes seconds - which is why a caller can leave it out.
    """
    findings: list[Finding] = []

    def add(severity: str, code: str, subject: str, message: str, remedy: str = "") -> None:
        findings.append(Finding(severity, code, message, subject=subject, remedy=remedy))

    for key, value, what in (("python.executable", profile.python, "Python interpreter"),
                             ("python.axqua", profile.axqua, "aXqua program")):
        if value is not None and not Path(value).is_file():
            add(ERROR, "axqua.environment.program_missing", key,
                f"the {what} {value} does not exist",
                "Select the file again, or detect it with 'axqua profile detect'.")

    for solver in SOLVERS:
        findings.extend(_check_binding(profile, solver, probe=probe, timeout=timeout))

    for program, value in profile.postprocessors.items():
        target = Path(value)
        if not target.is_file():
            add(ERROR, "axqua.environment.program_missing", f"postprocessors.{program}",
                f"the {program} launcher {target} does not exist")
        elif not os.access(target, os.X_OK):
            add(ERROR, "axqua.environment.program_missing", f"postprocessors.{program}",
                f"the {program} launcher {target} is not executable")

    if profile.job_root is not None:
        root = Path(profile.job_root)
        existing = next((p for p in (root, *root.parents) if p.exists()), None)
        if existing is None or not os.access(existing, os.W_OK):
            add(ERROR, "axqua.environment.job_root_not_writable", "jobs.root",
                f"the job root {root} cannot be written to",
                "Select a folder on a drive with write access and sufficient space.")

    if profile.launcher not in LAUNCHERS:
        add(ERROR, "axqua.config.invalid_value", "jobs.launcher",
            f"the launcher must be one of {', '.join(LAUNCHERS)}, "
            f"got {profile.launcher!r}")
    if profile.min_depth < 0:
        add(ERROR, "axqua.config.invalid_value", "display.min_depth",
            "the minimum water depth cannot be negative")
    if profile.velocity_cap <= 0:
        add(ERROR, "axqua.config.invalid_value", "display.velocity_cap",
            "the upper limit of the velocity scale must be positive")
    return findings


def _check_binding(profile: AxquaProfile, solver: str, *, probe: bool, timeout: float
                   ) -> list[Finding]:
    subject = f"solvers.{solver}.setup_script"
    binding = profile.solvers.get(solver)
    if binding is None or binding.setup_script is None:
        return [Finding(
            WARNING, "axqua.environment.solver_unbound",
            f"no environment script is set for {solver}, so simulations with it cannot "
            "be started", subject=subject,
            remedy="Leave it empty if this code is not used on this computer.")]

    out: list[Finding] = []
    script = Path(binding.setup_script)
    on_host = binding.environment != "wsl"      # a WSL path cannot be checked from here
    if on_host and not script.is_file():
        return [Finding(ERROR, "axqua.environment.script_missing",
                        f"the environment script of {solver} does not exist: {script}",
                        subject=subject,
                        remedy="Select the script of the installation on this computer.")]

    cores = os.cpu_count() or 0
    if binding.mpi_processes is not None and binding.mpi_processes < 1:
        out.append(Finding(ERROR, "axqua.config.invalid_value",
                           "the number of processes must be at least 1",
                           subject=f"solvers.{solver}.mpi_processes"))
    elif binding.mpi_processes and cores and binding.mpi_processes > cores:
        out.append(Finding(
            WARNING, "axqua.environment.too_many_processes",
            f"{binding.mpi_processes} processes are requested for {solver}, but this "
            f"computer has {cores} logical cores",
            subject=f"solvers.{solver}.mpi_processes",
            remedy="More processes than cores slow a simulation down."))

    if probe:
        out.extend(_probe(solver, binding, subject, timeout))
    return out


def _probe(solver: str, binding: SolverBinding, subject: str, timeout: float
           ) -> list[Finding]:
    """Enter the environment and see whether the code is really there."""
    from axqua.core.environment import SolverEnvironment, default_kind

    overrides = dict(binding.overrides)
    if binding.config_name:
        overrides.setdefault("USETELCFG", binding.config_name)
    environment = SolverEnvironment(
        kind=binding.environment or default_kind(), setup_script=binding.setup_script,
        shell=binding.shell, distro=binding.distro, mpi_launcher=binding.mpi_launcher,
        overrides=overrides)
    status = environment.validate(solver, timeout=timeout)
    if not status.ok:
        return [Finding(ERROR, "axqua.environment.solver_unreachable",
                        f"{solver} cannot be reached: {status.detail}", subject=subject,
                        remedy="Check that this is the environment script of a working "
                               "installation.")]
    out: list[Finding] = []
    if status.ambient:
        out.append(Finding(
            WARNING, "axqua.environment.ambient",
            f"the variables of {solver} were already set before its environment script "
            "ran, so the script may not be what provides them", subject=subject,
            remedy="A job does not inherit the settings of a terminal. Make sure the "
                   "script itself sets up the installation."))
    if solver == "openfoam":
        release = str(status.variables.get("WM_PROJECT_VERSION", ""))
        if OPENFOAM_RELEASE not in release:
            out.append(Finding(
                WARNING, "axqua.environment.openfoam_version",
                f"this is OpenFOAM {release or 'of an unknown version'}; aXqua writes "
                f"input files for v{OPENFOAM_RELEASE}", subject=subject,
                remedy=f"Bind the environment script of OpenFOAM v{OPENFOAM_RELEASE}."))
    return out
