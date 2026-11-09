"""How the TELEMAC solver is launched (:mod:`axqua.env`).

The launcher scripts (``telemac2d.py`` / ``telemac3d.py``) import numpy, which the
bare pysource shell does not necessarily provide, so they have to be run with an
interpreter that has it. These tests pin *how* that interpreter is chosen, because
the tempting shortcut - wrapping the call in ``mamba run -n <some-env>`` - bakes a
machine assumption into the package (that mamba exists, that the env has that name,
and that ``MAMBA_ROOT_PREFIX`` resolves) and breaks on any machine where one of
those is different.

Run via: mamba run -n axqua-env pytest tests/test_env.py
"""

from __future__ import annotations

import subprocess
import sys

import pytest


def _runtime(tmp_path, **kw):
    from axqua.config import TelemacEnv
    from axqua.env import TelemacRuntime

    pysource = tmp_path / "pysource.sh"
    pysource.write_text("true\n")
    return TelemacRuntime(TelemacEnv(pysource=pysource, **kw))


def _captured_command(runtime, tmp_path, monkeypatch, **kw):
    """The shell command run_solver would run, without running it."""
    seen = {}

    def fake_run(command, cwd=None, check=True, on_line=None, **kwargs):
        # **kwargs so this stub survives ShellRuntime.run growing a parameter (it has
        # gained should_stop/env for cancellable, detached runs); the assertions here
        # are about the command string, not the call signature.
        seen["command"] = command
        seen.update(kwargs)
        return None

    monkeypatch.setattr(runtime, "run", fake_run)
    runtime.run_solver("case.cas", cwd=tmp_path, **kw)
    return seen["command"]


def test_launcher_runs_under_axquas_own_interpreter(tmp_path, monkeypatch):
    """Default: the interpreter axqua is running in - which has numpy by
    construction, since axqua depends on it."""
    rt = _runtime(tmp_path)
    cmd = _captured_command(rt, tmp_path, monkeypatch, ncsize=1)

    assert sys.executable in cmd
    # the launcher is resolved inside the sourced shell, so no install layout is
    # assumed here either
    assert '"$(command -v telemac2d.py)"' in cmd
    assert "case.cas" in cmd


def test_no_conda_environment_is_hard_coded(tmp_path, monkeypatch):
    """The machine assumption this module must not make.

    Asserted on the command *shape*, not on substrings: the interpreter is
    ``sys.executable``, and its path legitimately carries the environment name
    (``.../envs/axqua-env/bin/python``, and on a conda install the string
    "conda" too) whenever axqua itself runs inside one. What must not appear
    is an env-*activation* wrapper naming an environment.
    """
    rt = _runtime(tmp_path)
    cmd = _captured_command(rt, tmp_path, monkeypatch, ncsize=1)

    for wrapper in ("mamba run", "conda run", "conda activate", "source activate"):
        assert wrapper not in cmd
    # the only interpreter named is the running one - no environment is named
    assert cmd.split()[0] == sys.executable


def test_solver_python_can_be_overridden(tmp_path, monkeypatch):
    """Config first, then the env var, then sys.executable - so a machine can
    redirect it without editing a config, and a case can pin it without touching
    the machine."""
    rt = _runtime(tmp_path, solver_python="/opt/other/bin/python")
    cmd = _captured_command(rt, tmp_path, monkeypatch, ncsize=1)
    assert "/opt/other/bin/python" in cmd

    rt2 = _runtime(tmp_path)
    monkeypatch.setenv("AXQUA_TELEMAC_PYTHON", "/opt/env/bin/python")
    cmd2 = _captured_command(rt2, tmp_path, monkeypatch, ncsize=1)
    assert "/opt/env/bin/python" in cmd2

    # the config wins over the environment
    rt3 = _runtime(tmp_path, solver_python="/opt/cfg/bin/python")
    cmd3 = _captured_command(rt3, tmp_path, monkeypatch, ncsize=1)
    assert "/opt/cfg/bin/python" in cmd3


