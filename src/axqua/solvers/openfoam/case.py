"""Assemble the complete OpenFOAM case: mesh, fields, dictionaries.

The counterpart of :func:`axqua.pipeline.run` for the 3D free-surface path. It
draws on exactly the same configuration the TELEMAC case was built from - the same
ROI, the same liquid boundaries, the same roughness zones, the same discharge and
the same outflow rating - so the two models are driven identically and their results
can be compared without arguing about the forcing.

What it produces, under ``<sim_dir>/openfoam/``::

    0/                alpha.water, U, p_rgh, nut, k, omega   (seeded from r2d.slf)
    constant/
      polyMesh/       points, faces, owner, neighbour, boundary
      g, transportProperties, turbulenceProperties
    system/
      controlDict, fvSchemes, fvSolution                     (the ACTIVE stage)
      fvOptions, decomposeParDict
      stage1-spinup/, stage2-run/                            (both dict sets)
    case.foam                                                (ParaView marker)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from axqua.config import Config
from axqua.core.capabilities import refresh_markers
from axqua.solvers.openfoam import (dicts, fields, mesh as ofmesh,
                                    planview, potential, quality)
from axqua.solvers.openfoam.hotstart import State2D, load_hotstart
from axqua.solvers.openfoam.polymesh import write_polymesh

log = logging.getLogger("axqua")

# multiple of the water velocity at which the limitVelocity constraint bites
DEFAULT_VELOCITY_CAP_FACTOR = 5.0

# The time step of a two-phase run, measured on the Isar example after the corrections
# of 2026-10-13 (1 m cells with 14 layers, thinnest layer 0.031 m, and 2 m cells with
# 8 layers, thinnest layer 0.057 m; water speed 0.80 m/s at the 95th percentile). Two
# limits, and both scale with the thinnest layer: the air next to the water reaches a
# Courant number of 0.9 at 0.026 s and 0.060 s, the water surface at 0.051 s and
# 0.100 s. Written as Courant x thinnest layer / (factor x water speed), the factor
# is 1.3 and 1.1 for the air and 0.68 and 0.64 for the surface.
AIR_SPEED_FACTOR = 1.2
SURFACE_SPEED_FACTOR = 0.65


def _describe_outflow(stage) -> str:
    """One line for the outlet prescription, scalar or per-patch.

    A crop with several open faces carries a different level on each, and the
    SPREAD is the number worth seeing: it is what says whether one scalar would
    have been an acceptable approximation.
    """
    from collections.abc import Mapping

    if stage is None:
        return "free outfall"
    if isinstance(stage, Mapping):
        levels = sorted(stage.values())
        if len(set(levels)) == 1:
            return f"stage held at {levels[0]:.3f} m a.s.l. on every outlet"
        return (", ".join(f"{p} {v:.3f}" for p, v in stage.items())
                + f" m a.s.l. (spread {levels[-1] - levels[0]:.3f} m)")
    return f"stage held at {float(stage):.3f} m a.s.l."


@dataclass
class OpenFoamArtifacts:
    """Everything the build produced, mirroring :class:`axqua.pipeline.Artifacts`."""

    case_dir: Path
    polymesh_dir: Path
    field_files: list[Path] = field(default_factory=list)
    dict_files: list[Path] = field(default_factory=list)
    mesh: object = None                 # openfoam.mesh.OpenFoamMesh
    report: object = None               # openfoam.quality.MeshReport
    discharge: float = 0.0
    inlet_discharges: dict[str, float] = field(default_factory=dict)
    outflow_stage: "float | dict[str, float] | None" = None
    velocity_cap: float = 0.0
    hotstart: Path | None = None
    notes: list[str] = field(default_factory=list)
    cfg: object = None                  # the Config the case was built from
    state: object = None                # the 2D hotstart it was seeded from

    def lines(self) -> list[str]:
        seed = self.hotstart.name if self.hotstart else "none (cold start, flat lid)"
        inflow = ", ".join(f"{k} {v:g} m3/s"
                           for k, v in self.inlet_discharges.items())
        outflow = _describe_outflow(self.outflow_stage)
        out = [f"OpenFOAM case built in {self.case_dir}"]
        out.append(f"  hotstart    : {seed}")
        out.append(f"  inflow      : {inflow}")
        out.append(f"  outflow     : {outflow}")
        out.append(f"  velocity cap: {self.velocity_cap:.2f} m/s "
                   "(limitVelocity, keeps the air phase off the time step)")
        for note in self.notes:
            out.append(f"  note        : {note}")
        return out


def _inlet_discharges(of_mesh, cfg: Config, total: float) -> dict[str, float]:
    """Discharge per inlet patch.

    Each inflow line's own ``Target flow`` when the layer carries one (isar: 1.6 and
    0.8 m3/s on the two upstream branches); otherwise the total reach discharge split
    by patch face count - a width proxy, and the same rule
    :func:`axqua.steering._prescribed_arrays` applies to the TELEMAC boundaries,
    so the two models feed the branches identically.
    """
    patches = of_mesh.inlet_patches
    if not patches:
        raise ValueError(
            "the mesh has no inlet patch: no line of boundaries.liquid_boundaries "
            "tagged 'inflow' came within the matching tolerance of the domain edge. "
            "Check that the liquid lines lie on the wetted-corridor boundary, or "
            "raise openfoam.boundary_tolerance.")
    per_line = {p: of_mesh.inlet_discharge[p] for p in patches
                if p in of_mesh.inlet_discharge}
    if len(per_line) == len(patches):
        return per_line
    counts = {p: of_mesh.polymesh.patch(p).n_faces for p in patches}
    n_total = sum(counts.values()) or 1
    return {p: total * counts[p] / n_total for p in patches}


#: Discharges, as shares of the case's own, at which a derived rating curve is
#: tabulated for an outlet that follows it (``openfoam.outflow_boundary:
#: rating-curve``). The outlet of the BAW refuses to extrapolate, so the range has to
#: cover what the outflow does while the run settles.
RATING_SHARES = (0.1, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)


def _derive_rating(cfg, discharges, *, out):
    """A rating curve from the terrain at the outflow, by the case's own method."""
    from axqua import rating

    if getattr(cfg.boundaries, "rating_method", "trapezoid") == "section":
        return rating.synthesize_outflow_rating_from_section(cfg, discharges, out=out)
    return rating.synthesize_outflow_rating(cfg, discharges, out=out)


