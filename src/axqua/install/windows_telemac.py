"""TELEMAC built on Windows itself, without WSL and without an administrator.

The steps of the installation that :func:`axqua.install.recipes.plan` writes for ``axqua install start telemac --native`` on Windows, after the conda step that creates the compiler environment (GNU compilers for Windows, CMake, Make and Git from conda-forge). Each step runs as ``python -m axqua.install.windows_telemac <step> ...``, so that the files and programs are looked up when the step runs, not when the plan is made:

``source``     download the source code of TELEMAC with Git (the large example files stay out)
``msmpi``      parallel build only: download MS-MPI, make its import library and Fortran module, run two processes
``metis``      parallel build only: download and compile METIS, which the partitioner of TELEMAC needs
``configure``  CMake with MinGW Makefiles, sequential or with MPI (no MED, MUMPS)
``build``      compile TELEMAC-2D with GAIA, or all modules
``script``     write ``configs\\pysource.win.bat`` (``pysource.win-mpi.bat``), the environment script aXqua enters in the profile
``remove``     remove an earlier installation (``--reinstall replace``)

A Windows test computer without administrator rights ran the sequential commands by hand on 2026-11-09 (TELEMAC v9.1.1, TELEMAC-2D, the steady run of the Isar example on one core), and the parallel ones on 2026-11-11 (MS-MPI 10.1.3 and METIS 5.1.0.3 in the user folder; TELEMAC-2D and TELEMAC-3D on four processes equal to one process and to TELEMAC's reference results, TELEMAC-3D with GAIA on two, and the Isar example through ``axqua submit --np 4``). Only the standard library is used: :mod:`axqua.install` is also imported by the Python of a WSL distribution.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

__all__ = ["BUILD", "BUILD_MPI", "METIS_URL", "MSMPI_FILES", "MSMPI_URL", "REPOSITORY", "SCRIPT", "SCRIPT_MPI", "TAG",
           "activation_hooks", "build_dir", "cmd_line", "compiler_env", "configure_argv", "environment_script",
           "main", "metis_dir", "msmpi_dir", "script_name"]

#: Where the source code of TELEMAC is published, and the version this route was tested with.
REPOSITORY = "https://gitlab.pam-retd.fr/otm/telemac-mascaret.git"
TAG = "v9.1.1"
#: The build configuration below ``builds``, and the environment script below ``configs``: sequential, and with MPI.
BUILD = "win64-seq"
SCRIPT = "pysource.win.bat"
BUILD_MPI = "win64-mpi"
SCRIPT_MPI = "pysource.win-mpi.bat"
#: The CMake options of the tested build: without the libraries that conda-forge does not offer for this compiler. ``-DUSE_MPI=OFF`` becomes ON in the parallel build.
CMAKE_OPTIONS = ("-DCMAKE_BUILD_TYPE=Release", "-DUSE_MPI=OFF", "-DUSE_MED=OFF", "-DUSE_MUMPS=OFF",
                 "-DUSE_AED2=OFF", "-DUSE_GOTM=OFF", "-DBUILD_TELAPY=OFF", "-DBUILD_HERMES_WRAPPER=OFF")
#: The Fortran module of MS-MPI has hexadecimal constants and calls without interfaces that GNU Fortran 10 and later refuse without these.
MPI_FORTRAN_FLAGS = "-fno-range-check -fallow-invalid-boz -fallow-argument-mismatch"
#: ``modules -> CMake targets``. ``build_commands`` writes the commands TELEMAC compiles user Fortran with during a run.
TARGETS = {"2d": ("homere_telemac2d", "build_commands"), "all": ()}
#: What the parallel build adds to ``2d``: the partitioner and the program that joins the results of the processes.
TARGETS_MPI = ("homere_partel", "homere_gretel")

#: MS-MPI 10.1.3 of Microsoft, the runtime and the software development kit as TELEMAC's own Windows build (WinTel) keeps them, at a fixed commit. ``path below MSMPI_URL -> SHA-256``. Downloaded at install time, not shipped with aXqua.
MSMPI_URL = ("https://gitlab.pam-retd.fr/otm/wintel/-/raw/59abee3d13d4d4d7cb205ff3e62b2941f397a3d7/"
             "deps/msmpi-10.1.3/")
MSMPI_FILES = {
    "bin/mpiexec.exe": "161d18ac9f66d28add3aec8719f834297d0dc08ce07ec536c8aed5a9172fca8e",
    "bin/msmpi.dll": "d8f74689e83d7e5a7277de15b221edea91f529529f8a16fd0e510236c0705dcb",
    "bin/msmpires.dll": "f9ef075684ccd2c2a72d14c2d89069b8b66298125333923d00b61434ac2f15ad",
    "bin/smpd.exe": "739df32db5a8879d7e721052acbe457f99afe7897c982d99c9483ae736a7b1ab",
    "sdk/mpi.f90": "18cd772e2b88fe891e9c89ef3ba16b9d5a13c29e504ea0bfdcaaedc84b6526cc",
    "sdk/mpif.h": "46af38075602397da4380d28bd82f85d9a51f47ae8fb7b70ab50eb85b682eda1",
    "sdk/mpifptr.h.x86_64": "e1f280b0d5d8d770ae12488968e1ddecd6d09eb28a86194caf5285183e415a37",
    "sdk/msmpi.def.x86_64": "137aa913e0ae0b3c60fa19fc673486305e96332f89eb3a7b0ba75b6eb6577918",
}
#: METIS 5.1.0.3 with a CMake build (Apache-2.0), the version the Windows test built TELEMAC with.
METIS_URL = "https://github.com/scivision/METIS/archive/refs/tags/v5.1.0.3.tar.gz"
METIS_SHA256 = "c860b393f61ef017f288692b00fd26eec2d3fdbc7a2bb3bed8b0f01e836e53cf"
METIS_FOLDER = "METIS-5.1.0.3"

#: The test of MS-MPI before TELEMAC is compiled: every process adds its number plus one, so two processes print a sum of 3.
PROBE = """program probe
  use mpi
  implicit none
  integer :: ierr, rank, n, send, total
  call MPI_Init(ierr)
  call MPI_Comm_rank(MPI_COMM_WORLD, rank, ierr)
  call MPI_Comm_size(MPI_COMM_WORLD, n, ierr)
  send = rank + 1
  call MPI_Allreduce(send, total, 1, MPI_INTEGER, MPI_SUM, MPI_COMM_WORLD, ierr)
  print '(A,I0,A,I0,A,I0)', 'rank=', rank, ' size=', n, ' sum=', total
  call MPI_Finalize(ierr)
