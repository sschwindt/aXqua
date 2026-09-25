"""The fourteen baffle stations, measured in the STL frame the case actually runs in.

`measure_slot_from_dxf.py` settles the pass's plan dimensions against the drawing, but
the drawing is in DHDN / GK zone 4 and the case runs in LOCAL CAD metres, and the two
cannot be matched by bounding box (see `README.md`, "Georeferencing is unresolved").
So a number measured on the drawing cannot be pointed at a *place* in the STLs. This
measures the same features in the STLs themselves, so the answer is usable by anything
that reads the CAD: a slice at a baffle's own invert, a slot breakline, a refinement
box, a probe line.

**The pass is in `stahlbeton.stl`.** Both the baffles and the slot blocks opposite them
are concrete and live in that one part. `stahlblech.stl` is something else entirely: a
single 24 m sheet-steel wall standing 2 to 3.3 m off the pass centreline on the OTHER
side, the outer wall of the parallel channel. It holds no baffle and no slot block, so
a slot sought inside its bounding box measures the parallel channel (~1.2 m) and never
finds one.

**How the fourteen are found.** Two facts about this CAD rule out the obvious routes:

* the far wall of the pass is drawn as a sheet with NO thickness, so it has no plan
  area and no horizontal cap - it cannot be found by continuity the way the 0.29 m
  near wall can, and it is not even parallel (it wanders 0.035 m over the 24 m);
* `stahlbeton` also carries the pass invert in places, so anything that merely asks
  "is there concrete here" merges the baffles, the blocks and the floor into one body.

What separates a baffle from all of that is elevation, not plan position: a baffle is
concrete standing **well above its own local bed and well below the wall tops**. That
is the test used here, against the bed from `dem-from-surfaces.tif`. It leaves 28
islands - 14 baffles and the 14 slot blocks - and a baffle is the island that reaches
the near wall's inner face, a block the one that does not.

The raster only says where to look. Each island's outline is then rebuilt from the
STL's own horizontal facets, and the slot is `baffle.distance(block)` on those exact
polygons: the shortest line between the two solids, which runs diagonally, which is
why no ray cast across the channel ever finds it.

    python cases/munich-vsf/measure_baffle_stations.py
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CAD = HERE / "user-sources" / "geodata" / "cad"
CENTERLINE = HERE / "axqua-case" / "preprocessing" / "channel-centerline.gpkg"
DEM = HERE / "axqua-case" / "preprocessing" / "dem-from-surfaces.tif"
OUT = HERE / "user-sources" / "geodata" / "baffle-stations.csv"
OUT_GPKG = HERE / "user-sources" / "geodata" / "baffle-footprints.gpkg"
OUT_GPKG_MESH = HERE / "user-sources" / "geodata" / "baffle-footprints-mesh.gpkg"

PART = "stahlbeton.stl"
RES = 0.005                      #: [m] raster step; it locates islands, never sizes them
#: (s0, s1, n0, n1) the pass itself: generous along it, but stopping at n = 0, the
#: OUTER face of its near wall. Past that lie a drainage channel and the lean-concrete
#: apron, whose edges run the length of the reach too.
WINDOW = (1.0, 27.0, -1.9, 0.0)
CONTINUOUS = 0.90                #: an n-row this unbroken over its own span is a wall
FLAT = 0.90                      #: |nz| above this is a horizontal facet, and those are
                                 #: what carry a plan outline (a vertical one is a line)
MIN_RISE = 0.25                  #: [m] a baffle stands about a metre above its pool;
                                 #: the invert's own tessellation noise is millimetres
BELOW_TOP = 0.05                 #: [m] clear of the 2.845 m wall tops, which are the
                                 #: only other horizontal concrete in the channel
MIN_ISLAND = 0.01                #: [m2] below this is tessellation speckle
#: [m] Douglas-Peucker tolerance for the meshing copy of the outlines. gmsh puts a
#: node on every vertex, so the boundary sets a floor on element size; 10 mm is well
#: under the 0.1697 m slot and, because DP keeps corners and the slot is measured
#: corner to corner, provably does not move it. Verified on write, not assumed.
MESH_SIMPLIFY = 0.010


# --------------------------------------------------------------------------- #
# the pass frame
# --------------------------------------------------------------------------- #
def pass_frame():
    """Origin and unit vectors of the pass-aligned frame, from the centerline."""
    import geopandas as gpd

    line = gpd.read_file(CENTERLINE).geometry.iloc[0]
    coords = np.asarray(line.coords)[:, :2]
    origin = coords[0]
    d = coords[-1] - coords[0]
    bearing = float(np.degrees(np.arctan2(d[0], d[1])))
    return frame_at(origin, bearing)


def frame_at(origin, bearing_deg):
    rad = np.radians(bearing_deg)
    return (origin, np.array([np.sin(rad), np.cos(rad)]),
            np.array([np.cos(rad), -np.sin(rad)]), bearing_deg)


def to_sn(xy, origin, along, across):
    d = np.asarray(xy, dtype=float) - origin
    return np.column_stack([d @ along, d @ across])


def to_xy(sn, origin, along, across):
    sn = np.atleast_2d(np.asarray(sn, dtype=float))
    return origin + sn[:, :1] * along + sn[:, 1:2] * across


def in_frame(tri_xyz, frame):
    """Re-express facets given in local CAD (x, y, z) as (s, n, z)."""
    origin, along, across, _ = frame
    out = np.empty_like(tri_xyz)
    for k in range(3):
        out[:, k, :2] = to_sn(tri_xyz[:, k, :2], origin, along, across)
        out[:, k, 2] = tri_xyz[:, k, 2]
    return out


# --------------------------------------------------------------------------- #
# the STL
# --------------------------------------------------------------------------- #
def read_ascii_stl(path, chunk=600_000):
    """Yield (m, 3, 3) blocks of facet vertices. The Munich parts are ASCII and
    `stahlbeton.stl` is 299 MB, so it is read in blocks rather than held twice."""
    buf = []
    with open(path) as fh:
        for line in fh:
            if line.lstrip().startswith("vertex "):
                buf.append(line.split()[1:4])
                if len(buf) >= chunk * 3:
                    yield np.asarray(buf, dtype=float).reshape(-1, 3, 3)
                    buf = []
    if buf:
        yield np.asarray(buf, dtype=float).reshape(-1, 3, 3)


def facet_normals(tri):
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.maximum(length, 1e-30), 0.5 * length[:, 0]


def collect(path, frame, window, pad=0.5):
    """Every facet of *path* OVERLAPPING the pass window, kept in local (x, y, z).

    Overlap, not centroid: a flat top is tessellated into a few long strips, and the
    24 m strip capping a wall has its centroid outside any window tight enough to be
    useful. Dropping it loses the wall the measurement is referenced to.
    """
    s0, s1, n0, n1 = window
    kept = []
    for tri in read_ascii_stl(path):
        sn = in_frame(tri, frame)
        overlap = ((sn[:, :, 0].max(axis=1) > s0 - pad)
                   & (sn[:, :, 0].min(axis=1) < s1 + pad)
                   & (sn[:, :, 1].max(axis=1) > n0 - pad)
                   & (sn[:, :, 1].min(axis=1) < n1 + pad))
        if overlap.any():
            kept.append(tri[overlap])
    return np.concatenate(kept) if kept else np.empty((0, 3, 3))


# --------------------------------------------------------------------------- #
# rasterising
# --------------------------------------------------------------------------- #
def _cells(p, window, res, shape):
    j = ((p[:, 0] - window[0]) / res).astype(int)
    i = ((p[:, 1] - window[2]) / res).astype(int)
    ok = (j >= 0) & (j < shape[1]) & (i >= 0) & (i < shape[0])
    return i[ok], j[ok], ok


def top_surface(tri, window, res, rng, budget=4_000_000):
    """Highest facet point per plan cell, by area-proportional barycentric sampling."""
    s0, s1, n0, n1 = window
    shape = (int((n1 - n0) / res), int((s1 - s0) / res))
    top = np.full(shape, -np.inf, dtype=np.float32)
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    # A density, not a per-facet cap: the long strips capping a wall need millions of
    # samples between them, and a cap would leave those walls dotted, not solid.
    k = np.maximum(np.ceil(6 * area / (res * res)).astype(int), 3)
    order = np.argsort(-k)
    batch, total = [], 0
    for t in list(order) + [None]:
        if t is not None and total + k[t] <= budget:
            batch.append(t)
            total += k[t]
            continue
        if batch:
            idx = np.repeat(np.asarray(batch), k[batch])
            r1 = np.sqrt(rng.random(len(idx)))
            r2 = rng.random(len(idx))
            p = (a[idx] * (1 - r1)[:, None] + b[idx] * (r1 * (1 - r2))[:, None]
                 + c[idx] * (r1 * r2)[:, None])
            i, j, ok = _cells(p, window, res, shape)
            np.maximum.at(top, (i, j), p[ok, 2])
        if t is None:
            break
        batch, total = [t], int(k[t])
    # Facet EDGES as well: a vertical face has no plan area, and the pass's far wall
    # is a sheet with no thickness at all - without this it is a dotted line.
    for e in range(3):
        u, v = tri[:, e], tri[:, (e + 1) % 3]
        d = np.linalg.norm(v[:, :2] - u[:, :2], axis=1)
        steps = np.clip(np.ceil(d / (0.5 * res)).astype(int), 1, 40_000)
        for lo in range(0, len(tri), 50_000):
            sl = slice(lo, min(lo + 50_000, len(tri)))
            idx = np.repeat(np.arange(lo, sl.stop), steps[sl])
            t01 = np.concatenate([np.linspace(0, 1, m) for m in steps[sl]])
            p = u[idx] + (v[idx] - u[idx]) * t01[:, None]
            i, j, ok = _cells(p, window, res, shape)
            np.maximum.at(top, (i, j), p[ok, 2])
    return top


def near_wall(top, window, res):
    """The n-band that runs the length of the pass at its highest elevation.

    The near wall is 0.29 m of concrete with a flat 2.845 m cap and is found by how
    continuous it is over its own s-span. Its FAR counterpart is not found this way
    and is not looked for: drawn with no thickness, it is neither continuous in the
    raster nor parallel to this one.
    """
    solid = np.isfinite(top)
    n0 = window[2]
    rows = []
    for i in range(solid.shape[0]):
        jj = np.flatnonzero(solid[i])
        if jj.size < 2:
            continue
        span = (jj.max() - jj.min() + 1) * res
        dense = jj.size / (jj.max() - jj.min() + 1)
        if span > 0.5 * (window[1] - window[0]) and dense > CONTINUOUS:
            rows.append(i)
    if not rows:
        raise SystemExit("no continuous wall in the window - check WINDOW")
    rows = np.asarray(rows)
    breaks = np.flatnonzero(np.diff(rows) > 1)
    starts = np.concatenate([[0], breaks + 1])
    ends = np.concatenate([breaks + 1, [len(rows)]])
    bands = [(n0 + rows[i] * res, n0 + (rows[j - 1] + 1) * res)
             for i, j in zip(starts, ends)]
    band = max(bands, key=lambda b: b[1] - b[0])
    ii = slice(int(round((band[0] - n0) / res)), int(round((band[1] - n0) / res)))
    return band, float(np.nanmax(np.where(np.isfinite(top[ii]), top[ii], np.nan)))


def invert_line(bed, band, window, res):
    """The pass invert per station: the median bed across the channel.

    Per cell rather than per station is the obvious thing and it is wrong here.
    `dem-from-surfaces.tif` has a hole right under the fourteenth slot block, so a
    per-cell test drops that block silently and the pass comes back with thirteen
    slots. The invert is flat across the channel at any station, so a median over
    the channel is both more robust and no less true; stations with no bed at all are
    interpolated along the pass.
    """
    rows = slice(0, int(round((band[0] - window[2]) / res)))
    with np.errstate(all="ignore"):
        line = np.nanmedian(bed[rows], axis=0)
    good = np.isfinite(line)
    if not good.any():
        raise SystemExit(f"no bed at all under the pass - is {DEM.name} built?")
    j = np.arange(line.size)
    return np.interp(j, j[good], line[good]), float(good.mean())


def midchannel(top, band, window, res, wall_top):
    """Half way between the two wall caps, per station.

    Used to say which wall an island belongs to. Adjacency would be the obvious
    test and it fails: the near wall OVERHANGS its own base by up to 0.04 m, so a
    baffle's cap stops that far short of the cap above it and reads as detached.
    Which half of the channel the island occupies does not care about the overhang,
    and the margin is half a metre rather than a couple of cells.
    """
    cap = np.isfinite(top) & (top > wall_top - 0.01)
    inner = int(round((band[0] - window[2]) / res))
    far = np.full(top.shape[1], np.nan)
    near = np.full(top.shape[1], np.nan)
    below, above = cap[:inner], cap[inner:]
    has = below.any(axis=0)
    far[has] = window[2] + np.argmax(below[:, has], axis=0) * res
    has = above.any(axis=0)
    near[has] = window[2] + (inner + np.argmax(above[:, has], axis=0)) * res
    j = np.arange(top.shape[1])
    out = []
    for line in (far, near):
        good = np.isfinite(line)
        if not good.any():
            raise SystemExit("a side wall has no cap at all - check WINDOW")
        out.append(np.interp(j, j[good], line[good]))
    return (out[0] + out[1]) / 2, out[0], out[1]


def islands(top, bed_line, window, res, wall_top):
    """Label concrete standing above its own bed and below the wall tops."""
    from scipy import ndimage

    rise = top - bed_line[None, :]
    mask = np.isfinite(top) & (rise > MIN_RISE) & (top < wall_top - BELOW_TOP)
    lab, count = ndimage.label(mask, structure=np.ones((3, 3), int))
    out = []
    for k in range(1, count + 1):
        ii, jj = np.nonzero(lab == k)
        if ii.size * res * res < MIN_ISLAND:
            continue
        out.append({
            "s0": window[0] + jj.min() * res,
            "s1": window[0] + (jj.max() + 1) * res,
            "n0": window[2] + ii.min() * res,
            "n1": window[2] + (ii.max() + 1) * res,
            "area": ii.size * res * res,
            "top": float(np.nanmax(top[ii, jj])),
            "col": int(round((jj.min() + jj.max()) / 2)),
        })
    out.sort(key=lambda p: p["s0"])
    return out


# --------------------------------------------------------------------------- #
# exact plan polygons
# --------------------------------------------------------------------------- #
def plan_polygon(tri, nz, island, pad=0.03, slab=0.10):
    """The island's exact outline, from the horizontal facets that cap it.

    These solids are prisms with vertical sides, so the cap outline IS the plan
    outline. Taking the cap rather than everything in the box keeps the pass invert,
    which `stahlbeton` also carries, out of the answer.
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    c = tri.mean(axis=1)
    sel = ((np.abs(nz) > FLAT)
           & (c[:, 0] > island["s0"] - pad) & (c[:, 0] < island["s1"] + pad)
           & (c[:, 1] > island["n0"] - pad) & (c[:, 1] < island["n1"] + pad)
           & (c[:, 2] > island["top"] - slab))
    polys = [Polygon(t[:, :2]) for t in tri[sel]]
    polys = [p for p in polys if p.is_valid and p.area > 0]
    if not polys:
        return None
    merged = unary_union(polys)
    if merged.geom_type == "MultiPolygon":
        merged = max(merged.geoms, key=lambda p: p.area)
    return merged.simplify(1e-6)


