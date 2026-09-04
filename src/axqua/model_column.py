"""Where a measurement sits in the *model's* water column.

A calibration target has to be placed at a coordinate the solver can be sampled
at. Doing that from the **surveyed** elevation couples every target to the survey's
vertical accuracy - and on a real reach that accuracy is the weakest link. On Inn
KB15 it failed three ways at once: a GNSS rover-pole height that was never
subtracted (+2.26/2.51/2.70 m), the measurement height above the bed never added
(-0.34 m), and a genuine bathymetric-LiDAR bias in the DEM (+0.3 m, growing with
depth). The result was targets ~2.6 m from where they belonged, and because
HydroBayesCal's OpenFOAM binding searches for the nearest point with **no distance
cutoff**, it returned plausible-looking near-zero velocities instead of failing.

Placing the target **relative to the model's own column** removes all three at a
stroke::

    z_target = bed_model(x, y) + f * depth_model(x, y)

with ``f`` the measurement's height above the bed as a fraction of the column
(:func:`axqua.ground_truth.relative_height`). Nothing but ``x``, ``y`` and ``f``
comes from the survey, and ``f`` is a ratio, so a vertical error cannot enter. It is
also what a 3D profile calibration actually asks: *at 40% of the depth, how fast is
the water?*

**The approximation this makes, stated plainly.** HydroBayesCal fixes the extraction
coordinates for the whole experimental design, so the column has to come from one
*reference* solution rather than from each perturbed run. Every :class:`ModelColumn`
therefore carries a ``source`` string naming what it was built from, and it is
written into the placement audit rather than left implicit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from axqua.ground_truth import DEFAULT_RELATIVE_HEIGHT  # noqa: F401 (re-export)

log = logging.getLogger("axqua")

#: Keep a target this far (as a fraction of the column) off the bed and the
#: surface. A target *on* the bed sits in the wall-function layer, where the
#: modelled velocity is a boundary condition rather than a result; one *on* the
#: surface sits on the lid.
DEFAULT_MARGIN = 0.05


@dataclass(frozen=True)
class ModelColumn:
    """The model's bed and depth at a set of query points."""

    x: np.ndarray
    y: np.ndarray
    bed: np.ndarray            # m a.s.l., NaN outside the domain
    depth: np.ndarray          # m, 0 where dry
    inside: np.ndarray         # bool
    source: str                # provenance, written into the audit

    @property
    def wse(self) -> np.ndarray:
        """Modelled water-surface elevation."""
        return self.bed + self.depth

    def usable(self, *, min_depth: float = 0.05) -> np.ndarray:
        """Points inside the domain with a column deep enough to place a target in."""
        return self.inside & np.isfinite(self.bed) & (self.depth >= float(min_depth))

    def _clamped(self, f, margin: float):
        f = np.asarray(f, dtype=float)
        lo, hi = float(margin), 1.0 - float(margin)
        out = np.clip(f, lo, hi)
        return out, ~np.isclose(out, f)

    def height_at(self, f, *, margin: float = DEFAULT_MARGIN):
        """Height above the model bed. TELEMAC-3D's CSV ``z`` convention.

        Returns ``(height, clamped)``; *clamped* marks the points whose requested
        fraction was pulled inside ``[margin, 1-margin]``, so a caller can report
        them instead of silently moving a target.
        """
        frac, clamped = self._clamped(f, margin)
        return frac * self.depth, clamped

    def z_at(self, f, *, margin: float = DEFAULT_MARGIN):
        """Absolute elevation. OpenFOAM's CSV ``z`` convention."""
        height, clamped = self.height_at(f, margin=margin)
        return self.bed + height, clamped


