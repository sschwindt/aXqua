"""Compare the modelled cross-section aggregates against the flume campaign.

The laboratory data is per cross-section - a mean water depth and a velocity maximum -
so the model side has to be reduced the same way rather than sampled at points. This
script does that for each of the three steady discharges and writes one comparison
table, plus a 1:1 plot with the measured standard deviation as error bars.

It needs, per discharge, a converged result file (``r2d-<label>.slf``) and a line layer
of the four cross-sections XS1-XS4 digitised where US2, US4, US5 and US7 are - read off
the CAD once the geometry is in place, saved as
``user-sources/geodata/cross-sections.gpkg`` with a ``Name`` field carrying ``XS1`` and
so on.

    python compare_sections.py               # every discharge that has a result
    python compare_sections.py --flow MQ     # just one
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from axqua import labdata
from axqua.config import load_config
from axqua.logsetup import setup_logging
from axqua.solvers.telemac.sections import line_discharges

HERE = Path(__file__).resolve().parent
SECTIONS = HERE / "user-sources" / "geodata" / "cross-sections.gpkg"
REFERENCE = HERE / "user-sources" / "ground-truth" / "section-reference.csv"

#: label -> the result file the corresponding steady run writes
FLOWS = {"Q30": "r2d-q030.slf", "MQ": "r2d-q135.slf", "HQ100": "r2d-q1000.slf"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=HERE / "case-config.yml")
    parser.add_argument("--flow", action="append", choices=sorted(FLOWS),
                        help="restrict to these discharge labels (repeatable)")
    args = parser.parse_args(argv)
    setup_logging(level=logging.INFO)
    log = logging.getLogger("axqua")

    cfg = load_config(args.config)
    if not SECTIONS.exists():
        raise SystemExit(
            f"{SECTIONS} is missing: digitise the four cross-sections XS1-XS4 at "
            f"US2/US4/US5/US7 as a line layer with a 'Name' field, then re-run")
    if not REFERENCE.exists():
        raise SystemExit(f"{REFERENCE} is missing: run `python lab_reference.py` first")

    import pandas as pd

    reference = pd.read_csv(REFERENCE)
    wanted = args.flow or sorted(FLOWS)
    frames = []
    for label in wanted:
        result = cfg.model_path(FLOWS[label])
        if not result.exists():
            log.warning("%s: no result at %s yet, skipped", label, result)
            continue
        log.info("%s: reducing %s over the cross-sections", label, result.name)
        modelled = line_discharges(result, SECTIONS,
                                   geometry=cfg.model_path(cfg.geometry_slf),
                                   name_field="Name", crs_epsg=cfg.crs_epsg)
        table = labdata.compare(reference[reference.discharge_label == label],
                                modelled)
        table.insert(0, "flow", label)
        frames.append(table)

    if not frames:
        raise SystemExit("no results to compare yet - run the steady cases first")
    joined = pd.concat(frames, ignore_index=True)
    joined.attrs.update(frames[0].attrs)
    out = labdata.write_comparison(
        joined, cfg.postprocessing_path("section-comparison.csv"))
    log.info("wrote %s", out)
    log.info("\n%s", joined.to_string(index=False))
    _plot(joined, cfg.postprocessing_path("section-comparison.png"), log)
    return 0


def _plot(table, path: Path, log) -> None:
    """Modelled against measured, 1:1, with the measured spread as error bars."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as error:                                # pragma: no cover
        log.warning("no plot: %s", error)
        return

    fig, axes = plt.subplots(1, 2, figsize=(11, 5))
    for axis, (measured, modelled, spread, title, unit) in zip(axes, [
            ("h_mean", "mean_depth", "h_std", "mean water depth", "m"),
            ("u_max", "max_velocity", "u_std", "section maximum velocity", "m/s")]):
        if measured not in table or modelled not in table:
            continue
        frame = table.dropna(subset=[measured, modelled])
        axis.errorbar(frame[measured], frame[modelled],
                      xerr=frame[spread] if spread in frame else None,
                      fmt="o", markersize=5, markerfacecolor="none",
                      markeredgecolor="black", ecolor="0.45", elinewidth=0.8,
                      capsize=2, linestyle="none", zorder=3)
        values = list(frame[measured]) + list(frame[modelled])
        if values:
            lo, hi = min(values), max(values)
            pad = 0.08 * (hi - lo or max(abs(hi), 1.0))
            limits = (lo - pad, hi + pad)
            axis.plot(limits, limits, linestyle="--", color="0.35", linewidth=1.1)
            axis.set_xlim(limits)
            axis.set_ylim(limits)
        axis.set_aspect("equal", adjustable="box")
        axis.set_xlabel(f"measured {title} [{unit}]")
        axis.set_ylabel(f"modelled {title} [{unit}]")
        axis.grid(True, color="0.9", linewidth=0.6)
        axis.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)
    log.info("wrote %s", path)


if __name__ == "__main__":
    sys.exit(main())
