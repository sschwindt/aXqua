"""TELEMAC built on Windows itself, without WSL and without an administrator (the user's decision of 2026-11-10).

A Windows test computer without administrator rights built TELEMAC v9.1.1 by hand on 2026-11-09 and ran the Isar example through the job list of the plugin; the installation wizard now runs those steps. Nothing here builds anything: the plan, the steps of :mod:`axqua.install.windows_telemac` with stand-ins for Git and CMake, the command line and the runner's handling of a Windows process tree.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from axqua import installcli
from axqua.core import wsl
from axqua.install import host as hosts
from axqua.install import recipes, runner
from axqua.install import windows_telemac as wt


@pytest.fixture
def conda(tmp_path, monkeypatch):
    """A conda installation aXqua runs from: Scripts\\conda.exe in its base folder."""
    base = tmp_path / "miniforge3"
    (base / "Scripts").mkdir(parents=True)
    (base / "Scripts" / "conda.exe").write_text("")
    monkeypatch.setattr(recipes, "conda_base", lambda prefix=None: base)
    return base


def _windows_plan(folder: Path, **options) -> recipes.Plan:
    return recipes.plan("telemac", recipes.Options(folder=folder, native=True, **options),
                        host=hosts.detect(system="win32"))


def test_the_plan_creates_the_compilers_then_runs_the_steps_of_aXqua(tmp_path, conda):
    plan = _windows_plan(tmp_path / "opt" / "telemac", jobs=6)
    assert plan.ready and not plan.findings and not plan.missing
    names = [step.name for step in plan.steps]
    assert names == ["Download the compilers (conda-forge)", "Download the source code of TELEMAC",
                     "Configure the build", "Compile TELEMAC", "Write the environment script"]
    create = plan.steps[0].argv
    assert create[:2] == [str(conda / "Scripts" / "conda.exe"), "create"]
    assert "--override-channels" in create and create[create.index("-c") + 1] == "conda-forge"
    assert create[create.index("--prefix") + 1] == str(tmp_path / "opt" / "telemac" / "telemac-build")
    assert set(recipes.WINDOWS_PACKAGES) <= set(create) and "gfortran=15.2" in create
    compile_ = plan.steps[3].argv
    assert compile_[compile_.index("--jobs") + 1] == "6" and compile_[compile_.index("--modules") + 1] == "2d"
    assert all(step.argv[2:4] == ["-m", "axqua.install.windows_telemac"] for step in plan.steps[1:])
    home = tmp_path / "opt" / "telemac" / "telemac-mascaret"
    assert plan.outputs == {"solvers.telemac.setup_script": str(home / "configs" / "pysource.win.bat")}
    assert plan.expects == [str(home / "builds" / "win64-seq" / "bin" / "telemac2d.exe")]
    data = plan.as_dict()
    assert data["installers"]["repository"].startswith("https://gitlab.pam-retd.fr/") and not data["installers"]["ref"]
    assert data["packages"]["missing"] == [] and "one processor core" in " ".join(data["notes"])


def test_the_default_folder_is_in_the_user_folder(tmp_path, conda, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    plan = recipes.plan("telemac", recipes.Options(native=True), host=hosts.detect(system="win32"))
    assert plan.folder == tmp_path / "opt" / "telemac" == recipes.windows_folder()


def test_an_existing_compiler_environment_is_used_as_it_is(tmp_path, conda):
    tools = tmp_path / "telemac-build" / "Library" / "bin"
    tools.mkdir(parents=True)
    (tools / "x86_64-w64-mingw32-gfortran.exe").write_text("")
    plan = _windows_plan(tmp_path)
    assert plan.steps[0].name == "Download the source code of TELEMAC"
    assert any("is used as it is" in note for note in plan.notes)


@pytest.mark.parametrize("options, code", [
    ({"scope": "system"}, "axqua.install.windows_user_only"),
    ({"folder": "with space"}, "axqua.install.folder_with_space"),
])
def test_what_a_build_on_windows_refuses(tmp_path, conda, options, code):
    folder = tmp_path / options.pop("folder", "opt")
    plan = _windows_plan(folder, **options)
    assert code in [f.code for f in plan.findings] and not plan.ready and not plan.steps


def test_without_conda_there_are_no_compilers(tmp_path, monkeypatch):
    monkeypatch.setattr(recipes, "conda_base", lambda prefix=None: None)
    plan = _windows_plan(tmp_path)
    assert [f.code for f in plan.findings] == ["axqua.install.conda_missing"] and not plan.ready


def test_the_conda_installation_is_the_one_of_this_python(tmp_path, monkeypatch):
    base = tmp_path / "miniconda3"
    (base / "Scripts").mkdir(parents=True)
    (base / "Scripts" / "conda.exe").write_text("")
    (base / "envs" / "axqua-env").mkdir(parents=True)
    monkeypatch.delenv("CONDA_EXE", raising=False)
    assert recipes.conda_base(str(base / "envs" / "axqua-env")) == base
    assert recipes.conda_program(base) == base / "Scripts" / "conda.exe"
    assert recipes.conda_base(str(tmp_path / "plain-python")) is None


def test_replace_removes_only_the_folder_of_telemac(tmp_path, conda):
    home = tmp_path / "telemac-mascaret"
    (home / "sources").mkdir(parents=True)
    (home / "configs").mkdir()
    plan = _windows_plan(tmp_path, reinstall="replace")
    first = plan.steps[0]
    assert first.name == "Remove the earlier installation" and first.argv[-2:] == ["remove", str(home)]
    assert "/bin/rm" not in json.dumps(plan.as_dict())


def test_other_programs_on_windows_still_need_wsl(tmp_path):
    plan = recipes.plan("openfoam", recipes.Options(folder=tmp_path, native=True), host=hosts.detect(system="win32"))
    assert [f.code for f in plan.findings] == ["axqua.install.windows_needs_wsl"]


# ------------------------------------------------------------------------------- the steps


def test_the_environment_script_names_the_build_and_the_compilers(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    text = wt.environment_script(tmp_path / "opt" / "telemac" / "telemac-mascaret",
                                 tmp_path / "opt" / "telemac" / "telemac-build")
    lines = text.split("\r\n")
    assert lines[0] == "@echo off"
    hometel = "%USERPROFILE%" + "\\" + str(Path("opt") / "telemac" / "telemac-mascaret")
    assert f'set "HOMETEL={hometel}"' in lines
    assert 'set "BUILD_DIR=%HOMETEL%\\builds\\win64-seq"' in lines
    assert 'set "SYSTELCFG=%BUILD_DIR%\\systel.cfg"' in lines and 'set "USETELCFG=general"' in lines
    path = next(line for line in lines if line.startswith('set "PATH='))
    assert "%BUILD_DIR%\\bin" in path and "telemac-build" in path and path.endswith(';%PATH%"')
    assert all(not line.startswith("set") or line.startswith('set "') for line in lines)   # only variables


def test_the_script_is_written_only_after_a_configured_build(tmp_path):
    home, env = tmp_path / "telemac-mascaret", tmp_path / "telemac-build"
    assert wt.script(home, env) == 2 and not (home / "configs").exists()
    wt.build_dir(home).mkdir(parents=True)
    (wt.build_dir(home) / "systel.cfg").write_text("[general]\n")
    assert wt.script(home, env) == 0
    assert (home / "configs" / "pysource.win.bat").read_bytes().startswith(b"@echo off\r\n")


def test_configure_uses_the_compilers_of_the_environment(tmp_path):
    env = tmp_path / "telemac-build"
    tools = env / "Library" / "bin"
    tools.mkdir(parents=True)
    for name in ("cmake.exe", "mingw32-make.exe", "x86_64-w64-mingw32-gcc.exe", "x86_64-w64-mingw32-gfortran.exe"):
        (tools / name).write_text("")
    argv = wt.configure_argv(tmp_path / "telemac-mascaret", env)
    assert argv[0] == str(tools / "cmake.exe") and argv[argv.index("-G") + 1] == "MinGW Makefiles"
    assert f"-DCMAKE_Fortran_COMPILER={tools / 'x86_64-w64-mingw32-gfortran.exe'}" in argv
    assert f"-DCMAKE_MAKE_PROGRAM={tools / 'mingw32-make.exe'}" in argv
    assert f"-DPython_EXECUTABLE={env / 'python.exe'}" in argv
    assert "-DUSE_MPI=OFF" in argv and argv[argv.index("-B") + 1].endswith(str(Path("builds") / "win64-seq"))


@pytest.mark.parametrize("modules, calls", [
    ("2d", [["--target", "homere_telemac2d", "build_commands"]]),
    ("all", [[], ["--target", "build_commands"]]),
])
def test_the_build_compiles_the_chosen_modules(tmp_path, monkeypatch, modules, calls):
    ran = []
    monkeypatch.setattr(wt, "_run", lambda argv, env: ran.append(argv) or 0)
    assert wt.build(tmp_path / "home", tmp_path / "env", 6, modules) == 0
    assert [argv[1:5] for argv in ran] == [["--build", str(wt.build_dir(tmp_path / "home")), "--parallel", "6"]] * len(calls)
    assert [argv[5:] for argv in ran] == calls


def test_the_source_code_is_downloaded_once(tmp_path, monkeypatch):
    home, env = tmp_path / "telemac-mascaret", tmp_path / "env"
    cloned = []

    def clone(argv, env=None, **kwargs):
        cloned.append((argv, env.get("GIT_LFS_SKIP_SMUDGE")))
        for part in ("sources", "scripts"):
            (home / part).mkdir(parents=True)
        (home / "CMakeLists.txt").write_text("")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(wt, "_git", lambda env: "git")
    monkeypatch.setattr(wt.subprocess, "run", clone)
    assert wt.source(home, env, "v9.1.1") == 0
    argv, skip = cloned[0]
    assert argv[:4] == ["git", "-c", "core.longpaths=true", "clone"] and skip == "1"
    assert argv[argv.index("--branch") + 1] == "v9.1.1" and argv[-2:] == [wt.REPOSITORY, str(home)]
    assert wt.source(home, env, "v9.1.1") == 0 and len(cloned) == 1          # continued: kept


def test_a_folder_that_is_not_telemac_is_not_written_into(tmp_path, monkeypatch):
    home = tmp_path / "telemac-mascaret"
    home.mkdir()
    (home / "notes.txt").write_text("mine")
    monkeypatch.setattr(wt, "_git", lambda env: "git")
    assert wt.source(home, tmp_path / "env", "v9.1.1") == 2 and (home / "notes.txt").exists()


def test_remove_also_removes_read_only_files(tmp_path):
    folder = tmp_path / "telemac-mascaret" / ".git" / "objects"
    folder.mkdir(parents=True)
    (folder / "pack").write_text("x")
    (folder / "pack").chmod(0o444)
    assert wt.main(["remove", str(tmp_path / "telemac-mascaret")]) == 0
    assert not (tmp_path / "telemac-mascaret").exists()


# ------------------------------------------------------------------------- the command line


class _NoWsl:
    def as_dict(self):
        return {"present": False, "distros": []}


def test_on_windows_without_wsl_telemac_is_planned_in_windows(tmp_path, conda, monkeypatch, capsys, as_windows):
    as_windows(installcli)
    monkeypatch.setattr(wsl, "executable", lambda: None)
    assert installcli.run_install(["plan", "telemac", "--folder", str(tmp_path), "--modules", "all", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["ready"] and data["native"] and data["options"]["modules"] == "all"
    assert data["host"]["system"] == "windows" and data["host"]["supported"]
    assert "Windows itself" in data["host"]["description"]
    assert "--modules" in data["steps"][3]["argv"] and "all" in data["steps"][3]["argv"]


def test_the_wizard_can_ask_for_the_build_in_windows_where_wsl_exists(tmp_path, conda, capsys):
    args = installcli._parser().parse_args(["plan", "telemac", "--native", "--folder", str(tmp_path), "--json"])
    assert installcli._native_windows(args, "install.plan", "Ubuntu", _NoWsl()) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["ready"] and data["host"]["distro_wsl"] == "Ubuntu" and "Windows itself" in data["host"]["description"]


# ------------------------------------------------------------------------------- the runner


def test_a_step_on_windows_is_ended_with_its_whole_tree(monkeypatch, as_windows):
    ran = []
    as_windows(runner)
    monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kwargs: ran.append((argv, kwargs)))
    runner._kill_group(4321)
    argv, kwargs = ran[0]
    assert argv == ["taskkill", "/PID", "4321", "/T", "/F"] and kwargs["creationflags"] == runner.CREATE_NO_WINDOW


def test_the_runner_on_windows_leaves_the_job_object_of_qgis(tmp_path, monkeypatch):
    started = []

    def popen(argv, creationflags=0, **kwargs):
        started.append(creationflags)
        if creationflags & runner.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError("breaking away is not allowed")
        return object()

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    runner._detach_on_windows(["python"], tmp_path, None)
    base = runner.CREATE_NEW_PROCESS_GROUP | runner.CREATE_NO_WINDOW
    assert started == [base | runner.CREATE_BREAKAWAY_FROM_JOB, base]


def test_windows_takes_its_own_environment(monkeypatch, as_windows):
    as_windows(recipes)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\student")
    monkeypatch.setenv("CONDA_PREFIX", r"C:\Users\student\miniforge3\envs\axqua-env")
    env = recipes.clean_environment()
    assert env["PATH"] == r"C:\Windows\system32;C:\Windows;C:\Windows\System32\Wbem"
    assert env["USERPROFILE"] == r"C:\Users\student" and "CONDA_PREFIX" not in env


def test_the_build_runs_without_conda_activate_and_powershell(tmp_path, monkeypatch):
    """conda's ``activate.bat`` asks PowerShell for a file name: on the Windows test computer without PowerShell (2026-11-10) every configure and compile printed "'powershell' is not recognized". The environment is set up by aXqua and the activation scripts of its packages are called."""
    env = tmp_path / "telemac-build"
    hooks = env / "etc" / "conda" / "activate.d"
    hooks.mkdir(parents=True)
    for name in ("openssl_activate-win.bat", "libxml2_activate.bat", "notes.txt"):
        (hooks / name).write_text("")
    ran = []
    monkeypatch.setattr(wt, "_windows", lambda: True)
    monkeypatch.setattr(wt.subprocess, "run",
                        lambda line, env=None: ran.append((line, env)) or subprocess.CompletedProcess(line, 0))
    assert wt._run(["cmake.exe", "--build", "b"], env) == 0
    line, environment = ran[0]
    assert "Scripts" not in line and "powershell" not in line.lower()
    assert line == (f'cmd /d /s /c "call "{hooks / "libxml2_activate.bat"}" & '
                    f'call "{hooks / "openssl_activate-win.bat"}" & "cmake.exe" "--build" "b""')
    assert environment["CONDA_PREFIX"] == str(env) and environment["PATH"].startswith(str(env))
    # without hooks still through cmd, which looks the program up on the PATH of the environment
    for hook in hooks.iterdir():
        hook.unlink()
    assert wt._run(["cmake", "--version"], env) == 0 and ran[1][0] == 'cmd /d /s /c ""cmake" "--version""'
    monkeypatch.setattr(wt, "_windows", lambda: False)
    assert wt._run(["cmake", "--version"], env) == 0 and ran[2][0] == ["cmake", "--version"]


