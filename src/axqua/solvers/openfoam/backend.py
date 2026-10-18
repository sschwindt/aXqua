"""The OpenFOAM backend: build, run, postprocess, export.

Like its TELEMAC counterpart this is an adapter, not new physics - ``build_case``, the
two-stage run and the discharge report already exist and are what
``openfoam_preprocessing.py`` / ``openfoam_run.py`` call.

**One thing is deliberately absent: the TELEMAC pre-run.** An OpenFOAM case is seeded
from a converged 2D result, and ``prerun.ensure_seed`` produces one - but calling it from
here would make the OpenFOAM backend reach into the TELEMAC backend, which
``tests/test_capabilities.py`` forbids by reading the source, and which would be wrong
even if it were not checked: the two backends are siblings. Orchestration that drives
both belongs above both, so the executor obtains the seed and hands it in - exactly what
``cli._run_openfoam`` already does.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from axqua.core import errors
from axqua.core.capabilities import Capability
from axqua.core.registry import BaseBackend

log = logging.getLogger("axqua.solvers.openfoam.backend")


class OpenFoamBackend(BaseBackend):
    """interFoam two-phase free-surface runs (Foundation OpenFOAM, openfoam.org)."""

    name = "openfoam"

    # -- environment --------------------------------------------------------------
    def check_environment(self, cfg) -> bool:
        from axqua.solvers.openfoam.runtime import OpenFoamRuntime
        try:
            OpenFoamRuntime(cfg.openfoam).check_available()
        except Exception as exc:  # noqa: BLE001 - report, never raise
            log.debug("openfoam unavailable: %s: %s", type(exc).__name__, exc)
            return False
        return True

    # -- build --------------------------------------------------------------------
    def build(self, cfg, capability: Capability | None = None, *, ctx: Any = None,
              hotstart: str | None = None, cell_size: float | None = None,
              layers: int | None = None, seed: Any = None, **_ignored):
        """Mesh the reach and write the case.

        *seed* is the 2D result to hotstart from, supplied by the caller. When it is
        absent the case still builds - cold, under a flat lid - which is the documented
        ``--no-pre-run`` behavior rather than a failure.
        """
        from axqua.solvers.openfoam import case as of_case
        from axqua.solvers.openfoam.hotstart import load_hotstart

        if cell_size is not None:
            cfg.openfoam.cell_size = cell_size
        if layers is not None:
            cfg.openfoam.n_layers = layers
        if capability is Capability.MORPHODYNAMICS:
            return self._build_sediment(cfg, ctx, hotstart or seed)

        source = hotstart or seed
        state = None
        if source is not None:
            try:
                state = load_hotstart(cfg, source)
            except Exception as exc:  # noqa: BLE001
                # A cold build is a worse build, not a failed one, and losing the whole
                # job over an unreadable seed would be the wrong trade.
                log.warning("could not read the hotstart %s (%s: %s); building cold",
                            source, type(exc).__name__, exc)

        if ctx is not None:
            ctx.check_cancelled()
        artifacts = of_case.build_case(cfg, state=state, hotstart=hotstart)
        if ctx is not None:
            cells = getattr(getattr(artifacts, "mesh", None), "n_cells", None)
            usable = getattr(getattr(artifacts, "report", None), "usable", None)
            ctx.progress(cells=cells, stage="built", mesh_usable=usable)
            ctx.record("OpenFOAM case", Path(artifacts.case_dir) / "case.foam",
                       kind="file",
                       description="open in ParaView; threshold alpha.water > 0.5")
        return artifacts

    def _build_sediment(self, cfg, ctx: Any, source):
        """The case of the fine sediment simulation. Unlike the free-surface case it
        cannot be built cold: without a 2D result there is no water surface to end
        the model at, so an unreadable result is an error here and not a warning."""
        from axqua.solvers.openfoam import sediment
        from axqua.solvers.openfoam.hotstart import load_hotstart

        state = load_hotstart(cfg, source) if source is not None else None
        if ctx is not None:
            ctx.check_cancelled()
        artifacts = sediment.build_case(cfg, state=state)
        if ctx is not None:
            ctx.progress(cells=artifacts.record.get("n_cells"), stage="built",
                         mesh_usable=True)
            ctx.record("Fine sediment case", Path(artifacts.case_dir) / "case.foam",
                       kind="file", description="open in ParaView")
        return artifacts

    def _run_sediment(self, cfg, ctx: Any, *, iterations=None, check_mesh=True):
        from axqua.solvers.openfoam import sediment

        sediment.run_case(
            cfg, iterations=iterations, check_mesh=check_mesh,
            sink=_sink_for(ctx, sediment.SOLVER), show_progress=ctx is None,
            should_stop=ctx.should_stop if ctx is not None else None)
        if ctx is not None:
            ctx.check_cancelled()
        return Path(cfg.fine_sediment_case_dir)

    # -- run ----------------------------------------------------------------------
    def run(self, cfg, capability: Capability | None = None, *, ctx: Any = None,
            n_processors: int | None = None, reconstruct: bool = True,
            check_mesh: bool = True, iterations: int | None = None, **_ignored):
        from axqua.solvers.openfoam import dicts
        from axqua.solvers.openfoam.runtime import OpenFoamRuntime

        if capability is Capability.MORPHODYNAMICS:
            return self._run_sediment(cfg, ctx, iterations=iterations,
                                      check_mesh=check_mesh)

        case_dir = Path(cfg.openfoam_case_dir)
        if not (case_dir / "constant" / "polyMesh" / "faces").exists():
            raise errors.ConfigError(
                f"no built OpenFOAM case at {case_dir}",
                subject="openfoam",
                remedy="Submit an openfoam-build job first.",
            )
        runtime = OpenFoamRuntime(cfg.openfoam)
        sink = _sink_for(ctx, "interfoam")

        if check_mesh:
            usable, failed, _ = runtime.check_mesh(case_dir)
            if not usable:
                # Starting a solver on a broken mesh wastes the entire run, so this is a
                # refusal rather than a warning.
                raise errors.MeshError(
                    "checkMesh found problems that prevent a run",
                    subject=str(case_dir),
                    remedy="Rebuild the mesh, usually with a coarser cell size or a "
                           "larger lid dilation.",
                    failed_checks=list(failed),
                )
            if failed:
                # A mesh cut from a real DEM routinely carries a few marginal faces at
                # survey steps and seams. That is the terrain, not a meshing fault.
                log.info("checkMesh: runnable, with marginal faces: %s", "; ".join(failed))

        nprocs = n_processors if n_processors is not None else cfg.openfoam.n_processors
        if nprocs and nprocs > 1:
            # The ranks of the job, not the number the case was built with: the decomposition has to give each rank its domain, or mpirun stops at once with "Number of processor directories = 1 is not equal to the number of ranks" (found by the mesh study of 2026-10-17, and the same for the MPI ranks option of an openfoam-run job). The runtime uses a dictionary of the run's own where the case's file names another count, so the case's file is never changed.
            runtime.decompose(case_dir, n_processors=int(nprocs))

        for stage in dicts.stages(cfg):
            if ctx is not None:
                ctx.check_cancelled()
                ctx.sink.step(f"openfoam:{stage.name}")
            # start_from, not the stage name: a rigid-lid case has ONE stage and it is called "run", but it starts from t=0 like a spin-up does. Keying off the name told the progress bar the run began at spinup_time and it reported a run that was already 8 s in before the first step.
            start = (0.0 if stage.start_from == "startTime"
                     else cfg.openfoam.spinup_time)
            proc = runtime.run_stage(
                case_dir, stage.name, end_time=stage.end_time, start_time=start,
                n_processors=nprocs, cfg=cfg,
                show_progress=ctx is None, sink=sink,
                should_stop=ctx.should_stop if ctx is not None else None)
            if ctx is not None:
                ctx.check_cancelled()
            if proc.returncode != 0:
                raise errors.SolverError(
                    f"interFoam stage {stage.name!r} exited with code "
                    f"{proc.returncode}",
                    subject=str(case_dir),
                    remedy=f"Read {case_dir / cfg.log_file} for the first error.",
                    component="interfoam",
                    return_code=proc.returncode,
                    stage=stage.name,
                )

        if reconstruct and nprocs and nprocs > 1:
            runtime.reconstruct(case_dir)
        return case_dir

    # -- after the run ------------------------------------------------------------
    def postprocess(self, cfg, ctx: Any = None):
        """Reports, and the optional VTK conversion.

        Both are guarded: the run itself has already succeeded by the time this is
        called, and a missing figure is not worth discarding hours of solver time.
        """
        from axqua.solvers.openfoam import report
        from axqua.solvers.openfoam.runtime import OpenFoamRuntime

        if _is_sediment(ctx):
            return self._book_sediment(cfg, ctx)
        if _is_mesh_study(ctx):
            return {}                    # the study wrote its own report
        case_dir = Path(cfg.openfoam_case_dir)
        out: dict[str, Any] = {}

        try:
            history, files = report.write_report(cfg, case_dir)
            out["discharge"] = history
            if ctx is not None:
                for path in files:
                    ctx.record(path.name, path,
                               kind="figure" if path.suffix == ".png" else "table")
        except Exception as exc:  # noqa: BLE001
            log.warning("discharge report skipped: %s: %s", type(exc).__name__, exc)

        try:
            # Whether the water ever reached the lid or the lateral wall - i.e. whether
            # the answer was set by a meshing decision rather than by the hydraulics,
            # which nothing else in the output reveals.
            out["surface_freedom"] = report.surface_freedom(cfg, case_dir)
            # ...and, under a rigid lid, whether the mode applied at all. That is a
            # property of the prescribed surface, so it is recorded at build time and
            # read back rather than measured from the result.
            out["lid_applicability"] = report.lid_applicability(cfg, case_dir)
        except Exception as exc:  # noqa: BLE001
            log.debug("surface-freedom report skipped: %s: %s", type(exc).__name__, exc)

        try:
            # The result seen from above, as a file QGIS draws like a 2D result and
            # a finer model can be started from.
            from axqua.solvers.openfoam import planview

            # after a build there is no result yet, which is nothing to report
            if planview.latest_time(case_dir) is not None:
                _, summary = planview.write(cfg, case_dir)
                out["plan_view"] = summary
                if ctx is not None and ctx.manifest is not None:
                    ctx.manifest.summary["depth_averaged"] = summary
        except Exception as exc:  # noqa: BLE001
            log.warning("depth-averaged result skipped: %s: %s",
                        type(exc).__name__, exc)

        if _wants_vtk(ctx, cfg):
            try:
                OpenFoamRuntime(cfg.openfoam).to_vtk(case_dir, latest=False)
                out["vtk"] = str(case_dir / "VTK")
            except Exception as exc:  # noqa: BLE001
                log.warning("foamToVTK failed: %s: %s", type(exc).__name__, exc)
        return out

    def _book_sediment(self, cfg, ctx: Any):
        """The bed and the budget of a finished fine sediment run.

        Not guarded like the reports of the free-surface run: this evaluation IS the
        result of the simulation, and a job that ended without it has not produced
        what it was started for. After a build there is nothing to book yet.
        """
        from axqua.solvers.openfoam import sediment

        if ctx.spec.meta.verb != "run":
            return {}
        result = sediment.evaluate(cfg)
        for line in result.lines():
            log.info("%s", line)
        if ctx.manifest is not None:
            ctx.manifest.summary["fine_sediment"] = result.budget
        return {"fine_sediment": result.budget}

    def study(self, cfg, capability=None, *, ctx: Any = None,
              launch_mode: str = "prepare", checkout=None, rebuild: bool = False,
              seed=None, **kwargs):
        """Multi-run investigations: the calibration campaign and the mesh
        convergence study.

        The 2D seed is passed in rather than obtained here: ``axqua.prerun`` imports
        the TELEMAC backend transitively, and this module must not reach into it
        (``tests/test_capabilities.py`` enforces that by reading the source).
        """
        from axqua.core.capabilities import Capability

        if capability is Capability.MESH_CONVERGENCE:
            return self._mesh_convergence(cfg, ctx=ctx, seed=seed, **kwargs)
        if capability is not Capability.CALIBRATION:
            return super().study(cfg, capability, **kwargs)
        from axqua.solvers.openfoam.calibration import run_openfoam_calibration
        return run_openfoam_calibration(
            cfg, launch_mode=launch_mode, checkout=checkout, rebuild=rebuild,
            state=seed, ctx=ctx, **kwargs)

    def _mesh_convergence(self, cfg, *, ctx: Any = None, seed=None,
                          discharge: float | None = None, refinement_ratio: float = 1.3,
                          tolerance: float = 0.05, n_coarser: int = 1, n_finer: int = 2,
                          max_extra_levels: int = 3, auto_extend: bool = False,
                          n_processors: int | None = None, **_ignored):
        """The mesh convergence study of the free-surface model
        (:mod:`axqua.solvers.openfoam.convergence`)."""
        from axqua import convergence
        from axqua.solvers.openfoam import convergence as of_convergence

        report = of_convergence.run_openfoam_mesh_convergence(
            cfg, seed=seed, discharge=discharge, refinement_ratio=refinement_ratio,
            tolerance=tolerance, n_coarser=n_coarser, n_finer=n_finer,
            max_extra_levels=max_extra_levels, auto_extend=auto_extend,
            n_processors=n_processors,
            # never the study's own stdin prompt (see jobs.interaction)
            ask=(ctx.ask if ctx is not None else None),
            on_level=convergence.level_progress(ctx),
            check_cancelled=(ctx.check_cancelled if ctx is not None else None))
        if ctx is not None:
            ctx.progress(levels_done=len(report.levels), levels_total=len(report.levels),
                         converged=bool(getattr(report, "converged", False)))
        convergence.write_study_report(
            report, Path(cfg.postprocessing_dir) / of_convergence.FOLDER,
            of_convergence.STEM, ctx)
        return report

    def extract_objective(self, cfg, ctx: Any = None) -> float | None:
        """The relative inlet/outlet discharge imbalance - what the run is judged on."""
        from axqua.solvers.openfoam import report
        if _is_mesh_study(ctx):
            return None
        if _is_sediment(ctx):
            # how far the sediment budget of the reach is from closing, as a share
            summary = getattr(getattr(ctx, "manifest", None), "summary", {}) or {}
            closure = (summary.get("fine_sediment") or {}).get("closure")
            return float(closure) if closure is not None else None
        try:
            history = report.analyse(cfg, Path(cfg.openfoam_case_dir))
        except Exception as exc:  # noqa: BLE001
            log.debug("no discharge history: %s: %s", type(exc).__name__, exc)
            return None
        # Direct attribute access, not getattr(..., None): this read used to name a
        # field DischargeHistory did not define, so it silently returned None for
        # every run. A real attribute means a future rename fails loudly instead.
        value = history.final_imbalance
        return float(value) if value is not None else None

    def describe_results(self, cfg) -> list:
        """What a visualizer can open for this case.

        Cheap by contract: directory listings and the ``0/`` field names, no
        pyvista and no solver launch, because a caller may ask this just to decide
        which figures are available. ``case.foam`` is handed over as-is - VisIt and
        ParaView both read an OpenFOAM case directory natively, so nothing is
        converted here.
        """
        from axqua.postproc.dataset import Dataset

        case_dir = Path(cfg.openfoam_case_dir)
        foam = case_dir / "case.foam"
        if not (case_dir / "constant" / "polyMesh" / "faces").is_file():
            return []
        if not foam.is_file():
            try:
                foam.write_text("")
            except OSError:
                return []

        times: list[float] = []
        # the top level, else the first domain of a parallel run (also of a collated one)
        for root in (case_dir, case_dir / "processor0",
                     *sorted(case_dir.glob("processors[0-9]*"))):
            if not root.is_dir():
                continue
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    times.append(float(entry.name))
                except ValueError:
                    continue
            if times:
                break

        fields: set[str] = set()
        zero = case_dir / "0"
        if zero.is_dir():
            fields = {f.name for f in zero.iterdir() if f.is_file()}

        try:
            from axqua.solvers.openfoam.polymesh import read_patch_names
            patches = set(read_patch_names(case_dir))
        except Exception as exc:  # noqa: BLE001 - a listing must never raise
            log.debug("could not read patch names: %s: %s", type(exc).__name__, exc)
            patches = set()

        notes = ()
        if cfg.openfoam.mode == "rigid-lid":
            # 0/alpha.water EXISTS in a rigid-lid case, but it is uniformly 1: the
            # domain is water only. Advertising it as a field would let a scene
            # contour alpha = 0.5 and render an empty isosurface - a blank figure
            # that looks like a successful render. What the case actually has is a
            # lid patch carrying the prescribed surface, which is reported below.
            fields.discard("alpha.water")
            # Worth carrying into every figure: under a rigid lid the surface is an
            # input, so a picture of it shows the 2D seed rather than a 3D result.
            notes = ("rigid lid: the free surface is prescribed by the 2D seed, "
                     "not solved",)

        return [Dataset(
            solver="openfoam", kind="openfoam", path=foam,
            times=tuple(sorted(times)), fields=frozenset(fields),
            patches=frozenset(patches),
            vector_fields={"U": ("U_x", "U_y", "U_z")},
            notes=notes,
        )]

    def export_qgis_results(self, cfg, ctx: Any = None):
        """List what QGIS can open. No PyQGIS, no copies of the case."""
        if ctx is None:
            return []
        if _is_sediment(ctx):
            return self._export_sediment(cfg, ctx)
        if _is_mesh_study(ctx):
            from axqua.solvers.openfoam.convergence import FOLDER, STEM

            workbook = Path(cfg.postprocessing_dir) / FOLDER / f"{STEM}.xlsx"
            ctx.record("Mesh-convergence report", workbook, kind="table")
            return [workbook] if workbook.exists() else []
        case_dir = Path(cfg.openfoam_case_dir)
        written: list[Path] = []

        # First what QGIS draws without a conversion: depth and velocity of each
        # column of the lattice, written after the run (planview).
        from axqua.jobs.results import STYLE_DEPTH, STYLE_VELOCITY
        from axqua.solvers.openfoam.planview import RESULT_NAME

        plan = case_dir / RESULT_NAME
        if plan.exists():
            label = "OpenFOAM result (depth-averaged)"
            ctx.record(label, plan, kind="mesh", style=STYLE_DEPTH,
                       variable="WATER DEPTH")
            ctx.record(f"{label} - velocity", plan, kind="mesh", style=STYLE_VELOCITY,
                       variable="SCALAR VELOCITY")
            written.append(plan)

        # what the case asks to offer for ParaView and VisIt (postproc.openfoam_outputs)
        # (an empty list is a choice: nothing for either program)
        outputs = getattr(getattr(cfg, "postproc", None), "openfoam_outputs", None)
        outputs = set(("paraview", "visit") if outputs is None else outputs)
        foam = case_dir / "case.foam"
        if foam.exists() and "paraview" in outputs:
            ctx.record("OpenFOAM case (ParaView)", foam, kind="file")
            written.append(foam)
        control = case_dir / "system" / "controlDict"
        if control.exists() and "visit" in outputs:
            # VisIt opens an OpenFOAM case through its controlDict, not case.foam
            ctx.record("OpenFOAM case (VisIt)", control, kind="file")

        # VTK is what QGIS can actually read; the .foam file needs ParaView. Both are
        # listed, and the plugin picks what it can load.
        vtk_root = case_dir / "VTK"
        if vtk_root.is_dir():
            for pvd in sorted(vtk_root.glob("*.pvd")):
                ctx.record(pvd.stem, pvd, kind="mesh")
                written.append(pvd)
            if not written or vtk_root.glob("*.vtk"):
                for vtk in sorted(vtk_root.rglob("*.vtu"))[:20]:
                    ctx.record(vtk.stem, vtk, kind="mesh")

        for name in ("discharge-convergence.csv", "discharge-convergence.png"):
            ctx.record(name, case_dir / name,
                       kind="figure" if name.endswith(".png") else "table")
        return written


    def _export_sediment(self, cfg, ctx: Any):
        """The bed as a polygon layer, colored by what the bed takes up."""
        from axqua.solvers.openfoam import sediment

        case_dir = Path(cfg.fine_sediment_case_dir)
        layer = case_dir / sediment.BED_LAYER_NAME
        period = cfg.fine_sediment.period is not None
        ctx.record(
            "Fine sediment at the bed", layer, kind="vector", style="graduated",
            variable="change" if period else "net",
            description=("change of the share of fines in the bed layer over the "
                         "period [-]" if period else
                         "mass the bed takes up per area and day [kg/m2/day]"))
        ctx.record("Sediment budget", case_dir / sediment.BUDGET_NAME, kind="table")
        ctx.record("Fine sediment case (ParaView)", case_dir / "case.foam", kind="file")
        return [layer] if layer.exists() else []


BACKEND = OpenFoamBackend()


def _is_sediment(ctx: Any) -> bool:
    """Whether the job at hand is one of the fine sediment simulation."""
    return getattr(ctx, "capability", None) is Capability.MORPHODYNAMICS


def _is_mesh_study(ctx: Any) -> bool:
    """Whether the job at hand is the mesh convergence study."""
    return getattr(ctx, "capability", None) is Capability.MESH_CONVERGENCE


def _wants_vtk(ctx: Any, cfg: Any = None) -> bool:
    """VTK conversion is opt-in: it can double the size of an already large case.

    The option of the job decides; without one, the case decides
    (``postproc.openfoam_outputs`` naming ``vtk``), and then the environment.
    """
    if ctx is None:
        return False
    explicit = getattr(getattr(ctx, "options", None), "to_vtk", None)
    if explicit is not None:
        return bool(explicit)
    outputs = getattr(getattr(cfg, "postproc", None), "openfoam_outputs", None) or ()
    if "vtk" in outputs:
        return True
    postprocess = (getattr(ctx, "spec", None) and
                   ctx.spec.environment.get("postprocess")) or []
    return "foamToVTK" in postprocess


def _sink_for(ctx: Any, name: str):
    if ctx is None:
        return None
    from axqua.jobs.events import TeeSink, TextLogSink
    return TeeSink(ctx.sink, TextLogSink(ctx.solver_sink(name)))
