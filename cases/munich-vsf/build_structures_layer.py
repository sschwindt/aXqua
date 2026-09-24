"""The structures layer the 2D build should use: CAD baffles + the drape's side walls.

`walls-drape.gpkg` is derived by rasterising the CAD and polygonising the result. That
works for a long continuous wall, and fails for the baffles: at the drape's resolution
fourteen 0.15 m cross-walls fuse into the side wall they touch, so the layer carries
seven blobs instead of fourteen baffles plus their side walls. Built through that layer
the pass has no baffles at all over its upper two thirds, which is why the 2D level sat
at 3.397 m against 2.845 m wall tops and why the hydrostatic 3D pre-run retained 105%
of its inflow.

The baffles do not need the drape. `measure_baffle_stations.py` finds them in
`stahlbeton.stl` by elevation (concrete standing above its own local bed and below the
wall tops) and writes 28 closed outlines at CAD precision - 14 baffles and the 14 slot
blocks opposite them - with the slot at 0.1697 m, sd 0.0000, at every one.

So this takes the baffles from the CAD and the *rest* from the drape. The split is by
where a drape feature lies relative to the pass channel: a feature that sits outside it
is a side wall or the weir and is kept, because a merged strip is the correct shape for
those; a feature inside the channel is a fused baffle blob and is dropped, because the
CAD outlines supersede it. Crest elevations for the baffles come from
`baffle-stations.csv` (`baffle_top_z` / `block_top_z`), which are measured per baffle
rather than assumed flat - the pass falls 1.9 m over its length, and so do its walls.
"""
from __future__ import annotations

import csv
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import MultiPoint, Polygon

CASE = Path(__file__).resolve().parent
GEO = CASE / "user-sources" / "geodata"
# the pass frame, as measure_baffle_stations.py reports it
ORIGIN = np.array([7.872073, 42.206112])
BEARING_DEG = 15.543955
N_FAR = -1.475            #: the pass's far wall, in the pass frame
N_NEAR = -0.310           #: the near wall's INNER face; between the two is water
S_LO, S_HI = 0.5, 26.5    #: the pass's own extent along its axis


def _frame():
    b = np.radians(BEARING_DEG)
    return np.array([np.sin(b), np.cos(b)]), np.array([np.cos(b), -np.sin(b)])


def _verts(geom):
    if geom.geom_type == "Polygon":
        return np.asarray(geom.exterior.coords)
    return np.concatenate([np.asarray(g.exterior.coords) for g in geom.geoms])