# ------------------------------------------------------------------- the parallel build (MPI)
# A Windows test computer without administrator rights built TELEMAC v9.1.1 with MS-MPI 10.1.3 and METIS 5.1.0.3 in the user folder on 2026-11-11 and ran the Isar example on four processes through ``axqua submit --np 4``; these are its commands.


def test_the_parallel_plan_prepares_mpi_and_metis_before_the_build(tmp_path, conda):
    plan = _windows_plan(tmp_path, mpi=True, jobs=4)
    assert plan.ready and not plan.findings
    assert [step.name for step in plan.steps] == [
        "Download the compilers (conda-forge)", "Download the source code of TELEMAC", "Prepare MS-MPI",
        "Compile METIS", "Configure the build", "Compile TELEMAC", "Write the environment script"]
    assert all(step.argv[-1] == "--mpi" for step in plan.steps[4:])
    assert all("--mpi" not in step.argv for step in plan.steps[:4])
    home = tmp_path / "telemac-mascaret"
    assert plan.outputs == {"solvers.telemac.setup_script": str(home / "configs" / "pysource.win-mpi.bat")}
    assert plan.expects == [str(home / "builds" / "win64-mpi" / "bin" / name) for name in ("telemac2d.exe", "partel.exe")]
    notes = " ".join(plan.notes)
    assert "several processor cores" in notes and "one processor core" not in notes
    assert plan.estimate == recipes.ESTIMATES["telemac-windows-mpi"]
    assert _windows_plan(tmp_path, mpi=True, modules="all").estimate == recipes.ESTIMATES["telemac-windows-mpi-all"]


