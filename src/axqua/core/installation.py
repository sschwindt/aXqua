"""What an installation of TELEMAC or OpenFOAM can do for aXqua.

A profile names the environment script of a simulation code. Entering that script and finding the variables of the code (:meth:`SolverEnvironment.validate`) tells a working installation from a file that merely exists, but not whether the installation has the parts aXqua needs: a TELEMAC built without GAIA runs every hydraulic simulation and fails on the first morphodynamic one, and an OpenFOAM without Olsen's ``sediDriftFoam2`` runs the free-surface simulations and not the fine sediment. Both failures used to come at run time, far from the cause.

:func:`inspect` enters the script once and then looks, in the environment it produced, for each part aXqua uses (:class:`Module`). The parts are of two kinds:

* **required**: without them aXqua cannot run the code at all (TELEMAC-2D, the solvers of OpenFOAM that every simulation uses). A missing one makes the installation invalid, the error ``axqua.environment.solver_incomplete``.
* **optional**: without them one aXqua module does not work and everything else does (GAIA, TELEMAC-3D, parallel runs, user Fortran; sediDriftFoam2, the stage-discharge outflow of the BAW, the release v2406). Each missing one is the warning ``axqua.environment.module_missing``, which names what will not work.

This is what the *Setup Wizard* of the plugin shows for an existing installation, and what *Check* of the profile reports (:func:`axqua.core.profile.check` calls it).

The files are looked for on this computer, with the ``PATH`` and the variables the script set. A script inside a WSL distribution cannot be looked into from Windows, so there the parts are reported as not checked. Nothing here imports a solver backend (the rule of :mod:`axqua.core`): the names of the files it looks for are kept here, and a test compares them with the backends that use them.
"""

from __future__ import annotations

import fnmatch
import glob
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from shutil import which as _find
from typing import Any

from axqua.core.diagnostics import ERROR, WARNING, Finding

__all__ = ["BAW_LIBRARY", "FINE_SEDIMENT_SOLVER", "Inspection", "Module", "SCRIPT_PATTERNS",
           "inspect"]

#: The OpenFOAM release aXqua writes dictionaries for (also in :mod:`axqua.core.profile`).
OPENFOAM_RELEASE = "2406"

#: Olsen's solver of the fine sediment (``axqua.solvers.openfoam.sediment.SOLVER``).
FINE_SEDIMENT_SOLVER = "sediDriftFoam2"

#: The library of the stage-discharge outflow of the BAW
#: (``axqua.solvers.openfoam.dicts.BAW_LIBRARY``).
BAW_LIBRARY = "lib_BAW_public_BCs_v2412_20260813.so"

#: What the environment script of each code is called, relative to its installation,
#: for the text of the wizard and for the hint about a file of another name.
SCRIPT_PATTERNS = {
    "telemac": ("configs/pysource.*.sh",),
    "openfoam": ("shell-rc.sh", "etc/bashrc"),
}

#: ``(name, what it is, what does not work without it)`` of the optional parts.
TELEMAC_OPTIONAL = (
    ("telemac3d", "TELEMAC-3D",
     "the 3D simulations (Steady 3D, Unsteady 3D) and the vertical convergence study"),
    ("gaia", "GAIA",
     "the morphodynamics (sediment transport with GAIA), also in the hydrology mode"),
    ("parallel", "parallel runs (partel and an MPI launcher)",
     "simulations on more than one processor core"),
    ("fortran", "the Fortran compiler of the build",
     "user Fortran: the Green-Ampt infiltration of the hydrology mode and the "
     "gain-lose exchange"),
)
OPENFOAM_OPTIONAL = (
    ("release", f"OpenFOAM v{OPENFOAM_RELEASE}",
     f"reading the input files aXqua writes for v{OPENFOAM_RELEASE} (another release "
     "may stop at a file it does not understand)"),
    ("sediment", f"Olsen's {FINE_SEDIMENT_SOLVER}",
     "the fine sediment simulation (Morphodynamics with OpenFOAM)"),
    ("rating", "the stage-discharge outflow boundary of the BAW",
     "an OpenFOAM outflow on a stage-discharge (rating) curve"),
)

#: The programs of OpenFOAM that every aXqua simulation uses.
OPENFOAM_REQUIRED = ("interFoam", "checkMesh", "decomposePar", "reconstructPar")