def rating_table(cfg, discharge: float, folder, *, derive=None
                 ) -> list[tuple[float, float]]:
    """The stage-discharge relation an outlet follows: ``[(Q, h), ...]`` sorted by Q.

    ``boundaries.stage_discharge`` where it has at least two discharges; otherwise a
    curve derived from the terrain over :data:`RATING_SHARES` of *discharge*, written
    to *folder* (one pair is enough for a fixed level, not for a relation).
    """
    from axqua.hydraulics import read_rating_pairs

    given = getattr(cfg.boundaries, "stage_discharge", None)
    if given and Path(given).is_file():
        q, h = read_rating_pairs(Path(given))
        if len(set(q.tolist())) >= 2:
            return [(float(a), float(b)) for a, b in zip(q, h)]
    out = Path(folder) / "rating-curve-openfoam.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    (derive or _derive_rating)(cfg, [discharge * f for f in RATING_SHARES], out=out)
    q, h = read_rating_pairs(out)
    return [(float(a), float(b)) for a, b in zip(q, h)]


def build_case(cfg: Config, *, state: State2D | None = None,
               hotstart: str | Path | None = None,
               case_dir: str | Path | None = None,
               discharge: float | None = None,
               uniform_bed_ks: bool = False) -> OpenFoamArtifacts:
    """Build the OpenFOAM case for *cfg*.

    *state* short-circuits reading the 2D result (used by tests); *hotstart* points
    at a different ``r2d.slf`` than the config's own; *discharge* overrides
    ``boundaries.prescribed_flowrate``. *uniform_bed_ks* writes one global bed
    roughness rather than the per-face list, which is what a calibration template
    needs (see :func:`axqua.solvers.openfoam.fields._wall_entries`).
    """
    of = cfg.openfoam
    of.validate()
    case_dir = Path(case_dir) if case_dir else cfg.openfoam_case_dir
    case_dir.mkdir(parents=True, exist_ok=True)

    if state is None:
        state = load_hotstart(cfg, hotstart)

    # The same shared helper the TELEMAC scripts use, so the 3D case takes its
    # discharge and its outflow rating from one source of truth. It also synthesises
    # the rating curve when the config leaves it unset - which isar deliberately
    # does, so each scenario derives its own from that scenario's geometry.
    from axqua.workflow import prepare_steady_inputs

    q = float(prepare_steady_inputs(cfg, discharge))
    if q <= 0:
        raise ValueError(
            "no steady discharge for the OpenFOAM build: set "
            "boundaries.prescribed_flowrate (m3/s) in case-config.yml.")

    # The level of the outflow is needed before the mesh, which walls an outlet above
    # it; a stage per outlet patch (openfoam.outlet_stage) is read by the mesher.
    tailwater = None
    rating = None
    if dicts.uses_rating_outlet(cfg):
        # the outlet follows a stage-discharge relation; the run starts from its level
        # at the discharge of the case, and the mesher walls the outlet above that
        rating = rating_table(cfg, q, case_dir)
        tailwater = float(np.interp(q, [r[0] for r in rating], [r[1] for r in rating]))
    elif of.outlet_stage is None and cfg.boundaries.outflow_condition != "free":
        from axqua.hydraulics import resolve_outflow_wse

        tailwater = float(resolve_outflow_wse(cfg, q))
    of_mesh = ofmesh.build_mesh(cfg, state=state, tailwater=tailwater)
    notes = list(of_mesh.notes)
    if state is not None:
        warning = state.resolution_check(ofmesh.plan_spacing(cfg))
        if warning:
            notes.append(warning)

    inlets = _inlet_discharges(of_mesh, cfg, q)
    if not of_mesh.outlet_patches:
        raise ValueError(
            "the mesh has no outlet patch: no 'outflow' line of "
            "boundaries.liquid_boundaries reached the domain edge. Without one the "
            "water has nowhere to leave and the run will simply fill up.")

    if of.outlet_stage is not None:
        stage = fields.outlet_stages(of.outlet_stage, of_mesh.outlet_patches)
        levels = sorted(stage.values())
        where = ("this domain is a sub-model, so its tailwater is the parent's "
                 "level at the crop face, not the case's own far-end prescription")
        if len(set(levels)) > 1:
            notes.append(
                "outlet stages " + ", ".join(f"{p} {v:.3f}" for p, v in stage.items())
                + f" m a.s.l. from openfoam.outlet_stage, spanning "
                  f"{levels[-1] - levels[0]:.3f} m - " + where)
        else:
            notes.append(f"outlet stage {levels[0]:.3f} m a.s.l. from "
                         f"openfoam.outlet_stage - {where}")
    elif rating is not None:
        stage = tailwater
        notes.append(
            f"outlet follows a stage-discharge relation of {len(rating)} pairs "
            f"(Q {rating[0][0]:.3g} to {rating[-1][0]:.3g} m3/s) with the boundary "
            f"condition waterLevel_alpha_prgh of the BAW; it starts at {stage:.3f} m "
            "a.s.l., the level at the discharge of the case. The library "
            f"{dicts.BAW_LIBRARY} has to be in the OpenFOAM installation (the one aXqua "
            "installs has it).")
        if len(of_mesh.outlet_patches) > 1:
            notes.append(
                f"{len(of_mesh.outlet_patches)} outlets each follow the same relation "
                "with the discharge through their own patch, which is right only where "
                "each outlet has a rating curve of that shape")
    elif cfg.boundaries.outflow_condition == "free":
        stage = None
        notes.append("free (Neumann) outfall: the model chooses its own tailwater")
    else:
        stage = tailwater
        notes.append(f"outlet stage {stage:.3f} m a.s.l. from "
                     f"boundaries.outflow_condition: {cfg.boundaries.outflow_condition}"
                     " - the same prescription the TELEMAC case uses")

    cap = of.air_velocity_cap
    if cap is None:
        reference = state.velocity_scale() if state is not None \
            else cfg.hydrodynamics.initial_velocity_guess
        cap = DEFAULT_VELOCITY_CAP_FACTOR * reference
        notes.append(f"limitVelocity cap {cap:.2f} m/s "
                     f"({DEFAULT_VELOCITY_CAP_FACTOR:g}x the reach's p95 water speed)")

    polymesh_dir = write_polymesh(of_mesh.polymesh, case_dir)
    # which column each cell is in and how thick it is: what a view of the result
    # from above needs, saved here so that it never depends on a second build
    planview.Columns.of(of_mesh).save(case_dir)
    if potential.is_potential(cfg):
        # Leg C: single phase, the free surface carried as `zeta` on the top
        # boundary rather than resolved as an interface. Different field set
        # (p/p_gh/U/zeta, no alpha) on the same water-only mesh the rigid lid uses.
        field_files = potential.write_potential_fields(of_mesh, cfg, case_dir,
                                                      state=state)
        fields.write_reference_level(case_dir, None)    # left by a two-phase build
    else:
        field_files = fields.write_fields(of_mesh, cfg, case_dir, state=state,
                                          outflow_stage=stage, discharges=inlets,
                                          uniform_bed_ks=uniform_bed_ks, rating=rating)
    dict_files = dicts.write_dicts(of_mesh, cfg, case_dir, velocity_cap=cap)
    (case_dir / "case.foam").write_text("")     # so ParaView can open the folder
    _write_build_record(cfg, of_mesh, case_dir, velocity_cap=float(cap))

    report = quality.assess(of_mesh, state=state,
                            roughness_constant=of.roughness_constant)

    art = OpenFoamArtifacts(
        case_dir=case_dir, polymesh_dir=polymesh_dir, field_files=field_files,
        dict_files=dict_files, mesh=of_mesh, report=report, discharge=q,
        inlet_discharges=inlets, outflow_stage=stage, velocity_cap=float(cap),
        hotstart=state.source if state is not None else None, notes=notes,
        cfg=cfg, state=state,
    )
    refresh_markers(cfg)      # the case folder now has an OpenFOAM mesh; say so
    for line in art.lines():
        log.info("%s", line)
    return art


