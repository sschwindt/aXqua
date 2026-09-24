"""Settle the fish pass's plan dimensions against the drawing itself.

The DXF is the arbiter: every other figure in this case comes from the same CAD
lineage (the STLs, and the meshes snapped onto them), so those are not independent of
each other. This reads the drawing.

**Why measuring segment lengths does not find the slot.** The obvious approach - scan
the construction layer, cluster the lengths, look for 0.380 m - cannot work, and it
returns a plausible-looking wrong answer when tried. A slot is an *absence of
material*. Nothing is drawn across it, so there is no 0.380 m line anywhere in the
drawing to find, and whatever segments do happen to measure 0.380 m are coincidence.

The slot exists only as a complement: the clear channel, minus the baffle that reaches
into it. Both of those ARE drawn, repeatedly and unambiguously, so the measurement is

    slot = (clear channel between the wall inner faces) - (baffle reach)

and the drawing states both sides of it 56 times each.

    python cases/munich-vsf/measure_slot_from_dxf.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CAD = HERE / "user-sources" / "geodata" / "cad"

#: The plan detail of the pass, in the drawing's own (DHDN GK zone 4) coordinates.
#: Found from the `Schlitzpass` leader and the drawing's own pool numbers 1..13.
WINDOW = (4473098.0, 5332088.5, 4473120.5, 5332092.5)
#: Construction geometry only: the hatching layer carries ten times as many segments
#: and none of them is a face.
HATCH = "Schraffur"
TOL = 0.005          # [m] how close two drawn lengths must be to count as the same


def load_segments(window):
    """Every drawn construction segment inside *window*, as (start, end, length)."""
    import geopandas as gpd
    from shapely.geometry import box

    dxf = next(CAD.glob("*.dxf"), None)
    if dxf is None:
        raise SystemExit(f"no .dxf in {CAD}")
    gdf = gpd.read_file(dxf)          # GDAL's DXF driver; no ezdxf needed
    inside = gdf[gdf.geometry.intersects(box(*window))]
    lines = inside[inside.Layer.str.startswith("BPR_Konstruktion")
                   & ~inside.Layer.str.contains(HATCH)]
    out = []
    for geom in lines.geometry:
        parts = [geom] if geom.geom_type == "LineString" else list(geom.geoms)
        for part in parts:
            coords = np.asarray(part.coords)[:, :2]
            for a, b in zip(coords[:-1], coords[1:]):
                length = float(np.hypot(*(b - a)))
                if length > 1e-6:
                    out.append((a, b, length))
    return out, dxf


def _angle(a, b) -> float:
    return abs(np.degrees(np.arctan2((b - a)[1], (b - a)[0])) % 180)


def main() -> None:
    segments, dxf = load_segments(WINDOW)
    print(f"{dxf.name}")
    print(f"{len(segments)} construction segments in the pass detail\n")

    across = [(a, b, L) for a, b, L in segments if 60 < _angle(a, b) < 120]

    # --- the baffles: the repeated cross-channel face ------------------------ #
    counts: dict[float, int] = {}
    for _, _, L in across:
        key = round(round(L / TOL) * TOL, 3)
        counts[key] = counts.get(key, 0) + 1
    print("repeated cross-channel lengths:")
    for value, count in sorted(counts.items(), key=lambda kv: -kv[1])[:6]:
        print(f"  {value:6.3f} m  x{count}")

    # the baffle face is the longest length that repeats across the whole pass
    reach, n = max(((v, c) for v, c in counts.items() if c >= 10),
                   key=lambda kv: kv[0])
    faces = [(a, b) for a, b, L in across if abs(L - reach) < TOL]
    ys = np.array([[min(a[1], b[1]), max(a[1], b[1])] for a, b in faces])
    xs = np.array([(a[0] + b[0]) / 2 for a, b in faces])
    near, tip = float(np.median(ys[:, 0])), float(np.median(ys[:, 1]))
    print(f"\nbaffle face: {n} segments of {reach:.3f} m, every one of them running "
          f"from y {near:.3f} to y {tip:.3f}")

    stations = np.unique(np.round(xs, 3))
    if len(stations) % 2 == 0:
        pairs = stations.reshape(-1, 2)
        print(f"  {len(stations)} faces = {len(pairs)} baffles, "
              f"{float(np.median(pairs[:, 1] - pairs[:, 0])):.3f} m thick, "
              f"{float(np.median(np.diff(pairs[:, 0]))):.3f} m pitch")

    # --- the channel: the streamwise wall faces ------------------------------ #
    alongs = [a for a, b, L in segments if L > 5.0 and _angle(a, b) < 10]
    wall_ys = np.array(sorted({round(float(a[1]), 3) for a in alongs}))
    print(f"\nstreamwise wall faces at y = {list(wall_ys)}")
    far = float(wall_ys[np.argmin(np.abs(wall_ys - (near + 1.15)))])
    print(f"  the baffles stand on y {near:.3f}; the opposite inner face is "
          f"y {far:.3f}")

    clear = far - near
    to_wall = clear - (tip - near)
    print(f"\n  clear channel        {clear:.4f} m")
    print(f"  baffle reach        -{tip - near:.4f} m")
    print("  " + "-" * 29)
    print(f"  baffle tip to wall   {to_wall:.4f} m")
    print(f"\nThe drawing states both sides of that {n} times each, and never states")
    print("the gap itself - nothing is drawn across a gap, which is why looking for a")
    print("segment of that length finds only coincidences.")

    # --- but the gap to the wall is NOT the slot ----------------------------- #
    # Opposite every baffle there is a rectangular concrete SLOT BLOCK projecting from
    # the other wall, offset downstream so the two never share a cross-section. A ray
    # cast across the channel at the baffle station therefore misses it entirely and
    # reports the gap to the wall. The slot is the narrowest opening between the two
    # solids.
    #
    # (The vertical-slot literature calls this the short baffle. Not a "nose": that
    # word means a ROUNDED feature, and this is a square-cornered rectangle - the
    # corner is what sets the slot, so the shape is the whole point.)
    from shapely.ops import polygonize, unary_union
    from shapely.geometry import LineString

    seen = {}
    for a, b, L in segments:
        seen[tuple(np.round([a[0], a[1], b[0], b[1]], 4))] = LineString([a, b])
    bodies = [p for p in polygonize(unary_union(list(seen.values()))) if p.area < 1.0]
    baffles = sorted((p for p in bodies
                      if abs((p.bounds[3] - p.bounds[1]) - reach) < TOL),
                     key=lambda p: p.bounds[0])
    blocks = sorted((p for p in bodies if p.bounds[1] > tip
                     and 0.01 < p.area < 0.1), key=lambda p: p.bounds[0])
    print(f"\nopposite the {len(baffles)} baffles the drawing carries {len(blocks)} "
          f"slot blocks, "
          f"{float(np.median([p.bounds[2]-p.bounds[0] for p in blocks])):.3f} x "
          f"{float(np.median([p.bounds[3]-p.bounds[1] for p in blocks])):.3f} m,")
    offs = []
    slots = []
    for baffle in baffles:
        later = [q for q in blocks if q.bounds[0] > baffle.bounds[0]]
        if not later:
            continue
        block = min(later, key=lambda q: q.bounds[0])
        offs.append(block.bounds[0] - baffle.bounds[2])
        slots.append(baffle.distance(block))
    if slots:
        t = np.array(slots)
        print(f"each offset {float(np.median(offs)):.3f} m downstream of its baffle, "
              "so the two never share a cross-section.")
        print(f"\n  SLOT (baffle to slot block)  {t.mean():.4f} m   "
              f"(sd {t.std():.4f} over {len(t)} baffles)")
        print(f"\nThat, not {to_wall:.3f} m, is the opening the flow passes "
              "through. A ray cast across\nthe channel AT a baffle station cannot see "
              "it, because the block is not there yet.")


if __name__ == "__main__":
    main()
