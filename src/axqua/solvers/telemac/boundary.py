"""Stage 3 - boundary conditions (.cli).

Classifies each contour node (in IPOBO order) against the user's liquid-boundary
lines and writes the TELEMAC boundary-conditions file. Codes:

* solid wall                       -> ``2 2 2``  (every non-liquid outer bound)
* inflow  (prescribed flowrate)    -> ``4 5 5``  (depth free, velocity from Q)
* outflow, prescribed elevation    -> ``5 4 4``  (``outflow_condition: stage_discharge``
                                                  [default] or ``elevation``)
* outflow, free / Neumann          -> ``4 4 4``  (``outflow_condition: free``)

The liquid-boundary lines come from ``boundaries.liquid_boundaries`` (a line layer
whose ``Type (inflow/outflow)`` field tags each line ``inflow`` or ``outflow``;
there may be several of each). They **must coincide with the outer bounds of the
mesh zones** so that contour nodes fall on them. Liquid boundaries are numbered
exactly the way TELEMAC's ``FRONT2`` does - per contour loop, starting at the
south-westernmost node and walking the contour with the domain on its left,
merging a run that wraps the loop start (see :func:`_front2_runs`) - so the
PRESCRIBED FLOWRATES / PRESCRIBED ELEVATIONS line up with the boundary numbering
TELEMAC derives from the geometry.

For numerical stability the total inflow-node count should be within ~10% of the
total outflow-node count; otherwise a stability-risk warning is logged (raise the
mesh resolution near the boundaries or adjust the line lengths to rebalance).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from axqua.config import Config, outlet_level
# The liquid-boundary readers are shared with the OpenFOAM backend, so they
# live in the core; re-exported here because this module published them.
from axqua.core.boundaries import (  # noqa: F401
    HYDROLOGY_OUTLETS, _flow_column, _is_internal, _normalise_kind, _type_column,
    line_condition, line_conditions, liquid_line_details, liquid_lines, parse_discharge,
)
from axqua.solvers.telemac.mesh import Mesh, _anisotropic_enabled

log = logging.getLogger("axqua")

WALL = (2, 2, 2)
# prescribed-discharge inflow: LIHBOR=4 (depth FREE, computed), LIUBOR/LIVBOR=5
# (velocity prescribed from the imposed Q + profile). LIHBOR must be 4, not 5:
# a 5 prescribes the depth from PRESCRIBED ELEVATIONS, which is 0 for an inflow,
# so the inflow would be forced to a zero water surface (dry) and TELEMAC's
# DEBIMP aborts ("PROBLEM ON BOUNDARY ... CHECK THE WATER DEPTHS").
INFLOW = (4, 5, 5)
OUTFLOW_FREE = (4, 4, 4)   # Neumann / free outflow: nothing prescribed
OUTFLOW_ELEV = (5, 4, 4)   # prescribed downstream water level

# inflow/outflow node SPACING differing by more than this fraction triggers a
# stability-risk warning (a resolution mismatch matters; unequal raw counts on
# boundaries of different physical width do not)
SPACING_BALANCE_TOL = 0.35
# a liquid boundary with fewer nodes than this is flagged as under-resolved
MIN_BOUNDARY_NODES = 4
# cap on the exterior vertices of an internal source-region polygon: TELEMAC's
# region reader checks each node against the vertex list (INPOLY), and the .cas
# must carry MAXIMUM NUMBER OF POINTS FOR SOURCES REGIONS >= this
MAX_REGION_VERTICES = 16


@dataclass
class LiquidBoundary:
    index: int        # 1-based, in contour-appearance order
    kind: str         # "inflow" | "outflow"
    n_nodes: int
    # per-line prescribed Q [m3/s]; None -> split the total reach discharge across inflows by node share
    discharge: float | None = None
    # the outflow condition of this line (the Outlet column of a hydrology case); None -> the case's condition (core.boundaries.line_condition)
    condition: str | None = None
    # the contour nodes of the boundary (0-based), known right after a build; not serialized (a hydrology outlet reads its own section through them)
    nodes: tuple = field(default=(), compare=False, repr=False)


@dataclass
class InternalSourceRegion:
    """An internal source/sink REGION for a losing-gaining reach.

    A 2D depth-averaged model has no subsurface, so hyporheic / underflow exchange
    where the surface flow *loses* water in one place and *gains* it back downstream
    is represented as TELEMAC **source regions** (``SOURCE REGIONS DATA FILE``): a
    withdrawal (``discharge < 0``) over the losing polygon and an injection
    (``discharge > 0``) over the gaining polygon. TELEMAC spreads each region's Q
    uniformly over the mesh nodes inside the polygon (Q/area as a depth rate,
    ``prosou.f``), so the exchange acts as a gentle distributed flux instead of
    hammering the few nodes under the line - a sink concentrated on single nodes
    dries them, spikes velocities and collapses the CFL-adaptive time step
    (TELEMAC has **no depth guard on negative sources**).
    """
    name: str
    discharge: float   # signed m3/s: < 0 withdraws (losing), > 0 injects (gaining)
    polygon: object    # shapely Polygon (region outline, EPSG per config)
    area: float        # polygon area [m2]
    n_nodes: int = 0   # mesh nodes inside (0 when counted without a mesh)
    # thickness [m] of the porous layer the exchange passes through, from the
    # percolation patch's depth field. Only used by the conductivity (Green-Ampt)
    # exchange mode; None when no patch applies.
    porous_depth: float | None = None


def dump_liquid_boundaries(liquids: list["LiquidBoundary"], path: str | Path) -> Path:
    """Serialize the FRONT2-ordered liquid boundaries to JSON.

    Written during the build (:mod:`axqua.solvers.telemac.pipeline`) so the standalone unsteady /
    3D scripts recover the exact TELEMAC boundary numbering (index/kind/node count,
    and any per-line discharge) without rebuilding the mesh - all
    :func:`load_liquid_boundaries` needs to write the hydrograph liquid-boundaries
    file and the prescribed-value arrays.
    """
    import json

    path = Path(path)
    path.write_text(json.dumps(
        [{"index": lb.index, "kind": lb.kind, "n_nodes": lb.n_nodes,
          "discharge": lb.discharge, "condition": lb.condition} for lb in liquids],
        indent=2) + "\n")
    return path


def load_liquid_boundaries(path: str | Path) -> list["LiquidBoundary"]:
    """Load the liquid boundaries dumped by :func:`dump_liquid_boundaries`."""
    import json

    data = json.loads(Path(path).read_text())
    return [LiquidBoundary(index=int(d["index"]), kind=str(d["kind"]),
                           n_nodes=int(d["n_nodes"]),
                           discharge=(None if d.get("discharge") is None
                                      else float(d["discharge"])),
                           condition=d.get("condition") or None) for d in data]


def _outflow_code(cfg: Config, line=None) -> tuple[int, int, int]:
    """An outflow without a level (free, Neumann) -> 4 4 4; every outflow with one -> 5 4 4. *line* is the liquid boundary, whose own condition holds where it has one."""
    condition = line_condition(cfg, line) if line is not None else cfg.outflow_condition
    return OUTFLOW_FREE if outlet_level(condition) is None else OUTFLOW_ELEV






def _internal_sign(name: str) -> float:
    """Sign of an internal exchange: -1 loses (withdraws), +1 gains (injects)."""
    s = name.lower()
    if any(k in s for k in ("lose", "loss", "sink", "withdraw")):
        return -1.0
    if any(k in s for k in ("gain", "source", "inject")):
        return 1.0
    return -1.0 if "out" in s else 1.0   # surface sense: internal OUTflow loses




def _simplify_region(polygon, max_vertices: int = MAX_REGION_VERTICES):
    """Reduce *polygon* to at most *max_vertices* exterior vertices.

    TELEMAC reads the region outline as a plain vertex list (INPOLY), so a light
    outline is enough; iteratively coarser Douglas-Peucker until it fits. Returns
    (polygon, exterior_coords_without_closing_duplicate).
    """
    tol = 0.05
    poly = polygon
    coords = list(poly.exterior.coords)[:-1]
    while len(coords) > max_vertices and tol < 1e3:
        poly = polygon.simplify(tol, preserve_topology=True)
        coords = list(poly.exterior.coords)[:-1]
        tol *= 2.0
    return poly, coords


def _count_nodes_inside(polygon, mesh: "Mesh") -> int:
    """Number of mesh nodes strictly inside *polygon* (bbox-prefiltered)."""
    import numpy as np
    import shapely

    x = np.asarray(mesh.x, dtype=float)
    y = np.asarray(mesh.y, dtype=float)
    minx, miny, maxx, maxy = polygon.bounds
    cand = (x >= minx) & (x <= maxx) & (y >= miny) & (y <= maxy)
    if not cand.any():
        return 0
    return int(shapely.contains_xy(polygon, x[cand], y[cand]).sum())


def _percolation_patches(cfg: Config) -> list[dict]:
    """Percolation patch polygons: ``{name, geom, porous_depth}`` (empty if unset)."""
    import geopandas as gpd

    if cfg.percolation.zone is None:
        return []
    gdf = gpd.read_file(cfg.percolation.zone)
    if gdf.crs and gdf.crs.to_epsg() != cfg.crs_epsg:
        gdf = gdf.to_crs(epsg=cfg.crs_epsg)
    name_col = next((c for c in gdf.columns
                     if c.lower() == cfg.percolation.name_field.lower()), None)
    depth_col = next((c for c in gdf.columns
                      if c.lower() == cfg.percolation.depth_field.lower()), None)
    patches = []
    for i, row in gdf.iterrows():
        patches.append({
            "name": str(row[name_col]) if name_col else f"patch-{i + 1}",
            "geom": row.geometry,
            "porous_depth": (float(row[depth_col]) if depth_col is not None
                             and row[depth_col] is not None else None),
        })
    return patches


def exchange_regions(cfg: Config, mesh: "Mesh | None" = None
                     ) -> list["InternalSourceRegion"]:
    """The internal exchange a build applies: the ``int-*`` lines, if the case asks.

    The lines are a property of the boundary layer, and a layer is shared between
    cases. They used to be applied whenever the layer held them, so a case that said
    nothing about an exchange got one anyway, in its least stable form: a fixed-rate
    source region on a thin strip. On the isar-2025 reach that ends a run at its first
    time step. An exchange now needs the case to ask for it, with a ``gain_lose`` block
    or with ``boundaries.internal_sources: true`` for the fixed-rate regions. Without
    either, the lines are reported and left out.
    """
    if cfg.gain_lose.active or cfg.boundaries.internal_sources:
        return load_internal_source_regions(cfg, mesh)
    try:
        # without the mesh: nothing is checked that only matters when they are applied
        ignored = load_internal_source_regions(cfg)
    except Exception as exc:                        # noqa: BLE001 - they are unused
        log.debug("internal lines not read (%s); they are not applied anyway", exc)
        return []
    if ignored:
        names = ", ".join(f"{region.name} ({region.discharge:+g} m3/s)"
                          for region in ignored)
        log.warning(
            "the liquid-boundary layer holds %d internal exchange line(s): %s. This "
            "case has no gain_lose block, so NO exchange is modeled. Add "
            "'gain_lose: {enabled: true, zone: <porous body>}' to model it, or "
            "'boundaries.internal_sources: true' for fixed-rate source regions.",
            len(ignored), names)
    return []


def load_internal_source_regions(cfg: Config,
                                 mesh: "Mesh | None" = None
                                 ) -> list["InternalSourceRegion"]:
    """Internal losing/gaining lines -> TELEMAC source regions (see
    :class:`InternalSourceRegion`). A line whose type tag starts with 'int'
    becomes a withdrawal (``lose``) or injection (``gain``) of its flow-column
    discharge, spread over a polygon region. Empty when the layer has no internal
    lines.

    The region polygon is the line buffered to a strip of
    ``boundaries.internal_source_region_width`` - except in ``percolation.mode:
    region``, where a **losing** line whose line intersects a percolation patch
    uses the whole patch polygon instead (same -Q over a far larger area, so the
    per-node depth rate drops by orders of magnitude).

    With a *mesh* the nodes inside each region are counted; a region containing no
    node raises (TELEMAC aborts on it at run time).
    """
    from axqua.core.geodata import dataset

    # the layer every reader shares: reprojected once, its discharges as numbers
    gdf = dataset(cfg).liquid_boundaries()
    type_col = _type_column(gdf)
    if type_col is None:
        return []
    flow_col = _flow_column(gdf)
    name = Path(cfg.boundaries.liquid_boundaries).name
    # In percolation mode the LOSING exchange may spread over the patch polygon
    # (mode 'region': TELEMAC source region; mode 'fortran': USER_RAIN domain).
    # With losing_region='line' it instead stays on the buffered line strip - use
    # that when the modeled water surface does not actually cover the patch, or
    # the withdrawal concentrates on a shrinking wet remnant (see the isar-2025
    # test-approaches.md: the patch holds only ~88 m2 of wet area at steady state,
    # against ~183 m2 for a 12 m strip along the line).
    # all patches are read whenever percolation is active (the conductivity mode
    # needs their porous depth even when the region stays on the line); `patches`
    # is the subset actually used AS the losing region.
    all_patches = (_percolation_patches(cfg)
                   if cfg.gain_lose.active else [])
    patches = (all_patches
               if cfg.percolation.losing_region == "patch" else [])
    width = float(cfg.boundaries.internal_source_region_width)
    regions: list[InternalSourceRegion] = []
    for _, row in gdf.iterrows():
        raw = str(row[type_col])
        if not _is_internal(raw):
            continue
        if flow_col is None:
            raise ValueError(
                f"liquid_boundaries {name!r} has an internal source line {raw!r} but "
                "no discharge/flow column to size it (add a 'Target flow' column with "
                "the exchange in m3/s)."
            )
        value = parse_discharge(row[flow_col])
        if value is None:                       # text, or an empty cell (NaN)
            raise ValueError(
                f"internal source line {raw!r} has no numeric {flow_col!r} value."
            )
        magnitude = abs(value)
        signed = _internal_sign(raw) * magnitude
        geom = row.geometry
        # default region: the line buffered to a thin strip (flat caps)
        polygon = geom.buffer(width / 2.0, cap_style=2)
        region_name = raw
        porous = None
        if signed < 0:
            # the porous layer thickness belongs to the LOSING side; take it from
            # whichever patch the line touches (needed by the conductivity mode
            # even when the region itself stays on the line)
            near = next((p for p in all_patches if p["geom"].intersects(geom)), None)
            if near is not None:
                porous = near["porous_depth"]
            if patches:
                hit = next((p for p in patches if p["geom"].intersects(geom)), None)
                if hit is not None:
                    polygon = hit["geom"]
                    region_name = f"{raw} ({hit['name']})"
                    log.info(
                        "  percolation region mode: losing line %r spread over patch %r"
                        " (%.0f m2%s)", raw, hit["name"], polygon.area,
                        "" if hit["porous_depth"] is None
                        else f", porous depth {hit['porous_depth']:g} m")
        polygon, _ = _simplify_region(polygon)
        n_nodes = 0
        if mesh is not None:
            n_nodes = _count_nodes_inside(polygon, mesh)
            if n_nodes == 0:
                raise ValueError(
                    f"internal source region {region_name!r} contains no mesh node "
                    "- TELEMAC aborts on an empty source region. Widen "
                    "boundaries.internal_source_region_width or check the geometry."
                )
        regions.append(InternalSourceRegion(
            name=region_name, discharge=signed, polygon=polygon,
            area=float(polygon.area), n_nodes=n_nodes, porous_depth=porous))
    return regions


def _match_tolerance(cfg: Config, mesh: Mesh | None = None) -> float:
    """Distance below which a contour node is taken to lie on a liquid line.

    Measured from the **mesh that was actually built** wherever possible: two median
    boundary edges, which is what "on the line" means for a node. The configured sizes
    are only a fallback for callers without a mesh, and a poor one - taking the largest
    of them (``breakline_size`` defaults to 3 m, deliberately coarser than a fine
    ``default_size``) yields a tolerance of meters on a centimeter mesh, which quietly
    swallows meters of wall either side of a short inflow line and prescribes the
    discharge across all of it.
    """
    scale = cfg.mesh.size_scale
    if _anisotropic_enabled(cfg):
        configured = max(cfg.mesh.floodplain_size, cfg.mesh.channel_size) * scale * 2.0
    else:
        configured = max(cfg.mesh.default_size, cfg.mesh.breakline_size) * scale * 1.5
    if mesh is None:
        return configured
    edge = _median_boundary_edge(mesh)
    if edge <= 0:
        return configured
    # Clamped by the mesh, never widened by it: the configured sizes are what a case
    # has always been matched with, and only the case where they are far coarser than
    # the mesh is a bug. Here breakline_size (3 m by default, deliberately coarse) beat
    # a 5 cm default_size and produced a 4.5 m tolerance on a 5 cm mesh, which swallowed
    # meters of wall either side of a 0.6 m inlet.
    return min(configured, 2.0 * edge)


def _solid_footprint(cfg: Config):
    """Union of the case's solid structure footprints, or None when it has none."""
    try:
        from axqua.core.structures import load_structures, solid_footprint

        return solid_footprint(load_structures(cfg))
    except Exception as error:                       # report-only: never block a build
        log.debug("no solid footprints available for boundary classification: %s", error)
        return None


