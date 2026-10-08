# The job system: simulations that outlive QGIS (`axqua/jobs/`)

**QGIS must not own the lifetime of a solver process** (plan §1), so a job is a *directory
on disk* that one process submits and another executes. `axqua execute job.json` works
with QGIS absent, and the per-case scripts remain the supported standalone path.

`axqua/jobs/` is a **third sibling** of `core/` and `solvers/`, not part of `core`:
the executor dispatches to a backend, which `tests/test_capabilities.py` forbids anything
in `core/` from doing (a new test enforces the same for `jobs/`, exempting only
`executor.py`). `model.py`, `ids.py` and `paths.py` are **stdlib-only**, so a job verb
costs no more than a capability listing.

**Two files, one writer each** (plan §3). `job.json` is the *immutable* submission record
and freezes the **resolved configuration** (via the existing `config_to_dict`) plus a
loadable `input/case-config.yml` (via `dump_config`, `base=<job>/input`) - so editing the
case afterwards cannot change a running job and a finished job is reproducible from its own
folder. It is `chmod 0o444` after writing; a second write raises. `status.json` is
*mutable*, written only by a process holding `job.lock`, replaced atomically (tmp + fsync +
`os.replace`, which is atomic on Windows too where `os.rename` is not) and read by
`read_json_tolerant`, which **retries on `JSONDecodeError`** - a plugin polling at 1 Hz
over a network filesystem will land in the replace window, and the right answer there is to
look again, not to report the job as broken.

**Progress is nested and typed per `JobKind`** (`SolverRunProgress`, `LadderProgress`,
`CalibrationProgress`, `PreprocessingProgress`, `OpenFoamBuildProgress`), not flattened:
hoisting `iteration`/`best_objective` to the top level would force every non-optimization
job to publish nulls. `Progress.from_dict` keeps an **unknown** `kind` as a raw dict, so an
older plugin degrades to showing nothing useful rather than losing the field. The visible
payoff is the dashboard's single Progress column, which reads correctly for every kind.

