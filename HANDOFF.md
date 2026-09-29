# munich-vsf: state for a restarting session

Rewritten 2026-09-29 on **lww-133**. Replaces the 2026-09-14 two-machine file, whose
premise (none of the case data is in git, so lww-133 cannot run it) no longer holds.

---

## 1. Something is probably running

```bash
pgrep -af 'interFoam|telemac2d|telemac3d'
```

At the time of writing: **interFoam, 16 ranks**, the VOF run of the fish-pass
sub-model. Logs live in the session scratchpad, not the repo:

```
/tmp/claude-11003/-srv-private-hydromate/<session>/scratchpad/of-run.log
```

A restarted session gets a NEW scratchpad, so that path is gone. Find the live log via
the OpenFOAM case directory instead:

```bash
ls -t cases/munich-vsf/axqua-case/openfoam/          # the case
ls -t cases/munich-vsf/axqua-case/openfoam/processor0/   # written times = progress
```

**Do not kill it to "start clean".** See §5 on orphaned ranks.

## 2. The goal, in the user's own framing

> The target is steady discharge across inlet and outlet. Calibration and validation
> will follow with lab flume velocity and depth readings (scaled). Federica's model is
> just fine but very slow and without georeferences. And this is why I put you on this
> task: remesh with a possibly regular clean channel mesh, cut off air fraction as well
> as possible with the help of telemac, and put georeferences.

So: **Federica's model is correct.** We are not reproducing or beating her physics; we
are building a faster, regular, georeferenced equivalent. Where our CAD-derived
geometry disagrees with her mesh, she is right.

Her baseline: interFoam, 1,748,288 cells, 8 ranks, 1,039,163 steps to t=3168 s. No logs
survive, so there is no wall-clock figure and none should be invented.

Standing constraints from the user:
- **physical correctness outranks speed**, even when slow;
- cost levers are (1) a possibly regular mesh, (2) cut the air fraction via TELEMAC
  pre-wetting;
- **"hydromate" is a protected name we do not own** - never in directories, env names
  or new writing. Legacy compat shims are deliberately kept;
- the pass/weir **discharge split cannot be measured in the lab and never was**. Do not
  validate against it. The ~108 l/s / 80% figure is design intent, not data.

## 3. Where the work stands

| deliverable | state |
| --- | --- |
| steady discharge in = out | **DONE** - 100.19% over T 1088..1200 s, mass error 1e-15 |
| georeference | **DERIVED AND VERIFIED, not applied** - see §4 |
| regular clean channel mesh | **BUILT** - 0.03 m lattice, 889,152 cells, 49% fewer than Federica |
| cut the air fraction | **PARTIAL** - 70% of cells are still air at t=0; dt is set by the air |

The 2D result `r2d-fill.slf` is the converged parent and the seed for everything
downstream. The pass steps **0.1251 m per pool** against the 0.1297 m drawn, and the
head-pool water surface elevation is **2.219 m** - within 11 mm of lww-134's
independent two-control (slot + weir) prediction of 2.208 m. That agreement is the
strongest cross-check the project has.

Known limits of that result: pools 10-14 drown as tailwater backs in (steps decay
0.102 -> 0.030 m), so the pass runs free over roughly its upper two thirds only.

Cost so far: 889,152 cells x 74,667 steps = 6.6e10 cell-steps against Federica's
1.8e12 - **27x less work**, mostly because 120 s is enough where she ran 3168 s. But
per simulated second we take 622 steps to her 328: the air still sets the time step
(velocity cap 6 m/s on a 0.021 m layer). The lever is `openfoam.freeboard`, currently
0.30 m over water 0.26-0.62 m deep.

## 4. The georeference: verified, deliberately not applied

```
rotation_deg: -74.559804   dx: 4473052.6711   dy: 5332085.5380   scale: 1.0
```

Local CAD metres -> EPSG:31468 (DHDN / GK zone 4). Recorded in
`cases/munich-vsf/case-config.yml` under `surfaces:` with the full reasoning.

**The fit's residual is not evidence.** The 14 baffle tips are collinear to 0.1 mm and
equally spaced, so they fit onto the DXF under ANY pairing, including the 180-degree
reversal (lww-134 caught this; the residual collapses to 5e-5 m either way).

**Verified off-axis instead:** the slot blocks sit 0.1202 m to one side of the tips, so
a reversal flips them. Transformed to GK4 they land at +0.1200 m northing, where the
DXF carries 112 vertices; the -0.15..-0.05 m band the reversal needs is EMPTY. This
agrees with lww-134's independent orientation from the drawing's
`Oberwasser`/`Unterwasser` labels.

**Why it is not applied:** every other layer (structures-merged, pass-walls,
clear-channel, baffle-stations, the drape) and ALL of Federica's reference data are in
local metres - her model carries no georeference at all. Applying the transform puts
the case in two frames at once, and frame confusion caused more wrong conclusions in
this work than anything else. Sequence: finish in local, then georeference everything
in one pass and re-verify.

