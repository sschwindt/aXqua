"""Where the water of a catchment goes, from the terrain model alone.

Two questions the hydrology checkup asks before anything is meshed:

**Does water leave through a wall?** The outline of a hydrology case is meant to be the drainage divide, which is a solid wall by definition: no surface water crosses it. Where the outline is not the divide, the terrain slopes out of the domain under the wall, and in the model the water collected upslope ponds against it. To find those places, every cell of the outline is opened as an exit and the terrain is flooded inwards from them (priority-flood, Barnes et al., 2014): each cell drains to the cell that flooded it, over its spill point where it lies in a depression. An exit cell whose contributing area is large and which lies on no outflow line is a wall the flow crosses.

**Which closed depressions are there?** The same flood fills every depression to its spill level. A depression keeps the rain that falls into it until it is full, which is right for a pond, and wrong where the terrain model blocks a culvert or a bridge opening. The checkup names the largest ones so they can be told apart.

The DEM is read over the outline at a cell size that keeps the grid at ``max_cells`` cells, so the check takes seconds on a catchment of any size.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class TerrainGrid:
    """The DEM over the catchment, resampled, with the mask of the outline."""

    z: np.ndarray                 # (rows, cols) elevation, nan outside
    mask: np.ndarray              # (rows, cols) inside the outline and with a value
    x0: float                     # x of the left edge
    y0: float                     # y of the top edge
    cell: float                   # cell size [m]

    def centre(self, flat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        rows, cols = np.divmod(np.asarray(flat), self.z.shape[1])
        return self.x0 + (cols + 0.5) * self.cell, self.y0 - (rows + 0.5) * self.cell


def load_grid(cfg, *, max_cells: int = 250_000) -> TerrainGrid:
    """Read ``geodata.dem_initial`` over ``geodata.boundary``, averaged to at most *max_cells* cells (never finer than the DEM itself)."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin
    from rasterio.windows import from_bounds

    from axqua.solvers.telemac.mesh import roi_polygon

    poly = roi_polygon(cfg)
    minx, miny, maxx, maxy = poly.bounds
    with rasterio.open(cfg.geodata.dem_initial) as dem:
        native = abs(dem.transform.a)
        cell = max(native, math.sqrt((maxx - minx) * (maxy - miny) / float(max_cells)))
        cols = max(int(math.ceil((maxx - minx) / cell)), 2)
        rows = max(int(math.ceil((maxy - miny) / cell)), 2)
        window = from_bounds(minx, maxy - rows * cell, minx + cols * cell, maxy,
                             transform=dem.transform)
        data = dem.read(1, window=window, out_shape=(rows, cols), boundless=True,
                        masked=True, resampling=Resampling.average,
                        fill_value=dem.nodata if dem.nodata is not None else np.nan)
        z = np.ma.filled(data.astype(float), np.nan)
        if dem.nodata is not None:
            z[z == dem.nodata] = np.nan
    transform = from_origin(minx, maxy, cell, cell)
    inside = geometry_mask([poly], out_shape=(rows, cols), transform=transform,
                           invert=True, all_touched=False)
    return TerrainGrid(z=z, mask=inside & np.isfinite(z), x0=minx, y0=maxy, cell=cell)


@dataclass
class Flood:
    """The result of :func:`priority_flood` on a grid."""

    filled: np.ndarray            # (rows, cols) spill elevation, nan outside
    parent: np.ndarray            # flat index each cell drains to; -1 exits, -2 outside
    area: np.ndarray              # (rows, cols) contributing area [m2]
    exits: np.ndarray             # flat indices of the outline cells (the seeds)


_NEIGHBORS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def priority_flood(grid: TerrainGrid) -> Flood:
    """Flood the grid inward from its outline cells; see the module docstring."""
    rows, cols = grid.z.shape
    mask = grid.mask.ravel()
    z = np.where(mask, grid.z.ravel(), np.nan)
    n = rows * cols
    padded = np.pad(grid.mask, 1, constant_values=False)
    edge = np.zeros_like(grid.mask)
    for dr, dc in _NEIGHBORS:
        edge |= ~padded[1 + dr:1 + dr + rows, 1 + dc:1 + dc + cols]
    seeds = np.flatnonzero((edge & grid.mask).ravel())
    filled = z.copy()
    parent = np.full(n, -2, dtype=np.int64)
    closed = ~mask.copy()
    heap = [(float(z[i]), int(i)) for i in seeds]
    heapq.heapify(heap)
    closed[seeds] = True
    parent[seeds] = -1
    order: list[int] = []
    zl, fl, cl, pl = z.tolist(), filled.tolist(), closed.tolist(), parent.tolist()
    while heap:
        level, i = heapq.heappop(heap)
        order.append(i)
        r, c = divmod(i, cols)
        for dr, dc in _NEIGHBORS:
            rr, cc = r + dr, c + dc
            if rr < 0 or cc < 0 or rr >= rows or cc >= cols:
                continue
            j = rr * cols + cc
            if cl[j]:
                continue
            cl[j] = True
            fj = zl[j] if zl[j] > level else level
            fl[j] = fj
            pl[j] = i
            heapq.heappush(heap, (fj, j))
    filled = np.array(fl)
    parent = np.array(pl, dtype=np.int64)
    acc = np.where(mask, grid.cell * grid.cell, 0.0).tolist()
    for i in reversed(order):
        p = pl[i]
        if p >= 0:
            acc[p] += acc[i]
    return Flood(filled=filled.reshape(rows, cols), parent=parent,
                 area=np.array(acc).reshape(rows, cols), exits=seeds)


