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

    python cases/munich-vsf/measure_pool_levels.py                       # the 2D seed
    python cases/munich-vsf/measure_pool_levels.py <result.slf>
"""

from __future__ import annotations

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


def main() -> None:
    import geopandas as gpd
    import pandas as pd

    from axqua import load_config
    from axqua.core import selafin

    cfg = load_config(HERE / "case-config-vof.yml")
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(cfg.model_dir) / cfg.results_slf)
    if not path.is_file():
        raise SystemExit(f"no result at {path}")

    stations = pd.read_csv(STATIONS).sort_values("s")
    channel = None
    if CHANNEL.is_file():
        channel = gpd.read_file(CHANNEL).geometry.union_all()
    else:
        print(f"WARNING: {CHANNEL.name} missing - the band is NOT clipped to the "
              "channel,\n         so a pool near a bend may straddle its baffle")

    r = selafin.read_slf(path)
    v = r["values"]
    rows = pool_levels(np.asarray(r["x"]), np.asarray(r["y"]),
                       np.asarray(v["WATER DEPTH"]),
                       np.asarray(v["FREE SURFACE"]),
                       cfg.hydrodynamics.wet_depth, stations, channel)

    print(f"{path.name}, band {POOL_NEAR}-{POOL_FAR} m upstream of each baffle, "
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