def main() -> None:
    t, n = _frame()
    stations = {int(r["baffle"]): r
                for r in csv.DictReader((GEO / "baffle-stations.csv").open())}

    baffles = gpd.read_file(GEO / "baffle-footprints.gpkg")
    drape = gpd.read_file(GEO / "walls-drape.gpkg")
    s_max = max(float(r["s"]) for r in stations.values())

    rows = []
    for _, r in baffles.iterrows():
        st = stations[int(r["baffle"])]
        crest = float(st["baffle_top_z"] if r["kind"] == "baffle" else st["block_top_z"])
        rows.append({"Name": f"{r['kind']}-{int(r['baffle']):02d}", "Type": "wall",
                     "Crest (m)": crest, "Source": "cad", "geometry": r.geometry})
    # The far wall, which is a zero-thickness sheet in the CAD and therefore invisible
    # to every footprint-derived method. Constructed by build_far_wall.py; without it
    # the pass is open along its whole length and water leaves sideways instead of
    # through the slots. See that script's docstring for what that cost.
    fw = GEO / "pass-walls.gpkg"
    if fw.exists():
        for _, r in gpd.read_file(fw).iterrows():
            rows.append({"Name": r["Name"], "Type": "wall",
                         "Crest (m)": float(r["Crest (m)"]), "Source": "cad",
                         "geometry": r.geometry})
        print(f"pass walls: {len(gpd.read_file(fw))} added, "
              f"{gpd.read_file(fw).geometry.area.sum():.2f} m2")
    else:
        print("WARNING: pass-walls.gpkg missing -- run build_pass_walls.py first; "
              "without it the pass has no far wall and will not convey through its slots")

    print(f"CAD: {len(rows)} baffle and slot-block outlines, "
          f"crest {min(x['Crest (m)'] for x in rows):.3f}..{max(x['Crest (m)'] for x in rows):.3f} m")

    # The drape calls a cell "wall" where CAD material stands above the local bed, and
    # that test cannot tell a wall from a weir: the overflow weir at the head of the
    # structure IS bed - `Substratum_weir` in the reference - and the DEM already
    # carries it (median difference 0.000 m over its 187 reference points), so water
    # passes over it by elevation. Left in, the drape claims a third of the weir as
    # solid at a fabricated 3.0 m crest, 0.85 m above its real 2.150 m: under `cut`
    # that deletes the overflow path, and under `overflow` it dams it. The flood
    # evacuation channel runs off this weir, so at the high discharges that is the
    # whole point of the channel, it would be the difference between spilling and not.
    weir = MultiPoint([(float(r["x"]), float(r["y"]))
                       for r in csv.DictReader((CASE / "user-sources" / "reference"
                                                / "federica-wetted-bed.csv").open())
                       if r["patch"] == "Substratum_weir"]).buffer(0.125).buffer(0)
    print(f"weir terrain to protect: {weir.area:.2f} m2 (crest ~2.15 m, carried by the DEM)")

    # Inside the clear channel the CAD outlines are the authority; outside it the drape
    # is. So drape features are CLIPPED to outside the channel, not dropped whole.
    #
    # Dropping whole features was wrong and cost the near wall: `wall-004` spans
    # n -1.492..-0.260, i.e. the fused baffle material AND the 0.29 m near wall that
    # bounds the pass. The CAD outlines replace only baffles and slot blocks - they
    # contain no wall - so discarding that feature left the channel open on its near
    # side, exactly the defect the far wall was just built to fix on the other.
    #
    # The band is the water corridor: n from the far wall to the near wall's inner
    # face, over the pass's own s-range. Everything of the drape outside it survives.
    # The corridor is the water between the two TRACED wall faces, written by
    # build_pass_walls.py. It is not a fixed band: the far wall wanders 0.268 m and the
    # near wall 0.236 m over the pass, so a nominal-offset corridor both mis-clips the
    # drape and mis-judges whether a wall sits in the water.
    cc = GEO / "clear-channel.gpkg"
    if not cc.exists():
        raise SystemExit("clear-channel.gpkg missing -- run build_pass_walls.py first")
    corridor = gpd.read_file(cc).geometry.iloc[0]
    print(f"clear channel corridor: {corridor.area:.2f} m2 (from the traced wall faces)")

    kept = 0
    for _, r in drape.iterrows():
        g = r.geometry
        if g is None or g.is_empty:
            continue
        before = g.area
        if g.intersects(weir):
            g = g.difference(weir)
        if g.intersects(corridor):
            g = g.difference(corridor)
        if g.is_empty or g.area < 1e-4:
            print(f"  drop {r['Name']:<10} {before:7.2f} m2 -> nothing outside the "
                  f"corridor and the weir")
            continue
        note = "" if abs(before - g.area) < 1e-4 else f"  (clipped from {before:.2f})"
        print(f"  keep {r['Name']:<10} {g.area:7.2f} m2{note}")
        rows.append({"Name": r["Name"], "Type": r.get("Type", "wall"),
                     "Crest (m)": r.get("Crest (m)", 3.0), "Source": "drape",
                     "geometry": g})
        kept += 1
    dropped = len(drape) - kept

    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=baffles.crs)

    # A baffle reaches the side wall's inner face, so the two outlines meet along a
    # hairline. Left as separate polygons the mesher cuts both and leaves a zero-area
    # sliver between them, which fails the geometry check. They are one piece of
    # concrete, so snap them into one: close gaps below SNAP, then reopen by the same
    # amount so nothing is inflated. SNAP is 5 mm against a 169.7 mm slot, so the
    # opening the whole case turns on cannot be bridged by it.
    SNAP = 0.005
    merged = out.geometry.buffer(SNAP).union_all().buffer(-SNAP)
    pieces = list(getattr(merged, "geoms", [merged]))
    slot_before = float(out.geometry.area.sum())
    print(f"\ndissolve: {len(out)} outlines -> {len(pieces)} connected solid(s), "
          f"area {slot_before:.2f} -> {sum(g.area for g in pieces):.2f} m2")

    # re-attach attributes: a dissolved piece takes the highest crest it swallowed,
    # because a solid is only as passable as its tallest part
    rec = []
    for i, g in enumerate(pieces):
        hits = [r for r in rows if g.intersects(r["geometry"].representative_point())]
        crest = max((float(r["Crest (m)"]) for r in hits), default=3.0)
        src = "cad" if any(r["Source"] == "cad" for r in hits) else "drape"
        rec.append({"Name": f"solid-{i:03d}", "Type": "wall", "Crest (m)": crest,
                    "Source": src, "Parts": len(hits), "geometry": g})
    # Decimate the boundary. gmsh puts a node on EVERY polygon vertex, so the mesh can
    # be no coarser than the outline it cuts: raster-derived drape edges are 5 mm
    # staircases and the dissolve buffers add their own, which left 39,821 of 50,720
    # segments under a millimetre. That produced 0.11 mm elements and 47:1 slivers, and
    # TELEMAC's conjugate-gradient solver diverged on the conditioning (GRACJG relative
    # precision 1e51, then NaN, within seconds of launch).
    #
    # SIMPLIFY is safe for the one dimension that matters: the slot is the distance
    # between two CORNERS, and Douglas-Peucker keeps corners. Measured over all 14
    # baffles, every tolerance from 0 to 0.05 m leaves the slot at 0.1697 m - 100.0% of
    # CAD - so this is chosen against mesh.min_size, not against the slot.
    SIMPLIFY = 0.010
    rec = [{**r, "geometry": r["geometry"].simplify(SIMPLIFY)} for r in rec]
    rec = [r for r in rec if not r["geometry"].is_empty and r["geometry"].is_valid]
    segs = []
    for r in rec:
        g = r["geometry"]
        rings = ([g.exterior] + list(g.interiors) if g.geom_type == "Polygon"
                 else [q.exterior for q in g.geoms])
        for ring in rings:
            q = np.asarray(ring.coords)
            segs.append(np.hypot(*np.diff(q, axis=0).T))
    seg = np.concatenate(segs)
    print(f"simplify {SIMPLIFY} m: {len(seg):,} boundary segments, "
          f"min {seg.min():.5f} m, under 1 mm: {(seg < 0.001).sum()}")

    out = gpd.GeoDataFrame(rec, geometry="geometry", crs=baffles.crs)
    dest = GEO / "structures-merged.gpkg"
    out.to_file(dest, driver="GPKG")
    print(f"\n{len(out)} features -> {dest.name}  "
          f"({len(baffles)} CAD + {kept} drape, {dropped} drape dropped)")
    print(f"total solid area {out.geometry.area.sum():.2f} m2, "
          f"all valid: {out.geometry.is_valid.all()}")


if __name__ == "__main__":
    main()
