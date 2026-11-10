"""Solver-agnostic HydroBayesCal plumbing: find it, stage a driver, launch it.

The part of the calibration path that is the same whichever solver is being
calibrated. The solver-specific halves live beside their solvers:

* :mod:`axqua.bayescal` - TELEMAC (``.cas`` rewriting, ``config_Telemac.py``,
  the single- and multi-flow entry points);
* :mod:`axqua.solvers.openfoam.calibration` - OpenFOAM (the ``interFoam`` case
  template, ``config_OpenFOAM.py``).

They are genuinely separate rather than one parameterised emitter: the two
HydroBayesCal config schemas differ in block *names* (``simulation`` vs
``hydrodynamic_simulation``), in block *membership* (``n_cpus`` lives under
``sampling`` for OpenFOAM and at top level for TELEMAC), in carrying a whole
extra ``interfoam`` block, and in what ``case_template_dir`` *means* - referenced
in place for TELEMAC, **copied per run** for OpenFOAM. A single emitter would be
an ``if solver ==`` ladder with two disjoint bodies. What they really share is
this module: locating HydroBayesCal, staging a driver out of it with the patches
axqua needs, and running that driver inside the right solver environment.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from axqua.core.environment import SolverEnvironment


# --------------------------------------------------------------------------- #
# install location
# --------------------------------------------------------------------------- #
#: Oldest HydroBayesCal that aXqua runs a calibration with; the same floor as the
#: ``calibration`` extra in ``pyproject.toml`` (a test keeps the two equal). 1.12.1
#: starts the Sobol design at the first point of the sequence, so that a design of
#: 16 runs puts one run into each sixteenth of every parameter range; 1.12.2 lets the
#: scikit-learn emulator (``gp_library: skl``) run with TELEMAC.
HBC_MINIMUM = "1.12.2"


def _release(text: str) -> tuple[int, ...]:
    """The leading release numbers of a version string (``1.12.1.dev3`` -> (1, 12, 1))."""
    match = re.match(r"\s*v?(\d+(?:\.\d+)*)", text)
    return tuple(int(n) for n in match.group(1).split(".")) if match else ()


def hbc_too_old(installed: str, minimum: str = HBC_MINIMUM) -> bool:
    """Whether *installed* is below *minimum*; an unreadable version passes."""
    have, need = _release(installed), _release(minimum)
    if not have:
        return False
    width = max(len(have), len(need))
    return have + (0,) * (width - len(have)) < need + (0,) * (width - len(need))


def require_hbc():
    """Import HydroBayesCal, or explain how to install it.

    It is an **optional dependency** (``pip install axqua[calibration]``): the
    surrogate stack it pulls in - bayesvalidrox, gpytorch, scikit-learn - is heavy and
    irrelevant to building or running a solver case, which is what most of axqua
    does. So it is imported where it is used rather than at module import.
    """
    try:
        import hydroBayesCal
    except ImportError as exc:                       # pragma: no cover - install aid
        raise SystemExit(
            "HydroBayesCal is not installed. It is an optional dependency:\n"
            "    pip install 'axqua[calibration]'\n"
            "or, for a local checkout you are also developing:\n"
            "    pip install -e /path/to/hydrobayescal\n"
            f"(import failed: {exc})"
        ) from exc
    if not hasattr(hydroBayesCal, "copy_driver"):
        raise SystemExit(
            "The installed HydroBayesCal is too old: it does not ship the calibration "
            "drivers with the package (hydroBayesCal.copy_driver is missing).\n"
            "Upgrade to the LATEST release - HydroBayesCal is bugfix-and-additive, so "
            "newest is always the one to want and axqua never pins an upper bound:\n"
            "    pip install -U hydrobayescal"
        )
    installed = hbc_version()
    if hbc_too_old(installed):
        raise SystemExit(
            f"The installed HydroBayesCal {installed} is too old: aXqua needs "
            f"{HBC_MINIMUM} or newer. Before 1.12.1, a Sobol initial design never "
            "tried the lowest part of each parameter range, and before 1.12.2 the "
            "scikit-learn emulator failed with TELEMAC. Upgrade it in the "
            "environment of aXqua:\n"
            "    pip install -U hydrobayescal"
        )
    return hydroBayesCal


def hbc_version() -> str:
    """Installed HydroBayesCal version, for the staging log."""
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("hydrobayescal")
    except PackageNotFoundError:                     # pragma: no cover
        return "unknown"


# --------------------------------------------------------------------------- #
# staging a driver
# --------------------------------------------------------------------------- #
#: The extraction-window patch every staged driver gets. The stock drivers average
#: the last *window* of result frames (``mean_last``); on a run marching to steady
#: state from a dry or pre-wetted start that averages the residual transient into
#: the calibration values. ``last`` takes the converged frame.
EXTRACTION_PATCH = {'output_extraction_time="mean_last"': 'output_extraction_time="last"'}


def stage_driver(out_dir: Path, template_name: str, *, checkout: Path | None = None,
                 also: tuple[str, ...] = (),
                 replacements: Mapping[str, str] | None = None,
                 required: Sequence[str] = ()) -> Path:
    """Copy a HydroBayesCal driver into *out_dir*, with axqua's patches applied.

    The driver comes from the **installed** HydroBayesCal package
    (``hydroBayesCal.copy_driver``, which also brings any sibling a driver imports),
    so there is no checkout path to configure. *checkout* overrides that with a
    source tree, for developing against an unreleased driver.

    *replacements* are further literal-to-literal rewrites, applied on top of
    :data:`EXTRACTION_PATCH`. *required* names literals that **must** have matched
    somewhere; if one did not, this raises rather than proceeding. That guard is the
    point of the argument: a patch rewrites a string literal in someone else's file,
    so without it a HydroBayesCal release that changes the literal turns the patch
    into a silent no-op - and the failure then shows up as a wrong *result* hours
    later rather than as an error at staging time.

    **Every** staged file is patched, not just the primary one. That is not caution:
    ``bal_telemac_multiflow.py`` imports ``run_complex_model`` / ``run_bal_model``
    from its ``bal_telemac.py`` sibling and calls them *without* passing
    ``output_extraction_time``, so a multi-flow run takes the sibling's default. With
    only the primary patched, multi-flow calibrations silently averaged the transient
    - the exact failure this patch exists to prevent - while single-flow ones did not.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if checkout is not None:
        checkout = Path(checkout)

        def _find(name):
            candidates = (checkout / name,
                          checkout / "templates" / name,
                          checkout / "src" / "hydroBayesCal" / "drivers" / name)
            return next((p for p in candidates if p.is_file()), None)

        primary = _find(template_name)
        if primary is None:
            raise SystemExit(
                f"HydroBayesCal driver {template_name!r} not found in {checkout}")
        for name in also:
            src = _find(name)
            if src is None:
                raise SystemExit(f"companion driver {name!r} not found in {checkout}")
            shutil.copy2(src, out_dir / name)
        shutil.copy2(primary, out_dir / template_name)
        source = "checkout"
    else:
        hbc = require_hbc()
        hbc.copy_driver(template_name, out_dir)
        for name in also:
            hbc.copy_driver(name, out_dir)
        source = f"hydroBayesCal {hbc_version()}"

    patches = dict(EXTRACTION_PATCH)
    patches.update(replacements or {})

    applied: dict[str, int] = {literal: 0 for literal in patches}
    for staged in sorted(out_dir.glob("*.py")):
        text = staged.read_text()
        changed = text
        for literal, replacement in patches.items():
            n = changed.count(literal)
            if n:
                applied[literal] += n
                changed = changed.replace(literal, replacement)
        if changed != text:
            staged.write_text(changed)

    missing = [literal for literal in required if applied.get(literal, 0) == 0]
    if missing:
        raise SystemExit(
            f"axqua could not patch the staged {template_name}: the following "
            f"required text was not found in any staged driver:\n"
            + "".join(f"    {literal!r}\n" for literal in missing)
            + f"This is {source}. The driver has changed shape since axqua's patch was "
            "written; the patch must be updated (or the fix taken upstream) before "
            "this calibration can run - proceeding would silently use unpatched "
            "behaviour."
        )

    total = sum(applied.values())
    print(f"staged {template_name} from {source}"
          + (f" (+{', '.join(also)})" if also else "")
          + f" -> {out_dir}"
          + (f" ({total} patch site(s))" if total else ""))
    return out_dir / template_name


