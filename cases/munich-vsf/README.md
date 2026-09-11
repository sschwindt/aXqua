# Munich vertical slot fishway - Hachinger Bach, Kreillerstrasse

A fish pass built from its **CAD assembly** rather than from a survey. There is no DEM
of it and there never will be, so the case uses the `surfaces` block: stage 0 of the
build rasterises the bed parts into a DEM, turns the walls into solid footprints with
crests, the inlet/outlet patches into liquid-boundary lines, and the materials into
roughness zones. Everything after that is an ordinary aXqua case.

Three steady design discharges: **60 L/s (Q30), 135 L/s (MQ), 1000 L/s (HQ100)**.

## The CAD

The geometry lives at `/home/modelling/OpenFOAM/Munich-VSF/` and is staged here by:

```bash
cp /home/modelling/OpenFOAM/Munich-VSF/stl-files/*.stl  user-sources/geodata/cad/
cp /home/modelling/OpenFOAM/Munich-VSF/cad-files/*.dxf  user-sources/geodata/cad/
```

About 1.14 GB, all ASCII STL, 6.2 M facets in total. `user-sources/` is gitignored and
the 20 MB CI file-size gate does not see it. (Until 2026-09 those files were mode `0600`
owned by `scolari` and unreadable to anyone else; they are now `0660` group `abt1_alle`.)

The part names in `case-config.yml` match the export: `substratum`,
`substratum_amphibienweg` and `magerbeton` are the bed; `stahlbeton`, `stahlblech` and
`edelstahlverkleidung` are the structure; `inlet` and `outlet` are the flow boundaries;
`inlet-box`, `air-sides` and `air-top` are the VOF air patches and are ignored.

### What the geometry turns out to be

The derived model is **24 x 88 m in local metres** with a 258 m2 wetted domain. The bed
falls **1.86 m** from the inlet end (bed 2.5 m, inlet patch at z = 2.30 m) through the
pool-and-slot reach at y 45-67 to the outlet (bed 0.33 m). The fish pass proper is a
**1.09 m wide channel with 12 baffles and 11 pools**, each pool 1.55-1.65 m long, the
slots pinched to 0.32-0.35 m, running beside a 1.78 m wide parallel channel. Stage 0
reads the 6.2 M facets in ~20 s and writes everything in ~80 s.

**Georeferencing is unresolved, and the case runs in local coordinates meanwhile.** The
drawing *is* georeferenced - DHDN / Gauss-Krueger zone 4 (EPSG:31468), easting
4 473 041, northing 5 332 067 - but it cannot be matched by bounding box, because the
sheet carries plans, sections and a title block, so its extent (126 x 29 m) is not the
structure's (24 x 88 m). `axqua georef` reports that rather than guessing. To fix it,
identify two features in both the CAD and the drawing and pass them as control points:

```bash
axqua georef --stl user-sources/geodata/cad/substratum.stl \
  --control-point <cadx>,<cady>:<mapx>,<mapy> \
  --control-point <cadx>,<cady>:<mapx>,<mapy>
```

Two pairs fix the transform exactly; a third checks it (its residual is reported).

## Then, in order

```bash
# 1. CAD -> geodata (DEM, ROI, boundary lines, wall footprints, roughness zones)
axqua surface case-config.yml

# 2. the measured cross-sections, recovered from the pool geometry
python make_sections.py

# 3. build the TELEMAC case, then test-run it
axqua case-config.yml --check
python preprocessing.py
python initial_run.py

# 4. the other two discharges, from the same build (no re-meshing)
#    see axqua.bayescal.write_flow_cas

# 5. grid independence, then the 3D extension
python mesh_convergence_study.py
python add3d.py --run hydrostatic
python vertical_convergence_3d.py

# 6. OpenFOAM, seeded by the converged 2D result
axqua openfoam case-config.yml --check     # cell count first
python openfoam_preprocessing.py
python openfoam_run.py
```

## Reference data

The physical 1:3 flume campaign is published at
<https://zenodo.org/records/14440623> (BSD-3). Only `physical-flume/` is used here.

```bash
python lab_reference.py        # downloads the two workbooks, writes section-reference.csv
python compare_sections.py     # modelled vs measured, once results exist
```

It is **four cross-sections per discharge**, each with a mean water depth and an ADV
velocity maximum - 12 depth and 12 velocity values, all cross-section *aggregates*.
They are therefore compared aggregate-to-aggregate (`axqua.labdata` against the section
statistics of `axqua.solvers.telemac.sections`), not turned into point targets in the
calibration CSV, where a modelled point velocity would be compared against a measured
section maximum.

