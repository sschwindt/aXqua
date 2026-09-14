"""How much of a model-data mismatch is just not knowing where the point is.

A calibration compares a measured value against the model sampled **at the
measurement's coordinates**. That comparison silently assumes the coordinates are
exact. When they are not, the model is read at the wrong place and the difference
is charged to the model - or, worse, to whichever parameter happens to move it.

This is not a hypothetical. Inn KB15's September-2025 survey was positioned with
an **RTK float** solution rather than a fixed one, which is decimetre-to-metre
horizontal rather than centimetre. On that reach the modelled water depth at the
survey verticals is 0.374 m while the deepest node within 3 m is 0.648 m, so a
position error of a few metres moves the modelled depth by more than the entire
model-data discrepancy being calibrated against.

**The fix is not to relocate the points.** Their true positions are unknown, and
snapping them to wherever the model happens to agree would manufacture agreement
and calibrate nothing. The fix is to state the uncertainty honestly: if the point
is known only to within sigma, then the model value at that point is uncertain by
however much the model varies over sigma. That uncertainty is added in quadrature
to the measurement error, so a target sitting on a steep gradient carries wide
error bars and a target in a uniform patch carries narrow ones - which is exactly
the weighting a likelihood should give them.

The spread is measured by **sampling**, not by a gradient. A gradient is a local
linearisation, and the fields that make this matter - depth near a bank, velocity
at a shear layer - are precisely the ones where a linearisation is worst over a
metre-scale radius. Sampling a ring at the uncertainty radius costs nothing here
(the sampler is a KD-tree lookup) and stays honest on a step.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Callable, Sequence

import numpy as np

log = logging.getLogger("axqua")

#: Horizontal sigma [m] for common GNSS solution qualities. These are working
#: figures for judging a survey, not a specification of anyone's receiver: a float
#: solution in particular degrades with baseline, sky view and occupation time, and
#: is the one quality where quoting a single number is least defensible.
GNSS_SIGMA = {
    "rtk-fixed": 0.03,
    "rtk-float": 1.00,
    "dgps": 1.00,
    "standalone": 3.00,
}

#: Points on the sampling ring(s). Eight is enough to catch a one-sided step
#: without making the sampler the cost of the calibration.
RING_POINTS = 8


def sample_spread(sample: Callable[[np.ndarray, np.ndarray], np.ndarray],
                  x, y, sigma: float, *,
                  radii: Sequence[float] = (0.5, 1.0),
                  ring_points: int = RING_POINTS) -> np.ndarray:
    """Spread of *sample* over the positional uncertainty, per point.

    *sample* maps ``(xs, ys)`` to model values. The field is read at each point and
    on rings at ``radii`` multiples of *sigma*; the returned value is the standard
    deviation over those samples - an estimate of how much the model value could
    differ purely because the coordinates are uncertain.

    Non-finite samples (a point that falls dry, or outside the mesh) are dropped
    rather than propagated, so a vertical near the waterline still gets a spread
    from the samples that did land in water.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    sigma = float(sigma)
    if sigma <= 0:
        return np.zeros(x.shape)

    offsets = [(0.0, 0.0)]
    for radius in radii:
        for k in range(ring_points):
            angle = 2.0 * np.pi * k / ring_points
            offsets.append((sigma * radius * np.cos(angle),
                            sigma * radius * np.sin(angle)))

    stack = np.full((len(offsets), x.size), np.nan)
    for i, (dx, dy) in enumerate(offsets):
        values = np.asarray(sample(x + dx, y + dy), dtype=float)
        stack[i] = np.where(np.isfinite(values), values, np.nan)

    counted = np.sum(np.isfinite(stack), axis=0)
    with np.errstate(invalid="ignore"):
        # nanstd warns on an all-NaN column; those are exactly the columns the
        # count below discards, so the warning is noise rather than information
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            spread = np.nanstd(stack, axis=0)
    # a point with fewer than three usable samples cannot support a spread; leave
    # it at zero rather than inventing one from two numbers
    return np.where(counted >= 3, np.nan_to_num(spread), 0.0)


def telemac_sampler(geometry, results, variable: str
                    ) -> Callable[[np.ndarray, np.ndarray], np.ndarray]:
    """A nearest-node sampler for one SELAFIN variable.

    Nearest-node rather than P1: a result merged from a parallel run is not a clean
    triangulation (it carries coincident nodes and zero-area triangles at the
    subdomain interfaces), and the question here is how much the field varies over
    metres - which nearest-node answers perfectly well on a sub-metre mesh.
    """
    from scipy.spatial import cKDTree

    from axqua.core.selafin import read_slf

    geom = read_slf(geometry)
    res = read_slf(results)
    if variable not in res["values"]:
        raise KeyError(
            f"{variable!r} is not in the result ({', '.join(sorted(res['values']))})")
    values = np.asarray(res["values"][variable], dtype=float)
    depth = np.asarray(res["values"].get("WATER DEPTH", values), dtype=float)
    tree = cKDTree(np.column_stack([geom["x"], geom["y"]]))

    def sample(xs, ys):
        _, idx = tree.query(np.column_stack([xs, ys]), k=1)
        out = values[idx]
        # a dry node carries no meaningful depth or velocity; report it as missing
        # so sample_spread drops it instead of averaging a zero into the spread
        return np.where(depth[idx] > 0.01, out, np.nan)

    return sample


def describe(sigma: float, quality: str | None = None) -> str:
    """One line naming the uncertainty and where it came from."""
    if quality:
        return (f"positional uncertainty sigma = {sigma:.2f} m "
                f"({quality} GNSS solution)")
    return f"positional uncertainty sigma = {sigma:.2f} m"


def resolve_sigma(cfg) -> tuple[float, str | None]:
    """``(sigma, quality)`` from ``ground_truth.position_quality`` / ``position_sigma``.

    An explicit sigma wins over a named quality, so a survey whose real accuracy is
    known does not have to be described by a lookup table.
    """
    gt = getattr(cfg, "ground_truth", None)
    sigma = getattr(gt, "position_sigma", None)
    quality = getattr(gt, "position_quality", None)
    if sigma is not None:
        return float(sigma), quality
    if quality:
        key = str(quality).strip().lower()
        if key not in GNSS_SIGMA:
            raise ValueError(
                f"unknown ground_truth.position_quality {quality!r}; expected one "
                f"of {', '.join(sorted(GNSS_SIGMA))}, or set "
                "ground_truth.position_sigma to a number of metres.")
        return GNSS_SIGMA[key], key
    return 0.0, None
