"""Modelled vertical velocity profiles against the measured verticals.

The scientific payoff of the 3D model, and the figure that closes the loop with
the OpenFOAM calibration: run it once at the prior centre and once at the
posterior best, and the two panels *are* the calibration result.

**VisIt samples, matplotlib draws.** VisIt's ``Lineout`` produces one curve per
vertical (:func:`axqua.postproc.visit._render_profiles`); this module reads those
curves and plots them. The split is deliberate - the measured overlay needs error
bars, the figure has to match aXqua's other figures
(``report._write_plot``, ``flux_convergence``), and once the curves exist the
comparison must stay reproducible on a machine with no VisIt at all.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("axqua")

#: Fallback height above the BED, as a fraction of the depth, when the ground truth
#: does not say where the probe sat. **The "0.6 depth" convention measures DOWN FROM
#: THE SURFACE** (verified on this dataset: MeasD/FinalD has median 0.59), so the
#: probe is at 0.4 of the depth ABOVE the bed. This constant was 0.6 and documented
#: as "from the BED", which placed every comparison 0.2*h too high. Prefer the
#: per-row value from ground_truth.relative_height, which reads MeasD/FinalD.
MEASUREMENT_FRACTION = 0.4


def read_curve(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """One VisIt Curve2D export as ``(distance_along_line, value)``."""
    xs, ys = [], []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            xs.append(float(parts[0]))
            ys.append(float(parts[1]))
        except ValueError:
            continue
    return np.asarray(xs), np.asarray(ys)


def _measured(cfg) -> pd.DataFrame:
    from axqua.ground_truth import read_tidy

    tables = read_tidy(cfg.ground_truth_path)
    if "hydraulics" not in tables:
        raise SystemExit(
            f"no 'hydraulics' tab in {cfg.ground_truth_path}; nothing to compare against")
    return tables["hydraulics"].reset_index(drop=True)


def compare(cfg, curves_dir: Path, out_dir: Path) -> list[Path]:
    """Plot modelled profiles against the measurements; write the figure and a CSV."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    curves_dir, out_dir = Path(curves_dir), Path(out_dir)
    df = _measured(cfg)

    found = sorted(curves_dir.glob("profile-*.curve")) + \
        sorted(curves_dir.glob("profile-*.ultra"))
    if not found:
        raise SystemExit(
            f"no exported profiles in {curves_dir}. Did the VisIt profiles scene run?")

    speed = np.sqrt(df.get("u", 0.0) ** 2 + df.get("v", 0.0) ** 2)
    try:
        from axqua.ground_truth import relative_height
        fractions, f_source = relative_height(df)
        log.info("profile comparison heights from: %s", f_source)
    except Exception:                                    # noqa: BLE001
        fractions = None
    rows = []
    n = len(found)
    cols = min(6, n)
    plot_rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(plot_rows, cols, figsize=(2.4 * cols, 2.6 * plot_rows),
                             squeeze=False, sharex=True)

    for index, curve_path in enumerate(found):
        ax = axes[index // cols][index % cols]
        distance, modelled = read_curve(curve_path)
        try:
            point = int(curve_path.stem.split("-")[-1]) - 1
        except ValueError:
            point = index
        if point >= len(df):
            continue

        depth = float(df["h"].iloc[point]) if "h" in df else float(distance.max() or 1.0)
        measured = float(speed.iloc[point])
        # per-row height above the bed when MeasD/FinalD are carried, else the
        # documented fallback - never a bare 0.6, which is measured from the surface
        f_row = float(fractions.iloc[point]) if fractions is not None \
            else MEASUREMENT_FRACTION

        if distance.size:
            # The lineout spans the WHOLE mesh z extent (the survey datum cannot be
            # trusted - see visit._render_profiles), so `distance` is measured from
            # the global mesh bottom, not from this column's bed. VisIt returns
            # samples only where the mesh exists, so the FIRST sample is the local
            # bed and the last is the local surface: re-reference to that, or the
            # model sits metres above the measurement on an incompatible axis.
            height = distance - distance[0]
            model_depth = float(height[-1]) if height.size > 1 else depth
            ax.plot(modelled, height, lw=1.4, color="#1f77b4", label="model")
            # compare at the same RELATIVE depth in each: the measurement is at
            # 0.6*h of the surveyed column, the model at 0.6 of its own column
            at_measurement = float(np.interp(f_row * model_depth,
                                             height, modelled))
        else:
            model_depth = float("nan")
            at_measurement = float("nan")

        err = 0.0
        for comp in ("u", "v"):
            if f"{comp}_err" in df:
                err += float(df[f"{comp}_err"].iloc[point]) ** 2
        err = float(np.sqrt(err))

        ax.errorbar(measured, f_row * depth, xerr=err or None,
                    fmt="o", ms=4, color="#d62728", capsize=2, label="measured")
        ax.set_title(f"{point + 1}", fontsize=8)
        ax.tick_params(labelsize=7)
        if index % cols == 0:
            ax.set_ylabel("height above bed [m]", fontsize=7)

        rows.append({
            "id": point + 1,
            "x": float(df["x"].iloc[point]), "y": float(df["y"].iloc[point]),
            "measured_depth_m": depth,
            "modelled_depth_m": model_depth,
            "measured_speed_ms": measured,
            "measured_error_ms": err,
            "modelled_speed_ms": at_measurement,
            "residual_ms": at_measurement - measured,
        })

    for index in range(n, plot_rows * cols):
        axes[index // cols][index % cols].axis("off")
    handles, labels = axes[0][0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="lower center", ncol=2, fontsize=8)
    fig.suptitle(f"Vertical velocity profiles - {cfg.name}   "
                 "(x: speed [m/s], y: height above the LOCAL bed [m])", fontsize=10)
    fig.tight_layout(rect=(0, 0.05, 1, 0.96))

    out_dir.mkdir(parents=True, exist_ok=True)
    figure = out_dir / "profiles.png"
    fig.savefig(figure, dpi=140)
    plt.close(fig)

    table = pd.DataFrame(rows)
    csv = out_dir / "profiles.csv"
    table.to_csv(csv, index=False)

    if not table.empty:
        finite = table["residual_ms"].replace([np.inf, -np.inf], np.nan).dropna()
        if len(finite):
            log.info("profile residuals: mean %+.3f m/s, RMS %.3f m/s over %d verticals",
                     finite.mean(), float(np.sqrt((finite ** 2).mean())), len(finite))
    return [figure, csv]