end program probe
"""


def build_dir(home: Path, mpi: bool = False) -> Path:
    return home / "builds" / (BUILD_MPI if mpi else BUILD)


def script_name(mpi: bool = False) -> str:
    return SCRIPT_MPI if mpi else SCRIPT


def msmpi_dir(home: Path) -> Path:
    """MS-MPI of a parallel build, beside ``telemac-mascaret`` and the compiler environment."""
    return home.parent / "msmpi"


def metis_dir(home: Path) -> Path:
    return home.parent / "metis"


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


def activation_hooks(env: Path) -> list[Path]:
    """The batch files the packages of *env* run when it is activated (``etc\\conda\\activate.d``), in the order conda runs them."""
    return sorted((env / "etc" / "conda" / "activate.d").glob("*.bat"))


def _run(argv: list[str], env: Path) -> int:
    """Run *argv* inside the compiler environment and return its exit code.

    What ``conda activate`` does, without conda's ``activate.bat``: the folders of the environment go first on ``PATH`` (:func:`compiler_env`), and the activation scripts of its packages are called before the command. ``activate.bat`` asks PowerShell for a name of a temporary file, and on the Windows test computer without PowerShell (2026-11-10) every configure and compile printed "'powershell' is not recognized" although the build succeeded.
    """
    print("$ " + " ".join(argv), flush=True)
    if _windows():
        # always through cmd, which finds a bare program name on the PATH of the compiler environment (a program started directly is looked up on the PATH of the runner); "&", not "&&": a hook's exit code is not the command's
        line = " & ".join([*(f'call "{hook}"' for hook in activation_hooks(env)), cmd_line(argv)])
        return subprocess.run(f'cmd /d /s /c "{line}"', env=compiler_env(env)).returncode
    return subprocess.run(argv, env=compiler_env(env)).returncode


def _windows() -> bool:
    return os.name == "nt"


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


def _tool(env: Path, *names: str) -> Path:
    tools = _bin(env)
    return _first(tools, *names) or tools / names[0]


def _cmake(env: Path) -> str:
    return str(_first(_bin(env), "cmake.exe") or "cmake")


def _make_options(env: Path) -> list[str]:
    """Generator, Make and C compiler of *env*, for TELEMAC and METIS alike."""
    return ["-G", "MinGW Makefiles", f"-DCMAKE_MAKE_PROGRAM={_tool(env, 'mingw32-make.exe', 'make.exe')}",
            f"-DCMAKE_C_COMPILER={_tool(env, 'x86_64-w64-mingw32-gcc.exe', 'gcc.exe')}"]


def _metis_library(home: Path) -> Path | None:
    """The compiled METIS: the one CMake writes below ``libmetis``, else the first one found."""
    build = metis_dir(home) / "build"
    if not build.is_dir():
        return None
    expected = build / "libmetis" / "libmetis.a"
    return expected if expected.is_file() else next(iter(sorted(build.rglob("libmetis.a"))), None)


def _mpi_options(home: Path) -> list[str]:
    """CMake's FindMPI does not find MS-MPI for GNU Fortran by itself: the Windows test stopped at ``MPI_Fortran_LIB_NAMES`` and then at ``MPI_mpifptr_INCLUDE_DIR`` (2026-11-11) and configured with these."""
    mpi = msmpi_dir(home)
    include = (mpi / "include").as_posix()
    metis = metis_dir(home) / "build"
    library = _metis_library(home) or metis / "libmetis.a"
    return ["-DUSE_MPI=ON", f"-DCMAKE_Fortran_FLAGS={MPI_FORTRAN_FLAGS}",
            f"-DMPIEXEC_EXECUTABLE={mpi / 'bin' / 'mpiexec.exe'}",
            f"-DMPI_Fortran_ADDITIONAL_INCLUDE_DIRS={include}", f"-DMPI_mpifptr_INCLUDE_DIR={include}",
            "-DMPI_Fortran_LIB_NAMES=msmpi", f"-DMPI_msmpi_LIBRARY={(mpi / 'lib' / 'libmsmpi.a').as_posix()}",
            f"-DMETIS_INCLUDE_DIR={metis / 'src' / 'include'}", f"-DMETIS_LIBRARY={library}"]


