"""What a visualiser can open, described without naming a solver.

Standard library only, deliberately: a solver backend has to build these to answer
:meth:`axqua.core.registry.SolverBackend.describe_results`, and that call must stay
cheap enough to make from a capability listing. Anything heavier belongs in the
renderer, not in the description of what there is to render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Dataset:
    """One openable result.

    *kind* is how a reader should open it, not what produced it:

    ``openfoam``
        a ``case.foam`` handle - VisIt and ParaView both read the case directory
        natively, so nothing is converted.
    ``vtu`` / ``pvd``
        a VTK XML file. This is the TELEMAC route, because no visualiser reads
        SERAFIN and there is no ``foamToVTK`` analogue for it (see
        :mod:`axqua.postproc.selafin_vtk`).
    """

    solver: str
    kind: str
    path: Path
    times: tuple[float, ...] = ()
    fields: frozenset[str] = frozenset()
    patches: frozenset[str] = frozenset()
    bounds: tuple[float, ...] | None = None
    vector_fields: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def has(self, capability: str) -> bool:
        """Whether this dataset satisfies one scene requirement.

        The vocabulary is deliberately tiny and string-based so a scene can declare
        what it needs without importing anything: ``field:U``, ``patch:lid``,
        ``times>1``.
        """
        if capability.startswith("field:"):
            return capability[6:] in self.fields
        if capability.startswith("patch:"):
            return capability[6:] in self.patches
        if capability == "times>1":
            return len(self.times) > 1
        if capability.startswith("solver:"):
            return self.solver == capability[7:]
        # An unknown requirement is NOT silently satisfied: a scene asking for
        # something this vocabulary cannot express should be reported unavailable
        # rather than rendered against an assumption.
        return False

    @property
    def last_time(self) -> float | None:
        return self.times[-1] if self.times else None
