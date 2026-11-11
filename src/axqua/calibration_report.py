"""The calibration report: what the posterior says about each zone, and where the zones are.

Written after a successful HydroBayesCal calibration into ``<folder>/report/`` (the
calibration folder, or its ``multiflow`` folder for calibration situations), because the
two questions a modeler asks of a calibration need pictures, not a log line (the user's
order of 2026-11-11, after the inn-KB15 trial):

* ``posteriors.png``: for every calibrated parameter, the prior (the range of the
  roughness table) and the posterior of the last iteration, with the calibrated value
  (:func:`axqua.validation.calibrated_parameters`, the per-parameter posterior maximum)
  marked, and one line on what the data say: how many calibration points lie in the zone,
  whether the posterior is narrow, not identifiable or pinned at a limit, whether the
  largest ks was lowered for shallow water (:mod:`axqua.calibration_zones`);
* ``roughness-zones.png``: a map of every roughness zone with its ks (calibrated, fixed or
  left out), the calibration points and the inflow and outflow lines, in the colors of the
  posterior panels;
* ``evidence.png``: the Bayesian model evidence and the relative entropy over the
  iterations of the active learning (HydroBayesCal's own figure needs LaTeX);
* ``roughness-zones-calibrated.gpkg``: the zones with ``zone``, ``ks_cm``, ``status`` and
  ``label``, which *Load results* adds to QGIS labeled;
* ``calibration-report.json``: the numbers behind the figures.

Each figure that fails is a warning, never a failed calibration.
"""

from __future__ import annotations

import json
import logging
import pickle
import re
from pathlib import Path

import numpy as np

log = logging.getLogger("axqua")

FOLDER = "report"
RESULTS = "auto-saved-results-HydroBayesCal"
#: Categorical colors of the calibrated parameters, in order (zone 1, 3, 4, ...): the
#: validated reference palette of the figures, fixed order, never cycled.
COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6",
          "#e34948")
FIXED = "#d9d8d3"
SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e6e5e1"

_ZONE = re.compile(r"^zone(\d+)$")


def _zone(name: str) -> int | None:
    match = _ZONE.match(str(name).strip())
    return int(match.group(1)) if match else None


def _last(data: dict, key: str):
    values = data.get(key)
    if values is None or not len(values):
        return None
    return values[-1]


def _dictionary(folder: Path) -> tuple[Path, dict] | None:
    found = sorted((Path(folder) / RESULTS / "calibration-data").glob("*/BAL_dictionary.pkl"),
                   key=lambda p: p.stat().st_mtime)
    if not found:
        return None
    with open(found[-1], "rb") as handle:
        return found[-1], pickle.load(handle)      # nosec B301 - written by the calibration


def _points(folder: Path):
    """The calibration point tables of the calibration in *folder*."""
    import pandas as pd

    tables = []
    for path in sorted(Path(folder).glob("measurements-calibration*.csv")):
        try:
            tables.append(pd.read_csv(path))
        except (OSError, ValueError):
            continue
    return tables


def _verdict(zone_info: dict | None, flags, reduction) -> str:
    parts = []
    if zone_info is not None:
        n = zone_info.get("points")
        parts.append("no measurement point" if not n else
                     f"{n} measurement point{'s' if n != 1 else ''}")
    flags = list(flags or [])
    if "non_identifiable" in flags:
        parts.append("not identifiable")
    elif reduction is not None and np.isfinite(reduction):
        parts.append("well determined" if reduction >= 0.5 else "weakly determined")
    if "pinned_at_upper_bound" in flags:
        parts.append("at its upper limit")
    if "pinned_at_lower_bound" in flags:
        parts.append("at its lower limit")
    if zone_info is not None and zone_info.get("decision") == "capped":
        parts.append(f"largest ks lowered to {100 * zone_info['ks_max_used']:.0f} cm "
                     f"(shallow water)")
    return ", ".join(parts)


def _value_text(value: float, zone: bool) -> str:
    if not zone:
        return f"{value:.4g}"
    cm = 100 * value
    return f"{cm:.1f} cm" if cm < 10 else f"{cm:.0f} cm"


