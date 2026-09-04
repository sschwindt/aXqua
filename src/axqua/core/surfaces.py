"""Triangulated CAD surfaces (STL) as the terrain and domain description of a case.

Why this exists, given :mod:`axqua.core.structures` argues the opposite
-----------------------------------------------------------------------
That module explains why a *structure* needs no STL: axqua writes its own mesh, so a
dam only has to say where its footprint is and how high it stands. Nothing there
changes. What this module addresses is the other direction - a case whose **terrain
itself arrives as CAD**, because the thing being modelled was designed rather than
surveyed. A fish pass, a culvert, a flume, a spillway: there is no DEM of it, and
there never will be, because it exists as a construction drawing.

The design decision is therefore *not* to teach the meshers about triangles. It is to
decompose a set of STL parts, once, into exactly the artifacts axqua already consumes:

======================  ==================================================
STL part                becomes
======================  ==================================================
bed-like surfaces       a DEM GeoTIFF, i.e. ``geodata.dem_initial``
their coverage          the ROI polygon, i.e. ``geodata.boundary``
near-vertical surfaces  ``geodata.structures`` footprints with a crest
inlet / outlet patches  the lines of ``boundaries.liquid_boundaries``
per-part materials      ``geodata.roughness_zones`` + its table
======================  ==================================================

After that stage every downstream step - the gmsh mesher, the ``.cli`` classification,
the OpenFOAM lattice - runs unchanged and unaware that a CAD file was ever involved.
That is the whole point: one new stage instead of a second geometry pathway through
the entire package.

A height field is not the same thing as a solid
-----------------------------------------------
A CAD assembly is a set of closed volumes; a DEM is a single-valued function z(x, y).
The conversion is lossy wherever the geometry is not single-valued, which for a fish
pass is precisely at its walls. Hence the slope split: facets flatter than
``max_slope_deg`` are bed and are rasterised, facets steeper than ``min_slope_deg``
are walls and become footprints with a crest elevation, and what falls between the two
is reported rather than silently assigned. Overhangs (a soffit, a cantilevered
walkway) cannot be represented at all and are counted in the report, because a
modeller needs to know how much of the geometry the height field dropped.

Reading STL
-----------
Binary STL is a fixed 84-byte header followed by 50 bytes per triangle, so it reads in
one ``numpy`` call with a structured dtype - no dependency, and fast enough that the
550 MB parts of a real CAD export are limited by disk rather than by parsing. ASCII
STL is parsed by streaming the ``vertex`` lines. ``pyvista`` and ``meshio`` would both
do this, but neither is worth a hard dependency in :mod:`axqua.core`, which the QGIS
plugin's capability queries import.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("axqua")

#: bytes per triangle in a binary STL: 12 floats (normal + 3 vertices) + 2 attribute
BINARY_TRIANGLE_BYTES = 50
#: bytes of header before the triangle count
BINARY_HEADER_BYTES = 80
#: triangles read per chunk, keeping peak memory near 100 MB for any file size
CHUNK_TRIANGLES = 1_000_000

#: a facet flatter than this (degrees from horizontal) is bed
BED_MAX_SLOPE_DEG = 45.0
#: a facet steeper than this is a wall
WALL_MIN_SLOPE_DEG = 70.0


@dataclass
class Surface:
    """A triangulated surface: ``triangles`` is ``(n, 3, 3)`` of xyz per corner.

    Normals are recomputed from the vertex winding rather than trusted from the file.
    Exporters disagree about winding and some write zero normals, and every decision
    here - bed or wall, up or down - hangs on the normal, so it is not something to
    take on faith from a header field.
    """

    triangles: np.ndarray
    name: str = ""

    def __post_init__(self) -> None:
        self.triangles = np.asarray(self.triangles, dtype=float)
        if self.triangles.ndim != 3 or self.triangles.shape[1:] != (3, 3):
            raise ValueError(
                f"triangles must have shape (n, 3, 3), got {self.triangles.shape}")

    def __len__(self) -> int:
        return int(self.triangles.shape[0])

    @property
    def normals(self) -> np.ndarray:
        """Unit normals from the vertex winding; degenerate facets give a zero normal."""
        a, b, c = self.triangles[:, 0], self.triangles[:, 1], self.triangles[:, 2]
        n = np.cross(b - a, c - a)
        length = np.linalg.norm(n, axis=1)
        good = length > 0
        out = np.zeros_like(n)
        out[good] = n[good] / length[good, None]
        return out

    @property
    def bounds(self) -> tuple[float, float, float, float, float, float]:
        """``(xmin, ymin, zmin, xmax, ymax, zmax)``."""
        pts = self.triangles.reshape(-1, 3)
        return (*pts.min(axis=0), *pts.max(axis=0))

    def slope_deg(self) -> np.ndarray:
        """Angle of each facet from horizontal, 0 = flat, 90 = vertical."""
        nz = np.abs(self.normals[:, 2])
        return np.degrees(np.arccos(np.clip(nz, 0.0, 1.0)))

    def select(self, mask: np.ndarray) -> Surface:
        return Surface(self.triangles[np.asarray(mask, dtype=bool)], name=self.name)

    def transformed(self, transform: Transform) -> Surface:
        flat = transform.apply(self.triangles.reshape(-1, 3))
        return Surface(flat.reshape(-1, 3, 3), name=self.name)


@dataclass
class Transform:
    """Similarity transform from CAD-local to project coordinates.

    Scale, then rotation about the vertical axis, then translation - the only degrees
    of freedom a georeferencing job actually has when the CAD and the map agree about
    which way is up. A general 3D rotation is deliberately not offered: a drawing that
    needs one is a drawing whose vertical axis is wrong, and that should be fixed in
    the CAD rather than papered over here.
    """

    scale: float = 1.0
    rotation_deg: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0

    @property
    def is_identity(self) -> bool:
        return (self.scale == 1.0 and self.rotation_deg == 0.0
                and self.dx == 0.0 and self.dy == 0.0 and self.dz == 0.0)

    def apply(self, points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, dtype=float)
        out = pts * self.scale
        if self.rotation_deg:
            theta = math.radians(self.rotation_deg)
            cos, sin = math.cos(theta), math.sin(theta)
            x, y = out[:, 0].copy(), out[:, 1].copy()
            out[:, 0] = cos * x - sin * y
            out[:, 1] = sin * x + cos * y
        out[:, 0] += self.dx
        out[:, 1] += self.dy
        out[:, 2] += self.dz
        return out

    @classmethod
    def from_control_points(cls, cad, world, *, dz: float = 0.0) -> "Transform":
        """Fit scale, rotation and translation to matched point pairs.

        Two pairs determine a similarity transform exactly; more are fitted by least
        squares (the Umeyama solution, restricted to a similarity). This is how a
        georeference is normally established when bounding boxes cannot be matched -
        a construction sheet carries plans, sections and a title block, so its extent
        is not the structure's - and it needs only two features a person can identify
        in both the CAD and the drawing: a corner, a slot, an axis intersection.
        """
        cad = np.asarray(cad, dtype=float)[:, :2]
        world = np.asarray(world, dtype=float)[:, :2]
        if cad.shape != world.shape or len(cad) < 2:
            raise ValueError("need at least two matching point pairs of equal count")

        cad_mean, world_mean = cad.mean(axis=0), world.mean(axis=0)
        a, b = cad - cad_mean, world - world_mean
        variance = float((a ** 2).sum()) / len(cad)
        if variance == 0:
            raise ValueError("the CAD control points coincide; they must be distinct")
        covariance = b.T @ a / len(cad)
        u, singular, vt = np.linalg.svd(covariance)
        correction = np.eye(2)
        if np.linalg.det(u) * np.linalg.det(vt) < 0:     # forbid a mirror
            correction[1, 1] = -1.0
        rotation = u @ correction @ vt
        scale = float(np.trace(np.diag(singular) @ correction)) / variance
        offset = world_mean - scale * (rotation @ cad_mean)
        return cls(scale=scale,
                   rotation_deg=math.degrees(math.atan2(rotation[1, 0], rotation[0, 0])),
                   dx=float(offset[0]), dy=float(offset[1]), dz=dz)

    def residuals(self, cad, world) -> np.ndarray:
        """Distance between each transformed CAD point and its world counterpart."""
        cad = np.asarray(cad, dtype=float)
        padded = np.column_stack([cad[:, :2], np.zeros(len(cad))])
        moved = self.apply(padded)[:, :2]
        return np.linalg.norm(moved - np.asarray(world, dtype=float)[:, :2], axis=1)

    def inverse(self) -> Transform:
        """The transform that undoes this one, for reporting a point back in CAD space."""
        theta = math.radians(-self.rotation_deg)
        cos, sin = math.cos(theta), math.sin(theta)
        dx = -(cos * self.dx - sin * self.dy) / self.scale
        dy = -(sin * self.dx + cos * self.dy) / self.scale
        return Transform(scale=1.0 / self.scale, rotation_deg=-self.rotation_deg,
                         dx=dx, dy=dy, dz=-self.dz / self.scale)


def is_binary_stl(path: Path) -> bool:
    """Whether *path* is a binary STL.

    The ``solid`` magic word is not decisive: binary writers put arbitrary text in the
    84-byte header and several of them start it with ``solid``. What is decisive is
    whether the triangle count at offset 80 accounts for the file size exactly.
    """
    path = Path(path)
    size = path.stat().st_size
    if size < BINARY_HEADER_BYTES + 4:
        return False
    with path.open("rb") as handle:
        handle.seek(BINARY_HEADER_BYTES)
        count = int(np.frombuffer(handle.read(4), dtype="<u4")[0])
    return size == BINARY_HEADER_BYTES + 4 + count * BINARY_TRIANGLE_BYTES


def stl_triangle_count(path: Path) -> int:
    """Triangle count without reading the vertices (binary only; ASCII returns -1)."""
    if not is_binary_stl(path):
        return -1
    with Path(path).open("rb") as handle:
        handle.seek(BINARY_HEADER_BYTES)
        return int(np.frombuffer(handle.read(4), dtype="<u4")[0])


def read_stl(path: Path, *, name: str | None = None,
             max_triangles: int | None = None) -> Surface:
    """Read an STL file, binary or ASCII, into a :class:`Surface`.

    Parameters
    ----------
    path : Path
        The ``.stl`` file.
    name : str, optional
        Label carried on the surface; defaults to the file stem.
    max_triangles : int, optional
        Raise rather than read beyond this many triangles. A guard for the callers
        that hold several parts in memory at once: a CAD export of a real structure
        runs to millions of facets per part, and finding that out through the OOM
        killer halfway into a build is the failure this prevents.
    """
    path = Path(path)
    label = name if name is not None else path.stem
    if is_binary_stl(path):
        triangles = _read_binary_stl(path, max_triangles=max_triangles)
    else:
        triangles = _read_ascii_stl(path, max_triangles=max_triangles)
    surface = Surface(triangles, name=label)
    log.debug("read %s: %d triangles, bounds %s", path.name, len(surface),
              np.round(surface.bounds, 3).tolist())
    return surface


def _read_binary_stl(path: Path, *, max_triangles: int | None) -> np.ndarray:
    count = stl_triangle_count(path)
    if max_triangles is not None and count > max_triangles:
        raise ValueError(
            f"{path.name} has {count} triangles, over the {max_triangles} limit; "
            f"decimate the part in the CAD export or raise max_triangles")
    dtype = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    out = np.empty((count, 3, 3), dtype=float)
    with path.open("rb") as handle:
        handle.seek(BINARY_HEADER_BYTES + 4)
        done = 0
        while done < count:
            block = min(CHUNK_TRIANGLES, count - done)
            raw = np.frombuffer(handle.read(block * BINARY_TRIANGLE_BYTES), dtype=dtype,
                                count=block)
            out[done:done + block] = raw["v"].astype(float)
            done += block
    return out


_VERTEX = re.compile(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)")


def _read_ascii_stl(path: Path, *, max_triangles: int | None) -> np.ndarray:
    coords: list[tuple[float, float, float]] = []
    with path.open("rb") as handle:
        for line in handle:
            match = _VERTEX.search(line)
            if match:
                coords.append((float(match.group(1)), float(match.group(2)),
                               float(match.group(3))))
    if len(coords) % 3:
        raise ValueError(f"{path.name}: {len(coords)} vertices is not a whole number "
                         f"of triangles - the file is truncated or malformed")
    count = len(coords) // 3
    if max_triangles is not None and count > max_triangles:
        raise ValueError(
            f"{path.name} has {count} triangles, over the {max_triangles} limit")
    return np.asarray(coords, dtype=float).reshape(count, 3, 3)


def split_by_slope(surface: Surface, *, bed_max_slope_deg: float = BED_MAX_SLOPE_DEG,
                   min_slope_deg: float = WALL_MIN_SLOPE_DEG) -> dict:
    """Classify facets into bed, wall, overhang and the unclassified remainder.

    ``bed`` is upward-facing and flatter than *bed_max_slope_deg*; ``wall`` is steeper
    than *min_slope_deg*; ``overhang`` is downward-facing and flat enough to be a
    soffit, which a height field cannot represent at all. Whatever sits between the bed
    and wall thresholds is returned as ``other`` so a caller can report it instead of
    guessing.
    """
    slope = surface.slope_deg()
    up = surface.normals[:, 2] > 0
    horizontal = slope <= bed_max_slope_deg
    bed = up & horizontal
    wall = slope >= min_slope_deg
    overhang = ~up & horizontal
    other = ~(horizontal | wall)
    return {"bed": bed, "wall": wall, "overhang": overhang, "other": other,
            # Every near-horizontal facet, whichever way it faces. This is what a DEM
            # is actually built from, because winding is not to be trusted: exporters
            # write closed solids inside-out often enough that a whole part arrives
            # with its top surface classified as overhang, contributes nothing, and
            # only warns. Taking the maximum over all horizontal facets recovers the
            # upper surface either way, and walls are already excluded by slope.
            "horizontal": horizontal}


def grid_from_bounds(bounds, resolution: float):
    """Raster transform, width and height covering *bounds* at *resolution*.

    Bounds are snapped outwards to whole multiples of the resolution, so two parts
    rasterised separately share a grid and can be combined cell by cell.
    """
    from rasterio.transform import from_origin

    xmin, ymin, xmax, ymax = bounds
    xmin = math.floor(xmin / resolution) * resolution
    ymin = math.floor(ymin / resolution) * resolution
    xmax = math.ceil(xmax / resolution) * resolution
    ymax = math.ceil(ymax / resolution) * resolution
    width = max(1, int(round((xmax - xmin) / resolution)))
    height = max(1, int(round((ymax - ymin) / resolution)))
    return from_origin(xmin, ymax, resolution, resolution), width, height


def rasterize(surface: Surface, resolution: float, *, mode: str = "max",
              bounds=None, nodata: float = -9999.0) -> tuple[np.ndarray, object]:
    """Burn a surface into a height field.

    Each triangle is scan-converted over the cell centres it covers and its plane
    evaluated there, so the result is the exact surface elevation at the cell centre
    rather than a nearest-vertex approximation. Where several triangles cover one cell
    - which is what a multi-valued CAD surface *means* - ``mode`` decides: ``"max"``
    takes the highest, the right answer for a bed seen from above, and ``"min"`` the
    lowest, for a soffit seen from below.

    Returns the array (with *nodata* where no facet covered the cell) and the affine
    transform. Writing it out is :func:`write_dem`, kept separate so callers can
    combine several parts before touching the disk.
    """
    if mode not in ("max", "min"):
        raise ValueError(f"mode must be 'max' or 'min', got {mode!r}")
    if bounds is None:
        xmin, ymin, _, xmax, ymax, _ = surface.bounds
        bounds = (xmin, ymin, xmax, ymax)
    transform, width, height = grid_from_bounds(bounds, resolution)

    fill = -np.inf if mode == "max" else np.inf
    grid = np.full((height, width), fill, dtype=float)
    if len(surface) == 0:
        return np.full((height, width), nodata, dtype=float), transform

    origin_x, origin_y = transform.c, transform.f
    tri = surface.triangles
    reducer = np.maximum if mode == "max" else np.minimum

    for start in range(0, len(surface), 50_000):
        block = tri[start:start + 50_000]
        _burn_block(block, grid, origin_x, origin_y, resolution, width, height, reducer)

    empty = ~np.isfinite(grid)
    grid[empty] = nodata
    covered = float(1.0 - empty.mean())
    if covered < 0.999:
        log.debug("%s covers %.1f%% of its own bounding box", surface.name,
                  covered * 100.0)
    return grid, transform


def _burn_block(triangles, grid, origin_x, origin_y, resolution, width, height,
                reducer) -> None:
    """Burn one block of triangles into *grid* in place.

    Two regimes, because a CAD export and a hand-built surface are opposite problems.
    A facet **smaller than one cell** - which is nearly every facet of a real export,
    where triangles are centimetres and cells decimetres - is burned by its vertices in
    one vectorised call: the vertices *are* the surface there, and the difference from
    evaluating the plane at the cell centre is the facet's own z-range, a fraction of a
    millimetre. A facet **spanning several cells** is scan-converted properly, so a
    coarse surface (two triangles over a whole ramp) still reproduces the plane exactly.
    Doing only the latter costs minutes on a multi-million-facet part; doing only the
    former loses the coarse case entirely.
    """
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    xs = triangles[:, :, 0]
    ys = triangles[:, :, 1]
    spans_cells = ((xs.max(axis=1) - xs.min(axis=1)) > resolution) | \
                  ((ys.max(axis=1) - ys.min(axis=1)) > resolution)

    small = triangles[~spans_cells]
    if small.size:
        pts = small.reshape(-1, 3)
        cols = np.clip(((pts[:, 0] - origin_x) / resolution).astype(int), 0, width - 1)
        rows = np.clip(((origin_y - pts[:, 1]) / resolution).astype(int), 0, height - 1)
        reducer.at(grid, (rows, cols), pts[:, 2])

    triangles = triangles[spans_cells]
    if not triangles.size:
        return
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    # the plane through each triangle: z = alpha*x + beta*y + gamma
    normals = np.cross(b - a, c - a)
    flat = np.abs(normals[:, 2]) < 1e-12          # vertical facets cover no cell area
    with np.errstate(divide="ignore", invalid="ignore"):
        alpha = np.where(flat, 0.0, -normals[:, 0] / np.where(flat, 1.0, normals[:, 2]))
        beta = np.where(flat, 0.0, -normals[:, 1] / np.where(flat, 1.0, normals[:, 2]))
    # a vertical facet has no plane over the cell centres and is skipped below; keeping
    # its coefficients finite stops a real CAD export (which has thousands of them)
    # from filling the log with invalid-value warnings
    gamma = a[:, 2] - alpha * a[:, 0] - beta * a[:, 1]

    xs = triangles[:, :, 0]
    ys = triangles[:, :, 1]
    col_lo = np.floor((xs.min(axis=1) - origin_x) / resolution).astype(int)
    col_hi = np.ceil((xs.max(axis=1) - origin_x) / resolution).astype(int)
    row_lo = np.floor((origin_y - ys.max(axis=1)) / resolution).astype(int)
    row_hi = np.ceil((origin_y - ys.min(axis=1)) / resolution).astype(int)

    # barycentric denominators, for the point-in-triangle test below
    denom = ((b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0])
             + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1]))
    degenerate = np.abs(denom) < 1e-15

    for i in range(triangles.shape[0]):
        if flat[i] or degenerate[i]:
            continue
        c0 = max(col_lo[i], 0)
        c1 = min(col_hi[i] + 1, width)
        r0 = max(row_lo[i], 0)
        r1 = min(row_hi[i] + 1, height)
        if c0 >= c1 or r0 >= r1:
            continue
        cx = origin_x + (np.arange(c0, c1) + 0.5) * resolution
        cy = origin_y - (np.arange(r0, r1) + 0.5) * resolution
        gx, gy = np.meshgrid(cx, cy)

        l1 = ((b[i, 1] - c[i, 1]) * (gx - c[i, 0])
              + (c[i, 0] - b[i, 0]) * (gy - c[i, 1])) / denom[i]
        l2 = ((c[i, 1] - a[i, 1]) * (gx - c[i, 0])
              + (a[i, 0] - c[i, 0]) * (gy - c[i, 1])) / denom[i]
        l3 = 1.0 - l1 - l2
        # a small negative tolerance keeps cell centres exactly on a shared edge from
        # falling through the crack between two triangles
        inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
        if not inside.any():
            continue
        z = alpha[i] * gx + beta[i] * gy + gamma[i]
        window = grid[r0:r1, c0:c1]
        reducer(window, np.where(inside, z, -np.inf if reducer is np.maximum else np.inf),
                out=window)


def write_dem(grid: np.ndarray, transform, path: Path, *, crs=None,
              nodata: float = -9999.0) -> Path:
    """Write a height field as a single-band float32 GeoTIFF."""
    import rasterio

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=grid.shape[0],
                       width=grid.shape[1], count=1, dtype="float32",
                       crs=crs, transform=transform, nodata=nodata,
                       compress="deflate") as dst:
        dst.write(grid.astype("float32"), 1)
    return path


def coverage_polygon(grid: np.ndarray, transform, *, nodata: float = -9999.0,
                     simplify: float | None = None):
    """The polygon of the cells that carry data, as the ROI of a CAD-defined case."""
    from rasterio import features
    from shapely.geometry import shape
    from shapely.ops import unary_union

    mask = np.isfinite(grid) & (grid != nodata)
    if not mask.any():
        raise ValueError("the rasterised surface has no valid cells at all")
    shapes = [shape(geom) for geom, value in
              features.shapes(mask.astype("uint8"), mask=mask, transform=transform)
              if value == 1]
    polygon = unary_union(shapes)
    if simplify:
        polygon = polygon.simplify(simplify)
    return polygon


def wall_footprints(surface: Surface, *, min_slope_deg: float = WALL_MIN_SLOPE_DEG,
                    resolution: float = 0.05, simplify: float | None = None,
                    min_area: float = 0.0) -> list[tuple[object, float]]:
    """Footprints of the near-vertical parts of a surface, with their crest elevations.

    Each connected footprint is returned with the highest elevation of the facets that
    produced it, which is what ``geodata.structures`` means by a crest. Rasterising the
    wall facets rather than unioning their 2D projections directly is deliberate: a CAD
    wall is thousands of slivers, and a raster union at the mesh's own resolution is
    both faster and free of the invalid-geometry problems that a shapely union of
    slivers reliably produces.
    """
    from rasterio import features
    from shapely.geometry import shape

    walls = surface.select(split_by_slope(surface, min_slope_deg=min_slope_deg)["wall"])
    if len(walls) == 0:
        return []

    xmin, ymin, _, xmax, ymax, _ = walls.bounds
    transform, width, height = grid_from_bounds((xmin, ymin, xmax, ymax), resolution)
    crest = np.full((height, width), -np.inf)
    origin_x, origin_y = transform.c, transform.f

    # A vertical facet has no area in plan, so the scan conversion in _burn_block
    # cannot see it. Burn its three edges instead, sampled along their own length:
    # marking only the corners would leave the middle of a long wall unburned, which
    # is exactly what a fish-pass sidewall is - two triangles spanning several metres.
    a, b, c = walls.triangles[:, 0], walls.triangles[:, 1], walls.triangles[:, 2]
    for start, end in ((a, b), (b, c), (c, a)):
        span = np.hypot(end[:, 0] - start[:, 0], end[:, 1] - start[:, 1]).max()
        steps = int(np.clip(math.ceil(span / (resolution * 0.5)), 1, 4096))
        for t in np.linspace(0.0, 1.0, steps + 1):
            pts = start + (end - start) * t
            cols = np.clip(((pts[:, 0] - origin_x) / resolution).astype(int),
                           0, width - 1)
            rows = np.clip(((origin_y - pts[:, 1]) / resolution).astype(int),
                           0, height - 1)
            np.maximum.at(crest, (rows, cols), pts[:, 2])
    # close the one-cell gaps between the projected corners of a long thin facet
    covered = np.isfinite(crest)
    filled = _dilate(covered)
    crest[filled & ~covered] = -np.inf

    out: list[tuple[object, float]] = []
    mask = filled.astype("uint8")
    for geom, value in features.shapes(mask, mask=filled, transform=transform):
        if value != 1:
            continue
        polygon = shape(geom)
        if simplify:
            polygon = polygon.simplify(simplify)
        if polygon.area < min_area:
            continue
        window = features.geometry_mask([polygon], out_shape=crest.shape,
                                        transform=transform, invert=True)
        heights = crest[window & covered]
        if heights.size == 0:
            continue
        out.append((polygon, float(heights.max())))
    return out


def _dilate(mask: np.ndarray) -> np.ndarray:
    """One-cell binary dilation, without pulling in scipy.ndimage for four lines."""
    out = mask.copy()
    out[:-1, :] |= mask[1:, :]
    out[1:, :] |= mask[:-1, :]
    out[:, :-1] |= mask[:, 1:]
    out[:, 1:] |= mask[:, :-1]
    return out


def patch_line(surface: Surface, *, simplify: float | None = None):
    """The centreline across a boundary patch, as a :class:`shapely.LineString`.

    An inlet or outlet patch of a CFD geometry is a planar lid across the channel. What
    ``boundaries.liquid_boundaries`` wants is a line *along* that cross-section, so the
    footprint's minimum rotated rectangle is taken and the mid-line of its two short
    sides returned - the line a modeller would have digitised by hand.
    """
    from shapely.geometry import LineString, MultiPoint

    pts = surface.triangles.reshape(-1, 3)[:, :2]
    rect = MultiPoint([tuple(p) for p in pts]).minimum_rotated_rectangle
    if not hasattr(rect, "exterior"):
        # The patch is vertical, so its footprint is already a line rather than a
        # rectangle - the normal case for an inlet or outlet lid across a channel.
        # Its two extreme points are the line we want.
        coords = np.asarray(rect.coords)
        line = LineString([tuple(coords[0]), tuple(coords[-1])])
        return line.simplify(simplify) if simplify else line
    corners = np.asarray(rect.exterior.coords)[:4]
    edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    lengths = [float(np.hypot(*(b - a))) for a, b in edges]
    short = int(np.argmin(lengths))
    opposite = (short + 2) % 4
    start = (edges[short][0] + edges[short][1]) / 2.0
    end = (edges[opposite][0] + edges[opposite][1]) / 2.0
    line = LineString([tuple(start), tuple(end)])
    return line.simplify(simplify) if simplify else line


def report(surface: Surface, *, bed_max_slope_deg: float = BED_MAX_SLOPE_DEG,
           min_slope_deg: float = WALL_MIN_SLOPE_DEG) -> dict:
    """What a part contains, for logging before anything is written.

    Report-only, in the convention of the rest of the package: it returns counts and a
    message and decides nothing. The fraction of facets that are overhangs is the
    number worth reading - it is exactly the geometry a height field will drop.
    """
    groups = split_by_slope(surface, bed_max_slope_deg=bed_max_slope_deg,
                            min_slope_deg=min_slope_deg)
    total = max(len(surface), 1)
    counts = {key: int(mask.sum()) for key, mask in groups.items()}
    xmin, ymin, zmin, xmax, ymax, zmax = surface.bounds
    result = {
        "name": surface.name,
        "triangles": len(surface),
        "bounds": (xmin, ymin, zmin, xmax, ymax, zmax),
        "size": (xmax - xmin, ymax - ymin, zmax - zmin),
        **counts,
        "overhang_fraction": counts["overhang"] / total,
        "message": "",
    }
    result["message"] = (
        f"{surface.name}: {len(surface)} triangles, "
        f"{(xmax - xmin):.2f} x {(ymax - ymin):.2f} x {(zmax - zmin):.2f} m, "
        f"{counts['bed']} bed / {counts['wall']} wall / {counts['overhang']} overhang "
        f"/ {counts['other']} between the thresholds")
    return result


def log_report(result: dict, logger_obj=None) -> dict:
    """Log a :func:`report` at the right level; returns it so it can be chained."""
    logger = logger_obj or log
    logger.info(result["message"])
    if result["overhang_fraction"] > 0.05:
        logger.warning(
            "%s: %.0f%% of the facets face downwards and cannot be represented by a "
            "height field (a soffit, a cantilever, or an inverted winding). They are "
            "dropped from the DEM - check that this part is really terrain.",
            result["name"], result["overhang_fraction"] * 100.0)
    if result["other"] > 0.2 * max(result["triangles"], 1):
        logger.warning(
            "%s: %d facets fall between the bed and wall slope thresholds and are "
            "neither rasterised nor turned into a footprint. Widen "
            "surfaces.bed_max_slope_deg or lower surfaces.wall_min_slope_deg.",
            result["name"], result["other"])
    return result
