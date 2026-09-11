"""Correct the rigid lid from its own pressure, and write the seed for the next pass.

Under ``openfoam.mode: rigid-lid`` the mesh stops at the converged 2D free surface and
there is no air phase at all - which is what makes the run finish in hours instead of
weeks (see README.md, "The air phase was the whole cost"). The price is that the free
surface becomes an INPUT: the 3D flow cannot raise it over a slot, draw it down through
one, or superelevate it round a bend.

**It can, however, say how badly it wanted to.** A rigid lid is a slip wall, so the flow
presses against it, and the pressure it presses with is exactly the elevation the
surface would have taken if the lid had let it::

    dz = p_rgh / (rho g)          [m]

positive where the water wanted to stand higher. Feed ``surface + dz`` back in as the
seed, rebuild, and run again: the lid moves to where the flow asked for it, the pressure
it presses with falls, and after two or three passes the surface is a RESULT - obtained
without ever meshing a single cell of air.

``max|dz|`` is worth recording whether or not another pass is run. It is the honest
error bar on a prescribed-surface result: a run whose lid is wrong by 2 mm is telling
you something different from one whose lid is wrong by 200.

What this pass costs, stated rather than buried: the corrected seed is 2D, so the next
build starts from a depth-averaged velocity instead of the TELEMAC-3D profile the first
one had. Under a rigid lid that matters far less than it did - there is no interface to
destabilise and the profile redevelops in the first seconds - but it is a real
difference from pass 1 and the reported velocities of a two-pass run are not seeded
identically to a one-pass run.

Run::

    mamba run -n axqua-env python cases/munich-vsf/correct_lid.py
    mamba run -n axqua-env python cases/munich-vsf/correct_lid.py --time 60

then rebuild from the file it names::

    python openfoam_preprocessing.py        # picks up openfoam.hotstart if set, or
    axqua openfoam case-config.yml --hotstart axqua-case/simulation/r2d-lid1.slf
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np

from axqua.config import load_config
from axqua.core.selafin import _write_selafin, read_slf
from axqua.solvers.openfoam.hotstart import load_hotstart
from axqua.solvers.openfoam.mesh import LID_PATCH, build_mesh

HERE = Path(__file__).resolve().parent
G = 9.81

# A lid correction larger than this is not a lid correction: it means the 2D surface and
# the 3D one disagree about the flow, not about the free surface, and re-seeding would
# chase a difference the geometry cannot absorb.
IMPLAUSIBLE = 0.5                       # [m]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("config", nargs="?", default="case-config.yml")
    p.add_argument("--time", default=None,
                   help="time directory to read (default: the latest)")
    p.add_argument("--out", default=None,
                   help="output SELAFIN (default: r2d-lid<n>.slf beside the result)")
    p.add_argument("--seed", default=None,
                   help="the seed the case was BUILT from, so this rebuilds the same "
                        "mesh (default: r3d-hydrostatic.slf, else r2d.slf)")
    p.add_argument("--dry-run", action="store_true",
                   help="report the correction without writing the seed")
    return p.parse_args(argv)


def latest_time(case_dir: Path) -> Path:
    """The largest numeric time directory holding a reconstructed ``p_rgh``."""
    times = []
    for entry in case_dir.iterdir():
        try:
            t = float(entry.name)
        except ValueError:
            continue
        if entry.is_dir() and (entry / "p_rgh").exists():
            times.append((t, entry))
    if not times:
        raise SystemExit(
            f"no reconstructed time directory with a p_rgh in {case_dir}. The run "
            "decomposes over 16 ranks, so run `reconstructPar -latestTime` first (or "
            "let openfoam_run.py finish, which reconstructs for you).")
    return max(times)[1]


_LIST = re.compile(rb"nonuniform\s+List<scalar>\s*\n?\s*(\d+)\s*\n?\s*\(")
_UNIFORM = re.compile(rb"uniform\s+([-0-9.eE+]+)\s*;")


def _read_list(buf: bytes, at: int, binary: bool) -> tuple[np.ndarray, int]:
    """``(values, index just past the closing paren)`` for the scalar list at *at*.

    ``writeFormat binary`` puts raw native-endian doubles between the parentheses, so
    the list cannot be found by searching for ``)`` - the payload contains that byte
    often enough. The count is read first and the payload length computed from it,
    which is also what makes this safe to walk past ``internalField`` on the way to
    ``boundaryField``.
    """
    m = _LIST.search(buf, at)
    if m is None:
        return np.zeros(0), at
    n = int(m.group(1))
    if binary:
        start = m.end()
        vals = np.frombuffer(buf[start:start + 8 * n], dtype="<f8")
        return vals, start + 8 * n + 1
    end = buf.index(b")", m.end())
    return np.fromstring(buf[m.end():end].decode("ascii"), sep="\n"), end + 1


def read_patch_values(field: Path, patch: str, n_faces: int) -> np.ndarray:
    """The ``boundaryField`` values of *patch*, as an ``(n_faces,)`` array.

    Reads both ``writeFormat ascii`` and ``binary``; the case writes binary, which is
    the default worth supporting since converting the case to ASCII to read one patch
    would rewrite every field on disk.

    A uniform entry is expanded rather than special-cased at the call site: a lid the
    flow never touches writes ``value uniform 0``, and that is a real, meaningful
    answer (the correction is zero) rather than a parse failure.
    """
    buf = field.read_bytes()
    # From the FoamFile header, not a fixed slice: the OpenFOAM banner is ~700 bytes on
    # its own, so anything short enough to be safe misses the keyword entirely.
    fmt = re.search(rb"format\s+(\w+)\s*;", buf[:2000])
    binary = bool(fmt) and fmt.group(1) == b"binary"

    # Step over internalField explicitly. Searching for "boundaryField" directly would
    # be searching 6 MB of raw doubles for a byte pattern that can occur inside them.
    at = buf.index(b"internalField")
    _, at = _read_list(buf, at, binary) if _LIST.search(buf, at) else (None, at)

    at = buf.index(b"boundaryField", at)
    at = buf.index(patch.encode(), at)
    head = buf[at:at + 400]
    if _LIST.search(head):
        vals, _ = _read_list(buf, at, binary)
        if vals.size != n_faces:
            raise SystemExit(
                f"{field.name}: patch {patch} has {n_faces} faces but the field lists "
                f"{vals.size}. The mesh and the result are not the same case.")
        return vals
    uniform = _UNIFORM.search(head)
    if uniform:
        return np.full(n_faces, float(uniform.group(1)))
    raise SystemExit(f"{field.name}: could not read a value entry for patch {patch}")


def face_centres(pm, patch) -> np.ndarray:
    """Centres of one patch's faces, as the mean of their four corners.

    The exact (fan-decomposed) centroid is not needed here - these are only used to
    match a lid face to the plan column below it, and the columns are 3 cm apart.
    """
    quads = pm.faces[patch.start_face:patch.start_face + patch.n_faces]
    return pm.points[quads].mean(axis=1)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(HERE / args.config)
    if cfg.openfoam.mode != "rigid-lid":
        raise SystemExit(
            f"openfoam.mode is {cfg.openfoam.mode!r}. There is nothing to correct: a "
            "two-phase run solves its own free surface.")

    case_dir = cfg.openfoam_case_dir
    time_dir = case_dir / args.time if args.time else latest_time(case_dir)
    print(f"reading the lid pressure from {time_dir}")
    if float(time_dir.name) == 0.0:
        # p_rgh in 0/ is the initial condition - uniform zero on the lid by
        # construction - so a zero correction here means "nothing has been written
        # yet", not "the lid was already right". Those look identical in the output.
        raise SystemExit(
            "that is the INITIAL condition, where p_rgh is uniform 0 on the lid: it "
            "would report a zero correction whatever the flow does. Let the run write "
            "a time directory and reconstruct it (reconstructPar -latestTime) first.")

    # The same config and the same seed build the same mesh, so this recovers the
    # lid-face-to-column mapping the run was built with without having stored it - and
    # fails loudly, on the face count, if either has moved underneath the result.
    sim = HERE / "axqua-case" / "simulation"
    seed_path = Path(args.seed) if args.seed else next(
        (p for p in (sim / "r3d-hydrostatic.slf", sim / "r2d.slf") if p.exists()), None)
    if seed_path is None:
        raise SystemExit(f"no seed to rebuild the mesh from in {sim}")
    print(f"rebuilding the mesh from {seed_path.name} to recover the lid mapping")
    state = load_hotstart(cfg, seed_path)
    mesh = build_mesh(cfg, state=state)
    patch = mesh.polymesh.patch(LID_PATCH)
    print(f"  lid patch: {patch.n_faces:,} faces over {mesh.grid.n_columns:,} columns")

    # `p`, not `p_rgh`. OpenFOAM's p_rgh carries the hydrostatic head of the cell's own
    # elevation (p_rgh = p - rho*(g & C)), so p_rgh/(rho g) on a correctly placed lid
    # returns the lid's ELEVATION, not its error - it correlated with z at 0.977 here
    # before this was noticed. The gauge pressure p is zero at a free surface by
    # definition, so p/(rho g) is the deviation directly.
    field = time_dir / "p"
    if not field.exists():
        raise SystemExit(
            f"{field} was not written. Add `p` to controlDict's writeObjects, or "
            "reconstruct it - p_rgh alone cannot give the deviation without undoing "
            "the hydrostatic term cell by cell.")
    dz_face = read_patch_values(field, LID_PATCH, patch.n_faces) / (
        cfg.openfoam.water_density * G)

    # Lid face -> plan column, by position rather than by the order the patch happens to
    # have been assembled in. One face per column, 3 cm apart, so the match is exact.
    from scipy.spatial import cKDTree
    tree = cKDTree(mesh.grid.cell_xy)
    _, col = tree.query(face_centres(mesh.polymesh, patch)[:, :2])
    dz_col = np.zeros(mesh.grid.n_columns)
    dz_col[col] = dz_face

    print(f"\nlid pressure as an elevation, over {patch.n_faces:,} lid faces:")
    for q in (1, 25, 50, 75, 99):
        print(f"  p{q:<2} {np.percentile(dz_face, q):+.4f} m")
    peak = float(np.abs(dz_face).max())
    print(f"  max |dz| {peak:.4f} m   rms {float(np.sqrt(np.mean(dz_face ** 2))):.4f} m")

    if peak > IMPLAUSIBLE:
        print(f"\n! max |dz| is {peak:.2f} m, over the {IMPLAUSIBLE:g} m this script "
              "treats as a lid correction. That is not a surface the lid got slightly "
              "wrong - check the run converged, and check the outlet and inlet before "
              "re-seeding from this.")

    # Onto the 2D nodes. The 2D mesh is coarser than the 3D lattice, so several lid
    # faces fall in one 2D node's neighbourhood; nearest-node is enough at this ratio
    # and does not invent a gradient the lid does not have. Nodes further than a few
    # cells from any column are outside the sub-model - the 2D reach extends 26 m
    # upstream and 33 m downstream of it - and keep the surface the 2D run gave them.
    src = read_slf(sim / "r2d.slf")
    x2, y2 = src["x"], src["y"]
    dist, near = tree.query(np.column_stack([x2, y2]))
    dz_node = np.where(dist < 3.0 * cfg.openfoam.cell_size, dz_col[near], 0.0)

    values = src["values"]

    def pick(*names):
        for n in names:
            for key, arr in values.items():
                if key.upper().startswith(n):
                    return np.asarray(arr, dtype=float)
        return None

    bottom = pick("BOTTOM", "FOND")
    depth = pick("WATER DEPTH", "HAUTEUR")
    surface = pick("FREE SURFACE", "SURFACE LIBRE")
    if bottom is None or (depth is None and surface is None):
        raise SystemExit(
            f"r2d.slf carries {src['var_names']}; a corrected seed needs BOTTOM plus "
            "either WATER DEPTH or FREE SURFACE.")
    if surface is None:
        surface = bottom + depth
    u, v = pick("VELOCITY U"), pick("VELOCITY V")
    if u is None:
        u = v = np.zeros_like(bottom)

    new_surface = surface + dz_node
    new_depth = np.maximum(new_surface - bottom, 0.0)
    moved = int((np.abs(dz_node) > 1e-4).sum())
    print(f"\n{moved:,} of {x2.size:,} 2D nodes moved by more than 0.1 mm "
          f"(max {float(np.abs(dz_node).max()):.4f} m)")

    if args.dry_run:
        print("\n--dry-run: no seed written")
        return 0

    out = Path(args.out) if args.out else _next_name(HERE / "axqua-case" / "simulation")
    _write_selafin(
        out, x2, y2, np.asarray(src["ikle"]), src["ipobo"],
        [("VELOCITY U", "M/S", u), ("VELOCITY V", "M/S", v),
         ("WATER DEPTH", "M", new_depth), ("FREE SURFACE", "M", new_surface),
         ("BOTTOM", "M", bottom)],
        title="axqua lid-corrected 2D state")
    print(f"\nwrote {out}")
    print("rebuild from it with:")
    print(f"  axqua openfoam {args.config} --hotstart {out}")
    print("then record max|dz| for this pass in README.md - it is the error bar on the "
          "prescribed surface, not just a step in the loop.")
    return 0


def _next_name(sim_dir: Path) -> Path:
    """``r2d-lid1.slf``, ``r2d-lid2.slf``, ... so the passes stay on the record."""
    n = 1
    while (sim_dir / f"r2d-lid{n}.slf").exists():
        n += 1
    return sim_dir / f"r2d-lid{n}.slf"


if __name__ == "__main__":
    sys.exit(main())
