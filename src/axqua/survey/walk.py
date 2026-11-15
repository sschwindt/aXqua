"""Walking through a river: how long it takes from one point to another, and along which line.

Wading is not symmetric. With the current a surveyor is carried along; against it every step costs more the deeper the water is, and above the wading limit there is no way at all. On a dry bank one walks at ease. The plan therefore does not connect its points by straight lines. It lays a raster over the reach, gives every step between two neighboring cells the time it takes in that direction, and finds the fastest walk with Dijkstra's algorithm on the directed graph of those steps:

* a step on land (dry cells of the model and everything outside it) at the land speed;
* a step in water with the current, or across it, at the speed with the current;
* a step against the current at the speed of its depth class, weighted by how directly it goes upstream (the cosine of the angle between the step and the flow);
* no step into water that cannot be waded (too deep, or too fast for its depth).

The fastest walk between two points upstream of each other therefore leaves the water, follows the bank and enters again, and the order of the points (an asymmetric traveling-salesman tour from the access point and back, improved by moving and reversing parts of it) runs with the current wherever it can.

Outside the model nothing is known about the ground; it is taken as land that can be walked on, which the factsheet states.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from axqua.survey.field import FlowField
from axqua.survey.settings import GroundSettings

#: Slower than this the water has no direction that matters for walking [m/s].
STILL = 0.05
KMH = 1.0 / 3.6


@dataclass
class WalkGrid:
    x0: float
    y0: float
    cell: float
    nx: int
    ny: int
    depth: np.ndarray           # (ny, nx)
    u: np.ndarray
    v: np.ndarray
    water: np.ndarray           # wet cells of the model
    passable: np.ndarray
    graph: object               # csr matrix of step times [s]

    def cell_of(self, x: float, y: float) -> int:
        ix = int(np.clip((x - self.x0) // self.cell, 0, self.nx - 1))
        iy = int(np.clip((y - self.y0) // self.cell, 0, self.ny - 1))
        return iy * self.nx + ix

    def center(self, index) -> tuple[np.ndarray, np.ndarray]:
        index = np.asarray(index)
        return self.x0 + (index % self.nx + 0.5) * self.cell, self.y0 + (index // self.nx + 0.5) * self.cell

    def nearest_passable(self, x: float, y: float) -> int:
        index = self.cell_of(x, y)
        if self.passable.flat[index]:
            return index
        cells = np.flatnonzero(self.passable.ravel())
        cx, cy = self.center(cells)
        return int(cells[np.argmin(np.hypot(cx - x, cy - y))])


def against_speed(ground: GroundSettings, depth: np.ndarray) -> np.ndarray:
    """The walking speed straight against the current at *depth* [m/s]."""
    speed = np.full(np.shape(depth), float(ground.speed_with_current))
    for deeper, value in sorted(ground.speed_against_current):
        speed = np.where(np.asarray(depth) > float(deeper), float(value), speed)
    return speed * KMH


def build_grid(flow: FlowField, ground: GroundSettings, sites_xy, *, max_cells: int = 160_000, min_cell: float = 1.0, margin: float = 30.0) -> WalkGrid:
    """The raster of walking times around the wetted reach and the *sites_xy* ``[(x, y), ...]``."""
    from scipy.sparse import coo_matrix

    wet = flow.wet
    xs = np.concatenate([flow.x[wet] if wet.any() else flow.x, [p[0] for p in sites_xy]])
    ys = np.concatenate([flow.y[wet] if wet.any() else flow.y, [p[1] for p in sites_xy]])
    x0, x1, y0, y1 = xs.min() - margin, xs.max() + margin, ys.min() - margin, ys.max() + margin
    cell = max(float(min_cell), 0.5 * flow.edge_length, float(np.sqrt((x1 - x0) * (y1 - y0) / max_cells)))
    nx, ny = int(np.ceil((x1 - x0) / cell)), int(np.ceil((y1 - y0) / cell))
    gx, gy = np.meshgrid(x0 + (np.arange(nx) + 0.5) * cell, y0 + (np.arange(ny) + 0.5) * cell)
    depth, u, v, inside = (a.reshape(ny, nx) for a in flow.sample(gx.ravel(), gy.ravel()))
    water = inside & (depth > flow.wet_depth)
    speed = np.hypot(u, v)
    passable = ~water | ground.wadeable(depth, speed)
    v_land, v_with = ground.speed_land * KMH, ground.speed_with_current * KMH
    v_against = against_speed(ground, depth)
    index = np.arange(nx * ny).reshape(ny, nx)
    rows, cols, times = [], [], []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        a = (slice(max(0, -dy), ny - max(0, dy)), slice(max(0, -dx), nx - max(0, dx)))
        b = (slice(max(0, dy), ny - max(0, -dy)), slice(max(0, dx), nx - max(0, -dx)))
        ok = passable[a] & passable[b]
        if abs(dx) + abs(dy) == 2:
            # no cutting the corner of water that cannot be waded
            ok &= passable[a[0], b[1]] & passable[b[0], a[1]]
        length = cell * float(np.hypot(dx, dy))
        in_water = water[a] | water[b]
        both = water[a] & water[b]
        fu = np.where(both, 0.5 * (u[a] + u[b]), np.where(water[b], u[b], u[a]))
        fv = np.where(both, 0.5 * (v[a] + v[b]), np.where(water[b], v[b], v[a]))
        fs = np.hypot(fu, fv)
        cosine = np.where(fs > STILL, (dx * fu + dy * fv) / (np.hypot(dx, dy) * np.where(fs > STILL, fs, 1.0)), 1.0)
        slow = np.where(water[a] & water[b], np.minimum(v_against[a], v_against[b]), np.where(water[b], v_against[b], v_against[a]))
        wading = np.where(cosine >= 0, v_with, v_with + (slow - v_with) * (-cosine))
        pace = np.where(in_water, wading, v_land)
        rows.append(index[a][ok])
        cols.append(index[b][ok])
        times.append((length / pace)[ok])
    graph = coo_matrix((np.concatenate(times), (np.concatenate(rows), np.concatenate(cols))), shape=(nx * ny, nx * ny)).tocsr()
    return WalkGrid(x0=float(x0), y0=float(y0), cell=float(cell), nx=nx, ny=ny, depth=depth, u=u, v=v, water=water, passable=passable, graph=graph)


def reachable_nodes(grid: WalkGrid, flow: FlowField, x: float, y: float) -> np.ndarray:
    """The mesh nodes a surveyor can walk to from ``(x, y)`` and back."""
    from scipy.sparse.csgraph import dijkstra

    start = grid.nearest_passable(x, y)
    there = dijkstra(grid.graph, directed=True, indices=start)
    back = dijkstra(grid.graph.T.tocsr(), directed=True, indices=start)
    ok = (np.isfinite(there) & np.isfinite(back)).reshape(grid.ny, grid.nx)
    ix = np.clip(((flow.x - grid.x0) // grid.cell).astype(int), 0, grid.nx - 1)
    iy = np.clip(((flow.y - grid.y0) // grid.cell).astype(int), 0, grid.ny - 1)
    inside = (flow.x >= grid.x0) & (flow.x < grid.x0 + grid.nx * grid.cell) & (flow.y >= grid.y0) & (flow.y < grid.y0 + grid.ny * grid.cell)
    return ok[iy, ix] & inside


class Router:
    """The walking times between the sites of a plan and the lines of the walks. Site 0 is the access point."""

    def __init__(self, grid: WalkGrid, sites_xy) -> None:
        from scipy.sparse.csgraph import dijkstra

        self.grid = grid
        self.xy = [(float(x), float(y)) for x, y in sites_xy]
        self.cells = [grid.nearest_passable(x, y) for x, y in self.xy]
        n = len(self.cells)
        self.seconds = np.full((n, n), np.inf)
        self._previous: list[np.ndarray] = []
        for i, cell in enumerate(self.cells):
            distance, previous = dijkstra(grid.graph, directed=True, indices=cell, return_predecessors=True)
            self.seconds[i] = distance[self.cells]
            self._previous.append(previous.astype(np.int32))

    def reachable(self, start: int = 0) -> np.ndarray:
        """The sites a surveyor can walk to from *start* and back."""
        return np.isfinite(self.seconds[start]) & np.isfinite(self.seconds[:, start])

    def line(self, i: int, j: int) -> list[tuple[float, float]]:
        """The walk from site *i* to site *j* as coordinates, from the one to the other."""
        previous, cells = self._previous[i], [self.cells[j]]
        while cells[-1] != self.cells[i]:
            before = int(previous[cells[-1]])
            if before < 0:
                return [self.xy[i], self.xy[j]]
            cells.append(before)
        cx, cy = self.grid.center(np.asarray(cells[::-1]))
        coords = [self.xy[i]] + list(zip(cx.tolist()[1:-1], cy.tolist()[1:-1])) + [self.xy[j]]
        return _simplify(coords, 0.4 * self.grid.cell)

    def wet_share(self, i: int, j: int) -> float:
        """The share of the walk from *i* to *j* that is in water."""
        previous, cell, wet, count = self._previous[i], self.cells[j], 0, 0
        while cell != self.cells[i] and cell >= 0:
            wet += bool(self.grid.water.flat[cell])
            count += 1
            cell = int(previous[cell])
        return wet / count if count else 0.0


def _simplify(coords, tolerance: float):
    if len(coords) < 3:
        return coords
    from shapely.geometry import LineString

    if coords[0] == coords[-1] and len(set(coords)) < 2:
        return coords[:2]
    return [tuple(c) for c in LineString(coords).simplify(tolerance).coords]


def length(coords) -> float:
    a = np.asarray(coords, dtype=float)
    return float(np.hypot(*np.diff(a, axis=0).T).sum()) if len(a) > 1 else 0.0


def tour_seconds(seconds: np.ndarray, order, start: int = 0) -> float:
    """The walking time of the closed tour ``start, order..., start``."""
    if not len(order):
        return 0.0
    path = [start, *order, start]
    return float(seconds[path[:-1], path[1:]].sum())


def order_sites(seconds: np.ndarray, sites, start: int = 0, *, passes: int = 25) -> list[int]:
    """The order in which to visit *sites* from *start* and back: nearest neighbor first, then improved by moving single sites and short runs and by reversing parts, as long as that shortens the walk. The times are not symmetric, so a reversal is judged by the whole tour."""
    left = list(sites)
    order: list[int] = []
    here = start
    while left:
        nxt = min(left, key=lambda s: seconds[here, s])
        order.append(nxt)
        left.remove(nxt)
        here = nxt
    best = tour_seconds(seconds, order, start)
    n = len(order)
    for _ in range(passes):
        improved = False
        for run in (1, 2, 3):
            for i in range(0, n - run + 1):
                part, rest = order[i:i + run], order[:i] + order[i + run:]
                pieces = (part, part[::-1]) if run > 1 else (part,)
                # the best place for this run; the order changes only after all its places are judged, so that no trial mixes two orders
                trials = (rest[:j] + piece + rest[j:] for j in range(len(rest) + 1) if j != i for piece in pieces)
                cost, trial = min(((tour_seconds(seconds, t, start), t) for t in trials), key=lambda item: item[0], default=(best, order))
                if cost < best - 1e-6:
                    order, best, improved = trial, cost, True
        for i in range(n - 1):
            for j in range(i + 2, n + 1):
                trial = order[:i] + order[i:j][::-1] + order[j:]
                cost = tour_seconds(seconds, trial, start)
                if cost < best - 1e-6:
                    order, best, improved = trial, cost, True
        if not improved:
            break
    return order


def split_tour(seconds: np.ndarray, order, service, parts: int, start: int = 0) -> list[list[int]]:
    """Cut the tour *order* into *parts* runs of about equal work (walking to a site plus *service* there), one per surveyor."""
    parts = max(1, min(int(parts), len(order)))
    if parts == 1:
        return [list(order)]
    load, here = [], start
    for site in order:
        load.append(seconds[here, site] + service[site])
        here = site
    total = float(np.sum(load))
    out, current, done = [], [], 0.0
    for site, work in zip(order, load):
        remaining_sites = len(order) - sum(len(p) for p in out) - len(current)
        remaining_parts = parts - len(out)
        if current and len(out) < parts - 1 and (done + 0.5 * work >= total * (len(out) + 1) / parts or remaining_sites <= remaining_parts - 1):
            out.append(current)
            current = []
        current.append(site)
        done += work
    out.append(current)
    return [part for part in out if part]


def deal_tour(seconds: np.ndarray, order, service, parts: int, start: int = 0) -> list[list[int]]:
    """Deal the sites of the tour *order* to *parts* surveyors who work side by side: every next site goes to the surveyor who can be done with it first. The surveyors leapfrog along the tour and stay close to each other, so that one DGPS rover can follow them."""
    parts = max(1, min(int(parts), len(order)))
    runs: list[list[int]] = [[] for _ in range(parts)]
    clock, here = [0.0] * parts, [start] * parts
    for site in order:
        who = min(range(parts), key=lambda k: (clock[k] + seconds[here[k], site], k))
        clock[who] += seconds[here[who], site] + service[site]
        here[who] = site
        runs[who].append(site)
    return [run for run in runs if run]


__all__ = ["Router", "WalkGrid", "against_speed", "build_grid", "deal_tour", "length", "order_sites", "reachable_nodes", "split_tour", "tour_seconds"]
