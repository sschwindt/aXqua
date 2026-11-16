"""Walking through a river: how long it takes from one point to another, and along which line.

Wading is not symmetric. With the current a surveyor is carried along; against it every step costs more the deeper the water is, and above the wading limit there is no way at all. On a dry bank one walks at ease. The plan therefore does not connect its points by straight lines. It lays a raster over the reach, gives every step between two neighboring cells the time it takes in that direction, and finds the fastest walk with Dijkstra's algorithm on the directed graph of those steps:

* a step on land at the land speed, where land can be walked on: within the bank strip along the wetted edge (1 m by default) and inside the walkable areas the user draws;
* a step in water with the current, or across it, at the speed with the current;
* a step against the current at the speed of its depth class, weighted by how directly it goes upstream (the cosine of the angle between the step and the flow);
* no step into water that cannot be waded (too deep, or too fast for its depth).

The fastest walk between two points upstream of each other therefore leaves the water, follows the bank and enters again, and the order of the points (an asymmetric traveling-salesman tour from the access point and back, improved by moving and reversing parts of it) runs with the current wherever it can.

**Dry ground is not walked on, with two exceptions.** Terrain beside a river is often next to impassable (riparian forest, riprap, steep banks, private land), and the model knows nothing about it. A surveyor therefore walks on land only within the bank strip, ``ground.bank_strip`` meters from the wetted edge of the result the plan rests on, and inside the polygons of the layer ``walkable_areas`` of ``survey-plan.gpkg`` (paths, gravel bars, meadows). On the raster the strip is the ring of cells around the water, so it is never narrower than one cell. This holds for dry ground inside the model, a gravel bar for instance, as much as for ground outside it.

A site on land that is not connected to the water this way, typically an access point set on a road, is joined to the nearest ground that is by a straight line walked at the land speed (:attr:`WalkGrid.links`); the plan warns of a long one, because nothing says that the line can be walked.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from axqua.survey.field import FlowField, SurveyError
from axqua.survey.settings import GroundSettings

#: Slower than this the water has no direction that matters for walking [m/s].
STILL = 0.05
#: The raster takes in walkable areas this far around the wetted reach and the sites [m].
AREA_REACH = 150.0
#: A straight line from a site to ground that can be walked on is worth a warning from this length on [m].
LONG_LINK = 10.0
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
    graph: object               # csr matrix of step times [s] between the cells that can be walked on: row and column n stand for the cell nodes[n]
    nodes: np.ndarray           # the cells of the graph
    node_of: np.ndarray         # the row of a cell in the graph, -1 for a cell that cannot be walked on
    strip: float = 0.0          # the width of the bank strip that was asked for [m]
    links: list = field(default_factory=list)    # (x, y, length [m]) of the sites joined to the walkable ground by a straight line

    def cell_of(self, x: float, y: float) -> int:
        ix = int(np.clip((x - self.x0) // self.cell, 0, self.nx - 1))
        iy = int(np.clip((y - self.y0) // self.cell, 0, self.ny - 1))
        return iy * self.nx + ix

    def center(self, index) -> tuple[np.ndarray, np.ndarray]:
        index = np.asarray(index)
        return self.x0 + (index % self.nx + 0.5) * self.cell, self.y0 + (index // self.nx + 0.5) * self.cell

    def node(self, index: int) -> int:
        """The row of the cell *index* in the graph. A cell that was made passable after the raster was built has none."""
        node = int(self.node_of[index])
        if node < 0:
            raise SurveyError("a point of the plan lies on ground that the walking raster does not hold")
        return node

    def nearest_passable(self, x: float, y: float) -> int:
        index = self.cell_of(x, y)
        if self.passable.flat[index]:
            return index
        cells = np.flatnonzero(self.passable.ravel())
        if not len(cells):
            raise SurveyError("no ground of the reach can be walked on or waded: all of it is water deeper or faster than the wading limits", remedy="Plan at a lower discharge, raise the wading limits, or choose an airborne or a kayak survey.")
        cx, cy = self.center(cells)
        return int(cells[np.argmin(np.hypot(cx - x, cy - y))])


def against_speed(ground: GroundSettings, depth: np.ndarray) -> np.ndarray:
    """The walking speed straight against the current at *depth* [m/s]."""
    speed = np.full(np.shape(depth), float(ground.speed_with_current))
    for deeper, value in sorted(ground.speed_against_current):
        speed = np.where(np.asarray(depth) > float(deeper), float(value), speed)
    return speed * KMH


def build_grid(flow: FlowField, ground: GroundSettings, sites_xy, *, walkable=None, max_cells: int = 2_500_000, min_cell: float = 1.0, margin: float = 30.0) -> WalkGrid:
    """The raster of walking times around the wetted reach and the *sites_xy* ``[(x, y), ...]``. *walkable* is the union of the polygons in which dry ground can be walked on (a shapely geometry), or None."""
    from scipy.ndimage import distance_transform_edt
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    wet = flow.wet
    xs = np.concatenate([flow.x[wet] if wet.any() else flow.x, [p[0] for p in sites_xy]])
    ys = np.concatenate([flow.y[wet] if wet.any() else flow.y, [p[1] for p in sites_xy]])
    x0, x1, y0, y1 = xs.min() - margin, xs.max() + margin, ys.min() - margin, ys.max() + margin
    if walkable is not None and walkable.is_empty:
        walkable = None
    if walkable is not None:                      # room for the areas, as far as they lie around the reach: a meadow of a square kilometer must not coarsen the raster
        ax0, ay0, ax1, ay1 = walkable.bounds
        x0, y0 = max(min(x0, ax0 - margin), x0 - AREA_REACH), max(min(y0, ay0 - margin), y0 - AREA_REACH)
        x1, y1 = min(max(x1, ax1 + margin), x1 + AREA_REACH), min(max(y1, ay1 + margin), y1 + AREA_REACH)
    # Cells of 1 m where the reach allows it: the strip along the wetted edge is as wide as that. Only the cells that can be walked on enter the graph, so
    # the fine raster costs little.
    cell = max(float(min_cell), float(np.sqrt((x1 - x0) * (y1 - y0) / max_cells)))
    nx, ny = int(np.ceil((x1 - x0) / cell)), int(np.ceil((y1 - y0) / cell))
    gx, gy = np.meshgrid(x0 + (np.arange(nx) + 0.5) * cell, y0 + (np.arange(ny) + 0.5) * cell)
    # The plans of two kinds of survey lay the same raster over the reach: the fields on it are sampled once.
    key = (float(x0), float(y0), float(cell), nx, ny)
    if flow._cache.get("raster", (None,))[0] != key:
        flow._cache["raster"] = (key, tuple(a.reshape(ny, nx) for a in flow.sample(gx.ravel(), gy.ravel())))
    depth, u, v, inside = flow._cache["raster"][1]
    water = inside & (depth > flow.wet_depth)
    speed = np.hypot(u, v)
    # Dry ground can be walked on within the bank strip and inside the walkable areas, and nowhere else. The strip is measured from the cells in water and is
    # at least the ring of cells around them; a cell that holds a wet node of the model belongs to it too (a measurement point at the very edge of the water).
    node_cell = np.zeros((ny, nx), dtype=bool)
    wet_here = (flow.x >= x0) & (flow.x < x0 + nx * cell) & (flow.y >= y0) & (flow.y < y0 + ny * cell) & wet
    node_cell[((flow.y[wet_here] - y0) // cell).astype(int), ((flow.x[wet_here] - x0) // cell).astype(int)] = True
    strip = float(ground.bank_strip)
    away = distance_transform_edt(~water) * cell if water.any() else np.full((ny, nx), np.inf)
    on_land = ~water & ((away <= max(strip + 0.5 * cell, 1.5 * cell)) | node_cell)
    on_land &= ~_river_goes_on(flow, on_land & ~inside, gx, gy, 2.0 * max(strip + 0.5 * cell, 1.5 * cell) + flow.edge_length)
    if walkable is not None:
        import shapely

        area = shapely.buffer(walkable, 0.5 * cell)          # a cell the area reaches into counts
        bx0, by0, bx1, by1 = area.bounds
        near = ~water & ~on_land & (gx >= bx0) & (gx <= bx1) & (gy >= by0) & (gy <= by1)
        if near.any():
            on_land[near] = shapely.contains_xy(area, gx[near], gy[near])
    passable = (water & ground.wadeable(depth, speed)) | on_land
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
    rows, cols, times = np.concatenate(rows), np.concatenate(cols), np.concatenate(times)
    # The graph holds the cells that can be walked on and the cells of the sites, which may lie on ground that cannot.
    site_cells = [int(np.clip((y - y0) // cell, 0, ny - 1)) * nx + int(np.clip((x - x0) // cell, 0, nx - 1)) for x, y in sites_xy]
    member = passable.ravel().copy()
    member[site_cells] = True
    nodes = np.flatnonzero(member)
    node_of = np.full(nx * ny, -1, dtype=np.int32)
    node_of[nodes] = np.arange(len(nodes), dtype=np.int32)
    rows, cols, count = node_of[rows], node_of[cols], len(nodes)
    # A site on land that the strip and the areas do not connect to water a surveyor can stand in is joined to the nearest ground that is, by a straight line.
    links, extra = [], ([], [], [])
    _, part = connected_components(coo_matrix((times, (rows, cols)), shape=(count, count)).tocsr(), directed=False) if count else (0, np.zeros(0, dtype=int))
    joined = np.zeros(int(part.max()) + 1 if count else 0, dtype=bool)
    joined[part[(water & passable).ravel()[nodes]]] = True
    targets = nodes[joined[part] & passable.ravel()[nodes]] if count else nodes
    if len(targets):
        tx, ty = x0 + (targets % nx + 0.5) * cell, y0 + (targets // nx + 0.5) * cell
        for (x, y), here in zip(sites_xy, site_cells):
            if water.flat[here] or (passable.flat[here] and joined[part[node_of[here]]]):
                continue
            distance = np.hypot(tx - x, ty - y)
            nearest = int(np.argmin(distance))
            seconds = float(distance[nearest]) / v_land
            passable.flat[here] = True
            a, b = int(node_of[here]), int(node_of[targets[nearest]])
            extra[0].extend((a, b))
            extra[1].extend((b, a))
            extra[2].extend((seconds, seconds))
            links.append((float(x), float(y), float(distance[nearest])))
    graph = coo_matrix((np.concatenate([times, extra[2]]), (np.concatenate([rows, extra[0]]).astype(np.int64), np.concatenate([cols, extra[1]]).astype(np.int64))), shape=(count, count)).tocsr()
    return WalkGrid(x0=float(x0), y0=float(y0), cell=float(cell), nx=nx, ny=ny, depth=depth, u=u, v=v, water=water, passable=passable, graph=graph, nodes=nodes, node_of=node_of, strip=strip, links=links)


def _river_goes_on(flow: FlowField, cells: np.ndarray, gx: np.ndarray, gy: np.ndarray, reach: float) -> np.ndarray:
    """The *cells* outside the model that lie beyond an inflow or an outflow in water. The model ends there and the river does not: without this the strip along the wetted edge would lead around the end of the water, and the plan would cross a river of any depth on dry ground."""
    out = np.zeros(cells.shape, dtype=bool)
    open_edges, other_edges = flow.open_water_edges()
    if not len(open_edges) or not cells.any():
        return out
    import shapely

    def lines(edges):
        return shapely.multilinestrings(shapely.linestrings(np.stack([flow.x[edges], flow.y[edges]], axis=2)))

    river = lines(open_edges)
    x0, y0, x1, y1 = river.bounds
    near = np.flatnonzero((cells & (gx >= x0 - reach) & (gx <= x1 + reach) & (gy >= y0 - reach) & (gy <= y1 + reach)).ravel())
    points = shapely.points(gx.ravel()[near], gy.ravel()[near])
    to_river = shapely.distance(points, river)
    near, points, to_river = near[to_river <= reach], points[to_river <= reach], to_river[to_river <= reach]
    # beside the model, along a wall or a dry bank, the nearest edge of the outline is none of the inflow or the outflow, and the strip stays
    to_bank = shapely.distance(points, lines(other_edges)) if len(other_edges) and len(points) else np.full(len(points), np.inf)
    out.flat[near[to_river <= to_bank + 1e-6]] = True
    return out


def link_warnings(grid: WalkGrid, what: str = "the access point") -> list[str]:
    """What to say about a site that only a straight line joins to the ground that can be walked on."""
    return [f"{what} lies {distance:.0f} m from ground the plan can walk on (the strip of {grid.strip:g} m along the wetted edge and the walkable areas): the plan takes the straight line between them; draw a walkable area where the way there is another"
            for _x, _y, distance in grid.links if distance > LONG_LINK]


def reachable_nodes(grid: WalkGrid, flow: FlowField, x: float, y: float) -> np.ndarray:
    """The mesh nodes a surveyor can walk to from ``(x, y)`` and back."""
    from scipy.sparse.csgraph import dijkstra

    start = grid.node(grid.nearest_passable(x, y))
    there = dijkstra(grid.graph, directed=True, indices=start)
    back = dijkstra(grid.graph.T.tocsr(), directed=True, indices=start)
    ok = np.zeros(grid.nx * grid.ny, dtype=bool)
    ok[grid.nodes] = np.isfinite(there) & np.isfinite(back)
    ok = ok.reshape(grid.ny, grid.nx)
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
        self.cells = [grid.node(grid.nearest_passable(x, y)) for x, y in self.xy]      # rows of the graph
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
        cx, cy = self.grid.center(self.grid.nodes[np.asarray(cells[::-1])])
        coords = [self.xy[i]] + list(zip(cx.tolist()[1:-1], cy.tolist()[1:-1])) + [self.xy[j]]
        return _simplify(coords, 0.4 * self.grid.cell)

    def wet_share(self, i: int, j: int) -> float:
        """The share of the walk from *i* to *j* that is in water."""
        previous, cell, wet, count = self._previous[i], self.cells[j], 0, 0
        while cell != self.cells[i] and cell >= 0:
            wet += bool(self.grid.water.flat[self.grid.nodes[cell]])
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


def split_tour(seconds: np.ndarray, order, service, parts, start: int = 0) -> list[list[int]]:
    """Cut the tour *order* into runs of work (walking to a site plus *service* there). *parts* is their number, for runs of about equal work, or one weight per run: teams of three and of one surveyor get three quarters and one quarter of the work."""
    weights = [1.0] * max(1, int(parts)) if np.isscalar(parts) else [max(float(w), 0.0) for w in parts]
    weights = weights[:max(1, len(order))] or [1.0]
    if sum(weights) <= 0:
        weights = [1.0] * len(weights)
    parts = len(weights)
    if parts == 1:
        return [list(order)]
    share = np.cumsum(weights) / float(sum(weights))
    load, here = [], start
    for site in order:
        load.append(seconds[here, site] + service[site])
        here = site
    total = float(np.sum(load))
    out, current, done = [], [], 0.0
    for site, work in zip(order, load):
        remaining_sites = len(order) - sum(len(p) for p in out) - len(current)
        remaining_parts = parts - len(out)
        if current and len(out) < parts - 1 and (done + 0.5 * work >= total * share[len(out)] or remaining_sites <= remaining_parts - 1):
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


__all__ = ["Router", "WalkGrid", "against_speed", "build_grid", "deal_tour", "length", "link_warnings", "order_sites", "reachable_nodes", "split_tour", "tour_seconds"]
