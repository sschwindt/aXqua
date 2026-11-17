"""The OpenFOAM calibration and validation at the discharges of measured situations.

What has to happen above both backends, next to :mod:`axqua.prerun`. A calibration run of the OpenFOAM model runs under a rigid lid that follows the water surface of a 2D result, so every situation (the discharge of a survey) needs a TELEMAC result at its own discharge. This module

* runs those TELEMAC results (:func:`seed_runs`): the built case at the situation's boundary values, started like the TELEMAC calibration runs, with the calibrated TELEMAC values when the case has a TELEMAC calibration (roughness zones and the energy slope at the outlet, so the water surface the lid follows is the calibrated one);
* hands the OpenFOAM backend the rules of the case that need the 2D mesh (:func:`point_filter`: points inside the model and at least :data:`axqua.bayescal.MIN_BOUNDARY_NODES` nodes from an inflow or outflow line), because the OpenFOAM backend does not read TELEMAC files;
* collects both for the job (:func:`prepare`).

The OpenFOAM backend must not reach into the TELEMAC backend (``tests/test_capabilities.py``), which is why this lives here and is called by the job executor.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("axqua")

#: The file names of the TELEMAC runs that give the OpenFOAM model its water surfaces.
SEED_STEM = "openfoam-seed"


def calibration_situations(cfg) -> list:
    """The situations of the OpenFOAM calibration.

    ``calibration.situations`` (with the case's own discharge first when ``ground_truth`` holds measurements, as for TELEMAC); a case without situations is calibrated at its own discharge, against ``ground_truth``.
    """
    from axqua import bayescal

    # the case's own situation with all its boundary values (the discharge of each
    # inflow line, the outflow level), as the TELEMAC calibration builds it
    return bayescal.calibration_situations(cfg) or [bayescal.case_situation(cfg)]


def telemac_values(cfg) -> dict[str, float]:
    """The calibrated TELEMAC values of the case, or none when it has no TELEMAC calibration."""
    from axqua import validation
    from axqua.core.errors import ConfigError

    try:
        found = validation.calibrated_parameters(cfg)
    except ConfigError:
        log.warning("the case has no TELEMAC calibration: the water surfaces the OpenFOAM "
                    "model follows come from the built case, with its roughness and its "
                    "outflow condition")
        return {}
    log.info("water surfaces of the OpenFOAM model from TELEMAC with the %s of %s: %s",
             found.estimate, _campaign(found.source),
             ", ".join(f"{k} = {v:.4g}" for k, v in found.as_dict().items()))
    return found.as_dict()


def _stamp(cfg, setup) -> dict[str, Any]:
    """What a seed run depends on: if any of it changes, the run is repeated."""
    def mtime(path) -> float:
        path = Path(path)
        return round(path.stat().st_mtime, 3) if path.exists() else 0.0

    return {"parameters": {k: round(float(v), 10) for k, v in setup.parameters.items()},
            "flowrates": setup.flowrates, "elevations": setup.elevations,
            "geometry": mtime(cfg.model_path(cfg.geometry_slf)),
            "steady": mtime(cfg.model_path(cfg.results_slf))}


def seed_runs(cfg, situations, *, parameters: dict[str, float] | None = None,
              ctx: Any = None, ncsize: int | None = None, role: str = "calibration",
              stem: str = SEED_STEM) -> dict[str, Path]:
    """A TELEMAC result at the discharge of each situation, run where it is missing.

    *parameters* defaults to :func:`telemac_values`. A result is reused when it was run with the same values, boundary values, geometry and steady result (``<result>.json`` beside it records them); otherwise it is run again. Returns ``{tag: result}``.
    """
    from axqua import validation
    from axqua.core import registry
    from axqua.core.errors import SolverError

    from axqua import bayescal
    from axqua.core.errors import ConfigError

    if bayescal.other_calibration_running(cfg.model_dir):
        # validation.build restores the built case, which a running calibration is
        # rewriting run by run
        raise ConfigError("a TELEMAC calibration of this case is running, and the TELEMAC "
                          "runs that give the OpenFOAM model its water surfaces would "
                          "write into the same model folder",
                          subject=str(cfg.model_dir), remedy="Wait until it has finished.")
    if parameters is None:
        parameters = telemac_values(cfg)
    backend = None
    seeds: dict[str, Path] = {}
    for situation in situations:
        tag = validation.slug(situation.name)
        setup = validation.build(cfg, situation, parameters, stem=stem, role=role)
        stamp = _stamp(cfg, setup)
        record = setup.results.with_suffix(".json")
        if setup.results.is_file() and record.is_file():
            try:
                if json.loads(record.read_text(encoding="utf-8")) == stamp:
                    log.info("water surface of %s: %s (reused)", situation.name,
                             setup.results.name)
                    seeds[tag] = setup.results
                    continue
            except ValueError:
                pass
        record.unlink(missing_ok=True)
        if backend is None:
            backend = registry.get("telemac").load()
        log.info("water surface of %s: TELEMAC at %.4g m3/s -> %s", situation.name,
                 setup.flowrate, setup.results.name)
        if ctx is not None:
            ctx.sink.step(f"TELEMAC at {situation.name}")
        backend._launch(cfg, ctx=ctx, cas_file=setup.cas.name,
                        ncsize=ncsize or cfg.telemac.n_processors, solver=None,
                        name="telemac2d",
                        duration=situation.duration or validation.run_duration(setup))
        if not setup.results.is_file():
            raise SolverError(f"the TELEMAC run of {situation.name} wrote no result",
                              subject=str(setup.cas), component="telemac2d")
        _check_balance(cfg, setup, situation.name)
        record.write_text(json.dumps(stamp, indent=1), encoding="utf-8")
        seeds[tag] = setup.results
    return seeds


def _check_balance(cfg, setup, name: str) -> None:
    """Warn when the seed run had not reached a steady state."""
    from axqua.solvers.telemac import sortie
    from axqua.solvers.telemac.flux_convergence import relative_imbalance

    listing = sortie.latest_sortie(cfg.model_dir, setup.cas.name)
    if listing is None:
        return
    history = sortie.read_sortie(listing)
    tail = relative_imbalance(history.gross_in, history.gross_out)[-10:]
    if tail.size and float(np.nanmean(tail)) > float(cfg.hydrodynamics.flux_tolerance):
        log.warning("the TELEMAC run of %s had not reached a steady state: inflow and "
                    "outflow still differed by %.2f %%; the OpenFOAM model follows its "
                    "water surface all the same", name, 100 * float(np.nanmean(tail)))


def point_filter(cfg, points) -> np.ndarray:
    """Which measurement points the OpenFOAM model is compared at.

    Those inside the 2D model and at least :data:`axqua.bayescal.MIN_BOUNDARY_NODES` mesh nodes from every inflow and outflow line (the user's rule of 2026-11-04), as for the TELEMAC calibration. *points* needs columns ``x`` and ``y``.
    """
    from axqua import bayescal

    if len(points) == 0:
        return np.zeros(0, dtype=bool)
    x = points["x"].to_numpy(float)
    y = points["y"].to_numpy(float)
    # the row number survives the outline check, which renumbers a column "id"
    probe = points[["x", "y"]].reset_index(drop=True).assign(row=np.arange(len(points)))
    kept = bayescal._inside_the_model(cfg, probe)["row"].to_numpy(int)
    keep = np.zeros(len(points), dtype=bool)
    keep[kept] = True
    keep &= bayescal.away_from_liquid_boundaries(cfg, x, y)
    return keep


def _seed_stamp(seed: Path | None) -> str | None:
    """The record of the run that wrote *seed* (``<result>.json``), which changes with every new run."""
    if seed is None or not Path(seed).is_file():
        return None
    record = Path(seed).with_suffix(".json")
    text = record.read_text(encoding="utf-8") if record.is_file() else ""
    return f"{Path(seed).stat().st_mtime_ns} {text}"


def situation_specs(cfg, situations, seeds: dict[str, Path]) -> list[dict[str, Any]]:
    """What the OpenFOAM backend needs of each situation: plain values, no TELEMAC objects."""
    from axqua import validation

    specs = []
    for situation in situations:
        tag = validation.slug(situation.name)
        q = situation.total_flowrate
        if q is None:
            q = cfg.boundaries.prescribed_flowrate
        specs.append({"name": situation.name, "tag": tag, "discharge": float(q),
                      "sources": list(situation.sources), "seed": seeds.get(tag),
                      "duration": situation.duration,
                      # what the template was built from: a new seed rebuilds it
                      "seed_stamp": _seed_stamp(seeds.get(tag))})
    return specs


def prepare(cfg, *, validate: bool = False, situation: str = "", ctx: Any = None,
            ncsize: int | None = None) -> dict[str, Any]:
    """The keywords the OpenFOAM calibration or validation job hands its backend.

    The water surface of every situation from TELEMAC (run where missing) and the point filter of the case.
    """
    from axqua import validation

    # everything that can refuse the job is checked before the TELEMAC runs, which
    # cost approximately half an hour each on a real reach
    if validate:
        chosen = [validation.situation_named(cfg, situation or None)]
        role = "validation"
        values = openfoam_values(cfg)
    else:
        from axqua.solvers.openfoam.calibration import require_multiflow_driver

        chosen = calibration_situations(cfg)
        role = "calibration"
        _distinct_names(chosen)
        require_multiflow_driver()
    _check_inflows(cfg, chosen, role)
    seeds = seed_runs(cfg, chosen, ctx=ctx, ncsize=ncsize, role=role)
    out = {"situations": situation_specs(cfg, chosen, seeds), "point_filter": point_filter}
    if validate:
        out["values"] = values
    return out


def _distinct_names(situations) -> None:
    """Refuse two situations whose names give the same folder."""
    from axqua import validation
    from axqua.core.errors import ConfigError

    tags = [validation.slug(s.name) for s in situations]
    if len(set(tags)) != len(tags):
        raise ConfigError("two calibration situations have the same name",
                          subject="calibration.situations",
                          remedy="Give every situation a name of its own.")


def _check_inflows(cfg, situations, role: str) -> None:
    """Refuse a situation whose split among the inflow lines the OpenFOAM model cannot follow.

    The OpenFOAM model takes the discharge of each inlet from the ``Target flow`` of its line, scaled to the total of the situation (:func:`axqua.solvers.openfoam.case._inlet_discharges`). A situation that gives every inflow line its own discharge is therefore followed only when its discharges keep the proportions of those of the case; otherwise the TELEMAC run whose water surface the rigid lid follows and the OpenFOAM inlets would disagree.
    """
    from axqua import bayescal
    from axqua.core.errors import ConfigError

    case = bayescal.case_inflows(cfg)
    for situation in situations:
        given = {int(k): float(v) for k, v in (situation.inflows or {}).items()}
        if not given:
            continue
        same = bool(case) and set(given) == set(case)
        if same:
            total, own = sum(given.values()), sum(case.values())
            same = total > 0 and own > 0 and all(
                abs(given[k] / total - case[k] / own) <= 1e-6 for k in case)
        if not same:
            raise ConfigError(
                f"the {role} situation {situation.name!r} gives its inflow lines "
                "discharges in other proportions than the Target flow of the lines of "
                "the case, and the OpenFOAM model splits the total of a situation by "
                "those of the case",
                subject=f"calibration.{'validation' if role == 'validation' else 'situations'}"
                        ".inflows",
                remedy="Give the situation its total discharge only, or enter the same "
                       "split in the Target flow of the inflow lines.")


def openfoam_values(cfg) -> dict[str, float]:
    """The calibrated values of the newest OpenFOAM calibration of the case."""
    from axqua import validation
    from axqua.solvers.openfoam.calibration import CAMPAIGN_SUBDIR, MULTIFLOW_SUBDIR

    root = Path(cfg.calibration_dir) / CAMPAIGN_SUBDIR
    found = validation.calibrated_parameters(cfg, roots=(root / MULTIFLOW_SUBDIR, root))
    log.info("OpenFOAM validation with the %s of %s: %s", found.estimate,
             _campaign(found.source),
             ", ".join(f"{k} = {v:.4g}" for k, v in found.as_dict().items()))
    return found.as_dict()


def _campaign(source: Path) -> str:
    """The folder of the calibration that wrote *source*
    (``<folder>/auto-saved-results-HydroBayesCal/calibration-data/<quantity>/BAL_dictionary.pkl``)."""
    return Path(source).parents[3].name