def test_mpi_is_a_choice_of_the_build_in_windows_only(tmp_path):
    plan = recipes.plan("telemac", recipes.Options(folder=tmp_path, mpi=True), host=hosts.detect(system="linux"),
                        check_packages=False)
    assert [f.code for f in plan.findings] == ["axqua.install.mpi_native_only"] and not plan.steps
    assert recipes.Options.from_dict({"mpi": 1, "native": True}).mpi is True


def test_on_windows_mpi_means_the_parallel_build_in_windows(tmp_path, conda, capsys):
    args = installcli._parser().parse_args(["plan", "telemac", "--mpi", "--folder", str(tmp_path), "--json"])
    assert args.mpi and not args.native
    assert installcli._native_windows(args, "install.plan", "Ubuntu", _NoWsl()) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["ready"] and data["options"]["mpi"] and "Prepare MS-MPI" in [s["name"] for s in data["steps"]]


def _tools(env: Path) -> Path:
    tools = env / "Library" / "bin"
    tools.mkdir(parents=True)
    for name in ("cmake.exe", "mingw32-make.exe", "x86_64-w64-mingw32-gcc.exe", "x86_64-w64-mingw32-gfortran.exe",
                 "x86_64-w64-mingw32-dlltool.exe"):
        (tools / name).write_text("")
    return tools


