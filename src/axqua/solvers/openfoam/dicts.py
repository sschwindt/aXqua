"""``constant/`` and ``system/`` dictionaries for the interFoam case.

This module is where the air phase is dealt with. ``interFoam`` in OpenFOAM 9 is
already a PIMPLE solver, so the question is never "which solver" but "which
settings", and the settings that matter are these four:

1. **``MULESCorr yes`` (semi-implicit MULES).** With the default explicit MULES the
   phase-fraction equation carries its own Courant limit and the tutorials run at
   ``maxAlphaCo 0.2``. The semi-implicit limiter removes that constraint, so the
   time step is set by the flow rather than by the interface and ``maxCo`` /
   ``maxAlphaCo`` can go to ~0.9 - a three- to four-fold larger step for the same
   physics.
2. **``limitVelocity`` in ``system/fvOptions``.** Air is a thousand times
   lighter than water, so a small pressure error accelerates it enormously; the
   resulting air jet then sets the Courant number and the time step collapses on a
   phase nobody is interested in. Capping ``|U|`` at a few times the expected water
   velocity stops that dead. Water never reaches the cap, so the water solution is
   untouched.
3. **``momentumPredictor no``.** Standard for gravity-driven free-surface flow: the
   momentum predictor buys nothing when buoyancy dominates and costs an extra solve.
4. **``nNonOrthogonalCorrectors 2``.** A terrain-following mesh *is* non-orthogonal
   wherever the bed slopes; without the correctors the pressure solution leaks that
   as spurious velocity near the bed.

The lid clamping that removes most air cells altogether is in
:mod:`axqua.solvers.openfoam.mesh`, and matters more than all four.

Staging
-------
Two dict sets are written, under ``system/stage1-spinup/`` and
``system/stage2-run/``; :mod:`axqua.solvers.openfoam.runtime` copies one into
``system/`` to select it. Stage 1 settles the interface from the 2D hotstart at a
tight Courant number with diffusive schemes; stage 2 restarts from it and runs the
production settings. The split exists because the first seconds after a hotstart are
the least like the converged flow - the 2D state is depth-averaged, so there is no
vertical structure at all at t = 0 - and paying for robustness there is far cheaper
than paying for it over the whole run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from axqua.solvers.openfoam.mesh import (
    ATMOSPHERE_PATCH, BANKS_PATCH, LID_PATCH,
)
from axqua.solvers.openfoam.polymesh import foam_footer, foam_header

log = logging.getLogger("axqua")

STAGE_DIRS = {"spinup": "stage1-spinup", "run": "stage2-run"}
# system/ files that differ between the two stages and are therefore staged
STAGED_FILES = ("controlDict", "fvSchemes", "fvSolution")


@dataclass
class Stage:
    """One run stage's numerics."""

    name: str
    start_from: str          # startTime | latestTime
    end_time: float
    max_courant: float
    alpha_scheme: str        # the div(phi,alpha) scheme
    c_alpha: float           # interface compression; ESI puts it in fvSolution
    velocity_scheme: str     # the div(rhoPhi,U) scheme
    n_outer_correctors: int
    purpose: str


