"""From the installer scripts to a plan that can be read before anything runs.

aXqua does not install TELEMAC or OpenFOAM by itself. It runs the installer scripts of
the repository named in :data:`REPOSITORY`, at the commit named in :data:`PINNED`, which
is the version this release of aXqua was tested with. Nothing is taken from a moving
branch unless the user asks for it (``--ref main``).

What aXqua adds to the scripts is what a click needs and a terminal does not:

*It asks no password.* The scripts install system packages with ``sudo``, which needs a
terminal. aXqua reads the package list **out of the script** (so the two cannot drift
apart), finds what is missing, and hands the one command to an administrator. The
scripts then run in their own mode without system packages (``--skip-apt``, or simply
without ``--install-system-packages``), as the ordinary user.

*It says beforehand what will happen.* A :class:`Plan` lists the commands, the folder,
the files that will exist afterwards, and everything that is not in order as findings.
The runner executes exactly the commands of the plan.

*It enters the result into the profile.* ``outputs`` maps a key of the profile
(``solvers.telemac.setup_script``) to the file the installation produces.

The scripts run in a cleaned environment: an active conda environment would otherwise
put its own ``cmake``, compilers and MPI in front of the system's, and the build would
mix the two.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axqua.core.diagnostics import ERROR, WARNING, Finding
from axqua.install import host as hosts

__all__ = ["ENV_INSTALLERS", "PINNED", "REPOSITORY", "SYSTEM_PATH", "TARGETS", "TITLES",
           "Options", "Plan", "Step", "admin_command", "clean_environment",
           "default_folder", "shadowed_tools",
           "elevation", "fetch_installers", "find_openfoam", "install_packages",
           "installer_python", "packages_needed", "packages_state", "plan"]

#: Where the installer scripts are published, and the commit this release was tested with.
REPOSITORY = "https://github.com/Ecohydraulics/numerical-software-installers"
PINNED = "12fcb7e8b19d355a63d983ddcbd25d10b3abc398"

#: A local copy of the installer scripts, used instead of downloading them.
ENV_INSTALLERS = "AXQUA_INSTALLERS"

TARGETS = ("telemac", "openfoam", "postprocessors")
TITLES = {
    "telemac": "TELEMAC",
    "openfoam": "OpenFOAM v2406 with the sediment solvers",
    "postprocessors": "ParaView and VisIt",
}

#: What can be downloaded of the example cases of TELEMAC (Options.telemac_examples).
TELEMAC_EXAMPLES = ("inputs", "all", "none")

#: ``base -> (installer script, environment script it writes)``.
TELEMAC_SCRIPTS = {
    "debian12": ("debian12/telemac_debian12_installer.sh", "pysource.debian12.sh"),
    "ubuntu24": ("ubuntu24-mint22/telemac_ubuntu24_installer.sh", "pysource.mint22.sh"),
}
OPENFOAM_DIR = "OpenFOAM-installer"
OPENFOAM_RELEASE = "2406"
PARAVIEW = Path("/usr/bin/paraview")

#: Free space below which a plan warns, in GiB.
DISK_GB = {"telemac": 10, "openfoam": 4, "openfoam-source": 20, "postprocessors": 4}

#: What a person can expect, per target.
ESTIMATES = {
    "telemac": "10 to 30 minutes",
    "telemac-all": "one to two hours; most of it downloads the example cases of TELEMAC",
    "telemac-none": "5 to 20 minutes",
    "openfoam": "a few minutes with an existing OpenFOAM v2406",
    "openfoam-source": "several hours; OpenFOAM v2406 is compiled from its source code",
    "postprocessors": "a few minutes; VisIt is a download of 600 MB",
}

_PACKAGE = re.compile(r"^[a-z0-9][a-z0-9+.\-]*$")

#: Installs VisIt with the installer's own pinned and checksummed download, without
#: the OpenFOAM build around it. Run with ``python -I -c``: nothing of aXqua and nothing
#: of the current folder is on the import path of that process.
VISIT_DRIVER = """\
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from sediment_installer.core import checked_download, clean_environment, run
from sediment_installer.visualization import install_visit

def clean_run(argv, cwd=None, log=None, env=None):
    run(argv, cwd=cwd, log=log, env=clean_environment())

prefix = Path(sys.argv[2]).expanduser().absolute()
launcher = install_visit(prefix, sys.argv[3], checked_download, clean_run)
print("VisIt launcher:", launcher, flush=True)
"""


#: Runs the OpenFOAM installer with the compilers of the operating system. The
#: installer builds with ``/usr/local/bin`` first on its search path; where that folder
#: holds another compiler, every solver fails to link against a packaged OpenFOAM. The
#: start scripts the installer writes use ``/usr/bin:/bin`` already; this gives the
#: build the same. Used only on a computer where the two differ.
OPENFOAM_DRIVER = """\
import sys
sys.path.insert(0, sys.argv[1])
from sediment_installer import core, installer

original = getattr(core, "clean_environment", None)
if original is not None:
    def system_tools_first():
        env = original()
        env["PATH"] = "/usr/bin:/bin"
        return env
    core.clean_environment = system_tools_first
    if hasattr(installer, "clean_environment"):
        installer.clean_environment = system_tools_first
