"""The plugin profile: this computer, described in one file (``*.axq-profile``).

A case describes a free-surface water body and has to travel. Where Python is, where TELEMAC and
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
      executable: /opt/miniforge3/envs/axqua-env/bin/python
      axqua: /opt/miniforge3/envs/axqua-env/bin/axqua
    installations:
      telemac: /opt/telemac-mascaret
      openfoam: /usr/lib/openfoam/openfoam2406
      paraview: /opt/ParaView
      visit: /opt/visit
    solvers:
      telemac:
        setup_script: /opt/telemac-mascaret/configs/pysource.debian12.sh
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

Installation folders
--------------------
``installations`` names the folder each program is installed in. What aXqua runs is the
environment script of a simulation code and the launcher of a postprocessor; where the
profile names the folder only, that file is looked for in it (:func:`locate`), so a
profile of four folders is a complete one. An entry under ``solvers`` or
``postprocessors`` is used as it is and outranks what the folder holds.

A profile outlives the version that wrote it
--------------------------------------------
The profile belongs to the user and is kept in their configuration folder, outside of
aXqua and outside of the QGIS plugin, so that updating or reinstalling either leaves it
where it is. Three rules keep it readable afterwards:

* The layout only grows. A key is not renamed or removed, and a new key is optional,
  so every profile ever written keeps loading.
* An entry this version does not know is kept and written back (``extra``), and
  reported as a warning by :func:`check`. A newer plugin or a newer aXqua may have
  written it, and the rest of the profile stays in use.
* ``schema_version`` is raised only for a change an older aXqua would misread. Such a
  file is refused, and :func:`save` never writes over a file it could not read without
  keeping it under another name.

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
PROGRAMS = SOLVERS + POSTPROCESSORS

#: Where a program keeps the file a profile names, relative to the folder it is
#: installed in: the environment script of a simulation code, the launcher of a
#: postprocessor. For OpenFOAM the script of aXqua's own installation comes first,
#: because it also sets up the sediment solvers.
LAYOUT = {
    "telemac": ("configs/pysource.*.sh",),
    "openfoam": ("shell-rc.sh", "etc/bashrc"),
    "paraview": ("bin/paraview",),
    "visit": ("bin/visit",),
}

#: Sub-folders that are searched when the folder itself holds none of these files: the
#: folder an installation wizard was given holds the installation one level below.
NESTED = ("*", "apps/*")

#: Folders that say nothing about where a program is installed.
SYSTEM_PREFIXES = ("/", "/usr", "/usr/local")

#: The blocks of a profile and the keys of each. An entry that is not listed is kept
#: as it is (see the module docstring), so this table is what "known" means.
BLOCKS = {
    "python": ("executable", "axqua"),
    "installations": PROGRAMS,
    "solvers": SOLVERS,
    "postprocessors": POSTPROCESSORS,
    "jobs": ("root", "launcher"),
    "display": ("min_depth", "velocity_cap"),
}
TOP_LEVEL = ("schema_version", "name", *BLOCKS)
ENVIRONMENTS = ("posix", "windows", "wsl")
LAUNCHERS = ("auto", "systemd", "posix", "windows", "wsl")

#: The OpenFOAM release aXqua writes dictionaries for. Another one starts, reads a file
#: it does not understand, and fails far from the cause.
OPENFOAM_RELEASE = "2406"

__all__ = ["AxquaProfile", "SolverBinding", "active_path", "candidates", "check",
           "default_path", "detect", "file_path", "installation_of", "load",
           "load_active", "locate", "save"]


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


def _mapping(value: Any, where: str) -> dict:
    """*value* as a dictionary; anything else is not a profile."""
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where or 'the profile'} must be a mapping of keys to "
                          f"values, got {type(value).__name__}", subject=where)
    return dict(value)


def _unknown(data: dict, known: set[str]) -> dict:
    """The entries of *data* that this version does not know, in their order."""
    return {key: value for key, value in data.items() if key not in known}


def _number(value: Any, kind: type, subject: str, default: Any = None) -> Any:
    """*value* as an ``int`` or ``float``. Text that is no number is not a crash."""
    if value in (None, ""):
        return default
    try:
        return kind(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{subject} must be a number, got {value!r}",
                          subject=subject) from None


# ------------------------------------------------------------ installation folders


def candidates(key: str, folder: str | os.PathLike) -> list[Path]:
    """The files in *folder* that a profile can name for *key*.

    The environment scripts of a simulation code, or the launcher of a postprocessor.
    The folder itself is searched first and then one level below it, so that the
    folder an installation was put in works as well as the folder of the program.
    A TELEMAC installation ships a template beside its environment scripts, which is
    left out.
    """
    folder = Path(folder).expanduser()
    patterns = LAYOUT.get(key, ())
    for prefix in ("", *NESTED):
        found: list[Path] = []
        for pattern in patterns:
            try:
                found = sorted(path for path in folder.glob(f"{prefix}/{pattern}"
                                                           if prefix else pattern)
                               if path.is_file() and "template" not in path.name)
            except OSError:
                found = []
            if found:
                return found
    return []


def locate(key: str, folder: str | os.PathLike) -> Path | None:
    """The one file in *folder* that a profile names for *key*, or ``None``.

    ``None`` also where the folder holds several: which environment script of a
    TELEMAC installation is meant is a decision, and aXqua does not take it.
    """
    found = candidates(key, folder)
    return found[0] if len(found) == 1 else None


def installation_of(key: str, path: str | os.PathLike) -> Path | None:
    """The folder *key* is installed in, told from its script or launcher.

    ``None`` for a file that does not lie where an installation keeps it, and for a
    program of the operating system (``/usr/bin/paraview``): "installed in /usr" says
    nothing. A launcher that is a link (``~/bin/visit``) is followed to the
    installation it starts. Only the file is followed, not the folders above it, so
    that a folder keeps the name its owner knows it by.
    """
    import fnmatch

    given = Path(path).expanduser()
    followed = given
    for _ in range(16):                          # a chain of links, never a loop
        try:
            if not followed.is_symlink():
                break
            target = Path(os.readlink(followed))
        except OSError:
            break
        followed = Path(os.path.normpath(target if target.is_absolute()
                                         else followed.parent / target))
    for candidate in dict.fromkeys((followed, given)):
        for pattern in LAYOUT.get(key, ()):
            parts = pattern.split("/")
            tail = candidate.parts[-len(parts):]
            if len(candidate.parts) <= len(parts) or not all(
                    fnmatch.fnmatch(have, want) for have, want in zip(tail, parts)):
                continue
            folder = Path(*candidate.parts[:-len(parts)])
            if str(folder) not in SYSTEM_PREFIXES:
                return folder
    return None


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
    #: Entries this version does not know. Kept and written back.
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, solver: str, data: dict[str, Any] | None, *,
                  base: Path | None = None) -> "SolverBinding":
        data = _mapping(data, f"solvers.{solver}")
        known = set(cls.keys())
        environment = data.get("environment")
        if environment is not None and environment not in ENVIRONMENTS:
            raise ConfigError(
                f"solvers.{solver}.environment must be one of "
                + ", ".join(ENVIRONMENTS) + f", got {environment!r}",
                subject=f"solvers.{solver}.environment")
        return cls(
            setup_script=_path(data.get("setup_script"), base,
                               verbatim=environment == "wsl"),
            environment=environment,
            shell=data.get("shell") or None,
            distro=data.get("distro") or None,
            config_name=data.get("config_name") or None,
            mpi_launcher=data.get("mpi_launcher") or None,
            mpi_processes=_number(data.get("mpi_processes"), int,
                                  f"solvers.{solver}.mpi_processes"),
            overrides={str(k): str(v) for k, v in _mapping(
                data.get("overrides"), f"solvers.{solver}.overrides").items()},
            extra=_unknown(data, known),
        )

    @classmethod
    def keys(cls) -> tuple[str, ...]:
        """The keys a binding has in the file."""
        return tuple(f.name for f in fields(cls) if f.name != "extra")

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
        for key, value in self.extra.items():
            out.setdefault(key, value)
        return out


@dataclass
class AxquaProfile:
    """Everything aXqua needs to know about one computer."""

    name: str = ""
    path: Path | None = None            # where it was read from; never written out
    python: Path | None = None          # the interpreter that runs aXqua
    axqua: Path | None = None           # the program the plugin calls
    #: program -> the folder it is installed in on this computer.
    installations: dict[str, Path] = field(default_factory=dict)
    solvers: dict[str, SolverBinding] = field(default_factory=dict)
    postprocessors: dict[str, Path] = field(default_factory=dict)
    job_root: Path | None = None
    launcher: str = "auto"
    min_depth: float = 0.01             # [m] shallower water is drawn transparent
    velocity_cap: float = 5.0           # [m/s] upper end of the velocity color scale
    schema_version: int = SCHEMA_VERSION
    #: Entries this version does not know, by the block they stand in ("" is the top
    #: level). Kept and written back, so that a profile a newer aXqua wrote loses
    #: nothing when this one saves it.
    extra: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Set by :func:`save`: where a file that could not be read was kept. Never written.
    backup: Path | None = None

    # -- reading -----------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, *, path: Path | None = None
                  ) -> "AxquaProfile":
        data = _mapping(data, "")
        version = _number(data.get("schema_version"), int, "schema_version",
                          SCHEMA_VERSION)
        if version > SCHEMA_VERSION:
            raise ConfigError(
                f"this profile was written by a newer aXqua (schema {version}; this "
                f"version understands {SCHEMA_VERSION})",
                subject="schema_version", remedy="Update aXqua.")
        base = Path(path).parent if path else None

        read = {block: _mapping(data.get(block), block) for block in BLOCKS}
        extra = {"": _unknown(data, set(TOP_LEVEL))}
        extra.update({block: _unknown(read[block], set(known))
                      for block, known in BLOCKS.items()})
        python, solvers, post = read["python"], read["solvers"], read["postprocessors"]
        jobs, display = read["jobs"], read["display"]

        bindings = {name: SolverBinding.from_dict(name, solvers[name], base=base)
                    for name in SOLVERS if name in solvers}
        in_wsl = {name for name, binding in bindings.items()
                  if binding.environment == "wsl"}
        return cls(
            name=str(data.get("name") or (Path(path).stem if path else "")),
            path=Path(path) if path else None,
            python=_path(python.get("executable"), base),
            axqua=_path(python.get("axqua"), base),
            installations={name: found for name in PROGRAMS
                           if (found := _path(read["installations"].get(name), base,
                                              verbatim=name in in_wsl)) is not None},
            solvers=bindings,
            postprocessors={name: found for name in POSTPROCESSORS
                            if (found := _path(post.get(name), base)) is not None},
            job_root=_path(jobs.get("root"), base),
            launcher=str(jobs.get("launcher") or "auto"),
            min_depth=_number(display.get("min_depth"), float, "display.min_depth", 0.01),
            velocity_cap=_number(display.get("velocity_cap"), float,
                                 "display.velocity_cap", 5.0),
            schema_version=version,
            extra={block: found for block, found in extra.items() if found},
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
        if self.installations:
            out["installations"] = {name: str(folder)
                                    for name, folder in self.installations.items()}
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
        # What this version does not know goes back where it was read, after the
        # entries it does know and never in place of one.
        for block, entries in self.extra.items():
            target = out.setdefault(block, {}) if block else out
            for key, value in entries.items():
                target.setdefault(key, value)
        return out

    # -- use ---------------------------------------------------------------------
    def script(self, key: str) -> Path | None:
        """The setup script of a solver, or the launcher of a postprocessor.

        The entry of the profile where there is one. Otherwise the file that the
        installation folder of *key* holds, where it holds exactly one.
        """
        named = self.named(key)
        if named is not None:
            return named
        folder = self.installations.get(key)
        return locate(key, folder) if folder is not None else None

    def named(self, key: str) -> Path | None:
        """The script or launcher the profile names itself for *key*, or ``None``."""
        binding = self.solvers.get(key)
        return binding.setup_script if binding is not None \
            else self.postprocessors.get(key)

    def programs(self) -> dict[str, str]:
        """``program -> the script or launcher aXqua runs``, where there is one."""
        return {key: str(found) for key in PROGRAMS
                if (found := self.script(key)) is not None}

    def unknown_entries(self) -> list[str]:
        """The dotted names of the entries this version does not know."""
        names = [f"{block}.{key}" if block else key
                 for block, entries in self.extra.items() for key in entries]
        names += [f"solvers.{solver}.{key}" for solver, binding in self.solvers.items()
                  for key in binding.extra]
        return names

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


def file_path(path: str | os.PathLike) -> Path:
    """The file that a profile named *path* is kept in, whether or not it exists yet.

    A file that exists is taken as it is named, so that a profile is written back to
    the file it was read from. A name without a file gets the suffix. Reading, writing
    and the question "is there one already?" all ask here, so that they cannot mean
    two different files.
    """
    target = Path(path).expanduser()
    if target.suffix == SUFFIX or target.is_file():
        return target
    return target.with_name(target.name + SUFFIX)


def active_path(explicit: str | os.PathLike | None = None) -> Path | None:
    """The profile in force, or ``None`` when this computer has none."""
    if explicit:
        return file_path(explicit)
    named = os.environ.get(ENV_VAR)
    if named:
        return file_path(named)
    default = default_path()
    return default if default.is_file() else None


# ------------------------------------------------------------------------ file I/O


def load(path: str | os.PathLike) -> AxquaProfile:
    """Read a profile. Raises :class:`ConfigError` for anything that is not one."""
    import yaml

    target = file_path(path)
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
    unknown = profile.unknown_entries()
    if unknown and (target, stamp) not in _WARNED:
        _WARNED.add((target, stamp))
        log.warning("the profile %s has entries this version of aXqua does not know "
                    "(%s); they are kept and not used", target, ", ".join(unknown))
    _CACHE[target] = (stamp, profile)
    return profile


def save(profile: AxquaProfile, path: str | os.PathLike | None = None) -> Path:
    """Write the profile, atomically, and return where it went.

    A file at that place which this version cannot read is not lost: it is moved to
    ``<name>.<date>-<time>.bak`` beside it first, and ``profile.backup`` says where. It
    may be the profile of a newer aXqua, or one with a typing error that its owner
    would rather correct than retype.
    """
    import time

    import yaml

    target = file_path(path or profile.path or default_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(profile.as_dict(), sort_keys=False, default_flow_style=False,
                          allow_unicode=True)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    profile.backup = None
    if target.is_file():
        try:
            load(target)
        except ConfigError as exc:
            kept = target.with_name(f"{target.name}.{time.strftime('%Y%m%d-%H%M%S')}.bak")
            number = 1
            while kept.exists():                # two saves within one second
                number += 1
                kept = kept.with_name(f"{kept.stem}-{number}.bak")
            os.replace(target, kept)
            profile.backup = kept
            log.warning("%s could not be read (%s) and was kept as %s", target, exc, kept)
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
    for key in PROGRAMS:
        named = profile.script(key)
        folder = installation_of(key, named) if named is not None else None
        if folder is not None:
            profile.installations[key] = folder
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

    for name in profile.unknown_entries():
        add(WARNING, "axqua.config.unknown_entry", name,
            f"this version of aXqua does not know the entry {name}; it is kept as it "
            "is and not used", _unknown_remedy(name))

    findings.extend(_check_installations(profile))
    for solver in SOLVERS:
        findings.extend(_check_binding(profile, solver, probe=probe, timeout=timeout))

    for program in POSTPROCESSORS:
        value = profile.script(program)
        subject = f"postprocessors.{program}"
        if value is None:
            folder = profile.installations.get(program)
            if folder is not None and Path(folder).is_dir():
                add(ERROR, "axqua.environment.program_missing", subject,
                    f"the {program} program was not found in the installation folder "
                    f"{folder}", "Select the program file itself.")
            continue
        target = Path(value)
        if not target.is_file():
            add(ERROR, "axqua.environment.program_missing", subject,
                f"the {program} launcher {target} does not exist")
        elif not os.access(target, os.X_OK):
            add(ERROR, "axqua.environment.program_missing", subject,
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


def _unknown_remedy(name: str) -> str:
    """What to do about an entry that is not known: most often it is a typing error."""
    import difflib

    place, _, key = name.rpartition(".")
    known = TOP_LEVEL if not place else BLOCKS.get(place) or SolverBinding.keys()
    close = difflib.get_close_matches(key, known, n=1)
    return ((f"If {close[0]} is meant, correct the name in the profile file. "
             if close else "")
            + "An entry that a newer aXqua or a newer plugin wrote is used once aXqua "
              "is updated.")


def _in_wsl(profile: AxquaProfile, key: str) -> bool:
    """A path inside a WSL distribution cannot be looked at from Windows."""
    binding = profile.solvers.get(key)
    return binding is not None and binding.environment == "wsl"


def _check_installations(profile: AxquaProfile) -> list[Finding]:
    """The installation folders: each exists, and holds what the profile names."""
    out: list[Finding] = []
    for key, folder in profile.installations.items():
        if _in_wsl(profile, key):
            continue
        subject = f"installations.{key}"
        folder = Path(folder)
        if not folder.is_dir():
            out.append(Finding(
                WARNING, "axqua.environment.installation_missing",
                f"the installation folder of {key} does not exist: {folder}",
                subject=subject,
                remedy="Select the folder again, or clear the entry."))
            continue
        named = profile.named(key)
        if named is None or not Path(named).exists():
            continue
        try:
            inside = Path(named).resolve().is_relative_to(folder.resolve()) \
                or Path(named).is_relative_to(folder)
        except OSError:
            continue
        if not inside:
            what = "environment script" if key in SOLVERS else "program"
            out.append(Finding(
                WARNING, "axqua.environment.installation_mismatch",
                f"the {what} of {key} ({named}) is not in the installation folder "
                f"entered for it ({folder}); aXqua uses the {what}",
                subject=subject,
                remedy=f"Select the folder the {what} belongs to, or clear the folder."))
    return out


def _check_binding(profile: AxquaProfile, solver: str, *, probe: bool, timeout: float
                   ) -> list[Finding]:
    subject = f"solvers.{solver}.setup_script"
    binding = profile.solvers.get(solver) or SolverBinding()
    script = profile.script(solver)
    if script is None:
        folder = profile.installations.get(solver)
        found = candidates(solver, folder) if folder is not None else []
        if len(found) > 1:
            message = (f"the installation folder of {solver} holds several environment "
                       "scripts (" + ", ".join(path.name for path in found) + "), so "
                       "aXqua does not know which one to use")
            remedy = "Select one of them as the environment script."
        elif folder is not None and Path(folder).is_dir():
            message = (f"no environment script of {solver} was found in the "
                       f"installation folder {folder}")
            remedy = "Select the environment script itself."
        else:
            message = (f"no environment script is set for {solver}, so simulations "
                       "with it cannot be started")
            remedy = "Leave it empty if this code is not used on this computer."
        return [Finding(WARNING, "axqua.environment.solver_unbound", message,
                        subject=subject, remedy=remedy)]

    out: list[Finding] = []
    script = Path(script)
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
        out.extend(_probe(solver, binding, script, subject, timeout))
    return out


def _probe(solver: str, binding: SolverBinding, script: Path, subject: str,
           timeout: float) -> list[Finding]:
    """Enter the environment and see whether the code is really there."""
    from axqua.core.environment import SolverEnvironment, default_kind

    overrides = dict(binding.overrides)
    if binding.config_name:
        overrides.setdefault("USETELCFG", binding.config_name)
    environment = SolverEnvironment(
        kind=binding.environment or default_kind(), setup_script=script,
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