def stages(cfg, *, single: bool = False) -> list[Stage]:
    """The run's stages, in order.

    *single* collapses a two-phase case to ONE full-length production stage. It
    exists for the Bayesian campaign: HydroBayesCal copies a case template and
    makes a single solver call, so a two-stage case would run only its spin-up -
    30 s at half interface compression and upwinded momentum - while the config
    claimed the full ``end_time``. The interface is settled once up front instead
    (``calibration._prespin_template``) and promoted into the template's ``0/``.
    """
    of = cfg.openfoam
    if single and of.mode != "rigid-lid":
        return [Stage(name="run", start_from="startTime", end_time=of.end_time,
                      max_courant=of.max_courant,
                      alpha_scheme="Gauss vanLeer", c_alpha=1.0,
                      velocity_scheme="Gauss limitedLinearV 1",
                      n_outer_correctors=2,
                      purpose="production run from a pre-spun interface "
                              "(the spin-up was run once into 0/)")]
    if of.mode == "rigid-lid":
        # No interface and no air, so the two-stage split has nothing to settle: the
        # spin-up exists to let a depth-averaged hotstart grow a vertical profile
        # without the interface sharpening into an instability, and here there is no
        # interface. One stage, at the full Courant number.
        return [Stage(name="run", start_from="startTime", end_time=of.end_time,
                      max_courant=of.max_courant,
                      alpha_scheme="Gauss vanLeer", c_alpha=1.0,
                      velocity_scheme="Gauss limitedLinearV 1",
                      n_outer_correctors=2,
                      purpose="single-phase run under a rigid lid")]
    return [
        Stage(name="spinup", start_from="startTime", end_time=of.spinup_time,
              max_courant=of.spinup_courant,
              # HALF the interface compression of the production stage. Compressing
              # hard while the hotstart still has no vertical velocity structure
              # sharpens the interface into an instability; dropping compression
              # altogether instead smears it over 30 s of spin-up, which is just as
              # unhelpful. ESI carries the coefficient as `cAlpha` in fvSolution,
              # not as a trailing number on the scheme the way Foundation did.
              alpha_scheme="Gauss vanLeer", c_alpha=0.5,
              velocity_scheme="Gauss upwind",
              n_outer_correctors=3,
              purpose="settle the interface and the vertical profile from the "
                      "depth-averaged 2D hotstart"),
        Stage(name="run", start_from="latestTime", end_time=of.end_time,
              max_courant=of.max_courant,
              alpha_scheme="Gauss vanLeer", c_alpha=1.0,
              velocity_scheme="Gauss limitedLinearV 1",
              n_outer_correctors=2,
              purpose="production run at the full Courant number"),
    ]


def _dict_file(obj: str, body: str, *, location: str, cls: str = "dictionary") -> str:
    return foam_header(cls, obj, location=location) + body + foam_footer()


# --------------------------------------------------------------------------- #
# constant/
# --------------------------------------------------------------------------- #


def transport_properties(cfg) -> str:
    of = cfg.openfoam
    body = f"""phases (water air);

water
{{
    transportModel  Newtonian;
    nu              {of.water_viscosity:g};
    rho             {of.water_density:g};
}}

air
{{
    transportModel  Newtonian;
    nu              {of.air_viscosity:g};
    rho             {of.air_density:g};
}}

sigma           {of.surface_tension:g};
"""
    return _dict_file("transportProperties", body, location="constant")


def turbulence_properties(cfg) -> str:
    """``constant/turbulenceProperties`` - the ESI name for the RAS settings.

    Foundation 8+ called this ``momentumTransport`` and keyed the model as ``model``;
    ESI v2406 knows neither, so a Foundation-shaped file is read as an empty
    dictionary and the run silently falls back to the built-in defaults.

    Under ``kEpsilon`` the closure coefficients are written **explicitly**, at the
    model's own defaults. Two reasons, both about calibration: OpenFOAM falls back
    to built-in values for any coefficient it does not find in the file, so a
    calibration that perturbs one which is absent would leave every run using the
    same value and never say so; and HydroBayesCal's ``update_dictionary_entry``
    requires the key to exist, raising ``Key 'Cmu' not found`` otherwise. Other
    models (kOmegaSST, the default) are written exactly as before.
    """
    of = cfg.openfoam
    if of.turbulence == "laminar":
        body = "simulationType  laminar;\n"
    else:
        coeffs = ""
        if of.turbulence == "kEpsilon":
            coeffs = (
                "\n    kEpsilonCoeffs\n    {\n"
                f"        Cmu             {of.kepsilon_cmu:g};\n"
                f"        C1              {of.kepsilon_c1:g};\n"
                f"        C2              {of.kepsilon_c2:g};\n"
                f"        sigmak          {of.kepsilon_sigmak:g};\n"
                f"        sigmaEps        {of.kepsilon_sigma_eps:g};\n"
                "    }\n"
            )
        body = f"""simulationType  RAS;

RAS
{{
    RASModel        {of.turbulence};

    turbulence      on;

    printCoeffs     on;
{coeffs}}}
"""
    return _dict_file("turbulenceProperties", body, location="constant")