# --------------------------------------------------------------------------- #
# triangulation, and the trap it exists to catch
# --------------------------------------------------------------------------- #
def triangulation_defects(x, y, ikle) -> dict:
    """Degenerate triangles and coincident nodes, without building anything."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    tri = np.asarray(ikle)
    ax, ay = x[tri[:, 0]], y[tri[:, 0]]
    bx, by = x[tri[:, 1]], y[tri[:, 1]]
    cx, cy = x[tri[:, 2]], y[tri[:, 2]]
    area2 = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    order = np.lexsort((y, x))
    xs, ys = x[order], y[order]
    coincident = int(np.count_nonzero((np.diff(xs) == 0) & (np.diff(ys) == 0)))
    return {"zero_area": int(np.count_nonzero(area2 == 0.0)),
            "negative_area": int(np.count_nonzero(area2 < 0.0)),
            "coincident_nodes": coincident,
            "n_triangles": int(tri.shape[0])}


def safe_triangulation(x, y, ikle, *, source: str = "the mesh"):
    """A ``matplotlib.tri.Triangulation``, or an error naming the real cause.

    matplotlib's own message (``Triangulation is invalid``) says nothing about why,
    and the why here is specific and recurring: **SERAFIN stores x/y as float32**.
    At an easting of ~759,500 m the representable spacing is about 0.06 m, which is
    coarser than the short cross-stream edges of a 0.5 m anisotropic channel mesh -
    so a *result* file collapses thousands of triangles to zero area even though the
    mesh that produced it is fine. ``geometry.slf`` is written double-precision
    (SERAFIND) and does not suffer this, which is why it, not ``r2d.slf``, is the
    file to triangulate.
    """
    from matplotlib.tri import Triangulation

    defects = triangulation_defects(x, y, ikle)
    bad = defects["zero_area"] + defects["negative_area"]
    if bad or defects["coincident_nodes"]:
        raise ValueError(
            f"{source} cannot be triangulated: {defects['zero_area']} zero-area and "
            f"{defects['negative_area']} negative-area triangles of "
            f"{defects['n_triangles']}, {defects['coincident_nodes']} coincident node "
            "pair(s).\nSERAFIN stores x/y as float32; at these coordinates the "
            "representable spacing is coarser than the mesh's shortest edges, which "
            "collapses them. Triangulate the double-precision geometry file "
            "(geometry.slf, SERAFIND) instead, or pass method='nearest' to sample "
            "by nearest node without a triangulation.")
    return Triangulation(np.asarray(x, dtype=float), np.asarray(y, dtype=float),
                         np.asarray(ikle))


# --------------------------------------------------------------------------- #
# building a column from a solver result
# --------------------------------------------------------------------------- #
def telemac_column(x, y, *, geometry: Path, fields: Path | None = None,
                   bed_var: str = "BOTTOM", depth_var: str = "WATER DEPTH",
                   method: Literal["linear", "nearest"] = "linear") -> ModelColumn:
    """The model column at ``(x, y)`` from a TELEMAC geometry + result.

    Coordinates and connectivity **always** come from *geometry* (SERAFIND, double
    precision); *fields* supplies the depth. That split is deliberate - see
    :func:`safe_triangulation`.
    """
    from axqua.core.selafin import read_slf

    geom = read_slf(Path(geometry))
    gx, gy, ikle = geom["x"], geom["y"], geom["ikle"]
    bed = np.asarray(geom["values"][bed_var], dtype=float)

    if fields is not None:
        res = read_slf(Path(fields))
        if len(res["x"]) != len(gx):
            raise ValueError(
                f"{Path(fields).name} has {len(res['x'])} nodes but "
                f"{Path(geometry).name} has {len(gx)}; they are not the same mesh.")
        if depth_var not in res["values"]:
            raise ValueError(
                f"{Path(fields).name} carries {sorted(res['values'])} but not "
                f"{depth_var!r}.")
        depth = np.asarray(res["values"][depth_var], dtype=float)
        source = f"{Path(geometry).name} bed + {Path(fields).name} {depth_var}"
    else:
        depth = np.zeros_like(bed)
        source = f"{Path(geometry).name} bed only (no depth)"

    qx = np.asarray(x, dtype=float)
    qy = np.asarray(y, dtype=float)

    if method == "nearest":
        from scipy.spatial import cKDTree
        tree = cKDTree(np.column_stack([gx, gy]))
        dist, idx = tree.query(np.column_stack([qx, qy]), k=1)
        # a generous tolerance: the mesh's own median nearest-neighbour spacing
        span = float(np.hypot(gx.max() - gx.min(), gy.max() - gy.min()))
        tol = max(5.0, span / 1000.0)
        return ModelColumn(qx, qy, bed[idx], depth[idx], dist <= tol,
                           source + " (nearest node)")

    from matplotlib.tri import LinearTriInterpolator
    tri = safe_triangulation(gx, gy, ikle, source=Path(geometry).name)
    bed_q = LinearTriInterpolator(tri, bed)(qx, qy)
    depth_q = LinearTriInterpolator(tri, depth)(qx, qy)
    inside = ~np.ma.getmaskarray(bed_q)
    return ModelColumn(qx, qy, np.ma.filled(bed_q, np.nan),
                       np.ma.filled(depth_q, 0.0), inside, source + " (linear)")


def openfoam_column(x, y, *, cfg, cache: Path | None = None,
                    state=None) -> ModelColumn:
    """The model column at ``(x, y)`` from the **OpenFOAM campaign lattice**.

    Not from ``geometry.slf``: the campaign mesh is coarsened by
    ``openfoam.cell_size_factor`` (0.5 m -> 2.0 m on KB15) and its bed is resampled
    from the DEM onto that lattice, so on a rough bed the two disagree by
    decimetres - enough to place a near-bed target below the OpenFOAM bed. The
    column has to come from the mesh the solver will actually run on.

    Under ``mode: rigid-lid`` the lid *is* the prescribed free surface, so the
    column height is exactly the modelled depth.

    *cache* is an ``.npz`` sidecar written at template-build time; without it the
    lattice is rebuilt, which is deterministic given the same config, DEM and seed.
    """
    from scipy.spatial import cKDTree

    qx = np.asarray(x, dtype=float)
    qy = np.asarray(y, dtype=float)

    if cache is not None and Path(cache).is_file():
        data = np.load(Path(cache))
        cell_xy, bed, top = data["cell_xy"], data["bed"], data["top"]
        dx = float(data["dx"])
        source = f"{Path(cache).name} (cached campaign lattice)"
    else:
        cell_xy, bed, top, dx = _openfoam_columns_from_mesh(cfg, state=state)
        source = "rebuilt campaign lattice"

    tree = cKDTree(cell_xy)
    dist, idx = tree.query(np.column_stack([qx, qy]), k=1)
    inside = dist <= max(float(dx), 1e-9)
    return ModelColumn(qx, qy, bed[idx], np.maximum(top[idx] - bed[idx], 0.0),
                       inside, source)


def columns_from_openfoam_mesh(mesh) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """``(cell_xy, bed, top, dx)`` per plan column of a built OpenFOAM mesh.

    Reads the mesh's own per-column arrays rather than re-deriving anything, so the
    column a target is placed in is the column the solver will run on.
    """
    bed = np.asarray(mesh.column_bed, dtype=float)
    if getattr(mesh, "column_wse", None) is not None:
        top = np.asarray(mesh.column_wse, dtype=float)
    elif getattr(mesh, "column_depth", None) is not None:
        top = bed + np.asarray(mesh.column_depth, dtype=float)
    else:
        raise ValueError(
            "the OpenFOAM mesh carries no hotstart column depth, so there is no "
            "modelled water column to place a target in. Build the case with a 2D "
            "seed (openfoam.pre_run / a converged r2d.slf).")
    return (np.asarray(mesh.grid.cell_xy, dtype=float), bed, top, float(mesh.grid.dx))


def _openfoam_columns_from_mesh(cfg, *, state=None):
    """Rebuild the campaign lattice and read its columns.

    *state* matters: ``build_mesh`` without a 2D seed trims nothing and lays a FLAT
    lid over the whole ROI, which is a different mesh from the one the template was
    built with. Passing the same seed is what makes the rebuild reproduce it.
    """
    from axqua.solvers.openfoam.mesh import build_mesh

    return columns_from_openfoam_mesh(build_mesh(cfg, state=state))


def write_column_cache(cfg, path: Path, *, mesh=None, state=None) -> Path:
    """Persist the campaign lattice's columns beside the calibration inputs.

    Pass *mesh* when one has just been built - then nothing is recomputed and the
    cache is exact by construction. Otherwise the lattice is rebuilt from *cfg* and
    *state*, which is deterministic given the same config, DEM and seed.

    Deliberately **not** written inside the case template: ``_clean_template`` would
    not strip it and every per-run copy would carry it.
    """
    if mesh is not None:
        cell_xy, bed, top, dx = columns_from_openfoam_mesh(mesh)
    else:
        cell_xy, bed, top, dx = _openfoam_columns_from_mesh(cfg, state=state)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, cell_xy=cell_xy, bed=bed, top=top, dx=dx)
    return path
