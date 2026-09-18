"""Write the OpenFOAM sub-model crop as a REACH-ALIGNED box, not an axis-aligned one.

Why this exists
---------------
``make_openfoam_roi.py`` cuts the domain with a y-normal window, which is correct only
if the reach runs along y. This one does not: measured on the wetted corridor of the
pass, its axis is **+14.3 deg** off the lattice y-axis, and the channel's centre moves
+4.1 m in x between y = 50 and y = 65 while staying a constant 3.6 m wide.

A y-normal face across that is a long diagonal slice rather than a cross-section, and
it shows: cropping to y 54-59 produced faces carrying 0.18 m3/s against a prescribed
0.135, with the free surface apparently falling 1.54 m in 5 m. Neither is real; both
are the face being oblique.

So this script takes the crop window as a rectangle **aligned with the reach**:

* the long edges run along the reach axis, so the walls follow the channel;
* the short edges are **perpendicular to the flow**, so they are true cross-sections
  and the prescribed Q and stage mean what they say;
* their along-reach position is chosen to sit in the quiet approach and exit rather
  than beside a slot, so the boundaries perturb the jets as little as possible.

It also writes the reach axis as a **centerline**, which is the input
``geodata.channel_centerline`` has been missing. With it, ``flow_angle`` stops
returning 0 and ``openfoam.align_to_flow`` rotates the lattice onto the reach - which
matters for more than tidiness: a slot 0.1 m wide crossing the grid at 14 deg is
stair-stepped, and every cell spent on the stair is a cell not spent on the jet.

Run::

    mamba run -n axqua-env python cases/munich-vsf/make_rotated_roi.py
    mamba run -n axqua-env python cases/munich-vsf/make_rotated_roi.py --along -16 16
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Polygon

from axqua import setup_logging
from axqua.config import load_config
from axqua.solvers.openfoam.hotstart import load_hotstart

HERE = Path(__file__).resolve().parent

# The pool-and-slot reach, used only to find the axis. Chosen to exclude the wide
# approach and exit, whose own orientation differs from the pass.
AXIS_BAND = (46.0, 68.0)

# Along-reach extent of the crop, in metres from the centre of that band. The default
# is the window the user marked up: faces in the quiet water above and below the pass,
# which is what keeps them from disturbing the slots.
DEFAULT_ALONG = (-16.0, 16.0)


def reach_axis(state, band=AXIS_BAND):
    """``(origin, along, across)`` unit vectors of the reach, from the wetted corridor."""
    wet = state.depth > 0.05
    x, y = state.x[wet], state.y[wet]
    sel = (y > band[0]) & (y < band[1])
    pts = np.column_stack([x[sel], y[sel]])
    origin = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - origin, full_matrices=False)
    along = vt[0] if vt[0][1] > 0 else -vt[0]
    return origin, along, np.array([-along[1], along[0]])


def face_state(state, origin, along, across, s, half_width, *, ds=0.02):
    """Depth-averaged state on a reach-NORMAL face at along-reach station *s*.

    The discharge is a **quadrature along the face**, sampled at a uniform ``ds``,
    not a mean over whatever mesh nodes happen to fall in a band. That distinction is
    not pedantic here: the 2D nodes cluster around the structures, where the water is
    fastest, so an unweighted node mean over-weights the jets. Doing it that way first
    gave 0.18 m3/s in and 0.22 m3/s out on a converged steady run whose prescribed
    inflow is 0.135 - an imbalance that is arithmetic, not hydraulics.
    """
    n_pts = max(int(2 * half_width / ds), 8)
    offs = np.linspace(-half_width, half_width, n_pts)
    pts = origin + s * along + offs[:, None] * across[None, :]
    surface, depth, uv = state.sample_columns(pts)
    wet = np.isfinite(depth) & (depth > 0.05)
    if not wet.any():
        raise SystemExit(f"no water on the face at s = {s:+.1f} m")
    normal = uv[:, 0] * along[0] + uv[:, 1] * along[1]
    step = offs[1] - offs[0]
    q = float(np.nansum(np.where(wet, normal * depth, 0.0)) * step)
    return {
        "s": s, "wse": float(np.nanmean(surface[wet])),
        "depth": float(np.nanmean(depth[wet])),
        "width": float(wet.sum() * step), "q": q,
        "speed": float(np.nanmean(np.hypot(uv[wet, 0], uv[wet, 1]))),
        "n": int(wet.sum()),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", type=Path, default=HERE / "case-config.yml")
    p.add_argument("--along", nargs=2, type=float, default=DEFAULT_ALONG,
                   metavar=("S0", "S1"),
                   help="along-reach extent [m] from the pass centre "
                        "(default: %(default)s)")
    p.add_argument("--seed", type=Path, default=None,
                   help="2D/3D result to read the faces from (default: r2d.slf, the "
                        "CONVERGED one - r3d-hydrostatic.slf is not mass-consistent)")
    p.add_argument("--half-width", type=float, default=3.0,
                   help="half width of the crop [m] (default: %(default)s)")
    args = p.parse_args(argv)
    setup_logging(level=logging.INFO)
    log = logging.getLogger("axqua")

    cfg = load_config(args.config)
    pre = Path(cfg.preprocessing_dir)
    sim = HERE / "axqua-case" / "simulation"
    # r2d.slf by default, NOT the 3D pre-run: measured on these very faces, the 2D
    # result carries 0.1358 m3/s in and 0.1347 out against a prescribed 0.135, while
    # r3d-hydrostatic.slf carries 0.1838 in and 0.2215 out - 36% and 64% high, and not
    # conserving mass between its own faces. The 3D file is a 42 s cold start and has
    # not converged; prerun._judge cannot detect that for a 3D seed.
    seed = Path(args.seed) if args.seed else sim / "r2d.slf"
    state = load_hotstart(cfg, seed)
    log.info("faces read from %s", seed.name)

    origin, along, across = reach_axis(state)
    bearing = np.degrees(np.arctan2(along[0], along[1]))
    log.info("reach axis %+.2f deg from the lattice y-axis, through (%.2f, %.2f)",
             bearing, *origin)

    s0, s1 = args.along
    hw = args.half_width

    # -- the two faces, and whether they are quiet ---------------------------------
    faces = {}
    for label, s in (("inflow", s0), ("outflow", s1)):
        f = face_state(state, origin, along, across, s, hw)
        faces[label] = f
        log.info("%s face at s = %+.1f m: WSE %.3f m, depth %.3f m, width %.2f m, "
                 "Q %+.4f m3/s, |U| %.3f m/s (%d nodes)",
                 label, f["s"], f["wse"], f["depth"], f["width"], f["q"],
                 f["speed"], f["n"])

    # -- the crop polygon -----------------------------------------------------------
    corners = [origin + s * along + n * across
               for s, n in ((s0, -hw), (s1, -hw), (s1, hw), (s0, hw))]
    roi = gpd.GeoDataFrame({"name": ["fishpass"]}, geometry=[Polygon(corners)],
                           crs=f"EPSG:{cfg.crs_epsg}" if cfg.crs_epsg else None)
    roi_out = pre / "roi-fishpass.gpkg"
    roi.to_file(roi_out, driver="GPKG")
    log.info("wrote %s (%.1f m along the reach x %.1f m wide, %.1f m2)",
             roi_out.name, s1 - s0, 2 * hw, roi.geometry.area.iloc[0])

    # -- the two liquid boundaries, perpendicular to the flow -----------------------
    lines, names, qs = [], [], []
    for label, s in (("inflow", s0), ("outflow", s1)):
        a = origin + s * along - hw * across
        b = origin + s * along + hw * across
        lines.append(LineString([a, b]))
        names.append(label)
        qs.append(cfg.boundaries.prescribed_flowrate if label == "inflow" else None)
    bnd = gpd.GeoDataFrame({"name": names, "type": ["inflow", "outflow"],
                            "discharge": qs}, geometry=lines,
                           crs=roi.crs)
    bnd_out = pre / "liquid-boundaries-fishpass.gpkg"
    bnd.to_file(bnd_out, driver="GPKG")
    log.info("wrote %s (2 lines, perpendicular to the reach)", bnd_out.name)

    # -- the centerline, which is what unblocks align_to_flow -----------------------
    cl = gpd.GeoDataFrame(
        {"name": ["reach"]},
        geometry=[LineString([origin + s0 * along, origin + s1 * along])], crs=roi.crs)
    cl_out = pre / "channel-centerline.gpkg"
    cl.to_file(cl_out, driver="GPKG")
    log.info("wrote %s - set geodata.channel_centerline to it so the lattice "
             "rotates onto the reach", cl_out.name)

    print("\npaste into case-config.yml:\n")
    print("geodata:")
    print(f"  channel_centerline: {cl_out.name}")
    print("boundaries:")
    print(f"  prescribed_flowrate: {cfg.boundaries.prescribed_flowrate:g}")
    print(f"  prescribed_elevation: {faces['outflow']['wse']:.3f}")
    print("openfoam:")
    print("  roi: roi-fishpass.gpkg")
    print("  liquid_boundaries: liquid-boundaries-fishpass.gpkg")
    print(f"  outlet_stage: {faces['outflow']['wse']:.3f}")
    print("  align_to_flow: true")
    q_in = faces["inflow"]["q"]
    print(f"\nthe parent carries {q_in:+.4f} m3/s through the inflow face "
          f"(prescribed {cfg.boundaries.prescribed_flowrate:g}); "
          f"{abs(q_in / cfg.boundaries.prescribed_flowrate - 1) * 100:.0f}% apart")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
