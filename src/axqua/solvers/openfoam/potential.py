"""Leg C: a free surface with **no air phase at all** (``potentialFreeSurfaceFoam``).

Why it is worth the trouble on this case. The two-phase runs spend four cells in
five on air: munich-vsf leg A is 582,924 cells of which 22% hold water, and the
time step is set by the air's velocity cap, not by the water. lww-134 measured the
cost of making that case *correct* - doubling the freeboard so the lid stops
clipping the surface, and adding the layers that go with it - at **3.3x the
per-step cost for 1.5x the cells**. Resolving the air better is the expensive
direction. This solver deletes it.

HOW IT WORKS. The domain is water only, as under aXqua's rigid lid, and the mesh
top is a real boundary. The free surface is not meshed: it is carried as a
displacement field ``zeta`` on that boundary, entering the pressure equation
through ``waveSurfacePressure`` as ``p_gh = -(g & zeta)``. So the surface moves
while the mesh does not - which is the whole difference from ``mode: rigid-lid``,
where the surface is frozen at whatever the 2D seed said.

THE TRAP, AND WHY ``0/zeta`` IS WRITTEN. ``p_gh`` is an ABSOLUTE gauge:
``createFields`` sets ``p_gh = p - (g & mesh.C())``, so hydrostatic equilibrium
with the surface at ``z_s`` needs ``p_gh = |g| z_s`` on the top. The boundary
condition instead imposes ``p_gh = |g| zeta_z``, and ``zeta`` defaults to **zero**
- which asserts the undisturbed surface lies at ``z = 0``. On a wave tank or a
ship hull that is true by construction and nobody notices. On this fish pass the
surface falls **1.4 m** over 25 m, so the default datum would impose a pressure
error of ``|g| * 1.4 = 13.7 m2/s2`` along the pass and drive a flow that is pure
gauge.

``zeta`` is ``READ_IF_PRESENT``, so the datum is fixable without touching
OpenFOAM: seed its boundary values on the top patch with the local lid elevation.
``zeta`` then means "where the surface actually is" rather than "how far it has
moved from z=0", the linearisation is about the 2D surface, and the quantity that
has to stay small is the 3D *correction* to it rather than the whole fall.
Verified on the v2406 oscillatingBox tutorial, which ships no ``0/zeta``: seeding
one reports ``min/max zetap = 0.5, 0.5`` on the first step and evolves from there,
so the value is read and used rather than reset.

WHAT IS STILL AN APPROXIMATION, stated rather than buried. The surface condition
is linearised: the pressure is applied on the mesh top, not on the displaced
surface, so it is only right while ``|zeta - z_lid|`` is small against the depth.
A plunging jet, an overturning wave and air entrainment are all outside it, and a
vertical-slot pass has jets through every slot. That is a question for the
measurement, not for this docstring - which is why leg C is a leg of a comparison
rather than a replacement.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("axqua")

SOLVER = "potentialFreeSurfaceFoam"

#: Patch name the water-only mesh gives its top (see mesh.LID_PATCH).
TOP_PATCH = "lid"


def is_potential(cfg) -> bool:
    """True when this case is leg C rather than a two-phase or rigid-lid run."""
    return getattr(cfg.openfoam, "solver", "") == SOLVER


def check_applicable(cfg) -> None:
    """Refuse the combinations that would run and be wrong.

    A two-phase mesh has an air region this solver has no equation for, and it
    would fill with water and report a plausible answer. Better to stop here.
    """
    if not is_potential(cfg):
        return
    if cfg.openfoam.mode != "rigid-lid":
        raise ValueError(
            f"openfoam.solver {SOLVER} needs a water-only domain, so "
            f"openfoam.mode must be 'rigid-lid' (got {cfg.openfoam.mode!r}). "
            "The mode names the MESH - water only, top boundary at the free "
            "surface - and the solver decides whether that top is a frozen lid "
            "or carries a moving surface through zeta.")


def lid_elevation_per_column(of_mesh) -> np.ndarray:
    """Lid elevation per plan column, taken from the top faces themselves.

    Not from ``of_mesh.lid`` (which is per plan VERTEX) and not from
    ``column_wse`` (the 2D seed, which the mesher may have clipped): the
    reference has to be the elevation of the very faces ``waveSurfacePressure``
    acts on, or the seeded datum and the boundary disagree by whatever the
    mesher did. The top patch carries one face per column, in column order.
    """
    polymesh = of_mesh.polymesh
    ids = polymesh.patch_face_ids(TOP_PATCH)
    points = np.asarray(polymesh.points, dtype=float)
    quads = np.asarray(polymesh.faces)[ids]
    return points[quads][:, :, 2].mean(axis=1)


def hydrostatic_pressure(of_mesh, g: float = 9.81) -> np.ndarray:
    """Kinematic ``p`` whose ``p_gh`` puts the surface on the mesh top.

    ``createFields`` recomputes ``p_gh = p - (g & C)`` from the ``p`` on disk, so
    ``p`` is what actually sets the initial state - writing ``p_gh`` values alone
    would be silently overwritten and only its boundary TYPES would survive.
    ``p = |g| (z_lid - z)`` gives ``p_gh = |g| z_lid``: hydrostatic, with the
    surface exactly where the 2D seed put it.
    """
    z = np.asarray(of_mesh.cell_centres, dtype=float)[:, 2]
    per_column = lid_elevation_per_column(of_mesh)
    return g * (per_column[of_mesh.cell_column] - z)


# --------------------------------------------------------------------------- #
# the four fields: p, p_gh, U, zeta - and no alpha anywhere
# --------------------------------------------------------------------------- #

def write_potential_fields(of_mesh, cfg, case_dir, *, state=None) -> list[Path]:
    """``0/`` for leg C. Returns the files written."""
    from . import fields as F

    check_applicable(cfg)
    g = abs(float(cfg.openfoam.gravity)) if hasattr(cfg.openfoam, "gravity") else 9.81
    case_dir = Path(case_dir)
    zero = case_dir / "0"
    zero.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    walls = [p for p in of_mesh.wall_patches]
    inlets, outlets = of_mesh.inlet_patches, of_mesh.outlet_patches
    stages = F.outlet_stages(_outflow_stage(cfg), outlets) or {}
    lid_z = lid_elevation_per_column(of_mesh)

    # ---- p: kinematic, and the field that actually sets the initial state ----
    p = hydrostatic_pressure(of_mesh, g)
    bc = {w: {"type": "fixedFluxPressure", "value": "uniform 0"} for w in walls}
    bc[TOP_PATCH] = {"type": "waveSurfacePressure", "value": "uniform 0"}
    for name in inlets:
        bc[name] = {"type": "fixedFluxPressure", "value": "uniform 0"}
    for name in outlets:
        # A prescribed tailwater IS a prescribed p_gh here: p_gh = |g| z_s says
        # "the free surface at this face is at z_s", with no depth to interpolate
        # and no alpha profile to get wrong.
        bc[name] = {"type": "fixedValue",
                    "value": f"uniform {g * stages.get(name, 0.0):.6f}"}
    # p gets PLAIN boundary conditions, and this is not cosmetic. `p` is read but
    # never solved - only `p_gh` is - and `waveSurfacePressure` is not a passive
    # value: its updateCoeffs INTEGRATES zeta from the patch flux. Attaching it to
    # both fields advances the free surface TWICE per time step.
    pbc = {q: {"type": "calculated", "value": "uniform 0"}
           for q in walls + inlets + outlets + [TOP_PATCH]}
    written.append(F._write(zero / "p", F.render_field(
        "volScalarField", "p", "[0 2 -2 0 0 0 0]",
        F.scalar_list(p), pbc)))

    # p_gh carries the same boundary TYPES; its internal values are recomputed
    # from p on startup, so they are a placeholder on purpose.
    written.append(F._write(zero / "p_gh", F.render_field(
        "volScalarField", "p_gh", "[0 2 -2 0 0 0 0]", "uniform 0", bc)))

    # ---- U: the depth-averaged hotstart, as the two-phase path uses ----
    u = F.initial_velocity(of_mesh, np.ones(of_mesh.n_cells), state=state)
    ubc = {w: {"type": "noSlip"} for w in walls}
    # Par-slip, not slip: the surface carries negligible shear but is free to move
    # in its own plane, and the solver's own tutorial uses this.
    ubc[TOP_PATCH] = {"type": "pressureInletOutletParSlipVelocity",
                      "value": "uniform (0 0 0)"}
    for name in inlets:
        # THE WHOLE POINT, for the discharge: with no alpha there is no
        # <alpha^2>/<alpha> to lose. flowRateInletVelocity delivers the volumetric
        # rate exactly, where variableHeightFlowRateInletVelocity delivered 82-96%.
        ubc[name] = {"type": "flowRateInletVelocity",
                     "volumetricFlowRate":
                         f"{of_mesh.inlet_discharge.get(name, 0.0):.6f}",
                     "value": "uniform (0 0 0)"}
    for name in outlets:
        ubc[name] = {"type": "pressureInletOutletVelocity",
                     "value": "uniform (0 0 0)"}
    written.append(F._write(zero / "U", F.render_field(
        "volVectorField", "U", "[0 1 -1 0 0 0 0]",
        F.vector_list(u), ubc)))

    # ---- zeta: the datum, which is the whole reason this case can be built ----
    zbc = {w: {"type": "calculated", "value": "uniform (0 0 0)"} for w in walls}
    for name in inlets + outlets:
        zbc[name] = {"type": "calculated", "value": "uniform (0 0 0)"}
    vec = np.zeros((lid_z.size, 3))
    vec[:, 2] = lid_z
    zbc[TOP_PATCH] = {"type": "calculated",
                      "value": F.vector_list(vec)}
    written.append(F._write(zero / "zeta", F.render_field(
        "volVectorField", "zeta", "[0 1 0 0 0 0 0]", "uniform (0 0 0)",
        zbc)))

    # ---- turbulence: the two-phase path's conditions, not reinvented ----------
    # Seeding the WALL values at 1e-8 instead of the seed scale put omega at
    # effectively zero there, and kOmegaSST divides by it: the first attempt died
    # with SIGFPE inside kOmegaSSTBase::correct(). The wall functions want the same
    # scale as the interior.
    if cfg.openfoam.turbulence != "laminar":
        k, _epsilon, omega = F.turbulence_scales(state, cfg)

        nbc = {q: {"type": "calculated", "value": "uniform 0"}
               for q in inlets + outlets + [TOP_PATCH]}
        for w in walls:
            nbc[w] = F._wall_entries(of_mesh, cfg, w)
        written.append(F._write(zero / "nut", F.render_field(
            "volScalarField", "nut", "[0 2 -1 0 0 0 0]", "uniform 0", nbc)))

        kbc = {}
        for name in inlets:
            kbc[name] = {"type": "turbulentIntensityKineticEnergyInlet",
                         "intensity": f"{F.TURBULENT_INTENSITY:g}",
                         "value": f"uniform {k:.6g}"}
        for name in outlets + [TOP_PATCH]:
            kbc[name] = {"type": "inletOutlet", "inletValue": f"uniform {k:.6g}",
                         "value": f"uniform {k:.6g}"}
        for w in walls:
            kbc[w] = {"type": "kqRWallFunction", "value": f"uniform {k:.6g}"}
        written.append(F._write(zero / "k", F.render_field(
            "volScalarField", "k", "[0 2 -2 0 0 0 0]", f"uniform {k:.6g}", kbc)))

        obc = {}
        for name in inlets + outlets + [TOP_PATCH]:
            obc[name] = {"type": "inletOutlet",
                         "inletValue": f"uniform {omega:.6g}",
                         "value": f"uniform {omega:.6g}"}
        for w in walls:
            obc[w] = {"type": "omegaWallFunction", "value": f"uniform {omega:.6g}"}
        written.append(F._write(zero / "omega", F.render_field(
            "volScalarField", "omega", "[0 0 -1 0 0 0 0]",
            f"uniform {omega:.6g}", obc)))

    log.info("0/ fields written for %s: no alpha, %d cells all water, "
             "zeta seeded to the lid (%.3f .. %.3f m a.s.l.)",
             SOLVER, of_mesh.n_cells, lid_z.min(), lid_z.max())
    return written




def _outflow_stage(cfg):
    return getattr(cfg.openfoam, "outlet_stage", None)


# --------------------------------------------------------------------------- #
# constant/ and system/ for a single-phase run
# --------------------------------------------------------------------------- #

def transport_properties(cfg) -> str:
    """Single phase: one kinematic viscosity, no phase pair, no surface tension."""
    from .dicts import _dict_file

    nu = float(getattr(cfg.openfoam, "water_viscosity", 1.3e-06))
    body = ("transportModel  Newtonian;\n\n"
            "// There is no second phase and no sigma: the interface is a boundary\n"
            "// condition here, not a field.\n"
            f"nu              {nu:g};\n")
    return _dict_file("transportProperties", body, location="constant")


def fv_schemes() -> str:
    """No alpha equation, so no compression scheme and no MULES limiter.

    ``ddtSchemes default Euler`` matters more than it looks: ``waveSurfacePressure``
    reads the ddt scheme registered for ``zeta`` to decide how to integrate the
    surface displacement, and aborts on anything it does not recognise.
    """
    from .dicts import _dict_file

    return _dict_file("fvSchemes", """ddtSchemes
{
    default         Euler;
}