Two things to settle before drawing conclusions from that comparison:

1. **Scale - settled.** Both sheets are already at prototype scale: the depth summary
   says so in its title ("SUMMARY IN PROTOTYPE (1:3) DIMENSIONS") and the velocity
   summary follows the same convention throughout, per the dataset's author. So no
   Froude conversion is applied, and in particular the velocities are *not* multiplied
   by `sqrt(3)`. `LENGTH_SCALE` and `VELOCITY_SCALE` in `lab_reference.py` stay explicit
   at 1.0 so that the choice is visible rather than assumed.
2. **Cross-section positions.** XS1-XS4 are labelled US2, US4, US5 and US7 - pool
   numbers, with no coordinates. `make_sections.py` recovers them from the CAD instead
   of guessing: it finds the pool reach (a cross-cut meets three open segments there
   rather than one), picks out the fish pass as the channel that is pinched once per
   baffle, and puts a section through the middle of each requested pool.

   ```bash
   python make_sections.py                     # pools 2, 4, 5, 7, numbered from upstream
   python make_sections.py --number-from downstream
   ```

   On this geometry it finds **12 baffles and 11 pools**, every pool 1.55-1.65 m long
   and 1.06-1.09 m wide, with the slots pinched to 0.32-0.35 m - the regularity a
   vertical-slot fish pass is designed with, which is itself a check that the detection
   is right. XS1-XS4 land at y = 49.4, 52.6, 54.2 and 57.3 m.

   **The one thing the geometry cannot settle is which end pool 1 is at.** Sections are
   numbered from the upstream (inflow) end by default; if the report counts from the
   downstream end, re-run with `--number-from downstream`. The plot it writes to
   `axqua-case/postprocessing/pool-sections.png` labels every pool, so comparing it
   with the report settles the question at a glance.

### 13 basins, not 11 - settled

`make_sections.py` reported 12 baffles and 11 pools. It was wrong, and the newer sources
in `user-sources/geodata/` say so three times over:

| source | baffles | basins | pitch | span |
| --- | --- | --- | --- | --- |
| contractor STL (`stahlbeton` + `stahlblech`) | 14, plus an end wall | **13** | 1.640 m, std 0.010 | 21.44 m |
| `blender-heightmap-high-res.jpg` | 14 | **13** | 1.64-1.65 m | 21.45 m |
| `fishpass-dimensions-ssc.fodp` | - | - | 1.50 m clear | 22.70 m reach |

Measured by rotating the CAD onto the reach's own axis (bearing 76.8 deg) and counting
periodic wall features across it, and independently by counting the baffle glyphs in the
heightmap against the drawing's own dimension lines - the drawing is 1 cm to the metre,
so its 22.70 m dimension is drawn 22.58 cm long and the scale checks itself.

**The two figures are not two designs.** The pitch agrees to a centimetre between the
contractor CAD and the Blender remodelling, which is what says they are the same
structure measured twice rather than a plan revision. What differed was the *detection*:
`make_sections.py` finds the pool reach by a cross-cut heuristic and dropped the two end
basins, where the channel transitions into the entry chamber and the outlet.

The drawing also reconciles its own numbers. Its **1.50 m** is the *clear* basin length,
between baffle faces; the pitch is 1.65 m and the baffle wall is about 0.15 m thick.
22.70 m of reach = 1.08 m entry chamber + 13 x 1.65 m of basins + the outlet.

And it settles what `make_sections.py` could only guess at - **where XS 1-4 actually
are**, as stations along the drawing:

| section | station | where |
| --- | --- | --- |
| XS 1 | 3.4 m | approach channel, upstream of the structure |
| XS 2 | 11.2 m | basin 3 |
| XS 3 | 17.8 m | basin 7 |
| XS 4 | 32.3 m | exit channel, 3.87 m below the reach |

Note that XS 1 and XS 4 are in the approach and exit channels, **not in basins at all**,
which is why the report's `US2 / US4 / US5 / US7` labels never mapped onto pool numbers.
Two of the four flume sections are channel sections, and the comparison has to treat
them as such.

## The steady run, and judging it

`hydrodynamics.turbulence_model` is set to **3 (k-epsilon) rather than `auto`**. The
auto-selection picks Smagorinski here, and Smagorinski carries no turbulent kinetic
energy: there is no `K` to print, so TKE could be neither converged against nor
calibrated - and the flume data has TKE columns. The steering therefore prints
`'U,V,S,B,H,M,Q,F,K,E'`.

