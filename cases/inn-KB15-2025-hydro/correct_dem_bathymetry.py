"""Correct the wetted-channel bathymetry of the 2025 DEM, and verify it.

**Why this is needed.** ``DEM-2025-20cm.tif`` is refraction-corrected bathymetric
LiDAR, but in the wetted channel its bed sits **+0.34 m too high** on average
against the pole-corrected DGPS beds, and the offset **grows with water depth**
(+0.33 m per m, r = 0.54, p = 2.3e-3). A datum or instrument error would be
constant; depth-proportionality is the signature of laser attenuation through a
turbid water column, which is exactly what the Inn is.

**Why it blocks the 3D calibration.** The consequence is not merely a biased depth.
With the bed 0.34 m too high the modelled column at the FlowTracker verticals is
0.30 m against a measured 0.5-0.7 m, while the bed roughness is ks = 0.155 m. So
**25 of 30 calibration targets fall inside the roughness layer itself**, where
``nutkRoughWallFunction`` clamps and the modelled velocity is a boundary condition
rather than a result. No placement of targets rescues that, and no value of ks
reproduces the measurements: the September 2026 campaign reached only 3 of 90
observations bracketed, median 6.5 sigma. Correcting the bed takes the column to
~0.64 m and column/ks from 1.9 to 4.1, which is a resolvable profile.

**What this does, and the honest limits.** It fits ``correction = a + b * depth``
on the 30 DGPS bed points and applies it to the DEM **wherever the 2D model says
there is water**, tapering to zero at the waterline so the banks are untouched.

Extrapolating 30 points over a 1.6 km reach is only defensible because the
predictor is *physical*: attenuation scales with the water path length, and the 2D
model supplies the depth everywhere. It is still an extrapolation, and three things
follow that the user must weigh:

* the fit explains about a third of the variance (r = 0.54), so per-point residuals
  of +/-0.1 m remain;
* the survey covers ~105 x 47 m of a much longer reach, all in two pools. Riffles
  and the braided margins are unsampled and may attenuate differently;
* the corrected DEM is a *hypothesis about the bed*, not a survey of it. The proper
  fix is echo-sounding or a denser wading survey. This makes the 3D calibration
  possible; it does not make the bathymetry known.

Run: mamba run -n axqua-env python <case>/correct_dem_bathymetry.py [--apply]

Without ``--apply`` it only reports the fit and the verification, changing nothing.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from scipy.stats import linregress

from axqua.config import load_config
from axqua.core.raster import sample_raster_at

HERE = Path(__file__).resolve().parent
DGPS = HERE / "user-sources/geodata/flowtracker2/dgps-flowtracker-kb15-sept25-zcorrected.gpkg"
OUT_NAME = "DEM-2025-20cm-bathy-corrected.tif"

#: Below this modelled depth the correction is tapered out, so the waterline and
#: the dry banks - which the LiDAR sees correctly - are left alone.
TAPER_DEPTH = 0.10

#: Refuse a raster larger than this. The full 0.2 m survey DEM is 4,537 Mcells.
MAX_CELLS = 200_000_000


def _roi_clip(cfg) -> Path:
    """The ROI-clipped DEM the pipeline actually meshes from, if one exists."""
    candidates = [Path(cfg.preprocessing_dir) / "dem-initial-roi.tif",
                  Path(cfg.geodata.dem_initial).with_name("dem-2025-roi-clip.tif")]
    for path in candidates:
        if path.is_file():
            return path
    return Path(cfg.geodata.dem_initial)


def fit_correction(cfg):
    """``(intercept, slope, stats)`` of ``DEM - bed_survey`` against measured depth."""
    dg = gpd.read_file(DGPS).sort_values("ID")
    x = dg.geometry.x.to_numpy(float)
    y = dg.geometry.y.to_numpy(float)
    dem = np.asarray(sample_raster_at(cfg.geodata.dem_initial, x, y), dtype=float)
    bed = dg["z"].to_numpy(float)
    depth = dg["WaterDepth"].to_numpy(float)

    residual = dem - bed                       # the DEM is HIGH by this much
    fit = linregress(depth, residual)
    return float(fit.intercept), float(fit.slope), {
        "n": len(dg), "median": float(np.median(residual)),
        "min": float(residual.min()), "max": float(residual.max()),
        "r": float(fit.rvalue), "p": float(fit.pvalue),
        "x": x, "y": y, "bed": bed, "depth": depth, "residual": residual,
    }


def model_column_on(cfg, xs, ys):
    """The 2D modelled ``(depth, wse)`` at raster cell centres."""
    from axqua.model_column import telemac_column

    column = telemac_column(
        xs, ys,
        geometry=cfg.model_path(cfg.geometry_slf),
        fields=cfg.model_path("hotstart-seed.slf"),
        method="nearest")            # nearest: a whole raster through a
                                     # triangulation would be needlessly slow
    depth = np.where(column.inside, column.depth, 0.0)
    return depth, np.where(column.inside, column.wse, np.nan)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="write the corrected raster (default: report only)")
    args = parser.parse_args()

    cfg = load_config(HERE / "case-config.yml")
    intercept, slope, s = fit_correction(cfg)

    print(f"DEM - surveyed bed over {s['n']} DGPS verticals:")
    print(f"  median {s['median']:+.3f} m   range {s['min']:+.3f} .. {s['max']:+.3f} m")
    print(f"  correction = {intercept:+.3f} + {slope:+.3f} * depth "
          f"(r={s['r']:.2f}, p={s['p']:.1e})")

    predicted = intercept + slope * s["depth"]
    after = s["residual"] - predicted
    print(f"\nresidual after correction, at the survey points:")
    print(f"  before: median {np.median(np.abs(s['residual'])):.3f} m, "
          f"max {np.abs(s['residual']).max():.3f} m")
    print(f"  after : median {np.median(np.abs(after)):.3f} m, "
          f"max {np.abs(after).max():.3f} m")
    if np.median(np.abs(after)) >= np.median(np.abs(s["residual"])):
        print("  the fit does NOT reduce the residual - do not apply it.")
        return 1

    if not args.apply:
        print("\nreport only. Re-run with --apply to write the corrected raster.")
        return 0

    # Correct the ROI CLIP, not the full survey raster. The full DEM is
    # 89,487 x 50,698 = 4,537 Mcells; sampling the model depth at every cell
    # centre would need tens of GB and hours to alter terrain the model never
    # sees. The pipeline meshes from the ROI clip anyway (dem.clip_dem_to_roi),
    # and that is 23.6 Mcells - 190x smaller and the same result where it counts.
    src_path = _roi_clip(cfg)
    out_path = src_path.with_name(OUT_NAME)
    with rasterio.open(src_path) as probe:
        cells = probe.width * probe.height
    if cells > MAX_CELLS:
        raise SystemExit(
            f"{src_path.name} has {cells / 1e6:.0f} Mcells, over the "
            f"{MAX_CELLS / 1e6:.0f} Mcell budget. Clip the DEM to the ROI first "
            "(`axqua clip <raster> -b <boundary> -o <out>`) and point "
            "geodata.dem_initial at the clip.")
    print(f"\ncorrecting {src_path.name} ({cells / 1e6:.1f} Mcells)")
    with rasterio.open(src_path) as src:
        profile = src.profile
        data = src.read(1).astype("float32")
        nodata = src.nodata
        rows, cols = np.indices(data.shape)
        xs, ys = rasterio.transform.xy(src.transform, rows.ravel(), cols.ravel())

    depth, wse = model_column_on(cfg, np.asarray(xs), np.asarray(ys))
    depth = depth.reshape(data.shape)
    wse = wse.reshape(data.shape)

    # SOLVE for the bed, do not just subtract a correction evaluated at the model's
    # own depth. The bias depends on the TRUE depth, and the model's depth is
    # itself too shallow precisely because the bed is too high - using it
    # under-corrects by ~0.12 m at the survey points, about half the correction.
    #
    #   bed_true = DEM - (a + b*(WSE - bed_true))  =>  bed_true = (DEM - a - b*WSE)/(1 - b)
    #
    # The modelled WSE is the right anchor: it is set by the downstream control and
    # the discharge rather than by local bed detail, and matches the corrected DGPS
    # water surface to ~1 cm on this reach even with the bed wrong.
    with np.errstate(invalid="ignore"):
        bed_true = (data - intercept - slope * wse) / (1.0 - slope)
        correction = np.where(np.isfinite(wse) & (depth > 0), data - bed_true, 0.0)

    # Do NOT extrapolate past the depths that support the fit. The survey spans
    # 0.30-0.99 m; the model has pools several metres deep, and the linear fit
    # there lowered the bed by up to 1.26 m on the strength of no data at all.
    # Held flat beyond the deepest surveyed vertical: attenuation must saturate
    # once the return is lost, so a constant is the conservative continuation.
    supported = float(s["depth"].max())
    correction = np.minimum(correction, intercept + slope * supported)
    # taper to zero at the waterline: the LiDAR sees dry ground correctly, and a
    # step at the bank edge would be a meshing artefact rather than terrain
    correction *= np.clip(depth / TAPER_DEPTH, 0.0, 1.0)
    correction = np.maximum(correction, 0.0)          # never RAISE the bed

    corrected = data - correction.astype("float32")
    if nodata is not None:
        corrected = np.where(data == nodata, data, corrected)

    profile.update(dtype="float32", compress="deflate")
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(corrected.astype("float32"), 1)

    wet = correction > 0
    print(f"\nwrote {out_path}")
    print(f"  lowered {int(wet.sum()):,} cells ({100 * wet.mean():.1f}% of the raster)")
    print(f"  by median {float(np.median(correction[wet])):.3f} m, "
          f"max {float(correction.max()):.3f} m "
          f"(capped at the deepest surveyed vertical, {supported:.2f} m)")
    deeper = int((depth > supported).sum())
    if deeper:
        print(f"  {deeper:,} cells are deeper than any surveyed vertical and take "
              "the capped correction - the fit does not extend there")
    print("\nNext, in order - each depends on the one before:")
    print(f"  1. point geodata.dem_initial at {OUT_NAME} in case-config.yml")
    print("  2. python preprocessing.py      # rebuild the mesh on the new bed")
    print("  3. python initial_run.py        # a NEW r2d.slf; the old one is invalid")
    print("  4. python run_Bayes_cal_openfoam.py --prepare-only   # check placement")
    print("  5. python run_Bayes_cal_openfoam.py --run")
    print("\nNote steps 2-3 invalidate measurements-corrected-*.csv, which are a "
          "function of the mesh - regenerate them with prepare_corrected_targets.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