def write_footprints(shapes, frame):
    """The 28 outlines in local CAD metres, at the CAD's own precision.

    NO crs is stamped. This case runs in local CAD metres, which are not a
    projection: `crs_epsg: 31468` describes the DRAWING, and labelling a local-metre
    layer with any EPSG makes the next reader reproject it into the void.
    """
    import geopandas as gpd
    from shapely.geometry import Polygon

    origin, along, across, _ = frame
    recs, geoms = [], []
    for k, kind, poly in shapes:
        ring = to_xy(np.asarray(poly.exterior.coords), origin, along, across)
        recs.append({"baffle": k, "kind": kind, "area_m2": round(poly.area, 5)})
        geoms.append(Polygon(ring))
    gpd.GeoDataFrame(recs, geometry=geoms, crs=None).to_file(OUT_GPKG, driver="GPKG")

    # A second, DECIMATED copy, for cutting holes in a mesh. gmsh puts a node on
    # every polygon vertex, so a mesh can be no coarser than the boundary it is cut
    # with, and an outline carrying hundreds of short segments produces slivers and
    # a diverging solve. Douglas-Peucker keeps corners, and the slot is a
    # corner-to-corner distance, so decimating does not move it - but that is
    # asserted here rather than assumed, and the exact copy above is kept for
    # measurement.
    thin, worst = [], None
    for (k, kind, poly), geom in zip(shapes, geoms):
        thin.append(geom.simplify(MESH_SIMPLIFY))
    for k in sorted({s[0] for s in shapes}):
        pair = [t for (kk, _, _), t in zip(shapes, thin) if kk == k]
        if len(pair) == 2:
            gap = pair[0].distance(pair[1])
            worst = gap if worst is None else min(worst, gap)
    gpd.GeoDataFrame(recs, geometry=thin, crs=None).to_file(
        OUT_GPKG_MESH, driver="GPKG")
    before = sum(len(g.exterior.coords) for g in geoms)
    after = sum(len(g.exterior.coords) for g in thin)
    print(f"decimated at {1000 * MESH_SIMPLIFY:.0f} mm for meshing: "
          f"{before:,} -> {after:,} vertices, narrowest slot still "
          f"{worst:.4f} m")


