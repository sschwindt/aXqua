"""``axqua validation``: what a case needs for a validation, and what it has.

``axqua validation <case-file>`` lists the validation situations of the case
(``calibration.validation``), the inflows of the built model with the discharge each
carries in the calibrated case, whether a finished calibration exists, and the result of
each validation that was run. The form on the *Calibration & validation* tab of the
plugin is filled from this.

The validation itself is a job: ``axqua submit <case-file> --kind validation``.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from axqua.jobcli import _common, _setup_logging, emit, fail, parse_args

log = logging.getLogger("axqua")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axqua validation",
        description="Show the validation situations of a case: measurements of "
                    "another flow situation with which the calibrated model is "
                    "compared. Run one with 'axqua submit <case> --kind validation'.")
    p.add_argument("case", type=Path, help="the case file")
    return _common(p)


def describe(cfg) -> dict:
    """Everything about the validation of *cfg* as one document."""
    from axqua import validation
    from axqua.config import _dump_value

    base = Path(cfg.config_dir) if getattr(cfg, "config_dir", None) else Path.cwd()
    situations = [_dump_value(situation, base)
                  for situation in cfg.calibration.validation]
    try:
        calibrated = validation.calibrated_parameters(cfg)
        found = {"values": calibrated.as_dict(), "estimate": calibrated.estimate,
                 "source": str(calibrated.source)}
    except Exception:                           # noqa: BLE001 - there is none yet
        found = None
    folder = Path(cfg.calibration_dir) / validation.FOLDER
    reports = []
    for path in sorted(folder.glob("validation-*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        figure = path.with_suffix(".png")
        report.update(path=str(path), table=str(path.with_suffix(".csv")),
                      figure=str(figure) if figure.is_file() else "")
        reports.append(report)
    try:
        boundaries = validation.boundaries_of(cfg)
    except Exception:                           # noqa: BLE001 - not built, or unreadable
        boundaries = []
    return {"situations": situations, "boundaries": boundaries,
            "outflow_condition": cfg.boundaries.outflow_condition,
            "calibrated": found, "reports": reports, "folder": str(folder)}


def run_validation(argv: list[str]) -> int:
    args = parse_args(_parser(), argv, "validation")
    _setup_logging(args)
    command = "validation"
    try:
        from axqua.config import load_config

        data = describe(load_config(args.case.expanduser()))
        lines = []
        for situation in data["situations"]:
            flow = situation.get("inflows") or situation.get("prescribed_flowrate")
            lines.append(f"situation {situation['name']}: discharge {flow}, "
                         f"{len(situation.get('sources') or [])} data source(s)")
        if not data["situations"]:
            lines.append("the case names no validation data (calibration.validation)")
        for boundary in data["boundaries"]:
            if boundary["kind"] == "inflow":
                lines.append(f"inflow {boundary['index']}: {boundary['discharge']} m3/s "
                             "in the calibrated case")
        lines.append("calibrated values: " + (
            ", ".join(f"{k} = {v:.4g}" for k, v in data["calibrated"]["values"].items())
            if data["calibrated"] else "none yet; run the calibration first"))
        for report in data["reports"]:
            for quantity, numbers in (report.get("summary") or {}).items():
                if numbers.get("n"):
                    lines.append(
                        f"{report['name']}, {quantity}: n = {numbers['n']}, bias "
                        f"{numbers['bias']:+.3f} ({100 * numbers['relative_bias']:+.0f} "
                        f"%), RMSE {numbers['rmse']:.3f}")
        return emit(command, data, as_json=args.as_json, lines=lines)
    except BaseException as exc:  # noqa: BLE001
        return fail(command, exc, as_json=args.as_json, verbose=args.verbose)


__all__ = ["describe", "run_validation"]
