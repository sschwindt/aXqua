"""The ``0/`` directory: initial and boundary conditions for the interFoam case.

Boundary conditions
-------------------
Every type here was checked against the OpenFOAM 9 source and the
``interFoam/RAS/{waterChannel,weirOverflow}`` tutorials, because the difference
between a river case that runs and one that oscillates is almost entirely in this
table.

========== ============================ ======================================= =====================
patch      ``alpha.water``              ``U``                                   ``p_rgh``
========== ============================ ======================================= =====================
inlet      ``variableHeightFlowRate``   ``flowRateInletVelocity`` (mass flow)   ``fixedFluxPressure``
outlet     ``inletOutlet`` at the stage ``pressureInletOutletVelocity``         ``prghPressure``
bed, banks ``zeroGradient``             ``noSlip``                              ``fixedFluxPressure``
atmosphere ``inletOutlet`` (0)          ``pressureInletOutletVelocity``         ``totalPressure`` 0
========== ============================ ======================================= =====================

Three of those deserve their reasoning recorded.

**The inlet does not prescribe a water level.** ``variableHeightFlowRate`` lets
``alpha`` at the inlet float between 0 and 1, so the water on the patch stands as
high as the water behind it. The inlet stage is an outcome, not an input - which is
what you want, because forcing both Q and the depth at an inlet over-determines it
and shows up as a standing wave a few cells downstream.

**The inlet prescribes the mass that enters, not the volume.** One velocity for the
whole patch, the mass flow of the water divided by the mass per length that stands on
the patch (``flowRateInletVelocity`` with ``massFlowRate`` and the density field).
Whatever part of the patch is wet then carries the discharge, and the air above it
enters at the speed of the water, which nothing notices. The condition written for
this purpose, ``variableHeightFlowRateInletVelocity``, sets the velocity in
proportion to ``alpha`` and scales it so that water and air together make up the
discharge: the water that enters is ``Q <alpha^2> / <alpha>``, short by whatever the
surface is smeared over on the patch. Measured on the Isar example with the same
state: 1.434 of 1.6 m3/s and 0.743 of 0.8 m3/s with that condition, 1.596 and 0.798
with the mass flow (on munich-vsf the older runs delivered 82 to 84 %). What is left,
0.2 to 0.3 %, is the share of the air in the mass on the patch.

**The outlet holds a stage.** The tutorials use a free outfall (``zeroGradient``
everywhere), which lets the model choose its own tailwater. This case has a
calibrated one - the same rating curve the TELEMAC run used - so the outlet gets
``prghPressure`` with a hydrostatic ``p`` profile below the target level and an
``inletOutlet`` ``alpha`` profile that is 1 below it and 0 above. That is the direct
3D analogue of TELEMAC's ``PRESCRIBED ELEVATIONS``, so 2D and 3D are driven
identically and their results are comparable. ``boundaries.outflow_condition: free``
still selects the free outfall.

**The air on the outlet is at rest, like the air under the open top.** The solver
carries ``p_rgh = p + rho g (z - hRef)``, and ``totalPressure 0`` on the open top says
``p_rgh = 0`` there, which is air at rest at any elevation. The outlet prescribes
``p``, so above the stage it has to prescribe the pressure of that same air,
``-rho_air g (z - hRef)``, and the water below the stage hangs on the air pressure at
the stage. A profile that is simply zero above the stage disagrees with the open top
by ``rho_air g (z - hRef)``: 8 kPa on a reach at 816 m a.s.l. with ``hRef`` left at
zero, which is a wind of the speed the velocity limit allows between the outlet and
the top, and it is that wind, in a handful of cells, that set the time step of the
whole run (isar: 0.0013 s; see :func:`reference_level`). ``constant/hRef`` is written
with the lowest outlet stage, so that ``p_rgh`` is the piezometric head above the
tailwater and stays small.

Initial conditions
------------------
``alpha.water`` and ``U`` come from the 2D hotstart (see
:mod:`axqua.solvers.openfoam.hotstart`) and are the only non-uniform internal fields;
``p_rgh`` starts at 0 (as in every tutorial - the first pressure solve resolves it)
and the turbulence fields start uniform at scales derived from the reach's own depth
and velocity.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from axqua.solvers.openfoam.polymesh import foam_footer, foam_header

log = logging.getLogger("axqua")

GRAVITY = 9.81
C_MU = 0.09
TURBULENT_INTENSITY = 0.1     # 10%: open-channel flow over a rough bed
MIXING_LENGTH_FACTOR = 0.07   # L = 0.07 * depth, the standard hydraulic-diameter rule


# --------------------------------------------------------------------------- #
# rendering helpers
# --------------------------------------------------------------------------- #


def scalar_list(values: np.ndarray) -> str:
    """``nonuniform List<scalar>`` body."""
    values = np.asarray(values, dtype=float)
    buf = io.StringIO()
    np.savetxt(buf, values, fmt="%.6g")
    return f"nonuniform List<scalar>\n{values.size}\n(\n{buf.getvalue()})"


def vector_list(values: np.ndarray) -> str:
    """``nonuniform List<vector>`` body."""
    values = np.asarray(values, dtype=float).reshape(-1, 3)
    buf = io.StringIO()
    np.savetxt(buf, values, fmt="(%.6g %.6g %.6g)")
    return f"nonuniform List<vector>\n{values.shape[0]}\n(\n{buf.getvalue()})"


def render_field(cls: str, obj: str, dimensions: str, internal: str,
                 boundary: dict[str, dict[str, str]], *, time_dir: str = "0") -> str:
    """Assemble one OpenFOAM field file."""
    out = [foam_header(cls, obj, location=time_dir)]
    out.append(f"dimensions      {dimensions};\n\n")
    out.append(f"internalField   {internal};\n\nboundaryField\n{{\n")
    for name, entries in boundary.items():
        out.append(f"    {name}\n    {{\n")
        for key, value in entries.items():
            out.append(f"        {key:<15} {value};\n")
        out.append("    }\n")
    out.append("}\n")
    out.append(foam_footer())
    return "".join(out)


# --------------------------------------------------------------------------- #
# the physical initial state
# --------------------------------------------------------------------------- #


def initial_alpha(of_mesh, *, wet_depth: float = 0.01) -> np.ndarray:
    """Per-cell water fraction from the hotstart free surface.

    The submerged fraction of each cell, so the interface is sharp to within one
    cell wherever the 2D surface crosses a cell. With no hotstart the domain starts
    dry, which is legitimate but means paying for the whole filling transient in the
    most expensive model of the chain.

    **A column that is dry in the 2D result starts dry.** The free surface of a dry
    2D node is its own bed, and the bed of the column comes from the terrain model,
    which resolves what the 2D mesh does not. Wherever the terrain at the column lies
    below the bed of the nearest dry node, "below the 2D free surface" is a layer of
    water on dry ground. On the Isar example at 1 m cells that was 583 m3, a sixth of
    the water of the reach, on 55 % of the dry columns (median 0.03 m, 0.49 m at the
    99th percentile), and it ran down the banks from the first time step at the speed
    the velocity limit allows.
    """
    if of_mesh.column_wse is None:
        return np.zeros(of_mesh.n_cells)
    z_lo, z_hi = of_mesh.cell_bounds
    surface = np.asarray(of_mesh.column_wse, dtype=float)
    surface = np.where(np.isfinite(surface), surface, -np.inf)
    depth = getattr(of_mesh, "column_depth", None)
    if depth is not None:
        surface = np.where(np.asarray(depth, dtype=float) > wet_depth, surface, -np.inf)
    wse = surface[of_mesh.cell_column]
    thickness = np.maximum(z_hi - z_lo, 1e-9)
    return np.clip((wse - z_lo) / thickness, 0.0, 1.0)


def initial_velocity(of_mesh, alpha: np.ndarray, state=None) -> np.ndarray:
    """Per-cell velocity: the seed's velocity in water, zero in air.

    From a **2D** seed this is uniform over the depth, deliberately, rather than an
    assumed log profile: the log profile's normalization is only meaningful where the
    wall function is admissible, and on a gravel bed that is often nowhere (see
    :mod:`axqua.solvers.openfoam.quality`). The vertical structure is what the 3D
    run is *for*, so it is left to develop during the spin-up rather than invented
    here.

    From a **3D** seed (``pre_run.dimension: 3d``) the profile is not assumed - it was
    computed, by a solver, on this reach's own bathymetry - so each cell is sampled at
    its own elevation instead. That is the one case where prescribing vertical
    structure is better than starting flat.

    Scaling by ``alpha`` keeps an interface cell from carrying a full-strength
    velocity into the air.
    """
    out = np.zeros((of_mesh.n_cells, 3))
    if state is not None and getattr(state, "has_profile", False):
        xy = of_mesh.grid.cell_xy[of_mesh.cell_column]
        uv = state.sample_profile(xy, of_mesh.cell_centres[:, 2])
        out[:, :2] = np.where(np.isfinite(uv), uv, 0.0) * alpha[:, None]
        return out
    if of_mesh.column_uv is None:
        return out
    uv = np.asarray(of_mesh.column_uv, dtype=float)[of_mesh.cell_column]
    out[:, :2] = np.where(np.isfinite(uv), uv, 0.0) * alpha[:, None]
    return out


def turbulence_scales(state, cfg) -> tuple[float, float, float]:
    """``(k, epsilon, omega)`` seeds from the reach's own depth and velocity."""
    velocity = state.velocity_scale() if state is not None else \
        cfg.hydrodynamics.initial_velocity_guess
    depth = state.depth_scale() if state is not None else 1.0
    k = max(1.5 * (TURBULENT_INTENSITY * velocity) ** 2, 1e-6)
    length = max(MIXING_LENGTH_FACTOR * depth, 1e-3)
    epsilon = C_MU ** 0.75 * k ** 1.5 / length
    omega = k ** 0.5 / (C_MU ** 0.25 * length)
    return k, epsilon, omega


