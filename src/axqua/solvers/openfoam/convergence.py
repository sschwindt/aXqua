"""Mesh convergence of the OpenFOAM free-surface model.

The same study as for TELEMAC (:mod:`axqua.convergence`): the same case on a ladder of
meshes with one refinement factor, the water depth and the velocity at the probe points
of the case, and the grid convergence index of the finest three meshes (Celik et al.
2008). Only the mesh and the result differ:

* the mesh of a level is the lattice of the OpenFOAM case at the plan spacing of that
  level (``openfoam.cell_size``), the baseline being the spacing the case asks for
  (``cell_size`` or ``cell_size_factor``); the number of layers stays, so the study
  is a study of the plan resolution, as the TELEMAC one is;
* the quantities are read from the result seen from above
  (:mod:`axqua.solvers.openfoam.planview`), the depth-averaged velocity and the water
  depth per column, which is what the TELEMAC study compares as well.

Every level starts from the same 2D result, the seed the job prepares
(:func:`axqua.jobs.executor._pre_step`); the levels differ by their mesh alone. A level
is built and run in a folder of its own below ``<postprocessing>/openfoam-mesh-
convergence/`` and can be resumed like a TELEMAC level (``level.json``).

The checks of the TELEMAC turbulence and roughness closure (:mod:`axqua.mesh_validity`)
do not apply to these levels and are not made.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from typing import Any

from axqua.convergence import (DEFAULT_TOLERANCE, LevelResult, _cfl_dt, _geom_stats,
                               _slug, _write_level_json, extract_at_points,
                               run_mesh_convergence)
from axqua.core import selafin

log = logging.getLogger("axqua.solvers.openfoam.convergence")

#: The folder of the study, in the post-processing folder of the case.
FOLDER = "openfoam-mesh-convergence"
#: The stem of its report files.
STEM = "openfoam-mesh-convergence"


def openfoam_levels(cfg, ratio: float = 1.3, n_coarser: int = 1,
                    n_finer: int = 2) -> list[tuple[str, float, float]]:
    """The ladder of plan spacings, coarse to fine, as ``(label, dx, dx)``.

    The same form as :func:`axqua.convergence.ratio_levels`, whose second and third
    entries are the channel and the floodplain size of a TELEMAC mesh; a lattice has
    one spacing for both.
    """
    from axqua.solvers.openfoam.mesh import plan_spacing

    if ratio <= 1.0:
        raise ValueError(f"refinement ratio must exceed 1.0 (got {ratio}); "
                         "Celik et al. (2008) recommend >= 1.3")
    base = float(plan_spacing(cfg))
    levels = []
    for k in range(n_coarser, -(n_finer + 1), -1):     # coarse (large) -> fine (small)
        f = ratio ** k
        label = ("baseline" if k == 0 else f"coarser x{f:.2f}" if k > 0
                 else f"finer x{1.0 / f:.2f}")
        levels.append((label, base * f, base * f))
    return levels


def make_openfoam_simulator(cfg, discharge: float | None, *, base_dir: Path,
                            seed: Any = None, n_processors: int | None = None,
                            check_cancelled=None, backend=None, plan_view=None,
                            spacing: float | None = None):
    """A ``simulate(label, dx, dx, probes, quantities) -> LevelResult`` that builds the OpenFOAM case at the plan spacing *dx*, runs it, and samples the result seen from above at the probe points.

    *seed* is the 2D result every level starts from; *spacing* is the plan spacing the case asks for, against which the size scale of a level is given (read from the case when not given). *backend* replaces the OpenFOAM backend and *plan_view* the writer of the result seen from above, ``(cfg, folder) -> (path, summary)`` (both for tests).
    """
    if backend is None:
        from axqua.solvers.openfoam.backend import BACKEND as backend
    if plan_view is None:
        from axqua.solvers.openfoam.planview import write as plan_view
    if spacing is None:
        from axqua.solvers.openfoam.mesh import plan_spacing

        spacing = plan_spacing(cfg)
    # the size scale of a level is its spacing against the one the case asks for
    base = float(spacing)

    def simulate(label, dx, _dx, probes, quantities):
        if check_cancelled is not None:
            check_cancelled()
        level = copy.deepcopy(cfg)
        level.openfoam.cell_size = float(dx)
        level.openfoam.cell_size_factor = None
        folder = Path(base_dir) / _slug(label)
        level.openfoam_dir = folder
        if discharge is not None:
            level.boundaries.prescribed_flowrate = float(discharge)
        start = time.time()
        artifacts = backend.build(level, None, seed=seed)
        if check_cancelled is not None:
            check_cancelled()
        backend.run(level, None, n_processors=n_processors)
        runtime = time.time() - start
        result, _summary = plan_view(level, folder)
        data = selafin.read_slf(result, frame=-1)
        min_edge, mean_depth, mean_velocity = _geom_stats(data, probes)
        cells = getattr(getattr(artifacts, "mesh", None), "n_cells", None)
        out = LevelResult(
            label=label, cell_size=float(dx), n_elem=int(cells or data["ikle"].shape[0]),
            n_nodes=int(data["x"].size), min_edge=min_edge,
            dt=_cfl_dt(min_edge, mean_depth, mean_velocity), runtime_s=runtime,
            qoi=extract_at_points(result, quantities, probes), scale=float(dx) / base,
            mean_depth=mean_depth, mean_velocity=mean_velocity)
        _write_level_json(folder, out, float(dx), float(dx))
        return out

    return simulate


def run_openfoam_mesh_convergence(cfg, *, seed: Any = None,
                                  discharge: float | None = None,
                                  refinement_ratio: float = 1.3,
                                  tolerance: float = DEFAULT_TOLERANCE,
                                  n_coarser: int = 1, n_finer: int = 2,
                                  max_extra_levels: int = 3, auto_extend: bool = False,
                                  n_processors: int | None = None, ask=None,
                                  on_level=None, check_cancelled=None, probes=None,
                                  backend=None, plan_view=None):
    """Run the study and return its :class:`~axqua.convergence.ConvergenceReport`."""
    from axqua.solvers.openfoam import planview
    from axqua.solvers.openfoam.mesh import plan_spacing

    base_dir = Path(cfg.postprocessing_dir) / FOLDER
    spacing = float(plan_spacing(cfg))
    levels = openfoam_levels(cfg, ratio=refinement_ratio, n_coarser=n_coarser,
                             n_finer=n_finer)
    simulate = make_openfoam_simulator(cfg, discharge, base_dir=base_dir, seed=seed,
                                       n_processors=n_processors,
                                       check_cancelled=check_cancelled,
                                       backend=backend, plan_view=plan_view,
                                       spacing=spacing)
    log.info("OpenFOAM mesh convergence: %d levels, plan spacing %s m",
             len(levels), ", ".join(f"{dx:.3f}" for _, dx, _ in levels))
    report = run_mesh_convergence(
        cfg, discharge=float(discharge or cfg.boundaries.prescribed_flowrate or 0.0),
        levels=levels, tolerance=tolerance, simulate=simulate, base_dir=base_dir,
        extend_ratio=refinement_ratio, max_extra_levels=max_extra_levels,
        auto_extend=auto_extend, ask=ask, on_level=on_level, probes=probes,
        result_name=planview.RESULT_NAME, check_validity=False)
    # A level taken over from an earlier study carries the scale its level.json stored, which before 2026-10-17 was relative to the first level simulated; the file is corrected too, so that the next resume reads the right one.
    for level in report.levels:
        scale = level.cell_size / spacing
        if abs(level.scale - scale) > 1e-9:
            level.scale = scale
            _correct_scale(base_dir / _slug(level.label) / "level.json", scale)
    return report


def _correct_scale(path: Path, scale: float) -> None:
    """Write the size scale into the ``level.json`` of a level, if it has one. A file that cannot be read or written is left as it is: the report is right either way, and a study of hours must not end in an error over its metadata."""
    try:
        if not path.is_file():
            return
        meta = json.loads(path.read_text())
        if not isinstance(meta, dict):
            raise ValueError("not a level description")
        meta["scale"] = scale
        path.write_text(json.dumps(meta, indent=2) + "\n")
    except (OSError, ValueError) as exc:
        log.warning("could not correct the size scale in %s (%s)", path, exc)