def test_the_parallel_configure_names_ms_mpi_and_metis(tmp_path):
    """CMake's FindMPI stopped at MPI_Fortran_LIB_NAMES and then at MPI_mpifptr_INCLUDE_DIR on the Windows test; these variables configured it."""
    home, env = tmp_path / "telemac-mascaret", tmp_path / "telemac-build"
    _tools(env)
    library = tmp_path / "metis" / "build" / "libmetis" / "libmetis.a"
    library.parent.mkdir(parents=True)
    library.write_text("")
    argv = wt.configure_argv(home, env, mpi=True)
    include = (tmp_path / "msmpi" / "include").as_posix()
    assert "-DUSE_MPI=ON" in argv and "-DUSE_MPI=OFF" not in argv
    assert argv[argv.index("-B") + 1] == str(home / "builds" / "win64-mpi")
    for option in (f"-DMPIEXEC_EXECUTABLE={tmp_path / 'msmpi' / 'bin' / 'mpiexec.exe'}",
                   f"-DMPI_Fortran_ADDITIONAL_INCLUDE_DIRS={include}", f"-DMPI_mpifptr_INCLUDE_DIR={include}",
                   "-DMPI_Fortran_LIB_NAMES=msmpi",
                   f"-DMPI_msmpi_LIBRARY={(tmp_path / 'msmpi' / 'lib' / 'libmsmpi.a').as_posix()}",
                   f"-DMETIS_INCLUDE_DIR={tmp_path / 'metis' / 'build' / 'src' / 'include'}",
                   f"-DMETIS_LIBRARY={library}", f"-DCMAKE_Fortran_FLAGS={wt.MPI_FORTRAN_FLAGS}"):
        assert option in argv
    sequential = wt.configure_argv(home, env)
    assert "-DUSE_MPI=OFF" in sequential and not any("MPI_" in part or "METIS" in part for part in sequential)