def gravity() -> str:
    """``constant/g``. z is up: the mesh is in a projected CRS with real elevations."""
    body = "dimensions      [0 1 -2 0 0 0 0];\nvalue           (0 0 -9.81);\n"
    return _dict_file("g", body, location="constant",
                      cls="uniformDimensionedVectorField")


# --------------------------------------------------------------------------- #
# system/
# --------------------------------------------------------------------------- #


def control_dict(cfg, stage: Stage, *, patches: list[str],
                 boundary_patches: tuple[str, str] | None = None) -> str:
    of = cfg.openfoam
    body = f"""application     {of.solver};

startFrom       {stage.start_from};

startTime       0;

stopAt          endTime;

endTime         {stage.end_time:g};

deltaT          {of.initial_time_step:g};

writeControl    adjustableRunTime;

writeInterval   {of.write_interval:g};

purgeWrite      {of.purge_write:d};

// binary keeps a multi-million-cell case's output and the decomposed meshes
// compact; only the hand-written polyMesh is ASCII
writeFormat     binary;

writePrecision  8;

writeCompression off;

timeFormat      general;

timePrecision   6;

runTimeModifiable yes;

adjustTimeStep  yes;

// with MULESCorr the alpha equation is no longer explicitly Courant-limited, so
// maxAlphaCo can match maxCo instead of the tutorials' 0.2
maxCo           {stage.max_courant:g};
maxAlphaCo      {_max_alpha_courant(of, stage):g};

maxDeltaT       {of.max_time_step:g};

functions
{{
{_flux_functions(cfg, patches, monitor_interval(of))}

{_freedom_functions(cfg, boundary_patches, monitor_interval(of))}
}}
"""
    return _dict_file("controlDict", body, location="system")


def _freedom_functions(cfg, boundary_patches, interval: float) -> str:
    """Monitors that say whether the run was ever *boxed in* by the 2D seed.

    The seed decides two things the flow does not get a vote on: how high the lid
    stands, and how far the plan footprint reaches. Both are sized from a TELEMAC
    result, and TELEMAC can be wrong - so if the 3D surface rises into the lid, or
    reaches the lateral wall, the answer is being set by a mesh decision rather than
    by the hydraulics, and the result still looks perfectly plausible.

    Rather than parse the run's binary fields, this asks OpenFOAM for the water area
    on each of those two patches, through the same ``surfaceFieldValue`` machinery
    that already reports the discharge - so
    :func:`axqua.solvers.openfoam.report.surface_freedom` reads it back with the
    existing reader. Zero throughout is the answer you want.

    Emitted only for a two-phase run: under a rigid lid the surface is prescribed by
    construction, so "did it touch the lid" is not a question with an answer.
    """
    from . import potential

    if potential.is_potential(cfg):
        # No alpha to integrate, and nothing to find: flow THROUGH the top patch is
        # how the surface moves in this formulation, so a "leak" there is the
        # kinematics rather than a defect. The question leg C has to answer instead
        # is whether zeta stayed small against the depth, which measure_pool_levels
        # reads off zeta itself.
        return ("    // single phase: the free surface is a boundary condition, so\n"
                "    // there is no alpha to integrate and no lid to breach")
    if cfg.openfoam.mode == "rigid-lid" or not boundary_patches:
        return "    // rigid lid: the free surface is prescribed, not solved"
    top, walls = boundary_patches
    blocks = []
    for label, patch in (("lidContact", top), ("wallContact", walls)):
        blocks.append(f"""    {label}
    {{
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        writeControl    runTime;
        writeInterval   {interval:g};
        log             no;
        writeFields     no;
        regionType      patch;
        name            {patch};
        operation       areaIntegrate;
        fields          (alpha.water);
    }}""")

    # WETTED AREA IS THE WRONG SCALE FOR HOW BAD A LID BREACH IS, so measure the
    # flux through it as well. Measured on munich-vsf: 0.47% of the lid patch ever
    # wet - a figure that reads like rounding error - while that same 0.47% vented
    # 1.67 m3/s against a 0.111 m3/s pass. The leak is replaced through the
    # pressure outlet, so the domain carries a steady spurious circulation at
    # fifteen times the discharge and every other diagnostic still looks healthy:
    # the balance closes, the surface is stepped and monotonic, the run converges.
    # Area says how much of the lid is touched; only the flux says whether the
    # result is ruined.
    blocks.append(f"""    lidLeak
    {{
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        writeControl    runTime;
        writeInterval   {interval:g};
        log             no;
        writeFields     no;
        regionType      patch;
        name            {top};
        // weightedSum, not sum: ESI treats weighting as a separate operation, so
        // plain sum would report the AIR leaving the lid. That is how a 0.054 m3/s
        // water leak first read as 7.7 m3/s.
        operation       weightedSum;
        weightField     alpha.water;
        fields          (phi);
    }}""")
    return "\n\n".join(blocks)


