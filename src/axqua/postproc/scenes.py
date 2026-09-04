"""What to draw: the scene registry, and why a scene is or is not available.

Standard library only. A :class:`Scene` declares its requirements in the small
vocabulary :meth:`axqua.postproc.dataset.Dataset.has` understands, so
:func:`plan` can say *why* a figure cannot be made instead of failing somewhere
inside a renderer - which, for a tool that shells out to VisIt, is the difference
between a useful message and a silent empty PNG.

``Scene.render`` names a renderer rather than holding a function, so the same
scene definition serves any backend that implements that name.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Scene:
    name: str
    title: str
    solvers: frozenset[str]
    requires: tuple[str, ...]
    render: str
    options: Mapping[str, Any] = field(default_factory=dict)
    #: Alternative requirement sets, tried in order when ``requires`` is unmet.
    #: This is how one scene covers both the two-phase and the rigid-lid case:
    #: under a rigid lid there is no ``alpha.water`` to contour, but there IS a lid
    #: patch carrying the (prescribed) surface.
    fallbacks: tuple[tuple[str, ...], ...] = ()


SCENES: tuple[Scene, ...] = (
    Scene(
        name="free-surface",
        title="Free surface coloured by velocity magnitude",
        solvers=frozenset({"openfoam"}),
        requires=("field:alpha.water", "field:U"),
        fallbacks=(("patch:lid", "field:U"),),
        render="free_surface",
        options={"iso_value": 0.5},
    ),
    Scene(
        name="velocity-plan",
        title="Plan-view velocity field",
        solvers=frozenset({"openfoam", "telemac"}),
        requires=("field:U",),
        # TELEMAC's .vtu carries the assembled vector under its own name
        fallbacks=(("field:velocity",),),
        render="velocity_plan",
        options={"depth_fraction": 0.6},
    ),
    Scene(
        name="profiles",
        title="Vertical velocity profiles at the measurement verticals",
        solvers=frozenset({"openfoam"}),
        requires=("field:U", "groundtruth:hydraulics"),
        render="profiles",
    ),
)

_BY_NAME = {scene.name: scene for scene in SCENES}


def available_names() -> tuple[str, ...]:
    return tuple(scene.name for scene in SCENES)


def resolve(names: Sequence[str] | None) -> list[Scene]:
    """Scenes by name, in the order given. ``None`` means every scene."""
    if names is None:
        return list(SCENES)
    out, unknown = [], []
    for name in names:
        scene = _BY_NAME.get(name)
        (out.append(scene) if scene else unknown.append(name))
    if unknown:
        raise ValueError(
            f"unknown scene(s): {', '.join(unknown)}. Available: "
            f"{', '.join(available_names())}")
    return out


def _unmet(scene: Scene, dataset, extras: Iterable[str]) -> list[str]:
    """The requirements *dataset* does not satisfy, for one requirement set."""
    extras = set(extras)
    return [req for req in scene.requires
            if not (req in extras or dataset.has(req))]


def check(scene: Scene, dataset, *, extras: Iterable[str] = ()) -> tuple[bool, str]:
    """``(available, reason)`` for one scene against one dataset.

    *extras* are requirements satisfied by something other than the dataset itself -
    ``groundtruth:hydraulics`` comes from the case, not from the result file.
    """
    if dataset.solver not in scene.solvers:
        return False, (f"not applicable to {dataset.solver} "
                       f"(this scene draws {', '.join(sorted(scene.solvers))})")

    extras = set(extras)
    missing = _unmet(scene, dataset, extras)
    if not missing:
        return True, ""

    for alternative in scene.fallbacks:
        unmet = [req for req in alternative
                 if not (req in extras or dataset.has(req))]
        if not unmet:
            return True, ""

    return False, "missing " + ", ".join(missing)


def resolved_requirements(scene: Scene, dataset, *,
                          extras: Iterable[str] = ()) -> tuple[str, ...]:
    """Which requirement set this dataset actually satisfies.

    The renderer needs to know: a rigid-lid case reaches ``free-surface`` through
    the lid-patch fallback, and must be drawn - and captioned - differently from a
    two-phase case that has a real interface to contour.
    """
    extras = set(extras)
    if not _unmet(scene, dataset, extras):
        return scene.requires
    for alternative in scene.fallbacks:
        if not [r for r in alternative if not (r in extras or dataset.has(r))]:
            return alternative
    return ()


def plan(datasets, *, names: Sequence[str] | None = None,
         extras: Iterable[str] = ()) -> list[tuple[Scene, object | None, str]]:
    """For every requested scene: the dataset that can draw it, or the reason none can.

    Reported rather than raised, so ``axqua postproc --list`` can show a whole case
    at once - the same idea as ``axqua case-status`` for capabilities.
    """
    out = []
    for scene in resolve(names):
        reasons = []
        chosen = None
        for dataset in datasets:
            ok, why = check(scene, dataset, extras=extras)
            if ok:
                chosen = dataset
                break
            reasons.append(f"{dataset.solver}: {why}")
        if chosen is not None:
            out.append((scene, chosen, ""))
        else:
            out.append((scene, None,
                        "; ".join(reasons) or "no results to draw from"))
    return out
