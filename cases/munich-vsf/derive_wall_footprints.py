"""Wall footprints from the REPAIRED CAD: material anywhere in the water column.

Replaces rasterise-and-hole-fill, which cannot do better on an open surface and costs
0.036 m of the 0.1697 m throat. A watertight solid can be asked directly whether it
occupies a point.

TWO BUGS THIS FILE EXISTS TO STOP REPEATING, both of which produced plausible-looking
wrong answers rather than errors:

* `np.meshgrid` puts row 0 at the SOUTH edge; `rasterio.transform.from_origin(west,
  north, ...)` expects it at the north. Without a `flipud` the footprint is mirrored
  about the window centre - far enough here to miss the ROI entirely, which reads as
  "the walls were never in the domain" and is not a conclusion to reach from a
  transposed array.
* this case declares `crs_epsg: 31468` (DHDN GK4) and runs in LOCAL CAD metres.
  Stamping the output EPSG:25832 makes `dataset()` reproject it into the void. The
  layer carries NO crs, because local metres are not a projection.

Verified after both: the footprint overlaps the ROI by 99% of its own area.


A single offset is fragile: at 0.05 m it lands on the baffle bottoms, so containment
flickers and the footprint comes out in 230 pieces. What a depth-averaged model needs
is whether a wall occupies the column at all, so the test is ORed over several heights.
Restricted to DEM-covered cells, which is also most of the cost.
"""
import time
import numpy as np, pyvista as pv, rasterio
from pathlib import Path
from scipy import ndimage

G = Path("cases/munich-vsf/user-sources/geodata")
DEM = Path("cases/munich-vsf/axqua-case/preprocessing/dem-from-surfaces.tif")
RES = 0.02
DELTAS = (0.10, 0.30, 0.60)      # heights above the local bed to test

solid = pv.read(G / "cad-repaired" / "stahlblech.stl").merge(
        pv.read(G / "cad-repaired" / "stahlbeton.stl"))
b = pv.read(G / "cad-repaired" / "stahlblech.stl").bounds
x0, x1, y0, y1 = b[0] - 0.3, b[1] + 0.3, b[2] - 0.3, b[3] + 0.3
xs = np.arange(x0, x1, RES); ys = np.arange(y0, y1, RES)
gx, gy = np.meshgrid(xs, ys)
with rasterio.open(DEM) as src:
    bed = np.array([v[0] for v in src.sample(
        list(zip(gx.ravel(), gy.ravel())))], dtype=float)
    nod = src.nodata
good = np.isfinite(bed) & (bed != nod)
idx = np.flatnonzero(good)
print(f"{gx.size:,} cells, {len(idx):,} with DEM ({100*good.mean():.0f}%)")

blocked = np.zeros(gx.size, bool)
for dz in DELTAS:
    t0 = time.perf_counter()
    pts = np.column_stack([gx.ravel()[idx], gy.ravel()[idx], bed[idx] + dz])
    d = pv.PolyData(pts).compute_implicit_distance(solid)["implicit_distance"]
    hit = np.asarray(d) < 0
    blocked[idx] |= hit
    print(f"  +{dz:.2f} m: {hit.sum():,} inside ({time.perf_counter()-t0:.0f} s); "
          f"cumulative {blocked.sum():,}")

mask = blocked.reshape(gx.shape)
lab, n = ndimage.label(mask)
sz = ndimage.sum(mask, lab, range(1, n + 1)) * RES * RES
print(f"\n{n} solid pieces, total {mask.sum()*RES*RES:.2f} m2")
print(f"  pieces over 0.02 m2: {sorted(round(v,3) for v in sz if v > 0.02)[:20]}")
openg = ~mask & good.reshape(gx.shape)
dist = ndimage.distance_transform_edt(openg) * RES
lo, nn = ndimage.label(openg)
osz = ndimage.sum(openg, lo, range(1, nn + 1))
big = lo == (int(np.argmax(osz)) + 1)
w = np.array([2 * dist[:, j][big[:, j]].max() for j in range(mask.shape[1])
              if big[:, j].any()])
print(f"  open corridor: {nn} regions; narrowest column openings "
      + " ".join(f"{v:.4f}" for v in np.sort(w)[:8]))
np.save(f"{Path(__file__).parent}/drape2.npy", mask)
print(f"  saved {mask.shape} origin ({x0:.3f},{y0:.3f}) res {RES}")
