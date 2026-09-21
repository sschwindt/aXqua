"""The acceptance gate: the realised throat at all 14 baffles of the built mesh.

lww-134's criterion, and the one that decides whether anything downstream is worth
running. Measured as the shortest distance between DISTINCT raised regions - a
perpendicular ray cannot see a diagonal throat, which cost three wrong answers.

    drawn      0.1697 m   (the drawing, measure_slot_from_dxf.py)
    footprints 0.1342 m   (what wall_footprints delivers at surfaces.resolution 0.02)
    at dx 0.035 with `touch`: 3 of 14 CLOSED, the rest 32-66%

    python cases/munich-vsf/measure_throats.py
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

    cells = shapely.polygons(np.column_stack([x, y])[tri[blocked]])
    body = shapely.union_all(cells)
    parts = [p for p in (body.geoms if body.geom_type == "MultiPolygon" else [body])
             if p.area > 1e-6]
    print(f"{len(parts)} distinct raised regions")
    del ndimage

    gaps = []
    for i, a in enumerate(parts):
        for b in parts[i + 1:]:
            d = a.distance(b)
            if d < 1.0:                     # neighbours across an opening
                gaps.append(d)
    gaps = np.sort(np.array(gaps))
    if not gaps.size:
        print("no opening found between raised regions - everything is welded")
        return
    print(f"\n{len(gaps)} openings under 1 m; the 20 narrowest [m]:")
    print("  " + "  ".join(f"{g:.4f}" for g in gaps[:20]))
    closed = int((gaps < 1e-6).sum())
    print(f"\nclosed (0 m): {closed}")
    print(f"median {np.median(gaps):.4f} m = {100*np.median(gaps)/DRAWN:.0f}% of drawn "
          f"{DRAWN:.4f}, {100*np.median(gaps)/FOOTPRINT:.0f}% of the footprint "
          f"{FOOTPRINT:.4f}")


if __name__ == "__main__":
    main()