def _boundary_patches(of_mesh) -> tuple[str, str] | None:
    """``(top, walls)`` for the surface-freedom monitors, or ``None``.

    ``getattr`` rather than attribute access: the monitors are a diagnostic, and a
    mesh object that cannot say which patch is its top simply does not get them.
    """
    top = getattr(of_mesh, "top_patch", None)
    return (top, BANKS_PATCH) if top else None


def _max_alpha_courant(of, stage: Stage) -> float:
    """Interface Courant limit.

    Under a rigid lid ``alpha`` is identically 1, so there is no interface to
    resolve and the limit would only hold the time step back for nothing - it is
    relaxed far above ``maxCo`` so the flow Courant number alone drives ``dt``.
    """
    return 10.0 if of.mode == "rigid-lid" else stage.max_courant


def monitor_interval(of) -> float:
    """Simulated-time spacing of the discharge monitors [s].

    A tenth of the field write interval, so a run writing ten result frames leaves a
    hundred discharge samples - enough for the ten-sample steady window
    :mod:`axqua.solvers.openfoam.report` looks for, without a line per time step.
    """
    return max(of.write_interval / 10.0, 1e-6)


def _flux_functions(cfg, patches: list[str], interval: float) -> str:
    """``surfaceFieldValue`` monitors giving the WATER discharge through each patch.

    ``phi`` is the total volumetric flux, water and air together; weighting it by
    ``alpha.water`` (a documented ``surfaceFieldValue`` option in OpenFOAM 9) gives
    the water discharge alone. That is the quantity the run has to converge on, and
    the one :mod:`axqua.solvers.openfoam.report` reads back - the direct analogue of the
    boundary-flux balance :mod:`axqua.flux_convergence` reads out of a TELEMAC
    listing.
    """
    from . import potential

    # SINGLE PHASE: phi IS the water flux, so weighting it by a phase fraction
    # that does not exist would abort the run on a missing field. The monitor is
    # also exact here rather than alpha-weighted, which is the point of leg C.
    # weightedSum, NOT sum, for a two-phase case. ESI treats weighting as a
    # SEPARATE OPERATION (typeWeighted is a bitmask on the enum and opSum is not in
    # it), so `operation sum` silently ignores weightField and reports the MIXTURE
    # flux. Foundation v9 applied the weight to plain sum, so the v9 -> v2406 move
    # changed what every discharge monitor meant without changing a line of any
    # case. Caught by an impossibility: a lid "leaking" 7.69 m3/s of water through
    # 0.1 m2 of wetted area, which needs 77 m/s against a 6 m/s cap.
    weight = ("" if potential.is_potential(cfg)
              else "\n        weightField     alpha.water;")
    op = "sum" if potential.is_potential(cfg) else "weightedSum"
    blocks = []
    for patch in patches:
        blocks.append(f"""    Q_{patch.replace('-', '_')}
    {{
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        // runTime, NOT timeStep: with an adaptive dt a per-step interval samples
        // the history unevenly in time - dense during the stiff opening, sparse
        // once the run speeds up - which is the opposite of what a convergence
        // history needs. 'runTime' writes at the first step past each interval and,
        // unlike 'adjustableRunTime', does not constrain dt to land on it.
        writeControl    runTime;
        writeInterval   {interval:g};
        log             no;
        writeFields     no;
        regionType      patch;
        name            {patch};
        operation       {op};{weight}
        fields          (phi);
    }}""")
    return "\n\n".join(blocks)