def _on_solid(cfg: Config, mesh: Mesh):
    """Nodes a solid structure stands on, by the element rule the bed raise uses.

    Testing a node's own center is not enough. A wall thinner than an element raises
    the bed of every node of the elements it crosses, so a node can end up standing on
    a crest while its center falls outside the footprint. Keeping such a node as a
    liquid boundary prescribes a discharge or a stage on top of the wall.
    """
    import numpy as np

    try:
        from axqua.core.structures import (blanking_area, load_structures,
                                           solid_mask)

        structures = load_structures(cfg)
    except Exception as error:                       # report-only: never block a build
        log.debug("no solid footprints available for boundary classification: %s", error)
        return None
    if not structures:
        return None
    xy = np.column_stack([mesh.x, mesh.y])
    return solid_mask(structures, xy, triangles=mesh.triangles,
                      min_area=blanking_area(cfg))


def _median_boundary_edge(mesh: Mesh) -> float:
    """Median distance between consecutive contour nodes of the mesh."""
    import numpy as np

    nodes = np.asarray(mesh.boundary_nodes)
    if nodes.size < 3:
        return 0.0
    x = np.asarray(mesh.x)[nodes]
    y = np.asarray(mesh.y)[nodes]
    steps = np.hypot(np.diff(x), np.diff(y))
    steps = steps[steps > 0]
    return float(np.median(steps)) if steps.size else 0.0