#: Written beside ``case.foam`` so the build's own findings survive the terminal.
#: A build-time warning that only reaches a log is the failure this exists to stop:
#: munich-vsf ran 61 hours, balanced its discharge to -0.000%, held Courant at 0.90,
#: and carried no trace of the warning that said the answer could not be trusted.
BUILD_RECORD = "axqua-build.json"


def _write_build_record(cfg: Config, of_mesh, case_dir: Path, *,
                        velocity_cap: float) -> Path:
    """Record the build-time judgements the finished run has to be read against.

    Only what the run itself cannot tell you afterwards. The lid-step ratio is the
    whole point: it is measured from the *prescribed* surface, so no amount of
    looking at the result recovers it - and it is the number that decides whether a
    rigid-lid answer is usable at all.
    """
    import json

    record = {
        "mode": cfg.openfoam.mode,
        "rigid_lid": bool(of_mesh.rigid_lid),
        "velocity_cap": velocity_cap,
        "n_cells": int(of_mesh.n_cells),
    }
    if of_mesh.lid_step_p99 is not None:
        record["lid_step_median"] = float(of_mesh.lid_step_median)
        record["lid_step_p99"] = float(of_mesh.lid_step_p99)
    path = case_dir / BUILD_RECORD
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return path


def water_speed(of_mesh, cfg: Config, state=None) -> float:
    """The reach's own water speed [m/s]: the 95th percentile of the 2D result."""
    if state is not None:
        return max(float(state.velocity_scale()), 1e-3)
    if getattr(of_mesh, "column_uv", None) is not None:
        speed = np.hypot(of_mesh.column_uv[:, 0], of_mesh.column_uv[:, 1])
        if speed.size:
            return max(float(np.percentile(speed, 95)), 1e-3)
    return float(cfg.hydrodynamics.initial_velocity_guess)