def fv_schemes(cfg, stage: Stage) -> str:
    body = f"""ddtSchemes
{{
    default         Euler;
}}

gradSchemes
{{
    default         Gauss linear;
}}

divSchemes
{{
    div(rhoPhi,U)   {stage.velocity_scheme};
    div(phi,alpha)  {stage.alpha_scheme};
    // the compression flux term. Foundation folded this into an
    // `interfaceCompression` scheme on div(phi,alpha); ESI keeps it a separate
    // divergence with its strength set by cAlpha in fvSolution.
    div(phirb,alpha) Gauss linear;
    div(phi,k)      Gauss upwind;
    div(phi,omega)  Gauss upwind;
    div(phi,epsilon) Gauss upwind;
    div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;
}}

laplacianSchemes
{{
    // 'limited corrected 0.33' rather than plain 'corrected': the terrain-following
    // mesh is non-orthogonal wherever the bed slopes, and the limiter keeps the
    // explicit correction from over-shooting there
    default         Gauss linear limited corrected 0.33;
}}

interpolationSchemes
{{
    default         linear;
}}

snGradSchemes
{{
    default         limited corrected 0.33;
}}

// kOmegaSST needs the distance to the nearest wall. 'meshWave' is the exact
// (front-marching) method; on a bed-following mesh the cheap 'Poisson'
// approximation is noticeably wrong in the thin near-bed layers, which is exactly
// where the blending function it feeds decides between k-omega and k-epsilon.
wallDist
{{
    method          meshWave;
}}
"""
    return _dict_file("fvSchemes", body, location="system")


def _alpha_mules(cfg, stage: Stage) -> str:
    """Whether the alpha equation is solved implicitly (MULESCorr) or explicitly.

    For the two-phase case ``yes`` is the whole reason the time step is usable: it
    decouples dt from the interface Courant number.

    Under a rigid lid it is the opposite. ``alpha`` is identically 1 - there is no
    interface, and nothing to advect - so the implicit correction solves a linear
    system whose answer is already known. Measured on isar-2025 (5x coarse,
    14,632 cells): 60 s of river took **88 s** with MULESCorr and **21 s** without,
    for the same time step (0.363 vs 0.369 s) and the same ``alpha`` (1 to 8
    significant figures). Four fifths of the cost was a correction with nothing to
    correct.
    """
    if cfg.openfoam.mode == "rigid-lid":
        return ("// alpha is identically 1 under the rigid lid: nothing to advect, so\n"
                "        // the implicit correction is pure overhead (measured 4x)\n"
                "        MULESCorr       no;")
    return ("// semi-implicit MULES: this is what decouples the time step from the\n"
            f"        // interface Courant number and lets maxAlphaCo run at "
            f"{stage.max_courant:g}\n"
            "        MULESCorr       yes;")


#: isoAdvector reconstruction schemes ESI v2406 ships, in increasing order of
#: fidelity. `isoAlpha` is the original isosurface cut; the two PLIC schemes fit a
#: plane per interface cell, `plicRDF` iterating it against a reconstructed distance
#: function. On aXqua's own structured hex lattice - isoAdvector's best case - PLIC
#: is the one worth having.
ISO_RECONSTRUCTION = ("isoAlpha", "gradAlpha", "plicRDF")


