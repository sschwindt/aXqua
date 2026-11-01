"""A river through the catchment: the liquid-boundary lines of a hydrology case.

A catchment rarely drains into nothing. The interesting case is often a river that already carries a discharge when it enters the model and takes up the runoff, and the sediment, of the slopes beside it. In aXqua that river is two lines of the liquid-boundary layer: an **inflow** line where it enters, which carries its discharge (column ``Target flow``, or ``boundaries.prescribed_flowrate``, or the hydrograph ``boundaries.inflow``), and an **outflow** line where it leaves, which carries an outlet of its own (column ``Outlet``, :func:`axqua.core.boundaries.line_conditions`), usually ``normal_depth``. The other outflow lines keep the outlet of the case, for example ``overfall`` along the edges of a field. The river is optional: a catchment without it is the ordinary hydrology case.

:func:`lines` lists the lines of the layer; :func:`write_lines` writes a copy of the layer with the discharge and the outlet of the lines changed, which is what the tab *River* of the data wizard calls. The copy goes into ``user-sources/hydrology``, like every file the wizard writes, and the layer the user drew stays as it is.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from axqua.core.boundaries import (HYDROLOGY_OUTLETS, _flow_column, _is_internal,
                                   _normalise_kind, _outlet_column, _type_column,
                                   normalise_condition, parse_discharge)

#: The columns a written layer carries for the river, where it had none.
FLOW_COLUMN = "Target flow"
OUTLET_COLUMN = "Outlet"
TYPE_COLUMN = "Type (inflow/outflow)"

FILE_NAME = "liquid-boundaries.gpkg"


def _frame(cfg):
    from axqua.core.geodata import dataset

    if cfg.boundaries.liquid_boundaries is None:
        raise ValueError("the case has no liquid boundaries (boundaries.liquid_boundaries)"
                         ": draw the outflow lines of the catchment, and the inflow and "
                         "outflow line of a river, as lines in one layer first")
    return dataset(cfg).liquid_boundaries()


def lines(cfg) -> list[dict]:
    """Every line of the liquid-boundary layer: ``{row, kind, length, discharge, outlet}``, in the order of the layer. ``kind`` is inflow, outflow or internal; ``outlet`` the line's own outlet (empty: the case's)."""
    gdf = _frame(cfg)
    type_col, flow_col, outlet_col = _type_column(gdf), _flow_column(gdf), _outlet_column(gdf)
    out = []
    for row, (_, feature) in enumerate(gdf.iterrows()):
        tag = feature[type_col] if type_col else "inflow"
        kind = "internal" if _is_internal(tag) else _normalise_kind(tag)
        out.append({
            "row": row, "kind": kind, "type": str(tag),
            "length": round(float(feature.geometry.length), 2),
            "discharge": parse_discharge(feature[flow_col]) if flow_col else None,
            "outlet": (normalise_condition(feature[outlet_col]) or "") if outlet_col else "",
        })
    return out


def write_lines(cfg, changes: list[dict], out_dir: Path) -> Path:
    """A copy of the liquid-boundary layer with *changes* applied, in *out_dir*.

    Each change names a ``row`` of :func:`lines` and any of ``kind`` (inflow or outflow), ``discharge`` (m3/s, an inflow line; None clears it) and ``outlet`` (an outflow line: one of the hydrology outlets, or '' for the case's). A layer that is the copy already is written over.
    """
    gdf = _frame(cfg).copy()
    type_col = _type_column(gdf)
    if type_col is None:
        type_col = TYPE_COLUMN
        gdf[type_col] = "inflow"
    flow_col = _flow_column(gdf) or FLOW_COLUMN
    outlet_col = _outlet_column(gdf) or OUTLET_COLUMN
    for column in (flow_col, outlet_col):
        if column not in gdf.columns:
            gdf[column] = None
    # the loader has made the discharge column numbers already (Dataset.liquid_boundaries); a column it did not take for one (new, or empty) is converted here
    if not pd.api.types.is_numeric_dtype(gdf[flow_col]):
        gdf[flow_col] = pd.to_numeric(gdf[flow_col].map(parse_discharge), errors="coerce")
    gdf[flow_col] = gdf[flow_col].astype("float64")
    gdf[outlet_col] = gdf[outlet_col].astype("object")
    for change in changes:
        row = int(change["row"])
        if not 0 <= row < len(gdf):
            raise ValueError(f"the layer has no line {row}")
        index = gdf.index[row]
        if "kind" in change:
            kind = str(change["kind"]).strip().lower()
            if kind not in ("inflow", "outflow"):
                raise ValueError(f"line {row}: the type is inflow or outflow, not {kind!r}")
            gdf.at[index, type_col] = kind
        if "discharge" in change:
            value = parse_discharge(change["discharge"])
            if value is not None and value < 0:
                raise ValueError(f"line {row}: a discharge cannot be negative")
            gdf.at[index, flow_col] = value
        if "outlet" in change:
            outlet = normalise_condition(change["outlet"])
            if outlet is not None and outlet not in HYDROLOGY_OUTLETS:
                raise ValueError(f"line {row}: the outlet is one of "
                                 f"{', '.join(HYDROLOGY_OUTLETS)}, not {change['outlet']!r}")
            gdf.at[index, outlet_col] = outlet
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / FILE_NAME
    gdf.to_file(path, driver="GPKG")
    return path