def limiting_velocity(of_mesh, cfg: Config, velocity_cap: float, state=None) -> float:
    """The velocity that sets the Courant number of the flow.

    Under a rigid lid that is the water's own speed. In the two-phase case it is the
    air next to the water, which moves a little faster than the water does
    (:data:`AIR_SPEED_FACTOR`) and never faster than the ``limitVelocity`` cap.
    **It is not the cap**, although this function said so until October 2026: the
    runs it was calibrated on had air at the cap because the outflow drove it there
    (see :func:`axqua.solvers.openfoam.fields.outlet_pressure`), and the estimate
    built on them was eight times too small once that was corrected.
    """
    water = water_speed(of_mesh, cfg, state)
    if getattr(of_mesh, "rigid_lid", False):
        return water
    return min(AIR_SPEED_FACTOR * water, float(velocity_cap))


def flow_courant(cfg: Config, courant: float | None = None) -> float:
    """The Courant limit of the flow in the production stage (``maxCo``)."""
    of = cfg.openfoam
    courant = courant if courant is not None else of.max_courant
    wanted = getattr(of, "max_flow_courant", None)
    if wanted and of.mode != "rigid-lid":
        return max(float(wanted), courant)
    return courant


def estimate_time_step(of_mesh, cfg: Config, velocity_cap: float, *,
                       courant: float | None = None, state=None) -> float:
    """Time step [s] the Courant target will settle on.

    **Two phases.** The limiting **length** is the *thinnest* layer in the mesh, not
    the bed layer and not ``cell_size``: the bed layer is pinned thick enough to clear
    the roughness, so the layers above it are thinner wherever a column is shallow,
    and what crosses a layer is the vertical motion at the waterline. Two limits
    apply, the Courant number of the flow, which the air next to the water reaches
    first, and that of the water surface; the smaller step is the estimate. Within
    12 % of the four measured steps the factors come from.

    **Rigid lid.** No air, and taking the two-phase model across gave an estimate
    nineteen times too pessimistic (0.019 s against a measured 0.369 s). There the
    water moves horizontally across the full plan spacing, and only its *vertical*
    component - which a terrain-following mesh gets from the bed slope, w ~ u.S -
    crosses a thin layer. So the two directions are summed on their own terms
    instead of the worst one being applied to both.
    """
    courant = courant if courant is not None else cfg.openfoam.max_courant
    from axqua.solvers.openfoam.mesh import plan_spacing

    dx = plan_spacing(cfg)
    speed = limiting_velocity(of_mesh, cfg, velocity_cap, state)
    if getattr(of_mesh, "rigid_lid", False):
        slope = terrain_slope(of_mesh)
        rate = speed / dx + speed * slope / max(of_mesh.min_layer_height, 1e-3)
        return courant / max(rate, 1e-6)
    length = min(of_mesh.min_layer_height, dx)
    air = flow_courant(cfg, courant) * length / max(speed, 1e-3)
    surface = courant * length / (SURFACE_SPEED_FACTOR * water_speed(of_mesh, cfg, state))
    return min(air, surface)


