"""Orchestration: config -> datasets -> scenes -> files.

The only module here that knows about both halves. It asks each enabled backend
what it has produced (:meth:`axqua.core.registry.SolverBackend.describe_results`),
matches that against the requested scenes, and hands the pairs to a rendering
backend.

It does **not** import a solver: the datasets arrive through the registry as
plain data, which is the whole point of the ``describe_results`` seam.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from axqua.postproc import scenes as scene_mod
from axqua.postproc.dataset import Dataset

log = logging.getLogger("axqua")


def datasets(cfg, *, solver: str | None = None) -> list[Dataset]:
    """Every result this case can show, across the enabled backends.

    Never raises for one misbehaving backend: a listing that dies because a solver
    it does not care about has a broken case directory is useless.
    """
    from axqua.core.registry import backends

    out: list[Dataset] = []
    for spec in backends():
        if solver is not None and spec.name != solver:
            continue
        try:
            if not spec.is_enabled(cfg):
                continue
            out.extend(spec.load().describe_results(cfg))
        except Exception as exc:  # noqa: BLE001
            log.debug("%s described no results: %s: %s",
                      spec.name, type(exc).__name__, exc)
    return out


def case_extras(cfg) -> list[str]:
    """Requirements satisfied by the case rather than by a result file.

    Currently just the ground truth, which is what the ``profiles`` scene compares
    against - it is a property of the case, not of the SELAFIN or the OpenFOAM
    directory, so a dataset could never report it.
    """
    extras = []
    try:
        if Path(cfg.ground_truth_path).exists():
            extras.append("groundtruth:hydraulics")
    except Exception as exc:  # noqa: BLE001
        log.debug("no ground truth: %s: %s", type(exc).__name__, exc)
    return extras


def measurement_points(cfg) -> list[dict]:
    """The verticals the ``profiles`` scene samples along.

    Each carries the bed elevation and the measured depth, so the lineout spans
    the actual water column rather than a guessed range.
    """
    from axqua.ground_truth import read_tidy

    tables = read_tidy(cfg.ground_truth_path)
    if "hydraulics" not in tables:
        return []
    df = tables["hydraulics"].reset_index(drop=True)
    points = []
    for i, row in df.iterrows():
        bed = float(row["z"]) if "z" in df.columns else 0.0
        depth = float(row["h"]) if "h" in df.columns else 1.0
        points.append({"id": f"{i + 1:02d}", "x": float(row["x"]),
                       "y": float(row["y"]), "z_bed": bed,
                       "z_top": bed + depth, "depth": depth})
    return points


def plan(cfg, *, names: Sequence[str] | None = None,
         solver: str | None = None) -> list[tuple]:
    """``(scene, dataset|None, reason)`` for every requested scene."""
    return scene_mod.plan(datasets(cfg, solver=solver), names=names,
                          extras=case_extras(cfg))


def plan_lines(cfg, *, names: Sequence[str] | None = None,
               solver: str | None = None) -> list[str]:
    """The plan as text, for ``axqua postproc --list``."""
    rows = plan(cfg, names=names, solver=solver)
    if not rows:
        return ["no scenes requested"]
    width = max(len(scene.name) for scene, _, _ in rows)
    out = []
    for scene, dataset, reason in rows:
        if dataset is not None:
            out.append(f"  {scene.name:<{width}}  available  ({dataset.solver})")
        else:
            out.append(f"  {scene.name:<{width}}  unavailable - {reason}")
    return out


def render(cfg, *, names: Sequence[str] | None = None, solver: str | None = None,
           out_dir: Path | None = None, script_only: bool = False) -> int:
    """Render the requested scenes. Returns a process exit code."""
    from axqua.postproc import visit as visit_backend

    if cfg.postproc.backend != "visit":
        raise SystemExit(f"unknown postproc.backend {cfg.postproc.backend!r}")

    figures_root = Path(out_dir) if out_dir else Path(cfg.postprocessing_dir) / "figures"
    script_root = Path(cfg.postprocessing_dir) / "visit"
    rows = plan(cfg, names=names, solver=solver)

    runnable = [(scene, dataset) for scene, dataset, _ in rows if dataset is not None]
    for scene, _, reason in rows:
        if reason:
            print(f"  {scene.name}: unavailable - {reason}")
    if not runnable:
        print("nothing to render.")
        return 1

    extras = case_extras(cfg)
    points = measurement_points(cfg) if any(
        s.render == "profiles" for s, _ in runnable) else []

    runtime = None
    if not script_only:
        runtime = visit_backend.VisitRuntime(cfg.postproc)
        print(f"using {runtime.check_available()}")

    failures = 0
    for scene, dataset in runnable:
        out_dir_scene = figures_root / dataset.solver
        out_dir_scene.mkdir(parents=True, exist_ok=True)
        text = visit_backend.render_script(
            dataset, scene, out_dir_scene,
            width=cfg.postproc.image_width, height=cfg.postproc.image_height,
            extras=extras, points=points)
        script = visit_backend.write_script(text, script_root / f"{scene.name}.py")
        print(f"  {scene.name}: script -> {script}")
        if script_only:
            continue

        proc = runtime.execute(script)
        problem = visit_backend.failed(proc)
        if problem:
            # One failed figure must not discard the others: rendering is the
            # cheapest step in this whole chain and the least worth aborting for.
            print(f"  {scene.name}: FAILED - {problem}")
            failures += 1
            continue
        print(f"  {scene.name}: rendered into {out_dir_scene}")

        if scene.render == "profiles":
            from axqua.postproc import profiles as profiles_mod
            try:
                produced = profiles_mod.compare(
                    cfg, out_dir_scene / "data", out_dir_scene)
                for path in produced:
                    print(f"    wrote {path.name}")
            except Exception as exc:  # noqa: BLE001
                print(f"    profile comparison skipped: {type(exc).__name__}: {exc}")

    # dedupe by path, not with a set: Dataset carries a Mapping (vector_fields),
    # so the frozen dataclass is not hashable
    seen: set = set()
    for _, dataset in runnable:
        if dataset.path in seen:
            continue
        seen.add(dataset.path)
        for note in dataset.notes:
            print(f"note: {note}")

    return 1 if failures else 0