To apply: set the three values in `surfaces:`, re-run the surfaces stage with
`force=True`, then regenerate every derived layer.

## 5. Traps this work actually fell into

Read this before trusting any measurement.

**Coordinate frames - seven times.** Six cross-channel "rulers" and one along-axis scan
each produced a confident wrong answer, because they sampled a PROJECTED frame instead
of the real geometry. The pass axis is straight; the channel turns east near s=26. The
worst cost a 10-hour run and sent lww-134 chasing a hole in the CAD that does not
exist.

> Measure along the actual geometry. For patency, walk the reference bed band
> (`federica-bed-reach.csv`), not the pass axis. For the slot, take the minimum distance
> between the two mesh holes - it runs diagonally between two corners, so no ray across
> the channel can ever find it.

**Stale artifacts - three times.** A `geometry.slf` from the previous night; a mesh read
while the solver was still writing partitions; and an `r2d.slf` a week old from the
broken geometry, which the OpenFOAM chain would have read silently. **Check the
timestamp before believing a result.** The stale one is archived as
`r2d-STALE-2026-09-21-broken-geometry.slf`.

**Orphaned MPI ranks.** `timeout`-wrapped solver calls kill the Python parent but NOT
the MPI children. Eleven survived one killed run and cost a factor of 3.3 in contention
while producing a plausible-looking but meaningless timing. Before trusting any rate:

```bash
for p in $(pgrep -f 'telemac3d|telemac2d|interFoam'); do
  echo "$p $(ps -o etimes= -p $p)"; done      # wildly different ages = strays
```

**Absurd numbers, not failing checks, caught most of these.** A closing buffer that
deleted a wall (7.41 -> 0.00 m2), a scalloped cut edge that broke the mesher, a 39-hour
estimate for a 45-second run. None errored. That is not a system to rely on.

## 6. Geometry: how it is built, and the rules that keep it right

The CAD alone is not enough. Three things are constructed:

- **`build_pass_walls.py`** - both pass walls. The far one is a ZERO-THICKNESS sheet in
  `stahlbeton.stl`, so no footprint method can ever see it; the near one was inside a
  drape blob that an earlier fix deleted. Without them the pass is open along its
  length and water leaves sideways.
  **Rule: a constructed wall must never stand where the reference mesh says BED.** The
  walls are traced over a fixed extent along a STRAIGHT axis while the channel turns,
  so without this they run past the turn and choke the outlet - which is exactly what
  happened.
- **`build_structures_layer.py`** - merges CAD baffles with the drape. **Inside the
  clear channel the CAD is the authority; outside it the drape stands, CLIPPED not
  dropped.** Dropping whole features deleted the near wall once.
- **`extract_bed_from_polymesh.py`** - the reference bed from Federica's mesh. Path via
  `AXQUA_REFERENCE_POLYMESH` (here: `/home/IWS/schwindt/Munich-VSF-reference/6_v5_HQ100/polyMesh`).

**A part declared `role: bed` is terrain and must never become a structure.** The
column-occupancy test cannot tell a wall from raised terrain, so it typed the weir and
the exit apron as wall; under `solid_mode: cut` that DELETES the overflow path and the
pass outlet. All 7 `Substratum_*`/`Magerbeton*` patches are protected, read from the
config rather than guessed from names (lww-134's fix - my name-prefix version missed
844 of 1,893 reference points).

Current geometry, verified: slots **0.1698 m = 100.1% of the 0.1697 m CAD at all 14**;
clear width median 1.150 m vs 1.165 design; patency along the true channel narrowest
1.82 m of a 2.60 m band.

## 7. The other machine

lww-134, coordinated on **GitHub issue #3 of `sschwindt/aXqua`**. Both push to
`rigid-lid-applicability`. They are a careful reviewer and have caught real errors -
the collinear-control warning, the `Magerbeton` omission, the missing weir in a
discharge calculation. Read their comments before re-deriving anything.

Open with them: their `patency_check` walks the pass axis, the frame that hid the
outlet choke - it should follow the reference bed band instead. Their measured
**C_Q = 0.496 +/- 0.011** for the slot geometry is the first non-assumed discharge
coefficient the project has.

## 8. Running things

```bash
# build the 2D case (surfaces stage is cached; force=True to redo it)
mamba run -n axqua-env python cases/munich-vsf/preprocessing.py

# the OpenFOAM scripts DEFAULT TO case-config.yml, which is rigid-lid, and will
# correctly REFUSE (the surface steps 152% of the local depth at the slots).
# Pass the VOF config explicitly:
mamba run -n axqua-env python cases/munich-vsf/openfoam_preprocessing.py case-config-vof.yml
mamba run -n axqua-env python cases/munich-vsf/openfoam_run.py case-config-vof.yml
```

Merging partitioned TELEMAC results for a mid-run look (gretel prompts in this order):

```
T2DGEO / SERAFIN / T2DCLI / T2DRES / SERAFIN / <ncsize> / 0 / 2
```