def _alpha_iso(cfg) -> str:
    """The ``alpha.water`` entries for ``interIsoFoam``, in place of MULES.

    isoAdvector advects the interface GEOMETRICALLY - it reconstructs a surface in
    each interface cell and fluxes the volume that crosses each face - rather than
    solving a transport equation and fighting the resulting smearing with a
    compression term. Two things follow that this case needs.

    **The discharge stops being wrong.** The inflow condition controls the MIXTURE
    flux, so the water entering is `Q*<alpha^2>/<alpha>`, which equals Q only for a
    sharp interface. Under MULES on munich-vsf that ratio was 0.821, steady: the
    case believed 0.135 m3/s and delivered 0.111, and the discharge balance could
    not see it because inflow and outflow were short by the same amount. Measured on
    the v2406 tutorial, isoAdvector holds `min(alpha) = 0, max(alpha) = 1 + 8e-10`,
    against this case's `-1.3e-08 .. 1.0001118` under MULES.

    **The interface stops setting the time step.** There is no compression flux to
    resolve, so the Interface Courant number is not a separate limit - the one that
    climbed to 93% of the flow Courant on the 0.045 m run and became the limiter.

    `cAlpha` is written although isoAdvector ignores it: `interfaceProperties` reads
    it when it is constructed, and the run aborts without it.
    """
    scheme = getattr(cfg.openfoam, "iso_reconstruction", "plicRDF")
    if scheme not in ISO_RECONSTRUCTION:
        raise ValueError(
            f"openfoam.iso_reconstruction {scheme!r} is not one of "
            f"{', '.join(ISO_RECONSTRUCTION)}")
    return f"""        // GEOMETRIC interface advection: no compression flux, so no
        // interface Courant limit and no smearing for the inlet to lose
        // discharge to. See axqua.solvers.openfoam.dicts._alpha_iso.
        reconstructionScheme {scheme};
        isoFaceTol      1e-6;
        surfCellTol     1e-6;
        nAlphaBounds    3;
        snapTol         1e-12;
        clip            true;
        writeFields     false;

        nAlphaSubCycles 1;
        // ignored by isoAdvector, but interfaceProperties reads it on construction
        cAlpha          1;"""


def _alpha_block(cfg, stage: Stage) -> str:
    if cfg.openfoam.solver == "interIsoFoam":
        return _alpha_iso(cfg)
    return f"""        nAlphaCorr      2;
        nAlphaSubCycles 1;
        // interface compression strength. Foundation carried this as a trailing
        // number on the div(phi,alpha) scheme; ESI reads it here.
        cAlpha          {stage.c_alpha:g};

        {_alpha_mules(cfg, stage)}
        nLimiterIter    5;
        alphaApplyPrevCorr yes;"""


def fv_solution(cfg, stage: Stage) -> str:
    body = f"""solvers
{{
    "alpha.water.*"
    {{
{_alpha_block(cfg, stage)}

        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-9;
        relTol          0;
    }}

    "pcorr.*"
    {{
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-5;
        relTol          0;
    }}

    p_rgh
    {{
        // GAMG, not PCG: on a multi-million-cell mesh the pressure solve dominates
        // and a geometric-algebraic multigrid is several times faster
        solver          GAMG;
        tolerance       1e-7;
        relTol          0.01;
        smoother        DIC;
    }}

    p_rghFinal
    {{
        $p_rgh;
        tolerance       1e-8;
        relTol          0;
    }}

    "(U|k|omega|epsilon).*"
    {{
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-7;
        relTol          0.1;
    }}

    "(U|k|omega|epsilon)Final"
    {{
        $U;
        relTol          0;
    }}
}}

PIMPLE
{{
    // buoyancy dominates in a free-surface flow: the momentum predictor costs an
    // extra solve and buys nothing
    momentumPredictor no;

    nOuterCorrectors {stage.n_outer_correctors};
    nCorrectors     3;

    // the bed-following mesh is non-orthogonal where the bed is steep; without
    // these the pressure solution leaks that as spurious near-bed velocity
    nNonOrthogonalCorrectors 2;
}}

relaxationFactors
{{
    equations
    {{
        ".*"            1;
    }}
}}
"""
    return _dict_file("fvSolution", body, location="system")


