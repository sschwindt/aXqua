# Case: inn-KB15-2025-hydro

TELEMAC-2D hydraulic model of the Inn river subreach **KB15** (Bavaria), built
from the 2025 UAS survey and calibrated with HydroBayesCal against FlowTracker2
field campaigns at several steady discharges (multi-flow Bayesian calibration
of the channel roughness).

## Structure and workflow

Standard aXqua case layout (see the repository `README.md` and
`CLAUDE.md`); everything is driven by `case-config.yml` (all paths relative to
this folder). Ordered steps:

1. `preprocessing.py` - full case build into `axqua-case/simulation/`
   (mesh `geometry.slf`, `boundaries.cli`, `friction.tbl`, `steady2d.cas`).
2. `initial_run.py` - steady test run + boundary-flux convergence check.
3. `mesh_convergence_study.py` - horizontal grid independence.
4. `prepare_corrected_targets.py` - **case-specific**: compiles the corrected
   calibration-target CSVs into `axqua-case/preprocessing/` (see "Data
   particularities" - run this before any calibration re-run).
5. `run_Bayes_cal_multiflow.py [--smoke|--run|--resume]` - multi-discharge
   Bayesian calibration (one shared channel `ks` against all campaigns
   jointly). `run_Bayes_cal.py` is the single-flow variant.
6. Optional: `add3d.py` / `vertical_convergence_3d.py` (3D extension),
   `unsteady_run.py` (hydrograph-driven run).

Inputs live in `user-sources/` (gitignored), produced artifacts in
`axqua-case/` (gitignored), split by phase. Calibration artifacts land in
`axqua-case/calibration-validation/multiflow/` (per-flow trees
`flow-<name>/`, combined surrogate + posterior under
`auto-saved-results-HydroBayesCal/`).

## Field campaigns (ground truth)

| campaign | Q (m3/s) | points | kind | role |
|---|---|---|---|---|
| Sept 2025 | 47.3 | 30 verticals, reach-spanning (2 pools) | `adapter` | main velocity + water-level target |
| Nov 2025 | 48.45 | 22 verticals, one taped cross-section | `transect` | secondary target (down-weighted) |
| March 2026 | 45.8 | 5 near-bank margin verticals | `inline` | **excluded (comparability, not discharge)** - the old "Q=168, model ~5x too fast" reason was a discharge mislabel; at the true 45.8 m3/s the field magnitudes match the model to order, but these few margin points sit in partially-wet cells and add no discharge diversity (45.8 ~= 47.3 ~= 48.45). Wadeable velocity surveys are safety-capped at low flow, so high flow must enter via water-level / flood-extent targets. |

Values: `user-sources/ground-truth/hydraulics/FT_TKE_Summary*.xlsx`;
positions: `user-sources/geodata/flowtracker2/` and `TKE_KB15_Nov25.gpkg`.

## Data particularities (read before touching the ground truth)

### 1. DGPS pole-height offset (Sept 2025)

The raw DGPS layer `dgps-flowtracker-kb15-sept25.gpkg` stores the **GNSS
antenna elevation** in `z`, not the bed: the rover pole was set to **2.26 m**
in the upstream pool (verticals 1501-1511), **2.70 m** in the downstream pool
(1513-1530) and **2.51 m** for the transitional vertical 1512. The pole
heights were recovered from the flat-water-surface condition
(`z + WaterDepth - WSE = pole`, constant to +/-2 cm within each pool) and are
consistent with standard pole lock positions. Use
**`dgps-flowtracker-kb15-sept25-zcorrected.gpkg`** (bed `z = z_raw - pole`;
carries `z_raw_antenna`, `pole_height`, `wse_implied`). Never join targets to
the raw layer's `z`.

### 2. Wetted-channel bathymetry bias of the 2025 DEM

`DEM-2025-20cm.tif` is refraction-corrected bathymetric LiDAR (confirmed),
yet in the wetted channel its bed is **~0.27 m (pool 1) to 0.33 m (pool 2)
too high on average** against the pole-corrected DGPS beds, and the offset
**grows with water depth** (pool 1: +0.69 m per m of depth, r = 0.80,
p = 0.003; pool 2: +0.23, r = 0.39). A depth-proportional bias cannot come
from a datum/pole error (that would be constant); the mechanism is laser
attenuation in the glacially turbid Inn water column - the bathymetric LiDAR
resolves the shallow wetted areas well but loses or biases the bottom returns
in the deeper pools. Consequences:

* modelled water **levels** are excellent (model WSE matches the corrected
  DGPS water surface within ~1 cm in both pools at Q~47 m3/s);
* modelled **depths** are biased low and point **velocities** biased high
  (continuity through the artificially reduced section) - raw depth/velocity
  measurements are NOT directly comparable to the model at these points.

The calibration therefore uses **bathymetry-corrected depth targets**:
`WATER DEPTH_DATA := WSE_measured - bed_model(x, y)` (the depth the model
should show given its own bed if its water level is right - mathematically a
water-level calibration), and adds a **structural discrepancy term** to the
velocity errors (0.10 m/s Sept, 0.20 m/s Nov, ~`U * dbed / h`). All of this is
implemented and documented in `prepare_corrected_targets.py`. The proper
long-term fix is fusing the DGPS bed points (plus echo-sounding or denser
wading survey in the deeper pools) into the DEM's wetted channel, followed by
a rebuild.

### 3. Multi-depth ADV verticals (Sept 2025)

20 of the 30 Sept verticals were measured at **three depths**
(~0.3h / 0.6h / 0.9h; `profiles` sheet of `FT_TKE_Summary.xlsx`, one row per
measurement, same x-y per vertical). They are used two ways:

* **Calibration targets:** the velocity target for those verticals is the
  USGS three-point depth average `(u02 + 2*u06 + u08) / 4` instead of the
  single 0.6h proxy (mean shift -0.015 m/s, up to 0.08 m/s). Implemented in
  `prepare_corrected_targets.py`; single-point verticals keep the 0.6h value.
* **Vertical-profile evidence:** log-law fits `u(z') = (u*/kappa) ln(z'/z0)`
  per vertical (see `axqua-case/preprocessing/kb15-loglaw-profiles.csv`)
  give `ks = 30 z0` with median **0.089 m** (IQR 0.05-0.41 m, very noisy at
  these low wadeable velocities) - i.e. the profiles support a roughness well
  inside the prior `[0.05, 0.45]` m and clearly below its upper bound. They
  also confirm near-logarithmic profiles, which justifies (a) the 0.6h point
  as a depth-average proxy for single-point verticals in 2D and (b) the
  logarithmic `VELOCITY VERTICAL PROFILES` prescription used by the 3D
  extension (`add3d.py`).

### 4. Nov-25 transect internal inconsistency (open QA item)

Within the single 10 m Nov transect the implied water surface `z + depth`
spreads by 0.34 m - physically impossible. Its vertical 1 agrees with the
corrected Sept pool-1 water surface; the transect's water level is therefore
anchored to that surface (376.32 m at Q~48 m3/s) and its depth-target error
widened to 0.10 m. The raw `z`/depth columns of the other verticals need
field-book QA.

## Calibration setup and history

* Parameter: channel Nikuradse `ks` (`zone1` in `friction.tbl`), prior
  `[0.05, 0.45]` m (`d50 .. 3 d90` from the bed GSD). Floodplain (`zone2`)
  fixed at 0.5 m.
* Targets: `WATER DEPTH` (bathymetry-corrected water level) + `SCALAR
  VELOCITY` (profile-averaged, discrepancy-widened) - 52 points, 104 values.
* Design: 8 initial grid runs + up to 7 Bayesian-Active-Learning iterations,
  each collocation point = one TELEMAC run per flow (~50 min on 16 cores);
  `--resume` reuses the per-flow initial designs
  (`flow-<name>/.../restart_data/`).
* **History (2D calibrations, `multiflow-*` archives):**
  1. *ks-only, velocity-only, uncorrected data* (Jul 2026): posterior pinned
     at the prior upper bound (`ks -> 0.45`). Diagnostics traced this to the
     DEM bathymetry bias (item 2), not roughness; an outflow-stage sensitivity
     run (+0.30 m, `steady2d-q47-3-stagetest.cas`) ruled out the downstream
     boundary (both rating stages sit below the natural outfall level; steep
     controls isolate the measurement subreach).
  2. *ks-only, corrected water-level + velocity targets* (archived
     `multiflow-2026-07-15-ks-only/`): interior posterior **ks = 0.411 m**,
     90% CI [0.348, 0.446]; RE ~1.27 nats, 906 posterior samples. Water levels
     fit to ~1 cm; velocity still over-predicted (+0.19 m/s at the Nov-25
     transect).
  3. *3-parameter: ks + VELOCITY DIFFUSIVITY + wall roughness* (Sobol 16+9,
     extended to 45 runs to converge RE): posterior **ks = 0.429 m** (90% CI
     [0.396, 0.447]), while **diffusivity and wall roughness stay
     unconstrained** (near-flat marginals = prior). The two added parameters
     are **non-identifiable** from this data: they cannot absorb the
     near-bank velocity over-prediction (residual +0.03 m/s Sept, +0.22 m/s
     Nov at the posterior median), so ks stays high. RE converged to ~2.1
     nats (noisy at the +/-5% level from rejection-sampling). Plots:
     `auto-saved-results-HydroBayesCal/plots/posterior-3param-final.png`.

  **Conclusion of the 2D path:** water level is well-calibrated (RMSE 2-3 cm);
  the single-zone channel resistance settles at an *effective* ks ~ 0.43 m -
  inflated by a near-bank velocity bias that grain roughness, eddy viscosity
  and wall friction all fail to remove (measured log-law profiles give grain
  ks ~ 0.09 m). The residual is a 2D depth-averaged representation limit at
  the slow wadeable margins, compounded by the wetted-margin bathymetry
  bias - not a calibration-parameter problem. This motivates the 3D path.

## TELEMAC-3D non-hydrostatic calibration (active)

The 2D depth-averaged model cannot represent the slow near-bed / near-bank
velocities the FlowTracker sampled. The **20 multi-depth ADV verticals**
(item 3, three depths each) are genuine vertical-profile ground truth, so a
**non-hydrostatic TELEMAC-3D** model - which resolves the vertical velocity
structure and secondary currents - is calibrated against the velocity **at
each measured depth** rather than a depth-averaged proxy. The 3D case reuses
the 2D horizontal mesh, hotstarts from `r2d.slf`, and (per aXqua's 3D
extension) uses a single representative `FRICTION COEFFICIENT FOR THE BOTTOM`
(3D has no zonal friction file), sigma layers sized to `dz ~ dx/2`, and MURD
PSI advection for wetting/drying robustness. See `add3d.py` /
`axqua/threed.py`.

## Boundary conditions

Inflow Q per flow (prescribed flowrate), outflow stage from the synthetic
normal-flow rating (`stage_discharge`); at these discharges the rating stage
lies below the natural outfall water level, so the outflow effectively acts as
a free outfall - the reach is insensitive to the exact rating value (verified
by the +0.30 m sensitivity run).

## OpenFOAM steady-flow scenarios (September 2026)

Measured on this reach, on 8 cores, with OpenFOAM Foundation 9.

### Cost, measured not assumed

One run at the calibration-campaign settings (rigid lid, `cell_size_factor` 4 ->
70,262 all-hex cells, 600 s of simulated time):

| | |
|---|---|
| wall-clock | **407 s** (6.8 min) |
| time steps | 1,481 (adaptive, dt ~0.42 s) |
| cost | 3.9e-6 s per step per cell |

So a 48-run campaign is ~5.4 h. That figure is what `INIT_RUNS` / `MAX_RUNS` in
`run_Bayes_cal_openfoam.py` are sized from.

### Why the steady scenario runs under a rigid lid, not as VOF

The two-phase production case (1.5 m x 12 layers, 1,000,224 cells) is **not a
viable steady scenario**, for two independent reasons:

* its time step is set by the **air**, not the water - `limitVelocity` caps air at
  7.82 m/s and the thinnest layer is 0.023 m, giving dt ~1.3e-3 s and ~229,000
  steps for 300 s. At the measured cost that is **~249 h on 8 cores**;
* 300 s is shorter than **one flush of the reach (1,081 s)**, so it could not
  reach a steady state even if it were run. A full flush would be ~898 h.

A rigid lid removes the air phase by construction, so dt is set by the water
(~0.4 s), and the free surface is prescribed from the converged 2D result - which
is precisely what a steady scenario wants. The two-phase case remains the right
tool for **verifying** a calibrated posterior over a short window, where the
surface must be free to move; it is not the tool for obtaining a steady answer.

### How steadiness is judged here - NOT by the discharge balance

The discharge balance reports inflow = outflow = 47.3000 m3/s and "converged at
t = 2.14 s". **That is a tautology under a rigid lid**: the lid fixes the water
volume, so inflow must equal outflow at every step whatever the flow is doing.
`report.DischargeHistory` now says so instead of reporting it as convergence.

Judged on the velocity field instead, over the last 100 s of the 600 s run:

| t [s] | mean \|U\| [m/s] | change vs previous write |
|---|---|---|
| 500 | 0.57102 | - |
| 520 | 0.57085 | 1.13% |
| 540 | 0.57102 | 1.20% |
| 560 | 0.57070 | 1.26% |
| 580 | 0.57080 | 1.24% |
| 600 | 0.57099 | 1.21% |

The mean is flat to within 0.05%, but the per-write change **does not decay** - it
sits at ~1.2%. The flow is steady *in the mean* with persistent local
fluctuation. That is the justification for `N_AVG_TIMESTEPS = 5`: the calibration
averages the last 100 s rather than sampling one instant, so the surrogate is
trained on the settled mean and not on the fluctuation.

### A gap in the cost estimate, measured

`case.estimate_time_step` predicted dt 0.573 s for the production rigid-lid case
(1.57 m/s across 1.50 m cells, dropping through a 0.100 m layer on a 3.4% bed
slope: `0.9 / (1.57/1.5 + 1.57*0.034/0.100)`). The solver settles at a stable
**0.272 s** - a 2.1x overestimate, and not a startup transient: dt reaches 0.276 s
by step 200 and stays there, with Courant max 0.889 against the 0.9 target, so the
run is correctly Courant-limited and it is the estimate that is off.

The docstring claims this model lands within 25%, calibrated on isar. It does not
hold on this reach. Deliberately NOT retuned here: one contradicting case is not
enough to re-fit a heuristic, and the consequence is a run that takes about twice
as long as advertised rather than a wrong answer. Treat the printed step count as
a lower bound until a second reach either confirms or contradicts it.

Note also that this case reports `Interface Courant Number: 0` exactly, which is
the documented single-phase rigid-lid behaviour. The coarse campaign template
reports ~0.5 instead, because alpha drifts a few percent off 1 numerically there
(`Phase-1 volume fraction` stays exactly 1 in both, so neither develops a real
interface).

### The profiles comparison works mechanically, but is NOT yet meaningful here

`axqua postproc` extracts 21 of 30 verticals and produces `profiles.png` /
`profiles.csv`. **Do not read the residuals as a model-vs-measurement result.**
Three measurements from the steady rigid-lid run at Q = 47.3 m3/s say why:

| quantity | modelled | measured |
|---|---|---|
| bed elevation at the vertical | 374.90 - 376.23 m | 377.18 - 378.20 m |
| column depth | 0.12 - 0.24 m | 0.52 - 0.74 m |
| speed at 0.6*h | 0.001 - 0.024 m/s | 0.08 - 0.31 m/s |

The surveyed bed sits a median **+2.11 m above** the model bed at the same (x, y),
the modelled column is about a third of the measured depth, and the modelled speed
is two orders of magnitude too slow. A roughness error cannot produce that; it is
the **bathymetry/datum discrepancy already known on this reach** - the same one
that made zones 1 and 5 pin at their bounds in the August 2026 2D restart, and the
reason `prepare_corrected_targets.py` and `measurements-corrected-*.csv` exist.

Consequences, in order:

1. The raw `ground_truth.sources` z is unusable as a vertical reference here. The
   `profiles` scene therefore takes its z span from the **mesh**
   (`Query("SpatialExtents")`) and re-references each curve to its own first
   sample, i.e. the local bed. That makes the figure datum-independent, and is
   why it works at all.
2. The 9 verticals that produced no curve fall outside the trimmed wetted extent.
3. **Reconcile the bathymetry before drawing conclusions from the OpenFOAM
   velocity comparison**, exactly as for the 2D calibration. Until then the
   figure demonstrates the machinery, not the agreement.

Note this does not invalidate the running calibration: it targets `U_x/U_y/U_z`
at those points through HydroBayesCal, so it inherits the same caveat the 2D
velocity calibration already carries, no more and no less.

### The OpenFOAM velocity calibration was STOPPED at 16 runs, deliberately

HydroBayesCal's adaptive initial-design check flagged the 16-run design as
insufficient on all three counts (predictivity, worst_column,
posterior_resolution) with **2 of 5000 prior samples accepted** - and still only 2
after it redrew 60,000. That is not an under-sized design; it is an unreachable
likelihood. Checked directly against the 16 completed runs:

| test | result |
|---|---|
| observations bracketed by any of the 16 runs | **0 of 90** |
| distance from the run-mean | median **10 sigma**, 90th pct 36, max 51 |
| beyond 5 sigma from every run | 66 of 90 |
| modelled range | -0.250 .. +0.571 m/s |
| observed range | -0.614 .. +0.876 m/s |

No value of `ks` or `Cmu` anywhere in the prior can reproduce these measurements.
Bayesian active learning refines a likelihood surface; it cannot reach one that
lies outside the model's attainable range. The remaining 32 runs (~4 h) would have
returned a posterior pinned at whichever corner least-badly minimises an
impossible misfit - the same failure as the August 2026 2D restart, where zones 1
and 5 pinned at their bounds.

**Root cause is the bathymetry, not the roughness** (see the section above): the
surveyed bed sits +2.11 m above the model bed at the same (x, y), the modelled
column is a third of the measured depth, and the modelled speed at 0.6*h is two
orders of magnitude low. The velocity observations describe a channel the model
does not have at those coordinates.

**Before re-running**, reconcile the vertical datum / bathymetry - the job
`prepare_corrected_targets.py` already exists for, and which the 2D calibration
needed for the same reason. Then either resume from the preserved 16 runs
(`--resume`, only if the observations change but the parameter design does not)
or start clean (the safer choice once the targets move).

The 16 completed runs are preserved in
`auto-saved-results-HydroBayesCal/restart_data/`. Nothing about the aXqua
machinery is implicated: parameter routing, the solver launch, extraction and the
per-run accumulation all worked (16 runs, 16 accumulated rows, ks spanning
0.055-0.265). It was pointed at targets the model cannot match.

### RESOLVED: the "+2.11 m bathymetry" finding was mostly a wiring defect

The September-2026 OpenFOAM section above reported a +2.11 m offset and blamed the
bathymetry. That was **wrong**, and the correction matters. It was three stacked
errors:

| layer | magnitude | nature |
|---|---|---|
| GNSS antenna height | **+2.26 / 2.51 / 2.70 m** | `case-config.yml` joined ground truth to the RAW DGPS layer, whose z is the antenna. Fixed: it now points at `-zcorrected.gpkg`. |
| measurement height never added | **-0.34 m** | `calibration.build_calibration_csv` wrote `z = df["z"]`, the BED, so the extraction point sat on the bed where the wall function gives ~0. Fixed by model-relative placement. |
| DEM bathymetry bias | **+0.28 / 0.37 m** | real, physical, depth-proportional (+0.72 m per m of depth). Bathymetric-LiDAR attenuation plus some DGPS rod-foot sinking. Unchanged - and now irrelevant to target placement. |

Nothing complained because HydroBayesCal's OpenFOAM binding finds the nearest
extraction point with **no distance cutoff**, so a target 2.3 m above the water
silently returned ~0.003 m/s.

**The fix: targets are placed in the model's own column**,
`z = bed_model + f * depth_model`, with `f` the measured height above the bed as a
fraction of the column, read from `MeasD`/`FinalD`. Only x, y and a *ratio* come
from the survey, so no vertical survey error can enter (`axqua.model_column`;
audit in `target-placement.csv`).

Measured effect, sampling the steady rigid-lid run at the new placements:

| | before | after |
|---|---|---|
| modelled \|U_h\| | 0.001 - 0.024 m/s | **0.000 - 0.377** (median 0.107) |
| measured \|U_h\| | 0.008 - 0.879 m/s | 0.040 - 0.555 (median 0.271) |
| observations reachable | **0 of 90** | **68% at a single ks** |

Two caveats to carry forward. The model is still ~2.5x **slow** at 0.4*h (in 2D it
was 1.5x fast), which is consistent with the wall-function limitation: 100% of
wetted bed faces have y1 < ks at this resolution, so near-bed velocity is
suppressed. And 7 of 30 verticals fall outside the coarsened campaign lattice and
are dropped - 23 remain, which passes the placement guard.

Also fixed while here: `MEASUREMENT_FRACTION` in `postproc/profiles.py` was 0.6
documented as "height above the bed", but the 0.6-depth convention is measured
**from the surface** (MeasD/FinalD median 0.59 on this dataset), so every profile
comparison sat 0.2*h too high.

### `measurements-corrected-*.csv` are tied to a specific mesh build

`prepare_corrected_targets.py` could not be re-run at all: it hard-coded
`axqua-case/` against a tree named `hydromate-case/`. It now resolves both through
`load_config` (`config._resolve_sim_dir`), so a future rename cannot break it again.

Re-running it changes **exactly one column** - `WATER DEPTH_DATA`, by a median of
5 mm and at most 9.1 cm. Everything else (`id, x, y, z`, velocities, all errors) is
bit-identical. That is **not** a regression, and the check that proves it is:

* the regenerated file reproduces the bed of the **current** `geometry.slf` to
  **0.48 mm** (i.e. CSV rounding);
* the previous file implies a bed differing from the current mesh by up to 9.1 cm.

So the old file was written against a **different mesh**. The anisotropic BAMG
mesh is documented as not bit-reproducible (~0.4% node-count spread between builds
from identical inputs), and on a 0.5 m channel mesh over a rough bed that moves the
interpolated bed by centimetres.

The operational consequence: `WATER DEPTH_DATA = WSE_measured - bed_model(x, y)`
is a function of the mesh, so **these targets must be regenerated whenever the mesh
is rebuilt**. Comparing them byte-for-byte across a rebuild is not a valid
regression test; comparing the implied bed against the current `geometry.slf` is.

### The real blocker: bed roughness against a bed that is too high

After the target-placement fix the corrected campaign still failed - 3 of 90
observations bracketed at 9 runs, median 6.5 sigma. The cause is physical, not
wiring, and the number that shows it is:

**25 of 30 targets sat inside the bed roughness itself.** Median target height
0.119 m above the bed; ks = 0.155 m; modelled column 0.30 m, of which 17 of 30
columns are shallower than 2*ks. `nutkRoughWallFunction` clamps there, so the
modelled velocity is a boundary condition rather than a result, and no value of ks
can reproduce a measurement. Per component: U_x 1/30 bracketed (model 0.102 vs
measured 0.250 m/s), U_y 2/30, U_z 0/30.

Two consequences, both acted on:

1. **U_z is excluded from calibration** (still extracted). Raw FlowTracker VelZ on
   this campaign averages |0.255| m/s with values to -0.598 m/s in a 0.5-0.7 m deep
   reach - that is probe tilt or noise, not vertical velocity. The model gives
   0.001 m/s, which is right. A third of the observations were noise.
2. **The DEM bathymetry is corrected** (`correct_dem_bathymetry.py`). Fitting
   `correction = 0.076 + 0.332 * depth` on the 30 DGPS beds and applying it in the
   wetted channel cuts the residual from a median 0.340 m to **0.066 m** and takes
   the modelled column from 0.30 m to ~0.64 m, i.e. column/ks from 1.9 to 4.1 - a
   resolvable profile. The correction is capped at the deepest surveyed vertical
   (0.99 m), because 31% of the corrected area is deeper than anything surveyed and
   the linear fit would otherwise lower the bed by up to 1.26 m on no data.

The corrected raster is written but **not yet wired in**: adopting it invalidates
the mesh, `r2d.slf` and `measurements-corrected-*.csv`, so it is a deliberate
decision with a multi-hour rebuild behind it. Steps are printed by the script.

It remains a hypothesis about the bed, not a survey of it (r = 0.54, two pools,
105 x 47 m of a 1.6 km reach). It makes the 3D calibration possible; echo-sounding
or a denser wading survey is what would make the bathymetry known.
