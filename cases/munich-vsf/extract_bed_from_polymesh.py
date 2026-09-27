"""Lift a patch of bed out of the reference model's mesh, where our CAD has a hole.

`dem-from-surfaces.tif` has **no bed** from about s = 26.25 m along the pass, between
the pass mouth and where the exit basin begins. That is a real gap in the CAD, and
`surface_stage.py` does not fail on it: the ROI is the raster coverage of the bed
parts, and `_largest_part` then keeps the biggest polygon, so a coverage hole silently
truncates the domain instead of raising. The model still meshes, still runs, still
balances - it simply has no outlet.

Federica's model ran with a working outlet, so its mesh carries the bed that ours is
missing. This lifts it out, in the same form as `user-sources/reference/
federica-wetted-bed.csv` - the lowest wetted boundary face per plan cell, with the
patch it belongs to - but over an arbitrary window and at an arbitrary resolution,
because 0.25 m is too coarse to rebuild a 0.4 m gap with.

Read straight out of `constant/polyMesh` by face index: no OpenFOAM environment to
source, no case to be valid, and it works on a case that would not run.

    python cases/munich-vsf/extract_bed_from_polymesh.py                # the outlet gap
    python cases/munich-vsf/extract_bed_from_polymesh.py 13.5 15.5 66.5 69.5 0.05
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MESH = Path("/home/modelling/OpenFOAM/Munich-VSF/6_v5_HQ100/constant/polyMesh")
OUT = HERE / "user-sources" / "reference" / "federica-bed-outlet.csv"

#: The pass outlet, where our CAD runs out of bed. The pass mouth is near
#: (14.2, 67.9) local and `Substratum_final` starts at x 14.6.
BOX = (13.5, 15.5, 66.5, 69.5)
RES = 0.05

#: Patches that are BED. Named rather than inferred: `Stahlbeton_*` and `Stahlblech`
#: are structure, and a wall's own face is not bed even where it is the lowest thing
#: in a column. `case-config.yml` makes the same distinction with `role:`.
BED_PREFIXES = ("Substratum", "Magerbeton")


def read_points(path):
    """The `points` vector list, as (n, 3)."""
    out = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("("):
                break
        for line in fh:
            if not line.startswith("("):
                break
            out.append(line[1:line.rindex(")")].split())
    return np.asarray(out, dtype=float)


def boundary_patches(mesh):
    """Every boundary patch as (name, startFace, nFaces), in file order."""
    txt = (mesh / "boundary").read_text()
    out = []
    for m in re.finditer(r"(\w+)\s*\{(.*?)\}", txt, re.S):
        name, block = m.group(1), m.group(2)
        n = re.search(r"nFaces\s+(\d+)", block)
        s = re.search(r"startFace\s+(\d+)", block)
        if n and s:
            out.append((name, int(s.group(1)), int(n.group(1))))
    return out


def read_faces_from(mesh, first):
    """Every face from index *first* to the end, as lists of point indices.

    One pass. Reading each patch separately re-walks 5.5 M lines per patch, which on
    this mesh is minutes per call for no reason.
    """
    faces = []
    with open(mesh / "faces") as fh:
        for line in fh:
            if line.startswith("("):
                break
        i = 0
        for line in fh:
            if "(" not in line:
                continue
            if i >= first:
                faces.append([int(v) for v in
                              line[line.index("(") + 1: line.rindex(")")].split()])
            i += 1
    return faces


def main() -> None:
    args = sys.argv[1:]
    box = tuple(float(v) for v in args[:4]) if len(args) >= 4 else BOX
    res = float(args[4]) if len(args) >= 5 else RES
    x0, x1, y0, y1 = box

    if not MESH.is_dir():
        raise SystemExit(f"reference mesh not on this machine: {MESH}")

    print(f"reading {MESH}")
    points = read_points(MESH / "points")
    patches = boundary_patches(MESH)
    bed = [p for p in patches if p[0].startswith(BED_PREFIXES)]
    if not bed:
        raise SystemExit("no bed patches found - check BED_PREFIXES against boundary")
    first = min(s for _, s, _ in bed)
    faces = read_faces_from(MESH, first)
    print(f"  {len(points):,} points, {len(faces):,} faces from index {first:,}")
    print("  bed patches: " + ", ".join(f"{n} ({c:,})" for n, _, c in bed))

    # lowest bed face per plan cell, and which patch it came from
    nx = int(np.ceil((x1 - x0) / res))
    ny = int(np.ceil((y1 - y0) / res))
    best = np.full((ny, nx), np.inf)
    who = np.full((ny, nx), -1, dtype=int)
    names = [n for n, _, _ in bed]
    for k, (name, start, count) in enumerate(bed):
        sl = slice(start - first, start - first + count)
        for f in faces[sl]:
            p = points[f]
            cx, cy, cz = p.mean(axis=0)
            if not (x0 <= cx < x1 and y0 <= cy < y1):
                continue
            j = int((cx - x0) / res)
            i = int((cy - y0) / res)
            if cz < best[i, j]:
                best[i, j] = cz
                who[i, j] = k

    filled = np.isfinite(best)
    print(f"\nwindow x {x0}..{x1}, y {y0}..{y1} at {res} m: "
          f"{filled.sum():,} of {filled.size:,} cells have bed "
          f"({100 * filled.mean():.1f}%)")
    if not filled.any():
        raise SystemExit("her mesh does NOT cover this window either - the outlet is "
                         "outside every model we have")

    rows = []
    for i, j in zip(*np.nonzero(filled)):
        rows.append({"x": round(x0 + (j + 0.5) * res, 4),
                     "y": round(y0 + (i + 0.5) * res, 4),
                     "bed_z": round(float(best[i, j]), 4),
                     "patch": names[who[i, j]]})
    rows.sort(key=lambda r: (r["y"], r["x"]))

    from collections import Counter
    for name, n in Counter(r["patch"] for r in rows).most_common():
        zs = [r["bed_z"] for r in rows if r["patch"] == name]
        print(f"  {name:26s} {n:6,d} cells   z {min(zs):.3f}..{max(zs):.3f}")

    # Is it continuous ALONG the pass? That is the question the gap raises.
    print(f"\ncoverage by row, y from {y0} to {y1} "
          f"(# = bed, . = none), x {x0} to {x1}:")
    for i in range(ny - 1, -1, -1):
        if i % max(1, int(round(0.25 / res))):
            continue
        line = "".join("#" if filled[i, j] else "." for j in range(nx))
        print(f"  y={y0 + (i + 0.5) * res:6.2f}  {line}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["x", "y", "bed_z", "patch"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {OUT}  ({len(rows):,} rows, same schema as "
          "federica-wetted-bed.csv)")


if __name__ == "__main__":
    main()