def _contour_length(node_ids: list[int], mesh: Mesh) -> float:
    """Length along the contour of a run of consecutive boundary node ids."""
    import numpy as np

    if len(node_ids) < 2:
        return 0.0
    xs = np.asarray([mesh.x[i] for i in node_ids], dtype=float)
    ys = np.asarray([mesh.y[i] for i in node_ids], dtype=float)
    return float(np.sum(np.hypot(np.diff(xs), np.diff(ys))))


def _warn_node_balance(liquids: list[LiquidBoundary], mesh: Mesh,
                       node_map: dict[int, list[int]], *,
                       hydrology: bool = False) -> None:
    """Stability check on liquid-boundary *resolution* (node spacing), not raw counts.

    Comparing total inflow vs total outflow node counts mislabels a legitimately
    narrower outflow - or an inflow split across several lines - as a risk. What
    actually matters numerically is that each boundary is adequately resolved and
    that inflow and outflow share a comparable node **spacing**, so this compares
    mean node spacing (and flags any under-resolved boundary) instead.
    """
    inflow = [lb for lb in liquids if lb.kind == "inflow"]
    outflow = [lb for lb in liquids if lb.kind == "outflow"]
    n_in = sum(lb.n_nodes for lb in inflow)
    n_out = sum(lb.n_nodes for lb in outflow)
    if hydrology and n_in == 0:
        # a catchment is fed by its rain: an outlet alone is the normal case
        if n_out == 0:
            log.warning("a hydrology case needs at least one outflow line: without "
                        "one, the rain has nowhere to go")
        return
    if n_in == 0 or n_out == 0:
        log.warning(
            "STABILITY RISK: liquid boundaries have %d inflow and %d outflow "
            "nodes - a model needs at least one of each. Check that the "
            "liquid-boundary lines are tagged and coincide with the mesh bounds.",
            n_in, n_out,
        )
        return

    def _spacing(group) -> tuple[int, float, float]:
        nodes = sum(lb.n_nodes for lb in group)
        length = sum(_contour_length(node_map.get(lb.index, []), mesh) for lb in group)
        segments = sum(max(lb.n_nodes - 1, 1) for lb in group)
        return nodes, length, (length / segments if segments else 0.0)

    ni, li, si = _spacing(inflow)
    no, lo, so = _spacing(outflow)

    thin = [lb for lb in liquids if lb.n_nodes < MIN_BOUNDARY_NODES]
    if thin:
        lb = min(thin, key=lambda b: b.n_nodes)
        log.warning(
            "STABILITY RISK: %s boundary %d has only %d node(s) (< %d) - too coarse "
            "to resolve the flux profile. Refine the mesh near it or lengthen the line.",
            lb.kind, lb.index, lb.n_nodes, MIN_BOUNDARY_NODES,
        )

    spread = abs(si - so) / max(si, so) if max(si, so) > 0 else 0.0
    if spread > SPACING_BALANCE_TOL:
        log.warning(
            "STABILITY RISK: inflow and outflow are resolved at different node "
            "spacings (inflow ~%.2f m over %d nodes, outflow ~%.2f m over %d nodes; "
            "%.0f%% apart, > %.0f%%). Refine the coarser side so both carry a "
            "comparable node density.",
            si, ni, so, no, spread * 100, SPACING_BALANCE_TOL * 100,
        )
    elif not thin:
        log.info(
            "liquid-boundary resolution OK: inflow %d nodes over %.1f m (~%.2f m "
            "spacing) vs outflow %d nodes over %.1f m (~%.2f m spacing)",
            ni, li, si, no, lo, so,
        )


