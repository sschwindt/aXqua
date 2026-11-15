"""The flow field a survey is planned on: the last printout of a steady run.

A plan needs to know where the water is, how deep and how fast, on the day of the survey. That is the result of a steady simulation at the discharge expected on that day (``r2d.slf`` of the model folder, or another result named in the settings). The node coordinates come from ``geometry.slf``: a TELEMAC result stores them in single precision, which at a UTM northing is a spacing of half a meter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from axqua.core.errors import AxquaError


class SurveyError(AxquaError):
    """A survey cannot be planned, with what to do about it."""

    code = "survey.not_possible"


@dataclass
class FlowField:
    """Depth and velocity on the mesh of the model, with what the placement rules need."""

    x: np.ndarray
    y: np.ndarray
    triangles: np.ndarray               # (nelem, 3), 0-based
    depth: np.ndarray
    u: np.ndarray
    v: np.ndarray
    bed: np.ndarray
    wet_depth: float = 0.01
    hops: np.ndarray | None = None      # mesh nodes to the nearest liquid boundary (-1: none)
    zone: np.ndarray | None = None      # roughness zone of every node (0: none)
    source: str = ""
    time: float = 0.0
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def speed(self) -> np.ndarray:
        return np.hypot(self.u, self.v)

    @property
    def wet(self) -> np.ndarray:
        return self.depth > self.wet_depth

    @property
    def edge_length(self) -> float:
        """The median length of a mesh edge among the wet triangles."""
        if "edge" not in self._cache:
            tri = self.triangles[self.wet[self.triangles].all(axis=1)]
            if not len(tri):
                tri = self.triangles
            a, b = tri[:, 0], tri[:, 1]
            self._cache["edge"] = float(np.median(np.hypot(self.x[a] - self.x[b], self.y[a] - self.y[b])))
        return self._cache["edge"]

    @property
    def triangulation(self):
        if "tri" not in self._cache:
            from matplotlib.tri import Triangulation

            self._cache["tri"] = Triangulation(self.x, self.y, self.triangles)
        return self._cache["tri"]

    def sample(self, xs, ys) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """``depth, u, v, inside`` at points, linear within the triangles; zero and False outside the mesh."""
        from matplotlib.tri import LinearTriInterpolator

        xs = np.asarray(xs, dtype=float)
        ys = np.asarray(ys, dtype=float)
        if "interp" not in self._cache:
            tri = self.triangulation
            finder = tri.get_trifinder()
            self._cache["interp"] = tuple(LinearTriInterpolator(tri, values, trifinder=finder) for values in (self.depth, self.u, self.v))
        out = []
        for interpolator in self._cache["interp"]:
            values = interpolator(xs, ys)
            out.append(np.ma.filled(values, np.nan))
        inside = np.isfinite(out[0])
        return tuple(np.where(inside, values, 0.0) for values in out) + (inside,)

    def neighbors(self):
        """The mesh as a sparse node adjacency matrix."""
        if "adjacency" not in self._cache:
            from scipy.sparse import coo_matrix

            t = self.triangles
            rows = np.concatenate([t[:, 0], t[:, 1], t[:, 2], t[:, 1], t[:, 2], t[:, 0]])
            cols = np.concatenate([t[:, 1], t[:, 2], t[:, 0], t[:, 0], t[:, 1], t[:, 2]])
            n = len(self.x)
            self._cache["adjacency"] = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(n, n)).tocsr()
        return self._cache["adjacency"]

    def gradient(self, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The gradient of a nodal field at the nodes (area-weighted mean of the triangle gradients)."""
        t = self.triangles
        x1, x2, x3 = (self.x[t[:, i]] for i in range(3))
        y1, y2, y3 = (self.y[t[:, i]] for i in range(3))
        f1, f2, f3 = (values[t[:, i]] for i in range(3))
        twice = (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)
        ok = np.abs(twice) > 1e-12
        safe = np.where(ok, twice, 1.0)
        gx = np.where(ok, (f1 * (y2 - y3) + f2 * (y3 - y1) + f3 * (y1 - y2)) / safe, 0.0)
        gy = np.where(ok, (f1 * (x3 - x2) + f2 * (x1 - x3) + f3 * (x2 - x1)) / safe, 0.0)
        area = np.where(ok, np.abs(twice) / 2.0, 0.0)
        n = len(self.x)
        weight = np.zeros(n)
        sx = np.zeros(n)
        sy = np.zeros(n)
        for i in range(3):
            np.add.at(weight, t[:, i], area)
            np.add.at(sx, t[:, i], gx * area)
            np.add.at(sy, t[:, i], gy * area)
        weight = np.where(weight > 0, weight, 1.0)
        return sx / weight, sy / weight

    def wet_edge_distance(self) -> np.ndarray:
        """How many mesh nodes every wet node lies from the nearest dry node or solid boundary (0 at the edge)."""
        if "edge_hops" not in self._cache:
            adjacency = self.neighbors()
            wet = self.wet
            hops = np.full(len(self.x), -1, dtype=int)
            dry_neighbors = adjacency.dot((~wet).astype(np.int32))
            front = np.flatnonzero(wet & (dry_neighbors > 0))
            hops[front] = 0
            level = 0
            while len(front):
                level += 1
                reached = np.unique(adjacency[front].indices)
                front = reached[wet[reached] & (hops[reached] < 0)]
                hops[front] = level
            self._cache["edge_hops"] = hops
        return self._cache["edge_hops"]


