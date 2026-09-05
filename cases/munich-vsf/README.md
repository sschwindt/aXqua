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

## What this case will and will not answer

The 2D stage exists to give a converged, mass-balanced result, a first roughness
estimate and the seed for what follows. A depth-averaged model cannot represent the
vertical structure of a slot jet, and aXqua's OpenFOAM mesher represents the walls as
vertical prisms on a height field, so chamfers and the cladding profile are lost. If
the jets turn out to depend on that detail, the alternative is a snappyHexMesh or
cfMesh case built from these same STLs directly - deliberately not what this case does.
