"""Installing into WSL from Windows (install/wslproxy.py, runner.start_in_wsl), on Linux.

``wsl.exe`` is replaced by a script that runs the command right here, so the Linux half (``axqua install run`` under the system's ``python3``, importing this aXqua from where the Windows side has it) really runs, and the Windows half is told it runs on Windows through the ``as_windows`` fixture.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from axqua import installcli
from axqua.core import wsl
from axqua.install import runner, wslproxy

FAKE_WSL = """#!/bin/sh
# wsl.exe [-d distro] [-u user] [--cd folder] -- program args: the program runs here, with the python3 of a fresh distribution
while [ $# -gt 0 ]; do
  case "$1" in -d|-u|--cd) shift 2 ;; --|--exec|-e) shift; break ;; *) break ;; esac
done
PATH="{distro_bin}:$PATH" exec "$@"
"""
#: python3 without site-packages: a fresh distribution has no numpy, which the conda test job's python3 has (the wheel test of 1.3.2 on GitHub, 2026-11-10)
BARE_PYTHON = """#!/bin/sh
exec "{python}" -S "$@"
"""


@pytest.fixture
def fake_wsl(tmp_path, monkeypatch):
    if shutil.which("python3") is None:
        pytest.skip("no python3 to stand in for the distribution's")
    distro_bin = tmp_path / "distro-bin"
    distro_bin.mkdir()
    (distro_bin / "python3").write_text(BARE_PYTHON.format(python=shutil.which("python3")))
    (distro_bin / "python3").chmod(0o755)
    script = tmp_path / "wsl.exe"
    script.write_text(FAKE_WSL.format(distro_bin=distro_bin))
    script.chmod(0o755)
    monkeypatch.setattr(wsl, "executable", lambda: str(script))
    monkeypatch.setattr(wsl, "distros", lambda timeout=30.0: [wsl.Distro("Ubuntu", True, 2)])
    return script


def test_the_installer_code_needs_nothing_but_the_standard_library():
    """What the distribution's python3 imports from the Windows folder of aXqua."""
    import subprocess

    code = ("import sys; sys.path.append(sys.argv[1]); before = set(sys.modules); "
            "import axqua.installcli, axqua.install.recipes, axqua.install.runner, axqua.install.telemac_examples; "
            "print(sorted(m for m in set(sys.modules) - before if not m.startswith(('axqua', '_')) "
            "and m.split('.')[0] not in sys.stdlib_module_names))")
    python = shutil.which("python3") or sys.executable
    done = subprocess.run([python, "-I", "-c", code, wslproxy.package_root()], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


def test_a_plan_on_windows_is_made_in_wsl_and_binds_in_windows(monkeypatch, capsys, fake_wsl, as_windows):
    seen = {}

    def call(args, distro, **kwargs):
        seen["args"], seen["distro"] = list(args), distro
        return {"ok": True, "data": {"target": "openfoam", "ready": True, "findings": [], "notes": [], "steps": [],
                                     "outputs": {}, "options": {"bind": False}, "folder": "/srv/stud/openfoam",
                                     "estimate": "", "host": {"description": "Ubuntu 24.04.1 LTS", "supported": True},
                                     "title": "OpenFOAM"}}

    as_windows(installcli)
    monkeypatch.setattr(wslproxy, "call", call)
    assert installcli.run_install(["plan", "openfoam", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert seen["distro"] == "Ubuntu" and seen["args"][:3] == ["install", "plan", "openfoam"]
    assert "--no-bind" in seen["args"] and "--no-visualization" in seen["args"]
    assert data["host"]["system"] == "windows" and data["distro"] == "Ubuntu"
    assert data["bind_windows"] is True and data["options"]["bind"] is False   # bound in Windows, not by the runner in WSL


def test_windows_without_wsl_gets_one_finding_for_its_administrator(monkeypatch, capsys, as_windows):
    """For OpenFOAM; TELEMAC is built in Windows itself there (tests/test_install_windows_native.py)."""
    as_windows(installcli)
    monkeypatch.setattr(wsl, "executable", lambda: None)
    assert installcli.run_install(["plan", "openfoam", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    codes = [f["code"] for f in data["findings"]]
    assert codes == ["axqua.install.windows_needs_wsl"]
    assert "administrator" in data["findings"][0]["remedy"] and "/opt" not in json.dumps(data["findings"])
    assert data["wsl"]["present"] is False and not data["ready"]


def test_postprocessors_are_windows_programs_on_windows(monkeypatch, capsys, fake_wsl, as_windows):
    as_windows(installcli)
    assert installcli.run_install(["plan", "postprocessors", "--json"]) != 0
    answer = json.loads(capsys.readouterr().out)
    assert "Windows programs" in answer["error"]["message"]


def test_an_installation_into_wsl_runs_there_and_is_entered_in_the_windows_profile(
        monkeypatch, tmp_path, fake_wsl, as_windows):
    built = tmp_path / "linux" / "telemac-mascaret" / "configs" / "pysource.ubuntu24.sh"
    plan = {"target": "telemac", "ready": True, "findings": [], "folder": str(tmp_path / "linux"),
            "options": {"bind": False}, "expects": [], "outputs": {"solvers.telemac.setup_script": str(built)},
            "steps": [{"name": "Build TELEMAC", "argv": ["sh", "-c", f"mkdir -p {built.parent} && touch {built}"],
                       "cwd": "", "env": {}, "optional": False, "admin": False}]}
    entered = {}

    def bind(outputs, probe=True):
        entered.update(outputs)
        return tmp_path / "default.axq-profile", [], set(outputs)

    as_windows(runner)
    monkeypatch.setattr(runner, "bind", bind)
    status = runner.start_in_wsl(plan, "Ubuntu", detach=False)
    assert status["state"] == "succeeded", (status.get("message"), runner.tail(status["log"]))
    assert built.is_file()                                     # the step ran on the Linux side
    assert status["distro"] == "Ubuntu" and (Path(status["folder"]) / runner.KEEPER).is_file()
    assert entered == {"solvers.telemac.setup_script": wsl.to_unc("Ubuntu", str(built))}
    assert status["bound"].endswith("default.axq-profile")
    # read again: entered once, not on every poll of the plugin
    entered.clear()
    runner.read(status["folder"])
    assert entered == {}


def test_cancel_of_an_installation_in_wsl_never_signals_a_linux_pid_in_windows(monkeypatch, tmp_path, as_windows):
    folder = tmp_path / "installs" / "x"
    folder.mkdir(parents=True)
    runner._write(folder, {"id": "x", "target": "telemac", "state": "running", "distro": "Ubuntu",
                           "pid": 4242, "windows_pid": None, "created": 0})
    as_windows(runner)
    killed = []
    monkeypatch.setattr(runner.os, "kill", lambda pid, sig: killed.append(pid), raising=False)
    answers = iter([True])        # running when asked, gone once the request is there
    monkeypatch.setattr(runner, "_alive", lambda status: next(answers, False))
    runner.cancel(folder, grace=0.1)
    assert killed == [] and (folder / "cancel.request").exists()


def test_inside_wsl_administrator_steps_run_as_the_root_of_the_distribution(monkeypatch, tmp_path):
    """No password and no administrator of Windows: the student's computer can install the packages (2026-11-06)."""
    from axqua.install import recipes

    exe = tmp_path / "wsl.exe"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-24.04")
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setenv("DISPLAY", ":0")                   # WSLg: a display, but no polkit agent
    assert recipes.elevation() == "wsl"
    assert runner.elevated(["install", "-d", "/opt/telemac"], interactive=True) == \
        [str(exe), "-d", "Ubuntu-24.04", "-u", "root", "--exec", "install", "-d", "/opt/telemac"]
    monkeypatch.delenv("WSL_DISTRO_NAME")
    assert recipes.elevation() != "wsl"


def test_paths_from_a_windows_dialog_reach_the_distribution_as_linux_paths():
    argv = ["start", "telemac", "--folder", r"\\wsl.localhost\Ubuntu\srv\stud\opt",
            r"--salome=C:\Users\stud\Downloads\salome.tar.gz", "--reuse-openfoam", "auto"]
    assert installcli._linux_paths(argv) == [
        "start", "telemac", "--folder", "/srv/stud/opt", "--salome=/mnt/c/Users/stud/Downloads/salome.tar.gz",
        "--reuse-openfoam", "auto"]


def test_a_flag_is_removed_without_the_option_after_it():
    """`--json --folder D:\\tm` lost the folder with the flag (review of 2026-11-06)."""
    assert installcli._without(["start", "telemac", "--json", "--folder", "D:\\tm", "--distro", "Ubuntu"],
                               "--distro", "--json") == ["start", "telemac", "--folder", "D:\\tm"]


def test_a_script_of_windows_after_a_wsl_entry_is_no_wsl_entry():
    from axqua.core import profile as profiles

    old = profiles.AxquaProfile(solvers={"telemac": profiles.SolverBinding(
        setup_script=r"\\wsl.localhost\Ubuntu\opt\telemac\configs\pysource.sh", environment="wsl", distro="Ubuntu")})
    new = profiles.rebound(old, "telemac", r"C:\telemac\configs\pysource.win.bat")
    assert new.environment is None and new.distro is None


def test_inside_wsl_only_the_axqua_package_is_read_from_the_windows_folder(tmp_path):
    """The Windows folder holds packages of another Python; they must not shadow a module of the distribution."""
    import subprocess

    root = tmp_path / "site-packages"
    shutil.copytree(Path(wslproxy.package_root()) / "axqua", root / "axqua",
                    ignore=shutil.ignore_patterns("__pycache__", "vendor"))
    (root / "json.py").write_text("raise SystemExit('the Windows folder shadowed json')\n")
    python = shutil.which("python3") or sys.executable
    code = wslproxy.LAUNCH.format(root=str(root))
    # -S: without site-packages, as the python3 of a fresh distribution (no numpy; the wheel test of 1.3.2 on GitHub)
    done = subprocess.run([python, "-I", "-S", "-c", code, "install", "list", "--json"], capture_output=True,
                          text=True, env={**os.environ, "AXQUA_DATA_DIR": str(tmp_path / "data")})
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["ok"] is True


#: What wsl.exe of Windows 11 24H2 wrote on the test computer of 2026-11-09, where WSL is not installed: UTF-16LE also with WSL_UTF8=1, lines ending in \r\r\n, exit code 50 for --status and 1 for -l.
NOT_INSTALLED = ("The Windows Subsystem for Linux is not installed. You can install by running 'wsl.exe --install'.\r\r\n"
                 "For more information please visit https://aka.ms/wslinstall\r\r\n").encode("utf-16-le")


def test_windows_with_the_wsl_stub_but_no_wsl_gets_the_finding_for_its_administrator(
        monkeypatch, tmp_path, capsys, as_windows):
    (tmp_path / "said.bin").write_bytes(NOT_INSTALLED)
    exe = tmp_path / "wsl.exe"
    exe.write_text(f'#!/bin/sh\ncat "{tmp_path}/said.bin"\ncase "$1" in --status) exit 50 ;; *) exit 1 ;; esac\n')
    exe.chmod(0o755)
    monkeypatch.setattr(wsl, "executable", lambda: str(exe))
    status = wsl.status()
    assert status.distros == [] and not status.usable
    assert status.message.splitlines() == [
        "The Windows Subsystem for Linux is not installed. You can install by running 'wsl.exe --install'.",
        "For more information please visit https://aka.ms/wslinstall"]
    as_windows(installcli)
    assert installcli.run_install(["plan", "openfoam", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert [f["code"] for f in data["findings"]] == ["axqua.install.windows_needs_wsl"]
    assert "administrator" in data["findings"][0]["remedy"] and not data["ready"]
    assert "C:\\" not in json.dumps(data["findings"]) and "/opt" not in json.dumps(data["findings"])
    assert data["folder"] == ""                               # not C:\Users\<name>\opt, where nothing is installed
    assert installcli.run_install(["overview", "--json"]) == 0
    targets = json.loads(capsys.readouterr().out)["data"]["targets"]
    assert all(t["default_folder"] == "" and t["default_folders"] == {} for t in targets if t["target"] != "telemac")
    telemac = next(t for t in targets if t["target"] == "telemac")      # built in Windows itself, in the user folder
    assert telemac["default_folder"] == str(Path.home() / "opt" / "telemac")