gradSchemes
{
    default         Gauss linear;
}

divSchemes
{
    default         none;
    div(phi,U)      Gauss limitedLinearV 1;
    div(phi,k)      Gauss limitedLinear 1;
    div(phi,omega)  Gauss limitedLinear 1;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}

laplacianSchemes
{
    default         Gauss linear corrected;
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         corrected;
}

// kOmegaSST blends on the distance to the nearest wall, so it needs this even
// though nothing else in a single-phase case does.
wallDist
{
    method          meshWave;
}
""", location="system")


def fv_solution(cfg) -> str:
    """``p_gh`` in place of ``p_rgh``, and nothing at all for alpha."""
    from .dicts import _dict_file

    body = """solvers
{
    p_gh
    {
        solver          GAMG;
        tolerance       1e-8;
        relTol          0.01;
        smoother        DIC;
    }

    p_ghFinal
    {
        $p_gh;
        tolerance       1e-8;
        relTol          0;
    }

    "(U|k|omega)"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-7;
        relTol          0.1;
    }

    "(U|k|omega)Final"
    {
        $U;
        tolerance       1e-7;
        relTol          0;
    }
}

PIMPLE
{
    momentumPredictor yes;
    nOuterCorrectors  2;
    nCorrectors       2;
    // a terrain-following mesh IS non-orthogonal; same value the two-phase path
    // uses, for the same reason
    nNonOrthogonalCorrectors 2;
}
"""
    return _dict_file("fvSolution", body, location="system")
