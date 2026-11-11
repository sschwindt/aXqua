"""``axqua calibration``: which roughness zones a calibration of the case would vary.

``axqua calibration <case-file>`` compiles the calibration points exactly as the
calibration does (each calibration situation, or the case's own ground truth; points
outside the model and near a liquid boundary left out) into a temporary folder, and
reports the zone plan of :mod:`axqua.calibration_zones`: for every zone the roughness
table calibrates, its points, the median measured depth and whether it is calibrated,
calibrated with a lower largest ks, or left out. The plugin asks this before it submits
a calibration, so that the user can confirm the zones or go and look at the measurements
in QGIS. Nothing of the case is written.

The calibration itself is a job: ``axqua submit <case-file> --kind calibration``.
"""

from __future__ import annotations

import argparse
import copy
import logging
import tempfile
from pathlib import Path

from axqua.jobcli import _common, _setup_logging, emit, fail, parse_args

log = logging.getLogger("axqua")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axqua calibration",
        description="Show which roughness zones a calibration of the case would vary, "
                    "from where its measurement points lie. Run the calibration with "
                    "'axqua submit <case> --kind calibration'.")
    p.add_argument("case", type=Path, help="the case file")
    return _common(p)


def describe(cfg) -> dict:
    """The zone plan of a calibration of *cfg*, and the points it rests on."""
    from axqua import bayescal
    from axqua.calibration_zones import point_zones, zone_plan
    from axqua.validation import slug

    work = Path(tempfile.mkdtemp(prefix="axqua-calibration-"))
    there = copy.deepcopy(cfg)
    there.calibration_dir = str(work)
    if there.ground_truth.sources and there.ground_truth.measurements is None:
        # compiled into the temporary folder, not over the case's own table
        there.ground_truth.measurements = work / "ground-truth.xlsx"
    bayescal._set_quantities(there)
    situations = bayescal.calibration_situations(there)
    groups = []
    if situations:
        folder = work / bayescal.MULTIFLOW
        folder.mkdir(parents=True, exist_ok=True)
        for situation in situations:
            _, table = bayescal.situation_points(there, situation, folder)
            groups.append((situation.name, slug(situation.name), table))
    else:
        _, table = bayescal.build_velocity_csv(there)
        groups.append(("calibration", "calibration", table))
    tables = [table for _, _, table in groups]
    plan = zone_plan(there, tables)
    points = []
    if plan.zones:
        ids, depths = point_zones(there, tables)
        k = 0
        for name, _, table in groups:
            for x, y in zip(table["x"], table["y"]):
                depth = float(depths[k])
                points.append({"situation": name, "x": float(x), "y": float(y),
                               "zone": int(ids[k]),
                               "depth": depth if depth == depth else None})
                k += 1
    sources = []
    for situation in situations or []:
        for source in situation.sources or []:
            sources.append({"situation": situation.name,
                            "positions": str(getattr(source, "positions", "") or ""),
                            "values": str(getattr(source, "values", "") or "")})
    data = plan.as_dict()
    data.update(
        situations=[name for name, _, _ in groups],
        points=points,
        roughness_zones=str(cfg.geodata.roughness_zones or ""),
        roughness_table=str(cfg.geodata.roughness_table or ""),
        zone_field=cfg.mesh.roughness_zone_field,
        crs_epsg=int(cfg.crs_epsg),
        sources=sources,
        needs_confirmation=bool(plan.excluded or plan.capped))
    return data


def run_calibration(argv: list[str]) -> int:
    args = parse_args(_parser(), argv, "calibration")
    _setup_logging(args)
    command = "calibration"
    try:
        from axqua.config import load_config

        data = describe(load_config(args.case.expanduser()))
        lines = [f"calibration points from: {', '.join(data['situations'])}"]
        for zone in data["zones"]:
            depth = (f", median depth {zone['median_depth']:.2f} m"
                     if zone["median_depth"] is not None else "")
            lines.append(f"zone {zone['zone']}: {zone['points']} points{depth}: "
                         f"{zone['decision']}" + (f" ({zone['reason']})"
                                                  if zone["reason"] else ""))
        lines += [f"warning: {w}" for w in data["warnings"]]
        lines.append("calibration parameters: "
                     + ", ".join(p["name"] for p in data["parameters"]))
        return emit(command, data, as_json=args.as_json, lines=lines)
    except BaseException as exc:  # noqa: BLE001
        return fail(command, exc, as_json=args.as_json, verbose=args.verbose)


__all__ = ["describe", "run_calibration"]
