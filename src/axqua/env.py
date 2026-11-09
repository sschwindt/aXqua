"""Bridge to an external solver's runtime environment.

The axqua pipeline runs in its own (``axqua-env``) Python environment and
does *not* import a solver's Python directly. Instead, whenever a solver tool is
needed, we spawn a subshell that first *sources* that solver's environment script -
TELEMAC's ``pysource.*.sh`` (exporting HOMETEL + PYTHONPATH + the right ``python``)
or OpenFOAM's ``etc/bashrc`` - and then runs the requested command. This keeps the
geospatial stack (gmsh, rasterio, geopandas) cleanly decoupled from either solver's
interpreter, and means neither solver's environment can leak into the other's.

:class:`ShellRuntime` is that mechanism on its own; :class:`TelemacRuntime` adds the
TELEMAC-specific launchers, and :class:`axqua.solvers.openfoam.runtime.OpenFoamRuntime`
the OpenFOAM ones.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from axqua.config import TelemacEnv
from axqua.core.environment import (
    EnvironmentKind, SolverEnvironment, output_encoding, process_args,
)


class ShellRuntime:
    """Run commands inside a solver's environment.

    The *how* - source a shell script, call a ``.bat``, or go through WSL - belongs to
    :class:`axqua.core.environment.SolverEnvironment`, which this delegates to.
    Before that existed this class hardcoded ``bash -lc 'source X; cmd'``, which is
    three Linux assumptions in one line and the reason axqua could not run on
    Windows at all.
    """

    def __init__(self, env_script: str | Path | None = None, *,
                 environment: SolverEnvironment | None = None):
        if environment is None:
            environment = SolverEnvironment(setup_script=env_script)
        self.environment = environment

    @property
    def env_script(self) -> Path | None:
        """The setup script (kept under its historic name for existing callers)."""
        script = self.environment.setup_script
        return Path(str(script)) if script is not None else None

    def _wrap(self, command: str) -> list[str]:
        """The argv that runs *command* inside the configured environment."""
        return self.environment.command(command)

    def run(self, command: str, cwd: str | Path | None = None,
            check: bool = True,
            on_line: Callable[[str], None] | None = None,
            should_stop: Callable[[], bool] | None = None,
            env: dict[str, str] | None = None,
            grace: float = 20.0) -> subprocess.CompletedProcess:
        """Run *command* inside the sourced environment.

        With *on_line* the command's combined stdout+stderr is streamed line by
        line (each passed to the callback as it arrives, so a long solver run is
        not silent) instead of being buffered until the end; the full output is
        still returned in ``CompletedProcess.stdout``. Without it, output is
        captured quietly as before.

        *should_stop* is polled between output lines and, once it returns true, the
        whole **process group** is terminated - not just the child. That distinction is
        the point: the child is a shell or TELEMAC's launcher, and the ranks that matter
        are its grandchildren, so signalling the child alone leaves orphan ``mpirun``
        processes behind (plan §9). It is what makes a multi-hour run cancellable from
        another process.

        *env* replaces the child's environment wholesale, which is how a detached job
        hands over an already-captured solver environment instead of sourcing it again.
        """
        in_wsl = self.environment.kind is EnvironmentKind.WSL
        if cwd and in_wsl:
            # the folder as the distribution spells it: wsl.exe would guess it from the Windows folder it is started in, which fails for a \\wsl.localhost path
            command = f"cd {shlex.quote(self.environment.translate(cwd))} && {command}"
        argv = self._wrap(command)
        # UTF-8 (aXqua runs what it starts in UTF-8 mode, Linux writes it anyway); a byte that does not decode becomes a replacement character instead of an exception in the middle of a run
        text_kwargs = {"encoding": output_encoding(), "errors": "replace"}
        args = process_args(argv)
        if in_wsl:
            from axqua.core.wsl import environment as wsl_environment
            env = wsl_environment(env)          # WSL_UTF8: wsl.exe's own messages readable
        cwd = str(cwd) if cwd else None
        popen_kwargs: dict = {}
        if os.name == "nt":
            # a console window per step would pop up from QGIS, and closing it kills the step
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if on_line is None and in_wsl:
            from axqua.core.wsl import decode

            # wsl.exe's own messages may be UTF-16 (Windows 10 ignores WSL_UTF8)
            done = subprocess.run(args, cwd=cwd, capture_output=True, env=env, **popen_kwargs)
            result = subprocess.CompletedProcess(args, done.returncode, decode(done.stdout),
                                                 decode(done.stderr))
            if check and done.returncode:
                raise subprocess.CalledProcessError(done.returncode, args, result.stdout,
                                                    result.stderr)
            return result
        if on_line is None:
            return subprocess.run(
                args, cwd=cwd, check=check, capture_output=True, env=env,
                **text_kwargs, **popen_kwargs,
            )
        # A new session makes the child a process-group leader, so killpg reaches every
        # descendant. Harmless when nothing ever cancels.
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            args, cwd=cwd, bufsize=1, env=env, **text_kwargs,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **popen_kwargs,
        )
        captured: list[str] = []
        stopped = False
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if in_wsl:
                    line = line.replace("\x00", "")
                captured.append(line)
                on_line(line.rstrip("\n"))
                if should_stop is not None and should_stop():
                    stopped = True
                    _terminate_tree(proc, grace=grace)
                    break
        finally:
            try:
                proc.stdout.close()
            except OSError:  # pragma: no cover
                pass
        returncode = proc.wait()
        result = subprocess.CompletedProcess(
            args, returncode, stdout="".join(captured), stderr="",
        )
        if check and returncode != 0 and not stopped:
            raise subprocess.CalledProcessError(
                returncode, args, output=result.stdout,
            )
        return result


def cmd_line(parts: list[str]) -> str:
    """*parts* as one command line of ``cmd.exe``: each in double quotes, which is how cmd keeps a path with spaces together (a Windows path holds no double quote)."""
    return " ".join(f'"{part}"' for part in parts)


def _terminate_tree(proc: subprocess.Popen, *, grace: float = 20.0) -> None:
    """Stop *proc* and everything it started.

    ``SIGTERM`` to the group first so TELEMAC and MPI get the chance to shut down
    cleanly, then ``SIGKILL`` to whatever is left. Never a bare ``os.kill(pid)``:
    the solver ranks are grandchildren, and killing only the parent orphans them
    (plan §9).
    """
    if os.name == "nt":
        # No process groups in the POSIX sense. proc is the cmd that called the batch file; ending only it left TELEMAC's launcher, its cmd and telemac2d.exe running while the job said CANCELLED (the Windows test computer, 2026-11-09), and the launcher's Job Object did not help: the runner ends cooperatively, so `axqua cancel` never reaches it. taskkill /T ends the tree by its parent links while proc still holds it together, and needs no administrator for one's own processes.
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=grace,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            proc.kill()
        return
    import signal
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):  # pragma: no cover - already gone
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):  # pragma: no cover
        return
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):  # pragma: no cover
        pass


class TelemacRuntime(ShellRuntime):
    def __init__(self, env: TelemacEnv):
        super().__init__(environment=SolverEnvironment.from_config(
            getattr(env, "environment", None), legacy_script=env.pysource))
        self.env = env

    @property
    def pysource(self) -> Path:
        """The sourced ``pysource.*.sh`` (kept as its own name for readability)."""
        return self.env_script

    def _default_python(self) -> str:
        """The interpreter of TELEMAC's launchers when none is named: aXqua's own, which has numpy, or inside WSL the distribution's ``python3`` (a Windows ``python.exe`` does not run in Linux; TELEMAC's installer sets up numpy for it)."""
        if self.environment.kind is EnvironmentKind.WSL:
            return "python3"
        return sys.executable

    def _solver_python(self) -> str:
        """The interpreter TELEMAC's launchers run with: the case's ``telemac.solver_python``, then ``$AXQUA_TELEMAC_PYTHON``, then :meth:`_default_python`."""
        return str(getattr(self.env, "solver_python", None)
                   or os.environ.get("AXQUA_TELEMAC_PYTHON")
                   or self._default_python())

    def python(self, code: str, cwd: str | Path | None = None,
               check: bool = True) -> subprocess.CompletedProcess:
        """Execute a Python snippet using TELEMAC's interpreter (has data_manip)."""
        if self.environment.uses_cmd:
            # cmd knows no here-document ("<< was unexpected at this time", the Windows test computer, 2026-11-09), so the snippet goes in a file; and the batch file of a Windows TELEMAC puts no Python on the PATH, so it runs with the launchers' interpreter
            with tempfile.TemporaryDirectory(prefix="axqua-") as folder:
                snippet = Path(folder) / "snippet.py"
                snippet.write_text(code, encoding="utf-8")
                return self.run(cmd_line([self._solver_python(), str(snippet)]),
                                cwd=cwd, check=check)
        interpreter = "python3" if self.environment.kind is EnvironmentKind.WSL else "python"
        wrapped = f"{interpreter} - <<'__TMSETUP_PY__'\n" + code + "\n__TMSETUP_PY__"
        return self.run(wrapped, cwd=cwd, check=check)

    def run_solver(self, cas_file: str | Path, cwd: str | Path,
                   ncsize: int | None = None,
                   sortie: bool = True,
                   solver: str | None = None,
                   check: bool = True,
                   on_line: Callable[[str], None] | None = None,
                   should_stop: Callable[[], bool] | None = None,
                   ) -> subprocess.CompletedProcess:
        """Launch a TELEMAC solver on *cas_file* (used for the dry test run).

        *solver* overrides the configured solver launcher (default
        ``self.env.solver``, e.g. ``telemac2d``); pass ``"telemac3d"`` to run the
        3D case written by ``add3d.py``. With ``check=False`` a non-zero exit is
        returned (in ``returncode``) instead of raising, so a caller running several
        cases (e.g. the vertical convergence study) can report which one failed.

        *sortie* (default True) adds ``-s --nozip`` so TELEMAC writes a plain-text
        ``<cas>_<timestamp>.sortie`` listing next to the case. That listing carries
        the per-boundary cumulated flowrates and volume balance that the flux-
        convergence analysis (axqua.sortie) reads; ``--nozip`` keeps it unzipped in
        parallel runs so the parser can find it.

        *on_line* is forwarded to :meth:`run`: pass a callback (e.g.
        ``SolverProgress.feed``) to stream the solver's listing live instead of
        running silently. *should_stop* is likewise forwarded, and is how a job cancels
        a run that is already marching.
        """
        ncsize = ncsize or self.env.n_processors
        parallel = f" --ncsize={ncsize}" if ncsize and ncsize > 1 else ""
        sortie_flags = " -s --nozip" if sortie else ""
        launcher = solver or self.env.solver
        # The TELEMAC launcher (telemac2d/3d.py) imports numpy, which the bare
        # pysource shell does not necessarily provide. Run it with an interpreter
        # that has it - by default the one axqua itself runs in, which has numpy
        # by construction - while the sourced pysource supplies HOMETEL and the
        # TELEMAC PYTHONPATH. `command -v` resolves the launcher inside that shell,
        # so nothing about the install layout is assumed here.
        #
        # Deliberately NOT a named conda/mamba environment: that hard-codes a machine
        # assumption into the package (that mamba is installed, that the env is called
        # X, and that mamba's root prefix resolves), and it fails on a machine whose
        # MAMBA_ROOT_PREFIX points somewhere else. An interpreter path needs none of
        # that, and pointing `telemac.solver_python` at another env's python covers
        # the case where a *different* interpreter is genuinely wanted.
        python = self._solver_python()
        cas = self.environment.translate(cas_file)
        if self.environment.uses_cmd:
            # cmd has no `command -v` and no single quotes, and %PATH% on the line would be read before the batch file sets it: the launcher is looked up in the environment the batch file sets, and every part is quoted as cmd quotes
            script = self._launcher_on_windows(f"{launcher}.py")
            if script is None:
                message = (f"{launcher}.py is not on the PATH that {self.pysource} sets; "
                           "check the batch file of the TELEMAC installation")
                if on_line is not None:
                    on_line(message)
                if check:
                    raise RuntimeError(message)
                return subprocess.CompletedProcess(launcher, 1, stdout=message, stderr="")
            cmd = cmd_line([python, script, cas]) + f"{parallel}{sortie_flags}"
        else:
            cmd = (f'{shlex.quote(python)} "$(command -v {launcher}.py)" '
                   f"{shlex.quote(cas)}{parallel}{sortie_flags}")
        return self.run(cmd, cwd=cwd, check=check, on_line=on_line,
                        should_stop=should_stop)

    def _launcher_on_windows(self, name: str) -> str | None:
        """The full path of TELEMAC's launcher *name* on the PATH the batch file sets (this process's own where the environment was entered before, as in a job)."""
        from axqua.core.installation import which_file

        variables = (dict(os.environ) if self.environment.assume_entered
                     else self.environment.capture())
        return which_file(name, variables.get("PATH", ""))

    def check_available(self) -> str:
        """Return the TELEMAC python version string, raising if the env is broken."""
        proc = self.python(
            "import sys; "
            "from data_manip.formats.selafin import Selafin; "
            "print(sys.version.split()[0])",
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "Could not initialize the TELEMAC environment via "
                f"{self.pysource}.\nstderr:\n{proc.stderr}"
            )
        return proc.stdout.strip()