def _front2_runs(loop_nodes, loop_kinds: list[str], x, y) -> list[tuple[str, list[int]]]:
    """Liquid/wall runs of one closed contour loop, in TELEMAC ``FRONT2`` order.

    TELEMAC numbers liquid boundaries (``bief/front2.f``) by starting at each
    loop's **south-westernmost** node (min ``x+y``, ties broken by min ``y``) and
    walking the contour with the domain on its left (the outer loop is traversed
    counter-clockwise, as ``KP1BOR`` follows the CCW element node order), counting
    a new liquid boundary at every transition into a liquid segment and merging a
    run that wraps the loop start. We replay exactly that here so our liquid-
    boundary numbering matches the order TELEMAC assigns - otherwise the
    PRESCRIBED FLOWRATES / ELEVATIONS land on the wrong boundary (inflow Q and
    outflow H swapped) or a single boundary that straddles the loop start is split
    into two.

    FRONT2 types the *segment* that leaves the start node, not the node, and a
    segment with one wall node is a wall ("solid over liquid"). So when the start
    node is the **last** node of a liquid run, TELEMAC begins on a wall and meets
    that run last, not first. Found on the Inn KB15 reach (2026-10-20): the south-
    westernmost node closed the inflow line, TELEMAC made the outflow boundary 1,
    and the discharge went to the outflow while the inflow received none.
    """
    import numpy as np

    n = len(loop_nodes)
    if n == 0:
        return []
    nodes = np.asarray(loop_nodes)
    xs = np.asarray(x)[nodes]
    ys = np.asarray(y)[nodes]

    # orient domain-on-left (outer loop CCW, signed area > 0); islands are all
    # wall, so their orientation does not affect liquid numbering.
    area2 = float(np.sum(xs * np.roll(ys, -1) - np.roll(xs, -1) * ys))
    order = np.arange(n)[::-1] if area2 < 0 else np.arange(n)
    oxs, oys = xs[order], ys[order]
    okinds = [loop_kinds[i] for i in order]
    onodes = nodes[order]

    # south-westernmost start (FRONT2's IDEP): min x+y, ties broken by min y
    key = oxs + oys
    eps = (key.max() - key.min()) * 1e-4
    cand = np.flatnonzero(np.abs(key - key.min()) <= eps)
    start = int(cand[np.argmin(oys[cand])])
    liquid = ("inflow", "outflow")
    if n > 1 and okinds[start] in liquid and okinds[(start + 1) % n] not in liquid:
        # the first segment is a wall: the start node closes the liquid run that
        # FRONT2 meets last, so the walk effectively begins one node further on
        start = (start + 1) % n
    rkinds = [okinds[(start + j) % n] for j in range(n)]
    rnodes = [int(onodes[(start + j) % n]) for j in range(n)]

    runs: list[list] = []
    for k, nd in zip(rkinds, rnodes):
        if runs and runs[-1][0] == k:
            runs[-1][1].append(nd)
        else:
            runs.append([k, [nd]])
    # the loop is cyclic: a run that wraps the start is one boundary, not two
    # (the wrapping tail precedes the head along the contour)
    if len(runs) > 1 and runs[0][0] == runs[-1][0]:
        runs[0][1] = runs[-1][1] + runs[0][1]
        runs.pop()
    return [(k, nd) for k, nd in runs]


