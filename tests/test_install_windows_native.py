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