```bash
python check_convergence.py                    # depth, velocity, TKE, discharge
python check_convergence.py --tolerance 0.005  # stricter than the 1% default
nohup ./watch_run.sh > /dev/null 2>&1 &        # a record while nobody is looking
tail -f axqua-case/simulation/convergence-watch.log
```

The test is the **RMS change over the wet nodes, divided by the field's own RMS**, not
the worst node: at a wetting front a node going from 1 to 2 mm is a permanent 100%
change, and a per-node maximum would never converge however still the reach became. The
worst node is reported alongside so a genuinely misbehaving one stays visible. Anything
that cannot be measured fails rather than passes. "Wet" means **5 cm**, aXqua's own
`min_depth`, not a numerical dry threshold - on this bed 5 mm of water stands inside the
grain roughness, and thousands of fringe nodes flickering wet and dry dominated the
statistic (velocity reads 6.9% at 5 mm and 3.4% at 10 cm, for the same flow).

**Two criteria, because there are two kinds of quantity here.** A vertical slot fishway
has no steady state to find: at constant discharge the slot jets flap and the pool gyres
shed, so the flow settles to a *statistically stationary* state - a fixed mean with
sustained fluctuation about it. Depth and discharge are therefore judged instantaneously,
as before, while velocity and TKE are judged as **window means**: the record's last third
is split in two, each half averaged in time node by node, and the two averages compared.
A stationary flow passes that easily; one still developing does not. The instantaneous
fluctuation is reported alongside as the *band*, so unsteadiness reads as a number rather
than as failure.

### What the first 1800 s actually showed

| quantity | at t = 1800 s | reading |
| --- | --- | --- |
| depth | 0.33% per frame | settled |
| boundary discharges | 0.000% / 0.072% | settled |
| velocity | window mean **7.5%**, band 3.6% | mean still drifting |
| TKE | window mean **7.5%**, band 3.4% | mean still drifting |
| mass balance | Qin - Qout = **+2.4%** of inflow | still filling |

The run was a **dry start** - the log says so: 6183 inflow-plug nodes at 0.20 m, dry
everywhere else - so all 89.5 m3 the reach eventually holds had to arrive through the
inflow at 0.135 m3/s. That is 663 s of pure volume at best, and because the pools fill in
a cascade the approach is exponential with a time constant of **553 s**, measured off the
storage curve. At t = 1800 s the domain was at 97.8% of its final storage and still
taking on 5.5 L/s. The velocity mean drifts because the pools are still deepening under
it; the 3.6% band is the jets and is irreducible.

So the answer was not a longer *first* run but a **continuation**:

```bash
python continue_run.py                       # another 1800 s from r2d.slf
python check_convergence.py --result r2d-hotstart.slf
```

### What the continuation showed - converged

1800 s more, 6.5 h of wall clock, and every criterion is met:

| quantity | first run | after the continuation | |
| --- | --- | --- | --- |
| depth | 0.33% | **0.347%** | converged |
| velocity | window mean 7.5% | **window mean 0.376%** | converged |
| TKE | window mean 7.5% | **window mean 0.325%** | converged |
| boundary 1 | 0.000% | **0.000%** | converged |
| boundary 2 | 0.072% | **0.060%** | converged |
| mass balance | +2.394% of inflow | **+0.037%** | balanced |

The velocity band is **3.7% per frame, unchanged** from the first run - which is the
point of separating the two measures. The filling drift fell by a factor of twenty
because it was a transient; the fluctuation did not move at all because it is the jets,
and it never will. `|Qin| - |Qout|` closing from 3.2 L/s to 0.05 L/s is the same fact
seen from the mass side.

1800 s more is about three time constants, taking the residual filling from 4% of the
inflow to roughly 0.1%. `continue_run.py` writes `hotstart2d.cas` through
`axqua.solvers.telemac.steering.write_hotstart_cas` - the same case with its initial
conditions switched to `PREVIOUS COMPUTATION FILE : r2d.slf` and the prescribed Q and
downstream stage carried over - so nothing is re-derived and nothing is re-filled. Chain
it again with `--from-result r2d-hotstart.slf` if the window means have not settled.

**The other two discharges should be continued, not restarted.** They share this mesh, so
the converged 135 L/s field is a far better starting point than a dry bed: the reach is
already full and the run only has to redistribute flow. That is most of a working day
saved per scenario.

```bash
python continue_run.py --discharge 0.060 --stage <tailwater>
python continue_run.py --discharge 1.000 --stage <tailwater>
```