def _number_liquid_boundaries(
        mesh: Mesh, kinds: list[str]) -> tuple[list[LiquidBoundary], dict[int, list[int]]]:
    """Number the liquid boundaries per contour loop in TELEMAC ``FRONT2`` order.

    Returns the boundaries and a map ``index -> contour node ids`` (used to match
    each boundary to its source line for per-line discharge assignment).
    """
    import numpy as np

    bn = np.asarray(mesh.boundary_nodes)
    loops = getattr(mesh, "boundary_loops", None)
    loop_lengths = ([int(bn.size)] if loops is None or len(loops) == 0
                    else [int(n) for n in loops])

    liquids: list[LiquidBoundary] = []
    node_map: dict[int, list[int]] = {}
    off = 0
    for length in loop_lengths:
        loop_nodes = bn[off:off + length]
        loop_kinds = kinds[off:off + length]
        off += length
        for kind, node_ids in _front2_runs(loop_nodes, loop_kinds, mesh.x, mesh.y):
            if kind in ("inflow", "outflow"):
                idx = len(liquids) + 1
                liquids.append(LiquidBoundary(idx, kind, len(node_ids),
                                              nodes=tuple(int(n) for n in node_ids)))
                node_map[idx] = node_ids
    return liquids, node_map


def _assign_line_discharges(cfg: Config, mesh: Mesh, liquids: list[LiquidBoundary],
                            node_map: dict[int, list[int]]) -> None:
    """Attach each inflow boundary its own prescribed Q from the liquid-boundary
    layer's flow column, by matching the boundary's node centroid to the nearest
    inflow line. No-op (leaves ``discharge=None``) when the layer has no flow column,
    so a single total inflow Q is split by node share (the historical behavior).
    """
    import numpy as np
    from shapely.geometry import Point

    details = liquid_line_details(cfg)
    if details is None:
        return
    inflow_lines = [d for d in details
                    if d["kind"] == "inflow" and d["discharge"] is not None]
    if not inflow_lines:
        return
    for lb in liquids:
        if lb.kind != "inflow":
            continue
        nds = node_map.get(lb.index, [])
        if not nds:
            continue
        centroid = Point(float(np.mean([mesh.x[i] for i in nds])),
                         float(np.mean([mesh.y[i] for i in nds])))
        nearest = min(inflow_lines, key=lambda d: d["geom"].distance(centroid))
        lb.discharge = float(nearest["discharge"])

    total = sum(lb.discharge for lb in liquids
                if lb.kind == "inflow" and lb.discharge is not None)
    cfg_q = cfg.boundaries.prescribed_flowrate
    shares = ", ".join(f"{lb.discharge:.4f}" for lb in liquids if lb.kind == "inflow")
    if cfg_q is not None and abs(total - float(cfg_q)) > 0.01 * max(total, float(cfg_q)):
        log.warning("per-line inflow discharges (%s) sum to %.4f m3/s but "
                    "boundaries.prescribed_flowrate is %.4f m3/s - reconcile them so "
                    "the total reach discharge is consistent.", shares, total, float(cfg_q))
    else:
        log.info("per-line inflow discharges: %s m3/s (sum %.4f)", shares, total)


