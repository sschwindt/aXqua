"""The calibration report: what the posterior says about each zone, and where the zones are.

Written after a successful HydroBayesCal calibration into ``<folder>/report/`` (the
calibration folder, or its ``multiflow`` folder for calibration situations), because the
two questions a modeler asks of a calibration need pictures, not a log line (the user's
order of 2026-11-11, after the inn-KB15 trial):

* ``posteriors.png``: for every calibrated parameter, the prior (the range of the
  roughness table) and the posterior of the last iteration, with the calibrated value
  (:func:`axqua.validation.calibrated_from`, the per-parameter posterior maximum, the
  tallest bar of the histogram drawn) marked, and one line on what the data say: how many calibration points lie in the zone,
  whether the posterior is narrow, not identifiable or pinned at a limit, whether the
  largest ks was lowered for shallow water (:mod:`axqua.calibration_zones`);
* ``roughness-zones.png``: a map of every roughness zone with its ks (calibrated, fixed or
  left out), the calibration points and the inflow and outflow lines, in the colors of the
  posterior panels;
* ``evidence.png``: the Bayesian model evidence and the relative entropy over the
  iterations of the active learning (HydroBayesCal's own figure needs LaTeX), with the
  verdict of :func:`convergence` on whether the learning has reached a plateau;
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
import textwrap
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

#: When the active learning has converged (the user's order of 2026-11-22: until the
#: evidence and the relative entropy converge by a literature-informed value, for a
#: literature-informed number of iterations). Oladyshkin et al. (2020, Entropy 22, 890)
#: stop the active learning once the Bayesian model evidence and the relative entropy
#: reach a plateau, and Mouris et al. (2023, Modeling Earth Systems and Environment 9,
#: 3643-3661) judged convergence on the last five iterations. A plateau is therefore
#: :data:`CONVERGENCE_WINDOW` iterations over which neither the natural logarithm of the
#: evidence nor the relative entropy varies by more than :data:`CONVERGENCE_TOLERANCE`.
#: Both are measured in nats, and a factor below e between two evidences, that is, a
#: Bayes factor below e, is "not worth more than a bare mention" (Kass and Raftery 1995,
#: Journal of the American Statistical Association 90, 773-795: 2 ln B < 2). A plateau
#: also needs :data:`CONVERGENCE_MINIMUM` iterations of active learning before it: in
#: Oladyshkin et al. (2020) and in Schwindt et al. (2023, Water Resources Research 59,
#: e2022WR033660), three-parameter calibrations began to converge after 25 iterations,
#: and in Mouris et al. (2023) four parameters after 46. Earlier, short plateaus are
#: false: the inn-KB15 trial had one at iterations 5 to 9, before its evidence fell by
#: 1.9 at iteration 12.
CONVERGENCE_WINDOW = 5
CONVERGENCE_TOLERANCE = 1.0
CONVERGENCE_MINIMUM = 25
#: The fewest consecutive windows of :data:`CONVERGENCE_WINDOW` iterations from which
#: :func:`convergence` judges whether the indicators are leveling off: the last and at
#: least two before it.
SETTLING_WINDOWS = 3


def _window_ranges(values: np.ndarray, window: int) -> list[float]:
    """Ranges of *values* in consecutive windows of *window* entries, ending at the last
    entry and oldest first; entry 0 (the initial design) belongs to none."""
    ranges = []
    end = values.size
    while end - window >= 1:
        ranges.append(float(np.ptp(values[end - window:end])))
        end -= window
    return ranges[::-1]


def settling(log_bme, relative_entropy, *, window: int = CONVERGENCE_WINDOW,
             tolerance: float = CONVERGENCE_TOLERANCE) -> dict:
    """Do the evidence and the relative entropy show a sign of leveling off?

    The iterations of active learning are cut into consecutive windows of *window*
    iterations, ending at the last one. An indicator levels off when it varies by at most
    *tolerance* over the last window, or by less than over a typical earlier window (the
    median of their ranges): its changes are getting smaller. A curve that still rises or
    jumps as much as before shows no sign of a plateau. ``settling`` is True when both
    indicators level off, False when one does not, and None while there are fewer than
    :data:`SETTLING_WINDOWS` windows; ``ranges`` holds the range of the last window and
    the median of the earlier ones per indicator.
    """
    result: dict = {"settling": None, "still_changing": [], "ranges": {}}
    signs = []
    for name, values in (("log_bme", log_bme), ("relative_entropy", relative_entropy)):
        ranges = _window_ranges(np.asarray(values, dtype=float), window)
        if len(ranges) < SETTLING_WINDOWS or not np.all(np.isfinite(ranges)):
            return result
        last, typical = ranges[-1], float(np.median(ranges[:-1]))
        result["ranges"][name] = (last, typical)
        sign = last <= tolerance or last < typical
        signs.append(sign)
        if not sign:
            result["still_changing"].append(name)
    result["settling"] = all(signs)
    return result


def convergence(log_bme, relative_entropy, runs=None, *, window: int = CONVERGENCE_WINDOW,
                tolerance: float = CONVERGENCE_TOLERANCE,
                minimum: int = CONVERGENCE_MINIMUM) -> dict:
    """Has the active learning converged? The evidence and the relative entropy per iteration.

    The first entry belongs to the emulator of the initial design, the others to one
    active-learning iteration each. The learning has converged when, over the last
    *window* iterations of active learning, the natural logarithm of the evidence and the
    relative entropy each vary by at most *tolerance* (:data:`CONVERGENCE_TOLERANCE`),
    and at least *minimum* iterations of active learning have run
    (:data:`CONVERGENCE_MINIMUM`). ``plateau_since`` is the first iteration of the
    trailing run of such windows, and ``plateau_since_run`` the number of parameter sets
    simulated by then (from *runs*); the ranges are those of the last window, and
    ``plateau`` says whether the last window alone holds, whatever the number of
    iterations. ``settling`` and ``still_changing`` are those of :func:`settling`.
    """
    log_bme = np.asarray(log_bme, dtype=float)
    entropy = np.asarray(relative_entropy, dtype=float)
    count = min(log_bme.size, entropy.size)
    result = {"window": int(window), "tolerance": float(tolerance),
              "minimum": int(minimum), "iterations": max(count - 1, 0),
              "converged": False, "plateau": False,
              "log_bme_range": None, "relative_entropy_range": None,
              "plateau_since": None, "plateau_since_run": None,
              "settling": None, "still_changing": [], "settling_ranges": {}}
    if count - 1 < window:
        return result
    trend = settling(log_bme[:count], entropy[:count], window=window, tolerance=tolerance)
    result["settling"], result["still_changing"] = trend["settling"], trend["still_changing"]
    result["settling_ranges"] = trend["ranges"]
    held = []
    for end in range(window, count):
        part = slice(end - window + 1, end + 1)
        spans = (float(np.ptp(log_bme[part])), float(np.ptp(entropy[part])))
        held.append(all(np.isfinite(spans)) and max(spans) <= tolerance)
    result["log_bme_range"], result["relative_entropy_range"] = spans
    result["plateau"] = bool(held[-1])
    result["converged"] = bool(held[-1]) and count - 1 >= minimum
    if held[-1]:
        first = len(held) - 1
        while first > 0 and held[first - 1]:
            first -= 1
        since = first + 1                          # the first iteration of that window
        result["plateau_since"] = since
        if runs is not None and len(runs) > since:
            result["plateau_since_run"] = int(runs[since])
    return result


def _zone(name: str) -> int | None:
    match = _ZONE.match(str(name).strip())
    return int(match.group(1)) if match else None


def _dictionary(folder: Path) -> tuple[Path, dict] | None:
    found = sorted((Path(folder) / RESULTS / "calibration-data").glob("*/BAL_dictionary.pkl"),
                   key=lambda p: p.stat().st_mtime)
    if not found:
        return None
    with open(found[-1], "rb") as handle:
        return found[-1], pickle.load(handle)      # nosec B301 - written by the calibration


def evidence_history(source: Path, data: dict) -> dict:
    """Log evidence and relative entropy per iteration, over every pass of the active learning.

    A calibration that continued its active learning (``calibration.until_converged``)
    ran HydroBayesCal more than once, and each pass began its BAL dictionary anew; the
    finished ones are kept beside *source* as ``BAL_dictionary.upto-<runs>.pkl``
    (:func:`axqua.bayescal.archive_dictionary`). Their histories are joined by the
    number of runs: a pass starts with the emulator of the runs the one before ended
    with, which then counts once, with the values of the later pass.
    """
    from axqua.validation import last_iteration

    def history(values, index):
        if values is None or index is None:
            return []
        return [float(v) if v is not None else None for v in list(values)[:index + 1]]

    found = {}
    archives = sorted(Path(source).parent.glob("BAL_dictionary.upto-*.pkl"),
                      key=lambda p: int(re.sub(r"\D", "", p.name.split("upto-")[1]) or 0))
    for item in [*archives, None]:
        if item is None:
            part = data
        else:
            with open(item, "rb") as handle:
                part = pickle.load(handle)          # nosec B301 - written by the calibration
        index = last_iteration(part)
        runs = history(part.get("N_tp"), index)
        bme = history(part.get("log_BME"), index)
        entropy = history(part.get("RE"), index)
        for n, b, e in zip(runs, bme, entropy):
            if n is not None and b is not None and e is not None:
                found[int(n)] = (b, e)
    order = sorted(found)
    return {"runs": order, "log_bme": [found[n][0] for n in order],
            "relative_entropy": [found[n][1] for n in order]}


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


#: Title and axis label of parameters that are not a roughness zone.
LABELS = {"f.OUTFLOW_OFFSET": ("Outflow level", "offset above the rating level (m)"),
          # the OpenFOAM calibration: one roughness for the whole bed, and the k-epsilon
          # coefficients
          "ks": ("Bed roughness (whole bed)", "ks (cm)"),
          "Cmu": ("Turbulence coefficient Cmu", "Cmu (-)"),
          "C1": ("Turbulence coefficient C1", "C1 (-)"),
          "C2": ("Turbulence coefficient C2", "C2 (-)"),
          "f.OUTFLOW_LOG_SLOPE": ("Energy slope at the outlet",
                                  "log10 of the energy slope of the outflow section (-)")}


def _length(entry: dict) -> bool:
    """Whether a parameter is a roughness height, shown in cm: a zone, or the ks of the
    whole bed."""
    return entry["zone"] is not None or entry["name"] == "ks"


def _value_text(value: float, zone: bool, name: str = "") -> str:
    if name == "f.OUTFLOW_OFFSET":
        return f"{value:+.2f} m"
    if name == "f.OUTFLOW_LOG_SLOPE":
        # the parameter is log10 of the slope; the slope itself is what a reader knows
        return f"S = {10.0 ** value:.2g}"
    if not zone and name != "ks":
        return f"{value:.4g}"
    cm = 100 * value
    return f"{cm:.1f} cm" if cm < 10 else f"{cm:.0f} cm"


def summarize(cfg, folder: Path) -> dict | None:
    """The numbers of the report, or None when *folder* holds no finished calibration."""
    from axqua.calibration_zones import read_plan
    from axqua.validation import calibrated_from, iteration_entry, last_iteration

    found = _dictionary(folder)
    if found is None:
        return None
    source, data = found
    names = [str(n) for n in data.get("calibration_parameters") or []]
    ranges = [list(map(float, r)) for r in data.get("param_values") or []]
    # the last iteration that has run: the entries of the iterations to come are empty
    index = last_iteration(data)

    def last(key):
        return iteration_entry(data, key, index)

    calibrated = calibrated_from(data, source)
    values = calibrated.as_dict()
    spread = last("marginal_peak_spread")
    hdi = last("marginal_hdi")
    reduction = last("variance_reduction")
    flags = last("identifiability_flags") or [[] for _ in names]
    joint = last("joint_optimum")
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
    evidence = evidence_history(source, data)
    evidence["convergence"] = convergence(evidence["log_bme"],
                                          evidence["relative_entropy"], evidence["runs"])
    return {
        "source": str(source),
        "estimate": calibrated.estimate,
        "parameters": parameters,
        "zone_plan": plan,
        "evidence": evidence,
        "posterior_size": int(len(np.atleast_2d(last("posterior")))),
        "_posterior": np.atleast_2d(np.asarray(last("posterior"), dtype=float)),
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
    """One panel per calibrated parameter: prior band, posterior, calibrated value.

    The posterior is the histogram the calibrated value is read from
    (:func:`axqua.validation.marginal_peak`), so the value lies in its tallest bar.
    """
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    from axqua.validation import marginal_peak

    plt = _style()
    parameters = summary["parameters"]
    posterior = summary["_posterior"]
    n = len(parameters)
    cols = 2 if n > 1 else 1
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(6.4 * cols, 3.3 * rows + 0.9),
                             facecolor=SURFACE, squeeze=False)
    # every title at the same height, above the longest verdict
    lines = max(len(textwrap.wrap(entry["verdict"], 72)) or 1 for entry in parameters)
    for k, (ax, entry) in enumerate(zip(axes.flat, parameters)):
        zone = entry["zone"] is not None
        scale = 100.0 if _length(entry) else 1.0
        lo, hi = (entry["prior"] or [np.nanmin(posterior[:, k]), np.nanmax(posterior[:, k])])
        _, counts, edges = marginal_peak(posterior[:, k])
        if counts is None:        # fewer than five samples: no peak, only where they are
            counts, edges = np.histogram(posterior[:, k], bins=np.linspace(lo, hi, 21))
        lo, hi = lo * scale, hi * scale
        edges = np.asarray(edges, dtype=float) * scale
        density = counts / max(int(counts.sum()), 1) / np.diff(edges)
        prior = 1.0 / (hi - lo) if hi > lo else 0.0
        ax.set_facecolor(SURFACE)
        ax.axhspan(0, prior, color=FIXED, lw=0, zorder=1)
        # bars narrower than 2 % of the panel get no gap, or the gap would hide them
        narrow = hi > lo and float(np.min(np.diff(edges))) < 0.02 * (hi - lo)
        ax.bar(edges[:-1], density, width=np.diff(edges), align="edge",
               color=COLORS[k % len(COLORS)] if k < len(COLORS) else MUTED,
               edgecolor=SURFACE, linewidth=0 if narrow else 1.5, zorder=2)
        top = max(float(density.max()) if density.size else 0.0, prior) * 1.32 or 1.0
        value = entry["value"]
        if value is not None:
            v = value * scale
            ax.axvline(v, color=INK, lw=2, zorder=3)
            right = v > lo + 0.6 * (hi - lo)
            ax.text(v + (-1 if right else 1) * 0.015 * (hi - lo), top * 0.93,
                    "calibrated: " + _value_text(value, zone, entry["name"]),
                    ha="right" if right else "left", va="top", fontweight="bold",
                    color=INK, zorder=4)
        ax.set_xlim(lo, hi)
        ax.set_ylim(0, top)
        ax.set_yticks([])
        ax.set_xlabel("ks (cm)" if zone else LABELS.get(entry["name"], ("", entry["name"]))[1])
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.grid(axis="x", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        title = (f"Zone {entry['zone']}" if zone
                 else LABELS.get(entry["name"], (entry["name"],))[0])
        # the verdict wraps within its panel (a long one ran into the next panel)
        verdict = textwrap.wrap(entry["verdict"], 72) or [""]
        ax.set_title(title, loc="left", fontsize=14, fontweight="bold",
                     pad=18 + 13 * (lines - 1))
        # blank lines below a shorter verdict keep its first line under the title
        verdict += [""] * (lines - len(verdict))
        ax.text(0, 1.03, "\n".join(verdict), transform=ax.transAxes, fontsize=10,
                color=INK2, va="bottom")
    for ax in list(axes.flat)[n:]:
        ax.set_visible(False)
    fig.suptitle(f"Posterior of each calibrated parameter ({summary['estimate']})",
                 fontsize=14, fontweight="bold", y=0.995)
    fig.legend(handles=[Patch(color=FIXED, label="prior (calibration range)"),
                        Patch(color=MUTED, label="posterior"),
                        Line2D([], [], color=INK, lw=2,
                               label="calibrated value (tallest bar)")],
               loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 0.955))
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return path


def _convergence_text(state: dict) -> str:
    """One line on the convergence of the active learning, for the evidence figure and the log."""
    window, tolerance = state["window"], state["tolerance"]
    if state["log_bme_range"] is None:
        return (f"Not converged: fewer than {window} active-learning iterations, so no "
                f"plateau can be judged yet.")
    ranges = (f"over the last {window} iterations, the log evidence varies by "
              f"{state['log_bme_range']:.2f} and the relative entropy by "
              f"{state['relative_entropy_range']:.2f} (limit {tolerance:g} each)")
    if state["converged"]:
        since = (f", on this plateau since parameter set {state['plateau_since_run']}"
                 if state.get("plateau_since_run") is not None else "")
        return f"Converged: {ranges}{since}."
    if state.get("plateau"):
        return (f"Not converged yet: {ranges}, but only {state['iterations']} of the at "
                f"least {state['minimum']} active-learning iterations before a plateau "
                f"counts have run.")
    if state.get("settling") is False:
        return (f"Not converged: {ranges}, no less than over a typical earlier window "
                f"of {window} iterations, so there is no sign of a plateau yet.")
    return f"Not converged: {ranges}; more active learning could still change the result."


def plot_evidence(summary: dict, path: Path) -> Path | None:
    """Log Bayesian model evidence and relative entropy over the training runs."""
    from matplotlib.ticker import MaxNLocator

    evidence = summary["evidence"]
    if not evidence["log_bme"]:
        return None
    plt = _style()
    runs = evidence["runs"] or list(range(len(evidence["log_bme"])))
    state = evidence.get("convergence") or convergence(evidence["log_bme"],
                                                       evidence["relative_entropy"], runs)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.0), facecolor=SURFACE)
    window = state["window"]
    for ax, values, label in ((axes[0], evidence["log_bme"], "log of the model evidence"),
                              (axes[1], evidence["relative_entropy"], "relative entropy")):
        ax.set_facecolor(SURFACE)
        if values:
            x = runs[:len(values)]
            if len(values) > window:
                # the iterations the verdict rests on
                ax.axvspan(x[-window] - 0.5, x[-1] + 0.5, color=GRID, lw=0, zorder=0)
            ax.plot(x, values, color=COLORS[0], lw=2, marker="o", ms=4)
        ax.set_xlabel("TELEMAC parameter sets")
        ax.xaxis.set_major_locator(MaxNLocator(integer=True, steps=[1, 2, 5, 10]))  # no run 32.5
        ax.set_title(label, loc="left", fontweight="bold")
        ax.grid(color=GRID, lw=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    fig.text(0.01, 0.01, _convergence_text(state), color=INK2, fontsize=10, va="bottom")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
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


#: The line with which TELEMAC's launcher opens the listing of every run.
_RUN_START = re.compile(r"Running your CAS file\(s\) for:")
_TITLE = re.compile(r"MOTCAR\(\s*1\)=\s*(.+)")


def run_balances(listing: Path, tolerance: float) -> dict | None:
    """Was every TELEMAC run of the calibration steady at its end? From the job's own log.

    *listing* is the output of the calibration job (``solver/stdout.log`` of the job
    folder), which holds the listing of every run TELEMAC made for HydroBayesCal. Each
    run is judged by :func:`axqua.solvers.telemac.flux_convergence.net_imbalance`: the
    trend of the stored volume over the last third of the run (at least 500 s) relative
    to the inflow, against *tolerance* (``hydrodynamics.flux_tolerance``). The user's
    rule of 2026-11-22: the runs have to last until the discharge has converged; the
    inn-KB15 trial of 2026-11-16 to 19 did not, with 49 of its 150 runs ending with
    inflow and outflow more than 0.1 % apart over their last 500 s, up to 5.4 %. None
    when the log holds no run.
    """
    import tempfile

    from axqua.solvers.telemac.flux_convergence import (NET_SHARE, NET_WINDOW,
                                                        net_imbalance, net_window)
    from axqua.solvers.telemac.sortie import read_sortie

    text = Path(listing).read_text(errors="replace")
    starts = [m.start() for m in _RUN_START.finditer(text)]
    if not starts:
        return None
    runs = []
    with tempfile.TemporaryDirectory() as scratch:
        for k, start in enumerate(starts):
            end = starts[k + 1] if k + 1 < len(starts) else len(text)
            part = Path(scratch) / f"run-{k}.sortie"
            part.write_text(text[start:end])
            title = _TITLE.search(text[start:end])
            entry = {"run": k + 1, "title": title.group(1).strip() if title else "",
                     "end": None, "window": None, "net": None, "swing": None}
            try:
                history = read_sortie(part)
            except (ValueError, OSError):
                runs.append(entry)                   # a run that stopped before its first printout
                continue
            entry["end"] = float(history.time[-1]) if history.time.size else None
            measured = net_imbalance(history.time, history.gross_in, history.gross_out)
            if measured is not None:
                entry["window"] = net_window(history.time)
                entry["net"], entry["swing"] = measured
            runs.append(entry)
    judged = [r for r in runs if r["net"] is not None]
    off = [r for r in judged if r["net"] > tolerance]
    return {"window_share": NET_SHARE, "window_min": NET_WINDOW,
            "tolerance": float(tolerance), "runs": len(runs),
            "judged": len(judged), "unbalanced": len(off),
            "worst_net": max((r["net"] for r in judged), default=None),
            "unbalanced_runs": [{key: r[key] for key in
                                 ("run", "title", "end", "window", "net", "swing")}
                                for r in off]}


def _job_listing() -> Path | None:
    """The output of the running job, when the report is written inside one."""
    import os

    folder = os.environ.get("AXQUA_JOB_DIR")
    if not folder:
        return None
    path = Path(folder) / "solver" / "stdout.log"
    return path if path.is_file() else None


def write_report(cfg, folder: Path, listing: Path | None = None) -> dict:
    """Write the report of the calibration in *folder*; returns ``{what: path}``.

    *listing* is the output of the calibration job with the listings of its TELEMAC
    runs (default: that of the job this runs in), for :func:`run_balances`.
    """
    folder = Path(folder)
    summary = summarize(cfg, folder)
    if summary is None:
        return {}
    listing = listing or _job_listing()
    if listing is not None:
        try:
            summary["run_balance"] = run_balances(
                listing, float(cfg.hydrodynamics.flux_tolerance))
        except Exception as exc:                  # noqa: BLE001 - the rest of the report stands
            log.warning("calibration report: the balance of the runs not read: %s", exc)
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
    if cfg.geodata.roughness_zones is not None and any(
            entry["zone"] is not None for entry in summary["parameters"]):
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
                     _value_text(entry["value"], entry["zone"] is not None, entry["name"]), entry["verdict"])
    state = summary["evidence"]["convergence"]
    (log.info if state["converged"] else log.warning)(
        "active learning: %s", _convergence_text(state))
    balance = summary.get("run_balance")
    if balance:
        text = (f"{balance['unbalanced']} of {balance['judged']} TELEMAC runs ended with "
                f"the volume in the reach still changing by more than "
                f"{100 * balance['tolerance']:g} % of the inflow over the last third of "
                f"the run")
        if balance["unbalanced"]:
            log.warning("calibration runs: %s (worst %.2f %%); lengthen them with "
                        "calibration.hotstart_duration", text, 100 * balance["worst_net"])
        else:
            log.info("calibration runs: %s", text)
    return written