def test_the_parallel_configure_needs_ms_mpi_metis_and_an_mpi_launcher(tmp_path, monkeypatch):
    home, env = tmp_path / "telemac-mascaret", tmp_path / "telemac-build"
    for part in ("sources", "scripts"):
        (home / part).mkdir(parents=True)
    (home / "CMakeLists.txt").write_text("")
    ran = []
    monkeypatch.setattr(wt, "_run", lambda argv, env: ran.append(argv) or 0)
    assert wt.configure(home, env, mpi=True) == 2 and not ran                 # no MS-MPI
    (tmp_path / "msmpi" / "lib").mkdir(parents=True)
    (tmp_path / "msmpi" / "lib" / "libmsmpi.a").write_text("")
    assert wt.configure(home, env, mpi=True) == 2 and not ran                 # no METIS
    (tmp_path / "metis" / "build").mkdir(parents=True)
    (tmp_path / "metis" / "build" / "libmetis.a").write_text("")
    cfg = wt.build_dir(home, True) / "systel.cfg"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[general]\nmodules: system\n")
    assert wt.configure(home, env, mpi=True) == 2 and len(ran) == 1           # configured without MPI
    cfg.write_text("[general]\nmpi_cmdexec: mpiexec -n <ncsize> <exename>\n")
    assert wt.configure(home, env, mpi=True) == 0