def _split_outlets(cfg: Config, mesh: Mesh, kinds: list[str]) -> int:
    """Separate adjacent outflow lines whose outlets differ by one wall node.

    TELEMAC starts a new liquid boundary only where the codes of two neighboring nodes differ (``front2.f``), so an ``overfall`` line that touches a ``normal_depth`` line (both ``5 4 4``) would be one boundary with one level. A segment with a wall node is a wall for TELEMAC ("solid over liquid"), so wall nodes between them make two boundaries, in TELEMAC and in :func:`_front2_runs` alike. They are two: ``front2.f`` stops at a single solid node between two liquid ones ("SOLID POINT BETWEEN TWO LIQUID POINTS", found on the first real run), and at a single liquid node between two solid ones, so a line left with one node becomes wall too. Returns the number of nodes set to wall.
    """
    import numpy as np
    from shapely.geometry import Point

    lines = line_conditions(cfg) if cfg.boundaries.liquid_boundaries is not None else None
    if not lines or not any(d["condition"] for d in lines):
        return 0
    bn = np.asarray(mesh.boundary_nodes)
    condition = {}
    for rank, node in enumerate(bn):
        if kinds[rank] == "outflow":
            p = Point(float(mesh.x[node]), float(mesh.y[node]))
            nearest = min(lines, key=lambda d: d["geom"].distance(p))
            condition[rank] = nearest["condition"] or cfg.outflow_condition
    loops = getattr(mesh, "boundary_loops", None)
    lengths = [int(bn.size)] if loops is None or len(loops) == 0 else [int(n) for n in loops]
    walled = 0
    off = 0
    for length in lengths:
        ranks = [off + j for j in range(length)]
        off += length
        # the runs of outflow nodes with one outlet, in contour order (cyclic)
        position = {rank: j for j, rank in enumerate(ranks)}
        runs: list[list[int]] = []
        for j, rank in enumerate(ranks):
            key = condition.get(rank) if kinds[rank] == "outflow" else None
            if runs and key is not None and condition.get(runs[-1][-1]) == key \
                    and j > 0 and runs[-1][-1] == ranks[j - 1]:
                runs[-1].append(rank)
            elif key is not None:
                runs.append([rank])
        if len(runs) > 1 and runs[0][0] == ranks[0] and runs[-1][-1] == ranks[-1] \
                and condition[runs[0][0]] == condition[runs[-1][-1]]:
            runs[0] = runs.pop() + runs[0]
        # two runs that touch: the two nodes at the junction go to the longer one's expense, so that a short river line keeps its nodes
        for first, second in zip(runs, runs[1:] + runs[:1]):
            if first is second or ranks[(position[first[-1]] + 1) % length] != second[0]:
                continue
            if len(second) >= len(first):
                victims = second[:2]
            else:
                victims = first[-2:]
            for rank in victims:
                if kinds[rank] == "outflow":
                    kinds[rank] = "wall"
                    walled += 1
        # a liquid node alone between two wall nodes is an error of TELEMAC's as well
        for j in range(length):
            before, here, after = ranks[j - 1], ranks[j], ranks[(j + 1) % length]
            if kinds[here] != "wall" and kinds[before] == "wall" == kinds[after]:
                kinds[here] = "wall"
                walled += 1
    kept = {condition[r] for r in condition if kinds[r] == "outflow"}
    for line in lines:
        if line["condition"] and line["condition"] not in kept:
            log.warning("  the outflow line with the outlet %s has no node left between "
                        "its neighbors; refine the mesh there or lengthen the line",
                        line["condition"])
    if walled:
        log.info("  %d contour node(s) set to wall between outflow lines with different "
                 "outlets, so that TELEMAC numbers them as boundaries of their own", walled)
    return walled