The downstream stage is the scenario's own tailwater, not something to interpolate, so it
has to be given; without it the 135 L/s value is kept and the script says so.

Rough cost at 5 cm resolution, 273k elements, 8 cores: about **6 s of simulated time per
minute of wall clock**, so 1800 s takes roughly five hours.

### The tailwater is too high, and it shows

The converged run's own outlet report says so:

```
outlet profile: backwater (near-boundary surface slope 1.4 permille vs 28.8 permille
                           in the reach above)
      0-3   m: WSE 0.6923  H 0.262 m  |U| 0.348 m/s  Fr 0.22
     20-40  m: WSE 0.7132  H 0.307 m  |U| 0.301 m/s  Fr 0.18
     40-70  m: WSE 2.0657  H 0.924 m  |U| 0.349 m/s  Fr 0.12
  -> the prescribed outflow stage sits ABOVE the reach's own level
```

`hydrodynamics.prescribed_elevation: 0.69` holds the lowest 40 m of the domain as a flat
pool at a **1.4 permille** surface slope where the reach above it falls at **28.8
permille**. That is a boundary condition backing water up over ground that would
otherwise drain, and it is also where the 34% of wetted area the wetting report calls
*stagnant film* (69 m2, 26 m3, |U| < 0.05 m/s) comes from - the report notes that the
film has **plateaued**, so it is not a transient a longer run removes.

None of that touches the fish pass itself, which sits at y 45-67 with WSE 2.07 and is
hydraulically upstream of the backwater. But it does mean two things:

* the downstream 40 m of this model is not a prediction of anything, and nothing should
  be read off it;
* **before the flume comparison**, either lower the stage to the reach's own outlet level
  or set `outflow_condition: free` and let the model find it. The 0.69 m came from the
  CAD, not from a measurement, and the model is now good enough to say it is wrong.

### Resolution, and what it limits

The mesh is uniform at a median **4.7 cm** (273k elements, 138k nodes; 2.6-7.6 cm range).
The slots are 0.32-0.35 m, so a slot is spanned by about **7 elements**. The usual
requirement for a resolved jet is 10-15 across the opening, so the slot jets here are
under-resolved and their spreading rate - and with it the pool velocities the flume data
is compared against - is mesh-dependent to an extent this case has not yet measured.

Refining only the pool reach (y 45-67, 96k of the 273k elements) would cost:

| target | element size | pool-reach elements | total | run time |
| --- | --- | --- | --- | --- |
| 10 per slot | 3.2 cm | x2.1 | ~375k | ~3x |
| 15 per slot | 2.1 cm | x4.7 | ~630k | ~8x |

Run time scales worse than element count because the variable step holds the Courant
number: halving the cell size roughly doubles the step count as well, so cost goes as
size^-3. `mesh_convergence_study.py` is the instrument for this question and computes a
GCI over a four-mesh ladder - but it is a multi-day study at these sizes, and every level
must be run long enough to *finish filling* or the GCI will compare four mid-fill states
and attribute the transient to discretization.

### The air phase was the whole cost, and it is now gone

The first sub-model VOF run is recorded in `axqua-case/openfoam/vof-record/`. It was
correct, stable and mass-balanced, and it was never going to finish. Measured on the
running job over 6 h 50 min on 16 ranks:

| quantity | measured |
| --- | --- |
| cells | 1,340,928 (111,744 columns x 12 layers) |
| wall per step | 8.70 s (24,584 s over 2,824 steps) |
| time step | 5.7e-4 s, having fallen from 9.5e-4 at t = 0.016 s |
| throughput | 6.5e-5 s of river per second of wall clock |
| stage 1 (8 s) | ~1.3 days remaining at t = 1.605 s |
| stage 2 (120 s) | **~22 days** |

Two lines from that log say where the money went. The `0/` fields report **39.5 % of
cells start as water**, so 60.5 % of the mesh was air; and `Interface Courant Number max`
equals `Courant Number max` on *every step in the log* - the cell setting the time step
was always an interface cell, never a water cell. `MULESCorr yes` did not buy the
decoupling its own comment claims: `maxAlphaCo` was the binding limit throughout.

So the run spent ~60 % of its cells and essentially all of its time-step budget on an
air-water interface that this case has no interest in. What the case actually needs from
the free surface is only that it be **non-horizontal**, and that is already known: the
converged 2D result has it, to a few millimetres, everywhere.

