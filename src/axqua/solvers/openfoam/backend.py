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
        ``--no-pre-run`` behaviour rather than a failure.
        """
        from axqua.solvers.openfoam import case as of_case
        from axqua.solvers.openfoam.hotstart import load_hotstart

        if cell_size is not None:
            cfg.openfoam.cell_size = cell_size
        if layers is not None:
            cfg.openfoam.n_layers = layers

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

    # -- run ----------------------------------------------------------------------
    def run(self, cfg, capability: Capability | None = None, *, ctx: Any = None,
            n_processors: int | None = None, reconstruct: bool = True,
            check_mesh: bool = True, **_ignored):
        from axqua.solvers.openfoam import dicts
        from axqua.solvers.openfoam.runtime import OpenFoamRuntime

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
            # scotch, set in the case: the wetted corridor is sinuous, so a geometric
            # split would hand some ranks empty space.
            runtime.decompose(case_dir)

        for stage in dicts.stages(cfg):
            if ctx is not None:
                ctx.check_cancelled()
                ctx.sink.step(f"openfoam:{stage.name}")
            start = 0.0 if stage.name == "spinup" else cfg.openfoam.spinup_time
            proc = runtime.run_stage(
                case_dir, stage.name, end_time=stage.end_time, start_time=start,
                n_processors=nprocs, cfg=cfg,
                show_progress=ctx is None, sink=sink,
                should_stop=ctx.should_stop if ctx is not None else None)
            if ctx is not None:
                ctx.check_cancelled()
            if proc.returncode != 0:
                raise errors.SolverError(
                    f"interFoam stage {stage.name!r} exited with code {proc.returncode}",
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
        except Exception as exc:  # noqa: BLE001
            log.debug("surface-freedom report skipped: %s: %s", type(exc).__name__, exc)

        if _wants_vtk(ctx):
            try:
                OpenFoamRuntime(cfg.openfoam).to_vtk(case_dir, latest=False)
                out["vtk"] = str(case_dir / "VTK")
            except Exception as exc:  # noqa: BLE001
                log.warning("foamToVTK failed: %s: %s", type(exc).__name__, exc)
        return out

    def study(self, cfg, capability=None, *, ctx: Any = None,
              launch_mode: str = "prepare", checkout=None, rebuild: bool = False,
              seed=None, **kwargs):
        """Multi-run investigations. Currently only the calibration campaign.

        The 2D seed is passed in rather than obtained here: ``axqua.prerun`` imports
        the TELEMAC backend transitively, and this module must not reach into it
        (``tests/test_capabilities.py`` enforces that by reading the source).
        """
        from axqua.core.capabilities import Capability

        if capability is not Capability.CALIBRATION:
            return super().study(cfg, capability, **kwargs)
        from axqua.solvers.openfoam.calibration import run_openfoam_calibration
        return run_openfoam_calibration(
            cfg, launch_mode=launch_mode, checkout=checkout, rebuild=rebuild,
            state=seed, ctx=ctx, **kwargs)

    def extract_objective(self, cfg, ctx: Any = None) -> float | None:
        """The relative inlet/outlet discharge imbalance - what the run is judged on."""
        from axqua.solvers.openfoam import report
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
        """What a visualiser can open for this case.

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
        for root in (case_dir, case_dir / "processor0"):
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
        case_dir = Path(cfg.openfoam_case_dir)
        written: list[Path] = []

        foam = case_dir / "case.foam"
        if foam.exists():
            ctx.record("OpenFOAM case (ParaView)", foam, kind="file")
            written.append(foam)

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


BACKEND = OpenFoamBackend()


def _wants_vtk(ctx: Any) -> bool:
    """VTK conversion is opt-in: it can double the size of an already large case."""
    if ctx is None:
        return False
    explicit = getattr(getattr(ctx, "options", None), "to_vtk", None)
    if explicit is not None:
        return bool(explicit)
    postprocess = (getattr(ctx, "spec", None) and
                   ctx.spec.environment.get("postprocess")) or []
    return "foamToVTK" in postprocess


def _sink_for(ctx: Any, name: str):
    if ctx is None:
        return None
    from axqua.jobs.events import TeeSink, TextLogSink
    return TeeSink(ctx.sink, TextLogSink(ctx.solver_sink(name)))
