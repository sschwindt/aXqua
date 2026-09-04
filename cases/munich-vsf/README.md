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

## What this case will and will not answer

The 2D stage exists to give a converged, mass-balanced result, a first roughness
estimate and the seed for what follows. A depth-averaged model cannot represent the
vertical structure of a slot jet, and aXqua's OpenFOAM mesher represents the walls as
vertical prisms on a height field, so chamfers and the cladding profile are lost. If
the jets turn out to depend on that detail, the alternative is a snappyHexMesh or
cfMesh case built from these same STLs directly - deliberately not what this case does.