@dataclass
class WallCrossing:
    """A stretch of wall the flow of the terrain crosses."""

    x: float
    y: float
    area_m2: float


@dataclass
class Depression:
    """A closed depression of the terrain model."""

    x: float
    y: float
    depth_m: float
    volume_m3: float
    area_m2: float


@dataclass
class TerrainReport:
    catchment_m2: float
    cell_m: float
    crossings: list[WallCrossing] = field(default_factory=list)
    crossing_m2: float = 0.0              # all area draining through walls
    depressions: list[Depression] = field(default_factory=list)
    depression_m3: float = 0.0


def analyze(cfg, *, max_cells: int = 250_000, min_depth: float = 0.05,
            outlet_tolerance: float | None = None) -> TerrainReport:
    """Wall crossings and closed depressions of a hydrology case (module docstring)."""
    from scipy import ndimage
    from shapely import distance, points

    grid = load_grid(cfg, max_cells=max_cells)
    flood = priority_flood(grid)
    cell_area = grid.cell * grid.cell
    total = float(grid.mask.sum()) * cell_area
    report = TerrainReport(catchment_m2=total, cell_m=grid.cell)

    # exits on an outflow line are the outlet; the others are walls
    outlet = None
    if cfg.boundaries.liquid_boundaries is not None:
        from shapely.ops import unary_union

        from axqua.core.boundaries import liquid_lines

        lines = liquid_lines(cfg)
        geoms = [geom for kind, geom in lines.items() if kind in ("outflow", "inflow")]
        outlet = unary_union(geoms) if geoms else None
    tol = outlet_tolerance if outlet_tolerance is not None else max(
        2.0 * grid.cell, 2.0 * cfg.mesh.default_size * cfg.mesh.size_scale)
    exits = flood.exits
    areas = flood.area.ravel()[exits]
    xs, ys = grid.centre(exits)
    on_line = np.zeros(exits.size, dtype=bool)
    if outlet is not None and exits.size:
        on_line = distance(points(xs, ys), outlet) <= tol
    wall = ~on_line & (areas >= 4 * cell_area)
    report.crossing_m2 = float(areas[wall].sum())
    # the largest exits through walls, at least five cells apart: where to look first
    taken: list[tuple[float, float]] = []
    for k in np.argsort(-areas):
        if not wall[k]:
            continue
        if any(math.hypot(xs[k] - x, ys[k] - y) < 5 * grid.cell for x, y in taken):
            continue
        taken.append((float(xs[k]), float(ys[k])))
        report.crossings.append(WallCrossing(float(xs[k]), float(ys[k]), float(areas[k])))
        if len(report.crossings) >= 5:
            break

    depth = np.where(grid.mask, flood.filled - grid.z, 0.0)
    pits = np.nan_to_num(depth) > min_depth
    labels, count = ndimage.label(pits, structure=np.ones((3, 3)))
    if count:
        index = np.arange(1, count + 1)
        volume = ndimage.sum(np.nan_to_num(depth), labels, index) * cell_area
        deepest = ndimage.maximum(np.nan_to_num(depth), labels, index)
        cells = ndimage.sum(pits, labels, index)
        centres = ndimage.center_of_mass(pits, labels, index)
        report.depression_m3 = float(np.sum(volume))
        for k in np.argsort(-volume)[:5]:
            r, c = centres[k]
            report.depressions.append(Depression(
                x=grid.x0 + (c + 0.5) * grid.cell, y=grid.y0 - (r + 0.5) * grid.cell,
                depth_m=float(deepest[k]), volume_m3=float(volume[k]),
                area_m2=float(cells[k]) * cell_area))
    return report