def _assign_line_conditions(cfg: Config, mesh: Mesh, liquids: list[LiquidBoundary],
                            node_map: dict[int, list[int]]) -> None:
    """Give each outflow boundary of a hydrology case the condition of its line (the ``Outlet`` column), matched like the discharges: the nearest outflow line to the centroid of its nodes. No-op without the column; a river case ignores it."""
    import numpy as np
    from shapely.geometry import Point

    if cfg.boundaries.liquid_boundaries is None:
        return
    lines = line_conditions(cfg)
    if not lines or not any(d["condition"] for d in lines):
        return
    if not getattr(cfg, "hydrology_mode", False):
        log.warning("  the column Outlet of the liquid boundaries is read in the hydrology "
                    "mode only; this river case uses boundaries.outflow_condition for "
                    "every outflow line")
        return
    for lb in liquids:
        if lb.kind != "outflow":
            continue
        nds = node_map.get(lb.index, [])
        if not nds:
            continue
        centroid = Point(float(np.mean([mesh.x[i] for i in nds])),
                         float(np.mean([mesh.y[i] for i in nds])))
        nearest = min(lines, key=lambda d: d["geom"].distance(centroid))
        condition = nearest["condition"]
        if condition is None:
            continue
        if condition not in HYDROLOGY_OUTLETS:
            raise ValueError(
                f"the outflow line {lb.index} of {Path(cfg.boundaries.liquid_boundaries).name} "
                f"has the outlet {nearest['value']!r}; the column Outlet takes "
                f"{', '.join(HYDROLOGY_OUTLETS)}, or nothing for "
                f"hydrology.outlet_condition ({cfg.outflow_condition})")
        lb.condition = condition
    own = [f"{lb.index}: {lb.condition}" for lb in liquids if lb.condition]
    if own:
        log.info("  outflow lines with an outlet of their own: %s (the others: %s)",
                 ", ".join(own), cfg.outflow_condition)


def classify_nodes(cfg: Config, mesh: Mesh) -> tuple[list[str], list[LiquidBoundary]]:
    """Classify contour nodes; return per-node kind list and liquid boundaries.

    ``kinds`` stays in ``mesh.boundary_nodes`` order (so it drives the ``.cli`` row
    order, which must match the geometry's IPOBO). The returned ``LiquidBoundary``
    list is numbered the way TELEMAC's ``FRONT2`` does (see
    :func:`_front2_runs`), so the steering file's per-boundary prescribed values
    line up with the boundary numbering TELEMAC derives from the geometry.
    """
    from shapely.geometry import Point

    hydrology = getattr(cfg, "hydrology_mode", False)
    if hydrology and cfg.boundaries.liquid_boundaries is None:
        log.warning("  no liquid boundaries: a closed catchment, all its rain stays in "
                    "it (set boundaries.liquid_boundaries for an outlet)")
        return ["wall"] * len(mesh.boundary_nodes), []
    lines = liquid_lines(cfg)
    tol = _match_tolerance(cfg, mesh)
    log.info("  liquid-boundary matching tolerance %.3f m (two median contour edges)",
             tol)
    on_solid_node = _on_solid(cfg, mesh)

    kinds: list[str] = []
    on_solid = 0
    for node in mesh.boundary_nodes:
        p = Point(mesh.x[node], mesh.y[node])
        best_kind, best_dist = "wall", tol
        for kind, geom in lines.items():
            d = geom.distance(p)
            if d < best_dist:
                best_kind, best_dist = kind, d
        # A node standing inside a solid structure is not a way in or out, whatever it
        # is near: the structure is burned into the bed, so the node's bed sits a meter
        # or more above the opening beside it, and prescribing a discharge or a stage
        # there pours water onto the crest. It shows up as a single node meters deep at
        # the boundary while its neighbors run at several m/s.
        if best_kind != "wall" and on_solid_node is not None and on_solid_node[node]:
            best_kind = "wall"
            on_solid += 1
        kinds.append(best_kind)
    if on_solid:
        log.info("  %d contour node(s) excluded from the liquid boundaries: they stand "
                 "on a solid structure", on_solid)

    if hydrology:
        _split_outlets(cfg, mesh, kinds)
    liquids, node_map = _number_liquid_boundaries(mesh, kinds)
    if not liquids and hydrology:
        log.warning("  no contour node lies on a liquid-boundary line: the catchment "
                    "is closed and all its rain stays in it")
        return kinds, liquids
    if not liquids:
        raise ValueError(
            "No contour nodes matched the liquid-boundary lines. Check that the "
            "liquid_boundaries lines coincide with the mesh-zone outer bounds and "
            f"the matching tolerance (~{tol:.1f} m) suits the mesh resolution."
        )
    _assign_line_discharges(cfg, mesh, liquids, node_map)
    _assign_line_conditions(cfg, mesh, liquids, node_map)
    _warn_node_balance(liquids, mesh, node_map, hydrology=hydrology)
    return kinds, liquids