def terrain_slope(of_mesh) -> float:
    """Median bed slope [-] across one plan cell, over the meshed columns.

    On a terrain-following mesh this is what turns horizontal flow into vertical
    flux: water running along a bed that falls by ``S`` per cell crosses the layers
    at roughly ``u.S``. It is read off the structured lattice rather than assumed,
    because it varies by an order of magnitude between a smooth sand reach and the
    bar-and-pool bed here (isar-2025: ~0.08).
    """
    grid = of_mesh.grid
    bed = np.full(grid.col_id.shape, np.nan)
    inside = grid.col_id >= 0
    bed[inside] = of_mesh.column_bed[grid.col_id[inside]]
    dj, di = np.gradient(bed, grid.dx)
    slope = np.hypot(di, dj)[inside]
    slope = slope[np.isfinite(slope)]
    return float(np.median(slope)) if slope.size else 0.0


def reach_flush_time(of_mesh, state) -> float:
    """Rough time [s] for water to traverse the modelled reach once.

    The yardstick for "how long must this run be": a free-surface run cannot be
    called steady before the domain has been flushed at least once, and comparing
    that against the configured ``end_time`` is the difference between a run that
    means something and one that merely finished.
    """
    if state is None:
        return 0.0
    xy = of_mesh.grid.cell_xy
    span = float(np.hypot(*(xy.max(axis=0) - xy.min(axis=0))))
    return span / max(state.velocity_scale(), 1e-3)


