"""How much of a throat does each blanking rule eat, and does it still seal?

`structures.apply_to_bed(triangles=)` raises every node of every element a wall
crosses. That is safe by construction - a crossing path must use an element the wall
touches - but it costs up to one element of width on each side of an opening. On a
0.380 m slot that is tolerable; on the Munich pass's real 0.170 m throat at dx 0.035 it
leaves under two elements, which is the resolution question lww-134 raised.

This measures the alternatives on the flume, geometrically - no solver, seconds per
mesh. Two numbers per rule, and both matter:

* **the realised throat**, against the 0.1697 m drawn one;
* **whether it still seals**, tested independently of the rule: for every pair of
  adjacent UNBLOCKED elements, does the segment joining their centroids cross a wall?
  If any does, water has a path through solid concrete. This is the guarantee the
  `touch` rule buys, and no rule that loses it is admissible however little it erodes.

    python cases/slot-flume/blanking_rules.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_geodata as design                                   # noqa: E402

from axqua.config import load_config                            # noqa: E402
from axqua.core.structures import load_structures               # noqa: E402

LEVELS = (0.10, 0.05, 0.035, 0.025)


# --------------------------------------------------------------------------- #
# the rules
# --------------------------------------------------------------------------- #

def rule_node(wall, xy, tri):
    """Pre-3de95ec: raise the nodes inside the footprint. Known to leak."""
    import shapely
    inside = shapely.contains_xy(wall, xy[:, 0], xy[:, 1])
    return inside[tri].all(axis=1)


def rule_touch(wall, xy, tri):
    """Current: raise every element the footprint intersects at all."""
    import shapely
    return shapely.intersects(wall, shapely.polygons(xy[tri]))


def rule_area(wall, xy, tri, fraction=0.5):
    """lww-134's suggestion: only when the wall really covers the element."""
    import shapely
    cells = shapely.polygons(xy[tri])
    near = shapely.intersects(wall, cells)
    out = np.zeros(len(tri), bool)
    if near.any():
        part = shapely.intersection(wall, cells[near])
        out[near] = shapely.area(part) >= fraction * shapely.area(cells[near])
    return out


def rule_separating(wall, xy, tri):
    """Raise an element only when the wall SEPARATES it.

    The physical requirement is that no element carries flow from one side of a wall
    to the other. An element the wall merely clips at a corner does not: the wall does
    not reach across it, so `element - wall` is still one piece and any path through
    the element stays on one side. An element the wall cuts right through breaks into
    two, and that is exactly the element that must be blocked.

    So the test is whether `element - wall` is disconnected (or empty, the element
    being buried in the wall). It is the same guarantee as `touch`, stated on what
    actually matters, and it declines to raise the corner-grazed elements that make a
    narrow throat narrower.
    """
    import shapely
    cells = shapely.polygons(xy[tri])
    near = shapely.intersects(wall, cells)
    out = np.zeros(len(tri), bool)
    idx = np.flatnonzero(near)
    if idx.size:
        rest = shapely.difference(cells[idx], wall)
        pieces = shapely.get_num_geometries(rest)
        empty = shapely.is_empty(rest)
        out[idx] = empty | (pieces > 1)
    return out


def centrelines(structures):
    """The long axis of each footprint - what a crossing path must cross."""
    import shapely

    lines = []
    for s in structures:
        rect = np.asarray(s.polygon.minimum_rotated_rectangle.exterior.coords)[:4]
        sides = [(rect[i], rect[(i + 1) % 4]) for i in range(4)]
        sides.sort(key=lambda ab: np.hypot(*(ab[1] - ab[0])))
        (a1, b1), (a2, b2) = sides[2], sides[3]
        lines.append(shapely.linestrings([(a1 + b2) / 2, (b1 + a2) / 2]))
    return shapely.union_all(lines)


def rule_centreline(wall, xy, tri, structures=None):
    """Block only the elements the wall's CENTRELINE crosses.

    Sound for the same reason `touch` is, but on a smaller set: any path from one side
    of a wall to the other must cross its centreline, so blocking the elements the
    centreline passes through cuts every such path. A centreline is a curve, so it
    touches a single chain of elements - about one element wide - where the full
    footprint touches everything within `thickness + 2 dx`.

    What it gives up is volume, not integrity: elements straddling the wall's faces
    stay open, so a little water sits where concrete is. On a 0.15 m wall that is a
    band a fraction of an element wide, and it is the opposite trade from eroding the
    opening the structure exists to create.
    """
    import shapely

    if not structures:
        return np.zeros(len(tri), bool)
    return np.asarray(shapely.intersects(centrelines(structures),
                                         shapely.polygons(xy[tri])))


RULES = {"node (pre-fix)": rule_node, "touch (current)": rule_touch,
         "area >= 50%": rule_area, "separating": rule_separating,
         "centreline": rule_centreline}


# --------------------------------------------------------------------------- #
# the two measurements
# --------------------------------------------------------------------------- #

