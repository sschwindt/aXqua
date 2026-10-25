"""The 3D result seen from above: water depth and depth-averaged velocity per column.

A free-surface result of OpenFOAM is a field on several hundred thousand cells, and what
a hydraulic engineer asks of it first is what a 2D result answers directly: where is the
water, how deep is it, and how fast does it flow. The mesh is a lattice of columns
(:mod:`axqua.solvers.openfoam.mesh`), so the answer is a sum over each column:

* water depth: the water fraction of each cell times its thickness, summed;
* velocity: the same sum of the velocity, divided by the depth.

The result is written as a SELAFIN with one node per column, ``depth-averaged.slf`` in
the case folder, with the variables a TELEMAC result has. That serves two purposes.
QGIS draws it like a 2D result, with the styles of the plugin, beside the 2D result
the model was started from. And it is a result the build can start from
(``openfoam-build`` with the option ``hotstart``): a coarse 3D model run to its
discharge balance gives the water surface of a finer one, which then only has to
adjust instead of repeating the approach from the 2D surface. On the Isar example the
3D model runs a fifth shallower and a third faster than the 2D model that seeds it,
so that approach takes a few thousand seconds of simulated time.

What it needs of the mesh is saved when the case is built (``axqua-columns.npz``), so
that reading a result never depends on building the same lattice a second time.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from axqua.core import errors
from axqua.solvers.openfoam.layout import time_folders

log = logging.getLogger("axqua")

#: The lattice of the built case, as much of it as a view from above needs.
COLUMNS_FILE = "axqua-columns.npz"
#: The depth-averaged result of the latest time, in the case folder.
RESULT_NAME = "depth-averaged.slf"

_COMPONENTS = {"scalar": 1, "vector": 3}
_UNIFORM = re.compile(rb"internalField\s+uniform\s+(\([^)]*\)|[-+\d.eE]+)\s*;")
_LIST = re.compile(rb"internalField\s+nonuniform\s+List<(\w+)>\s*(\d+)\s*\(")


@dataclass
class Columns:
    """The lattice of a built case: which column each cell is in, and how thick it is."""

    cell_column: np.ndarray      # (n cells,) column of each cell
    thickness: np.ndarray        # (n cells,) mean height of each cell [m]
    xy: np.ndarray               # (n columns, 2) column centers
    bed: np.ndarray              # (n columns,) bed elevation at the center [m a.s.l.]
    col_id: np.ndarray           # (ny, nx) column number on the lattice, -1 outside
    dx: float                    # plan spacing [m]

    @property
    def n_cells(self) -> int:
        return int(self.cell_column.size)

    @property
    def n_columns(self) -> int:
        return int(self.xy.shape[0])

    @classmethod
    def of(cls, of_mesh) -> "Columns":
        z_lo, z_hi = of_mesh.cell_bounds
        return cls(cell_column=np.asarray(of_mesh.cell_column, dtype=np.int32),
                   thickness=np.asarray(z_hi - z_lo, dtype=float),
                   xy=np.asarray(of_mesh.grid.cell_xy, dtype=float),
                   bed=np.asarray(of_mesh.column_bed, dtype=float),
                   col_id=np.asarray(of_mesh.grid.col_id, dtype=np.int32),
                   dx=float(of_mesh.grid.dx))

    def save(self, case_dir: str | Path) -> Path:
        path = Path(case_dir) / COLUMNS_FILE
        np.savez_compressed(path, cell_column=self.cell_column,
                            thickness=self.thickness.astype(np.float32), xy=self.xy,
                            bed=self.bed, col_id=self.col_id, dx=np.array(self.dx))
        return path

    @classmethod
    def load(cls, case_dir: str | Path) -> "Columns":
        path = Path(case_dir) / COLUMNS_FILE
        if not path.is_file():
            raise errors.ConfigError(
                f"{path} is missing: the case was built before aXqua saved the "
                "lattice of its columns", subject="openfoam",
                remedy="Build the OpenFOAM case again; the result of a finished run "
                       "has to be computed again with it.")
        with np.load(path) as data:
            return cls(cell_column=data["cell_column"],
                       thickness=data["thickness"].astype(float), xy=data["xy"],
                       bed=data["bed"], col_id=data["col_id"], dx=float(data["dx"]))


def read_cells(path: str | Path, n_cells: int) -> np.ndarray:
    """The cell values of a field file, written in binary or as text.

    ``(n,)`` for a scalar field and ``(n, 3)`` for a vector field. A field that is
    the same everywhere (``uniform``) comes back spread over the cells.
    """
    path = Path(path)
    raw = path.read_bytes()
    match = _LIST.search(raw)
    if match is None:
        uniform = _UNIFORM.search(raw)
        if uniform is None:
            raise errors.SolverError(f"{path} holds no internalField", subject=str(path))
        value = np.array(uniform.group(1).decode().strip("()").split(), dtype=float)
        return np.tile(value, (n_cells, 1)) if value.size > 1 else np.full(n_cells, value[0])
    kind, count = match.group(1).decode(), int(match.group(2))
    components = _COMPONENTS.get(kind)
    if components is None:
        raise errors.SolverError(f"{path} holds a field of {kind}, which is neither a "
                                 "scalar nor a vector", subject=str(path))
    if count != n_cells:
        raise errors.SolverError(
            f"{path} holds {count} values for a mesh of {n_cells} cells",
            subject=str(path),
            remedy="The result belongs to another mesh. Build the case and run it again.")
    start = match.end()
    binary = re.search(rb"format\s+binary\s*;", raw[:start]) is not None
    try:
        if binary:
            values = np.frombuffer(raw, dtype="<f8", count=count * components,
                                   offset=start).copy()
        else:
            end = raw.find(b"boundaryField", start)
            text = raw[start:end if end > 0 else None].decode()
            for character in "();":
                text = text.replace(character, " ")
            values = np.array(text.split(), dtype=float)[:count * components]
            if values.size != count * components:
                raise ValueError(f"{values.size} numbers for {count * components}")
    except ValueError as exc:
        raise errors.SolverError(
            f"{path} cannot be read: {exc}", subject=str(path),
            remedy="The run may have been stopped while it wrote. Use an earlier "
                   "time, or run the simulation again.") from exc
    return values if components == 1 else values.reshape(count, components)


def latest_time(case_dir: str | Path) -> str | None:
    """Name of the last time folder that holds a result, ``None`` when there is none."""
    later = [path for value, path in time_folders(case_dir)
             if value > 0 and (path / "U").is_file()]
    return later[-1].name if later else None


@dataclass
class PlanView:
    """Depth and depth-averaged velocity of every column at one time."""

    columns: Columns
    time: str
    depth: np.ndarray            # (n columns,) [m]
    velocity: np.ndarray         # (n columns, 2) [m/s]

    @property
    def speed(self) -> np.ndarray:
        return np.hypot(self.velocity[:, 0], self.velocity[:, 1])

    def summary(self, wet_depth: float = 0.01) -> dict[str, float]:
        """The numbers that say how much water there is and how fast it flows."""
        area = self.columns.dx ** 2
        wet = self.depth > wet_depth
        volume = float(self.depth.sum() * area)
        return {
            "time": float(self.time),
            "wetted_area": float(wet.sum() * area),
            "volume": volume,
            "mean_depth": float(self.depth[wet].mean()) if wet.any() else 0.0,
            # weighted with the depth: the velocity of the water, not of the columns
            "mean_velocity": (float((self.speed * self.depth).sum() / self.depth.sum())
                              if volume > 0 else 0.0),
        }


def depth_average(columns: Columns, alpha: np.ndarray, velocity: np.ndarray,
                  *, time: str = "0", wet_depth: float = 0.01) -> PlanView:
    """Sum the water of each column.

    A column with less water than *wet_depth* is dry: depth and velocity zero, as in a
    2D result. Without that, traces of the water fraction in the air above dry
    ground, a thousandth of a cell, would be a film of water with a velocity.
    """
    alpha = np.clip(np.asarray(alpha, dtype=float), 0.0, 1.0)
    water = alpha * columns.thickness
    depth = np.zeros(columns.n_columns)
    np.add.at(depth, columns.cell_column, water)
    flux = np.zeros((columns.n_columns, 2))
    np.add.at(flux, columns.cell_column, water[:, None] * np.asarray(velocity)[:, :2])
    wet = depth > wet_depth
    mean = np.zeros_like(flux)
    mean[wet] = flux[wet] / depth[wet, None]
    return PlanView(columns=columns, time=str(time), depth=np.where(wet, depth, 0.0),
                    velocity=mean)


def lattice_triangles(col_id: np.ndarray, xy: np.ndarray) -> np.ndarray:
    """Two triangles for every square of four neighboring column centers.

    Counter-clockwise in plan, as TELEMAC numbers its elements, whichever way the
    lattice is turned.
    """
    a, b, c, d = col_id[:-1, :-1], col_id[:-1, 1:], col_id[1:, 1:], col_id[1:, :-1]
    full = (a >= 0) & (b >= 0) & (c >= 0) & (d >= 0)
    a, b, c, d = a[full], b[full], c[full], d[full]
    triangles = np.concatenate([np.stack([a, b, c], axis=1), np.stack([a, c, d], axis=1)])
    p = xy[triangles]
    area = ((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
            - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1]))
    triangles[area < 0] = triangles[area < 0][:, ::-1]
    return triangles


def _boundary_numbers(triangles: np.ndarray, n_nodes: int) -> np.ndarray:
    """IPOBO of a SELAFIN: the nodes on the outline numbered from 1, the others 0."""
    edges = np.sort(np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]],
                                    triangles[:, [2, 0]]]), axis=1)
    unique, count = np.unique(edges, axis=0, return_counts=True)
    outline = np.unique(unique[count == 1])
    numbers = np.zeros(n_nodes, dtype=np.int32)
    numbers[outline] = np.arange(1, outline.size + 1)
    return numbers


def write_slf(view: PlanView, path: str | Path) -> Path:
    """Write *view* as a 2D SELAFIN with the variables of a TELEMAC result."""
    from axqua.core.selafin import _write_selafin

    columns = view.columns
    triangles = lattice_triangles(columns.col_id, columns.xy)
    if triangles.size == 0:
        raise errors.MeshError(
            "the lattice has no square of four columns, so it has no plan view",
            subject="openfoam.cell_size", remedy="Use a smaller cell size.")
    # a column at a corner of the lattice that touches no full square is no node
    used = np.zeros(columns.n_columns, dtype=bool)
    used[triangles.ravel()] = True
    number = np.full(columns.n_columns, -1)
    number[used] = np.arange(int(used.sum()))
    triangles = number[triangles]
    xy, bed = columns.xy[used], columns.bed[used]
    depth, velocity = view.depth[used], view.velocity[used]
    variables = [
        ("VELOCITY U", "M/S", velocity[:, 0]),
        ("VELOCITY V", "M/S", velocity[:, 1]),
        ("WATER DEPTH", "M", depth),
        ("FREE SURFACE", "M", bed + depth),
        ("BOTTOM", "M", bed),
        ("SCALAR VELOCITY", "M/S", np.hypot(velocity[:, 0], velocity[:, 1])),
    ]
    return _write_selafin(
        Path(path), xy[:, 0], xy[:, 1], triangles + 1,
        _boundary_numbers(triangles, xy.shape[0]), variables,
        title=f"aXqua: OpenFOAM result at {view.time} s, depth-averaged",
        date=(2026, 1, 1, 0, 0, 0), double_precision=True)


def read(cfg, case_dir: str | Path | None = None, *, time: str | None = None) -> PlanView:
    """The plan view of one time of a finished (reconstructed) run, the latest by default."""
    case_dir = Path(case_dir) if case_dir else Path(cfg.openfoam_case_dir)
    columns = Columns.load(case_dir)
    time = time or latest_time(case_dir)
    if time is None:
        raise errors.SolverError(
            f"{case_dir} holds no result", subject=str(case_dir),
            remedy="Run the simulation. A run on several processors has to be "
                   "reconstructed (the option reconstruct of the job).")
    folder = case_dir / time
    velocity = read_cells(folder / "U", columns.n_cells)
    alpha_file = folder / "alpha.water"
    # a single-phase result (leg C) has no water fraction: every cell is water
    alpha = (read_cells(alpha_file, columns.n_cells) if alpha_file.is_file()
             else np.ones(columns.n_cells))
    return depth_average(columns, alpha, velocity, time=time,
                         wet_depth=float(cfg.wet_depth))


def write(cfg, case_dir: str | Path | None = None, *, time: str | None = None,
          path: str | Path | None = None) -> tuple[Path, dict[str, float]]:
    """Write ``depth-averaged.slf`` for the latest time; returns the file and the summary."""
    case_dir = Path(case_dir) if case_dir else Path(cfg.openfoam_case_dir)
    view = read(cfg, case_dir, time=time)
    out = write_slf(view, Path(path) if path else case_dir / RESULT_NAME)
    summary = view.summary(float(cfg.wet_depth))
    log.info("depth-averaged result at t = %s s: %.0f m2 wet, %.0f m3 of water, mean "
             "depth %.3f m, mean velocity %.3f m/s -> %s", view.time,
             summary["wetted_area"], summary["volume"], summary["mean_depth"],
             summary["mean_velocity"], out.name)
    return out, summary