def cost_lines(of_mesh, state, cfg: Config, velocity_cap: float) -> list[str]:
    """What this run will cost, stated before anyone commits to it.

    A 3D VOF model of a whole river reach is expensive in a way a 2D model is not,
    and the expense is not obvious from the cell count alone: it is the cell count
    **times** the number of time steps, and the time step is set by the thinnest
    cell. Spelling both out here is the difference between choosing a resolution and
    discovering one.
    """
    of = cfg.openfoam
    dt = estimate_time_step(of_mesh, cfg, velocity_cap, state=state)
    steps = of.end_time / dt if dt > 0 else float("inf")
    flush = reach_flush_time(of_mesh, state)
    speed = limiting_velocity(of_mesh, cfg, velocity_cap, state)
    courants = f"Courant {of.max_courant:g}"
    from axqua.solvers.openfoam.mesh import plan_spacing

    if getattr(of_mesh, "rigid_lid", False):
        basis = (f"({speed:.2f} m/s - the water's own speed, there is no air phase - "
                 f"across {plan_spacing(cfg):.2f} m cells, dropping through the "
                 f"thinnest {of_mesh.min_layer_height:.3f} m layer on a "
                 f"{terrain_slope(of_mesh):.1%} bed slope)")
    else:
        flow = flow_courant(cfg)
        air = flow * min(of_mesh.min_layer_height, plan_spacing(cfg)) / max(speed, 1e-3)
        if flow > of.max_courant:
            courants = (f"Courant {of.max_courant:g} at the water surface and "
                        f"{flow:g} in the air")
        what = ("the air next to the water" if air <= dt * (1 + 1e-9)
                else "the water surface")
        basis = (f"(set by {what} in the thinnest {of_mesh.min_layer_height:.3f} m "
                 f"layer; the water moves at {water_speed(of_mesh, cfg, state):.2f} "
                 "m/s)")
    out = [f"  time step   : ~{dt:.2e} s at {courants} {basis}",
           f"  run length  : {of.end_time:g} s needs ~{steps:,.0f} time steps "
           f"on {of_mesh.n_cells:,} cells"]
    if flush:
        out.append(f"  reach flush : ~{flush:,.0f} s for water to cross the domain "
                   "once at the reach's own speed")
        if of.end_time < flush:
            out.append(f"  ! openfoam.end_time ({of.end_time:g} s) is shorter than one "
                       f"flush ({flush:,.0f} s), so the run cannot reach a steady "
                       "state. Either accept it as a transient study of the first "
                       f"{of.end_time:g} s, or shorten the modelled reach - raising "
                       "end_time to a full flush multiplies the step count above by "
                       f"{flush / max(of.end_time, 1e-9):.0f}.")
    return out


def estimate_cells(cfg: Config, *, state: State2D | None = None) -> int:
    """Cell count a build would produce, without building it.

    Cheap enough to call before committing to a resolution: it lays the plan lattice
    and multiplies by the layer count, skipping the DEM sampling and the extrusion.
    """
    polygon, _ = ofmesh._domain_polygon(
        cfg, state, domain=cfg.openfoam.domain, wet_margin=cfg.openfoam.wet_margin,
        wet_depth=cfg.hydrodynamics.wet_depth)
    angle = ofmesh.flow_angle(cfg) if cfg.openfoam.align_to_flow else 0.0
    grid = ofmesh.build_plan_grid(polygon, cfg.openfoam.cell_size, angle=angle,
                                  max_columns=cfg.openfoam.max_plan_columns)
    return int(grid.n_columns * cfg.openfoam.n_layers)


def summarise(art: OpenFoamArtifacts) -> list[str]:
    """Printable build summary: what was made, and what to run next."""
    out = list(art.lines())
    if art.report is not None:
        out.append("")
        out += art.report.lines()
    total = art.report.n_cells if art.report is not None else 0
    if art.mesh is not None and total:
        # Under a rigid lid alpha is set to 1 outright (fields.write_fields), so
        # re-deriving it from the seed here would report a water fraction the case
        # does not have - and reporting 96.8% for a domain that is water by
        # construction is exactly the kind of contradiction this line exists to catch.
        wet_depth = art.cfg.hydrodynamics.wet_depth if art.cfg is not None else 0.01
        water = (1.0 if getattr(art.mesh, "rigid_lid", False)
                 else float(np.mean(fields.initial_alpha(
                     art.mesh, wet_depth=wet_depth) > 0.5)))
        out.append(f"  water at t=0 : {100 * water:.1f}% of cells "
                   f"({total:,} total)")
    if art.mesh is not None and art.cfg is not None:
        out.append("")
        out.append("cost:")
        out += cost_lines(art.mesh, art.state, art.cfg, art.velocity_cap)
    return out