raise SystemExit(installer.main(sys.argv[2:]))
"""


# --------------------------------------------------------------------------- options


@dataclass
class Options:
    """What a user can choose. Everything has a default that works."""

    folder: Path | None = None          # where to install
    tag: str = ""                       # TELEMAC: version tag; empty = the script's own
    salome: Path | None = None          # TELEMAC: a SALOME archive, optional
    #: TELEMAC: how much of its example cases to download. ``inputs``: the files a
    #: run of an example reads; ``all``: reference results and manuals as well;
    #: ``none``: the steering files only, which come with the source code.
    telemac_examples: str = "inputs"
    reuse_openfoam: str = "auto"        # auto | no | the etc/bashrc of an OpenFOAM v2406
    jobs: int | None = None             # processes for compiling
    visualization: bool = True          # OpenFOAM: also ParaView and VisIt
    examples: bool = False
    smoke_test: bool = True
    installers: Path | None = None      # a local copy of the installer scripts
    ref: str = ""                       # another commit or branch of the scripts
    base: str = ""                      # the base of an unrecognized derivative
    bind: bool = True                   # enter the result into the profile

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Options":
        data = dict(data or {})
        known = {f for f in cls.__dataclass_fields__}
        unknown = sorted(set(data) - known)
        if unknown:
            from axqua.core.errors import ConfigError
            raise ConfigError(f"unknown installation option {unknown[0]!r}",
                              subject=unknown[0],
                              remedy="Known options: " + ", ".join(sorted(known)))
        out = cls()
        for key, value in data.items():
            if key in ("folder", "salome", "installers"):
                value = Path(value).expanduser() if value not in (None, "") else None
            elif key == "jobs":
                value = int(value) if value not in (None, "") else None
            elif key in ("visualization", "examples", "smoke_test", "bind"):
                value = bool(value)
            elif key == "telemac_examples":
                if isinstance(value, bool):         # the switch this setting once was
                    value = "inputs" if value else "none"
                value = str(value or "inputs").strip().lower()
                if value not in TELEMAC_EXAMPLES:
                    from axqua.core.errors import ConfigError
                    raise ConfigError(
                        f"telemac_examples must be one of {', '.join(TELEMAC_EXAMPLES)}, "
                        f"got {value!r}", subject="telemac_examples")
            else:
                value = str(value or "")
            setattr(out, key, value)
        if not out.reuse_openfoam:
            out.reuse_openfoam = "auto"
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "folder": str(self.folder) if self.folder else "",
            "tag": self.tag,
            "salome": str(self.salome) if self.salome else "",
            "telemac_examples": self.telemac_examples,
            "reuse_openfoam": self.reuse_openfoam,
            "jobs": self.jobs,
            "visualization": self.visualization,
            "examples": self.examples,
            "smoke_test": self.smoke_test,
            "installers": str(self.installers) if self.installers else "",
            "ref": self.ref,
            "base": self.base,
            "bind": self.bind,
        }


def default_folder(target: str) -> Path:
    """Where *target* is installed when the user names no folder."""
    home = Path.home()
    return {
        "telemac": home / "opt",
        "openfoam": home / ".local" / "openfoam-sediment-v2406",
        "postprocessors": home / ".local" / "axqua-postprocessors",
    }[target]


def default_jobs() -> int:
    """Processes for compiling: at most 8, and one per 2 GiB of memory.

    The rule of the OpenFOAM installer, repeated here so that the plan can show the
    number before the installer runs.
    """
    jobs = min(os.cpu_count() or 1, 8)
    try:
        memory = Path("/proc/meminfo").read_text().split("MemTotal:", 1)[1].split()[0]
        jobs = min(jobs, max(1, int(memory) // (2 * 1024 * 1024)))
    except (OSError, IndexError, ValueError):
        pass
    return jobs


# ----------------------------------------------------------------------- environment


#: The search path of an installation: the programs of the operating system first.
#: Everything the installers build against is a system package, compiled with the
#: system's compilers. A compiler that somebody once built into ``/usr/local/bin``
#: would otherwise come first, and what it compiles cannot be linked with them.
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/usr/local/sbin"

#: Build programs whose copy in ``/usr/local/bin`` changes what an installer builds.
SHADOWING = ("gcc", "g++", "c++", "cc", "cpp", "gfortran", "f95", "ld", "as", "ar",
             "make", "cmake", "flex", "bison", "mpirun", "mpicc", "mpicxx", "mpif90")


def shadowed_tools(local: Path = Path("/usr/local/bin"),
                   system: Path = Path("/usr/bin")) -> list[str]:
    """Build programs of the operating system that ``/usr/local/bin`` overrides."""
    found = []
    for name in SHADOWING:
        ours, theirs = system / name, local / name
        try:
            if ours.exists() and theirs.is_file() and not os.path.samefile(ours, theirs):
                found.append(name)
        except OSError:
            continue
    return found


def clean_environment() -> dict[str, str]:
    """The environment an installer runs in: the system's, and nothing of conda.

    The proxy and certificate variables are kept, because without them a download
    behind a proxy fails; the display variables are kept for the graphical installers
    of the postprocessors.
    """
    keep = {"HOME", "USER", "LOGNAME", "TERM", "LANG", "LC_ALL", "DISPLAY",
            "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
            "XAUTHORITY", "http_proxy", "https_proxy", "no_proxy",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE"}
    env = {key: value for key, value in os.environ.items() if key in keep}
    env["PATH"] = SYSTEM_PATH
    env.setdefault("LANG", "C.UTF-8")
    # The OpenFOAM installer is Python. Without this, running it leaves compiled
    # files beside its sources, which for a local copy of the scripts
    # (AXQUA_INSTALLERS) is somebody's working folder: the first real runs left 17
    # such files in the clone they were given. The commands also carry -B, because
    # an isolated interpreter (-I) does not read this variable.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(argv: list[str], *, timeout: float = 120.0, cwd: Path | None = None
         ) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True,
                          timeout=timeout, env=clean_environment(),
                          cwd=str(cwd) if cwd else None, check=False)


def _which(name: str) -> str | None:
    return shutil.which(name, path=clean_environment()["PATH"])


# ------------------------------------------------------------------ installer scripts


def _is_commit(ref: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{40}", ref))


def fetch_installers(options: Options, *, root: Path | None = None) -> Path:
    """The folder with the installer scripts, downloading them when needed.

    A local copy (``options.installers`` or ``$AXQUA_INSTALLERS``) is used as it is and
    never modified. Otherwise the repository is cloned into aXqua's data folder and set
    to the requested commit. A commit is fetched once; a branch is updated every time.
    """
    from axqua.core.errors import EnvironmentError as AxquaEnvironmentError

    local = options.installers or os.environ.get(ENV_INSTALLERS)
    if local:
        path = Path(local).expanduser().resolve()
        if not path.is_dir():
            raise AxquaEnvironmentError(
                f"the folder of the installer scripts does not exist: {path}",
                subject="installers")
        return path

    from axqua.jobs import paths

    ref = options.ref or PINNED
    root = Path(root) if root else paths.data_dir() / "installers"
    target = root / re.sub(r"[^A-Za-z0-9._-]", "_", ref)[:60]
    marker = target / ".axqua-installers"
    if _is_commit(ref) and marker.is_file() and marker.read_text().strip() == ref:
        return target

    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".fetch-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        if _which("git"):
            _clone(ref, staging)
        else:
            _download_archive(ref, staging)
        (staging / ".axqua-installers").write_text(ref + "\n", encoding="utf-8")
        if target.exists():
            shutil.rmtree(target)                 # aXqua's own copy, replaced whole
        staging.rename(target)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise AxquaEnvironmentError(
            f"the installer scripts could not be downloaded from {REPOSITORY}: {exc}",
            subject="installers",
            remedy="Check the internet connection, or download the repository by hand "
                   f"and name its folder in the environment variable {ENV_INSTALLERS}."
        ) from exc
    return target


def _clone(ref: str, staging: Path) -> None:
    done = _run(["git", "clone", "--quiet", "--no-checkout", REPOSITORY, staging],
                timeout=600)
    if done.returncode:
        raise RuntimeError((done.stderr or done.stdout).strip()[-400:])
    done = _run(["git", "-C", staging, "checkout", "--quiet", "--detach",
                 ref if _is_commit(ref) else f"origin/{ref}"], timeout=120)
    if done.returncode:
        raise RuntimeError((done.stderr or done.stdout).strip()[-400:])
    if _is_commit(ref):
        head = _run(["git", "-C", staging, "rev-parse", "HEAD"]).stdout.strip()
        if head != ref:
            raise RuntimeError(f"the repository is at {head}, not at the requested {ref}")


def _download_archive(ref: str, staging: Path) -> None:
    """Without git: the archive of that commit, unpacked with the paths checked."""
    import tarfile
    import tempfile
    import urllib.request

    url = f"{REPOSITORY}/archive/{ref}.tar.gz"
    with tempfile.TemporaryDirectory(dir=staging.parent) as scratch:
        archive = Path(scratch) / "installers.tar.gz"
        with urllib.request.urlopen(url, timeout=120) as response, \
                open(archive, "wb") as out:               # nosec B310 - https constant
            shutil.copyfileobj(response, out)
        unpacked = Path(scratch) / "unpacked"
        unpacked.mkdir()
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                name = Path(member.name)
                if name.is_absolute() or ".." in name.parts or member.issym() \
                        or member.islnk() or member.isdev():
                    raise RuntimeError(f"unexpected entry in the archive: {member.name}")
            try:
                tar.extractall(unpacked, filter="data")
            except TypeError:                             # a Python before the filters
                tar.extractall(unpacked)                  # nosec B202 - members checked
        tops = [p for p in unpacked.iterdir() if p.is_dir()]
        if len(tops) != 1:
            raise RuntimeError("the archive does not hold exactly one folder")
        tops[0].rename(staging)


def installer_python(checkout: Path) -> str | None:
    """An interpreter that can run the OpenFOAM installer, or ``None``.

    The installer is tried with the interpreter that runs aXqua first and then with the
    newer ones on the search path. Trying is the only reliable test: a version of the
    installer uses a form of formatted string that Python accepts from 3.12 on, and a
    later version may not.
    """
    folder = Path(checkout) / OPENFOAM_DIR
    files = sorted((folder / "sediment_installer").glob("*.py")) + [folder / "install.py"]
    files = [str(f) for f in files if f.is_file()]
    if not files:
        return None
    probe = ("import sys\n"
             "for name in sys.argv[1:]:\n"
             "    compile(open(name, encoding='utf-8').read(), name, 'exec')\n")
    seen: list[str] = []
    for candidate in (sys.executable, _which("python3.14"), _which("python3.13"),
                      _which("python3.12"), _which("python3")):
        if not candidate or candidate in seen:
            continue
        seen.append(candidate)
        try:
            if _run([candidate, "-I", "-c", probe, *files], timeout=60).returncode == 0:
                return candidate
        except (OSError, subprocess.SubprocessError):
            continue
    return None


# -------------------------------------------------------------------------- packages


def _script_packages(text: str, function: str) -> list[str]:
    """The packages a shell function installs with ``apt-get install``."""
    body = re.search(re.escape(function) + r"\(\)\s*\{(.*?)\n\}", text, re.S)
    if not body:
        return []
    names: list[str] = []
    for block in re.finditer(r"apt-get install((?:[^\n]*\\\n)*[^\n]*)", body.group(1)):
        for word in block.group(1).replace("\\\n", " ").split():
            if word in ("||", "&&", ";"):
                break
            if _PACKAGE.match(word) and word not in names:
                names.append(word)
    return names


def _python_list(text: str, name: str) -> list[str]:
    """A list of strings assigned to *name* in Python source, read without running it."""
    found = re.search(r"^" + re.escape(name) + r"\s*=\s*(\[.*?\])", text, re.S | re.M)
    if not found:
        return []
    try:
        value = ast.literal_eval(found.group(1))
    except (ValueError, SyntaxError):
        return []
    return [str(v) for v in value if _PACKAGE.match(str(v))]


def packages_needed(target: str, checkout: Path, host: hosts.Host,
                    options: Options) -> list[str]:
    """The system packages *target* needs, as its installer script names them."""
    checkout = Path(checkout)
    if target == "telemac":
        script = TELEMAC_SCRIPTS.get(host.base)
        if not script or not (checkout / script[0]).is_file():
            return []
        text = (checkout / script[0]).read_text(encoding="utf-8", errors="replace")
        names = _script_packages(text, "apt_install_deps_telemac")
        if options.salome:
            for name in _script_packages(text, "apt_install_deps_salome"):
                if name not in names:
                    names.append(name)
        return names
    source = checkout / OPENFOAM_DIR / "sediment_installer" / "installer.py"
    if not source.is_file():
        return []
    text = source.read_text(encoding="utf-8", errors="replace")
    gui = _python_list(text, "GUI_PACKAGES")
    if target == "postprocessors":
        return gui
    return _python_list(text, "BUILD_PACKAGES") + (gui if options.visualization else [])


def packages_state(names: list[str]) -> tuple[list[str], list[str]]:
    """``(missing, unavailable)`` of *names* on this system.

    ``dpkg-query`` says what is installed. What it does not know is then put to
    ``apt-get --simulate``, which needs no privileges and resolves what the first
    question cannot: a name that another installed package provides is not missing, and
    a name the package sources do not offer cannot be installed at all.
    """
    names = [n for n in names if _PACKAGE.match(n)]
    if not names or not _which("dpkg-query"):
        return [], []
    done = _run(["dpkg-query", "-W", "-f=${Package} ${db:Status-Abbrev}\\n", *names])
    installed = {line.split()[0].split(":")[0] for line in done.stdout.splitlines()
                 if len(line.split()) >= 2 and line.split()[1].startswith("ii")}
    open_names = [n for n in names if n not in installed]
    if not open_names or not _which("apt-get"):
        return open_names, []

    missing, unavailable, remaining = list(open_names), [], list(open_names)
    for _ in range(3):
        try:
            trial = _run(["apt-get", "--simulate", "--no-install-recommends", "install",
                          *remaining], timeout=120)
        except (OSError, subprocess.SubprocessError):
            break                                     # cannot tell: all stay missing
        text = trial.stdout + "\n" + trial.stderr
        if trial.returncode == 0:
            would = {name.split(":")[0] for name in re.findall(r"^Inst (\S+)", text, re.M)}
            # "Note, selecting 'libtiff-dev' instead of 'libtiff5-dev'"
            instead = {old: new for new, old in
                       re.findall(r"selecting '([^']+)' instead of '([^']+)'", text)}
            missing = [n for n in remaining if instead.get(n, n).split(":")[0] in would]
            break
        gone = [n for n in re.findall(
            r"(?:Unable to locate package |Package '?)([a-z0-9][a-z0-9+.\-]*)", text)
            if n in remaining]
        if not gone:
            missing = list(remaining)
            break
        unavailable += [n for n in gone if n not in unavailable]
        remaining = [n for n in remaining if n not in gone]
        missing = list(remaining)
        if not remaining:
            break
    return missing, unavailable


#: The programs the OpenFOAM installer looks for before it builds anything.
BUILD_TOOLS = ("git", "gcc", "g++", "make", "flex", "bison", "cmake", "mpirun")


def _tools_missing(checkout: Path) -> list[str]:
    """The build programs the OpenFOAM installer requires and this computer lacks."""
    tools = list(BUILD_TOOLS)
    source = Path(checkout) / OPENFOAM_DIR / "sediment_installer" / "installer.py"
    try:
        found = re.search(r"for tool in (\([^)]*\)):",
                          source.read_text(encoding="utf-8", errors="replace"))
        if found:
            tools = [str(v) for v in ast.literal_eval(found.group(1))]
    except (OSError, ValueError, SyntaxError):
        pass
    return [tool for tool in tools if _which(tool) is None]


def admin_command(target: str, missing: list[str]) -> str:
    """The one command an administrator runs to install *missing*."""
    if not missing:
        return ""
    recommends = "" if target == "telemac" else " --no-install-recommends"
    return ("sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install "
            f"-y{recommends} " + " ".join(missing))


def elevation() -> str:
    """How this session can ask for administrator rights: ``pkexec`` or ``""``.

    ``pkexec`` shows the password dialog of the desktop, so aXqua never sees a
    password. It needs a graphical session; in a plain terminal session the command is
    shown instead and the administrator runs it.
    """
    graphical = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    return "pkexec" if graphical and _which("pkexec") else ""


def install_packages(target: str, missing: list[str], log_dir: Path) -> dict[str, Any]:
    """Install *missing* through the password dialog of the desktop. Never raises.

    The command is the one :func:`admin_command` shows, run by ``pkexec`` instead of
    ``sudo``. The names were checked against the pattern of a package name before, so
    nothing but package names reaches the shell that joins the two ``apt-get`` calls.
    """
    import time

    names = [n for n in missing if _PACKAGE.match(n)]
    command = admin_command(target, names)
    if not names:
        return {"returncode": 0, "log": "", "message": "nothing to install"}
    if elevation() != "pkexec":
        return {"returncode": 127, "log": "",
                "message": "this session cannot ask for administrator rights; an "
                           "administrator runs: " + command}
    recommends = "" if target == "telemac" else " --no-install-recommends"
    script = ("apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y"
              + recommends + " " + " ".join(names))
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"packages-{time.strftime('%Y%m%d-%H%M%S')}.log"
    with open(log_path, "ab") as out:
        try:
            code = subprocess.call(["pkexec", "/bin/sh", "-c", script],
                                   stdin=subprocess.DEVNULL, stdout=out,
                                   stderr=subprocess.STDOUT, env=clean_environment())
        except OSError as exc:
            return {"returncode": 127, "log": str(log_path),
                    "message": f"pkexec could not be started: {exc}"}
    if code == 0:
        message = "the packages were installed"
    elif code == 126:
        message = "the password dialog was closed; nothing was installed"
    elif code == 127:
        message = ("administrator rights were not granted; an administrator runs: "
                   + command)
    else:
        message = f"apt-get ended with an error (exit code {code}); see {log_path}"
    return {"returncode": code, "log": str(log_path), "message": message}


# -------------------------------------------------------------------------- OpenFOAM


def _api(bashrc: Path) -> str:
    """The release an ``etc/bashrc`` belongs to, from the installation's own record."""
    info = bashrc.parent.parent / "META-INFO" / "api-info"
    try:
        for line in info.read_text(encoding="utf-8").splitlines():
            if line.startswith("api="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def find_openfoam() -> Path | None:
    """The ``etc/bashrc`` of an OpenFOAM v2406 that is already on this computer."""
    candidates: list[Path] = []
    try:
        from axqua.core import machine
        known = machine.resolve("openfoam")
        if known:
            candidates.append(Path(known.path))
    except Exception:                                   # noqa: BLE001 - only a hint
        pass
    home = Path.home()
    candidates += [
        Path(f"/usr/lib/openfoam/openfoam{OPENFOAM_RELEASE}/etc/bashrc"),
        Path(f"/opt/openfoam{OPENFOAM_RELEASE}/etc/bashrc"),
        Path(f"/opt/OpenFOAM/OpenFOAM-v{OPENFOAM_RELEASE}/etc/bashrc"),
        home / "OpenFOAM" / f"OpenFOAM-v{OPENFOAM_RELEASE}" / "etc" / "bashrc",
    ]
    for candidate in candidates:
        if candidate.name == "bashrc" and candidate.is_file() \
                and _api(candidate) == OPENFOAM_RELEASE:
            return candidate
    return None


# ------------------------------------------------------------------------------ plan


@dataclass
class Step:
    """One command of an installation. *env* adds to the cleaned environment."""

    name: str
    argv: list[str]
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    #: An optional command may fail without failing the installation: what it adds is
    #: reported as missing, and the program itself is installed.
    optional: bool = False

    def as_dict(self) -> dict[str, Any]:
        settings = " ".join(f"{key}={_quote(value)}" for key, value in self.env.items())
        return {"name": self.name, "argv": list(self.argv), "cwd": self.cwd,
                "env": dict(self.env), "optional": self.optional,
                "command": (settings + " " if settings else "")
                + " ".join(_quote(a) for a in self.argv)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Step":
        return cls(name=str(data.get("name", "")),
                   argv=[str(a) for a in data.get("argv") or []],
                   cwd=str(data.get("cwd") or ""),
                   env={str(k): str(v) for k, v in (data.get("env") or {}).items()},
                   optional=bool(data.get("optional")))


def _quote(text: str) -> str:
    import shlex
    return shlex.quote(text) if "\n" not in text else "'<a short Python program>'"


@dataclass
class Plan:
    """What an installation would do. Nothing in it has happened yet."""

    target: str
    host: hosts.Host
    options: Options
    folder: Path
    installers: Path | None = None
    steps: list[Step] = field(default_factory=list)
    needed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    package_note: str = ""              # why missing packages may not matter here
    outputs: dict[str, str] = field(default_factory=dict)
    expects: list[str] = field(default_factory=list)
    estimate: str = ""
    notes: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Whether the installation can be started at all."""
        return bool(self.steps) and not any(f.severity == ERROR for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "title": TITLES[self.target],
            "host": self.host.as_dict(),
            "options": self.options.as_dict(),
            "folder": str(self.folder),
            "installers": {"path": str(self.installers) if self.installers else "",
                           "repository": REPOSITORY,
                           "ref": self.options.ref or PINNED},
            "steps": [step.as_dict() for step in self.steps],
            "packages": {"needed": self.needed, "missing": self.missing,
                         "unavailable": self.unavailable,
                         "command": admin_command(self.target, self.missing),
                         "elevation": elevation() if self.missing else "",
                         "note": self.package_note},
            "outputs": dict(self.outputs),
            "expects": list(self.expects),
            "estimate": self.estimate,
            "notes": list(self.notes),
            "findings": [f.as_dict() for f in self.findings],
            "ready": self.ready,
        }


def _free_gb(folder: Path) -> float | None:
    existing = next((p for p in (folder, *folder.parents) if p.exists()), None)
    if existing is None:
        return None
    try:
        return shutil.disk_usage(existing).free / 1024 ** 3
    except OSError:
        return None


def plan(target: str, options: Options | None = None, *,
         host: hosts.Host | None = None, check_packages: bool = True) -> Plan:
    """What installing *target* on this computer takes. Never raises for a finding.

    The installer scripts are downloaded for this (2 MB), because the list of system
    packages is read out of them. A plan with an error finding has no steps that could
    be started; a plan with warnings can.
    """
    from axqua.core.errors import AxquaError, ConfigError

    if target not in TARGETS:
        raise ConfigError(f"unknown installation {target!r}", subject="target",
                          remedy="One of: " + ", ".join(TARGETS))
    options = options or Options()
    host = host or hosts.detect(base=options.base or None)
    folder = (options.folder or default_folder(target)).expanduser()
    if not folder.is_absolute():
        folder = Path.cwd() / folder
    out = Plan(target=target, host=host, options=options, folder=folder)
    subject = f"install.{target}"

    def add(severity: str, code: str, message: str, remedy: str = "",
            where: str = subject) -> None:
        out.findings.append(Finding(severity, code, message, subject=where,
                                    remedy=remedy))

    # ---- the computer ------------------------------------------------------------
    if host.system == "windows":
        add(ERROR, "axqua.install.windows_needs_wsl",
            "TELEMAC and OpenFOAM are installed in the Windows Subsystem for Linux, "
            "not in Windows itself",
            "Install a distribution with 'wsl --install -d Ubuntu-24.04' in a "
            "PowerShell with administrator rights, install aXqua in it, and start "
            "this installation there.")
        return out
    if not host.supported:
        add(ERROR, "axqua.install.unsupported_system",
            f"there is no installer for this system ({host.describe()}); the "
            "installers cover " + ", ".join(hosts.BASES.values()) + " and systems "
            "built on them",
            "On a system that is built on one of these, name its base with --base. "
            "Otherwise install the program by hand and enter it in the profile.")
        return out
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        add(ERROR, "axqua.install.as_root",
            "the installers refuse to run as the user root",
            "Start aXqua as an ordinary user.")
    if target != "telemac" and not host.x86_64:
        add(ERROR, "axqua.install.unsupported_system",
            f"the installer for {TITLES[target]} supports x86-64 computers only; "
            f"this is {host.machine or 'another architecture'}")
    if host.wsl and str(folder).startswith("/mnt/"):
        add(ERROR, "axqua.install.windows_folder",
            f"{folder} is a folder of Windows; a build there is slower by an order "
            "of magnitude and the OpenFOAM installer refuses it",
            "Select a folder in the Linux file system, for example below your home "
            "folder.", where=f"{subject}.folder")
    if any(ch.isspace() for ch in str(folder)):
        add(ERROR, "axqua.install.folder_with_space",
            f"the folder {folder} has a space in its name; neither TELEMAC nor "
            "OpenFOAM can be built in such a folder",
            "Select a folder without spaces.", where=f"{subject}.folder")

    # ---- the installer scripts ---------------------------------------------------
    try:
        checkout = fetch_installers(options)
    except AxquaError as exc:
        add(ERROR, "axqua.install.no_installer", exc.message, exc.remedy or "")
        return out
    out.installers = checkout

    builder = {"telemac": _plan_telemac, "openfoam": _plan_openfoam,
               "postprocessors": _plan_postprocessors}[target]
    builder(out, checkout, add)

    # ---- system packages ---------------------------------------------------------
    out.needed = packages_needed(target, checkout, host, options)
    if not out.needed and out.steps:
        add(WARNING, "axqua.install.packages_unknown",
            "the list of system packages could not be read from the installer, so "
            "aXqua cannot tell whether one is missing",
            "The installation stops with a message if a package is missing.")
    elif check_packages:
        out.missing, out.unavailable = packages_state(out.needed)
        if out.missing:
            remedy = ("An administrator installs them with: "
                      + admin_command(target, out.missing))
            if target == "openfoam" and _reuse(options) is not None \
                    and not _tools_missing(checkout):
                # The list is what compiling OpenFOAM itself takes. Building the
                # solvers against an installed OpenFOAM needs the compiler tools
                # only, and those are all here.
                out.package_note = ("They are needed to compile OpenFOAM itself. With "
                                    "the installed OpenFOAM the installation normally "
                                    "works without them.")
                remedy = out.package_note + " " + remedy
            add(WARNING, "axqua.install.packages_missing",
                f"{len(out.missing)} of {len(out.needed)} system packages are not "
                "installed: " + " ".join(out.missing), remedy,
                where=f"{subject}.packages")
        if out.unavailable:
            add(WARNING, "axqua.install.packages_unavailable",
                "the package sources of this system do not offer: "
                + " ".join(out.unavailable),
                "The installation may still work if another package provides the "
                "same files. Otherwise an administrator has to add a package source.",
                where=f"{subject}.packages")

    # ---- space -------------------------------------------------------------------
    key = "openfoam-source" if target == "openfoam" and not _reuse(options) else target
    out.estimate = out.estimate or ESTIMATES[key]
    free = _free_gb(folder)
    if free is not None and free < DISK_GB[key]:
        add(WARNING, "axqua.install.little_space",
            f"{free:.0f} GiB are free in {folder}; the installation needs about "
            f"{DISK_GB[key]} GiB", "Select a folder on a drive with more free space.",
            where=f"{subject}.folder")
    if any(f.severity == ERROR for f in out.findings):
        out.steps = []
    return out


def _reuse(options: Options) -> Path | None:
    """The OpenFOAM v2406 the installation builds on, or ``None`` to compile one."""
    choice = (options.reuse_openfoam or "auto").strip()
    if choice.lower() == "no":
        return None
    if choice.lower() == "auto":
        return find_openfoam()
    return Path(choice).expanduser()


def _plan_telemac(out: Plan, checkout: Path, add) -> None:
    script = TELEMAC_SCRIPTS.get(out.host.base)
    subject = "install.telemac"
    if script is None or not (checkout / script[0]).is_file():
        add(ERROR, "axqua.install.unsupported_system",
            f"the TELEMAC installer covers Debian 12 and Ubuntu 24.04 (with Linux Mint "
            f"22); this system is built on {hosts.BASES.get(out.host.base, 'another')}",
            "Install TELEMAC as its documentation describes and enter its "
            "environment script in the profile.")
        return
    root = out.folder
    home = root / "telemac-mascaret"
    if out.options.salome and not Path(out.options.salome).is_file():
        add(ERROR, "axqua.install.file_missing",
            f"the SALOME archive {out.options.salome} does not exist",
            "Select the downloaded archive, or leave the field empty: aXqua does not "
            "need SALOME.", where=f"{subject}.salome")
    if home.exists():
        add(WARNING, "axqua.install.folder_exists",
            f"{home} exists already; the installer keeps its source code and builds "
            "TELEMAC in it again",
            "Select another folder to leave this installation untouched.",
            where=f"{subject}.folder")
    if _which("git") and _which("git-lfs"):
        # The script does this itself only together with the system packages.
        out.steps.append(Step("Prepare Git for large files", ["git", "lfs", "install"]))
    argv = ["/bin/bash", str(checkout / script[0]), "--root", str(root), "--skip-apt"]
    if out.options.tag:
        argv += ["--tag", out.options.tag]
    if out.options.salome:
        argv += ["--salome-tar", str(out.options.salome)]
    # The binary files of the example cases and the manuals are 1,500 files in Git LFS,
    # 1.65 GB, which Git fetches one by one at the checkout: on the development
    # computer more than an hour of an installation whose build takes minutes. Nothing
    # in the source code is stored that way. So the checkout leaves them out unless
    # everything is asked for, and what a run of an example reads is fetched afterwards
    # as one list (see axqua.install.examples).
    choice = out.options.telemac_examples
    out.steps.append(Step("Download and build TELEMAC", argv,
                          env={} if choice == "all" else {"GIT_LFS_SKIP_SMUDGE": "1"}))
    out.outputs["solvers.telemac.setup_script"] = str(home / "configs" / script[1])
    out.expects.append(str(home / "builds" / "*" / "bin" / "telemac2d"))
    out.notes.append(f"TELEMAC is installed in {home}.")
    if choice == "inputs":
        out.steps.append(Step(
            "Download the input files of the example cases",
            [sys.executable, "-B", "-m", "axqua", "install", "examples", str(home)],
            optional=True))
        out.notes.append("Of the example cases of TELEMAC, the files that a run reads "
                         "are downloaded (about 360 files, 460 MB), without the "
                         "reference results and the manuals. To get those later, run "
                         f"'git lfs pull' in {home}.")
    elif choice == "none":
        out.estimate = ESTIMATES["telemac-none"]
        out.notes.append("The example cases of TELEMAC come with their steering files "
                         "only. To get their geometry files later, run 'axqua install "
                         f"examples {home}'.")
    else:
        out.estimate = ESTIMATES["telemac-all"]
        out.notes.append("All files of the example cases and the manuals of TELEMAC "
                         "are downloaded (1,500 files, 1.65 GB).")
    if not out.options.salome:
        out.notes.append("SALOME is not installed. aXqua does not need it.")


def _plan_openfoam(out: Plan, checkout: Path, add) -> None:
    subject = "install.openfoam"
    prefix = out.folder
    python = installer_python(checkout)
    if python is None:
        add(ERROR, "axqua.install.python_too_old",
            "the OpenFOAM installer does not run with any Python on this computer; "
            f"aXqua itself runs with Python {sys.version_info.major}."
            f"{sys.version_info.minor}",
            "Install Python 3.12 or newer, for example with Miniforge, and run aXqua "
            "with it.")
        return
    marker = prefix / ".sediment-installer.json"
    if prefix.exists() and not marker.is_file():
        add(ERROR, "axqua.install.folder_in_use",
            f"{prefix} exists and was not made by this installer, which only installs "
            "into a new folder or continues its own installation",
            "Select a folder that does not exist yet.", where=f"{subject}.folder")
    elif prefix.exists():
        add(WARNING, "axqua.install.folder_exists",
            f"{prefix} holds an installation of this installer, which is continued",
            "The installer refuses the folder if its own version has changed in the "
            "meantime. Select a new folder in that case.", where=f"{subject}.folder")

    jobs = out.options.jobs or default_jobs()
    shadowed = shadowed_tools()
    if shadowed:
        argv = [python, "-I", "-B", "-c", OPENFOAM_DRIVER, str(checkout / OPENFOAM_DIR)]
        out.notes.append(
            "The build uses the compilers of the operating system. The folder "
            "/usr/local/bin holds other versions of " + ", ".join(shadowed)
            + ", with which the solvers cannot be linked to a packaged OpenFOAM.")
    else:
        argv = [python, "-B", str(checkout / OPENFOAM_DIR / "install.py")]
    argv += ["--prefix", str(prefix), "--jobs", str(jobs)]
    reuse = _reuse(out.options)
    if reuse is not None:
        if not reuse.is_file():
            add(ERROR, "axqua.install.file_missing",
                f"the OpenFOAM environment script {reuse} does not exist",
                "Select the file etc/bashrc of an OpenFOAM v2406, or let aXqua compile "
                "OpenFOAM.", where=f"{subject}.reuse_openfoam")
        elif _api(reuse) not in ("", OPENFOAM_RELEASE):
            add(ERROR, "axqua.install.wrong_openfoam",
                f"{reuse} belongs to OpenFOAM release {_api(reuse)}; the sediment "
                f"solvers are built against v{OPENFOAM_RELEASE}",
                f"Select the etc/bashrc of v{OPENFOAM_RELEASE}, or let aXqua compile "
                "it.", where=f"{subject}.reuse_openfoam")
        argv += ["--reuse-openfoam", str(reuse)]
        out.notes.append(f"The OpenFOAM v{OPENFOAM_RELEASE} in {reuse.parent.parent} "
                         "is used; only the sediment solvers and the outflow boundary "
                         "are compiled.")
    else:
        add(WARNING, "axqua.install.compiles_openfoam",
            f"no OpenFOAM v{OPENFOAM_RELEASE} was found on this computer, so it is "
            "compiled from its source code, which takes several hours and about "
            "20 GiB",
            "If v2406 is installed, select its etc/bashrc.",
            where=f"{subject}.reuse_openfoam")
    if out.options.visualization:
        argv += ["--visit-platform", out.host.base]
    else:
        argv.append("--skip-visualization")
    if out.options.examples:
        argv.append("--examples")
    if out.options.smoke_test:
        argv.append("--smoke-test")
    out.steps.append(Step("Build the OpenFOAM sediment stack", argv))
    out.outputs["solvers.openfoam.setup_script"] = str(prefix / "shell-rc.sh")
    out.expects.append(str(prefix / "receipt.json"))
    out.expects.append(str(prefix / "user" / "platforms" / "*" / "bin" / "sediDriftFoam2"))
    if out.options.visualization:
        out.outputs["postprocessors.paraview"] = str(PARAVIEW)
        out.outputs["postprocessors.visit"] = str(
            prefix / "apps" / f"visit-*-{out.host.base}" / "bin" / "visit")
    out.notes.append("Installed are sediDriftFoam, sediDriftFoam2, the stage-discharge "
                     "outflow boundary of the BAW"
                     + (", ParaView and VisIt." if out.options.visualization else "."))


def _plan_postprocessors(out: Plan, checkout: Path, add) -> None:
    subject = "install.postprocessors"
    prefix = out.folder
    if not (checkout / OPENFOAM_DIR / "sediment_installer" / "visualization.py").is_file():
        add(ERROR, "axqua.install.no_installer",
            "this version of the installer scripts has no installer for VisIt")
        return
    python = sys.executable
    argv = [python, "-I", "-B", "-c", VISIT_DRIVER, str(checkout / OPENFOAM_DIR),
            str(prefix), out.host.base]
    out.steps.append(Step("Download and install VisIt", argv))
    out.outputs["postprocessors.visit"] = str(
        prefix / "apps" / f"visit-*-{out.host.base}" / "bin" / "visit")
    out.outputs["postprocessors.paraview"] = str(PARAVIEW)
    out.notes.append("ParaView is the package of the operating system. VisIt is "
                     f"downloaded from its publisher and installed in {prefix}.")
    if not PARAVIEW.is_file():
        add(WARNING, "axqua.install.paraview_missing",
            "ParaView is not installed; it is one of the system packages listed here",
            "It is entered in the profile once the packages are installed.",
            where=f"{subject}.packages")
