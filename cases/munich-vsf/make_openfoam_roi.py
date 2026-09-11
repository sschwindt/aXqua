"""Cut the OpenFOAM domain down to the fish pass, as a sub-model of the 2D run.

The VOF run does not need the whole reach. Measured on the converged ``r2d.slf``, the
88 m domain is three parts and only the middle one is a 3D question:

===========  ========  =====================================================
y [m]        length    what it is
===========  ========  =====================================================
19 - 45      26 m      approach pool, flat at WSE 2.99, |v| ~ 0.10 m/s
**45 - 70**  **25 m**  **the pool-and-slot reach: WSE 2.99 -> 0.72, a 1.86 m drop**
70 - 107     37 m      exit channel, flat at WSE 0.72 -> 0.69, |v| ~ 0.29 m/s
===========  ========  =====================================================

Two thirds of the plan area is uniform channel flow that the depth-averaged model
already resolves and that interFoam would re-solve at roughly a day per metre. Cropping
to the middle takes the wetted footprint from 195.6 m2 to about 80 m2.

**This makes it a sub-model, and that is the part worth being careful about.** Both
liquid boundaries of the parent live at the extreme ends - the inflow at y 19.2, the
outflow at y 106.8 - so a crop removes them, and the sub-model needs its own. They are
not invented: this writes cross-reach lines at the crop faces and reads the discharge and
the free-surface elevation the *parent* model produced there, so the sub-model is driven
by the reach it was cut out of.

Writes into ``axqua-case/preprocessing/``:

* ``roi-fishpass.gpkg`` - the case ROI clipped to the crop window
* ``liquid-boundaries-fishpass.gpkg`` - the two new cross-reach lines

and prints the ``boundaries:`` block to paste into ``case-config.yml``.

    python make_openfoam_roi.py                    # the default window
    python make_openfoam_roi.py --from 40 --to 76  # a wider margin either side
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, box

from axqua.config import load_config
from axqua.core import selafin
from axqua.logsetup import setup_logging

HERE = Path(__file__).resolve().parent

#: The crop window [m in the case's local y]. The pool-and-slot reach is y 45-70; the
#: margins give the inflow somewhere to develop and keep the outflow face clear of the
#: last slot, whose jet must not sit on a boundary.
DEFAULT_FROM = 42.0
DEFAULT_TO = 74.0
#: A node counts as wet here, matching aXqua's min_depth rather than a numerical floor.
WET = 0.05


def reach_state(cfg, y0: float, y1: float, *, half_window: float = 0.75):
    """Depth, free surface and discharge the parent model carries across a station."""
    data = selafin.read_slf(Path(cfg.model_dir) / cfg.results_slf)
    x, y = np.asarray(data["x"]), np.asarray(data["y"])
    V = {k.strip().upper(): np.asarray(v) for k, v in data["values"].items()}
    h, s = V["WATER DEPTH"], V["FREE SURFACE"]
    u, v = V["VELOCITY U"], V["VELOCITY V"]

    out = {}
    for label, station in (("inflow", y0), ("outflow", y1)):
        band = (np.abs(y - station) <= half_window) & (h > WET)
        if not band.any():
            raise SystemExit(f"no wet nodes within {half_window} m of y = {station}")
        xs = x[band]
        # Unit discharge integrated across the band: sum(h*v)/n * width is crude on a
        # scattered mesh, so bin across x at the mesh's own spacing instead.
        edges = np.arange(xs.min(), xs.max() + 0.05, 0.05)
        idx = np.clip(np.digitize(xs, edges) - 1, 0, len(edges) - 2)
        q = 0.0
        for cell in np.unique(idx):
            sel = idx == cell
            q += float(np.mean(h[band][sel] * v[band][sel])) * 0.05
        out[label] = {
            "y": station,
            "x_min": float(xs.min()), "x_max": float(xs.max()),
            "wse": float(np.median(s[band])),
            "depth": float(np.median(h[band])),
            "speed": float(np.median(np.hypot(u[band], v[band]))),
            "discharge": q,
            "n_nodes": int(band.sum()),
        }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=HERE / "case-config.yml")
    parser.add_argument("--from", dest="y0", type=float, default=DEFAULT_FROM,
                        help="upstream crop face [m] (default: %(default)s)")
    parser.add_argument("--to", dest="y1", type=float, default=DEFAULT_TO,
                        help="downstream crop face [m] (default: %(default)s)")
    parser.add_argument("--margin", type=float, default=1.0,
                        help="lateral slack on the boundary lines [m]")
    args = parser.parse_args(argv)
    setup_logging(level=logging.INFO)
    log = logging.getLogger("axqua")

    cfg = load_config(args.config)
    pre = Path(cfg.preprocessing_dir)
    # geodata.boundary is what dataset().roi_polygon() reads; the surfaces stage points
    # it at its own roi-from-surfaces.gpkg, so this follows whatever the case uses.
    roi = gpd.read_file(cfg.geodata.boundary)

    state = reach_state(cfg, args.y0, args.y1)
    for label, s in state.items():
        log.info("%s face at y = %.1f m: WSE %.3f m, depth %.3f m, |U| %.3f m/s, "
                 "Q %+.4f m3/s across x %.2f..%.2f (%d nodes)",
                 label, s["y"], s["wse"], s["depth"], s["speed"], s["discharge"],
                 s["x_min"], s["x_max"], s["n_nodes"])

    # -- the clipped ROI ----------------------------------------------------------
    minx, _, maxx, _ = roi.total_bounds
    window = box(minx - 1.0, args.y0, maxx + 1.0, args.y1)
    clipped = roi.copy()
    clipped["geometry"] = roi.geometry.intersection(window)
    clipped = clipped[~clipped.geometry.is_empty]
    if clipped.empty:
        raise SystemExit("the crop window missed the ROI entirely")
    # A crop can shatter a ROI into slivers; keep the body.
    clipped = clipped.explode(index_parts=False)
    clipped = clipped.loc[[clipped.geometry.area.idxmax()]].reset_index(drop=True)
    clipped["name"] = "fishpass"
    roi_out = pre / "roi-fishpass.gpkg"
    clipped.to_file(roi_out, driver="GPKG")
    log.info("ROI %.1f m2 -> %.1f m2 (%.0f%%) -> %s",
             roi.geometry.area.sum(), clipped.geometry.area.sum(),
             clipped.geometry.area.sum() / roi.geometry.area.sum() * 100, roi_out.name)

    # -- the two new liquid boundaries --------------------------------------------
    rows = []
    for label, s in state.items():
        line = LineString([(s["x_min"] - args.margin, s["y"]),
                           (s["x_max"] + args.margin, s["y"])])
        rows.append({"Name": f"fishpass-{label}",
                     "Type (inflow/outflow)": label, "geometry": line})
    lb_out = pre / "liquid-boundaries-fishpass.gpkg"
    gpd.GeoDataFrame(rows, crs=roi.crs).to_file(lb_out, driver="GPKG")
    log.info("wrote %s (%d lines)", lb_out.name, len(rows))

    # -- what to paste ------------------------------------------------------------
    inflow, outflow = state["inflow"], state["outflow"]
    log.info("")
    log.info("The parent carries %+.4f m3/s in and %+.4f m3/s out across these faces "
             "(prescribed inflow is %.4f).", inflow["discharge"], outflow["discharge"],
             cfg.boundaries.prescribed_flowrate or float("nan"))
    log.info("paste into case-config.yml:\n\n"
             "geodata:\n"
             "  # the OpenFOAM sub-model only; the 2D case keeps its own ROI\n"
             "boundaries:\n"
             f"  prescribed_flowrate: {cfg.boundaries.prescribed_flowrate}\n"
             f"  prescribed_elevation: {outflow['wse']:.3f}\n"
             "openfoam:\n"
             f"  roi: {roi_out.name}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
