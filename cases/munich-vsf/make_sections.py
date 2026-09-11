"""Derive the pool cross-sections XS1-XS4 from the geometry.

.. warning::

   **Superseded, and it was wrong.** This script reports 12 baffles and 11 pools. The
   structure has **14 baffles and 13 basins** at a 1.640 m pitch, which the contractor
   STL, the Blender heightmap and the dimensioned drawing all agree on (see the case
   README). The pool-reach detection below drops the two end basins, where the channel
   transitions into the entry chamber and the outlet, so every section it places is
   numbered one or two basins off.

   ``user-sources/geodata/fishpass-dimensions-ssc.fodp`` gives the four sections
   directly, as stations along the reach: **XS 1 at 3.4 m** (approach channel),
   **XS 2 at 11.2 m** (basin 3), **XS 3 at 17.8 m** (basin 7), **XS 4 at 32.3 m** (exit
   channel). Two of the four are channel sections rather than basins, which is also why
   the campaign's ``US2/US4/US5/US7`` labels never mapped onto pool numbers. Use those
   stations; keep this script for the pool geometry it reports along the way.

The flume campaign reports its measurements per **pool** - XS1 at US2, XS2 at US4, XS3
at US5, XS4 at US7 - and nothing else: no coordinates, no station chainage. The pools
themselves are in the CAD, though, so the sections can be recovered from it rather than
guessed:

1. take the wetted domain the surface stage derived, minus the wall footprints;
2. of the resulting strips, the fish pass is the one whose width **oscillates** - it is
   pinched to a vertical slot at every baffle, where the parallel channel beside it runs
   at a constant width;
3. the pinch points are the baffles, so the pools are the intervals between them;
4. a section is the perpendicular through the middle of the requested pool.

Run it after ``axqua surface`` and check the plot it writes against the paper::

    python make_sections.py                      # pools 2, 4, 5, 7, numbered from upstream
    python make_sections.py --number-from downstream
    python make_sections.py --pools 2 4 5 7 --plot

**The numbering direction is the one thing the geometry cannot settle.** Pools are
numbered from the upstream (inflow) end by default, because that is how a fish pass is
usually described - but if the report counts from the downstream end, pass
``--number-from downstream`` and everything shifts accordingly. The plot labels every
pool it found, so a single look settles it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np

from axqua.config import load_config
from axqua.logsetup import setup_logging

HERE = Path(__file__).resolve().parent

#: which pools the campaign measured, in the order XS1..XS4
DEFAULT_POOLS = (2, 4, 5, 7)
#: a free width below this fraction of the strip's median is a slot, not a pool
SLOT_FRACTION = 0.6
#: stations along the channel [m]
STEP = 0.05
#: two pinch points closer than this are the two sides of one baffle [m]
MIN_POOL_LENGTH = 0.8


def channel_axis(polygon, step: float = 0.25):
    """A polyline down the middle of a strip, from its width-wise centroids.

    The fish pass runs monotonically in y here, so slicing by y and taking the centre
    of each slice is enough - a medial-axis transform would be more general and much
    slower, and the extra generality buys nothing on a channel that never doubles back.
    """
    from shapely.geometry import LineString

    ymin, ymax = polygon.bounds[1], polygon.bounds[3]
    xmin, xmax = polygon.bounds[0] - 1.0, polygon.bounds[2] + 1.0
    points = []
    for y in np.arange(ymin + step, ymax, step):
        chord = LineString([(xmin, y), (xmax, y)]).intersection(polygon)
        if chord.is_empty:
            continue
        parts = list(getattr(chord, "geoms", [chord]))
        longest = max(parts, key=lambda g: g.length)
        points.append((longest.centroid.x, y))
    return np.array(points)


def width_profile(polygon, axis, half_width: float = 6.0):
    """Free width perpendicular to the axis at every station."""
    from shapely.geometry import LineString

    tx, ty = np.gradient(axis[:, 0]), np.gradient(axis[:, 1])
    norm = np.hypot(tx, ty)
    norm[norm == 0] = 1.0
    nx, ny = ty / norm, -tx / norm
    widths, chords = [], []
    for (x, y), a, b in zip(axis, nx, ny):
        probe = LineString([(x - half_width * a, y - half_width * b),
                            (x + half_width * a, y + half_width * b)])
        cut = probe.intersection(polygon)
        parts = [g for g in getattr(cut, "geoms", [cut]) if not g.is_empty]
        if not parts:
            widths.append(0.0)
            chords.append(None)
            continue
        # the piece the axis itself runs through, not a sliver across a neighbouring bay
        best = min(parts, key=lambda g: g.distance(__import__("shapely.geometry",
                                                              fromlist=["Point"])
                                                   .Point(x, y)))
        widths.append(best.length)
        chords.append(best)
    return np.asarray(widths), chords


def find_slots(widths, axis, *, fraction: float = SLOT_FRACTION,
               min_pool_length: float = MIN_POOL_LENGTH):
    """Indices of the pinch points: local minima well below the strip's median width.

    Two pinch points closer together than *min_pool_length* are one baffle seen twice -
    a vertical-slot baffle is a T in plan, so a cross-cut is pinched once beside its
    stem and again beside its tip. Merging them is what keeps a 0.3 m gap between two
    halves of the same baffle from being reported as a pool.
    """
    median = float(np.median(widths[widths > 0]))
    threshold = fraction * median
    below = widths < threshold
    raw = []
    index = 0
    while index < len(below):
        if not below[index]:
            index += 1
            continue
        end = index
        while end + 1 < len(below) and below[end + 1]:
            end += 1
        raw.append(index + int(np.argmin(widths[index:end + 1])))
        index = end + 1

    slots, group = [], []
    for station in raw:
        if group and _along(axis, group[-1], station) > min_pool_length:
            slots.append(group[len(group) // 2])
            group = []
        group.append(station)
    if group:
        slots.append(group[len(group) // 2])
    return slots, median, threshold


def _along(axis, first, second) -> float:
    """Distance between two stations, measured along the channel.

    From the station spacing rather than by walking the axis polyline: at a baffle the
    width-wise centroid hops from one side of the T to the other, so the path length
    between two stations a few centimetres apart can come out at more than a metre -
    which would defeat the very merging this feeds.
    """
    return abs(int(second) - int(first)) * STEP


def _pool_reach(open_area, roi, log, *, step: float = 0.5, min_segments: int = 3):
    """The y-range over which the fish pass runs beside the parallel channel.

    A cross-cut of the domain meets one open segment where there is a single channel
    and three or more where the fish pass, its dividing wall and the channel beside it
    are all present. The longest run of the latter is the pool reach.
    """
    from shapely.geometry import LineString

    ymin, ymax = roi.bounds[1], roi.bounds[3]
    ys = np.arange(ymin + step, ymax, step)
    multi = []
    for y in ys:
        cut = LineString([(roi.bounds[0] - 1, y), (roi.bounds[2] + 1, y)])
        pieces = [g for g in getattr(cut.intersection(open_area), "geoms", [])
                  if g.length > 0.1]
        multi.append(len(pieces) >= min_segments)
    best, run, start = (0, 0), 0, 0
    for index, flag in enumerate([*multi, False]):
        if flag:
            if run == 0:
                start = index
            run += 1
        else:
            if run > best[1] - best[0]:
                best = (start, index)
            run = 0
    low, high = float(ys[best[0]]), float(ys[min(best[1], len(ys) - 1)])
    log.info("pool reach: y %.1f .. %.1f (%.1f m, from the cross-cut segment count)",
             low, high, high - low)
    return low, high


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=HERE / "case-config.yml")
    parser.add_argument("--pools", type=int, nargs="+", default=list(DEFAULT_POOLS),
                        help="pool numbers to place sections in (default: 2 4 5 7)")
    parser.add_argument("--number-from", choices=["upstream", "downstream"],
                        default="upstream",
                        help="which end pool 1 is at (default: upstream)")
    parser.add_argument("--plot", action="store_true", default=True)
    args = parser.parse_args(argv)
    setup_logging(level=logging.INFO)
    log = logging.getLogger("axqua")

    import geopandas as gpd
    from shapely.geometry import LineString
    from shapely.ops import unary_union

    cfg = load_config(args.config)
    pre = Path(cfg.preprocessing_dir)
    roi = gpd.read_file(pre / cfg.surfaces.roi_name).geometry.iloc[0]
    walls = unary_union(gpd.read_file(pre / cfg.surfaces.structures_name).geometry.values)

    open_area = roi.difference(walls)

    # 1) Find the pool reach. Everywhere else the domain is a single channel, so a
    # cross-cut meets one open segment; where the fish pass runs beside the parallel
    # channel it meets three or more. That count, not a hand-set y-range, is what
    # locates the reach.
    low, high = _pool_reach(open_area, roi, log)

    # 2) Within that reach the two channels are separate polygons - they join only
    # upstream and downstream of it, which is why splitting the whole domain does not
    # work. The fish pass is the component that is pinched at every baffle: its
    # narrowest cross-cut is a fraction of its median width, where the parallel channel
    # runs at a near-constant width.
    from shapely.geometry import box

    # The two channels merge just outside the reach, so a window taken at its full
    # extent still yields one blob. Shrink it from both ends until they come apart -
    # a few metres is enough, and the pools in between are what we are after anyway.
    clipped, span = None, high - low
    for trim in np.arange(0.0, 0.31, 0.02):
        window = open_area.intersection(
            box(-1e6, low + trim * span, 1e6, high - trim * span))
        parts = sorted([g for g in getattr(window, "geoms", [window]) if g.area > 5.0],
                       key=lambda g: -g.area)
        # Two *comparable* channels, not one blob plus a sliver: while the fish pass and
        # the channel beside it are still joined at the ends of the reach they come
        # through as a single polygon, and a lone sliver would pass a bare count.
        if len(parts) >= 2 and parts[1].area > 0.3 * parts[0].area:
            clipped = window
            log.info("channels separate with %.0f%% trimmed from each end "
                     "(%.1f and %.1f m2)", trim * 100, parts[0].area, parts[1].area)
            break
    if clipped is None:
        raise SystemExit("the fish pass never separates from the channel beside it; "
                         "check structures-from-surfaces.gpkg in QGIS")

    scored = []
    for part in sorted(getattr(clipped, "geoms", [clipped]), key=lambda g: -g.area):
        # a sliver between two wall polygons can be pinched to nothing and would win
        # the pinch test on noise alone
        if part.area < 5.0:
            continue
        axis = channel_axis(part, step=STEP)
        if len(axis) < 20:
            continue
        widths, chords = width_profile(part, axis)
        positive = widths[widths > 0]
        if positive.size == 0:
            continue
        ratio = float(positive.min() / np.median(positive))
        found, _, _ = find_slots(widths, axis)
        scored.append((len(found), -ratio, part, axis, widths, chords))
        log.info("candidate: area %6.1f m2, median width %.2f m, min %.2f m "
                 "(pinch ratio %.2f), %d pinch points", part.area,
                 float(np.median(positive)), float(positive.min()), ratio, len(found))
    if not scored:
        raise SystemExit("no channel found inside the pool reach")
    # The fish pass is the one that is pinched *repeatedly* - once per baffle. A count
    # separates it from the channel beside it far more sharply than the depth of any
    # single pinch, which a sliver or a wall gap can imitate.
    count, _, strip, axis, widths, chords = max(scored, key=lambda s: (s[0], s[1]))
    log.info("the fish pass is the candidate with %d pinch points", count)

    # The window was trimmed until the two channels came apart, which cuts the first
    # and last pool off the sequence. Recover them: the fish pass is now identified, so
    # the same channel can be followed through the untrimmed open area by taking the
    # part of it that touches what was found.
    full = open_area.intersection(box(-1e6, low, 1e6, high))
    pieces = [g for g in getattr(full, "geoms", [full])
              if g.area > 5.0 and g.intersects(strip.buffer(-0.05))]
    if pieces:
        candidate = max(pieces, key=lambda g: g.area)
        if candidate.area > strip.area:
            axis_full = channel_axis(candidate, step=STEP)
            widths_full, chords_full = width_profile(candidate, axis_full)
            slots_full, _, _ = find_slots(widths_full, axis_full)
            if len(slots_full) >= count:
                log.info("extending to the untrimmed reach recovers %d pinch points "
                         "(was %d)", len(slots_full), count)
                strip, axis, widths, chords = (candidate, axis_full, widths_full,
                                               chords_full)

    slots, median, threshold = find_slots(widths, axis)
    log.info("fish pass: %.1f m2, median width %.2f m, %d slots below %.2f m",
             strip.area, median, len(slots), threshold)
    if len(slots) < 3:
        raise SystemExit("fewer than three pinch points - the strip picked is probably "
                         "not the fish pass; check the plot")

    # 3) pools sit between consecutive slots, numbered from the chosen end
    pools = [(slots[i], slots[i + 1]) for i in range(len(slots) - 1)]
    if args.number_from == "downstream":
        pools = list(reversed(pools))
    log.info("%d pools between the slots, numbered from the %s end",
             len(pools), args.number_from)
    for number, (start, end) in enumerate(pools, start=1):
        y0, y1 = axis[start, 1], axis[end, 1]
        log.info("  pool %2d: y %.2f .. %.2f (%.2f m long)", number, min(y0, y1),
                 max(y0, y1), abs(y1 - y0))

    # 4) a section through the middle of each requested pool
    rows, geoms = [], []
    for label, pool in enumerate(args.pools, start=1):
        if pool > len(pools):
            log.warning("pool %d does not exist - only %d were found", pool, len(pools))
            continue
        start, end = pools[pool - 1]
        middle = (start + end) // 2
        chord = chords[middle]
        if chord is None:
            log.warning("pool %d has no chord at its centre", pool)
            continue
        geoms.append(LineString(chord.coords))
        rows.append({"Name": f"XS{label}", "pool": pool,
                     "y": round(float(axis[middle, 1]), 3),
                     "width": round(chord.length, 3)})
        log.info("XS%d -> pool %d (US%d) at y %.2f, width %.2f m", label, pool, pool,
                 axis[middle, 1], chord.length)

    out = HERE / "user-sources" / "geodata" / "cross-sections.gpkg"
    out.parent.mkdir(parents=True, exist_ok=True)
    crs = None if cfg.surfaces.transform().is_identity else f"EPSG:{cfg.crs_epsg}"
    gpd.GeoDataFrame(rows, geometry=geoms, crs=crs).to_file(out, driver="GPKG")
    log.info("wrote %s", out)

    if args.plot:
        _plot(strip, axis, widths, slots, pools, rows, geoms,
              cfg.postprocessing_path("pool-sections.png"), args.number_from, log)
    log.warning("check the numbering against the report: the geometry fixes where the "
                "pools are, but not which end pool 1 is at. Re-run with "
                "--number-from %s if the plot disagrees.",
                "downstream" if args.number_from == "upstream" else "upstream")
    return 0


def _plot(strip, axis, widths, slots, pools, rows, geoms, path, number_from, log):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, (plan, profile) = plt.subplots(1, 2, figsize=(13, 11),
                                        gridspec_kw={"width_ratios": [1.4, 1]})
    x, y = strip.exterior.xy
    plan.fill(x, y, facecolor="0.92", edgecolor="0.5", linewidth=0.6)
    for interior in strip.interiors:
        plan.fill(*interior.xy, facecolor="white", edgecolor="0.5", linewidth=0.6)
    plan.plot(axis[:, 0], axis[:, 1], color="0.6", linewidth=0.8)
    for index in slots:
        plan.plot(axis[index, 0], axis[index, 1], "o", color="crimson", markersize=3)
    for number, (start, end) in enumerate(pools, start=1):
        middle = (start + end) // 2
        plan.annotate(str(number), (axis[middle, 0], axis[middle, 1]), fontsize=7,
                      ha="center", va="center", color="black")
    for row, geom in zip(rows, geoms):
        plan.plot(*geom.xy, color="tab:blue", linewidth=2.2)
        plan.annotate(f"{row['Name']} (US{row['pool']})",
                      (geom.centroid.x, geom.centroid.y), fontsize=8, color="tab:blue",
                      xytext=(6, 0), textcoords="offset points")
    plan.set_aspect("equal")
    plan.set_title(f"pools numbered from the {number_from} end\n"
                   f"red = slots, blue = the measured sections")

    profile.plot(widths, axis[:, 1], color="black", linewidth=0.8)
    profile.plot(widths[slots], axis[slots, 1], "o", color="crimson", markersize=3)
    profile.set_xlabel("free width [m]")
    profile.set_ylabel("y [m]")
    profile.grid(True, color="0.9")
    profile.set_title("width along the fish pass")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    log.info("wrote %s", path)


if __name__ == "__main__":
    sys.exit(main())
