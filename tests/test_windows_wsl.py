"""aXqua in Windows, TELEMAC and OpenFOAM inside WSL (the user's decision of 2026-11-06), simulated on Linux.

Nothing here starts wsl.exe: the calls are replaced, and modules are told they run on Windows through the ``as_windows`` fixture (``os.name`` of one module, never of :mod:`os` itself).
"""
from __future__ import annotations

import subprocess

from axqua.core import installation, machine, profile as profiles, wsl
from axqua.core.environment import EnvironmentKind, SolverEnvironment
from axqua.jobs import launcher as launcher_mod

UNC = r"\\wsl.localhost\Ubuntu-24.04\opt\telemac\telemac-mascaret\configs\pysource.debian12.sh"


def test_a_script_picked_inside_wsl_is_entered_as_a_wsl_installation():
    binding = profiles.rebound(None, "telemac", UNC)
    assert binding.environment == "wsl" and binding.distro == "Ubuntu-24.04"
    assert str(binding.setup_script) == UNC
    env = SolverEnvironment(kind=binding.environment, distro=binding.distro,
                            setup_script=binding.setup_script)
    assert env.script == "/opt/telemac/telemac-mascaret/configs/pysource.debian12.sh"
    assert binding.as_dict()["setup_script"] == UNC          # written as Windows names it


def test_the_variables_of_a_wsl_script_point_at_files_windows_can_open(tmp_path):
    seen = installation.seen_from_windows(
        {"PATH": "/opt/telemac/scripts/python3:/usr/bin:/mnt/c/Windows",
         "HOMETEL": "/opt/telemac/telemac-mascaret", "USETELCFG": "debian12",
         "SYSTELCFG": "/opt/telemac/telemac-mascaret/configs/systel.cfg"}, "Ubuntu")
    assert seen["HOMETEL"] == r"\\wsl.localhost\Ubuntu\opt\telemac\telemac-mascaret"
    assert seen["USETELCFG"] == "debian12"
    parts = seen["PATH"].split(";")
    assert parts[0] == r"\\wsl.localhost\Ubuntu\opt\telemac\scripts\python3"
    assert parts[-1] == "C:\\Windows"
    # a Linux program has no extension: it is found by its file, not by shutil.which
    (tmp_path / "telemac2d.py").write_text("")
    found = installation._which("telemac2d.py", {"PATH": str(tmp_path), installation.SEEN_FROM_WINDOWS: "1"})
    assert found == str(tmp_path / "telemac2d.py")


def test_an_installation_inside_wsl_is_discovered_on_windows(monkeypatch, tmp_path, as_windows):
    root = tmp_path / "Ubuntu-24.04"
    script = root / "home" / "stud" / "opt" / "telemac-mascaret" / "configs" / "pysource.debian12.sh"
    script.parent.mkdir(parents=True)
    script.write_text("export HOMETEL=x\n")
    (script.parent / "pysource.template.sh").write_text("")
    as_windows(machine)
    monkeypatch.setattr(machine, "_WSL_DISTROS", ["Ubuntu-24.04"])
    monkeypatch.setattr(wsl, "to_unc", lambda distro, path: str(tmp_path / distro))
    assert machine._discover_in_wsl("telemac") == script
    assert machine._discover_in_wsl("paraview") is None


def test_a_job_with_its_solver_in_wsl_runs_in_windows(monkeypatch, as_windows):
    """The wsl launcher started the Windows python.exe inside bash (review of 2026-11-06)."""
    as_windows(launcher_mod)
    env = SolverEnvironment(kind="wsl", distro="Ubuntu", setup_script="/opt/of/shell-rc.sh")
    assert launcher_mod.select_launcher(env).name == "windows"
    assert launcher_mod.select_launcher(env, prefer="wsl").name == "windows"


def test_telemac_inside_wsl_runs_with_the_distributions_python_and_linux_paths(monkeypatch):
    from axqua import env as env_mod
    from axqua.config import TelemacEnv

    telemac = TelemacEnv(pysource="/opt/telemac/configs/pysource.sh")
    runtime = env_mod.TelemacRuntime(telemac)
    runtime.environment = SolverEnvironment(kind=EnvironmentKind.WSL, distro="Ubuntu",
                                            setup_script="/opt/telemac/configs/pysource.sh")
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, b"done", b"")

    monkeypatch.setattr(env_mod.subprocess, "run", fake_run)
    monkeypatch.delenv("AXQUA_TELEMAC_PYTHON", raising=False)
    runtime.run_solver(r"C:\Users\stud\case\steady2d.cas", cwd=r"C:\Users\stud\case")
    payload = seen["args"][-1]
    assert seen["args"][:4] == ["wsl.exe", "-d", "Ubuntu", "--"] or seen["args"][1:4] == ["-d", "Ubuntu", "--"]
    assert "cd /mnt/c/Users/stud/case && python3 " in payload
    assert "'/mnt/c/Users/stud/case/steady2d.cas'" in payload or "/mnt/c/Users/stud/case/steady2d.cas" in payload
    assert "python.exe" not in payload