`openfoam.mode: rigid-lid` takes it. The lid is built from `State2D.sample_surface` at
every plan vertex, so it *is* the 2D free surface - sloping, stepped over the baffles, no
freeboard - as a slip wall. `alpha` is identically 1, so `interFoam` degenerates to a
single-phase solver while keeping its `p_rgh` + gravity treatment intact, and there is no
interface left to set the Courant number. What it costs is that the surface becomes an
**input**: it cannot rise, overtop or wet a dry bar, the waterline is a fixed vertical
wall, and `outlet_stage` is not applied (the lid geometry already fixes the level there).

That last trade is recoverable without ever meshing air. `p_rgh` on the lid is exactly
the elevation the surface wanted and the geometry refused, `dz = p_rgh / (rho g)`; feeding
`surface + dz` back in as a corrected seed and rebuilding converges the surface in two or
three passes. `correct_lid.py` does that, and the `max|dz|` it reports each pass is also
the honest error bar on the prescribed-surface result.

**Measured, on the same 16 ranks, both at t = 0.93 s** - the same simulated time, which
matters: the VOF time step fell by half over its first second, so an early window would
have flattered either run.

| | VOF | rigid lid |
| --- | --- | --- |
| cells | 1,340,928 | **815,016** (101,877 columns x 8 layers) |
| air cells | 60.5 % | **none** |
| wall per step | 8.70 s | **3.79 s** |
| time step | 5.7e-4 s | **2.96e-3 s** |
| throughput | 6.5e-5 s/s | **7.8e-4 s/s** |
| 120 s costs | ~22 days | **~42 hours** |

**Twelve times faster**, and `Interface Courant Number max` now reads 0 on every step
because there is no interface for it to be measured on. Cumulative continuity error is
3.4e-8, `limitVelocity` is not clipping anything, and the time step is flat: 2.95e-3 to
3.03e-3 over 100 consecutive steps, against a VOF step that was still falling when it
was stopped. The three windows (whole run, last half, last 100 steps) agree to 2 %.

**Both columns above are single-tenant figures**, and the machine did not stay that way.
At about 15:00 on 2026-09-08, 20 hours in, a second 16-rank job started on the same 16
physical cores and the cost per step stepped from 3.80 s to 8.91 s - flat before, flat
after, a 2.34x ratio and no ramp, which is contention rather than anything in the flow.
The time step never moved (3.02e-3 throughout), so the model is unaffected; only the
wall clock is. The run therefore lands at **~70 h rather than ~42**. Both numbers are
worth keeping: 42 h is what this case costs, 70 h is what it cost on a shared box.

That is short of the ~60x the arithmetic suggested, and the missing factor is in the time
step: 3.1e-3 s against the 1.5e-2 predicted from a 1.8 m/s slot jet across 3 cm cells.
OpenFOAM's Courant number sums the flux over all six faces of a cell rather than taking
the worst direction, so a cell carrying both a horizontal jet and the vertical velocity
the baffle steps force through a 2.5 cm layer runs out of Courant budget several times
sooner than the horizontal estimate says. The prediction was optimistic; the run is not
in trouble - `Co` is pinned at its 0.90 ceiling and `dt` bottomed at 2.95e-3 and is
recovering.

### Two things the rigid-lid build taught, both measured

**The lid steps ~1 m across a single 3 cm cell** where the 2D free surface passes a
structure edge - measured lid gradient 34.8 there against a mesh-wide p95 of 0.81. A
first attempt set `layer_expansion: 0.6` to thicken the bed layer towards `ks`, which
also makes the *top* layer the thinnest, exactly where that step is: 256 incorrectly
oriented face pyramids, which checkMesh treats as fatal. Uniform layers give zero. The
folds are a lid problem, not a bed problem, and smoothing the lid does not fix them.

**The mesh is better than the one it replaces on every measure but skewness**: 85,394
faces over 70 deg non-orthogonality against the VOF mesh's 488,897, zero folded faces
against 0 - but max skewness 80.9 with 3,032 faces over 4, against 12.4 and 50. Those
sit at the same lid steps. At 0.12 % of faces and with the pressure solve converging in
2-3 GAMG iterations, they are being carried rather than fixed.

## What this case will and will not answer

The 2D stage exists to give a converged, mass-balanced result, a first roughness
estimate and the seed for what follows. A depth-averaged model cannot represent the
vertical structure of a slot jet, and aXqua's OpenFOAM mesher represents the walls as
vertical prisms on a height field, so chamfers and the cladding profile are lost. If
the jets turn out to depend on that detail, the alternative is a snappyHexMesh or
cfMesh case built from these same STLs directly - deliberately not what this case does.
