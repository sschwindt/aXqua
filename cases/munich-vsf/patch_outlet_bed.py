"""Bridge the CAD's missing bed at the pass outlet, using the reference mesh.

Our CAD has no bed from s = 26.25: a real hole between where the fish pass ends and
where `Substratum_final` begins. `surface_stage.py` derives the ROI as raster coverage
of the bed parts and then keeps only `_largest_part`, so the gap does not fail the
build - it silently TRUNCATES the domain mid-pass. The pass then had no outlet, drained
through a 0.28 m side strip holding 1.554 m of head, carried 4% of the discharge, and
still passed every check we had: connected mesh, 1e-15 mass balance, 98% throughput,
water surface elevation 0.492 m below the wall tops.

Federica's mesh does cover it (`extract_bed_from_polymesh.py`), and the user's position
is that her model is correct. So the bed comes from her, not from interpolation: this
writes a patched DEM and a patched ROI, and the case points at those instead of the
derived pair.

BOTH have to be patched. A patched DEM alone leaves the ROI truncated and the domain
still ends mid-pass - the bed would exist where the mesher is not allowed to go.

    python cases/munich-vsf/patch_outlet_bed.py
"""
from __future__ import annotations

import csv
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import rowcol
from scipy.interpolate import griddata
from shapely.geometry import MultiPoint
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
PRE = HERE / "axqua-case" / "preprocessing"
REF = HERE / "user-sources" / "reference" / "federica-bed-outlet.csv"
DEM_IN, DEM_OUT = PRE / "dem-from-surfaces.tif", PRE / "dem-patched.tif"
ROI_IN, ROI_OUT = PRE / "roi-from-surfaces.gpkg", PRE / "roi-patched.gpkg"
#: half a reference cell, so the 0.05 m samples close into a surface
BUFFER = 0.035
#: the one-row seam on the patch boundary at y~67.53 that her mesh also has
SEAM = 0.30


def main() -> None:
    pts = [(float(r["x"]), float(r["y"]), float(r["bed_z"]))
           for r in csv.DictReader(REF.open())]
    P = np.asarray(pts)
    print(f"reference bed at the outlet: {len(P):,} points, "
          f"x {P[:,0].min():.2f}..{P[:,0].max():.2f}, y {P[:,1].min():.2f}..{P[:,1].max():.2f}, "
          f"z {P[:,2].min():.3f}..{P[:,2].max():.3f}")

    with rasterio.open(DEM_IN) as src:
        dem = src.read(1).astype(float)
        prof, tr, nod = src.profile, src.transform, src.nodata
    missing = (dem == nod) if nod is not None else ~np.isfinite(dem)
    print(f"DEM {dem.shape}, {missing.sum():,} nodata cells before")

    # fill only cells that are BOTH nodata and inside the reference's own footprint:
    # never overwrite CAD bed, and never invent bed the reference does not have
    foot = unary_union([MultiPoint([(x, y) for x, y, _ in pts]).buffer(BUFFER)]).buffer(SEAM).buffer(-SEAM)
    ys, xs = np.nonzero(missing)
    wx, wy = rasterio.transform.xy(tr, ys, xs)
    wx, wy = np.asarray(wx), np.asarray(wy)
    from matplotlib.path import Path as MPath
    polys = [foot] if foot.geom_type == "Polygon" else list(foot.geoms)
    inside = np.zeros(len(wx), bool)
    for poly in polys:
        inside |= MPath(np.asarray(poly.exterior.coords)).contains_points(
            np.column_stack([wx, wy]))
    print(f"  {inside.sum():,} nodata cells fall inside the reference footprint")
    if not inside.any():
        raise SystemExit("nothing to patch - check the CRS/extent of both inputs")

    z = griddata(P[:, :2], P[:, 2], np.column_stack([wx[inside], wy[inside]]),
                 method="linear")
    ok = np.isfinite(z)
    dem[ys[inside][ok], xs[inside][ok]] = z[ok]
    print(f"  filled {int(ok.sum()):,} cells, z {z[ok].min():.3f}..{z[ok].max():.3f} m"
          f"  ({int((~ok).sum())} outside the convex hull, left as nodata)")
    with rasterio.open(DEM_OUT, "w", **prof) as dst:
        dst.write(dem.astype(prof["dtype"]), 1)
    print(f"wrote {DEM_OUT.name}")

    roi = gpd.read_file(ROI_IN)
    before = roi.geometry.iloc[0].area
    merged = unary_union([roi.geometry.iloc[0], foot])
    if merged.geom_type != "Polygon":
        merged = max(merged.geoms, key=lambda g: g.area)
    gpd.GeoDataFrame({"name": ["munich-vsf"]}, geometry=[merged],
                     crs=roi.crs).to_file(ROI_OUT, driver="GPKG")
    print(f"ROI {before:.1f} -> {merged.area:.1f} m2 (+{merged.area-before:.1f}); "
          f"wrote {ROI_OUT.name}")


if __name__ == "__main__":
    main()
