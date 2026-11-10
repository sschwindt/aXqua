"""TELEMAC built on Windows itself, without WSL and without an administrator.

The steps of the installation that :func:`axqua.install.recipes.plan` writes for ``axqua install start telemac --native`` on Windows, after the conda step that creates the compiler environment (GNU compilers for Windows, CMake, Make and Git from conda-forge). Each step runs as ``python -m axqua.install.windows_telemac <step> ...``, so that the files and programs are looked up when the step runs, not when the plan is made:

``source``     download the source code of TELEMAC with Git (the large example files stay out)
``configure``  CMake with MinGW Makefiles, sequential (no MPI, MED, MUMPS)
``build``      compile TELEMAC-2D with GAIA, or all modules
``script``     write ``configs\\pysource.win.bat``, the environment script aXqua enters in the profile
``remove``     remove an earlier installation (``--reinstall replace``)

A Windows test computer without administrator rights ran exactly these commands by hand on 2026-11-09 (TELEMAC v9.1.1, TELEMAC-2D, the steady run of the Isar example on one core). Only the standard library is used: :mod:`axqua.install` is also imported by the Python of a WSL distribution.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

__all__ = ["BUILD", "REPOSITORY", "SCRIPT", "TAG", "build_dir", "cmd_line", "compiler_env",
           "configure_argv", "environment_script", "main"]

#: Where the source code of TELEMAC is published, and the version this route was tested with.
REPOSITORY = "https://gitlab.pam-retd.fr/otm/telemac-mascaret.git"
TAG = "v9.1.1"
#: The build configuration below ``builds``, and the environment script below ``configs``.
BUILD = "win64-seq"
SCRIPT = "pysource.win.bat"
#: The CMake options of the tested build: sequential, and without the libraries that conda-forge does not offer for this compiler.
CMAKE_OPTIONS = ("-DCMAKE_BUILD_TYPE=Release", "-DUSE_MPI=OFF", "-DUSE_MED=OFF", "-DUSE_MUMPS=OFF",
                 "-DUSE_AED2=OFF", "-DUSE_GOTM=OFF", "-DBUILD_TELAPY=OFF", "-DBUILD_HERMES_WRAPPER=OFF")
#: ``modules -> CMake targets``. ``build_commands`` writes the commands TELEMAC compiles user Fortran with during a run.
TARGETS = {"2d": ("homere_telemac2d", "build_commands"), "all": ()}


def build_dir(home: Path) -> Path:
    return home / "builds" / BUILD


def _bin(env: Path) -> Path:
    return env / "Library" / "bin"


def _first(folder: Path, *names: str) -> Path | None:
    return next((folder / name for name in names if (folder / name).is_file()), None)


def compiler_env(env: Path) -> dict[str, str]:
    """The environment of a command in the compiler environment *env*: its folders first on ``PATH``, as ``conda activate`` puts them."""
    base = dict(os.environ)
    folders = [env, env / "Library" / "mingw-w64" / "bin", env / "Library" / "usr" / "bin", _bin(env),
               env / "Scripts", env / "bin"]
    base["PATH"] = os.pathsep.join([*(str(f) for f in folders), base.get("PATH", "")])
    base["CONDA_PREFIX"] = str(env)
    return base


def cmd_line(argv: list[str]) -> str:
    """*argv* as one line of ``cmd``, every part in double quotes."""
    return " ".join(f'"{part}"' for part in argv)


def _run(argv: list[str], env: Path, conda_base: Path | None) -> int:
    """Run *argv* inside the compiler environment and return its exit code.

    The conda activation of the environment is what the test computer used (``call activate.bat <env> && ...``); without it, the folders of the environment are put first on ``PATH``.
    """
    print("$ " + " ".join(argv), flush=True)
    activate = conda_base / "Scripts" / "activate.bat" if conda_base else None
    if os.name == "nt" and activate is not None and activate.is_file():
        line = f'cmd /d /s /c "call "{activate}" "{env}" && {cmd_line(argv)}"'
        return subprocess.run(line, env=compiler_env(env)).returncode
    return subprocess.run(argv, env=compiler_env(env)).returncode


# ----------------------------------------------------------------------------- the steps


def _git(env: Path) -> str | None:
    found = _first(_bin(env), "git.exe") or _first(env / "Library" / "cmd", "git.exe") \
        or _first(env / "Library" / "mingw64" / "bin", "git.exe") or _first(env / "Scripts", "git.exe")
    if found is None and (env / "Library").is_dir():
        found = next(iter(sorted((env / "Library").glob("**/git.exe"))), None)
    return str(found) if found else shutil.which("git")


def _complete(home: Path) -> bool:
    return (home / "CMakeLists.txt").is_file() and (home / "sources").is_dir() and (home / "scripts").is_dir()


def source(home: Path, env: Path, tag: str) -> int:
    """Download the source code into *home*, unless it is there already (an installation continued)."""
    if _complete(home):
        print(f"The source code of TELEMAC is in {home} already and is kept.", flush=True)
        return 0
    if home.exists():
        left = [p.name for p in home.iterdir()]
        if left and set(left) != {".git"}:
            print(f"{home} exists and is not the source code of TELEMAC; aXqua does not write into it. "
                  "Select another folder, or remove this one.", flush=True)
            return 2
        _remove(home)                                  # an interrupted download
    git = _git(env)
    if git is None:
        print("Git was not found in the compiler environment " + str(env), flush=True)
        return 2
    home.parent.mkdir(parents=True, exist_ok=True)
    argv = [git, "-c", "core.longpaths=true", "clone", "--depth", "1", "--branch", tag or TAG, REPOSITORY,
            str(home)]
    print("$ " + " ".join(argv), flush=True)
    # the 1,500 large files of the example cases and manuals are not needed to build or run TELEMAC
    code = subprocess.run(argv, env={**compiler_env(env), "GIT_LFS_SKIP_SMUDGE": "1"}).returncode
    if code:
        print("The download failed. When the server of TELEMAC does not answer (HTTP 502 or 504), start the "
              "installation again later; it continues from here.", flush=True)
    return code


def configure_argv(home: Path, env: Path) -> list[str]:
    """The CMake command of the tested build, with the compilers of *env*."""
    tools = _bin(env)
    make = _first(tools, "mingw32-make.exe", "make.exe") or tools / "mingw32-make.exe"
    gcc = _first(tools, "x86_64-w64-mingw32-gcc.exe", "gcc.exe") or tools / "x86_64-w64-mingw32-gcc.exe"
    fortran = _first(tools, "x86_64-w64-mingw32-gfortran.exe", "gfortran.exe") \
        or tools / "x86_64-w64-mingw32-gfortran.exe"
    cmake = _first(tools, "cmake.exe") or "cmake"
    return [str(cmake), "-S", str(home), "-B", str(build_dir(home)), "-G", "MinGW Makefiles",
            f"-DCMAKE_MAKE_PROGRAM={make}", f"-DCMAKE_C_COMPILER={gcc}", f"-DCMAKE_Fortran_COMPILER={fortran}",
            f"-DPython_EXECUTABLE={env / 'python.exe'}", *CMAKE_OPTIONS]


def configure(home: Path, env: Path, conda_base: Path | None) -> int:
    if not _complete(home):
        print(f"There is no source code of TELEMAC in {home}.", flush=True)
        return 2
    return _run(configure_argv(home, env), env, conda_base)


def build(home: Path, env: Path, conda_base: Path | None, jobs: int, modules: str) -> int:
    cmake = str(_first(_bin(env), "cmake.exe") or "cmake")
    argv = [cmake, "--build", str(build_dir(home)), "--parallel", str(max(1, jobs))]
    targets = TARGETS.get(modules, TARGETS["2d"])
    code = _run(argv + (["--target", *targets] if targets else []), env, conda_base)
    if code == 0 and not targets:
        # all modules: the commands for user Fortran are a target of their own
        code = _run(argv + ["--target", "build_commands"], env, conda_base)
    return code


def _home_relative(path: Path) -> str:
    """*path* below the home folder as ``%USERPROFILE%\\...``: a batch file is read in the code page of the console, and a user name with an umlaut would not survive it."""
    home = Path.home()
    try:
        rest = path.relative_to(home)
    except ValueError:
        return str(path)
    return "%USERPROFILE%" + ("\\" + str(rest) if str(rest) not in ("", ".") else "")


def environment_script(home: Path, env: Path) -> str:
    """The text of ``configs\\pysource.win.bat``: variables only, nothing on the computer is changed."""
    tools = _home_relative(_bin(env))
    lines = ["@echo off",
             "rem Written by aXqua: the environment of TELEMAC built on Windows (no MPI).",
             f'set "HOMETEL={_home_relative(home)}"',
             f'set "BUILD_DIR=%HOMETEL%\\builds\\{BUILD}"',
             'set "SYSTELCFG=%BUILD_DIR%\\systel.cfg"',
             'set "USETELCFG=general"',
             'set "PYTHONPATH=%HOMETEL%\\scripts\\python3;%BUILD_DIR%\\lib"',
             f'set "PATH=%HOMETEL%\\scripts\\python3;%BUILD_DIR%\\bin;{tools};%PATH%"',
             'set "PYTHONUNBUFFERED=1"']
    return "\r\n".join(lines) + "\r\n"


def script(home: Path, env: Path) -> int:
    if not (build_dir(home) / "systel.cfg").is_file():
        print(f"The build wrote no {build_dir(home) / 'systel.cfg'}; the configuration failed.", flush=True)
        return 2
    target = home / "configs" / SCRIPT
    target.parent.mkdir(parents=True, exist_ok=True)
    text = environment_script(home, env)
    try:
        data = text.encode("oem" if os.name == "nt" else "utf-8")
    except (LookupError, UnicodeEncodeError):
        print(f"The folder {home} has characters the console of Windows cannot read in a batch file. "
              "Install into a folder whose path has only plain letters.", flush=True)
        return 2
    target.write_bytes(data)
    print(f"Wrote {target}", flush=True)
    return 0


def _writable(func, path, _exc) -> None:
    """Git writes its objects read-only, which Windows refuses to delete."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=lambda func, p, exc: _writable(func, p, exc))
    else:                                               # pragma: no cover - Python before 3.12
        shutil.rmtree(path, onerror=_writable)


def remove(paths: list[Path]) -> int:
    for path in paths:
        if path.exists():
            print(f"Removing {path}", flush=True)
            _remove(path)
    return 0


# --------------------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m axqua.install.windows_telemac", description=__doc__.split("\n")[0])
    p.add_argument("step", choices=("source", "configure", "build", "script", "remove"))
    p.add_argument("paths", nargs="*", type=Path, help="remove: what to remove")
    p.add_argument("--home", type=Path, help="the folder telemac-mascaret")
    p.add_argument("--env", type=Path, help="the compiler environment")
    p.add_argument("--conda-base", type=Path, help="the conda installation that has activate.bat")
    p.add_argument("--tag", default=TAG)
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--modules", choices=sorted(TARGETS), default="2d")
    args = p.parse_args(argv)
    if args.step == "remove":
        return remove(args.paths)
    if args.home is None or args.env is None:
        p.error(f"{args.step} needs --home and --env")
    if args.step == "source":
        return source(args.home, args.env, args.tag)
    if args.step == "configure":
        return configure(args.home, args.env, args.conda_base)
    if args.step == "build":
        return build(args.home, args.env, args.conda_base, args.jobs, args.modules)
    return script(args.home, args.env)


if __name__ == "__main__":
    sys.exit(main())