# --------------------------------------------------------------------------- #
# the field files
# --------------------------------------------------------------------------- #


def _wall_entries(of_mesh, cfg, patch: str, *, uniform: bool = False) -> dict[str, str]:
    r"""``nut`` entry for a wall patch, with per-face ``Ks`` on the bed.

    *uniform* forces a single ``Ks uniform <value>`` even where the mesh carries a
    per-face roughness. That is what a **calibration template** needs: HydroBayesCal
    perturbs ``ks`` by rewriting the one line matching ``^\s*Ks\s+`` with
    ``Ks uniform <v>;``, and it has no skip logic for a multi-line list - so against
    a ``nonuniform List<scalar>`` the header is replaced and the list body survives
    as orphaned tokens, giving an unparseable ``0/nut``. A calibrated ``ks`` is one
    global number anyway, so there is nothing to lose here and a corrupt dictionary
    (reported far away, as an OpenFOAM parse error) to avoid.
    """
    of = cfg.openfoam
    if patch == "bed" and of_mesh.bed_ks is not None and not uniform:
        ks = np.nan_to_num(np.asarray(of_mesh.bed_ks, dtype=float),
                           nan=of.friction_ks)
        return {"type": "nutkRoughWallFunction",
                "Ks": scalar_list(ks),
                "Cs": f"uniform {of.roughness_constant:g}",
                "value": "uniform 0"}
    return {"type": "nutkRoughWallFunction",
            "Ks": f"uniform {of.friction_ks:g}",
            "Cs": f"uniform {of.roughness_constant:g}",
            "value": "uniform 0"}