def summarize(cfg, folder: Path) -> dict | None:
    """The numbers of the report, or None when *folder* holds no finished calibration."""
    from axqua.calibration_zones import read_plan
    from axqua.validation import calibrated_parameters

    found = _dictionary(folder)
    if found is None:
        return None
    source, data = found
    names = [str(n) for n in data.get("calibration_parameters") or []]
    ranges = [list(map(float, r)) for r in data.get("param_values") or []]
    calibrated = calibrated_parameters(cfg)
    values = calibrated.as_dict()
    spread = _last(data, "marginal_peak_spread")
    hdi = _last(data, "marginal_hdi")
    reduction = _last(data, "variance_reduction")
    flags = _last(data, "identifiability_flags") or [[] for _ in names]
    joint = _last(data, "joint_optimum")
    plan = read_plan(folder) or {}
    zones = {z["zone"]: z for z in plan.get("zones") or []}
    parameters = []
    for i, name in enumerate(names):
        zone = _zone(name)
        info = zones.get(zone) if zone is not None else None
        entry = {
            "name": name, "zone": zone,
            "prior": ranges[i] if i < len(ranges) else None,
            "value": values.get(name),
            "spread": float(spread[i]) if spread is not None else None,
            "hdi": [float(v) for v in hdi[i]] if hdi is not None else None,
            "variance_reduction": float(reduction[i]) if reduction is not None else None,
            "flags": list(flags[i]) if i < len(flags) else [],
            "joint_maximum": float(joint[i]) if joint is not None else None,
            "points": info.get("points") if info else None,
        }
        entry["verdict"] = _verdict(info, entry["flags"], entry["variance_reduction"])
        parameters.append(entry)
    log_bme = data.get("log_BME")
    entropy = data.get("RE")
    runs = data.get("N_tp")
    return {
        "source": str(source),
        "estimate": calibrated.estimate,
        "parameters": parameters,
        "zone_plan": plan,
        "evidence": {
            "runs": [int(v) for v in runs] if runs is not None else [],
            "log_bme": [float(v) for v in log_bme] if log_bme is not None else [],
            "relative_entropy": [float(v) for v in entropy] if entropy is not None else [],
        },
        "posterior_size": int(len(np.atleast_2d(_last(data, "posterior")))),
        "_posterior": np.atleast_2d(np.asarray(_last(data, "posterior"), dtype=float)),
    }


def _style():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 11, "axes.edgecolor": MUTED,
                         "axes.labelcolor": INK2, "xtick.color": MUTED,
                         "ytick.color": MUTED, "text.color": INK})
    return plt


def plot_posteriors(summary: dict, path: Path) -> Path:
    """One panel per calibrated parameter: prior band, posterior, calibrated value."""
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt = _style()
    parameters = summary["parameters"]
    posterior = summary["_posterior"]
    n = len(parameters)
    cols = 2 if n > 1 else 1
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(6.4 * cols, 3.3 * rows + 0.9),
                             facecolor=SURFACE, squeeze=False)
    for k, (ax, entry) in enumerate(zip(axes.flat, parameters)):
        zone = entry["zone"] is not None
        scale = 100.0 if zone else 1.0
        lo, hi = (entry["prior"] or [np.nanmin(posterior[:, k]), np.nanmax(posterior[:, k])])
        lo, hi = lo * scale, hi * scale
        column = posterior[:, k] * scale
        edges = np.linspace(lo, hi, 21)
        density, _ = np.histogram(column, bins=edges, density=True)
        prior = 1.0 / (hi - lo) if hi > lo else 0.0
        ax.set_facecolor(SURFACE)
        ax.axhspan(0, prior, color=FIXED, lw=0, zorder=1)
        ax.bar(edges[:-1], density, width=np.diff(edges), align="edge",
               color=COLORS[k % len(COLORS)] if k < len(COLORS) else MUTED,
               edgecolor=SURFACE, linewidth=1.5, zorder=2)
        top = max(float(density.max()) if density.size else 0.0, prior) * 1.32 or 1.0
        value = entry["value"]
        if value is not None:
            v = value * scale
            ax.axvline(v, color=INK, lw=2, zorder=3)
            right = v > lo + 0.6 * (hi - lo)
            ax.text(v + (-1 if right else 1) * 0.015 * (hi - lo), top * 0.93,
                    "calibrated: " + _value_text(value, zone),
                    ha="right" if right else "left", va="top", fontweight="bold",
                    color=INK, zorder=4)
        ax.set_xlim(lo, hi)
        ax.set_ylim(0, top)
        ax.set_yticks([])
        ax.set_xlabel("ks (cm)" if zone else entry["name"])
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.grid(axis="x", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        title = f"Zone {entry['zone']}" if zone else entry["name"]
        ax.set_title(title, loc="left", fontsize=14, fontweight="bold", pad=18)
        ax.text(0, 1.03, entry["verdict"], transform=ax.transAxes, fontsize=10,
                color=INK2, va="bottom")
    for ax in list(axes.flat)[n:]:
        ax.set_visible(False)
    fig.suptitle(f"Posterior of each calibrated parameter ({summary['estimate']})",
                 fontsize=14, fontweight="bold", y=0.995)
    fig.legend(handles=[Patch(color=FIXED, label="prior (calibration range)"),
                        Patch(color=MUTED, label="posterior"),
                        Line2D([], [], color=INK, lw=2, label="calibrated value")],
               loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 0.955))
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return path