def fv_options(cfg, velocity_cap: float) -> str:
    body = f"""// Cap on the velocity magnitude. In the two-phase case this is the single most
// effective stop on the AIR phase destroying the time step; under a rigid lid there
// is no air, and it remains only as a cheap divergence guard. Air is ~1000x lighter than water, so a small pressure
// error accelerates it enormously; the resulting jet then sets the Courant number
// and the step collapses on a phase this model does not care about.
//
// {velocity_cap:.2f} m/s is several times the water velocity this reach carries, so
// the water solution never reaches the cap and is untouched by it. Raise it if a
// legitimate jet (a steep chute, a weir nappe) is being clipped - the run log
// reports how often the constraint bites.
limitU
{{
    type            limitVelocity;

    selectionMode   all;

    max             {velocity_cap:.4g};
}}
"""
    return _dict_file("fvOptions", body, location="system")


def decompose_par_dict(cfg) -> str:
    n = max(int(cfg.openfoam.n_processors), 1)
    body = f"""numberOfSubdomains {n};

// 'scotch' rather than 'simple'/'hierarchical': the wetted corridor is a sinuous,
// non-convex block, so a geometric split would hand some processors mostly empty
// space. Scotch balances by cell count and minimises the interface.
method          scotch;
"""
    return _dict_file("decomposeParDict", body, location="system")


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #


def write_dicts(of_mesh, cfg, case_dir: str | Path, *,
                velocity_cap: float) -> list[Path]:
    """Write ``constant/`` and ``system/`` (both staged dict sets)."""
    case_dir = Path(case_dir)
    constant = case_dir / "constant"
    system = case_dir / "system"
    constant.mkdir(parents=True, exist_ok=True)
    system.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    from . import potential

    leg_c = potential.is_potential(cfg)
    for name, text in (("transportProperties",
                        potential.transport_properties(cfg) if leg_c
                        else transport_properties(cfg)),
                       ("turbulenceProperties", turbulence_properties(cfg)),
                       ("g", gravity())):
        written.append(_write(constant / name, text))

    written.append(_write(system / "decomposeParDict", decompose_par_dict(cfg)))
    # limitVelocity is kept for leg C too, and dropping it was a mistake worth
    # recording. The argument for dropping it was that the cap exists to keep the
    # AIR off the time step and leg C has no air - true, but it is also the only
    # thing bounding a bad seed, and this case's 3D pre-run has not reached flux
    # balance (imbalance 0.58, and one node carrying a 14.9 km free surface).
    # Without the cap, leg C went from Courant 0.65 to 2.54 in a single step and
    # died with SIGFPE inside kOmegaSST::correct(). Here the cap bounds WATER, so
    # it is a startup safeguard rather than a phase trick, and the run reports how
    # many cells it touched - if that number is not ~0 once settled, the result is
    # not to be read.
    written.append(_write(system / "fvOptions", fv_options(cfg, velocity_cap)))

    # A case built by an older aXqua against Foundation OpenFOAM carries
    # constant/momentumTransport and system/fvConstraints. ESI reads neither, so
    # leaving them is worse than clutter: the run proceeds on built-in turbulence
    # defaults and with no velocity cap, and nothing in the log says the files were
    # ignored. Same class as a stale reconstructed time directory.
    for stale in (constant / "momentumTransport", system / "fvConstraints"):
        if stale.is_file():
            stale.unlink()
            log.info("removed %s, which only Foundation OpenFOAM reads", stale.name)

    patches = of_mesh.inlet_patches + of_mesh.outlet_patches
    wanted = {STAGE_DIRS[s.name] for s in stages(cfg)}
    for name in set(STAGE_DIRS.values()) - wanted:
        # A rebuild that changes the mode changes which stages exist, and a stage
        # directory left behind by the previous one is not merely clutter: activate()
        # would happily copy a two-phase stage1-spinup (MULESCorr yes, maxCo 0.3) into
        # a rigid-lid case, and the case would run - just not as the config describes.
        stale = system / name
        if stale.is_dir():
            for f in stale.iterdir():
                f.unlink()
            stale.rmdir()
            log.info("removed the stale %s stage directory (this mode does not use it)",
                     name)

    for stage in stages(cfg):
        stage_dir = system / STAGE_DIRS[stage.name]
        stage_dir.mkdir(parents=True, exist_ok=True)
        written.append(_write(stage_dir / "controlDict",
                              control_dict(cfg, stage, patches=patches,
                                           boundary_patches=_boundary_patches(
                                               of_mesh))))
        written.append(_write(stage_dir / "fvSchemes",
                              potential.fv_schemes() if leg_c
                              else fv_schemes(cfg, stage)))
        written.append(_write(stage_dir / "fvSolution",
                              potential.fv_solution(cfg) if leg_c
                              else fv_solution(cfg, stage)))

    # activate the first stage so the case is runnable straight out of the build.
    # Named rather than hardcoded: a rigid-lid case has one stage ("run"), because
    # with no interface there is nothing for a spin-up to settle.
    activate(case_dir, stages(cfg)[0].name)
    return written


