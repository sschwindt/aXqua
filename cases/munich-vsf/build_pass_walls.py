"""The pass's far wall, which no footprint method can ever find.

`stahlbeton.stl` draws the far wall of the fish pass as a sheet with NO THICKNESS.
It therefore has no plan area, no horizontal cap and no interior: every method that
derives a structure from a footprint - the column-occupancy test that produced
`walls-drape.gpkg`, the elevation-island finder in `measure_baffle_stations.py`, and
`solid_mode: cut` itself - is structurally incapable of seeing it. The near wall
(0.290 m of concrete) comes through all of them; its far counterpart silently does not.

What that costs is the whole model. With no far wall the pass is open along its entire
length into the ground beside it, so water is never forced through the slots: it leaves
sideways. That is why 2D settled at 3.397 m against 2.845 m wall tops, why the
hydrostatic 3D pre-run retained 105% of its inflow, and why every cross-channel ruler
laid across the pass measured ~1.89 m instead of the 1.165 m clear width - the ruler
crossed the channel and kept going.

So the wall is CONSTRUCTED here rather than extracted. The sheet's own facets give the
line: vertical facets (|nz| small) on the far side of the pass, projected to plan and
ordered along the pass axis. That line is then given a nominal thickness OUTWARD, away
from the channel, so the clear width the slots depend on is unchanged - the wall is
added beside the water, never into it.

    python cases/munich-vsf/build_far_wall.py
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point, Polygon

from measure_baffle_stations import read_ascii_stl, facet_normals

HERE = Path(__file__).resolve().parent
CAD = HERE / "user-sources" / "geodata" / "cad"
OUT = HERE / "user-sources" / "geodata" / "pass-walls.gpkg"

ORIGIN = np.array([7.872073, 42.206112])
BEARING_DEG = 15.543955
S0, S1 = 1.0, 27.0            # the pass, as measure_baffle_stations.py windows it
N_BAND = 0.25                 # how far either side of a face to accept a facet
VERTICAL = 0.30               # |nz| below this is a vertical facet
CREST = 2.845                 # the near wall's measured cap; the far wall matches it
#: (name, n of the INNER face, thickness) - both built OUTWARD, away from the water.
#: The far wall is a zero-thickness sheet so its thickness is nominal; the near wall
#: is 0.290 m of real concrete and is built to that.
WALLS = (("far-wall", -1.475, 0.150), ("near-wall", -0.310, 0.290))


def frame():
    b = np.radians(BEARING_DEG)
    return np.array([np.sin(b), np.cos(b)]), np.array([np.cos(b), -np.sin(b)])


def trace(p, name, n_face):
    """Median offset per 0.1 m of pass, for the facets near one wall face."""
    edges = np.arange(S0, S1 + 0.1, 0.1)
    idx = np.digitize(p[:, 0], edges) - 1
    out = []
    for k in range(len(edges) - 1):
        m = idx == k
        if m.sum() < 3:
            continue
        out.append(((edges[k] + edges[k + 1]) / 2, np.median(p[m, 1])))
    out = np.asarray(out)
    print(f"  {name}: {len(out)} stations, n {out[:,1].min():+.3f}..{out[:,1].max():+.3f} "
          f"(wander {out[:,1].max()-out[:,1].min():.3f} m)")
    return out


def main() -> None:
    t, n = frame()
    buckets = {w[0]: [] for w in WALLS}
    for tri in read_ascii_stl(CAD / "stahlbeton.stl"):
        nz = np.abs(facet_normals(tri)[0][:, 2])
        c = tri.mean(axis=1)
        d = c[:, :2] - ORIGIN
        s, off = d @ t, d @ n
        base = (nz < VERTICAL) & (s > S0) & (s < S1)
        for name, n_face, _ in WALLS:
            k = base & (np.abs(off - n_face) < N_BAND)
            if k.any():
                buckets[name].append(np.column_stack([s[k], off[k], c[k, 2]]))

    # Trace both faces FIRST. The water is whatever lies between them, and that is not
    # a constant band: the far wall is a sheet that wanders 0.268 m over the pass, so a
    # fixed-offset corridor both mis-clips the drape and makes the guard below fire on
    # a wall that is correctly placed. The corridor is therefore built from the two
    # traces themselves and written out, so every consumer uses the same one.
    traces = {}
    for name, n_face, _ in WALLS:
        p_ = np.concatenate(buckets[name])
        print(f"{name}: {len(p_):,} vertical facets, z {p_[:,2].min():.2f}..{p_[:,2].max():.2f}")
        traces[name] = trace(p_, name, n_face)

    ss = np.intersect1d(np.round(traces["far-wall"][:, 0], 3),
                        np.round(traces["near-wall"][:, 0], 3))
    fa = np.interp(ss, traces["far-wall"][:, 0], traces["far-wall"][:, 1])
    ne = np.interp(ss, traces["near-wall"][:, 0], traces["near-wall"][:, 1])
    width = ne - fa
    print(f"\nclear width between the traced faces: {width.min():.3f}..{width.max():.3f} m, "
          f"median {np.median(width):.3f} m  (design 1.165 m)")
    left = ORIGIN + ss[:, None] * t + fa[:, None] * n
    right = ORIGIN + ss[:, None] * t + ne[:, None] * n
    corridor = Polygon(np.vstack([left, right[::-1]]))
    print(f"corridor {corridor.area:.2f} m2")

    recs = []
    for name, n_face, thick in WALLS:
        tr = traces[name]
        xy = ORIGIN + tr[:, :1] * t + tr[:, 1:2] * n
        line = LineString(xy)
        # Which side `single_sided` puts the material on depends on the traced line's
        # direction, not on the sign one expects. So build BOTH and keep the one that
        # stays out of the water; refuse if neither does. Choosing by test rather than
        # by convention is what stops a wall being silently built into the channel,
        # which would narrow the slots and still look like a clean build.
        cands = [line.buffer(sgn * thick, single_sided=True, cap_style=2)
                 for sgn in (+1.0, -1.0)]
        overlaps = [c.intersection(corridor).area for c in cands]
        k = int(np.argmin(overlaps))
        wall, bad = cands[k], overlaps[k]
        print(f"  {name}: area {wall.area:.2f} m2, length {line.length:.2f} m, "
              f"side {'+' if k == 0 else '-'}, overlap with the water {bad:.4f} m2 "
              f"(other side would be {overlaps[1-k]:.2f})")
        if wall.is_empty or bad > 0.02:
            raise SystemExit(f"REFUSING: {name} cannot be built clear of the channel")
        recs.append({"Name": name, "Type": "wall", "Crest (m)": CREST, "geometry": wall})

    gpd.GeoDataFrame([{"Name": "clear-channel", "geometry": corridor}],
                     geometry="geometry").to_file(
        HERE / "user-sources" / "geodata" / "clear-channel.gpkg", driver="GPKG")

    gpd.GeoDataFrame(recs, geometry="geometry").to_file(OUT, driver="GPKG")
    print(f"\nwrote {OUT.name}: {len(recs)} walls, "
          f"{sum(r['geometry'].area for r in recs):.2f} m2")


if __name__ == "__main__":
    main()
