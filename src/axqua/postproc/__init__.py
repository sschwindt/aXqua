"""Post-processing: turn solver results into figures.

Solver-agnostic by construction. It never imports a backend; each backend
*describes* what it produced through
:meth:`axqua.core.registry.SolverBackend.describe_results`, and this package
draws whatever comes back. A test asserts the direction of that dependency.

Deliberately a top-level package rather than part of ``core``: it renders
matplotlib and writes VTK, and ``tests/test_capabilities.py`` requires that
listing a case's capabilities imports nothing heavy. It sits beside the other
cross-cutting, solver-neutral modules (``campaigns``, ``convergence``).

Attributes resolve lazily (PEP 562), so ``import axqua.postproc`` costs nothing
until a figure is actually asked for.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

_EXPORTS: dict[str, tuple[str, ...]] = {
    "dataset": ("Dataset",),
    "scenes": ("Scene", "available_names", "check", "plan", "resolve"),
    "render": ("case_extras", "datasets", "plan_lines", "render"),
    "visit": ("VisitRuntime", "render_script", "write_script"),
}
_SUBMODULES = ("dataset", "profiles", "render", "scenes", "selafin_vtk", "visit")

_NAME_TO_MODULE = {name: module
                   for module, names in _EXPORTS.items()
                   for name in names}

__all__ = ["Dataset", "Scene", "VisitRuntime", "available_names", "case_extras",
           "check", "datasets", "plan", "plan_lines", "render", "render_script",
           "resolve", "write_script", *_SUBMODULES]

if TYPE_CHECKING:  # pragma: no cover
    from axqua.postproc.dataset import Dataset
    from axqua.postproc.render import case_extras, datasets, plan_lines, render
    from axqua.postproc.scenes import Scene, available_names, check, plan, resolve
    from axqua.postproc.visit import VisitRuntime, render_script, write_script


def __getattr__(name: str):
    if name in _SUBMODULES:
        return importlib.import_module(f"axqua.postproc.{name}")
    module = _NAME_TO_MODULE.get(name)
    if module is None:
        raise AttributeError(f"module 'axqua.postproc' has no attribute {name!r}")
    return getattr(importlib.import_module(f"axqua.postproc.{module}"), name)


def __dir__() -> list[str]:
    return sorted(__all__)
