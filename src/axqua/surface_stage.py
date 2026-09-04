"""Stage 0: turn a CAD assembly into the geodata a case is normally given.

This is the bridge between :mod:`axqua.core.surfaces`, which knows about triangles,
and the rest of the package, which knows about DEMs and vector layers. It runs before
the five numbered pipeline stages, writes its artifacts into ``preprocessing_dir``, and
after it the case is indistinguishable from one built on a surveyed DEM.

It is deliberately re-runnable and lazy: the outputs are checked against the STL
modification times, and a build that changed nothing does not spend minutes rasterising
several million facets again. Pass ``force=True`` to rebuild anyway.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from axqua.core import surfaces as surf

log = logging.getLogger("axqua")


@dataclass
class SurfaceArtifacts:
    """What the stage wrote, in the order the pipeline needs them."""

    dem: Path | None = None
    roi: Path | None = None
    liquid_boundaries: Path | None = None
    structures: Path | None = None
    roughness_zones: Path | None = None
    roughness_table: Path | None = None
    reports: list[dict] = field(default_factory=list)
    skipped: bool = False

    def paths(self) -> list[Path]:
        return [p for p in (self.dem, self.roi, self.liquid_boundaries,
                            self.structures, self.roughness_zones,
                            self.roughness_table) if p is not None]


def is_stale(cfg) -> bool:
    """Whether the artifacts are missing or older than any STL part."""
    outputs = [cfg.preprocessing_path(cfg.surfaces.dem_name),
               cfg.preprocessing_path(cfg.surfaces.roi_name)]
    if cfg.surfaces.by_role("inflow") or cfg.surfaces.by_role("outflow"):
        outputs.append(cfg.preprocessing_path(cfg.surfaces.boundaries_name))
    if cfg.surfaces.by_role("wall"):
        outputs.append(cfg.preprocessing_path(cfg.surfaces.structures_name))
    if any(not p.exists() for p in outputs):
        return True
    newest_input = max(Path(p.file).stat().st_mtime for p in cfg.surfaces.parts)
    return newest_input > min(p.stat().st_mtime for p in outputs)


def run(cfg, *, force: bool = False) -> SurfaceArtifacts:
    """Build the DEM, ROI, boundary lines, structures and roughness zones from CAD."""
    if not cfg.surfaces.active:
        return SurfaceArtifacts(skipped=True)
    cfg.surfaces.validate()
    if not force and not is_stale(cfg):
        log.info("surfaces: artifacts are newer than the CAD parts, skipping "
                 "(use --force to rebuild)")
        return SurfaceArtifacts(
            dem=cfg.preprocessing_path(cfg.surfaces.dem_name),
            roi=cfg.preprocessing_path(cfg.surfaces.roi_name), skipped=True)

    block = cfg.surfaces
    transform = block.transform()
    out_dir = Path(cfg.preprocessing_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = SurfaceArtifacts()

    loaded: dict[str, list[tuple]] = {role: [] for role in ("bed", "wall", "inflow",
                                                            "outflow", "roi")}
    for part in block.parts:
        if part.role == "ignore":
            log.info("surfaces: ignoring %s", part.label())
            continue
        surface = surf.read_stl(part.file, name=part.label(),
                                max_triangles=block.max_triangles)
        if not transform.is_identity:
            surface = surface.transformed(transform)
        artifacts.reports.append(surf.log_report(
            surf.report(surface, bed_max_slope_deg=block.bed_max_slope_deg,
                        min_slope_deg=block.wall_min_slope_deg)))
        loaded[part.role].append((part, surface))

    bounds = _common_bounds([s for parts in loaded.values() for _, s in parts])
    artifacts.dem = _write_dem(cfg, loaded["bed"], bounds, out_dir)
    artifacts.roi = _write_roi(cfg, loaded, bounds, out_dir)
    # walls first: where they are decides where a boundary line may be placed
    if loaded["wall"]:
        artifacts.structures = _write_structures(cfg, loaded["wall"], out_dir)
    if loaded["inflow"] or loaded["outflow"]:
        artifacts.liquid_boundaries = _write_liquid_boundaries(
            cfg, loaded, out_dir, roi=_read_polygon(artifacts.roi),
            dem=artifacts.dem, walls=_wall_union(artifacts.structures))
    if block.has_roughness_zones:
        zones, table = _write_roughness(cfg, loaded["bed"], bounds, out_dir)
        artifacts.roughness_zones, artifacts.roughness_table = zones, table

    for path in artifacts.paths():
        log.info("surfaces: wrote %s", path)
    return artifacts


def _common_bounds(surfaces_list) -> tuple[float, float, float, float]:
    """One grid for every part, so their rasters can be combined cell by cell."""
    if not surfaces_list:
        raise ValueError("no CAD parts to build from")
    boxes = np.array([[s.bounds[0], s.bounds[1], s.bounds[3], s.bounds[4]]
                      for s in surfaces_list])
    return (float(boxes[:, 0].min()), float(boxes[:, 1].min()),
            float(boxes[:, 2].max()), float(boxes[:, 3].max()))


def _bed_grid(cfg, bed_parts, bounds):
    """The merged bed height field of every part that plays the bed role."""
    block = cfg.surfaces
    grid = None
    transform = None
    for part, surface in bed_parts:
        # every near-horizontal facet, not only the upward-facing ones: 'max' takes the
        # top surface of a closed solid regardless of which way its normals were wound,
        # and a part exported inside-out would otherwise contribute nothing at all
        horizontal = surf.split_by_slope(
            surface, bed_max_slope_deg=block.bed_max_slope_deg,
            min_slope_deg=block.wall_min_slope_deg)["horizontal"]
        one, transform = surf.rasterize(surface.select(horizontal), block.resolution,
                                        mode="max", bounds=bounds)
        grid = one if grid is None else np.where(
            (one != -9999.0) & ((grid == -9999.0) | (one > grid)), one, grid)
    if grid is None:
        raise ValueError("surfaces: no part has role: bed")
    return grid, transform


def _write_dem(cfg, bed_parts, bounds, out_dir: Path) -> Path:
    grid, transform = _bed_grid(cfg, bed_parts, bounds)
    covered = float((grid != -9999.0).mean())
    log.info("surfaces: DEM %d x %d at %.3f m, %.1f%% of the bounding box covered",
             grid.shape[1], grid.shape[0], cfg.surfaces.resolution, covered * 100.0)
    if covered < 0.5:
        log.warning(
            "surfaces: only %.0f%% of the DEM's bounding box carries bed elevations. "
            "The gaps become nodata and the mesher will interpolate across them - "
            "check that every bed part is declared and that the parts overlap in plan.",
            covered * 100.0)
    return surf.write_dem(grid, transform, out_dir / cfg.surfaces.dem_name,
                          crs=_crs(cfg))


def _write_roi(cfg, loaded, bounds, out_dir: Path) -> Path:
    import geopandas as gpd

    if loaded["roi"]:
        polygons = []
        for _, surface in loaded["roi"]:
            grid, transform = surf.rasterize(surface, cfg.surfaces.resolution,
                                             bounds=bounds)
            polygons.append(surf.coverage_polygon(grid, transform,
                                                  simplify=_roi_simplify(cfg)))
        from shapely.ops import unary_union

        polygon = unary_union(polygons)
    else:
        grid, transform = _bed_grid(cfg, loaded["bed"], bounds)
        polygon = surf.coverage_polygon(grid, transform, simplify=_roi_simplify(cfg))
    # the ROI has to be one closed polygon: the mesher walks a single boundary
    polygon = _largest_part(polygon)
    path = out_dir / cfg.surfaces.roi_name
    gpd.GeoDataFrame({"name": [cfg.name]}, geometry=[polygon],
                     crs=_crs(cfg)).to_file(path, driver="GPKG")
    return path


def _roi_simplify(cfg) -> float:
    """Simplification tolerance for the domain outline.

    The outline comes from a raster mask, so it is a staircase of cell-sized steps. A
    mesher handed that produces slivers and zero-area cells along every step, because
    the boundary segments are finer than the cells it is being asked to build. At least
    two cells of tolerance turns the staircase into a line; a configured value wins,
    but is raised to that floor with a notice rather than quietly kept.
    """
    floor = 2.0 * cfg.surfaces.resolution
    wanted = cfg.surfaces.simplify
    if wanted is None:
        return floor
    if wanted < floor:
        log.info("surfaces: raising the outline simplification from %.3f to %.3f m "
                 "(two raster cells) - a finer tolerance keeps the raster staircase, "
                 "which meshes into slivers", wanted, floor)
        return floor
    return float(wanted)


def _largest_part(polygon):
    """The biggest piece of a possibly-multipart footprint, with its holes closed."""
    from shapely.geometry import Polygon

    if polygon.geom_type == "MultiPolygon":
        parts = sorted(polygon.geoms, key=lambda g: g.area, reverse=True)
        dropped = sum(g.area for g in parts[1:])
        if dropped > 0:
            log.warning(
                "surfaces: the CAD footprint falls into %d disconnected pieces; "
                "keeping the largest (%.1f m2) and dropping %.1f m2. A domain has to "
                "be one polygon - if the dropped pieces matter, they belong in a "
                "separate case.", len(parts), parts[0].area, dropped)
        polygon = parts[0]
    return Polygon(polygon.exterior)


def _write_liquid_boundaries(cfg, loaded, out_dir: Path, roi=None, dem=None,
                             walls=None) -> Path:
    import geopandas as gpd

    rows, geoms = [], []
    for role in ("inflow", "outflow"):
        for index, (part, surface) in enumerate(loaded[role], start=1):
            line = surf.patch_line(surface, simplify=cfg.surfaces.simplify)
            if roi is not None:
                line = _snap_to_domain(line, roi, part.label(), dem=dem, walls=walls)
            geoms.append(line)
            rows.append({"Name": part.label(), "Type (inflow/outflow)": role})
    path = out_dir / cfg.surfaces.boundaries_name
    gpd.GeoDataFrame(rows, geometry=geoms, crs=_crs(cfg)).to_file(path, driver="GPKG")
    if not any(r["Type (inflow/outflow)"] == "inflow" for r in rows):
        log.warning("surfaces: no part has role: inflow - the case has no upstream "
                    "boundary and the build will fail at the .cli stage")
    if not any(r["Type (inflow/outflow)"] == "outflow" for r in rows):
        log.warning("surfaces: no part has role: outflow - water would have nowhere "
                    "to leave")
    return path


def _wall_union(path):
    """The wall footprints as one geometry, or None when the case has no walls."""
    if path is None or not Path(path).exists():
        return None
    import geopandas as gpd
    from shapely.ops import unary_union

    frame = gpd.read_file(path)
    return unary_union(frame.geometry.values) if len(frame) else None


def _read_polygon(path):
    import geopandas as gpd

    return gpd.read_file(path).geometry.iloc[0]


def _snap_to_domain(line, roi, label: str, dem=None, walls=None, search: float = 6.0):
    """Put a boundary line on the domain edge, at the invert, keeping its width.

    A CFD inlet or outlet patch is a lid across the flow, and it need not sit exactly
    where the *wetted* domain ends: this fish pass has its inlet plate 2.5 m upstream of
    where the bed parts stop. A line floating off the edge matches no contour node, so
    the boundary silently disappears and the case is built with no inflow at all.

    Projecting onto the *nearest* stretch of the outline is not enough either. Near the
    end of a channel the outline runs across walls as well as bed, and a discharge
    prescribed over a segment whose bed stands a metre above its neighbours piles water
    onto the wall instead of into the channel - which shows up as a metres-deep spike at
    the boundary and nowhere else. So the line is placed at the **lowest** stretch of
    outline within a few metres of the patch: the invert, which is where the opening is.
    """
    from shapely.geometry import LineString

    exterior = roi.exterior
    offset = line.distance(exterior)
    if offset < 1e-6:
        return line
    midpoint = line.interpolate(0.5, normalized=True)
    nearest = exterior.project(midpoint)
    half = max(line.length / 2.0, 1e-3)

    best, best_bed, blocked = None, None, 0
    if dem is not None:
        sampler = _BedSampler(dem)

        for shift in np.arange(-search, search + 1e-9, 0.1):
            centre = nearest + shift
            if not (half <= centre <= exterior.length - half):
                continue
            candidate = _substring(exterior, centre - half, centre + half)
            # The wall is burned into the bed later, at the mesh stage, so a boundary
            # node sitting on one is a node whose bed stands a metre above its
            # neighbours - and prescribing a discharge over it piles water onto the
            # crest. The opening itself is *between* walls, though, so rejecting every
            # stretch that touches one would reject the inlet as well: clip instead,
            # and keep the widest open gap.
            if walls is not None:
                candidate = _widest_open_part(candidate, walls)
                if candidate is None or candidate.length < 0.2 * line.length:
                    blocked += 1
                    continue
            points = [candidate.interpolate(f, normalized=True)
                      for f in np.linspace(0.0, 1.0, 9)]
            level = sampler.mean([p.x for p in points], [p.y for p in points])
            if level is None:          # entirely off the bed: not a boundary position
                continue
            if best_bed is None or level < best_bed:
                best, best_bed = candidate, level
    if blocked:
        log.info("surfaces: %s - %d candidate positions rejected as mostly wall",
                 label, blocked)
    if best is None:
        best = _substring(exterior, max(nearest - half, 0.0),
                          min(nearest + half, exterior.length))
    log.info("surfaces: %s sits %.2f m off the domain edge; placed on it at the "
             "invert (%.2f m wide%s)", label, offset, best.length,
             f", mean bed {best_bed:.2f} m" if best_bed is not None else "")
    return LineString(best.coords)


class _BedSampler:
    """Bed elevation at points, with nodata reported rather than raised.

    :func:`axqua.core.raster.sample_raster_at` raises when *every* sampled point falls
    on nodata, which is right for a mesh - a mesh off the DEM is a broken case - but
    wrong here: searching for the lowest stretch of outline deliberately probes
    positions that may lie past the end of the bed, and one of those must skip, not
    abort the whole stage.
    """

    def __init__(self, path):
        import rasterio

        with rasterio.open(Path(path)) as src:
            self._band = src.read(1, masked=True).astype(float).filled(np.nan)
            self._transform = ~src.transform
            self._shape = self._band.shape
            self._nodata = src.nodata

    def mean(self, xs, ys) -> float | None:
        cols, rows = self._transform * (np.asarray(xs, float), np.asarray(ys, float))
        rows = np.floor(rows).astype(int)
        cols = np.floor(cols).astype(int)
        inside = ((rows >= 0) & (rows < self._shape[0])
                  & (cols >= 0) & (cols < self._shape[1]))
        if not inside.any():
            return None
        values = self._band[rows[inside], cols[inside]]
        if self._nodata is not None:
            values = np.where(values == self._nodata, np.nan, values)
        values = values[np.isfinite(values)]
        return float(values.mean()) if values.size else None


def _widest_open_part(line, walls):
    """The longest piece of *line* that no wall footprint covers."""
    from shapely.geometry import LineString

    open_part = line.difference(walls)
    pieces = [g for g in getattr(open_part, "geoms", [open_part])
              if isinstance(g, LineString) and not g.is_empty]
    return max(pieces, key=lambda g: g.length) if pieces else None


def _substring(line, start: float, end: float):
    """The piece of *line* between two distances along it."""
    from shapely.geometry import LineString
    from shapely.ops import substring

    piece = substring(line, start, end)
    return piece if piece.length > 0 else LineString(
        [line.interpolate(start), line.interpolate(min(end + 1e-3, line.length))])


def _write_structures(cfg, wall_parts, out_dir: Path) -> Path:
    import geopandas as gpd

    rows, geoms = [], []
    for part, surface in wall_parts:
        footprints = surf.wall_footprints(
            surface, min_slope_deg=cfg.surfaces.wall_min_slope_deg,
            resolution=cfg.surfaces.resolution, simplify=cfg.surfaces.simplify,
            min_area=cfg.surfaces.min_wall_area)
        log.info("surfaces: %s gives %d wall footprint(s)", part.label(),
                 len(footprints))
        for index, (polygon, crest) in enumerate(footprints, start=1):
            geoms.append(polygon)
            rows.append({"Name": f"{part.label()}-{index}", "Type": "wall",
                         "Mode": "solid", "Crest (m)": round(crest, 4)})
    path = out_dir / cfg.surfaces.structures_name
    gpd.GeoDataFrame(rows, geometry=geoms, crs=_crs(cfg)).to_file(path, driver="GPKG")
    return path


def _write_roughness(cfg, bed_parts, bounds, out_dir: Path) -> tuple[Path, Path]:
    import geopandas as gpd
    import pandas as pd

    rows, geoms, table = [], [], {}
    for part, surface in bed_parts:
        if part.zone_id is None:
            continue
        grid, transform = surf.rasterize(surface, cfg.surfaces.resolution,
                                         bounds=bounds)
        polygon = surf.coverage_polygon(grid, transform, simplify=cfg.surfaces.simplify)
        geoms.append(polygon)
        rows.append({"Zone ID": int(part.zone_id), "Name": part.label()})
        table[int(part.zone_id)] = float(part.ks)

    zones_path = out_dir / cfg.surfaces.roughness_zones_name
    gpd.GeoDataFrame(rows, geometry=geoms, crs=_crs(cfg)).to_file(zones_path,
                                                                  driver="GPKG")
    table_path = out_dir / cfg.surfaces.roughness_table_name
    pd.DataFrame(sorted(table.items()), columns=["zone_id", "ks"]).to_csv(
        table_path, index=False)
    return zones_path, table_path


def _crs(cfg):
    """The project CRS, or None when the case runs in local CAD coordinates.

    A case whose transform is the identity has not been georeferenced, and stamping a
    UTM code onto metres that start at the corner of a drawing would be a lie that QGIS
    would then act on. Local coordinates are a supported way to run: the model is the
    same, only its place on the map is missing.
    """
    if cfg.surfaces.transform().is_identity:
        log.info("surfaces: no georeferencing transform, writing artifacts in local "
                 "CAD coordinates without a CRS")
        return None
    return f"EPSG:{cfg.crs_epsg}"