# --------------------------------------------------------------------------- #
# launching
# --------------------------------------------------------------------------- #
def solver_running(pattern: str, model_dir: Path, *, extra: Sequence[str] = ()) -> bool:
    """Whether a solver process from *model_dir* is already active.

    Two calibrations sharing one model directory fight over the files each rewrites
    per run (the friction table, the steering file, the field dictionaries), so this
    is checked before launching rather than diagnosed afterwards from a confusing
    result.
    """
    probe = subprocess.run(["pgrep", "-fa", pattern], capture_output=True, text=True)
    if str(model_dir) in probe.stdout:
        return True
    # a solver started with a relative steering file (``steady2d.cas``) names no
    # folder: it counts where it works in *model_dir* (TELEMAC works in a folder of its
    # own inside it), not because a case elsewhere on this computer runs one too
    folder = Path(model_dir).resolve()
    for line in probe.stdout.splitlines():
        pid, _, command = line.partition(" ")
        if not any(token in command for token in extra):
            continue
        try:
            cwd = Path(os.readlink(f"/proc/{pid}/cwd"))
        except OSError:
            continue
        if cwd == folder or folder in cwd.parents:
            return True
    return False


def launch_driver(environment: SolverEnvironment, driver: Path, config_path: Path, *,
                  note: str = "HydroBayesCal calibration",
                  extra_args: Sequence[str] = (),
                  env: Mapping[str, str] | None = None) -> int:
    """Run a staged driver inside *environment*, in this interpreter's Python.

    HydroBayesCal is a dependency of axqua, so it is importable here - there is no
    second conda environment to name and no interpreter to guess. The driver is
    still run as a **subprocess** rather than imported, for two reasons that matter
    over a calibration lasting hours: it is a script with module-level state and an
    ``argparse`` ``main()``, and it has to run with the **solver sourced**, since
    every surrogate iteration launches the solver. Sourcing mutates the environment,
    which is exactly the sort of thing not to do to the caller's own process.

    ``sys.executable`` is used explicitly so the subshell cannot pick up a different
    ``python`` from the sourced solver environment.

    Returns the driver's exit code.
    """
    import os

    if os.name == "nt" and str(getattr(environment, "kind", "")) == "wsl":
        from axqua.core.errors import EnvironmentError as AxquaEnvironmentError
        raise AxquaEnvironmentError(
            "the Bayesian calibration with HydroBayesCal is not yet available on Windows with TELEMAC inside WSL",
            subject="calibration",
            remedy="HydroBayesCal starts TELEMAC itself and has to run where TELEMAC is. Run the calibration on a "
                   "Linux computer (or inside the WSL distribution with an aXqua installed there); the build, the "
                   "simulations and the validation work on Windows.")
    inner = (f"cd {shlex.quote(str(driver.parent))}; "
             f"{shlex.quote(sys.executable)} -u {shlex.quote(driver.name)} "
             f"--config {shlex.quote(str(config_path))}"
             + "".join(f" {shlex.quote(str(a))}" for a in extra_args))
    argv = environment.command(inner)
    print(f"\nlaunching {note}:\n  {' '.join(argv[:2])} {inner}\n")
    merged = None
    if env:
        merged = {**os.environ, **{k: str(v) for k, v in env.items()}}
    from axqua.core.environment import process_args

    return subprocess.run(process_args(argv), env=merged).returncode


