# Reference geometry, taken from an independent simulation of the same structure

`federica-wetted-bed.csv` is the **lowest wetted boundary** of a previously built
OpenFOAM model of this fish pass, sampled onto a 0.25 m plan grid over the pass reach.
It is ground truth for "where is the bed, and what is it made of", independent of
anything aXqua derives from the STLs.

| column | meaning |
| --- | --- |
| `x`, `y` | cell centre, local CAD metres (same frame as the STLs) |
| `bed_z` | lowest wetted face in that cell, metres |
| `patch` | which part of the structure that face belongs to |

1,893 cells cover the reach from y = 42 to 70.

## Where it comes from

`/home/modelling/OpenFOAM/Munich-VSF/6_v5_HQ100/constant/polyMesh` on lww-134, read
directly: 2,049,035 points, 5,520,365 faces, the 328,368 boundary faces split by the 18
patches in `constant/polyMesh/boundary`. Only wall patches are kept - the inlet, outlet
and the air patches are dropped, so every row is solid ground the water touches.

That model is a 100-year-flood case at 60 l/s inflow. It never fully converged (its time
step collapsed 21x over the run) so **its water levels are not a reference**. Its
*geometry* is, because it was meshed by snapping onto the same CAD surfaces.

## What it establishes

Measured from these rows, and worth keeping because two of them contradict what the
build was assuming:

* the **amphibian path runs 0.76 to 1.14 m above the fish pass invert** at every
  station, offset 3 to 4 m to the side. It is not a second channel and at the design
  discharge it is dry;
* the **lean-concrete apron is real wetted ground**, 48.5 m2 at 0.13 to 0.73 m - a
  larger bed area than the fish pass invert's 32.5 m2. The pass is a flume with 2.845 m
  walls standing beside a lower apron, and the walls are what keep them apart;
* the **pass invert** falls from 2.11 m to 0.22 m, and the **wall tops are 2.845 m**,
  so the walls stand about 1.67 m above the invert;
* the **baffle tops are 1.45 to 1.94 m**, well below the side walls, which is why a
  water level over 2.845 m drowns the whole structure into one pool.

## How to use it

Compare `bed_z` against `dem-from-surfaces.tif` at the same `x, y`. Agreement to a few
centimetres means the CAD-to-DEM path is sound there; a large difference means a part is
missing, mis-declared, or an overhang a height field cannot carry.

Checked at the pass entrance (y = 43 to 48) the two agree to within 4 cm:

```
  y = 43   reference 2.056   DEM 2.078
  y = 44   reference 2.003   DEM 2.029
  y = 45   reference 1.921   DEM 1.962
  y = 46   reference 0.130   DEM 0.130
```