#: Each module code of the TELEMAC build, by the names its files carry.
_TELEMAC_FILES = {
    "telemac2d": ("bin/telemac2d*", "lib/libtelemac2d.*", "lib/telemac2d/*"),
    "telemac3d": ("bin/telemac3d*", "lib/libtelemac3d.*", "lib/telemac3d/*"),
    "gaia": ("lib/libgaia.*", "lib/gaia/*"),
}


@dataclass
class Module:
    """One part of an installation that aXqua uses."""

    name: str
    title: str
    available: bool | None          # None: not checked (WSL, or no build folder)
    required: bool = False
    needed_for: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "available": self.available,
                "required": self.required, "needed_for": self.needed_for,
                "detail": self.detail}


@dataclass
class Inspection:
    """What :func:`inspect` found about one installation."""

    solver: str
    script: str
    valid: bool
    detail: str = ""
    version: str = ""
    folder: str = ""
    modules: list[Module] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def missing(self) -> list[Module]:
        """The optional parts that are not there."""
        return [m for m in self.modules if not m.required and m.available is False]

    def verdict(self) -> str:
        """One sentence for a person."""
        name = {"telemac": "TELEMAC", "openfoam": "OpenFOAM"}.get(self.solver, self.solver)
        if not self.valid:
            return (f"This is not a usable installation of {name}: " + self.detail
                    if self.detail else f"This is not a usable installation of {name}.")
        missing = self.missing
        if not missing:
            return f"{name} was found and the installation is valid and complete."
        return (f"{name} was found and the installation is valid, but some aXqua "
                "modules will not work:\n"
                + "\n".join(f"- {_capital(m.needed_for)} ({m.title} not found)"
                            for m in missing))

    def as_dict(self) -> dict[str, Any]:
        return {"solver": self.solver, "script": self.script, "valid": self.valid,
                "detail": self.detail, "version": self.version, "folder": self.folder,
                "verdict": self.verdict(),
                "modules": [m.as_dict() for m in self.modules],
                "findings": [f.as_dict() for f in self.findings]}


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def inspect(solver: str, script: str | os.PathLike, *, binding=None,
            subject: str | None = None, timeout: float = 120.0) -> Inspection:
    """Enter the environment script of *solver* and report what the installation has.

    *binding* is the :class:`~axqua.core.profile.SolverBinding` of a profile (how to enter the script: kind, shell, distribution, overrides); without one the script is entered as on this computer. Never raises.
    """
    from axqua.core.environment import SolverEnvironment, default_kind
    from axqua.core.profile import installation_of

    subject = subject or f"solvers.{solver}.setup_script"
    script = Path(script).expanduser()
    out = Inspection(solver=solver, script=str(script), valid=False)
    folder = installation_of(solver, script)
    out.folder = str(folder or "")

    def add(severity: str, code: str, message: str, remedy: str = "") -> None:
        out.findings.append(Finding(severity, code, message, subject=subject,
                                    remedy=remedy))

    kind = getattr(binding, "environment", None) or default_kind()
    in_wsl = str(kind) == "wsl"
    if solver not in SCRIPT_PATTERNS:
        out.detail = f"aXqua knows no simulation code called {solver}"
        add(ERROR, "axqua.environment.solver_unreachable", out.detail)
        return out
    if not in_wsl and not script.is_file():
        out.detail = f"the file {script} does not exist"
        add(ERROR, "axqua.environment.script_missing",
            f"the environment script of {solver} does not exist: {script}",
            "Select the script of the installation on this computer.")
        return out
    if not _named_like(solver, script):
        add(WARNING, "axqua.environment.unusual_script",
            f"{script.name} is not named like an environment script of {solver} ("
            + " or ".join(SCRIPT_PATTERNS[solver]) + ")",
            "Check that this is the script that sets up the installation.")

    overrides = dict(getattr(binding, "overrides", None) or {})
    if getattr(binding, "config_name", None):
        overrides.setdefault("USETELCFG", binding.config_name)
    environment = SolverEnvironment(
        kind=kind, setup_script=script, shell=getattr(binding, "shell", None),
        distro=getattr(binding, "distro", None),
        mpi_launcher=getattr(binding, "mpi_launcher", None), overrides=overrides)
    status = environment.validate(solver, timeout=timeout)
    if not status.ok:
        out.detail = status.detail
        add(ERROR, "axqua.environment.solver_unreachable",
            f"{solver} cannot be reached: {status.detail}",
            "Check that this is the environment script of a working installation.")
        return out
    if status.ambient:
        add(WARNING, "axqua.environment.ambient",
            f"the variables of {solver} were already set before its environment script "
            "ran, so the script may not be what provides them",
            "A job does not inherit the settings of a terminal. Make sure the script "
            "itself sets up the installation.")

    variables = dict(status.variables)
    if solver == "telemac":
        out.version = _telemac_version(variables)
        out.modules = _telemac_modules(variables, checked=not in_wsl)
    else:
        out.version = str(variables.get("WM_PROJECT_VERSION", ""))
        out.modules = _openfoam_modules(variables, checked=not in_wsl)

    lacking = [m for m in out.modules if m.required and m.available is False]
    out.valid = not lacking
    if lacking:
        out.detail = ("the environment was entered, but "
                      + ", ".join(m.title for m in lacking) + " "
                      + ("is" if len(lacking) == 1 else "are") + " missing")
        add(ERROR, "axqua.environment.solver_incomplete",
            f"{solver}: {out.detail}",
            "Select the environment script of a complete build, or install the "
            "program again with the Setup Wizard.")
    else:
        out.detail = status.detail
    for module in out.modules:
        if module.required or module.available is not False:
            continue
        if module.name == "release":
            add(WARNING, "axqua.environment.openfoam_version",
                f"this is OpenFOAM {out.version or 'of an unknown version'}; aXqua writes "
                f"input files for v{OPENFOAM_RELEASE}",
                f"Bind the environment script of OpenFOAM v{OPENFOAM_RELEASE}.")
            continue
        add(WARNING, "axqua.environment.module_missing",
            f"{solver} has no {module.title}, so {module.needed_for} will not work",
            _remedy(solver, module.name))
    return out


