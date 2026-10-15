# Olsen geometric outlet rating: experimental, opt-in, serial

BAW's `waterLevel_alpha_prgh` library is a **VOF `interFoam`** boundary condition. It does not fit Olsen's single-phase `p`/`Conc` solver. The installer builds BAW separately and adds this small, distinct research extension as a separate `sediDriftFoam2Rating` executable. The baseline `sediDriftFoam` and `sediDriftFoam2` receive no rating extension. All three receive the required v2406 API fix `simple.loop(runTime)` → `simple.loop()`; v2406's [`simpleControl`](https://gitlab.com/openfoam/core/openfoam/-/raw/OpenFOAM-v2406/src/finiteVolume/cfdTools/general/solutionControl/simpleControl/simpleControl.H) accepts no Time argument.

The extension is disabled when `constant/ratingCurveProperties` is missing or `enabled false`. To enable, copy [the example](../examples/ratingCurveProperties) to that path in a **disposable case copy**, replace every example rating value with your data, set `enabled true`, and invoke `sediDriftFoam2Rating` explicitly. Keep the original case `p`: kinematic pressure with zero-gradient `freeSurface` and zero fixed outlet pressure. Do not use BAW's physical `p_rgh` values here.

## What it changes

After each SIMPLE pressure solve, the outward volumetric flux sum on `outletPatch` gives `Q` [m³/s]. Piecewise-linear interpolation of `table ((Q eta) ...)` gives `eta` [m], in the mesh's absolute z datum. Q must increase strictly; stage must not decrease. Negative Q aborts. Out-of-range Q aborts unless `outOfBounds clamp` is explicitly chosen; there is no extrapolation.

The existing free-surface movement becomes

```text
deltaZ = relaxation * ((p_surfaceCell - p_outletAdjacentSurfaceCell)/gravity
                       + eta(Q) - currentSurfaceZ)
```

Downstream free-surface nodes are identified by intersection with outlet patch nodes. After smoothing, each is forced to `oldZ + relaxation*(eta-oldZ)`. This converges to the prescribed stage; one iteration reaches it only for `relaxation 1`. The pressure reference is selected from an actual outlet-adjacent surface face, not the last arbitrary surface face. `relaxation` is dimensionless per **hydraulic iteration** and independent of `timeStepSediments`.

Surface motion can run during `initialIterations`. Bed movement, morphodynamic time advance, and slope-slide remain off until that spin-up finishes. Invalid depth aborts; the rating path does not lower the bed to manufacture water depth. The disabled path of `sediDriftFoam2Rating` retains upstream morphology/geometry behavior, except it explicitly rejects MPI decomposition. Baseline solver2 should also be run serially: its original algorithm is not decomposition-safe.

This is a **quasi-steady research extension**, not a transient ALE solver. The original solver does not demonstrate mesh-relative flux correction or geometric-conservation-law compliance. Changing domain volume need not balance a physical timestep's net flux. Do not claim flood routing, rapid transient continuity, wetting/drying, hydraulic-jump validity, or scientific validation from a successful compilation.

## Required validation before production

1. Run upstream and patched-disabled copies of the same short serial case; compare fields, mesh points, and scour series. Baseline behavior should agree.
2. Use an equal-stage two-point table covering Q. With matching initial outlet stage, compare to the original fixed-stage geometry (`waterRelax` equal to extension `relaxation`). Check pressure reference differences are negligible; the newly selected reference cell can differ from upstream's last cell.
3. Freeze morphology by setting `initialIterations` above the entire short run. Test stage adjustment; verify bed z and logged sediment time remain unchanged, target convergence follows relaxation, no columns invert, and `checkMesh -latestTime` passes.
4. Test an increasing rating at multiple constant inlet discharges. Compare measured outlet stage and `sum(phi)` to the table, confirm upstream hydraulic profiles, and record flow residuals/volume changes. Then enable morphology and repeat mesh/erosion conservation checks.
5. Verify failure cases: missing patch, incompatible topology, reverse Q, table bounds, stage datum error, and target below bed. No case must be silently repaired with extra erosion.

Topology restrictions remain Olsen's: serial, z-up vertical contiguous columns shorter than 1,000 points, quadrilateral corresponding `bedWall`/`freeSurface` faces, hexahedral cells, and an outlet sharing the downstream surface edge. Source patching refuses an unfamiliar SHA256 rather than guessing changed anchors. Mesh quality still requires `checkMesh`; these checks cannot prove hydraulic validity.

Upstream: [Olsen solver2](https://www.pvv.ntnu.no/~nilsol/sediDriftFoam2/sourceCode/), [Olsen solver1](https://www.pvv.ntnu.no/~nilsol/sediDriftFoam/sourceCode/), [BAW HydBCsForOF](https://github.com/baw-de/HydBCsForOF).
