"""Reading the liquid-boundary lines: which line is an inflow, which an outflow.

The layer that says where water enters and leaves a reach is **shared**: TELEMAC turns
it into the ``.cli`` boundary codes, OpenFOAM turns it into ``inlet-N`` / ``outlet-N``
patches, and both must take flow in and out at exactly the same places or the two
models are not comparable. It lived in the TELEMAC ``.cli`` writer until the backends
were separated; nothing here knows about either solver.

The Inn dataset's ``Type (inflow/outlfow)`` typo is deliberately still matched - the
layer is field data, and refusing to read it over a spelling mistake would help
nobody.

What each outflow condition prescribes at the outflow lines (:data:`OUTLET_LEVELS`) is here as well, for the same reason: the case settings, the TELEMAC build and the hydrology build each ask it, and a list of condition names in each of them missed the ``overfall`` outlet twice when it was added.
"""

from __future__ import annotations

import logging
from pathlib import Path

#: What each outflow condition prescribes at the outflow lines, so that the code that writes or reads an outlet asks this one table instead of listing condition names: ``None`` no level (``free``, zero gradient of depth and velocity), ``"curve"`` a level TELEMAC takes from its own Z(Q) curve as the discharge changes (``normal_depth``), ``"fixed"`` the level ``boundaries.prescribed_elevation`` (``elevation``), ``"rating"`` the level of the rating curve ``boundaries.stage_discharge`` at the discharge of the case (``stage_discharge``), ``"mesh"`` a level below the lowest node, so that the lines hold no water (``overfall``).
OUTLET_LEVELS: dict[str, str | None] = {
    "free": None, "normal_depth": "curve", "elevation": "fixed",
    "stage_discharge": "rating", "overfall": "mesh"}
#: The outflow conditions of a river case (``boundaries.outflow_condition``), the default first.
RIVER_OUTLETS = ("elevation", "stage_discharge", "free")
#: The outflow conditions of a hydrology case (``hydrology.outlet_condition``), the default first.
HYDROLOGY_OUTLETS = ("normal_depth", "free", "elevation", "overfall")


def outlet_level(condition: str) -> str | None:
    """Where the level of an outflow line under *condition* comes from (:data:`OUTLET_LEVELS`); a name that is no outflow condition raises."""
    try:
        return OUTLET_LEVELS[condition]
    except KeyError:
        raise ValueError(f"{condition!r} is no outflow condition: a river case takes "
                         f"{', '.join(RIVER_OUTLETS)}, a hydrology case "
                         f"{', '.join(HYDROLOGY_OUTLETS)}") from None


log = logging.getLogger("axqua")


def _normalise_kind(value) -> str:
    """Map a free-text type tag to 'inflow' / 'outflow' (else the lowercased tag)."""
    s = str(value).strip().lower()
    if "out" in s:
        return "outflow"
    if "in" in s:
        return "inflow"
    return s


def _type_column(gdf) -> str | None:
    """Find the attribute holding the inflow/outflow tag.

    Matches a column whose name mentions type/kind/inflow/outflow/stringdef (so
    the Inn layer's ``Type (inflow/outflow)`` is picked up); falls back to the
    sole non-geometry column when there is exactly one.
    """
    cols = [c for c in gdf.columns if c != gdf.geometry.name]
    for c in cols:
        cl = c.lower()
        if any(k in cl for k in ("type", "kind", "inflow", "outflow", "stringdef")):
            return c
    return cols[0] if len(cols) == 1 else None


def _flow_column(gdf) -> str | None:
    """Find the attribute holding a per-line prescribed discharge (m3/s), if any.

    Matches a column whose name mentions flow/discharge/Q (so the Isar layer's
    ``Target flow`` is picked up), excluding the type column. Returns None when the
    layer carries no per-line discharge (then the total reach Q is split by node
    share, the historical single-inflow behavior).
    """
    for c in [c for c in gdf.columns if c != gdf.geometry.name]:
        cl = c.strip().lower()
        if any(k in cl for k in ("type", "kind", "stringdef")):
            continue
        if "discharge" in cl or "flow" in cl or cl in ("q", "q_m3s", "qm3s"):
            return c
    return None


def _is_internal(type_value) -> bool:
    """True for an internal source/sink line (its type tag starts with 'int')."""
    return str(type_value).strip().lower().startswith("int")


def liquid_lines(cfg):
    """Return dict kind -> shapely geometry (union of that kind's contour lines).

    Internal source/sink lines (type tag starting 'int', handled by
    :func:`load_internal_source_regions`) are skipped so they never pull a contour
    node into an inflow/outflow classification.
    """
    from shapely.ops import unary_union

    from axqua.core.geodata import dataset

    gdf = dataset(cfg).liquid_boundaries()
    type_col = _type_column(gdf)
    out: dict[str, list] = {}
    if type_col is None:
        log.warning("liquid_boundaries %s has no inflow/outflow type column; "
                    "treating every line as inflow",
                    Path(cfg.boundaries.liquid_boundaries).name)
        out["inflow"] = list(gdf.geometry.values)
        return {k: unary_union(v) for k, v in out.items()}
    for _, row in gdf.iterrows():
        if _is_internal(row[type_col]):
            continue
        kind = _normalise_kind(row[type_col])
        if kind not in ("inflow", "outflow"):
            raise ValueError(
                f"liquid_boundaries {Path(cfg.boundaries.liquid_boundaries).name!r}: "
                f"line tagged {row[type_col]!r} in column {type_col!r} is neither "
                "'inflow' nor 'outflow'"
            )
        out.setdefault(kind, []).append(row.geometry)
    return {k: unary_union(v) for k, v in out.items()}


def liquid_line_details(cfg) -> list[dict] | None:
    """Per non-internal liquid line: ``{kind, discharge, geom}`` (discharge from the
    flow column, else None). Returns None when the layer has no flow column at all -
    the signal to fall back to node-share discharge splitting.
    """
    from axqua.core.geodata import dataset

    gdf = dataset(cfg).liquid_boundaries()
    type_col = _type_column(gdf)
    flow_col = _flow_column(gdf)
    if type_col is None or flow_col is None:
        return None
    details: list[dict] = []
    for _, row in gdf.iterrows():
        if _is_internal(row[type_col]):
            continue
        kind = _normalise_kind(row[type_col])
        if kind not in ("inflow", "outflow"):
            continue
        try:
            q = float(row[flow_col])
        except (TypeError, ValueError):
            q = None
        details.append({"kind": kind, "discharge": q, "geom": row.geometry})
    return details