# --------------------------------------------------------------------------- #
def main() -> None:
    import rasterio
    from shapely.ops import nearest_points

    frame = pass_frame()
    origin, along, across, bearing = frame
    print(f"pass frame: origin ({origin[0]:.3f}, {origin[1]:.3f}) local m, "
          f"bearing {bearing:.4f} deg off +y")

    tri_xyz = collect(CAD / PART, frame, WINDOW)
    tri = in_frame(tri_xyz, frame)
    print(f"{PART}: {len(tri):,} facets over the pass")
    nrm, _ = facet_normals(tri)

    top = top_surface(tri, WINDOW, RES, np.random.default_rng(0))
    top[~np.isfinite(top)] = np.nan
    band, wall_top = near_wall(top, WINDOW, RES)
    print(f"near wall: n {band[0]:+.3f}..{band[1]:+.3f} "
          f"({band[1] - band[0]:.3f} m thick), cap at z = {wall_top:.3f} m")

    with rasterio.open(DEM) as src:
        ns, nn = top.shape[1], top.shape[0]
        gs, gn = np.meshgrid(WINDOW[0] + (np.arange(ns) + 0.5) * RES,
                             WINDOW[2] + (np.arange(nn) + 0.5) * RES)
        xy = to_xy(np.column_stack([gs.ravel(), gn.ravel()]), origin, along, across)
        bed = np.array([v[0] for v in src.sample(list(map(tuple, xy)))], dtype=float)
        bed[bed == src.nodata] = np.nan
    bed = bed.reshape(top.shape)
    bed_line, covered = invert_line(bed, band, WINDOW, RES)
    print(f"invert from {DEM.name}: {100 * covered:.0f}% of stations carry bed, "
          f"the rest interpolated along the pass")

    mid, far_face, near_face = midchannel(top, band, WINDOW, RES, wall_top)
    # Medians: the far wall is a sheet with no thickness, so its cap is missing at
    # some stations and the per-station reading is noisy there. It only has to be
    # good enough to say which half of the channel an island is in.
    print(f"channel: far wall n {np.median(far_face):+.3f}, near wall inner face "
          f"n {np.median(near_face):+.3f}, clear width "
          f"{np.median(near_face - far_face):.3f} m")

    found = islands(top, bed_line, WINDOW, RES, wall_top)
    baffles = [p for p in found if p["n1"] > mid[p["col"]]]
    blocks = [p for p in found if p["n1"] <= mid[p["col"]]]
    print(f"{len(found)} islands standing >{MIN_RISE} m above the invert: "
          f"{len(baffles)} reach the near wall (baffles), "
          f"{len(blocks)} do not (slot blocks)")

    rows, shapes = [], []
    for k, baffle in enumerate(baffles, start=1):
        later = [q for q in blocks if q["s0"] > baffle["s0"]]
        if not later:
            print(f"  baffle {k} at s {baffle['s0']:.2f}: no block downstream, skipped")
            continue
        block = min(later, key=lambda q: q["s0"])
        pb = plan_polygon(tri, nrm[:, 2], baffle)
        pk = plan_polygon(tri, nrm[:, 2], block)
        if pb is None or pk is None:
            print(f"  baffle {k} at s {baffle['s0']:.2f}: no cap facets, skipped")
            continue
        slot = pb.distance(pk)
        shapes.append((k, "baffle", pb))
        shapes.append((k, "slot_block", pk))
        qa, qb = nearest_points(pb, pk)
        mid = ((qa.x + qb.x) / 2, (qa.y + qb.y) / 2)
        tip_xy = to_xy((qa.x, qa.y), origin, along, across)[0]
        blk_xy = to_xy((qb.x, qb.y), origin, along, across)[0]
        mid_xy = to_xy(mid, origin, along, across)[0]

        j = int((mid[0] - WINDOW[0]) / RES)
        rows.append({
            "baffle": k,
            "s": round(float(mid[0]), 4),
            "slot_x": round(float(mid_xy[0]), 4),
            "slot_y": round(float(mid_xy[1]), 4),
            "baffle_tip_x": round(float(tip_xy[0]), 4),
            "baffle_tip_y": round(float(tip_xy[1]), 4),
            "block_corner_x": round(float(blk_xy[0]), 4),
            "block_corner_y": round(float(blk_xy[1]), 4),
            "slot_m": round(float(slot), 4),
            "invert_z": round(float(bed_line[j]), 4),
            "baffle_top_z": round(float(baffle["top"]), 4),
            "block_top_z": round(float(block["top"]), 4),
            "baffle_area_m2": round(float(baffle["area"]), 4),
        })

    print(f"\n{'#':>2} {'s':>7} {'x':>9} {'y':>9} {'slot':>7} {'invert':>7} "
          f"{'b_top':>7} {'k_top':>7}")
    for r in rows:
        print(f"{r['baffle']:>2} {r['s']:7.3f} {r['slot_x']:9.4f} "
              f"{r['slot_y']:9.4f} {r['slot_m']:7.4f} {r['invert_z']:7.3f} "
              f"{r['baffle_top_z']:7.3f} {r['block_top_z']:7.3f}")

    if not rows:
        raise SystemExit("no baffle/block pairs found")
    t = np.array([r["slot_m"] for r in rows])
    s = np.array([r["s"] for r in rows])
    print(f"\nSLOT over {t.size} baffles: mean {t.mean():.4f} m, sd {t.std():.4f}, "
          f"min {t.min():.4f}, max {t.max():.4f}")
    print(f"pitch: median {np.median(np.diff(s)):.4f} m over "
          f"{s.max() - s.min():.3f} m of pass")
    inv = np.array([r["invert_z"] for r in rows])
    print(f"invert: {inv.max():.3f} m down to {inv.min():.3f} m, "
          f"median drop {abs(np.median(np.diff(inv))):.4f} m per pool")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    write_footprints(shapes, frame)
    print(f"\nwrote {OUT}")
    print(f"wrote {OUT_GPKG}       (exact, for measuring)")
    print(f"wrote {OUT_GPKG_MESH}  (decimated, for cutting)")
    print(f"pass frame, to reproduce s and n: origin "
          f"({origin[0]:.6f}, {origin[1]:.6f}) local m, bearing {bearing:.6f} deg "
          f"off +y; s = (p - origin) . (sin b, cos b), n = (p - origin) . (cos b, -sin b)")


if __name__ == "__main__":
    main()