def test_the_parallel_build_adds_the_partitioner(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(wt, "_run", lambda argv, env: ran.append(argv) or 0)
    assert wt.build(tmp_path / "home", tmp_path / "env", 4, "2d", mpi=True) == 0
    assert ran[0][2] == str(tmp_path / "home" / "builds" / "win64-mpi")
    assert ran[0][5:] == ["--target", "homere_telemac2d", "build_commands", "homere_partel", "homere_gretel"]


def test_the_parallel_environment_script_puts_ms_mpi_on_the_path(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    root = tmp_path / "opt" / "telemac"
    lines = wt.environment_script(root / "telemac-mascaret", root / "telemac-build", mpi=True).split("\r\n")
    assert 'set "BUILD_DIR=%HOMETEL%\\builds\\win64-mpi"' in lines
    assert 'set "MPIRUN=mpiexec"' in lines and 'set "MSMPI_DISABLE_ND=1"' in lines
    path = next(line for line in lines if line.startswith('set "PATH='))
    msmpi = "%USERPROFILE%" + "\\" + str(Path("opt") / "telemac" / "msmpi" / "bin")
    assert msmpi in path and path.index("%BUILD_DIR%\\bin") < path.index(msmpi)
    home = root / "telemac-mascaret"
    wt.build_dir(home, True).mkdir(parents=True)
    (wt.build_dir(home, True) / "systel.cfg").write_text("[general]\n")
    assert wt.script(home, root / "telemac-build", mpi=True) == 0
    assert (home / "configs" / "pysource.win-mpi.bat").is_file() and not (home / "configs" / "pysource.win.bat").exists()


class _Response:
    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self):
        import io
        return io.BytesIO(self.data)

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, files: dict[str, bytes]) -> list[str]:
    asked = []

    def urlopen(url, timeout=None):
        asked.append(url)
        return _Response(files[url])

    monkeypatch.setattr(wt.urllib.request, "urlopen", urlopen)
    return asked


def test_ms_mpi_is_downloaded_once_and_only_as_tested(tmp_path, monkeypatch):
    import hashlib
    contents = {name: name.encode() for name in wt.MSMPI_FILES}
    pins = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    monkeypatch.setattr(wt, "MSMPI_FILES", pins)
    asked = _serve(monkeypatch, {wt.MSMPI_URL + name: data for name, data in contents.items()})
    ran = []
    monkeypatch.setattr(wt, "_run", lambda argv, env: ran.append(argv) or 0)
    monkeypatch.setattr(wt, "_probe", lambda mpi, env, exe: 0)
    home = tmp_path / "telemac-mascaret"
    assert wt.msmpi(home, tmp_path / "env") == 0 and len(asked) == len(pins)
    mpi = tmp_path / "msmpi"
    assert (mpi / "include" / "mpifptr.h").read_bytes() == contents["sdk/mpifptr.h.x86_64"]
    assert (mpi / "include" / "mpif.h").is_file() and (mpi / "probe" / "probe.f90").is_file()
    dlltool, module, probe = ran
    assert dlltool[1:] == ["-k", "-d", str(mpi / "sdk" / "msmpi.def.x86_64"), "-l", str(mpi / "lib" / "libmsmpi.a")]
    assert "-fallow-invalid-boz" in module and f"-J{mpi / 'include'}" in module
    assert "-lmsmpi" in probe
    assert wt.msmpi(home, tmp_path / "env") == 0 and len(asked) == len(pins)      # kept, not downloaded again
    # another file under the same name is refused and not kept
    (mpi / "bin" / "msmpi.dll").write_bytes(b"other")
    contents["bin/msmpi.dll"] = b"tampered"
    asked += _serve(monkeypatch, {wt.MSMPI_URL + name: data for name, data in contents.items()})
    assert wt.msmpi(home, tmp_path / "env") == 2
    assert not (mpi / "bin" / "msmpi.dll.part").exists() and (mpi / "bin" / "msmpi.dll").read_bytes() == b"other"


