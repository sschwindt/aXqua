"""The water surface elevation in each of the fourteen pools, by ONE agreed definition.

Two machines reported head-pool elevations 47 mm apart on the *same* result file, and
the difference was entirely in how the pool was sampled - 0% physics. That is the same
order as the mesh-convergence difference the comparison exists to detect, so it would
have been read as a result. Worse, lww-133 found they had quoted two different bands in
a single report: a head-pool figure from "everything with s < 4" and a per-pool step
figure from a proper band.

So the band is defined once, here, and both machines run this file rather than their
own understanding of it. `pool_levels` in `cases/slot-flume/slot_resistance_study.py`
is the convention adopted, lifted verbatim:

* **0.20 to 1.20 m upstream of the baffle face.** Close enough to be the head the slot
  sees, far enough to be out of its own jet and the recirculation behind it.
* **median, not mean.** A pool is a near-still body with a jet crossing it; the mean is
  pulled by the few cells in the jet. On `r2d-fill.slf` the mean is 2.2482 against the
  median 2.2383, so it matters here too.
* **inside the traced clear channel**, from `clear-channel.gpkg`. Without it a ball
  around the slot straddles the baffle and medians across TWO pools - the error that
  produced the 47 mm, where the two sides differed by 0.107 m against a 0.130 m design
  step.

Munich runs along the pass axis, so the band is in `s` rather than `x`.

Both the 2D SELAFIN and the 3D VOF result go through the SAME band, because that is
the only way the mesh-convergence comparison means anything. A VOF case has no "free
surface" field to read, so one is derived per plan column: the lattice is aXqua's own
structured grid of `n_columns x n_layers` cells ordered column-major (mesh.py line 392
ravels `(n_columns, n_layers+1)` vertex levels), so cell `i` belongs to column
`i // n_layers`. Column depth is the mass-conservative `sum(alpha * h)` with
`h = V / dx**2`, and the surface is the bed plus that depth - not the highest cell with
alpha > 0.5, which would quantise the answer to the layer thickness (~0.05 m here,
larger than the difference being measured).

    python cases/munich-vsf/measure_pool_levels.py                       # the 2D seed
    python cases/munich-vsf/measure_pool_levels.py <result.slf>
    python cases/munich-vsf/measure_pool_levels.py <openfoam-case-dir>   # VOF
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
STATIONS = HERE / "user-sources" / "geodata" / "baffle-stations.csv"
CHANNEL = HERE / "user-sources" / "geodata" / "clear-channel.gpkg"

#: The pass frame measure_baffle_stations.py reports.
ORIGIN = np.array([7.872073, 42.206112])
BEARING_DEG = 15.543955

POOL_NEAR = 0.20        #: [m] upstream of the baffle face
POOL_FAR = 1.20         #: [m] upstream of the baffle face


def along(xy):
    b = np.radians(BEARING_DEG)
    return (np.asarray(xy, dtype=float) - ORIGIN) @ np.array([np.sin(b), np.cos(b)])


def pool_levels(x, y, depth, surface, wet_depth, stations, channel=None):
    """Median free surface in each pool band. The one definition; do not re-invent it."""
    from shapely.geometry import Point
    from shapely.prepared import prep

    s = along(np.column_stack([x, y]))
    wet = depth > wet_depth
    inside = np.ones_like(wet)
    if channel is not None:
        ready = prep(channel)
        inside = np.fromiter(
            (ready.contains(Point(a, b)) for a, b in zip(x, y)), bool, len(x))
    out = []
    for _, r in stations.iterrows():
        band = wet & inside & (s > r.s - POOL_FAR) & (s < r.s - POOL_NEAR)
        out.append({
            "baffle": int(r.baffle),
            "s": float(r.s),
            "n": int(band.sum()),
            "wse": float(np.median(surface[band])) if band.any() else float("nan"),
            "mean": float(np.mean(surface[band])) if band.any() else float("nan"),
            "depth": float(np.median(depth[band])) if band.any() else float("nan"),
        })
    return out


def lattice_spacing(cx: np.ndarray, cy: np.ndarray) -> float:
    """The lattice pitch, measured from the column centres themselves.

    NOT taken from a config, and not guessed from the case-directory name. Every VOF
    variant of this case resolves to the same `openfoam_case_dir`, so the directory
    name does not say which `cell_size` built what is in it - reading 0.03 for a 0.045 m
    build scales the column area by 2.25 and every depth with it. The grid is uniform,
    so its own nearest-neighbour spacing is the authoritative answer.
    """
    from scipy.spatial import cKDTree

    d, _ = cKDTree(np.column_stack([cx, cy])).query(
        np.column_stack([cx, cy]), k=2)
    return float(np.median(d[:, 1]))


def read_vof_columns(case_dir: Path, n_layers: int):
    """Per-column (x, y, depth, surface) from a reconstructed interFoam time.

    Reuses `correct_lid._read_list`, which already handles `writeFormat binary` - the
    case writes binary, and converting it to ASCII to read one field would rewrite
    every field on disk.
    """
    sys.path.insert(0, str(HERE))
    from correct_lid import _LIST, _read_list

    times = sorted((float(p.name), p) for p in case_dir.iterdir()
                   if p.is_dir() and p.name.replace(".", "").isdigit()
                   and (p / "alpha.water").is_file())
    if not times:
        raise SystemExit(f"no reconstructed time with alpha.water in {case_dir} - "
                         "run `reconstructPar -latestTime` first")
    t, tdir = times[-1]

    def field(name):
        buf = (tdir / name).read_bytes()
        fmt = re.search(rb"format\s+(\w+)\s*;", buf[:2000])
        binary = bool(fmt) and fmt.group(1) == b"binary"
        at = buf.index(b"internalField")
        if _LIST.search(buf, at) is None:
            raise SystemExit(f"{name} has no nonuniform internalField list")
        vals, _ = _read_list(buf, at, binary)
        return vals

    alpha = field("alpha.water")
    cx, cy, cz, vol = (field(n) for n in ("Cx", "Cy", "Cz", "V"))

    if alpha.size % n_layers:
        raise SystemExit(f"{alpha.size} cells is not a multiple of n_layers "
                         f"{n_layers}; the column assumption does not hold")
    shape = (alpha.size // n_layers, n_layers)
    a = alpha.reshape(shape)
    cell_size = lattice_spacing(cx.reshape(shape)[:, 0], cy.reshape(shape)[:, 0])
    h = (vol / (cell_size * cell_size)).reshape(shape)
    z = cz.reshape(shape)
    depth = (a * h).sum(axis=1)
    bed = z[:, 0] - h[:, 0] / 2.0
    return (cx.reshape(shape)[:, 0], cy.reshape(shape)[:, 0],
            depth, bed + depth, t, shape, cell_size)


def main() -> None:
    import geopandas as gpd
    import pandas as pd

    from axqua import load_config
    from axqua.core import selafin

    cfg = load_config(HERE / "case-config-vof.yml")
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(cfg.model_dir) / cfg.results_slf)
    if not path.exists():
        raise SystemExit(f"no result at {path}")

    stations = pd.read_csv(STATIONS).sort_values("s")
    channel = None
    if CHANNEL.is_file():
        channel = gpd.read_file(CHANNEL).geometry.union_all()
    else:
        print(f"WARNING: {CHANNEL.name} missing - the band is NOT clipped to the "
              "channel,\n         so a pool near a bend may straddle its baffle")

    if path.is_dir():
        # A VOF case: the config whose cell_size built it decides the column area.
        x, y, depth, surface, t, shape, dx = read_vof_columns(
            path, cfg.openfoam.n_layers)
        label = (f"{path.name} t={t:g} s, {shape[0]:,} columns x {shape[1]} layers, "
                 f"lattice {dx:.4f} m measured from the grid")
    else:
        r = selafin.read_slf(path)
        v = r["values"]
        x, y = np.asarray(r["x"]), np.asarray(r["y"])
        depth = np.asarray(v["WATER DEPTH"])
        surface = np.asarray(v["FREE SURFACE"])
        label = path.name

    rows = pool_levels(x, y, depth, surface,
                       cfg.hydrodynamics.wet_depth, stations, channel)

    print(f"{label}, band {POOL_NEAR}-{POOL_FAR} m upstream of each baffle, "
          "median, inside the clear channel")
    print(f"\n{'#':>2} {'s':>7} {'WSE':>8} {'mean':>8} {'depth':>7} {'nodes':>6} "
          f"{'step':>7}")
    prev = None
    for row in rows:
        step = "" if prev is None else f"{prev - row['wse']:+7.4f}"
        print(f"{row['baffle']:>2} {row['s']:7.3f} {row['wse']:8.4f} "
              f"{row['mean']:8.4f} {row['depth']:7.3f} {row['n']:6d} {step:>7}")
        prev = row["wse"]

    wse = np.array([r0["wse"] for r0 in rows])
    steps = -np.diff(wse)
    print(f"\nhead-pool WSE {wse[0]:.4f} m      <- the comparison number")
    print(f"step per pool: median {np.median(steps):.4f} m, "
          f"range {steps.min():+.4f}..{steps.max():+.4f} (drawn 0.1297)")
    print(f"total fall over 13 pools {wse[0] - wse[-1]:.4f} m")


if __name__ == "__main__":
    main()
