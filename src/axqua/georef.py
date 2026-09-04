"""Deriving the transform that puts CAD-local geometry on the map.

An STL carries no coordinate system - the format has no field for one. The drawing the
STL was exported from usually does, because a construction drawing is set out in the
national grid. So the transform is recoverable by matching the two: the same structure,
drawn twice, once in metres of the map and once in whatever the CAD file used.

What can honestly be recovered, and what cannot
-----------------------------------------------
Matching two axis-aligned bounding boxes gives **scale** and **translation** reliably,
and **rotation** only up to the ambiguity of a rectangle - a footprint rotated by 90
degrees has the same bounding box with its sides swapped, and one rotated by 180 has
exactly the same box. This module therefore *reports candidates* and asks for a
decision rather than picking one: it prints both boxes, the size ratio, the aspect
agreement and the rotations that would align them, and leaves the choice to someone who
can look at the result in QGIS. Silently choosing would produce a model that is
plausible, wrong, and hard to catch later.

The honest fallback is the identity: run the case in the drawing's own coordinates. The
model is physically identical and only its position on a map is missing, which matters
for a fish pass far less than it would for a river reach.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from axqua.core.surfaces import Transform, read_stl

log = logging.getLogger("axqua")

#: scales a CAD drawing plausibly uses, as (factor, what it means)
COMMON_SCALES = ((1.0, "metres"), (0.001, "millimetres"), (0.01, "centimetres"),
                 (0.3048, "feet"))


@dataclass
class Match:
    """A proposed georeferencing, with everything needed to judge it."""

    transform: Transform
    cad_bounds: tuple
    map_bounds: tuple
    scale_ratio: float
    aspect_cad: float
    aspect_map: float
    rotation_candidates: list[float] = field(default_factory=list)
    verdict: str = "unavailable"
    message: str = ""
    recommendation: str = ""

    def as_yaml(self) -> str:
        """The ``surfaces`` keys this match implies, ready to paste into a config."""
        t = self.transform
        return ("surfaces:\n"
                f"  scale: {t.scale:.9g}\n"
                f"  rotation_deg: {t.rotation_deg:.6g}\n"
                f"  dx: {t.dx:.4f}\n"
                f"  dy: {t.dy:.4f}\n"
                f"  dz: {t.dz:.4f}\n")


def read_dxf_bounds(path: Path, *, layers: list[str] | None = None):
    """Bounding box of a DXF drawing, in its own (map) coordinates.

    GDAL's DXF driver reads the entities as ordinary features, so this needs no CAD
    library. ``layers`` filters by the ``Layer`` attribute, which is how a drawing full
    of dimension lines, hatches and title blocks is reduced to the structure itself -
    without it the box is the box of the sheet, not of the building.
    """
    import geopandas as gpd

    frame = gpd.read_file(path)
    if frame.empty:
        raise ValueError(f"{Path(path).name} holds no readable entities")
    if layers:
        wanted = {name.lower() for name in layers}
        column = next((c for c in frame.columns if c.lower() == "layer"), None)
        if column is None:
            raise ValueError(f"{Path(path).name} has no Layer attribute to filter on")
        frame = frame[frame[column].str.lower().isin(wanted)]
        if frame.empty:
            raise ValueError(f"no entities on layer(s) {sorted(wanted)}")
    xmin, ymin, xmax, ymax = frame.total_bounds
    return float(xmin), float(ymin), float(xmax), float(ymax)


def dxf_layers(path: Path) -> list[tuple[str, int]]:
    """Every layer of a DXF with its feature count, for choosing what to match on."""
    import geopandas as gpd

    frame = gpd.read_file(path)
    column = next((c for c in frame.columns if c.lower() == "layer"), None)
    if column is None:
        return []
    counts = frame[column].value_counts()
    return [(str(name), int(n)) for name, n in counts.items()]


def match(stl: Path, dxf: Path, *, layers: list[str] | None = None,
          rotation_deg: float | None = None, dz: float = 0.0) -> Match:
    """Propose a transform placing *stl* where *dxf* says the structure is.

    The scale comes from the ratio of the two footprints' diagonals, snapped to a
    common CAD unit when it is within 2 % of one - a drawing is in millimetres or it is
    not, and 0.00098 is the former measured through a bounding box that includes a
    kerb. The rotation is not solved for: the candidates that could align the boxes are
    reported, and *rotation_deg* selects one. The translation then follows from the box
    centres, which is exact once scale and rotation are fixed.
    """
    surface = read_stl(stl)
    xmin, ymin, _, xmax, ymax, _ = surface.bounds
    cad = (xmin, ymin, xmax, ymax)
    map_box = read_dxf_bounds(dxf, layers=layers)

    cad_size = (cad[2] - cad[0], cad[3] - cad[1])
    map_size = (map_box[2] - map_box[0], map_box[3] - map_box[1])
    cad_diag = math.hypot(*cad_size)
    map_diag = math.hypot(*map_size)
    if cad_diag == 0 or map_diag == 0:
        raise ValueError("one of the two footprints is degenerate")

    raw_scale = map_diag / cad_diag
    scale, unit = _snap_scale(raw_scale)
    aspect_cad = cad_size[0] / cad_size[1] if cad_size[1] else math.inf
    aspect_map = map_size[0] / map_size[1] if map_size[1] else math.inf

    # a rectangle is its own image under 180 degrees, and under 90 with its sides
    # swapped, so these are the only ones a bounding box can distinguish
    upright = abs(aspect_cad - aspect_map) / max(aspect_map, 1e-9) < 0.05
    swapped = abs(aspect_cad - 1.0 / aspect_map) / max(1.0 / aspect_map, 1e-9) < 0.05
    candidates = ([0.0, 180.0] if upright else []) + ([90.0, 270.0] if swapped else [])
    chosen = rotation_deg if rotation_deg is not None else (
        candidates[0] if candidates else 0.0)

    # translation from the box centres, after scale and rotation
    centre_cad = np.array([[(cad[0] + cad[2]) / 2.0, (cad[1] + cad[3]) / 2.0, 0.0]])
    moved = Transform(scale=scale, rotation_deg=chosen).apply(centre_cad)[0]
    dx = (map_box[0] + map_box[2]) / 2.0 - moved[0]
    dy = (map_box[1] + map_box[3]) / 2.0 - moved[1]

    result = Match(
        transform=Transform(scale=scale, rotation_deg=chosen, dx=dx, dy=dy, dz=dz),
        cad_bounds=cad, map_bounds=map_box, scale_ratio=raw_scale,
        aspect_cad=aspect_cad, aspect_map=aspect_map, rotation_candidates=candidates)

    if not candidates:
        result.verdict = "shape_mismatch"
        result.message = (
            f"the two footprints are not the same shape: the CAD part is "
            f"{aspect_cad:.3f} wide-to-tall and the drawing {aspect_map:.3f}. They are "
            f"probably not the same extent - filter the drawing to the structure's own "
            f"layer(s) with --layer, or check that the STL is the whole assembly.")
        result.recommendation = ("run without a transform, in local coordinates, until "
                                 "the two extents can be made to agree")
    elif abs(raw_scale / scale - 1.0) > 0.02:
        result.verdict = "scale_unclear"
        result.message = (
            f"the size ratio is {raw_scale:.6g}, which is not within 2% of any usual "
            f"CAD unit (nearest: {scale:g}, {unit}). Either the two extents cover "
            f"different things, or the drawing is at a plot scale rather than 1:1.")
        result.recommendation = "check the layer filter before accepting this scale"
    else:
        result.verdict = "match"
        rotations = ", ".join(f"{r:g}" for r in candidates)
        result.message = (
            f"consistent: the drawing is {scale:g} x the CAD part ({unit}), the "
            f"footprints agree in shape to within 5%, and rotation candidates are "
            f"[{rotations}] degrees - {chosen:g} selected.")
        result.recommendation = (
            "check the result in QGIS against a basemap before trusting it; a bounding "
            "box cannot tell 0 from 180 degrees" if len(candidates) > 1 else
            "check the result in QGIS against a basemap")
    return result


def _snap_scale(raw: float) -> tuple[float, str]:
    """The usual CAD unit nearest *raw*, and its name."""
    best, unit = min(COMMON_SCALES, key=lambda s: abs(math.log(raw / s[0])))
    return best, unit


def parse_control_point(text: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """``"12.5,40.0:4473100.2,5332090.7"`` -> the CAD pair and the map pair."""
    try:
        cad_text, map_text = text.split(":")
        cad = tuple(float(v) for v in cad_text.split(","))
        world = tuple(float(v) for v in map_text.split(","))
    except ValueError as error:
        raise ValueError(
            f"cannot read control point {text!r}; expected CADX,CADY:MAPX,MAPY") from error
    if len(cad) != 2 or len(world) != 2:
        raise ValueError(f"control point {text!r} needs exactly two numbers per side")
    return cad, world


def report_control_points(stl: Path, points: list[str], *, dz: float = 0.0) -> int:
    """Fit and report a transform from control points, and say how well it fits.

    The residuals are the whole point of reporting rather than applying: two points
    always fit exactly, so a third is what tells you whether the pairs were identified
    correctly. A metre of residual on a fish pass means a point was mis-picked.
    """
    pairs = [parse_control_point(text) for text in points]
    cad = [p[0] for p in pairs]
    world = [p[1] for p in pairs]
    transform = Transform.from_control_points(cad, world, dz=dz)
    residuals = transform.residuals(cad, world)

    result = Match(transform=transform, cad_bounds=(), map_bounds=(),
                   scale_ratio=transform.scale, aspect_cad=0.0, aspect_map=0.0,
                   verdict="match")
    log.info("GEOREFERENCE from %d control point(s): scale %.6g, rotation %.4f deg",
             len(pairs), transform.scale, transform.rotation_deg)
    for index, (pair, residual) in enumerate(zip(pairs, residuals), start=1):
        log.info("  point %d: CAD %s -> map %s, residual %.4f m",
                 index, pair[0], pair[1], residual)
    if len(pairs) == 2:
        log.warning("two points fit exactly by construction, so these residuals prove "
                    "nothing. Add a third, independent point to check the fit.")
    elif residuals.max() > 0.05:
        log.warning("largest residual %.3f m - larger than a slot width. Check that "
                    "each pair really names the same feature.", residuals.max())
    surface = read_stl(stl)
    moved = surface.transformed(transform)
    log.info("  %s lands at x %.2f..%.2f, y %.2f..%.2f", Path(stl).name,
             moved.bounds[0], moved.bounds[3], moved.bounds[1], moved.bounds[4])
    log.info("paste into the case config:\n\n%s", result.as_yaml())
    return 0


def log_match(result: Match, logger_obj=None) -> Match:
    """Log a :func:`match` for a human to accept or reject; returns it unchanged."""
    logger = logger_obj or log
    write = logger.warning if result.verdict != "match" else logger.info
    write("GEOREFERENCE: %s", result.message)
    logger.info("  CAD footprint: %.3f .. %.3f x %.3f .. %.3f (%.3f x %.3f)",
                result.cad_bounds[0], result.cad_bounds[2], result.cad_bounds[1],
                result.cad_bounds[3], result.cad_bounds[2] - result.cad_bounds[0],
                result.cad_bounds[3] - result.cad_bounds[1])
    logger.info("  drawing:       %.3f .. %.3f x %.3f .. %.3f (%.3f x %.3f)",
                result.map_bounds[0], result.map_bounds[2], result.map_bounds[1],
                result.map_bounds[3], result.map_bounds[2] - result.map_bounds[0],
                result.map_bounds[3] - result.map_bounds[1])
    if result.recommendation:
        logger.info("  -> %s", result.recommendation)
    if result.verdict == "match":
        logger.info("paste into the case config:\n\n%s", result.as_yaml())
    return result
