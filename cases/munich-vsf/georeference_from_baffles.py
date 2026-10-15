"""Tie the local CAD frame to DHDN / GK zone 4, using the fourteen baffles as control.

`README.md` says georeferencing is unresolved because the drawing cannot be matched to
the STLs by bounding box - the sheet carries plans, sections and a title block, so its
extent is not the structure's. That is true and it is not the only route. **The
fourteen baffles are identifiable in both frames**, measured independently:

* in GK4, by `measure_slot_from_dxf.py`, from the drawing's construction layer;
* in local CAD metres, by `measure_baffle_stations.py`, from `stahlbeton.stl`.

**And the residual over those fourteen is worthless, which is the finding here.** The
baffles of a vertical-slot fishway are identical features on a straight line at a fixed
pitch: measured, the fourteen tips are collinear to 0.1 mm and equally spaced to 0.1 mm.
Fitting one such set onto another gives a residual of **zero by construction**, whatever
the pairing, and the 180-degree reversal fits exactly as well as the correct one. So a
low residual here says only "both are straight lines", never "the control points are
right" - which is precisely the assurance a residual is wanted for.

Adding the slot blocks does not rescue it: they sit 0.12 m across the axis against a
21 m baseline, 175:1, so the fit stays longitudinally dominated.

What DOES resolve the reversal is the drawing's own words. It labels `Oberwasser`
upstream and `Unterwasser` downstream, so the orientation is read off the sheet rather
than fitted. This script therefore reports the transform, refuses to certify it on
residual, and orients it from those labels.

Scale is fixed at 1. Both frames are metres, and letting scale float would absorb a
mis-paired baffle into a plausible-looking stretch instead of showing up as error.

    python cases/munich-vsf/georeference_from_baffles.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
STATIONS = HERE / "user-sources" / "geodata" / "baffle-stations.csv"
sys.path.insert(0, str(HERE))


def baffles_in_gk4():
    """Plan centroid of each baffle in the drawing, DHDN / GK4.

    Uses `measure_slot_from_dxf.py`'s own segment filtering rather than a fresh one:
    it already excludes the hatching layer and restricts to the pass detail window, so
    the sections elsewhere on the sheet cannot contribute candidates.
    """
    from shapely.ops import polygonize, unary_union
    from shapely.geometry import LineString

    import measure_slot_from_dxf as dxf

    segments, path = dxf.load_segments(dxf.WINDOW)
    across = [(a, b, L) for a, b, L in segments if 60 < dxf._angle(a, b) < 120]
    counts: dict[float, int] = {}
    for _, _, L in across:
        key = round(round(L / dxf.TOL) * dxf.TOL, 3)
        counts[key] = counts.get(key, 0) + 1
    reach, _ = max(((v, c) for v, c in counts.items() if c >= 10),
                   key=lambda kv: kv[0])

    seen = {}
    for a, b, L in segments:
        seen[tuple(np.round([a[0], a[1], b[0], b[1]], 4))] = LineString([a, b])
    bodies = [p for p in polygonize(unary_union(list(seen.values()))) if p.area < 1.0]
    baffles = sorted((p for p in bodies
                      if abs((p.bounds[3] - p.bounds[1]) - reach) < dxf.TOL),
                     key=lambda p: p.bounds[0])
    pts = np.array([[p.centroid.x, p.centroid.y] for p in baffles])
    return pts, path.name, reach


def oberwasser_easting():
    """Easting of the drawing's `Oberwasser` label - the upstream end, stated in words.

    The one piece of orientation on the sheet that a fit cannot get wrong, because it
    is not geometry. `Unterwasser` marks the other end; both appear twice.
    """
    import geopandas as gpd

    import measure_slot_from_dxf as dxf

    path = next(dxf.CAD.glob("*.dxf"))
    g = gpd.read_file(path)
    t = g[g["Text"].notna()]
    up = t[t["Text"].astype(str) == "Oberwasser"]
    down = t[t["Text"].astype(str) == "Unterwasser"]
    if up.empty or down.empty:
        raise SystemExit("no Oberwasser/Unterwasser labels - orientation is unresolved")
    ux = float(up.geometry.representative_point().x.mean())
    dx_ = float(down.geometry.representative_point().x.mean())
    if ux >= dx_:
        raise SystemExit("Oberwasser is not upstream of Unterwasser in easting - "
                         "the sheet is not laid out as assumed")
    return ux


def rigid_fit(src, dst):
    """Rotation + translation taking *src* onto *dst*, scale fixed at 1 (Kabsch)."""
    cs, cd = src.mean(axis=0), dst.mean(axis=0)
    h = (src - cs).T @ (dst - cd)
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    r = vt.T @ np.diag([1.0, d]) @ u.T
    t = cd - r @ cs
    res = np.linalg.norm((src @ r.T + t) - dst, axis=1)
    return r, t, res


def main() -> None:
    import pandas as pd

    if not STATIONS.is_file():
        raise SystemExit(f"{STATIONS.name} not built - run "
                         "measure_baffle_stations.py first")
    st = pd.read_csv(STATIONS).sort_values("s")
    local = st[["baffle_tip_x", "baffle_tip_y"]].to_numpy()

    gk4, sheet, reach = baffles_in_gk4()
    print(f"{sheet}: {len(gk4)} baffles in the pass detail, face length "
          f"{reach:.3f} m")
    print(f"{STATIONS.name}: {len(local)} baffles in local CAD metres")
    if len(gk4) != len(local):
        raise SystemExit(f"cannot pair {len(gk4)} drawn baffles with {len(local)} "
                         "measured ones - fix the DXF filter before fitting")

    # Is the control usable at all? Collinear points fit anything.
    spread = local - local.mean(axis=0)
    _, sv, _ = np.linalg.svd(spread, full_matrices=False)
    aspect = sv[0] / max(sv[1], 1e-12)
    print(f"\ncontrol geometry: {sv[0]:.3f} m along the pass against {sv[1]:.4f} m "
          f"across it,\n  aspect {aspect:,.0f}:1 - "
          + ("effectively COLLINEAR" if aspect > 20 else "genuinely two-dimensional"))

    both = {}
    for name, order in (("same", gk4), ("reversed", gk4[::-1])):
        both[name] = rigid_fit(local, order)
    for name, (_, _, res) in both.items():
        print(f"  {name:9s} pairing: residual max {res.max():.5f} m")
    if aspect > 20:
        print("  Both fit. On collinear control they always will, so the residual\n"
              "  CANNOT choose between them and cannot detect a mis-picked point.")

    # So orient from the drawing's own words instead. `Oberwasser` is upstream.
    upstream_x = oberwasser_easting()
    order = "same" if (
        abs(both["same"][0] @ local[0] + both["same"][1] - upstream_x)[0]
        < abs(both["reversed"][0] @ local[0] + both["reversed"][1] - upstream_x)[0]
    ) else "reversed"
    r, t, res = both[order]
    worst = res.max()
    bearing = np.degrees(np.arctan2(r[1, 0], r[0, 0]))
    print("\norientation from the drawing's labels, not from the fit:")
    print(f"  'Oberwasser' (upstream) sits at easting {upstream_x:.1f}")
    print(f"  baffle 1 is the upstream one, so the {order} pairing is the right way "
          "round")

    print(f"\n{'#':>2} {'local x':>9} {'local y':>9} {'-> GK4 x':>13} "
          f"{'-> GK4 y':>13} {'residual':>9}")
    mapped = local @ r.T + t
    target = gk4 if order == "same" else gk4[::-1]
    for i, (p, q, e) in enumerate(zip(mapped, target, res), start=1):
        print(f"{i:>2} {local[i-1][0]:9.4f} {local[i-1][1]:9.4f} "
              f"{p[0]:13.4f} {p[1]:13.4f} {e:9.4f}")

    print(f"\n  rotation   {bearing:+.6f} deg")
    print(f"  translation ({t[0]:.4f}, {t[1]:.4f})")
    print(f"  residual   max {worst:.5f} m  -- NOT evidence, see above")

    print("\nsurfaces: transform for case-config.yml (crs_epsg: 31468)")
    print(f"  rotation_deg: {bearing:.6f}")
    print(f"  dx: {t[0]:.4f}")
    print(f"  dy: {t[1]:.4f}")
    print("  scale: 1.0")
    print("\nUNVERIFIED. The residual above is structurally incapable of detecting a\n"
          "mis-picked control point, so this needs a control feature OFF the pass\n"
          "axis before it is trusted - the weir, a wall corner, a survey point. The\n"
          "drawing carries four `Bestandsplan` survey points with GK4 coordinates in\n"
          "their labels; if any of them can be identified in the CAD, that alone\n"
          "would make the fit checkable.")


if __name__ == "__main__":
    main()
