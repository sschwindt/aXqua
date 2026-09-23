"""The slot, measured off the reference model's own mesh - a third, independent source.

The 0.1697 m slot now rests on two sources that are not independent of each other:
the contractor DXF (`measure_slot_from_dxf.py`) and the STL assembly exported from
that same CAD (`measure_baffle_stations.py`). `federica-wetted-bed.csv` is no help -
it is a 0.25 m bed grid and cannot resolve a 0.17 m opening.

The mesh behind that CSV can. `/home/modelling/OpenFOAM/Munich-VSF/6_v5_HQ100` was
meshed by snapping onto the CAD surfaces by someone else, with their own tooling and
their own judgement about which surface is which, and its `Stahlbeton_refinement`
patch carries the baffles and the slot blocks at ~0.02 m face size. So it is a
genuinely separate reading of the same structure, and it is the only one available.

**What it can and cannot settle.** It measures the built mesh, so it answers "did an
independent modeller's mesh realise the same opening", not "what did the designer
draw". A disagreement would mean one of the two lost the geometry, not which.

Nothing here is read through OpenFOAM: the patch's faces are pulled straight out of
`constant/polyMesh` by index, so no environment has to be sourced and no case has to
be valid.

    python cases/munich-vsf/measure_slot_from_polymesh.py
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MESH = Path("/home/modelling/OpenFOAM/Munich-VSF/6_v5_HQ100/constant/polyMesh")
STATIONS = HERE / "user-sources" / "geodata" / "baffle-stations.csv"
FOOTPRINTS = HERE / "user-sources" / "geodata" / "baffle-footprints.gpkg"

#: The patch the baffles and slot blocks live on. `Stahlbeton_right` is the far side
#: wall and `Stahlblech` the parallel channel's, neither of which bounds a slot.
PATCH = "Stahlbeton_refinement"
HALF = 0.55                      #: [m] how far either side of a station to look
CLEAR = 0.08                     #: [m] above the invert, clear of the fillet at the toe
BELOW = 0.10                     #: [m] below the baffle top, clear of its own cap
#: [m] how far a mesh face may sit from the CAD outline and still be counted as that
#: solid's surface. The CAD is used ONLY to say which faces belong to which of the two
#: solids; the gap reported is between the mesh's own faces. Splitting instead at the
#: channel midline does not work - this patch carries the side walls and the invert
#: too, so the midline of whatever falls in the window is not between baffle and block.
ATTACH = 0.08


def read_points(path):
    """The `points` vector list, as (n, 3).

    Line-based rather than one big `fromstring`: the file closes with `)` and then
    OpenFOAM's `// ***` footer, which a whole-buffer parse chokes on.
    """
    out = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("("):
                break                       # the opening paren of the list
        for line in fh:
            if not line.startswith("("):
                break                       # the closing paren, then the footer
            out.append(line[1:line.rindex(")")].split())
    return np.asarray(out, dtype=float)


def read_patch(mesh, name):
    """Face-vertex index lists for one boundary patch, by (startFace, nFaces)."""
    bnd = (mesh / "boundary").read_text()
    m = re.search(rf"\b{re.escape(name)}\b\s*\{{(.*?)\}}", bnd, re.S)
    if not m:
        raise SystemExit(f"no patch {name} in {mesh / 'boundary'}")
    block = m.group(1)
    n = int(re.search(r"nFaces\s+(\d+)", block).group(1))
    start = int(re.search(r"startFace\s+(\d+)", block).group(1))

    faces = []
    with open(mesh / "faces") as fh:
        for line in fh:
            if line.startswith("("):
                break
        i = 0
        for line in fh:
            if i >= start + n:
                break
            if "(" not in line:
                continue
            if i >= start:
                faces.append([int(v) for v in
                              line[line.index("(") + 1: line.rindex(")")].split()])
            i += 1
    return faces, start, n


def face_centres(faces, points):
    return np.array([points[f].mean(axis=0) for f in faces])


def hull(points_2d):
    from shapely.geometry import MultiPoint

    return MultiPoint([tuple(p) for p in points_2d]).convex_hull


def main() -> None:
    import pandas as pd

    if not MESH.is_dir():
        raise SystemExit(f"reference mesh not on this machine: {MESH}")
    if not STATIONS.is_file():
        raise SystemExit(f"{STATIONS.name} not built - run "
                         "measure_baffle_stations.py first")

    st = pd.read_csv(STATIONS)
    # The pass frame that measure_baffle_stations.py reports; the slot is diagonal,
    # so both measurements have to be made in the same frame to be comparable.
    origin = np.array([7.872073, 42.206112])
    bearing = np.radians(15.543955)
    along = np.array([np.sin(bearing), np.cos(bearing)])
    across = np.array([np.cos(bearing), -np.sin(bearing)])

    print(f"reading {MESH}")
    points = read_points(MESH / "points")
    faces, start, n = read_patch(MESH, PATCH)
    cen = face_centres(faces, points)
    print(f"  {len(points):,} points, patch {PATCH}: {n:,} faces "
          f"from {start:,}")
    print(f"  patch bounds x {cen[:, 0].min():.2f}..{cen[:, 0].max():.2f}  "
          f"y {cen[:, 1].min():.2f}..{cen[:, 1].max():.2f}  "
          f"z {cen[:, 2].min():.2f}..{cen[:, 2].max():.2f}")

    d = cen[:, :2] - origin
    s = d @ along
    nn = d @ across
    z = cen[:, 2]

    import geopandas as gpd
    from shapely.geometry import Point

    if not FOOTPRINTS.is_file():
        raise SystemExit(f"{FOOTPRINTS.name} not built - run "
                         "measure_baffle_stations.py first")
    fp = gpd.read_file(FOOTPRINTS)
    solids = {(int(r["baffle"]), r["kind"]): r.geometry for _, r in fp.iterrows()}

    rows = []
    for _, r in st.iterrows():
        k = int(r.baffle)
        lo = r.invert_z + CLEAR
        hi = min(r.baffle_top_z, r.block_top_z) - BELOW
        pb, pk = solids.get((k, "baffle")), solids.get((k, "slot_block"))
        if hi <= lo or pb is None or pk is None:
            rows.append((k, np.nan, 0, 0, np.nan))
            continue
        band = np.flatnonzero((np.abs(s - r.s) < HALF) & (z > lo) & (z < hi))
        sides, offs = [], []
        for poly in (pb, pk):
            keep, off = [], []
            for i in band:
                dist = poly.distance(Point(cen[i, 0], cen[i, 1]))
                if dist < ATTACH:
                    keep.append((s[i], nn[i]))
                    off.append(dist)
            sides.append(keep)
            offs += off
        if min(len(v) for v in sides) < 3:
            rows.append((k, np.nan, len(sides[0]), len(sides[1]), np.nan))
            continue
        gap = hull(np.array(sides[0])).distance(hull(np.array(sides[1])))
        rows.append((k, gap, len(sides[0]), len(sides[1]), float(np.mean(offs))))

    print(f"\n{'#':>2} {'s':>7} {'CAD slot':>9} {'mesh slot':>10} {'diff':>8} "
          f"{'on baffle':>10} {'on block':>9} {'offset':>7}")
    for (k, gap, na, nb, off), (_, r) in zip(rows, st.iterrows()):
        if not np.isfinite(gap):
            print(f"{k:>2} {r.s:7.3f} {r.slot_m:9.4f} {'-':>10} {'-':>8} "
                  f"{na:10d} {nb:9d} {'-':>7}")
            continue
        print(f"{k:>2} {r.s:7.3f} {r.slot_m:9.4f} {gap:10.4f} "
              f"{gap - r.slot_m:+8.4f} {na:10d} {nb:9d} {off:7.4f}")

    g = np.array([v for _, v, _, _, _ in rows if np.isfinite(v)])
    if not g.size:
        raise SystemExit("no station had faces on both solids")
    diffs = g - st.slot_m.to_numpy()[: g.size]
    print(f"\nmesh slot over {g.size} baffles: mean {g.mean():.4f} m, "
          f"sd {g.std():.4f}, min {g.min():.4f}, max {g.max():.4f}")
    print(f"against the CAD:          mean difference {diffs.mean():+.4f} m, "
          f"largest {np.abs(diffs).max():.4f} m")
    print("\nTwo biases, both one-way and both small. A convex hull of face CENTRES "
          "sits\ninside the true surface by about half a face, so the gap reads WIDE "
          "by roughly\n0.02 m at this patch's ~0.02 m face size; and `offset` above "
          "is how far the\nmesh's own faces sit from the CAD outline, which is the "
          "snapping error.")


if __name__ == "__main__":
    main()