def leaks(wall, spine, xy, tri, blocked) -> tuple[int, int]:
    """``(through, into)`` - the two ways a blanking rule can be wrong.

    Both are measured on the same object, the segment joining the centroids of two
    adjacent UNBLOCKED elements, and they are not the same failure:

    * **through** - the segment crosses the wall's CENTRELINE, so water gets from one
      side of the structure to the other. Fatal: the structure is not a structure.
    * **into** - the segment crosses the wall's footprint but not its centreline, so
      water stands inside the concrete without passing through it. A volume error,
      and a small one if it is a sliver along a face.

    Keeping them apart is what makes the comparison mean anything. A rule that erodes
    less than `touch` does so by leaving elements open that straddle a wall face, and
    then the only question is whether those elements also chain across.
    """
    import shapely

    edges = defaultdict(list)
    for e, (a, b, c) in enumerate(tri):
        if blocked[e]:
            continue
        for u, v in ((a, b), (b, c), (c, a)):
            edges[(min(u, v), max(u, v))].append(e)
    cent = xy[tri].mean(axis=1)
    pairs = [(v[0], v[1]) for v in edges.values() if len(v) == 2]
    if not pairs:
        return 0, 0
    lines = shapely.linestrings([[cent[i], cent[j]] for i, j in pairs])
    into = np.asarray(shapely.intersects(wall, lines))
    through = np.asarray(shapely.intersects(spine, lines))
    return int(through.sum()), int((into & ~through).sum())


def throat(xy, tri, blocked, structures) -> float:
    """Median realised throat, measured AT each baffle rather than globally.

    Per baffle: union the blocked elements belonging to it, do the same for its slot
    block, and take the distance between the two - the same corner-to-corner diagonal
    the drawing gives, which no ray across the channel can see.

    Measuring the global minimum distance between blocked bodies instead looks
    simpler and is wrong: a rule that leaves a hole inside one wall produces two
    fragments millimetres apart somewhere irrelevant, and the throat then reads zero
    while every opening is fine. That is not hypothetical - `area >= 50%` does it at
    dx 0.025.
    """
    import shapely

    cells = shapely.polygons(xy[tri][blocked])
    if not len(cells):
        return float("nan")
    centres = xy[tri][blocked].mean(axis=1)
    baffles = [s for s in structures if s.name.startswith("baffle")]
    blocks = {s.name.split("-")[1]: s for s in structures
              if s.name.startswith("block")}

    # Each blocked element belongs to the structure it is nearest to. Unambiguous,
    # and it keeps a baffle's blanket from swallowing its own slot block - which a
    # radius around the footprint does, making every throat read zero.
    owners = np.argmin(np.stack([
        shapely.distance(shapely.points(centres), s.polygon) for s in structures]),
        axis=0)
    by_owner = {}
    for i, s in enumerate(structures):
        sel = owners == i
        if sel.any():
            by_owner[s.name] = shapely.union_all(cells[sel])

    gaps = []
    for baffle in baffles:
        block = blocks.get(baffle.name.split("-")[1])
        a = by_owner.get(baffle.name)
        b = by_owner.get(block.name) if block is not None else None
        if a is None or b is None or a.is_empty or b.is_empty:
            continue
        gaps.append(a.distance(b))
    return float(np.median(gaps)) if gaps else float("nan")


def main() -> None:
    import shapely

    cfg = load_config(Path(__file__).resolve().parent / "case-config.yml")
    structures = load_structures(cfg)
    wall = shapely.union_all([s.polygon for s in structures])
    print(f"{len(structures)} structures; drawn throat {design.THROAT:.4f} m "
          f"(baffle tip to slot block), gap to the far wall "
          f"{design.SLOT_WIDTH:.3f} m\n")

    from axqua import mesh as meshmod
    import copy

    spine = centrelines(structures)
    header = (f"{'dx':>7} {'rule':<18} {'raised':>7} {'throat':>9} {'% drawn':>8} "
              f"{'THROUGH':>8} {'into':>6}")
    print(header)
    print("-" * len(header))
    for dx in LEVELS:
        lc = copy.deepcopy(cfg)
        lc.mesh.size_scale = dx / cfg.mesh.default_size
        m = meshmod.build_mesh(lc)
        xy = np.column_stack([m.x, m.y])
        tri = np.asarray(m.triangles)
        for name, rule in RULES.items():
            kw = {"structures": structures} if name == "centreline" else {}
            blocked = np.asarray(rule(wall, xy, tri, **kw))
            t = throat(xy, tri, blocked, structures)
            through, into = leaks(wall, spine, xy, tri, blocked)
            print(f"{dx:>7.3f} {name:<18} {blocked.sum():>7} {t:>9.4f} "
                  f"{100 * t / design.THROAT:>7.0f}% {through:>8} {into:>6}")
        print()


if __name__ == "__main__":
    main()