def _patch_lid_level(of_mesh, patch: str) -> float:
    """Mean lid elevation over one patch [m a.s.l.], the rigid-lid fallback stage.

    Used only when no ``outlet_stage`` was configured. Under a rigid lid the lid *is*
    the free surface, so its own elevation on the outlet patch is the level the
    pressure datum has to be referenced to - the geometry already knows the answer.
    """
    ids = of_mesh.polymesh.patch_face_ids(patch)
    quads = of_mesh.polymesh.faces[ids]
    return float(of_mesh.polymesh.points[quads][:, :, 2].max(axis=1).mean())


def outlet_pressure(z: np.ndarray, stage: float, *, water_density: float,
                    air_density: float, reference: float) -> np.ndarray:
    """Static pressure [Pa] on outlet faces at the elevations *z* for a tailwater at *stage*.

    Air at rest above the stage, and still water below it that hangs on the pressure
    of that air at the stage. *reference* is the level ``hRef`` the solver counts the
    elevation from. ``prghPressure`` turns the profile into
    ``p_rgh = p + rho g (z - hRef)`` with the density that is at the face, which is
    zero wherever there is air: the value ``totalPressure 0`` gives the open top. With
    a profile of zero above the stage the two boundaries disagree by
    ``rho_air g (z - hRef)`` and air is driven from one to the other.
    """
    z = np.asarray(z, dtype=float)
    air = -air_density * GRAVITY * (z - reference)
    at_stage = -air_density * GRAVITY * (stage - reference)
    water = at_stage + water_density * GRAVITY * (stage - z)
    return np.where(z <= stage, water, air)