def write_cli(cfg: Config, mesh: Mesh) -> tuple[Path, list[LiquidBoundary]]:
    kinds, liquids = classify_nodes(cfg, mesh)
    code = {"wall": WALL, "inflow": INFLOW, "outflow": _outflow_code(cfg)}
    # an outflow line with an outlet of its own may differ from the case's code
    own_code = {int(n): _outflow_code(cfg, lb) for lb in liquids
                if lb.kind == "outflow" and lb.condition for n in lb.nodes}

    # The tracer code (LITBOR, GAIA's LICBOR) matters only where TELEMAC carries tracers, which here are GAIA's suspended classes: a prescribed concentration at an inflow (5), a free one at an outflow (4). With a wall (2) there, suspended sediment could not leave the model. Without tracers the column stays 2, as every case had it.
    tracer = ({"wall": 2, "inflow": 5, "outflow": 4}
              if cfg.morphodynamics.suspended_classes else {})
    rows = []
    for rank, (node, kind) in enumerate(zip(mesh.boundary_nodes, kinds), start=1):
        c = own_code.get(int(node), code[kind]) if kind == "outflow" else code[kind]
        n_global = int(node) + 1  # SELAFIN/TELEMAC 1-based
        comment = "" if kind == "wall" else kind
        rows.append(
            f"{c[0]} {c[1]} {c[2]}  0.000 0.000 0.000 0.000  {tracer.get(kind, 2)}  "
            "0.000 0.000 0.000  "
            f"{n_global:>10}  {rank:>10}   # {comment}"
        )
    path = cfg.model_path(cfg.boundary_cli)
    path.write_text("\n".join(rows) + "\n")
    return path, liquids


# Public since the OpenFOAM extension classifies its lateral faces against the very
# same lines that drive the .cli; the underscore spellings stay as aliases.
_load_liquid_lines = liquid_lines
_boundary_line_details = liquid_line_details


def liquid_boundary_hops(geometry: str | Path, cli: str | Path):
    """How many mesh nodes each node lies from the nearest liquid boundary.

    Returns ``(x, y, hops, kind)``: the node coordinates of *geometry* (double
    precision), the number of mesh edges on the shortest path from the node to a node
    of an inflow or outflow line of *cli* (0 on the line, -1 where no liquid boundary
    is reachable), and ``"inflow"`` or ``"outflow"`` for the boundary that is nearest.
    A node of the ``.cli`` is liquid when its depth code is not a wall (2); it is an
    inflow when it prescribes the velocity or the discharge (code 5 or 6).
    """
    from collections import deque

    import numpy as np

    from axqua.core import selafin

    geo = selafin.read_slf(geometry, frame=0)
    x, y = np.asarray(geo["x"], float), np.asarray(geo["y"], float)
    n = len(x)
    hops = np.full(n, -1, dtype=int)
    kind = np.full(n, "", dtype=object)
    queue: deque = deque()
    for line in Path(cli).read_text().splitlines():
        cols = line.split("#")[0].split()
        if len(cols) < 12:
            continue
        lih, liu, node = int(cols[0]), int(cols[1]), int(cols[11]) - 1
        if lih == 2 or not 0 <= node < n or hops[node] == 0:
            continue
        hops[node] = 0
        kind[node] = "inflow" if liu in (5, 6) else "outflow"
        queue.append(node)
    ikle = np.asarray(geo["ikle"], dtype=int)
    neighbors: list[set] = [set() for _ in range(n)]
    for a, b, c in ikle[:, :3]:
        neighbors[a].update((b, c))
        neighbors[b].update((a, c))
        neighbors[c].update((a, b))
    while queue:
        node = queue.popleft()
        for other in neighbors[node]:
            if hops[other] < 0:
                hops[other] = hops[node] + 1
                kind[other] = kind[node]
                queue.append(other)
    return x, y, hops, kind