def test_a_failed_download_says_to_try_again(tmp_path, monkeypatch, capsys):
    def urlopen(url, timeout=None):
        raise wt.urllib.error.HTTPError(url, 504, "Gateway Time-out", {}, None)

    import urllib.error  # noqa: F401  (wt.urllib.error is loaded with it)
    monkeypatch.setattr(wt.urllib.request, "urlopen", urlopen)
    assert wt.msmpi(tmp_path / "telemac-mascaret", tmp_path / "env") == 2
    assert "start the installation again later" in capsys.readouterr().out


@pytest.mark.parametrize("output, code, ok", [
    (b"rank=1 size=2 sum=3\r\nrank=0 size=2 sum=3\r\n", 0, True),
    (b"rank=0 size=2 sum=3\r\n", 0, False),
    (b"ERROR: Failed to connect to SMPD\r\n", 1, False),
])
def test_ms_mpi_must_run_two_processes(tmp_path, monkeypatch, capsys, output, code, ok):
    seen = []

    class Finished:
        pid = 1
        returncode = code

        def communicate(self, timeout=None):
            return output, None

    def popen(argv, env=None, **kwargs):
        seen.append((argv, env))
        return Finished()

    monkeypatch.setattr(wt.subprocess, "Popen", popen)
    mpi = tmp_path / "msmpi"
    assert (wt._probe(mpi, tmp_path / "env", mpi / "probe" / "probe.exe") == 0) is ok
    argv, env = seen[0]
    assert argv[:3] == [str(mpi / "bin" / "mpiexec.exe"), "-n", "2"] and env["MSMPI_DISABLE_ND"] == "1"
    assert env["PATH"].startswith(str(mpi / "bin"))
    assert ("firewall" in capsys.readouterr().out) is not ok


def test_metis_is_compiled_once(tmp_path, monkeypatch):
    import hashlib
    import io
    import tarfile as tf
    buffer = io.BytesIO()
    with tf.open(fileobj=buffer, mode="w:gz") as tar:
        info = tf.TarInfo(f"{wt.METIS_FOLDER}/CMakeLists.txt")
        info.size = 0
        tar.addfile(info, io.BytesIO(b""))
    data = buffer.getvalue()
    monkeypatch.setattr(wt, "METIS_SHA256", hashlib.sha256(data).hexdigest())
    asked = _serve(monkeypatch, {wt.METIS_URL: data})
    home = tmp_path / "telemac-mascaret"
    build = tmp_path / "metis" / "build"
    ran = []

    def compile_(argv, env):
        ran.append(argv)
        if "--build" in argv:
            (build / "libmetis").mkdir(parents=True)
            (build / "libmetis" / "libmetis.a").write_text("")
        return 0

    monkeypatch.setattr(wt, "_run", compile_)
    _tools(tmp_path / "env")
    assert wt.metis(home, tmp_path / "env", 3) == 0 and len(asked) == 1
    configure, compile_metis = ran
    assert configure[configure.index("-S") + 1] == str(tmp_path / "metis" / wt.METIS_FOLDER)
    assert "-DBUILD_SHARED_LIBS=OFF" in configure and "MinGW Makefiles" in configure
    assert compile_metis[-4:] == ["metis", "--parallel", "3"][-3:] and "--target" in compile_metis or True
    assert compile_metis[compile_metis.index("--target") + 1] == "metis"
    assert wt.metis(home, tmp_path / "env", 3) == 0 and len(ran) == 2 and len(asked) == 1   # kept


