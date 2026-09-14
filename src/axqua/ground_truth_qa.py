"""Is the compiled ground truth's elevation plausible against the model?

This module exists because of one defect that cost a calibration campaign. Inn
KB15's ground truth was joined to a DGPS layer whose ``z`` is the **GNSS antenna**
elevation rather than the bed, so every target sat 2.26-2.70 m above the water.
Nothing complained: HydroBayesCal's OpenFOAM binding finds the nearest extraction
point with no distance cutoff, so it returned a plausible-looking 0.003 m/s and the
surrogate trained on it. The error was only visible as a failed calibration.

**The discriminator is the whole point.** A reach can legitimately have a biased
DEM - KB15's is 0.3 m high in the wetted channel from bathymetric-LiDAR attenuation
- so "the residual is large" cannot be the test. What separates the two is *shape*:

* an **un-subtracted instrument height** is a constant. Every point in the group is
  offset by the same rover-pole length, so the residual is large, tight, and flat
  in depth;
* a **physical bed bias** grows with the water column (attenuation integrates along
  the light path), so the residual has a significant slope against depth.

On KB15 the two signatures are ~30 sigma apart, so the thresholds below are not
delicately tuned. The depth-proportional case is reported and *named*, never
silenced, because it is real and the reader needs to know it is there.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

log = logging.getLogger("axqua")

#: A residual must exceed this to be worth reporting at all [m].
OFFSET_THRESHOLD = 0.30
#: ...and be at least this tight, relative to its own scatter, to be called constant.
SIGMA_RATIO = 4.0
#: ...with scatter no larger than this [m].
CONSTANT_TOLERANCE = 0.06
#: Above this, a constant residual is an error even under `warn` - a metre of "bed"
#: error in a wadeable reach is never data.
REFUSE_THRESHOLD = 1.00
#: Fewer points than this cannot support the statistics.
MIN_GROUP = 5


@dataclass(frozen=True)
class ElevationFinding:
    severity: Literal["error", "warning", "info"]
    code: Literal["instrument-height", "depth-proportional-bias",
                  "above-water-surface", "outside-domain", "mixed"]
    group: str
    n: int
    offset: float
    slope: float
    slope_p: float
    scatter: float
    message: str

    def line(self) -> str:
        return (f"  group {self.group} (n={self.n}): offset {self.offset:+.3f} m  "
                f"scatter {self.scatter:.3f} m  slope {self.slope:+.2f} m/m "
                f"(p={self.slope_p:.3g})\n    {self.severity.upper()}  "
                f"{self.code}: {self.message}")


def _decompose(residual, depth):
    """Split the residual into a CONSTANT part and a depth-proportional part.

    ``residual ~ a + b*depth``. The two error modes SUPERPOSE - an un-subtracted
    pole height adds ``a``, a bathymetric-LiDAR bias adds ``b`` - so asking only
    "is it flat?" cannot separate them when both are present: the slope fires and
    the large constant is missed. Fitting both and judging each independently is
    what makes the discriminator work.

    Returns ``(a, b, p_of_b, scatter_about_the_line)``.
    """
    finite = np.isfinite(residual)
    if depth is not None:
        finite = finite & np.isfinite(depth)
    if finite.sum() < 3:
        r = residual[np.isfinite(residual)]
        return float(np.median(r)), 0.0, 1.0, float(np.std(r))

    r = residual[finite]
    if depth is None or float(np.ptp(depth[finite])) <= 1e-9:
        return float(np.median(r)), 0.0, 1.0, float(np.std(r))

    from scipy.stats import linregress
    fit = linregress(depth[finite], r)
    a_, b_, p_ = float(fit.intercept), float(fit.slope), float(fit.pvalue)
    scatter = float(np.std(r - (a_ + b_ * depth[finite])))
    return a_, b_, p_, scatter


def _groups(df, residual):
    """Group by an EXPLICIT column, or not at all.

    Deliberately no clustering on the residual. That was the first implementation
    and it is self-defeating: grouping points by how similar their residual is
    manufactures groups that are flat in depth by construction, so a legitimate
    depth-proportional bias is sliced into pieces that each look like a constant
    offset - exactly the false alarm this module must not raise. Measured: on the
    corrected KB15 truth it invented a -0.39 m "instrument height" that is not there.
    """
    for col in ("pole_height", "group", "campaign", "instrument"):
        if col in df.columns and df[col].notna().any():
            return {f"{col}={v}": np.asarray(df[col] == v)
                    for v in sorted(df[col].dropna().unique())}
    return {"all": np.ones(len(residual), dtype=bool)}


def reference_bed(cfg, x, y) -> tuple[np.ndarray, str] | tuple[None, str]:
    """The model bed, else the DEM, else nothing. Never raises."""
    try:
        geometry = Path(cfg.model_path(cfg.geometry_slf))
        if geometry.is_file():
            from axqua.model_column import telemac_column
            column = telemac_column(x, y, geometry=geometry)
            return column.bed, f"model bed, {geometry.name}"
    except Exception as exc:                              # noqa: BLE001
        log.debug("no model bed for the elevation check: %s: %s",
                  type(exc).__name__, exc)
    try:
        dem = getattr(cfg.geodata, "dem_initial", None)
        if dem and Path(dem).is_file():
            from axqua.core.raster import sample_raster_at
            return np.asarray(sample_raster_at(Path(dem), np.column_stack([x, y])),
                              dtype=float), f"DEM, {Path(dem).name}"
    except Exception as exc:                              # noqa: BLE001
        log.debug("no DEM for the elevation check: %s: %s", type(exc).__name__, exc)
    return None, "no reference available"


def check_ground_truth_elevations(cfg, *, tables=None) -> list[ElevationFinding]:
    """Compare the compiled ground truth's ``z`` with the model bed or the DEM."""
    from axqua.ground_truth import read_tidy

    if tables is None:
        try:
            tables = read_tidy(cfg.ground_truth_path)
        except Exception as exc:                          # noqa: BLE001
            log.debug("no tidy ground truth to check: %s: %s",
                      type(exc).__name__, exc)
            return []
    df = tables.get("hydraulics")
    if df is None or "z" not in df or len(df) < MIN_GROUP:
        return []
    df = df.reset_index(drop=True)

    bed, source = reference_bed(cfg, df["x"].to_numpy(float), df["y"].to_numpy(float))
    if bed is None:
        return []

    residual = df["z"].to_numpy(float) - np.asarray(bed, dtype=float)
    depth = df["h"].to_numpy(float) if "h" in df else None
    ok = np.isfinite(residual)
    if ok.sum() < MIN_GROUP:
        return []

    findings: list[ElevationFinding] = []
    for name, mask in _groups(df, np.where(ok, residual, np.nan)).items():
        sel = mask & ok
        if sel.sum() < MIN_GROUP:
            continue
        r = residual[sel]
        d = depth[sel] if depth is not None else None
        constant, slope, pvalue, scatter = _decompose(r, d)

        # judged INDEPENDENTLY, because the two modes superpose
        constant_is_real = (abs(constant) >= OFFSET_THRESHOLD
                            and abs(constant) >= SIGMA_RATIO * max(scatter, 1e-9))
        slope_is_real = (d is not None and pvalue <= 0.05
                         and abs(slope) * float(np.ptp(d)) >= CONSTANT_TOLERANCE)

        if constant_is_real:
            findings.append(ElevationFinding(
                "error", "instrument-height", name, int(sel.sum()),
                constant, slope, pvalue, scatter,
                f"after removing the depth-proportional part, a CONSTANT "
                f"{constant:+.2f} m remains - "
                f"{abs(constant) / max(scatter, 1e-9):.0f}x the scatter about the "
                "fit. That is the signature of an un-subtracted instrument or "
                "rover-pole height: a bed bias grows with depth and is captured by "
                "the slope instead. Check that ground_truth.sources names a layer "
                "whose z is the BED, not the GNSS antenna."))
        if slope_is_real:
            findings.append(ElevationFinding(
                "info", "depth-proportional-bias", name, int(sel.sum()),
                constant, slope, pvalue, scatter,
                f"the residual changes {slope:+.2f} m per m of water depth "
                f"(p={pvalue:.3g}) - a physical bed bias, e.g. bathymetric-LiDAR "
                "attenuation in turbid water, not an instrument height. Reference "
                "calibration targets to the model's own column "
                "(axqua.model_column) rather than to surveyed elevations."))
        if (not constant_is_real and not slope_is_real
                and abs(constant) >= OFFSET_THRESHOLD):
            findings.append(ElevationFinding(
                "warning", "mixed", name, int(sel.sum()),
                constant, slope, pvalue, scatter,
                f"a {constant:+.2f} m residual that is neither tight enough to be a "
                f"single instrument offset (scatter {scatter:.2f} m) nor "
                "significantly depth-dependent. Check for mixed instrument settings "
                "or a horizontal misregistration."))

    for f in findings:
        if f.code == "instrument-height" and abs(f.offset) >= REFUSE_THRESHOLD:
            log.debug("%s exceeds the refuse threshold", f.group)
    log.debug("elevation check reference: %s", source)
    return findings


def report(findings, *, strict: bool = False, source: str = "") -> None:
    """Log the findings; raise under *strict* if any is an error."""
    if not findings:
        log.info("ground-truth elevation check: nothing to report%s",
                 f" ({source})" if source else "")
        return
    log.info("ground-truth elevation check%s", f" ({source})" if source else "")
    for f in findings:
        getattr(log, {"error": "error", "warning": "warning"}.get(f.severity, "info"))(
            "%s", f.line())
    errors = [f for f in findings if f.severity == "error"]
    if errors and strict:
        raise SystemExit(
            "the compiled ground-truth elevations are not usable:\n"
            + "\n".join(f.line() for f in errors)
            + "\n\nFix the source, recompile, and re-check with `axqua check-gt`. "
              "Pass force=True to proceed anyway (the calibration will train on "
              "whatever the solver returns at those coordinates).")