def plot_evidence(summary: dict, path: Path) -> Path | None:
    """Log Bayesian model evidence and relative entropy over the training runs."""
    evidence = summary["evidence"]
    if not evidence["log_bme"]:
        return None
    plt = _style()
    runs = evidence["runs"] or list(range(len(evidence["log_bme"])))
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6), facecolor=SURFACE)
    for ax, values, label in ((axes[0], evidence["log_bme"], "log of the model evidence"),
                              (axes[1], evidence["relative_entropy"], "relative entropy")):
        ax.set_facecolor(SURFACE)
        if values:
            ax.plot(runs[:len(values)], values, color=COLORS[0], lw=2, marker="o", ms=4)
        ax.set_xlabel("TELEMAC parameter sets")
        ax.set_title(label, loc="left", fontweight="bold")
        ax.grid(color=GRID, lw=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return path


def zone_table(cfg, summary: dict):
    """The roughness zones as one feature each, with their ks and status."""
    from axqua.solvers.telemac.mesh import read_roughness_table, roughness_zone_frame

    zones = roughness_zone_frame(cfg).dissolve(by="zone_id").reset_index()
    nominal = (read_roughness_table(Path(cfg.geodata.roughness_table))
               if cfg.geodata.roughness_table else {})
    by_zone = {p["zone"]: (k, p) for k, p in enumerate(summary["parameters"])
               if p["zone"] is not None}
    plan = {z["zone"]: z for z in (summary.get("zone_plan") or {}).get("zones") or []}
    ks, status, label, color = [], [], [], []
    for zone in zones["zone_id"].astype(int):
        if zone in by_zone and by_zone[zone][1]["value"] is not None:
            k, entry = by_zone[zone]
            value = entry["value"]
            state = "calibrated"
            color.append(COLORS[k] if k < len(COLORS) else MUTED)
        else:
            value = nominal.get(zone)
            state = "left out" if plan.get(zone, {}).get("decision") == "excluded" else "fixed"
            color.append(FIXED)
        ks.append(round(100 * value, 2) if value is not None else None)
        status.append(state)
        # calibrated values rounded as they are read out; fixed ones as the model has them
        shown = ("?" if value is None else _value_text(value, True) if state == "calibrated"
                 else f"{100 * value:g} cm")
        text = f"Zone {zone}\nks {shown}"
        if state != "calibrated":
            text += f", {state}"
        label.append(text)
    zones["zone"] = zones["zone_id"].astype(int)
    zones["ks_cm"] = ks
    zones["status"] = status
    zones["label"] = label
    zones["_color"] = color
    return zones


def plot_zone_map(cfg, summary: dict, zones, tables, path: Path) -> Path:
    """Map of the roughness zones, labeled with their ks, and the calibration points."""
    from matplotlib.patches import Patch

    plt = _style()
    xmin, ymin, xmax, ymax = zones.total_bounds
    aspect = (xmax - xmin) / max(ymax - ymin, 1.0)
    width = 13.0
    fig, ax = plt.subplots(figsize=(width, max(4.0, width / aspect + 1.6)),
                           facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    for _, row in zones.iterrows():
        calibrated = row["status"] == "calibrated"
        zones[zones["zone"] == row["zone"]].plot(
            ax=ax, color=row["_color"], edgecolor=SURFACE, linewidth=0.6,
            alpha=0.85 if calibrated else 0.6,
            hatch=None if calibrated else ("//" if row["status"] == "fixed" else "xx"))
    lines = cfg.boundaries.liquid_boundaries
    if lines:
        try:
            import geopandas as gpd

            liquid = gpd.read_file(lines)
            if liquid.crs and liquid.crs.to_epsg() != cfg.crs_epsg:
                liquid = liquid.to_crs(epsg=cfg.crs_epsg)
            liquid.plot(ax=ax, color=INK, linewidth=2.2)
        except Exception as exc:                  # noqa: BLE001 - a map without them
            log.debug("liquid boundaries not drawn: %s", exc)
    for table in tables:
        if len(table):
            ax.scatter(table["x"], table["y"], s=9, color=INK, edgecolor="white",
                       linewidth=0.4, zorder=5)
    # labels in a row above and a row below the reach, with leader lines to a point
    # inside the zone, so that no label covers another or a narrow zone
    height = max(ymax - ymin, 1.0)
    span = xmax - xmin
    anchors = []
    for _, row in zones.iterrows():
        geometry = row.geometry
        parts = list(getattr(geometry, "geoms", [geometry]))
        point = max(parts, key=lambda g: g.area).representative_point()
        anchors.append((point.x, point.y, row["label"]))
    anchors.sort(key=lambda a: a[0])
    rows = (anchors[0::2], anchors[1::2])
    for level, row_anchors in zip((ymax + 0.16 * height, ymin - 0.16 * height), rows):
        for j, (ax_x, ax_y, text) in enumerate(row_anchors):
            x = xmin + (j + 0.5) / len(row_anchors) * span
            ax.plot([x, ax_x], [level, ax_y], color=INK, lw=0.7, zorder=6)
            ax.plot([ax_x], [ax_y], "o", color=INK, ms=3, zorder=7)
            ax.annotate(text, (x, level), fontsize=9, fontweight="bold", ha="center",
                        va="center", zorder=8,
                        bbox={"boxstyle": "round,pad=0.3", "fc": "white", "ec": INK2,
                              "lw": 0.6})
    bar = float(10 ** np.floor(np.log10(span / 4)))
    bar *= 2 if span / 4 >= 2 * bar else 1
    x0, y0 = xmin, ymin - 0.32 * height
    ax.plot([x0, x0 + bar], [y0, y0], color=INK, lw=3)
    ax.text(x0 + bar / 2, y0 + 0.02 * height, f"{bar:g} m", ha="center", va="bottom",
            fontsize=9)
    ax.set_ylim(ymin - 0.36 * height, ymax + 0.26 * height)
    ax.set_aspect("equal")
    ax.set_axis_off()
    handles = [Patch(color=COLORS[0], label="calibrated zone (colors as in posteriors.png)"),
               Patch(facecolor=FIXED, hatch="//", label="fixed zone"),
               Patch(facecolor=FIXED, hatch="xx", label="left out (no or too shallow data)")]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, fontsize=9)
    ax.set_title("Roughness zones and their ks; dots: calibration points", loc="left",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return path


def write_report(cfg, folder: Path) -> dict:
    """Write the report of the calibration in *folder*; returns ``{what: path}``."""
    folder = Path(folder)
    summary = summarize(cfg, folder)
    if summary is None:
        return {}
    out = folder / FOLDER
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    tables = _points(folder)
    if not (summary.get("zone_plan") or {}).get("zones") and tables \
            and cfg.geodata.roughness_zones is not None:
        # a calibration from before the zone plan: count its points per zone here
        try:
            from axqua.calibration_zones import point_zones

            ids, _ = point_zones(cfg, tables)
            for entry in summary["parameters"]:
                if entry["zone"] is not None:
                    entry["points"] = int((ids == entry["zone"]).sum())
                    entry["verdict"] = _verdict({"points": entry["points"]},
                                                entry["flags"],
                                                entry["variance_reduction"])
        except Exception as exc:                  # noqa: BLE001 - verdicts without counts
            log.debug("points per zone not counted: %s", exc)
    steps = [("posteriors", lambda: plot_posteriors(summary, out / "posteriors.png")),
             ("evidence", lambda: plot_evidence(summary, out / "evidence.png"))]
    if cfg.geodata.roughness_zones is not None:
        def zone_files():
            zones = zone_table(cfg, summary)
            gpkg = out / "roughness-zones-calibrated.gpkg"
            if gpkg.exists():
                gpkg.unlink()
            zones.drop(columns=["_color", "zone_id"]).to_file(gpkg, driver="GPKG")
            written["zones"] = gpkg
            return plot_zone_map(cfg, summary, zones, tables, out / "roughness-zones.png")
        steps.append(("map", zone_files))
    for name, step in steps:
        try:
            path = step()
            if path is not None:
                written[name] = Path(path)
        except Exception as exc:                  # noqa: BLE001 - never fails a calibration
            log.warning("calibration report: %s not written: %s", name, exc)
    document = {key: value for key, value in summary.items() if not key.startswith("_")}
    document["files"] = {key: str(value) for key, value in written.items()}
    path = out / "calibration-report.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    written["summary"] = path
    for entry in summary["parameters"]:
        if entry["value"] is not None:
            log.info("calibrated %s = %s (%s)", entry["name"],
                     _value_text(entry["value"], entry["zone"] is not None), entry["verdict"])
    return written