**Job kinds are the ten case-template scripts** (`PREPROCESSING`, `STEADY_RUN`,
`MESH_CONVERGENCE`, `BUILD_3D`, `STEADY_RUN_3D`, `VERTICAL_CONVERGENCE`, `UNSTEADY_RUN`,
`OPENFOAM_BUILD`, `OPENFOAM_RUN`, `CALIBRATION`, `CALIBRATION_MULTIFLOW`). `KIND_META`
carries slug, solver, `Capability`, the backend verb, the **options dataclass** (which is
where each script's module-level constants moved - that is the answer to "don't copy-paste
the scripts") and the default workspace mode. `gain_lose`/`morphodynamics` are **not**
kinds - they are config switches folded into the steady/unsteady kinds. Options refuse an
unknown key (plan §5: never silently ignore a field, or a job that *looks* submitted
quietly runs something else).

**Workspace modes** resolve the spec's conflict with the repo. §5's tree gives every job an
`input/`, but steady/3D/unsteady/calibration all run *inside an existing `model_dir`* and
hotstart from an `r2d.slf` there; copying it violates §26. So `workspace.mode` is
`case` (leave the four phase dirs alone - **the default for every kind**, and the only mode
under which the standalone scripts and the job system touch the same files) | `job` (rebase
them into the job, for a self-contained build: `--workspace job`) | `link:<job_id>` (point
at a previous job's build, copying nothing). The rebase is four assignments, because
everything already resolves through `cfg.model_path()` and friends. **The build used to
default to `job`, and that broke the default chain**: `submit --kind preprocessing` then
`submit --kind steady` failed with "no built case", because the build sat in a job folder
the run never looked in and `case-status` kept reporting the case as unbuilt. Those are the
two buttons of the plugin's first tab, so nobody who tried the plugin got past them.

**A job runs in the interpreter that submitted it** (`launcher.runner_argv` is
`[sys.executable, "-m", "axqua", "execute", job_dir]`). It used to start whichever `axqua`
came first on `PATH`, which is another program as soon as two environments carry aXqua - the
plugin calls one by its full path, `PATH` offers the other - and the job then ran with a
different Python and different package versions, silently. On lww-134 a calibration submitted
from `axqua-env` (HydroBayesCal 1.9) ran in the miniforge base environment against a 1.7
checkout.

**A state change is written at once** (`StatusFileSink.state_changed`). The executor assigns
a transition on the status object, so the sink considered itself clean and `flush()` skipped
the write; the new state reached `status.json` only when a progress event happened to follow.
A steady run reports within seconds, a calibration never did, and it stood in the dashboard as
STARTING for as long as it ran. `_finish` documents the same trap for the terminal state.

**The result manifest carries `crs_epsg`.** A SELAFIN file names no CRS, so a loaded result
had none and only lined up with a base map when the project happened to use the same system.
The executor records the case's code and the plugin's loader sets it on a layer that has no
valid CRS of its own; the project CRS is never touched.

**TELEMAC jobs of one case run one after the other** (`jobs/workspace.py`). A job holds a
lock in `cfg.model_dir` (`.axqua-job.lock`, the job lock's record on another file, so the
same staleness rules free a case whose runner died) from before STARTING until its final
state is written, and a second job of the same case **waits** instead of failing: its phase
reads `waiting for <job id>`, which `axqua status` and the plugin's Progress column show,
and it can be cancelled while it waits. Waiting is what the user means - *Build* then
*Submit* can be clicked in succession - and it is what makes a calibration safe, since
HydroBayesCal rewrites `friction.tbl` in that folder before each of its runs. Two things to
know: a detached job is already STARTING while it waits (the launcher sets that before the
executor runs), and **a waiting job does not check that its predecessor succeeded**.
**OpenFOAM jobs take no part**: their legs work in case folders of their own that may share
one TELEMAC `model_dir` and run side by side on purpose.

**The order is the order of submission, and a lock alone does not give that.** Of two
waiting jobs, whichever polls first when the folder frees would take it, and three jobs
submitted in a row are started by three detached processes that reach the lock in any
order - the run before its build. So `submit_job` leaves a **ticket** in
`model_dir/.axqua-queue/` (`<time_ns>-<job id>.json`, written by the *submitter*, before the
runner is launched) and a job may only try the lock while no older live ticket exists. A
ticket is dropped when its job is terminal (asked through `reaper.reconcile`, so a runner
that died while waiting is noticed without anybody polling it), when its job folder is gone,
or when its runner never appeared within `TICKET_GRACE`. The plugin's batch submits its
steps in ONE background call for the same reason. Verified live: build, steady and a
calibration preparation submitted within a second ran 13:58:58-14:00:50, 14:00:52-14:05:53,
14:05:57-14:05:58.

**Every verb resolves the job root the same way** (`paths.job_root`): flag, `AXQUA_JOB_ROOT`,
solver profile, **`jobs.root` of the active `.axq-profile`**, case, default. Only `submit`
used to know the profile's folder, so a job was created there and then not found by `list`,
`status`, `logs` or `cancel`. The profile is imported inside the function, which keeps
`paths.py` standard-library-only at import time.

**Staleness** is `(host, boot_id, pid, process start time)`, in that order: a different
host is **never** judged (a shared job root may hold another machine's jobs), a different
boot id is stale (also the `wsl --shutdown` case), a dead pid is stale, and a live pid with
a different start time is stale - **PID reuse**, which `os.kill(pid, 0)` alone cannot see.
`reaper.reconcile` runs on every status/list, so a job **never sticks in RUNNING**: a
recorded process that is gone becomes FAILED (or CANCELLED, if `cancel.request` exists).
Note `pid_alive` treats a **zombie as dead** - `os.kill(pid,0)` succeeds against one, which
made `cancel` report failure against something that had already stopped.

**The index is a cache.** SQLite at `data_dir()/jobs.sqlite`, WAL so a QGIS reader never
blocks the runner. **Every index failure is caught and ignored**, which is what makes §24's
"a solver failure must not corrupt the registry" true by construction; `list` falls back to
a live directory scan, and `list --rebuild` reconstructs by scanning (a *tested* path -
it covers a moved, restored or hand-edited job folder). `list_jobs` also re-reads
**non-terminal** rows from disk, because the index is written at submit time and cannot
know a detached job has moved on.

**The event seam** (`jobs/events.py`) is a wrapper, not a replacement. `SolverProgress`
already parses TELEMAC's `ITERATION ... TIME:` header and `OpenFoamProgress` already parses
interFoam's Courant/`deltaT`/`Time =`, both hard-won against real output - so both gained a
`sink=` parameter and emit the *same* parse as structured progress. `StatusFileSink` is
throttled to >=2 s (a listing at a few headers a second would otherwise cause several
fsync'd rewrites a second on scratch) but never throttles a state or step change. The
terminal write in `executor._finish` deliberately bypasses the sink: the sink considers
itself clean because those fields are assigned directly, so a failed job kept its real error
in `runner.log` while `status.json` still said RUNNING and the reaper later relabelled it
"abandoned".

**Interactive studies.** `convergence.run_mesh_convergence`'s `_default_ask` returns the
default off a TTY, so a detached job does not hang - but it silently *truncates* a study
submitted to march finer. The executor therefore **always injects `ask=`**
(`interaction.PolicyAsk`), answering from `job.json`'s `options.answers`, recording every
question and answer in `runner.log`, and honouring a later `answers.json`. A test
monkeypatches `_default_ask` to raise and asserts a mesh-convergence job still completes.

**Detachment** (`jobs/launcher.py` + `jobs/launchers/`) is the first caller of
`SolverEnvironment.capture()`: the environment is captured once and set **directly on the
detached process**. For that to actually flatten the tree, `SolverEnvironment` gained
`assume_entered` (defaulting from the `AXQUA_ENV_CAPTURED` marker the launcher exports)
so the child does not re-source the setup script - otherwise the tree is
`systemd -> axqua -> bash -lc -> python telemac2d.py -> mpirun -> ranks`, and each
layer is somewhere a signal can fail to propagate. `SystemdUserLauncher` is preferred on
Linux (`--collect`, so a failed unit does not squat the deterministic name;
`EnvironmentFile` rather than N x `--setenv`, because a full OpenFOAM environment exceeds
`ARG_MAX`; `KillMode=control-group`, so a stop kills the cgroup and the MPI ranks with it).
`available()` checks `systemd-run` **and** `$XDG_RUNTIME_DIR` **and** `systemctl --user
is-system-running`, because a bare SSH session without lingering has the binary and no user
manager. `PosixDetachedLauncher` uses `start_new_session=True` so `pgid == pid` and
`killpg` reaches every descendant - **never** `os.kill(pid)`. `WindowsJobObjectLauncher`
creates a **named** Job Object (`Local\axqua-<job_id>`) with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` cleared: an *unnamed* one is reachable only through
the creating handle, which dies with the submitter, so a later `cancel` from a fresh
process could not open it - correcting the plan's own sketch. `WslLauncher` detaches
*inside* the distro and acts on the **Linux** pgid through `wsl.exe`; killing `wsl.exe`
does not kill the tree, it is a client. Windows and WSL are **unit-tested but never
executed against a real install**, and say so.

**Cancellation is cooperative first**: `cancel` writes `<job>/cancel.request` (its own file,
so the one-writer-per-file rule survives), the executor's `CancelToken` notices it between
steps and inside the streaming callback, and the job tears itself down and writes CANCELLED
- which lets it close its files. Only if it does not respond within the grace period does
the launcher tree-kill. `ShellRuntime.run` gained `should_stop` and `start_new_session`, and
`_terminate_tree` does SIGTERM-then-SIGKILL on the **group**; that is the one change to
`env.py` and it is what makes a multi-hour run cancellable at all.

**The CLI** keeps its manual dispatch chain (a `_DISPATCH` table now): `add_subparsers`
cannot express the default `axqua <config.yml>` form without a pre-pass on `argv[0]`,
so the chain survives the conversion anyway and only the help output would churn. New
verbs: `submit`, `execute`, `status`, `cancel`, `logs`, `list`, `profiles`, plus
`case-status` (the renamed case status). **`status` is overloaded and the argument
decides** - an existing *path* means the case (historic meaning kept, with a notice), a
`JOB_ID_RE`-shaped argument means the job; they cannot collide. Every verb takes `--json`
and emits one envelope (`{ok, command, axqua, data, error}`) on **stdout** with all
narration on stderr. Exit codes are per `AxquaError` category (2 config, 3 geodata,
4 environment, 5 solver, 6 mesh, 1 unexpected, 130 cancelled). `src/axqua/__main__.py`
makes `python -m axqua` work, which the launchers need when the console script is
absent from a captured solver `PATH`.

**Solver backends finally exist.** `BackendSpec.implementation` had always named
`solvers/telemac/backend.py` and `solvers/openfoam/backend.py`; neither module existed, so
`spec.load()` was dead code. Both are now **pure adapters** over the functions the case
scripts already call (`pipeline.run`, `run_solver_streaming`, `analyze_flux_convergence`,
`run_mesh_convergence`, `build_case`, the OpenFOAM stage loop, `bayescal.run_*`), which is
what keeps the standalone and job paths from drifting. The `SolverBackend` protocol gained
`study` (a convergence ladder is neither a build nor a run) plus `postprocess`,
`extract_objective` and `export_qgis_results`, with `BaseBackend` supplying no-op defaults.
`export_qgis_results` **converts nothing** for TELEMAC: QGIS/MDAL reads SELAFIN natively, so
it writes a small `results/results.json` manifest of paths plus styling hints. The OpenFOAM
backend deliberately **does not call `prerun.ensure_seed`** - `prerun` imports the TELEMAC
backend transitively, so that would break the sibling rule in a way the regex test cannot
see; the executor obtains the seed and passes it in, exactly as `cli._run_openfoam` does.
`OpenFoamRuntime`'s hard-coded `mpirun -np N` now goes through `environment.mpi_command()`
(MS-MPI provides `mpiexec`), and it gained `to_vtk`. The `axqua.solvers` entry-point
group is declared in `pyproject.toml`.

Fake solvers live in `tests/fakes/`: `bin/telemac2d.py` is **Python, not shell**, because
aXqua runs the launcher through an interpreter, and it prints a real-shaped listing so
`SolverProgress` genuinely parses it; `fake_pysource.sh` exports the real `SENTINELS` and a
stub `data_manip.formats.selafin`, so `capture()`/`validate()`/`check_available()` succeed
in CI - a path that had never had end-to-end coverage.