def _remedy(solver: str, name: str) -> str:
    if solver == "openfoam" and name in ("sediment", "rating"):
        return ("The OpenFOAM installation of the Setup Wizard compiles it; its "
                "shell-rc.sh sets it up.")
    if name == "parallel":
        return "Install an MPI library (OpenMPI) and build TELEMAC with it."
    return f"Build {solver} with this module, or leave it if the module is not used."


def _named_like(solver: str, script: Path) -> bool:
    text = script.as_posix()
    return any(fnmatch.fnmatch(text, "*/" + pattern) or fnmatch.fnmatch(script.name, pattern)
               for pattern in SCRIPT_PATTERNS[solver])


def _which(name: str, variables: dict[str, str]) -> str | None:
    # on the PATH the script set, not on the one of this process
    return _find(name, path=variables.get("PATH", ""))


# --------------------------------------------------------------------------- TELEMAC


def telemac_build(variables: dict[str, str]) -> Path | None:
    """The build folder of a TELEMAC environment: ``BUILD_DIR`` of v9 (CMake), the configuration ``USETELCFG`` below ``$HOMETEL/builds``, or the one build there is."""
    build = variables.get("BUILD_DIR")
    if build and Path(build).is_dir():
        return Path(build)
    home = variables.get("HOMETEL")
    if not home:
        return None
    builds = Path(home) / "builds"
    config = variables.get("USETELCFG")
    if config and (builds / config).is_dir():
        return builds / config
    found = sorted(p for p in builds.glob("*") if p.is_dir()) if builds.is_dir() else []
    return found[0] if len(found) == 1 else None


def _has(build: Path, patterns) -> bool:
    return any(glob.glob(str(build / pattern)) for pattern in patterns)


def _telemac_version(variables: dict[str, str]) -> str:
    home = variables.get("HOMETEL")
    if not home:
        return ""
    for name in ("VERSION", "version.txt"):
        path = Path(home) / name
        if path.is_file():
            return path.read_text(errors="replace").strip().splitlines()[0][:40]
    return ""


def _fortran_compiler(build: Path, variables: dict[str, str]) -> tuple[bool | None, str]:
    """The compiler TELEMAC compiles user Fortran with, and whether it is there.

    TELEMAC v9 reads it from ``build_commands.json`` of the build, the older versions from ``cmd_obj`` of the configuration file ``SYSTELCFG``.
    """
    command = ""
    commands = build / "build_commands.json"
    if commands.is_file():
        try:
            data = json.loads(commands.read_text())
            command = str(data["telemac2d"]["library"]["compile"])
        except (ValueError, KeyError, TypeError):
            command = ""
    if not command:
        systel = variables.get("SYSTELCFG")
        if systel and Path(systel).is_file():
            for line in Path(systel).read_text(errors="replace").splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "cmd_obj" and value.strip():
                    command = value.strip()
                    break
    if not command:
        return None, "the build names no compiler"
    compiler = command.split()[0]
    found = _which(compiler, variables) or (compiler if Path(compiler).is_file() else None)
    return (found is not None), compiler