def activate(case_dir: str | Path, stage: str, cfg=None, *,
             single: bool = False) -> None:
    """Make one stage's dictionaries the active ``system/`` set.

    With *cfg* the three staged files are **regenerated from that config** before
    being activated, so a run always honours the configuration it was launched with.
    Without it they would come from the build, and changing ``end_time`` or
    ``max_courant`` and re-running ``openfoam_run.py`` would silently run the old
    values while every message reported the new ones - a trap precisely because
    nothing about it looks wrong.

    The patch names are read back from the written mesh
    (:func:`axqua.solvers.openfoam.polymesh.read_patch_names`), so regenerating needs no
    remeshing and no state carried over from the build.
    """
    import shutil

    from axqua.solvers.openfoam.polymesh import read_patch_names

    case_dir = Path(case_dir)
    system = case_dir / "system"
    src = system / STAGE_DIRS[stage]

    if cfg is not None:
        target = next((s for s in stages(cfg, single=single) if s.name == stage),
                      None)
        if target is None:
            raise ValueError(f"unknown stage {stage!r}; expected one of "
                             f"{sorted(STAGE_DIRS)}")
        patches = [p for p in read_patch_names(case_dir)
                   if p.startswith(("inlet", "outlet"))]
        src.mkdir(parents=True, exist_ok=True)
        names = read_patch_names(case_dir)
        top = next((n for n in names if n in (ATMOSPHERE_PATCH, LID_PATCH)), None)
        bounds = (top, BANKS_PATCH) if top and BANKS_PATCH in names else None
        (src / "controlDict").write_text(
            control_dict(cfg, target, patches=patches, boundary_patches=bounds))
        # Leg C has its own single-phase numerics. This branch matters more than
        # the one in write_dicts: activate() REGENERATES from the config on every
        # run, so without it a correctly built leg C case is overwritten with
        # two-phase dictionaries at launch - which is exactly how it first failed,
        # on `div(phi,alpha)` in a case that has no alpha.
        from . import potential

        if potential.is_potential(cfg):
            (src / "fvSchemes").write_text(potential.fv_schemes())
            (src / "fvSolution").write_text(potential.fv_solution(cfg))
        else:
            (src / "fvSchemes").write_text(fv_schemes(cfg, target))
            (src / "fvSolution").write_text(fv_solution(cfg, target))

    if not src.is_dir():
        raise FileNotFoundError(
            f"no {stage!r} stage in {src}; rebuild the case with "
            "openfoam_preprocessing.py")
    for name in STAGED_FILES:
        shutil.copyfile(src / name, system / name)
    log.info("activated the %s stage (%s)", stage, STAGE_DIRS[stage])


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path