SERVER_DOWN = ("When the server does not answer (HTTP 502 or 504), start the installation again later; "
               "it continues from here.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, target: Path, sha256: str) -> bool:
    """*url* to *target*, checked against *sha256*; a file there already with that hash is kept. False on any failure, said in the log."""
    if target.is_file() and _sha256(target) == sha256:
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    print(f"Downloading {url}", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=120) as response, part.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except (OSError, http.client.HTTPException) as exc:     # URLError and HTTPError are OSErrors; a cut connection is IncompleteRead
        part.unlink(missing_ok=True)
        print(f"The download of {url} failed: {exc}. {SERVER_DOWN}", flush=True)
        return False
    got = _sha256(part)
    if got != sha256:
        part.unlink(missing_ok=True)
        print(f"{url} is not the file aXqua was tested with (SHA-256 {got}, expected {sha256}); it is not used.",
              flush=True)
        return False
    part.replace(target)
    return True


def msmpi(home: Path, env: Path) -> int:
    """Download MS-MPI into ``msmpi`` beside *home*, make what GNU Fortran needs of it, and run two processes.

    MS-MPI comes for Microsoft's compilers: GNU Fortran needs an import library made from ``msmpi.def`` and the module ``mpi.mod`` compiled from ``mpi.f90``. Nothing is installed in Windows and no service is registered: ``mpiexec`` starts its helper ``smpd`` itself. A step that passed once is not repeated (a second run of ``mpiexec`` may bring up the question of a firewall again): ``probe\\passed`` records it, next to the files it was made from.
    """
    mpi = msmpi_dir(home)
    for name, sha256 in MSMPI_FILES.items():
        if not _download(MSMPI_URL + name, mpi / name, sha256):
            return 2
    include, lib = mpi / "include", mpi / "lib"
    passed = mpi / "probe" / "passed"
    if passed.is_file() and (lib / "libmsmpi.a").is_file() and (include / "mpi.mod").is_file():
        print(f"MS-MPI is prepared in {mpi} and ran two processes before; it is kept.", flush=True)
        return 0
    include.mkdir(exist_ok=True)
    lib.mkdir(exist_ok=True)
    shutil.copyfile(mpi / "sdk" / "mpif.h", include / "mpif.h")
    shutil.copyfile(mpi / "sdk" / "mpifptr.h.x86_64", include / "mpifptr.h")
    dlltool = _tool(env, "x86_64-w64-mingw32-dlltool.exe", "dlltool.exe")
    fortran = _tool(env, "x86_64-w64-mingw32-gfortran.exe", "gfortran.exe")
    steps = [[str(dlltool), "-k", "-d", str(mpi / "sdk" / "msmpi.def.x86_64"), "-l", str(lib / "libmsmpi.a")],
             [str(fortran), *MPI_FORTRAN_FLAGS.split(), f"-I{include}", f"-J{include}", "-c",
              str(mpi / "sdk" / "mpi.f90"), "-o", str(lib / "mpi.o")]]
    probe = mpi / "probe"
    probe.mkdir(exist_ok=True)
    (probe / "probe.f90").write_text(PROBE, encoding="ascii")
    exe = probe / "probe.exe"
    steps.append([str(fortran), f"-I{include}", str(probe / "probe.f90"), f"-L{lib}", "-lmsmpi", "-o", str(exe)])
    for argv in steps:
        code = _run(argv, env)
        if code:
            return code
    passed.unlink(missing_ok=True)
    code = _probe(mpi, env, exe)
    if code == 0:
        passed.write_text("mpiexec -n 2: sum=3 on both processes\n", encoding="ascii")
    return code


def _probe(mpi: Path, env: Path, exe: Path) -> int:
    """Run *exe* on two processes; each must print the sum 3."""
    environment = compiler_env(env)
    environment["PATH"] = str(mpi / "bin") + os.pathsep + environment["PATH"]
    environment.update(MSMPI_DISABLE_ND="1", MPIEXEC_TIMEOUT="60")
    argv = [str(mpi / "bin" / "mpiexec.exe"), "-n", "2", str(exe)]
    print("$ " + " ".join(argv), flush=True)
    try:
        process = subprocess.Popen(argv, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as exc:
        print(f"MS-MPI did not run two processes: {exc}", flush=True)
        return 2
    try:
        stdout, _ = process.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        # mpiexec has started smpd and the two processes: end the whole tree, not mpiexec alone
        _end_tree(process)
        print("MS-MPI did not run two processes within 90 s. A firewall or security program may block mpiexec "
              "and smpd; allow them for private networks, or install the build on one processor core (--one-core).", flush=True)
        return 2
    done = subprocess.CompletedProcess(argv, process.returncode, stdout=stdout)
    output = done.stdout.decode("utf-8", errors="replace")
    print(output, end="" if output.endswith("\n") else "\n", flush=True)
    sums = re.findall(r"rank=(\d+) size=2 sum=(\d+)", output)
    if done.returncode or sorted(sums) != [("0", "3"), ("1", "3")]:
        print("MS-MPI did not run two processes on this computer. A firewall or security program may block "
              "mpiexec and smpd; allow them for private networks, or install the build on one processor core (--one-core).",
              flush=True)
        return done.returncode or 2
    print("MS-MPI ran two processes.", flush=True)
    return 0


def _end_tree(process: subprocess.Popen) -> None:
    """End *process* and every process it started (``taskkill /T`` on Windows)."""
    if _windows():
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        process.kill()
    try:
        process.communicate(timeout=10)
    except subprocess.TimeoutExpired:                   # pragma: no cover - taskkill failed
        process.kill()


def _extract(archive: Path, target: Path) -> None:
    with tarfile.open(archive) as tar:
        if hasattr(tarfile, "data_filter"):           # Python 3.12, and the security releases of 3.10 and 3.11
            tar.extractall(target, filter="data")
            return
        root = target.resolve()
        for member in tar.getmembers():
            if not (member.isfile() or member.isdir()) or not (root / member.name).resolve().is_relative_to(root):
                raise ValueError(f"{archive} has an unexpected member {member.name}")
        tar.extractall(target)                          # pragma: no cover - checked above, Python without filters


def metis(home: Path, env: Path, jobs: int) -> int:
    """Download METIS into ``metis`` beside *home* and compile it as a static library (the partitioner PARTEL links it)."""
    if _metis_library(home) is not None:
        print(f"METIS is compiled in {metis_dir(home)} already and is kept.", flush=True)
        return 0
    folder = metis_dir(home)
    archive = folder / "metis-5.1.0.3.tar.gz"
    if not _download(METIS_URL, archive, METIS_SHA256):
        return 2
    source_dir = folder / METIS_FOLDER
    if not (source_dir / "CMakeLists.txt").is_file():
        try:
            _extract(archive, folder)
        except (OSError, tarfile.TarError, ValueError) as exc:
            print(f"{archive} could not be unpacked: {exc}", flush=True)
            return 2
    build = folder / "build"
    for argv in ([_cmake(env), "-S", str(source_dir), "-B", str(build), *_make_options(env),
                  "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF"],
                 [_cmake(env), "--build", str(build), "--target", "metis", "--parallel", str(max(1, jobs))]):
        code = _run(argv, env)
        if code:
            return code
    if _metis_library(home) is None:
        print(f"The build of METIS wrote no libmetis.a below {build}.", flush=True)
        return 2
    return 0


def configure_argv(home: Path, env: Path, mpi: bool = False) -> list[str]:
    """The CMake command of the tested build, with the compilers of *env*; with *mpi* the parallel one."""
    fortran = _tool(env, "x86_64-w64-mingw32-gfortran.exe", "gfortran.exe")
    options = [o for o in CMAKE_OPTIONS if not (mpi and o == "-DUSE_MPI=OFF")]
    # parallel: --fresh, as the Windows test had it, so that FindMPI does not keep what a failed attempt cached
    return [_cmake(env), *(["--fresh"] if mpi else []), "-S", str(home), "-B", str(build_dir(home, mpi)),
            *_make_options(env),
            f"-DCMAKE_Fortran_COMPILER={fortran}", f"-DPython_EXECUTABLE={env / 'python.exe'}", *options,
            *(_mpi_options(home) if mpi else [])]


def configure(home: Path, env: Path, mpi: bool = False) -> int:
    if not _complete(home):
        print(f"There is no source code of TELEMAC in {home}.", flush=True)
        return 2
    if mpi and not (msmpi_dir(home) / "lib" / "libmsmpi.a").is_file():
        print(f"MS-MPI is not prepared in {msmpi_dir(home)}; the step 'Prepare MS-MPI' did not finish.", flush=True)
        return 2
    if mpi and _metis_library(home) is None:
        print(f"METIS is not compiled in {metis_dir(home)}; the step 'Compile METIS' did not finish.", flush=True)
        return 2
    code = _run(configure_argv(home, env, mpi), env)
    if code == 0 and mpi:
        cfg = build_dir(home, mpi) / "systel.cfg"
        text = cfg.read_text(encoding="utf-8", errors="replace") if cfg.is_file() else ""
        if not re.search(r"^\s*mpi_cmdexec\s*:", text, re.MULTILINE):
            print(f"CMake configured TELEMAC without MPI: {cfg} has no mpi_cmdexec.", flush=True)
            return 2
    return code


def build(home: Path, env: Path, jobs: int, modules: str, mpi: bool = False) -> int:
    cmake = _cmake(env)
    argv = [cmake, "--build", str(build_dir(home, mpi)), "--parallel", str(max(1, jobs))]
    targets = TARGETS.get(modules, TARGETS["2d"])
    if targets and mpi:
        targets = (*targets, *TARGETS_MPI)
    code = _run(argv + (["--target", *targets] if targets else []), env)
    if code == 0 and not targets:
        # all modules: the commands for user Fortran are a target of their own
        code = _run(argv + ["--target", "build_commands"], env)
    return code


def _home_relative(path: Path) -> str:
    """*path* below the home folder as ``%USERPROFILE%\\...``: a batch file is read in the code page of the console, and a user name with an umlaut would not survive it."""
    home = Path.home()
    try:
        rest = path.relative_to(home)
    except ValueError:
        return str(path)
    return "%USERPROFILE%" + ("\\" + str(rest) if str(rest) not in ("", ".") else "")


def environment_script(home: Path, env: Path, mpi: bool = False) -> str:
    """The text of ``configs\\pysource.win.bat`` (``pysource.win-mpi.bat``): variables only, nothing on the computer is changed.

    With *mpi*, MS-MPI's folder goes on ``PATH`` (``mpiexec`` of ``systel.cfg``), and ``MSMPI_DISABLE_ND`` keeps MS-MPI off network adapters that a desktop computer does not have, as in the Windows test.
    """
    tools = _home_relative(_bin(env))
    mpi_bin = _home_relative(msmpi_dir(home) / "bin") + ";" if mpi else ""
    lines = ["@echo off",
             "rem Written by aXqua: the environment of TELEMAC built on Windows "
             + ("(parallel, MS-MPI)." if mpi else "(no MPI)."),
             f'set "HOMETEL={_home_relative(home)}"',
             f'set "BUILD_DIR=%HOMETEL%\\builds\\{BUILD_MPI if mpi else BUILD}"',
             'set "SYSTELCFG=%BUILD_DIR%\\systel.cfg"',
             'set "USETELCFG=general"',
             *(['set "MPIRUN=mpiexec"', 'set "MSMPI_DISABLE_ND=1"'] if mpi else []),
             'set "PYTHONPATH=%HOMETEL%\\scripts\\python3;%BUILD_DIR%\\lib"',
             f'set "PATH=%HOMETEL%\\scripts\\python3;%BUILD_DIR%\\bin;{mpi_bin}{tools};%PATH%"',
             'set "PYTHONUNBUFFERED=1"']
    return "\r\n".join(lines) + "\r\n"


def script(home: Path, env: Path, mpi: bool = False) -> int:
    if not (build_dir(home, mpi) / "systel.cfg").is_file():
        print(f"The build wrote no {build_dir(home, mpi) / 'systel.cfg'}; the configuration failed.", flush=True)
        return 2
    target = home / "configs" / script_name(mpi)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = environment_script(home, env, mpi)
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
    p.add_argument("step", choices=("source", "msmpi", "metis", "configure", "build", "script", "remove"))
    p.add_argument("paths", nargs="*", type=Path, help="remove: what to remove")
    p.add_argument("--home", type=Path, help="the folder telemac-mascaret")
    p.add_argument("--env", type=Path, help="the compiler environment")
    p.add_argument("--conda-base", type=Path, help=argparse.SUPPRESS)   # not used; a plan of aXqua 1.3.1 passes it
    p.add_argument("--tag", default=TAG)
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--modules", choices=sorted(TARGETS), default="2d")
    p.add_argument("--mpi", action="store_true", help="the parallel build with MS-MPI")
    args = p.parse_args(argv)
    if args.step == "remove":
        return remove(args.paths)
    if args.home is None or args.env is None:
        p.error(f"{args.step} needs --home and --env")
    if args.step == "source":
        return source(args.home, args.env, args.tag)
    if args.step == "msmpi":
        return msmpi(args.home, args.env)
    if args.step == "metis":
        return metis(args.home, args.env, args.jobs)
    if args.step == "configure":
        return configure(args.home, args.env, args.mpi)
    if args.step == "build":
        return build(args.home, args.env, args.jobs, args.modules, args.mpi)
    return script(args.home, args.env, args.mpi)


if __name__ == "__main__":
    sys.exit(main())
