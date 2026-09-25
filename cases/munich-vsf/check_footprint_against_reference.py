"""Check a wall footprint against two independent references, before it is trusted.

A derived wall footprint is the single input everything downstream depends on, and it
fails silently: a footprint in the wrong place, mirrored, or in the wrong units still
builds a mesh, still runs, and still returns a water level. Three such failures have
happened on this case, none of them raising anything:

* a **vertical flip** - `np.meshgrid` puts row 0 at the south edge while
  `rasterio.transform.from_origin(west, north, ...)` expects it at the north;
* a **wrong CRS stamp** - this case runs in local CAD metres, and labelling that
  layer EPSG:25832 sends `dataset()` off to reproject it to x = 3,664,114;
* a **footprint fattened by rasterise-and-hole-fill**, which keeps every wall in the
  right place and still closes the opening between two of them.

The first two are gross and the third is subtle, so this checks against two references
rather than one:

`user-sources/reference/federica-wetted-bed.csv`
    The lowest wetted boundary of an independently built OpenFOAM model of the same
    structure, on a 0.25 m plan grid. It says where that model found WATER, so a
    footprint claiming wall there disagrees with it. At 0.25 m it cannot resolve a
    0.15 m baffle, so it is a check on PLACEMENT, not on width - which is exactly
    the two gross failures above.

`user-sources/geodata/baffle-footprints.gpkg`
    The 28 baffle and slot-block outlines read straight off the CAD by
    `measure_baffle_stations.py`, at the CAD's own precision. This is the check on
    width: how much of each solid the footprint realises, and - the number that
    decides this case - what is left of the 0.1697 m slot between them.

    python cases/munich-vsf/check_footprint_against_reference.py <footprint.gpkg> ...

With no argument it checks every candidate it can find in the case.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REFERENCE = HERE / "user-sources" / "reference" / "federica-wetted-bed.csv"
EXACT = HERE / "user-sources" / "geodata" / "baffle-footprints.gpkg"
STATIONS = HERE / "user-sources" / "geodata" / "baffle-stations.csv"
CANDIDATES = [
    HERE / "axqua-case" / "preprocessing" / "structures-from-surfaces.gpkg",
    HERE / "user-sources" / "geodata" / "walls-drape.gpkg",
]
CELL = 0.25                      #: the reference grid step
#: Patches of the reference that are unambiguously open water over bed, inside the
#: pass. A footprint has no business claiming any of them.
WET_PATCHES = ("Substratum_fishpass", "Magerbeton", "Magerbeton_refinement",
               "Substratum_final", "Substratum_weir")


def load_footprint(path):
    import geopandas as gpd

    gdf = gpd.read_file(path)
    return gdf, gdf.geometry.union_all()


def frame_check(union, ref_xy):
    """Does the layer even land on the structure? Catches a wrong CRS stamp."""
    x0, y0, x1, y1 = union.bounds
    rx0, ry0 = ref_xy.min(axis=0)
    rx1, ry1 = ref_xy.max(axis=0)
    print(f"  bounds      x {x0:12.3f}..{x1:12.3f}   y {y0:12.3f}..{y1:12.3f}")
    print(f"  reference   x {rx0:12.3f}..{rx1:12.3f}   y {ry0:12.3f}..{ry1:12.3f}")
    overlap = not (x1 < rx0 or x0 > rx1 or y1 < ry0 or y0 > ry1)
    if not overlap:
        print("  FRAME       the layer does not touch the reference at all - "
              "wrong CRS or wrong units")
    else:
        print("  FRAME       ok, the layer is on the structure")
    return overlap


def orientation_check(union, solids):
    """Does a mirrored copy cover the known solids better? Catches a flip.

    Mirrored about the STRUCTURE's own centre, not the layer's: a footprint written
    for the whole 88 m domain has a bbox far larger than the pass, and any mirror of
    that lands off the reference entirely, which scores well by accident and says
    nothing. What a flip actually does is move the walls off the solids they are
    supposed to be, so that is what is measured.
    """
    from shapely.affinity import scale

    if solids is None:
        print("  ORIENTATION skipped, no exact solids to test against")
        return True
    whole = solids.union_all()
    cx = (whole.bounds[0] + whole.bounds[2]) / 2
    cy = (whole.bounds[1] + whole.bounds[3]) / 2
    hits = {
        "as given": union,
        "flipped north-south": scale(union, 1, -1, origin=(cx, cy)),
        "flipped east-west": scale(union, -1, 1, origin=(cx, cy)),
    }
    cover = {k: 100 * v.intersection(whole).area / whole.area
             for k, v in hits.items()}
    best = max(cover, key=cover.get)
    for name, pct in cover.items():
        print(f"  {name:22s} covers {pct:5.1f}% of the 28 known solids"
              + ("   <- best" if name == best else ""))
    if best != "as given":
        print(f"  ORIENTATION a copy {best} fits the structure BETTER than the "
              "layer does - suspect a flip")
    else:
        print("  ORIENTATION ok, no mirror of this layer fits better")
    return best == "as given"


def exclusion_check(union, ref):
    """How much open water does the footprint claim as wall?"""
    from shapely.geometry import Point

    print(f"  {'patch':26s} {'cells':>6} {'claimed':>8} {'%':>6}")
    worst = 0.0
    for patch, group in ref.groupby("patch"):
        if patch not in WET_PATCHES:
            continue
        inside = sum(union.contains(Point(x, y))
                     for x, y in zip(group.x, group.y))
        frac = 100 * inside / len(group)
        worst = max(worst, frac)
        print(f"  {patch:26s} {len(group):6d} {inside:8d} {frac:6.1f}")
    print(f"  EXCLUSION   worst patch {worst:.1f}% claimed as wall "
          f"({'ok' if worst < 5 else 'the footprint is standing in the water'})")
    return worst


def terrain_check(union, ref, solids):
    """Is raised BED being typed as structure? lww-133's class of bug, made testable.

    A drape or column test asks "is there CAD material above the local bed here", and
    a weir, an invert, a ramp and an exit apron all answer yes - so bed becomes wall,
    and under `solid_mode: cut` those cells leave the domain. Counting claimed cells
    does not separate that from a footprint merely being fat, and the two want
    opposite fixes.

    What separates them is DISTANCE to a solid known to be real. Fattening sits a few
    centimetres off a baffle; mis-typed terrain sits metres away in open ground.

    **Only judged INSIDE the baffled corridor**, and that restriction is the whole
    care of this function. The exact outlines are baffles and slot blocks - they
    contain no wall, by construction. So outside the corridor a cell correctly sitting
    on a side wall is metres from the nearest baffle and would be flagged as mis-typed
    terrain, which is the same "reference set is not the full set" error that made
    lww-133 drop `wall-004`. Cells outside are counted and reported, never judged.
    """
    from shapely.geometry import Point

    if solids is None:
        print("  TERRAIN     skipped, no exact solids to measure distance from")
        return
    known = solids.union_all()
    corridor = known.convex_hull.buffer(0.35)
    print(f"  {'bed patch':26s} {'cells':>6} {'claimed':>8} {'far, in corridor':>17} "
          f"{'outside':>8}")
    stranded = outside = 0
    for patch, group in ref.groupby("patch"):
        if not (patch.startswith("Substratum") or patch.startswith("Magerbeton")):
            continue
        hit = far = out = 0
        for x, y in zip(group.x, group.y):
            p = Point(x, y)
            if not union.contains(p):
                continue
            hit += 1
            if not corridor.contains(p):
                out += 1
            elif known.distance(p) > 0.5:
                far += 1
        stranded += far
        outside += out
        if hit:
            print(f"  {patch:26s} {len(group):6d} {hit:8d} {far:17d} {out:8d}")
    print(f"  TERRAIN     {stranded} claimed cells inside the corridor sit >0.5 m "
          "from a known solid,")
    print(f"              {outside} more are outside it. These are CANDIDATES for "
          "mis-typed bed,\n              not a verdict: the exact outlines are "
          "baffles and slot blocks only, so\n              a cell correctly on a "
          "SIDE WALL is also far from anything known. Separating\n              the "
          "two needs exact wall outlines, which this case does not yet have.")
    print("              What the number is good for is a BEFORE/AFTER: apply a "
          "terrain\n              protection and it should fall sharply. Absolute, "
          "it over-counts.")


def patency_check(union, stations, solids, step=0.005, pad=0.10):
    """Can water get down the pass, and OUT of it?

    The check that matters most and the one nobody runs. lww-133 lost a 10 h run to a
    footprint that walled off the pass OUTLET: the level gate passed - 2.350 m against
    2.845 m wall tops - because a perched pond sits under the wall tops too. What gave
    it away was the longitudinal profile, a flat surface where a vertical-slot fishway
    must step down ~0.13 m per pool.

    So this walks the pass axis and reports the widest continuous opening across it.
    Inside the baffled reach it should pinch at every baffle and open between them;
    PAST the last baffle it must stay open, all the way out.

    **The scan is bounded to the channel**, and that matters more than it looks. Run
    across a fixed half-width it eventually leaves the pass, and then open ground
    beyond the far wall joins the "continuous opening" and a genuine choke inside the
    channel reads as wide open. A check that cannot fail is worse than no check, so
    the width comes from the known solids' own extent across the axis.
    """
    from shapely.geometry import Point

    p = stations[["slot_x", "slot_y"]].to_numpy()
    centre = p.mean(axis=0)
    _, _, vt = np.linalg.svd(p - centre, full_matrices=False)
    along = vt[0] / np.linalg.norm(vt[0])
    if along @ (p[-1] - p[0]) < 0:
        along = -along
    across = np.array([along[1], -along[0]])
    s0 = float((p[0] - centre) @ along)
    s1 = float((p[-1] - centre) @ along)

    corners = np.vstack([np.asarray(g.exterior.coords) for g in solids.geometry])
    n_of = (corners - centre) @ across
    lo, hi = float(n_of.min()) - pad, float(n_of.max()) + pad
    print(f"  channel     scanned across n {lo:+.3f}..{hi:+.3f} m "
          f"({hi - lo:.3f} m, from the solids' own extent)")
    ns = np.arange(lo, hi + step, step)
    worst_in, worst_out, out_at = None, None, None
    for s in np.arange(s0 - 2.0, s1 + 10.0, 0.25):
        pts = centre + s * along + ns[:, None] * across
        blocked = np.array([union.contains(Point(*q)) for q in pts])
        runs, cur = [], 0
        for v in blocked:
            if v:
                runs.append(cur)
                cur = 0
            else:
                cur += 1
        runs.append(cur)
        gap = max(runs) * step
        if s <= s1:
            worst_in = gap if worst_in is None else min(worst_in, gap)
        elif worst_out is None or gap < worst_out:
            worst_out, out_at = gap, s
    print(f"  pass axis   {s0:.2f} to {s1:.2f} m baffled, then 10 m past the last one")
    print(f"  in the reach          narrowest continuous opening {worst_in:.3f} m")
    print(f"  past the last baffle  narrowest {worst_out:.3f} m at s = {out_at:.2f}")
    if worst_out < 0.3:
        print("  PATENCY     the OUTLET IS CHOKED - the pass would pond rather than "
              "convey,\n              and a level check cannot see that")
    elif worst_in <= 0.0:
        print("  PATENCY     a station inside the reach is fully blocked")
    else:
        print("  PATENCY     ok, the pass conveys and its outlet is open")


def width_check(union, solids, drawn=0.1697):
    """What the footprint makes of each solid, and of the slot between them.

    The slot is measured on the FOOTPRINT, not on the CAD: the two solids are
    located from the CAD, but the opening reported is the gap this layer actually
    leaves between the piece of itself standing on the baffle and the piece standing
    on the slot block. Intersecting the exact polygons with the layer and measuring
    between those would return 0.1697 m for any layer that covers both, which is the
    question begged rather than answered.
    """
    if solids is None:
        print("  WIDTH       skipped, no exact solids "
              "(run measure_baffle_stations.py)")
        return
    realised, area = [], []
    for _, group in solids.groupby("baffle"):
        parts = {row["kind"]: row.geometry for _, row in group.iterrows()}
        if {"baffle", "slot_block"} - parts.keys():
            continue
        area += [100 * p.intersection(union).area / p.area for p in parts.values()]
        near = union.intersection(
            parts["baffle"].union(parts["slot_block"]).buffer(0.6))
        pieces = list(getattr(near, "geoms", [near]))
        on_baffle = [g for g in pieces if g.intersects(parts["baffle"])]
        on_block = [g for g in pieces if g.intersects(parts["slot_block"])]
        if not on_baffle or not on_block:
            realised.append(np.nan)           # the layer is not on one of them
        elif any(a.equals(b) for a in on_baffle for b in on_block):
            realised.append(0.0)              # one piece: the opening is welded shut
        else:
            realised.append(min(a.distance(b)
                                for a in on_baffle for b in on_block))
    a = np.array(area)
    t = np.array(realised, dtype=float)
    print(f"  solids      {len(t)} pairs, {a.mean():.1f}% of each solid's area "
          f"covered (min {a.min():.1f}%)")
    with np.errstate(all="ignore"):
        shut = int(np.nansum(t <= 1e-9))
        missing = int(np.isnan(t).sum())
        open_ = t[np.isfinite(t) & (t > 1e-9)]
        if open_.size:
            print(f"  SLOT        drawn {drawn:.4f} m -> this layer leaves "
                  f"{open_.mean():.4f} m mean, {open_.min():.4f} min "
                  f"({100 * open_.mean() / drawn:.0f}% of drawn) "
                  f"over {open_.size} of {len(t)} slots")
        if shut:
            print(f"  SLOT        {shut} of {len(t)} are WELDED SHUT by this "
                  "footprint")
        if missing:
            print(f"  SLOT        {missing} of {len(t)} have no footprint on one "
                  "of the two solids")


def main() -> None:
    import pandas as pd

    ref = pd.read_csv(REFERENCE)
    ref_xy = ref[["x", "y"]].to_numpy()
    wet = ref[ref.patch.isin(WET_PATCHES)]
    print(f"reference: {len(ref)} cells at {CELL} m, "
          f"{len(wet)} of them open water over bed\n")

    solids = None
    if EXACT.is_file():
        import geopandas as gpd
        solids = gpd.read_file(EXACT)
        print(f"exact solids: {len(solids)} outlines read from {EXACT.name}\n")
    else:
        print(f"exact solids: {EXACT.name} not built, width checks skipped\n")

    stations = pd.read_csv(STATIONS) if STATIONS.is_file() else None
    if stations is None:
        print("no baffle-stations.csv, so no patency check\n")

    paths = [Path(a) for a in sys.argv[1:]] or [p for p in CANDIDATES if p.is_file()]
    if not paths:
        raise SystemExit("no footprint layer given and none of the usual ones exist")

    for path in paths:
        print(f"=== {path} ===")
        if not path.is_file():
            print("  missing\n")
            continue
        gdf, union = load_footprint(path)
        print(f"  {len(gdf)} features, {union.area:.2f} m2, crs {gdf.crs}")
        if not frame_check(union, ref_xy):
            print()
            continue
        orientation_check(union, solids)
        exclusion_check(union, ref)
        terrain_check(union, ref, solids)
        width_check(union, solids)
        if stations is not None:
            patency_check(union, stations, solids)
        print()


if __name__ == "__main__":
    main()