def result_path(cfg, result: str = "") -> Path:
    """The result a plan reads: *result* when given, else the steady run of the model folder."""
    if result:
        path = Path(result).expanduser()
        return path if path.is_absolute() else Path(cfg.model_dir) / path
    return Path(cfg.model_path(cfg.results_slf))


def load_field(cfg, result: str = "") -> FlowField:
    """Read the flow field of the case. Raises :class:`SurveyError` where the model is not built or has not run."""
    from axqua.core.selafin import SelafinFile, read_slf

    geometry = Path(cfg.model_path(cfg.geometry_slf))
    if not geometry.is_file():
        raise SurveyError(f"the model of the case is not built yet ({geometry.name} is missing)", remedy="Build the case on the tab Preprocessing, then run the steady simulation.")
    path = result_path(cfg, result)
    if not path.is_file():
        raise SurveyError(f"there is no simulation result to plan with ({path.name} is missing in {path.parent})", remedy="Run the steady simulation of the case at the discharge you expect on the survey day, then plan the survey.")
    geo = read_slf(geometry, frame=0)
    slf = SelafinFile(path)
    if slf.nplan > 1:
        raise SurveyError(f"{path.name} is a 3D result; a survey is planned on the depth-averaged result of the 2D model", remedy="Name the result of the steady 2D run.")
    if slf.npoin != len(geo["x"]) or not slf.n_frames:
        raise SurveyError(f"{path.name} does not belong to the built mesh ({slf.npoin} nodes against {len(geo['x'])}) or holds no printout", remedy="Run the steady simulation again after the last build.")
    names = {name.upper(): name for name in slf.var_names}
    missing = [name for name in ("WATER DEPTH", "VELOCITY U", "VELOCITY V") if name not in names]
    if missing:
        raise SurveyError(f"{path.name} lacks {', '.join(missing)}", remedy="Print the water depth and the velocity components (VARIABLES FOR GRAPHIC PRINTOUTS U,V,H).")
    frame = slf.frame(-1, [names[n] for n in ("WATER DEPTH", "VELOCITY U", "VELOCITY V")] + ([names["BOTTOM"]] if "BOTTOM" in names else []))
    depth = np.maximum(np.nan_to_num(frame[names["WATER DEPTH"]].astype(float)), 0.0)
    if "BOTTOM" in names:
        bed = frame[names["BOTTOM"]].astype(float)
    else:                                            # a result printed without the bed: the bed of the built mesh
        built = {str(name).upper(): values for name, values in (geo.get("values") or {}).items()}
        bed = np.asarray(built["BOTTOM"], dtype=float) if "BOTTOM" in built else np.zeros(len(depth))
    triangles = np.asarray(geo["ikle"], dtype=int)[:, :3]
    out = FlowField(x=np.asarray(geo["x"], dtype=float), y=np.asarray(geo["y"], dtype=float), triangles=triangles, depth=depth, u=np.nan_to_num(frame[names["VELOCITY U"]].astype(float)), v=np.nan_to_num(frame[names["VELOCITY V"]].astype(float)), bed=bed, wet_depth=float(cfg.wet_depth), source=str(path), time=float(slf.times[-1]))
    out.hops = _boundary_hops(cfg, geometry, len(out.x))
    out.zone = _node_zones(cfg, out)
    return out


def _boundary_hops(cfg, geometry: Path, n: int) -> np.ndarray:
    cli = Path(cfg.model_path(cfg.boundary_cli))
    if not cli.is_file():
        return np.full(n, -1, dtype=int)
    from axqua.solvers.telemac.boundary import liquid_boundary_hops

    _, _, hops, _ = liquid_boundary_hops(geometry, cli)
    return np.asarray(hops, dtype=int)


def _node_zones(cfg, flow: FlowField) -> np.ndarray:
    """The roughness zone of every wet node; zero where the case has no zones or the node is dry."""
    zones = np.zeros(len(flow.x), dtype=int)
    if cfg.geodata.roughness_zones is None:
        return zones
    from axqua.solvers.telemac.mesh import roughness_zone_frame, zone_of_points

    wet = np.flatnonzero(flow.wet)
    if len(wet):
        zones[wet] = zone_of_points(roughness_zone_frame(cfg), flow.x[wet], flow.y[wet], cfg.crs_epsg)
    return zones


__all__ = ["FlowField", "SurveyError", "load_field", "result_path"]