#: The name of the log a HydroBayesCal driver keeps in its results folder, and the line
#: it writes there each time it starts a run of the full model. The driver announces a
#: run twice. ``Running  full complexity model # 3`` counts within the initial design and
#: ``... model after BAL # 1`` within the active learning, so both start again at 1;
#: ``Running full complexity model 9`` counts through both, and is the one read here.
DRIVER_LOG = "logfile.log"
#: The results folder of a HydroBayesCal driver. Since HydroBayesCal 1.12.0 the driver
#: writes ``logfile.log`` there; before, it wrote it into its working folder.
DRIVER_RESULTS = "auto-saved-results-HydroBayesCal"
_MODEL_RUN = re.compile(r"Running\s+full\s+complexity\s+model\s+(\d+)\b")


class RunCounter:
    """Report which model run a HydroBayesCal driver has reached, from its own log.

    A calibration is the longest job aXqua starts and was the only one that said
    nothing while it ran: the driver is a separate process whose output is not parsed,
    so the job stood at "iteration 0" from the first minute to the last. The driver
    does write one line per model run to ``logfile.log`` in its results folder, and
    that is enough to say "run 7 of 12".

    Used as a context manager around the launch. A thread reads what the log has
    gained every *interval* seconds and calls *report* with the highest run number
    seen, once per change. Only lines written after entry count, so the log of an
    earlier calibration in the same folder is not replayed. Nothing here can fail a
    calibration: an unreadable log and a failing *report* are both ignored, and a
    driver that words the line differently simply reports no progress.

    *logfile* may be a list of paths: all of them are followed, because the driver
    writes its log into its results folder since HydroBayesCal 1.12.0 and into its
    working folder before (:func:`driver_logs`).
    """

    def __init__(self, logfile: Path | str | Sequence[Path | str],
                 report: Callable[[int], None], *, interval: float = 5.0) -> None:
        paths = [logfile] if isinstance(logfile, (str, Path)) else list(logfile)
        self.logfiles = [Path(path) for path in paths]
        self.logfile = self.logfiles[0]
        self.report = report
        self.interval = interval
        self.run = 0
        self._offsets = {path: 0 for path in self.logfiles}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "RunCounter":
        for path in self.logfiles:
            try:
                self._offsets[path] = path.stat().st_size
            except OSError:
                self._offsets[path] = 0
        self._thread = threading.Thread(target=self._follow, name="hbc-run-counter",
                                        daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 5.0)
        self.poll()                      # the lines written since the last look

    def _follow(self) -> None:
        while not self._stop.wait(self.interval):
            self.poll()

    def poll(self) -> None:
        """Read what the logs have gained and report a new run number, if there is one."""
        text = "".join(self._gained(path) for path in self.logfiles)
        runs = [int(number) for number in _MODEL_RUN.findall(text)]
        if not runs or max(runs) <= self.run:
            return
        self.run = max(runs)
        try:
            self.report(self.run)
        except Exception:                # noqa: BLE001 - reporting must not stop a run
            pass

    def _gained(self, path: Path) -> str:
        """What *path* has gained since the last look; empty if it cannot be read."""
        try:
            size = path.stat().st_size
            offset = self._offsets.get(path, 0)
            if size < offset:            # the driver started the file afresh
                offset = 0
            if size == offset:
                return ""
            with path.open("rb") as handle:
                handle.seek(offset)
                text = handle.read().decode("utf-8", errors="replace")
            self._offsets[path] = size
            return text
        except OSError:
            return ""


def driver_logs(folder: Path | str) -> list[Path]:
    """Where a HydroBayesCal driver run in *folder* writes ``logfile.log``.

    Into its results folder since HydroBayesCal 1.12.0, into *folder* itself before.
    A driver from a checkout (``checkout=``) can be older than the floor of the
    package, so both are followed.
    """
    folder = Path(folder)
    return [folder / DRIVER_RESULTS / DRIVER_LOG, folder / DRIVER_LOG]


def solver_environment(block, legacy_script) -> SolverEnvironment:
    """A :class:`SolverEnvironment` for a calibration launch.

    ``assume_entered`` is forced **off**, and that is not defensive noise: it
    otherwise defaults to reading the ``AXQUA_ENV_CAPTURED`` marker a detached
    launcher exports. A calibration started from inside a job that had already
    entered *another* solver's environment would then skip sourcing this one
    entirely and run against whatever binaries that environment put on ``$PATH`` -
    a failure that is cheap to prevent here and expensive to diagnose later.
    """
    environment = SolverEnvironment.from_config(block, legacy_script=legacy_script)
    environment.assume_entered = False
    return environment