def _outlet_profiles(of_mesh, patch: str, stage: float, rho: float, *,
                     air_density: float = 0.0,
                     reference: float | None = None) -> tuple[str, str]:
    """``(alpha inletValue, p profile)`` on an outlet patch holding *stage*.

    ``alpha`` is 1 on faces whose center lies below the target water level and 0
    above; ``p`` is the still-water hydrostatic pressure of a column standing at that
    level, continued upwards as air at rest (:func:`outlet_pressure`). Together they
    are what ``prghPressure`` needs to hold a tailwater.
    """
    ids = of_mesh.polymesh.patch_face_ids(patch)
    z = of_mesh.polymesh.face_centres(ids)[:, 2]
    alpha = (z <= stage).astype(float)
    pressure = outlet_pressure(z, stage, water_density=rho, air_density=air_density,
                               reference=stage if reference is None else reference)
    return scalar_list(alpha), scalar_list(pressure)


#: ``constant/hRef``: the level the two-phase solver counts the elevation from.
REFERENCE_FILE = "hRef"


def reference_level(of_mesh, stages: Mapping[str, float] | None) -> float:
    """The level ``hRef`` [m a.s.l.] of the two-phase case.

    The solver works with ``p_rgh = p + rho g (z - hRef)``. Where the water surface
    stands at ``eta``, ``p_rgh`` jumps across it by ``(rho_water - rho_air) g
    (eta - hRef)``, and the pressure solution is only as exact as the tolerance of
    its solver times that jump. Left at zero on a mesh in meters above sea level the
    jump is 8 MPa where the flow is driven by a few pascals. So the level is put where
    the water is: at the lowest stage the outlets hold, which also makes ``p_rgh / (rho
    g)`` the piezometric head above the tailwater. A free outfall prescribes no
    pressure, and any level of the reach serves: the lowest point of the lid.
    """
    if stages:
        return float(min(stages.values()))
    return float(np.nanmin(of_mesh.lid))


def write_reference_level(case_dir: str | Path, level: float | None) -> Path | None:
    """Write ``constant/hRef``, or remove it where *level* is ``None``.

    A case that is rebuilt single-phase in the folder of a two-phase one must not
    keep the file: the solver reads it whenever it is there, and the pressure the
    rigid lid prescribes at its outlet counts from zero.
    """
    path = Path(case_dir) / "constant" / REFERENCE_FILE
    if level is None:
        path.unlink(missing_ok=True)
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    buf.write(foam_header("uniformDimensionedScalarField", REFERENCE_FILE,
                          location="constant"))
    buf.write("dimensions      [0 1 0 0 0 0 0];\n\n")
    buf.write(f"value           {level:.6f};\n")
    buf.write(foam_footer())
    return _write(path, buf.getvalue())


def outlet_stages(outflow_stage, patches) -> dict[str, float] | None:
    """Per-patch tailwater level [m a.s.l.], broadcasting a scalar.

    A single number is the right answer for a whole-reach case, which has one
    outfall. A **sub-model crop does not**: cutting a reach mid-stream leaves
    several open faces at different stations, and the free surface across them
    spans whatever the parent's water-surface slope puts there - 1.18 m on the
    KB15 40 m crop. Prescribing one level on all of them would impose a
    tailwater the parent model never had on at least one face.

    A mapping must name **every** outlet patch. Silently defaulting the ones it
    omits would reintroduce exactly the bug this exists to prevent, in the case
    where it is hardest to notice.
    """
    patches = list(patches)
    if outflow_stage is None:
        return None
    if isinstance(outflow_stage, Mapping):
        missing = [p for p in patches if p not in outflow_stage]
        if missing:
            raise ValueError(
                f"openfoam.outlet_stage names {sorted(outflow_stage)} but the mesh "
                f"has outlet patch(es) {missing} with no level. Give every outlet "
                "its own tailwater, or use a single number if they really do share "
                "one - a crop's open faces sit at different stations and generally "
                "do not.")
        return {p: float(outflow_stage[p]) for p in patches}
    return {p: float(outflow_stage) for p in patches}


