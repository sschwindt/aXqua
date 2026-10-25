"""End-to-end orchestration of the TELEMAC case-build pipeline."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from axqua import calibration, dem, hydraulics
from axqua.solvers.telemac import boundary, fortran, mesh, steering
from axqua.config import Config
from axqua.core.capabilities import refresh_markers
from axqua.env import TelemacRuntime
from axqua.logsetup import log_step, setup_logging

log = logging.getLogger("axqua")


@dataclass
class Artifacts:
    geometry_slf: Path | None = None
    boundary_cli: Path | None = None
    friction_tbl: Path | None = None
    initial_conditions: Path | None = None
    cas_file: Path | None = None
    unsteady_cas: Path | None = None
    liquid_boundaries: Path | None = None
    liquid_boundaries_meta: Path | None = None
    gaia_cas: Path | None = None
    source_regions: Path | None = None   # SOURCE REGIONS DATA FILE (losing-gaining reach)
    user_fortran: Path | None = None     # FORTRAN FILE folder (percolation.mode: fortran)
    # the hydrology run (hydrology mode): its .cas and the files it reads
    hydrology_cas: Path | None = None
    hydrology_files: dict[str, Path] = field(default_factory=dict)
    ground_truth: Path | None = None
    calibration_csv: Path | None = None
    hbc_config: Path | None = None
    rasters: dict[str, Path] = field(default_factory=dict)
    # what stage 0 derived from CAD, when the case is built from surfaces
    surfaces: list[Path] = field(default_factory=list)


def run(cfg: Config, *, validate_env: bool = True, dry_run: bool = False,
        log_to_file: bool = True) -> Artifacts:
    """Build the full case. Returns the produced :class:`Artifacts`.

    Parameters
    ----------
    validate_env : check that the TELEMAC environment can be sourced first.
    dry_run : after building, launch the solver once to confirm the case is valid.
    log_to_file : attach this build's ``<model_dir>/axqua.log`` (set False
        when a caller already routes everything to its own compound log, e.g. the
        mesh-convergence study logging the per-level builds to postprocessing/).
    """
    cfg.ensure_dirs()
    # compound logfile for the build, in the simulation (model) output folder. Opened
    # before anything else so that stage 0 and a validation failure are recorded too.
    if log_to_file:
        setup_logging(cfg.model_path(cfg.log_file))
    art = Artifacts()
    # 0) CAD surfaces -> the geodata a surveyed case would have been given. Ahead of
    # everything else, including the rating synthesis below, which reads the DEM and
    # the outflow line that this stage writes.
    if cfg.surfaces.active:
        from axqua import surface_stage

        with log_step("stage 0: derive geodata from the CAD surfaces"):
            produced = surface_stage.run(cfg)
            art.surfaces = produced.paths()
    # synthesize a missing outflow rating before validating: with
    # boundaries.rating_method set, leaving stage_discharge unset is a legitimate
    # "derive it from the geodata at the simulated Q" (see workflow.
    # synthesize_rating_if_missing), and it must land before validate() checks it.
    if (cfg.outflow_condition == "stage_discharge"
            and cfg.boundaries.stage_discharge is None
            and cfg.boundaries.prescribed_flowrate is not None):
        from axqua.workflow import synthesize_rating_if_missing
        synthesize_rating_if_missing(cfg, float(cfg.boundaries.prescribed_flowrate))
    cfg.validate()
    if cfg.hydrology_mode:
        _hydrology_checkup(cfg)
    t_start = time.perf_counter()
    log.info("build start: case '%s' -> %s", cfg.name, cfg.model_dir)

    runtime = TelemacRuntime(cfg.telemac)
    if validate_env or dry_run:
        with log_step("validate TELEMAC environment"):
            version = runtime.check_available()
            log.info("TELEMAC environment OK (python %s)", version)

    # 1) DEM -> ROI
    with log_step("stage 1/5: clip DEM(s) to the region of interest"):
        art.rasters = dem.run(cfg)
        dem_initial = art.rasters["dem_initial_roi"]

    # 2) mesh + bathymetry + geometry SELAFIN
    with log_step("stage 2/5: generate mesh, bathymetry and geometry SELAFIN"):
        the_mesh, art.geometry_slf = mesh.run(cfg, dem_initial)
        log.info("  mesh: %d nodes, %d elements, %d boundary nodes",
                 the_mesh.npoin, the_mesh.nelem, the_mesh.boundary_nodes.size)

    # 3) boundary conditions
    with log_step("stage 3/5: write boundary conditions (.cli)"):
        art.boundary_cli, liquids = boundary.write_cli(cfg, the_mesh)
        for lb in liquids:
            log.info("  liquid boundary %d: %s (%d nodes)", lb.index, lb.kind, lb.n_nodes)
        # persist the FRONT2 boundary numbering so the standalone unsteady / 3D
        # scripts can drive the hydrograph without rebuilding the mesh
        art.liquid_boundaries_meta = boundary.dump_liquid_boundaries(
            liquids, cfg.model_path(cfg.liquid_boundaries_json))

        # boundary prescribed values. The inflow series is optional (it drives a
        # later unsteady case); when given, read the full series (steady=False) so a
        # hydrograph is detected regardless of regime. The steady initial run uses the
        # scalar prescribed_flowrate, falling back to the series' representative value.
        inflow = (hydraulics.read_inflow(Path(cfg.boundaries.inflow), steady=False)
                  if cfg.boundaries.inflow is not None else None)
        if cfg.hydrology_mode:
            # a catchment is driven by its rain; the outlet takes what arrives, and
            # inflow lines (catchments upstream) are optional
            inflow_q = outflow_wse = None
            log.info("  hydrology mode: outlet %s, %d inflow line(s)",
                     cfg.outflow_condition, sum(lb.kind == "inflow" for lb in liquids))
        else:
            inflow_q, outflow_wse = _river_boundary_values(cfg, inflow)

    # 4) steering files
    with log_step("stage 4/5: write friction table and steering (.cas)"):
        art.friction_tbl = steering.write_friction_tbl(cfg)
        if cfg.hydrology_mode:
            _write_hydrology(cfg, the_mesh, liquids, inflow, art)
        else:
            _write_river_steering(cfg, the_mesh, liquids, inflow, inflow_q,
                                  outflow_wse, art)

    # 5) ground-truth -> calibration CSV + HydroBayesCal config.
    # The model setup (stages 1-4) is complete at this point. Ground-truth feeds
    # only the calibration / HydroBayesCal setup, so any problem with it must NOT
    # abort the build: warn and skip the HydroBayesCal setup, leaving the initial
    # model setup intact so it can still be run/converged and calibrated later.
    # Two failure modes: missing inputs (reported by cfg.ground_truth.problems()
    # before we touch them) and data that do not match (e.g. FlowTracker verticals
    # with no DGPS position, or no category supplying the calibration quantities),
    # which surface as exceptions while compiling.
    with log_step("stage 5/5: compile ground truth, calibration CSV and HydroBayesCal config"):
        gt_problems = cfg.ground_truth.problems()
        if gt_problems:
            for problem in gt_problems:
                log.warning("%s", problem)
            log.warning(
                "skipping HydroBayesCal setup; the model setup is complete and unaffected"
            )
        else:
            try:
                art.ground_truth = calibration.compile_ground_truth(cfg)
                if art.ground_truth:
                    log.info("  compiled tidy ground-truth table -> %s", art.ground_truth)
                art.calibration_csv = calibration.build_calibration_csv(cfg)
                art.hbc_config = calibration.emit_hbc_config(cfg, art.calibration_csv)
            except Exception as exc:  # noqa: BLE001 - degrade gracefully, keep the model
                art.ground_truth = art.calibration_csv = art.hbc_config = None
                log.warning(
                    "ground-truth data do not match; skipping HydroBayesCal setup "
                    "(the model setup is complete and unaffected): %s: %s",
                    type(exc).__name__, exc,
                )

    if dry_run:
        # lazy import: axqua.workflow is a sibling that must not be pulled in at
        # module import time (the package __init__ wires both up)
        from axqua.workflow import run_solver_streaming

        # the hydrology mode builds hydrology2d.cas and no steady2d.cas; its duration
        # comes from the rain, not from hydrodynamics.duration
        cas, duration = cfg.cas_file, None
        if cfg.hydrology_mode:
            from axqua.solvers.telemac.backend import _cas_duration
            cas = cfg.hydrology_cas_file
            duration = _cas_duration(cfg, cas)
        with log_step(f"dry run: launch {cfg.telemac.solver} once to validate the case"):
            # stream the listing live with the simulated-time progress bar (same
            # look as initial_run.py) on the configured core count - this is also
            # the per-level solver run of the mesh-convergence study
            proc = run_solver_streaming(runtime, cfg, cas_file=cas, duration=duration)
            log.info("solver finished rc=%d", proc.returncode)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"{cfg.telemac.solver} failed (rc={proc.returncode}) on "
                    f"{cas}; see the listing above / {cfg.model_dir}")

    # refresh the case's MODEL= markers so the folder describes itself as it is now
    refresh_markers(cfg)
    log.info("build done: case '%s' in %.2fs", cfg.name, time.perf_counter() - t_start)
    return art


def _hydrology_checkup(cfg: Config) -> None:
    """The hydrology checkup before the build: warnings logged, errors stop it."""
    from axqua.core import errors
    from axqua.hydrology.checks import run_checks

    with log_step("hydrology checkup (rain, weather, soil, outlet, terrain)"):
        findings = run_checks(cfg, terrain=True)
        for finding in findings:
            (log.error if finding.severity == "error" else log.warning)(
                "  %s", finding.line())
    bad = [f for f in findings if f.severity == "error"]
    if bad:
        raise errors.ConfigError(bad[0].message, subject=bad[0].subject,
                                 remedy=bad[0].remedy)


def _river_boundary_values(cfg: Config, inflow) -> tuple[float, float | None]:
    """The steady inflow discharge and outflow level of a river reach."""
    if cfg.boundaries.prescribed_flowrate is not None:
        inflow_q = float(cfg.boundaries.prescribed_flowrate)
    elif inflow is not None:
        inflow_q = inflow.steady_value
    else:
        raise ValueError(
            "no steady discharge for the build: set boundaries.prescribed_flowrate "
            "(m3/s) in case-config.yml, or provide boundaries.inflow."
        )
    if cfg.boundaries.outflow_condition == "free":
        outflow_wse = None
        log.info("  prescribed inflow Q=%.3f m3/s, free (Neumann) outflow", inflow_q)
    else:
        outflow_wse = resolve_outflow_wse(cfg, inflow_q)
        log.info("  prescribed inflow Q=%.3f m3/s, outflow WSE=%.3f m (%s)",
                 inflow_q, outflow_wse, cfg.boundaries.outflow_condition)
    return inflow_q, outflow_wse


def _write_river_steering(cfg: Config, the_mesh, liquids, inflow, inflow_q,
                          outflow_wse, art: Artifacts) -> None:
    """Stage 4 of a river reach: GAIA, initial state, sources, steady and
    unsteady steering."""
    art.gaia_cas = steering.write_gaia_cas(cfg)
    gaia_name = art.gaia_cas.name if art.gaia_cas else None
    # turbulence model selected from the actual mesh resolution + velocity guess
    turb_model, turb_why = steering.select_turbulence_model(cfg, the_mesh)
    log.info("  turbulence model %d (%s): %s", turb_model,
             steering.TURB_NAMES.get(turb_model, "?"), turb_why)
    # initial condition. Default is a DRY START - only a thin plug at the inflow
    # is wetted (a fully dry bed makes DEBIMP abort at the prescribed-Q inflow);
    # the channel-wide hotstart is used only when prewet_depth is set (e.g. the
    # mesh-convergence study). Both continue from the written SELAFIN.
    prev_comp = None
    if cfg.initialization.prewet_depth is not None:
        art.initial_conditions = steering.write_initial_conditions(cfg, the_mesh)
        prev_comp = art.initial_conditions.name
    else:
        art.initial_conditions = steering.write_dry_start_conditions(cfg, the_mesh)
        prev_comp = art.initial_conditions.name if art.initial_conditions else None
    # internal source/sink REGIONS for a losing-gaining reach (empty unless the
    # liquid-boundary layer carries internal 'int-*' lines); shared by every .cas
    source_regions = boundary.exchange_regions(cfg, the_mesh)
    for r in source_regions:
        log.info("  internal source region %s: %+.4f m3/s over %d node(s), "
                 "%.0f m2", r.name, r.discharge, r.n_nodes, r.area)
    # The water table the generated routine uses as the drain's taper floor must
    # be the one the pre-wet seeded the pools with, so it is re-fitted here from
    # the initial-conditions file that was just written. With explicit
    # percolation.water_table_levels both fits read the same numbers and are
    # identical by construction; derived from the seeded surface they can differ
    # by a centimeter or so near the internal lines.
    plane = None
    if cfg.percolation.water_table == "phreatic" and art.initial_conditions:
        import numpy as np

        from axqua.solvers.telemac import watertable
        from axqua.core import selafin

        ic = selafin.read_slf(art.initial_conditions)["values"]["WATER DEPTH"]
        plane = watertable.fit_phreatic_plane(
            cfg, the_mesh, surface=np.asarray(the_mesh.bottom, float) + ic)
    if source_regions:
        if cfg.gain_lose.active and cfg.gain_lose.implementation == "fortran":
            # USER_RAIN percolation: the generated routine withdraws over the
            # patch depth-limited and reinjects at the gaining line; no
            # SOURCE REGIONS file (the .cas carries FORTRAN FILE + RAIN keys).
            art.user_fortran = fortran.write_user_fortran(cfg, source_regions,
                                                          plane=plane)
            log.info("  percolation USER_RAIN routine -> %s", art.user_fortran)
        else:
            art.source_regions = steering.write_source_regions(cfg, source_regions)
    # the initial run is ALWAYS steady (steady2d.cas)
    art.cas_file = steering.write_cas(
        cfg, liquids, inflow_q, outflow_wse, gaia_cas=gaia_name,
        previous_computation=prev_comp, turbulence_model=turb_model,
        source_regions=source_regions,
    )
    # additionally write the unsteady hydrograph case when a varying inflow
    # series is available (a constant-Q inflow needs no unsteady run)
    if _has_hydrograph(inflow):
        art.liquid_boundaries = steering.write_liquid_boundaries(
            cfg, liquids, inflow, _outflow_wse_fn(cfg))
        art.unsteady_cas = steering.write_cas(
            cfg, liquids, float(inflow.discharge[0]), outflow_wse, gaia_cas=gaia_name,
            previous_computation=prev_comp, turbulence_model=turb_model,
            unsteady=True, liquid_boundaries_file=art.liquid_boundaries.name,
            duration=_hydrograph_duration(inflow), out_name=cfg.unsteady_cas_file,
            source_regions=source_regions,
        )
        log.info("  unsteady hydrograph case -> %s (Q(t) from %s)",
                 art.unsteady_cas.name, art.liquid_boundaries.name)
    else:
        log.info("  inflow is a constant discharge; no unsteady2d.cas written")


def _write_hydrology(cfg: Config, the_mesh, liquids, inflow, art: Artifacts) -> None:
    """Stage 4 of a hydrology case: forcing, runoff routine, outlet and the .cas.

    The run starts dry; only an inflow line gets the plug of the dry start, since
    TELEMAC cannot prescribe a discharge through a dry section. No steady case is
    written: a catchment has no steady state to start from.
    """
    from axqua.solvers.telemac import hydrology

    turb_model, turb_why = steering.select_turbulence_model(cfg, the_mesh)
    log.info("  turbulence model %d (%s): %s", turb_model,
             steering.TURB_NAMES.get(turb_model, "?"), turb_why)
    prev_comp = None
    if any(lb.kind == "inflow" for lb in liquids):
        art.initial_conditions = steering.write_dry_start_conditions(cfg, the_mesh)
        prev_comp = art.initial_conditions.name if art.initial_conditions else None
    written = hydrology.write_case(cfg, the_mesh, liquids, inflow,
                                   turbulence_model=turb_model,
                                   previous_computation=prev_comp)
    art.hydrology_cas = written.pop("cas")
    art.user_fortran = written.pop("user_fortran")
    art.liquid_boundaries = written.pop("liquid_boundaries", None)
    art.hydrology_files = written


def _has_hydrograph(inflow) -> bool:
    """True when the inflow carries a genuinely varying time series (a hydrograph),
    not a single value or a constant-Q placeholder."""
    import numpy as np

    if inflow is None:
        return False
    q = np.asarray(inflow.discharge, dtype=float)
    return inflow.times_s is not None and q.size > 1 and float(np.ptp(q)) > 1e-6


def _hydrograph_duration(inflow) -> float:
    """Total simulated time [s] spanned by the inflow hydrograph."""
    import numpy as np

    t = np.asarray(inflow.times_s, dtype=float)
    return float(t[-1] - t[0]) or 3600.0


def _outflow_wse_fn(cfg: Config):
    """Callable Q -> outflow WSE for the hydrograph file, matching the outflow
    condition (free outflow prescribes nothing)."""
    cond = cfg.outflow_condition
    if cond in ("free", "normal_depth"):
        return None
    if cond == "elevation":
        wse = float(cfg.boundaries.prescribed_elevation)
        return lambda q: wse
    return hydraulics.read_stage_discharge(Path(cfg.boundaries.stage_discharge))




# Both backends prescribe the same downstream stage, so resolving it moved to
# axqua.hydraulics; re-exported here (and under its old private spelling)
# because this module published it.
from axqua.hydraulics import resolve_outflow_wse  # noqa: E402
_resolve_outflow_wse = resolve_outflow_wse