def _telemac_modules(variables: dict[str, str], *, checked: bool) -> list[Module]:
    launcher = _which("telemac2d.py", variables) if checked else None
    build = telemac_build(variables) if checked else None
    core = Module("telemac2d", "TELEMAC-2D", None, required=True,
                  needed_for="every TELEMAC simulation")
    modules = [core]
    if not checked:
        core.detail = "not checked: the installation is inside WSL"
        modules += [Module(name, title, None, needed_for=what,
                           detail="not checked: the installation is inside WSL")
                    for name, title, what in TELEMAC_OPTIONAL]
        return modules
    if launcher is None:
        core.available = False
        core.detail = "telemac2d.py is not on the PATH the script sets"
    elif build is None:
        # A launcher without a build folder aXqua can find: the launcher decides.
        core.available = True
        core.detail = f"{launcher}; no build folder found below $HOMETEL/builds"
    else:
        core.available = _has(build, _TELEMAC_FILES["telemac2d"])
        core.detail = str(build) if core.available else f"not built in {build}"
    for name, title, what in TELEMAC_OPTIONAL:
        module = Module(name, title, None, needed_for=what)
        if name in _TELEMAC_FILES:
            if build is None:
                module.detail = "not checked: no build folder found"
            else:
                module.available = _has(build, _TELEMAC_FILES[name])
                if name == "telemac3d" and module.available:
                    module.available = _which("telemac3d.py", variables) is not None
                module.detail = str(build)
        elif name == "parallel":
            partel = ((build is not None and _has(build, ("bin/partel*",)))
                      or _which("partel", variables) is not None)
            mpi = next((m for m in (variables.get("MPIRUN", ""), "mpiexec", "mpirun")
                        if m and _which(m.split()[0], variables)), None)
            module.available = bool(partel and mpi)
            module.detail = ("partel and " + mpi if module.available else
                             "no MPI launcher on the PATH" if partel else "no partel")
        elif name == "fortran":
            if build is None:
                module.detail = "not checked: no build folder found"
            else:
                module.available, module.detail = _fortran_compiler(build, variables)
        modules.append(module)
    return modules


# -------------------------------------------------------------------------- OpenFOAM


def _openfoam_modules(variables: dict[str, str], *, checked: bool) -> list[Module]:
    if not checked:
        note = "not checked: the installation is inside WSL"
        return ([Module("solvers", "the OpenFOAM solvers", None, required=True,
                        needed_for="every OpenFOAM simulation", detail=note)]
                + [Module(name, title, None, needed_for=what, detail=note)
                   for name, title, what in OPENFOAM_OPTIONAL])
    absent = [name for name in OPENFOAM_REQUIRED if _which(name, variables) is None]
    modules = [Module("solvers", "the OpenFOAM solvers", not absent, required=True,
                      needed_for="every OpenFOAM simulation",
                      detail=("missing: " + ", ".join(absent)) if absent
                      else ", ".join(OPENFOAM_REQUIRED))]
    version = str(variables.get("WM_PROJECT_VERSION", ""))
    for name, title, what in OPENFOAM_OPTIONAL:
        module = Module(name, title, None, needed_for=what)
        if name == "release":
            module.available = OPENFOAM_RELEASE in version
            module.detail = f"OpenFOAM {version or 'of an unknown version'}"
        elif name == "sediment":
            found = _which(FINE_SEDIMENT_SOLVER, variables)
            module.available = found is not None
            module.detail = found or f"no {FINE_SEDIMENT_SOLVER} on the PATH"
        elif name == "rating":
            folders = [variables.get("FOAM_USER_LIBBIN", ""), variables.get("FOAM_LIBBIN", ""),
                       *variables.get("LD_LIBRARY_PATH", "").split(os.pathsep)]
            found = next((str(Path(f) / BAW_LIBRARY) for f in folders
                          if f and (Path(f) / BAW_LIBRARY).is_file()), None)
            module.available = found is not None
            module.detail = found or f"no {BAW_LIBRARY} in the library folders"
        modules.append(module)
    return modules