def rating_entry(table) -> str:
    """A rating curve as the ``table`` of a Function1: ``table ((Q h) ...)``."""
    return "table (" + " ".join(f"({q:.6g} {h:.6f})" for q, h in table) + ")"


def write_fields(of_mesh, cfg, case_dir: str | Path, *, state=None,
                 outflow_stage: "float | Mapping[str, float] | None" = None,
                 discharges: dict[str, float] | None = None,
                 uniform_bed_ks: bool = False,
                 rating: "list[tuple[float, float]] | None" = None) -> list[Path]:
    """Write ``0/{alpha.water, U, p_rgh, nut, k, omega|epsilon}``.

    *outflow_stage* is the water level [m a.s.l.] the outlet patches hold; ``None``
    selects the free-outfall boundary instead (``outflow_condition: free``).
    *discharges* maps each inlet patch to its own Q [m3/s].
    *uniform_bed_ks* writes one global bed roughness instead of the per-face list
    (see :func:`_wall_entries`); set it for a calibration template.
    *rating* is a stage-discharge relation ``[(Q, h), ...]``: with it, every outlet of
    a two-phase case holds the level that the relation gives at the discharge that
    leaves through it, with the boundary condition ``waterLevel_alpha_prgh`` of the
    BAW (``openfoam.outflow_boundary: rating-curve``). *outflow_stage* is then the
    level the run starts from.
    """
    of = cfg.openfoam
    rigid = getattr(of_mesh, "rigid_lid", False)
    # Resolve to one level PER OUTLET PATCH up front, so the three boundary
    # conditions below cannot disagree about what the tailwater is.
    stages = outlet_stages(outflow_stage, of_mesh.outlet_patches)
    # The level the two-phase solver counts the elevation from. Under a rigid lid
    # there is none: the pressure its outlet prescribes counts from zero.
    reference = None if rigid else reference_level(of_mesh, stages)
    case_dir = Path(case_dir)
    zero = case_dir / "0"
    zero.mkdir(parents=True, exist_ok=True)
    discharges = discharges or {}
    top = of_mesh.top_patch if hasattr(of_mesh, "top_patch") else "atmosphere"
    # Every wall-type patch the mesher actually made, not a hardcoded pair: the
    # lateral faces are split into `banks` (the domain edge) and `structures` (the
    # sides of blanked solids), and a mesh with no solids has only the first. They
    # take the same conditions - a wall is a wall to the solver - so the only thing
    # that must not happen is one of them being left without any. Under a rigid lid
    # the top patch is a wall too, and it is excluded: it takes slip, not noSlip.
    walls = list(getattr(of_mesh, "wall_patches", None) or ["bed", "banks"])
    written: list[Path] = []

    # Under a rigid lid the domain is water only, so alpha is identically 1: no
    # interface to compress, no air to accelerate, and interFoam degenerates to a
    # single-phase solver while keeping its p_rgh + gravity treatment intact. That is
    # the whole trick - the air phase is removed by construction rather than modeled
    # cheaply, and none of the pressure formulation has to be re-derived.
    alpha = (np.ones(of_mesh.n_cells) if rigid
             else initial_alpha(of_mesh, wet_depth=cfg.wet_depth))
    velocity = initial_velocity(of_mesh, alpha, state)
    k, epsilon, omega = turbulence_scales(state, cfg)

    # ---- alpha.water --------------------------------------------------------
    bc: dict[str, dict[str, str]] = {}
    for patch in of_mesh.inlet_patches:
        # let the inlet stage settle itself; prescribing Q *and* a depth
        # over-determines the boundary
        bc[patch] = {"type": "variableHeightFlowRate", "lowerBound": "0",
                     "upperBound": "1", "value": "uniform 0"}
    for patch in of_mesh.outlet_patches:
        # Under a rigid lid the outlet is fully wet to the lid, so there is no water
        # level to find on the patch and the face-by-face profile is not computed -
        # it would only be discarded below.
        if rigid or stages is None:
            bc[patch] = {"type": "zeroGradient"}
        elif rating:
            # BAW: zeroGradient where water leaves, the level of the curve where it
            # enters; the profile of the starting level is only the first value
            inlet_value, _ = _outlet_profiles(of_mesh, patch, stages[patch],
                                              of.water_density)
            bc[patch] = {"type": "waterLevel_alpha_prgh", "mode": "ratingCurveTable",
                         "data": rating_entry(rating), "value": inlet_value}
        else:
            inlet_value, _ = _outlet_profiles(of_mesh, patch, stages[patch],
                                              of.water_density)
            bc[patch] = {"type": "inletOutlet", "inletValue": inlet_value,
                         "value": inlet_value}
    for patch in walls:
        bc[patch] = {"type": "zeroGradient"}
    bc[top] = ({"type": "zeroGradient"} if rigid else
               {"type": "inletOutlet", "inletValue": "uniform 0",
                "value": "uniform 0"})
    if rigid:
        # the inlet is fully wet by construction, so the variable-height machinery
        # has nothing to vary
        for patch in of_mesh.inlet_patches:
            bc[patch] = {"type": "fixedValue", "value": "uniform 1"}
    written.append(_write(zero / "alpha.water", render_field(
        "volScalarField", "alpha.water", "[0 0 0 0 0 0 0]",
        scalar_list(alpha), bc)))

    # ---- U ------------------------------------------------------------------
    bc = {}
    for patch in of_mesh.inlet_patches:
        q = discharges.get(patch)
        if q is None:
            raise ValueError(
                f"no discharge for inlet patch {patch!r}. Give each inflow line a "
                "'Target flow' value in boundaries.liquid_boundaries, or set "
                "boundaries.prescribed_flowrate for a single inflow.")
        bc[patch] = ({"type": "flowRateInletVelocity",
                      "volumetricFlowRate": f"constant {q:g}",
                      "value": "uniform (0 0 0)"} if rigid else
                     # the mass of the water, over the mass that stands on the
                     # patch: see the module text. rhoInlet serves the one
                     # evaluation before the solver has made its density field.
                     {"type": "flowRateInletVelocity",
                      "massFlowRate": f"constant {q * of.water_density:.8g}",
                      "rho": "rho", "rhoInlet": f"{of.water_density:g}",
                      "value": "uniform (0 0 0)"})
    for patch in of_mesh.outlet_patches:
        bc[patch] = ({"type": "zeroGradient"} if stages is None else
                     {"type": "pressureInletOutletVelocity",
                      "value": "uniform (0 0 0)"})
    for patch in walls:
        bc[patch] = {"type": "noSlip"}
    # slip, not noSlip: an air-water interface carries negligible shear, so the lid
    # must not put a boundary layer on the top of the water column
    bc[top] = ({"type": "slip"} if rigid else
               {"type": "pressureInletOutletVelocity", "value": "uniform (0 0 0)"})
    written.append(_write(zero / "U", render_field(
        "volVectorField", "U", "[0 1 -1 0 0 0 0]", vector_list(velocity), bc)))

    # ---- p_rgh --------------------------------------------------------------
    bc = {}
    for patch in of_mesh.inlet_patches:
        bc[patch] = {"type": "fixedFluxPressure", "value": "uniform 0"}
    for patch in of_mesh.outlet_patches:
        if rigid:
            # The lid IS the free surface, so the outlet needs a LEVEL rather than a
            # profile - but it is not zero, and this used to say it was.
            #
            # OpenFOAM carries p_rgh = p - rho*(g & C), so for a hydrostatic column
            # whose surface stands at z_s the whole patch has p_rgh = rho*g*z_s: a
            # constant, but a constant equal to the free-surface elevation head, not
            # to nothing. Writing 0 puts the zero-pressure level at the mesh DATUM
            # instead of at the water surface, which on this case (z_s = 0.715 m in
            # local CAD meters) offset every reported pressure by -7.0 kPa. Velocities
            # are unaffected - a uniform shift of p has no gradient - but anything
            # read FROM the pressure is wrong, and correct_lid.py is exactly that: it
            # reported a 0.87 m lid error that was purely this datum.
            level = (stages[patch] if stages is not None
                     else _patch_lid_level(of_mesh, patch))
            bc[patch] = {"type": "fixedValue",
                         "value": f"uniform {of.water_density * GRAVITY * level:.6g}"}
        elif stages is None:
            bc[patch] = {"type": "zeroGradient"}
        elif rating:
            # the hydrostatic pressure below the level that the curve gives for the
            # discharge through the patch, air at rest above it (Thorenz 2024)
            bc[patch] = {"type": "waterLevel_alpha_prgh", "mode": "ratingCurveTable",
                         "data": rating_entry(rating), "value": "uniform 0"}
        else:
            _, profile = _outlet_profiles(of_mesh, patch, stages[patch],
                                          of.water_density,
                                          air_density=of.air_density,
                                          reference=reference)
            bc[patch] = {"type": "prghPressure", "p": profile, "value": "uniform 0"}
    for patch in walls:
        bc[patch] = {"type": "fixedFluxPressure", "value": "uniform 0"}
    bc[top] = ({"type": "fixedFluxPressure", "value": "uniform 0"} if rigid else
               {"type": "totalPressure", "p0": "uniform 0"})
    written.append(_write(zero / "p_rgh", render_field(
        "volScalarField", "p_rgh", "[1 -1 -2 0 0 0 0]", "uniform 0", bc)))
    reference_file = write_reference_level(case_dir, reference)
    if reference_file is not None:
        written.append(reference_file)

    if of.turbulence == "laminar":
        return written

    # ---- nut ----------------------------------------------------------------
    bc = {p: {"type": "calculated", "value": "uniform 0"}
          for p in of_mesh.inlet_patches + of_mesh.outlet_patches + [top]}
    for patch in walls:
        bc[patch] = _wall_entries(of_mesh, cfg, patch, uniform=uniform_bed_ks)
    written.append(_write(zero / "nut", render_field(
        "volScalarField", "nut", "[0 2 -1 0 0 0 0]", "uniform 0", bc)))

    # ---- k ------------------------------------------------------------------
    bc = {}
    for patch in of_mesh.inlet_patches:
        bc[patch] = {"type": "turbulentIntensityKineticEnergyInlet",
                     "intensity": f"{TURBULENT_INTENSITY:g}",
                     "value": f"uniform {k:.6g}"}
    for patch in of_mesh.outlet_patches + [top]:
        bc[patch] = {"type": "inletOutlet", "inletValue": f"uniform {k:.6g}",
                     "value": f"uniform {k:.6g}"}
    for patch in walls:
        bc[patch] = {"type": "kqRWallFunction", "value": f"uniform {k:.6g}"}
    written.append(_write(zero / "k", render_field(
        "volScalarField", "k", "[0 2 -2 0 0 0 0]", f"uniform {k:.6g}", bc)))

    # ---- omega / epsilon ----------------------------------------------------
    if of.turbulence == "kOmegaSST":
        name, value, dims, wall = "omega", omega, "[0 0 -1 0 0 0 0]", "omegaWallFunction"
    else:
        name, value, dims, wall = ("epsilon", epsilon, "[0 2 -3 0 0 0 0]",
                                   "epsilonWallFunction")
    bc = {}
    for patch in of_mesh.inlet_patches + of_mesh.outlet_patches + [top]:
        bc[patch] = {"type": "inletOutlet", "inletValue": f"uniform {value:.6g}",
                     "value": f"uniform {value:.6g}"}
    for patch in walls:
        bc[patch] = {"type": wall, "value": f"uniform {value:.6g}"}
    written.append(_write(zero / name, render_field(
        "volScalarField", name, dims, f"uniform {value:.6g}", bc)))

    wet = float((alpha > 0.5).mean())
    log.info("0/ fields written: %.1f%% of cells start as water, "
             "k=%.4g m2/s2, %s=%.4g", 100 * wet, k, name, value)
    return written


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path
