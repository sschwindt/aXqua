"""The acceptance gate: the realised slot at all 14 baffles of the built mesh.

lww-134's criterion, and the one that decides whether anything downstream is worth
running. Measured as the shortest distance between DISTINCT raised regions - a
perpendicular ray cannot see a diagonal slot, which cost three wrong answers.

    drawn      0.1697 m   (the drawing, measure_slot_from_dxf.py)
    footprints 0.1342 m   (what wall_footprints delivers at surfaces.resolution 0.02)
    at dx 0.035 with `touch`: 3 of 14 CLOSED, the rest 32-66%

    python cases/munich-vsf/measure_slots.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

DRAWN = 0.1697
FOOTPRINT = 0.1342


def main() -> None:
    import shapely
    from scipy import ndimage

    from axqua.config import load_config
    from axqua.core import selafin

    cfg = load_config(Path(__file__).resolve().parent / "case-config.yml")
    geo = selafin.read_slf(cfg.model_path(cfg.geometry_slf))
    x = np.asarray(geo["x"], float)
    y = np.asarray(geo["y"], float)
    z = np.asarray(geo["values"]["BOTTOM"], float)
    tri = np.asarray(geo["ikle"])
    tri = tri - 1 if tri.min() == 1 else tri
    print(f"mesh: {len(x):,} nodes, {len(tri):,} elements")

    # A node is raised when the BUILT bed stands above the DEM the mesh was
    # interpolated from. Exact, and it needs no assumption about the terrain: the
    # structures are the only thing that lifts a node off the DEM.
    import rasterio

    with rasterio.open(cfg.geodata.dem_initial) as src:
        dem = np.array([v[0] for v in src.sample(list(zip(x, y)))], dtype=float)
        nodata = src.nodata
    good = np.isfinite(dem) & (dem != nodata)
    raised = good & (z > dem + 0.10)
    print(f"DEM sampled at {good.sum():,} of {len(x):,} nodes; "
          f"raised above it: {raised.sum():,}")
    blocked = raised[tri].all(axis=1)
    print(f"raised nodes {raised.sum():,}; blocked elements {blocked.sum():,}")

    # --- the narrowest constriction, without needing separable footprints ------ #
    # Distance-between-regions is the natural measure and it does not work here: these
    # footprints are merged CAD parts, so the raised set comes out as hundreds of
    # fragments and the nearest pair is two bits of the same wall. Instead rasterise
    # the OPEN area and take its distance transform - twice the largest inscribed
    # radius at a station is the width of the opening there, and the minimum along the
    # pass is the slot. It sees a diagonal slot, which a ray across the channel
    # cannot, and it needs no assumption about which fragment belongs to which baffle.

    res = 0.01
    corridor = 1.5                     # [m] either side of the pass axis
    import geopandas as gpd

    axis = gpd.read_file(cfg.geodata.channel_centerline).geometry.iloc[0]
    band = axis.buffer(corridor)
    minx, miny, maxx, maxy = band.bounds
    nx = int((maxx - minx) / res) + 2
    ny = int((maxy - miny) / res) + 2

    open_cells = shapely.polygons(np.column_stack([x, y])[tri[~blocked]])
    from rasterio import features, transform as rtransform

    tr = rtransform.from_origin(minx, maxy, res, res)
    grid = features.rasterize(
        [(g, 1) for g in open_cells if g.intersects(band)],
        out_shape=(ny, nx), transform=tr, all_touched=False).astype(bool)
    inside = features.rasterize([(band, 1)], out_shape=(ny, nx),
                                transform=tr).astype(bool)
    grid &= inside
    dist = ndimage.distance_transform_edt(grid) * res

    print(f"\nopen corridor rasterised at {res:g} m: {grid.sum():,} cells "
          f"({grid.sum() * res * res:.1f} m2)")
    widths = []
    for d0 in np.arange(0, axis.length, 0.25):
        pt = axis.interpolate(d0)
        col = int((pt.x - minx) / res)
        row = int((maxy - pt.y) / res)
        w = 6                               # +/- 6 cm window on the axis
        patch = dist[max(0, row - w):row + w, max(0, col - w):col + w]
        if patch.size:
            widths.append((d0, 2.0 * float(patch.max())))
    if widths:
        arr = np.array(widths)
        narrow = arr[np.argsort(arr[:, 1])][:12]
        print("\nnarrowest openings along the pass axis [station m, width m]:")
        for d0, w in narrow:
            print(f"   s {d0:6.2f}   {w:.4f} m"
                  + ("   CLOSED" if w < 1e-6 else
                     f"   {100 * w / FOOTPRINT:.0f}% of the {FOOTPRINT:.4f} footprint"))
        print(f"\nminimum along the pass: {arr[:, 1].min():.4f} m; "
              f"median {np.median(arr[:, 1]):.4f} m")


if __name__ == "__main__":
    main()