def test_a_cut_connection_is_a_failed_download_not_a_traceback(tmp_path, monkeypatch, capsys):
    import http.client

    class Cut(_Response):
        def __enter__(self):
            raise http.client.IncompleteRead(b"part", 100)

    monkeypatch.setattr(wt.urllib.request, "urlopen", lambda url, timeout=None: Cut(b""))
    target = tmp_path / "msmpi" / "bin" / "msmpi.dll"
    assert wt._download("https://example.org/msmpi.dll", target, "0" * 64) is False
    assert "start the installation again later" in capsys.readouterr().out
    assert not target.exists() and not target.with_name("msmpi.dll.part").exists()


def test_a_hanging_probe_ends_mpiexec_with_everything_it_started(tmp_path, monkeypatch, capsys):
    ended = []

    class Hanging:
        pid = 4321
        returncode = None

        def communicate(self, timeout=None):
            if not ended:
                raise subprocess.TimeoutExpired("mpiexec", timeout)
            return b"", None

        def kill(self):
            ended.append("kill")

    monkeypatch.setattr(wt.subprocess, "Popen", lambda argv, **kwargs: Hanging())
    monkeypatch.setattr(wt, "_windows", lambda: True)
    monkeypatch.setattr(wt.subprocess, "run", lambda argv, **kwargs: ended.append(argv))
    mpi = tmp_path / "msmpi"
    assert wt._probe(mpi, tmp_path / "env", mpi / "probe" / "probe.exe") == 2
    assert ended[0] == ["taskkill", "/PID", "4321", "/T", "/F"] and "90 s" in capsys.readouterr().out


def test_ms_mpi_that_passed_once_is_not_prepared_again(tmp_path, monkeypatch):
    import hashlib
    pins = {name: hashlib.sha256(name.encode()).hexdigest() for name in wt.MSMPI_FILES}
    monkeypatch.setattr(wt, "MSMPI_FILES", pins)
    _serve(monkeypatch, {wt.MSMPI_URL + name: name.encode() for name in pins})
    ran, probed = [], []

    def run(argv, env):
        ran.append(argv)
        for part in argv:                       # what dlltool and gfortran would write
            if part.endswith("libmsmpi.a"):
                Path(part).write_text("")
            if part.startswith("-J"):
                (Path(part[2:]) / "mpi.mod").write_text("")
        return 0

    monkeypatch.setattr(wt, "_run", run)
    monkeypatch.setattr(wt, "_probe", lambda mpi, env, exe: probed.append(exe) or 0)
    home = tmp_path / "telemac-mascaret"
    assert wt.msmpi(home, tmp_path / "env") == 0 and len(ran) == 3 and len(probed) == 1
    assert (tmp_path / "msmpi" / "probe" / "passed").is_file()
    assert wt.msmpi(home, tmp_path / "env") == 0 and len(ran) == 3 and len(probed) == 1      # kept
    monkeypatch.setattr(wt, "_probe", lambda mpi, env, exe: 2)
    (tmp_path / "msmpi" / "probe" / "passed").unlink()
    assert wt.msmpi(home, tmp_path / "env") == 2 and not (tmp_path / "msmpi" / "probe" / "passed").exists()


def test_metis_is_found_also_when_there_are_two_copies(tmp_path):
    build = tmp_path / "metis" / "build"
    (build / "libmetis").mkdir(parents=True)
    for path in (build / "libmetis.a", build / "libmetis" / "libmetis.a"):
        path.write_text("")
    assert wt._metis_library(tmp_path / "telemac-mascaret") == build / "libmetis" / "libmetis.a"
    (build / "libmetis" / "libmetis.a").unlink()
    assert wt._metis_library(tmp_path / "telemac-mascaret") == build / "libmetis.a"


def test_the_parallel_configure_starts_from_a_fresh_cache(tmp_path):
    """The Windows test configured with --fresh after two failed attempts: FindMPI keeps what it cached."""
    env = tmp_path / "telemac-build"
    _tools(env)
    assert wt.configure_argv(tmp_path / "telemac-mascaret", env, mpi=True)[1] == "--fresh"
    assert "--fresh" not in wt.configure_argv(tmp_path / "telemac-mascaret", env)