@pytest.mark.parametrize("ncsize,expect", [(1, False), (8, True)])
def test_parallel_flag_follows_the_core_count(tmp_path, monkeypatch, ncsize, expect):
    rt = _runtime(tmp_path)
    cmd = _captured_command(rt, tmp_path, monkeypatch, ncsize=ncsize)
    assert ("--ncsize=" in cmd) is expect
    # the sortie listing the flux analysis reads is always requested, unzipped so a
    # parallel run's copy can be found
    assert "-s --nozip" in cmd


def test_solver_can_be_switched_to_3d(tmp_path, monkeypatch):
    rt = _runtime(tmp_path)
    cmd = _captured_command(rt, tmp_path, monkeypatch, ncsize=1, solver="telemac3d")
    assert '"$(command -v telemac3d.py)"' in cmd


# --------------------------------------------------------------------------- #
# TELEMAC built natively on Windows, entered by a .bat through cmd (the Windows
# test computer, 2026-11-09: a here-document and $(command -v ...) stopped both jobs)
# --------------------------------------------------------------------------- #


def _windows_runtime(tmp_path, monkeypatch, path):
    from axqua.config import TelemacEnv
    from axqua.core.environment import SolverEnvironment
    from axqua.env import TelemacRuntime

    runtime = TelemacRuntime(TelemacEnv(pysource=tmp_path / "pysource.win.bat"))
    runtime.environment = SolverEnvironment(kind="windows",
                                            setup_script=r"C:\Users\stud\opt\telemac\configs\pysource.win.bat")
    runtime.environment.assume_entered = False
    monkeypatch.setattr(runtime.environment, "capture", lambda **kw: {"PATH": path})
    monkeypatch.delenv("AXQUA_TELEMAC_PYTHON", raising=False)
    return runtime


def test_the_check_of_a_windows_telemac_runs_its_snippet_from_a_file(tmp_path, monkeypatch):
    runtime = _windows_runtime(tmp_path, monkeypatch, "")
    seen = {}

    def fake_run(command, cwd=None, check=True, **kwargs):
        seen["command"] = command
        snippet = command.split('" "')[1].rstrip('"')
        seen["code"] = open(snippet, encoding="utf-8").read()
        return subprocess.CompletedProcess(command, 0, "3.12.15\n", "")

    monkeypatch.setattr(runtime, "run", fake_run)
    assert runtime.check_available() == "3.12.15"
    assert "<<" not in seen["command"]
    assert seen["command"].startswith(f'"{sys.executable}" "')       # the batch file sets no Python
    assert "from data_manip.formats.selafin import Selafin" in seen["code"]


def test_a_windows_telemac_is_started_by_the_full_path_of_its_launcher(tmp_path, monkeypatch):
    scripts = tmp_path / "telemac" / "scripts" / "python3"
    scripts.mkdir(parents=True)
    (scripts / "telemac2d.py").write_text("")             # not executable, no .PY in PATHEXT
    runtime = _windows_runtime(tmp_path, monkeypatch, rf"C:\Windows\system32;{scripts};C:\Windows")
    command = _captured_command(runtime, tmp_path, monkeypatch, ncsize=1)
    assert command == f'"{sys.executable}" "{scripts / "telemac2d.py"}" "case.cas" -s --nozip'
    assert "$(" not in command and "'" not in command


def test_a_windows_telemac_without_its_launcher_says_so(tmp_path, monkeypatch):
    runtime = _windows_runtime(tmp_path, monkeypatch, r"C:\Windows\system32")
    monkeypatch.setattr(runtime, "run", lambda *a, **k: pytest.fail("nothing to run"))
    lines = []
    proc = runtime.run_solver("case.cas", cwd=tmp_path, check=False, on_line=lines.append)
    assert proc.returncode == 1
    assert lines and "telemac2d.py is not on the PATH" in lines[0]
    with pytest.raises(RuntimeError, match="telemac2d.py is not on the PATH"):
        runtime.run_solver("case.cas", cwd=tmp_path)
