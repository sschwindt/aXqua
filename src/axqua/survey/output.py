"""What a survey plan leaves on the disk: the layers of ``survey-plan.gpkg`` and one map per kind of survey.

``survey-plan.gpkg`` always holds the same layers (:data:`LAYERS`), in the coordinate system of the case. The layers ``access_points`` and ``walkable_areas`` belong to the user: aXqua creates them empty and never writes into them again. The other layers are the plan and are replaced by every plan; the layers of a kind of survey that is not planned are empty.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from axqua.survey.field import FlowField, SurveyError
from axqua.survey.settings import KIND_TITLES

GPKG_NAME = "survey-plan.gpkg"
ACCESS_LAYER = "access_points"
#: The polygons in which dry ground can be walked on; everywhere else a walk on land keeps to the bank strip along the wetted edge.
WALKABLE_LAYER = "walkable_areas"
#: The roles a point of ``access_points`` can have, with what each is for.
ROLES = {
    "access": "where the surveyors reach the river: the start and the end of a wading survey, the put-in of the kayaks, and the take-off point of the drone unless one is set",
    "leave": "where the kayaks are taken out of the river",
    "takeoff": "where the drone takes off and lands, if not at the access point",
}

# layer -> (geometry type, key of the plan, [(column, key of the row, type)])
_S, _I, _F = "str", "int", "float"
LAYERS: dict[str, tuple] = {
    "flowtracker_points": ("Point", [("label", "label", _S), ("sequence", "sequence", _I), ("surveyor", "surveyor", _I), ("day", "day", _I), ("stop", "order", _I), ("depth_m", "depth", _F), ("velocity_ms", "velocity", _F), ("n_depths", "n_depths", _I), ("heights", "heights", _S), ("heights_m", "heights_m", _S), ("zone", "zone", _I), ("serves", "serves", _S), ("start_time", "start", _S), ("end_time", "end", _S), ("minutes", "minutes", _F)]),
    "walk_paths": ("LineString", [("survey", "survey", _S), ("who", "who", _S), ("number", "number", _I), ("day", "day", _I), ("leg", "order", _I), ("kind", "kind", _S), ("from_point", "from", _S), ("to_point", "to", _S), ("start_time", "start", _S), ("minutes", "minutes", _F), ("length_m", "length", _F), ("in_water", "in_water", _F)]),
    "survey_events": ("Point", [("survey", "survey", _S), ("kind", "kind", _S), ("surveyor", "surveyor", _I), ("day", "day", _I), ("at_point", "at", _S), ("start_time", "start", _S), ("end_time", "end", _S), ("minutes", "minutes", _F)]),
    "drone_positions": ("Point", [("station", "station", _I), ("battery", "battery", _I), ("day", "day", _I), ("altitude_m", "altitude", _F), ("water_level", "water_level", _F), ("above_takeoff_m", "above_takeoff", _F), ("heading_deg", "heading", _F), ("flow_bearing", "flow_bearing", _F), ("frame_along_m", "frame_along", _F), ("frame_across_m", "frame_across", _F), ("gsd_cm", "gsd_cm", _F), ("velocity_ms", "velocity", _F), ("velocity_max", "velocity_max", _F), ("px_per_frame", "px_per_frame", _F), ("frame_step", "frame_step", _I), ("hover_s", "hover_s", _F), ("start_time", "start", _S), ("end_time", "end", _S), ("serves", "serves", _S)]),
    "drone_footprints": ("Polygon", [("station", "station", _I), ("battery", "battery", _I), ("day", "day", _I), ("altitude_m", "altitude", _F), ("heading_deg", "heading", _F), ("gsd_cm", "gsd_cm", _F), ("velocity_ms", "velocity", _F), ("frame_step", "frame_step", _I), ("start_time", "start", _S), ("serves", "serves", _S)]),
    "drone_route": ("LineString", [("leg", "order", _I), ("battery", "battery", _I), ("day", "day", _I), ("kind", "kind", _S), ("start_time", "start", _S), ("minutes", "minutes", _F), ("length_m", "length", _F)]),
    "kayak_tracks": ("LineString", [("float_no", "float", _I), ("round_no", "round", _I), ("kayaker", "kayaker", _I), ("day", "day", _I), ("discharge_share", "share_of_discharge", _F), ("length_m", "length", _F), ("minutes", "minutes", _F), ("drift_minutes", "drift_minutes", _F), ("start_time", "start", _S), ("end_time", "end", _S), ("positions", "fixes", _I), ("velocity_mean", "velocity_mean", _F), ("velocity_max", "velocity_max", _F), ("depth_min", "depth_min", _F), ("spacing_min_m", "spacing_min", _F), ("spacing_max_m", "spacing_max", _F), ("too_fast_m", "fast_m", _F), ("too_shallow_m", "shallow_m", _F), ("serves", "serves", _S)]),
    "kayak_avoid": ("MultiPolygon", [("area_m2", "area", _F), ("velocity_max", "velocity_max", _F)]),
    "temperature_points": ("Point", [("label", "label", _S), ("stop", "order", _I), ("method", "method", _S), ("water", "water", _S), ("depth_m", "depth", _F), ("velocity_ms", "velocity", _F), ("reach", "reach", _S), ("day", "day", _I), ("start_time", "start", _S), ("end_time", "end", _S)]),
    "temperature_patches": ("MultiPolygon", [("water", "water", _S), ("area_m2", "area", _F)]),
}


def _crs(epsg) -> str | None:
    return f"EPSG:{int(epsg)}" if epsg else None


def _layers_of(path: Path) -> list[str]:
    if not Path(path).is_file():
        return []
    import pyogrio

    return [str(name) for name, _ in pyogrio.list_layers(path)]


def ensure_user_layers(path: Path, epsg) -> bool:
    """Create ``survey-plan.gpkg`` with the empty layers ``access_points`` and ``walkable_areas`` as far as it lacks them. True when the layer of the access points was created."""
    import geopandas as gpd
    import pandas as pd

    path = Path(path)
    have = _layers_of(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    empty = lambda columns: gpd.GeoDataFrame({name: pd.Series([], dtype="str") for name in columns}, geometry=gpd.GeoSeries([], crs=_crs(epsg)), crs=_crs(epsg))  # noqa: E731
    if ACCESS_LAYER not in have:
        empty(("role", "name")).to_file(path, layer=ACCESS_LAYER, driver="GPKG", geometry_type="Point")
    if WALKABLE_LAYER not in have:               # also in the file of a plan from before the layer existed
        empty(("name",)).to_file(path, layer=WALKABLE_LAYER, driver="GPKG", geometry_type="Polygon")
    return ACCESS_LAYER not in have


def role_of(text) -> str:
    """The role a user wrote, forgivingly: an empty role is an access point."""
    word = "".join(ch for ch in str(text or "").lower() if ch.isalpha())
    if word in ("nan", "none", "null"):
        word = ""
    if word.startswith(("takeoff", "drone", "start", "launch")):
        return "takeoff"
    if word.startswith(("leave", "exit", "takeout", "out", "end", "pickup", "egress")):
        return "leave"
    if word == "" or word.startswith(("access", "putin", "in", "entry")):
        return "access"
    return ""


def read_access(path: Path, epsg) -> dict:
    """The points of ``access_points`` by role: ``{"access": (x, y) | None, "leave": ..., "takeoff": ..., "count", "notes"}`` in the coordinate system of the case."""
    out = {"access": None, "leave": None, "takeoff": None, "count": 0, "notes": [], "names": {}}
    if ACCESS_LAYER not in _layers_of(path):
        return out
    import geopandas as gpd

    frame = gpd.read_file(path, layer=ACCESS_LAYER)
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty]
    if frame.crs is not None and epsg and frame.crs.to_epsg() != int(epsg):
        frame = frame.to_crs(_crs(epsg))
    out["count"] = int(len(frame))
    seen: dict[str, int] = {}
    for _, row in frame.iterrows():
        point = row.geometry.representative_point() if row.geometry.geom_type != "Point" else row.geometry
        role = role_of(row.get("role"))
        if not role:
            out["notes"].append(f"a point of the layer {ACCESS_LAYER} has the role {row.get('role')!r}, which is none of {', '.join(ROLES)}: it is not used")
            continue
        seen[role] = seen.get(role, 0) + 1
        if out[role] is None:
            out[role] = (float(point.x), float(point.y))
            name = row.get("name")
            out["names"][role] = "" if name is None or str(name) in ("nan", "None") else str(name)
    for role, count in seen.items():
        if count > 1:
            out["notes"].append(f"the layer {ACCESS_LAYER} has {count} points with the role '{role}': the first is used")
    return out


def read_walkable(path: Path, epsg) -> dict:
    """The polygons of ``walkable_areas``: ``{"geometry": their union | None, "count", "area" [m2], "notes"}`` in the coordinate system of the case."""
    out = {"geometry": None, "count": 0, "area": 0.0, "notes": []}
    if WALKABLE_LAYER not in _layers_of(path):
        return out
    import geopandas as gpd
    import shapely

    frame = gpd.read_file(path, layer=WALKABLE_LAYER)
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty]
    if frame.crs is not None and epsg and frame.crs.to_epsg() != int(epsg):
        frame = frame.to_crs(_crs(epsg))
    polygons, other = [], 0
    for geometry in frame.geometry:
        if not geometry.is_valid:                # a polygon that crosses itself, drawn by hand
            geometry = shapely.make_valid(geometry)
        parts = [part for part in getattr(geometry, "geoms", [geometry]) if part.geom_type in ("Polygon", "MultiPolygon") and part.area > 0]
        if parts:
            polygons += parts
            out["count"] += 1
        else:
            other += 1
    if other:
        out["notes"].append(f"{other} feature{'s' if other > 1 else ''} of the layer {WALKABLE_LAYER} {'are' if other > 1 else 'is'} no polygon with an area: not used")
    if polygons:
        union = shapely.unary_union(polygons)
        out["geometry"], out["area"] = union, float(union.area)
    return out


def write_layers(path: Path, epsg, plans: dict) -> dict[str, int]:
    """Replace the plan layers of ``survey-plan.gpkg`` with those of *plans* (``kind -> plan``). Returns the number of features per layer."""
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import LineString, MultiPolygon, Point, Polygon

    ensure_user_layers(path, epsg)
    ground, air, kayak, temperature = (plans.get(kind) or {} for kind in ("ground", "airborne", "kayak", "temperature"))
    line = lambda row: LineString(row["coords"]) if len(row["coords"]) > 1 else LineString([row["coords"][0], row["coords"][0]])  # noqa: E731
    point = lambda row: Point(row["x"], row["y"])  # noqa: E731
    multi = lambda row: row["geometry"] if row["geometry"].geom_type == "MultiPolygon" else MultiPolygon([row["geometry"]])  # noqa: E731
    events = [dict(e, survey=e.get("survey", "ground")) for plan in (ground, air, kayak, temperature) for e in plan.get("events", [])]
    walks = [dict(p, survey="ground") for p in ground.get("paths", [])] + [dict(p, survey="temperature") for p in temperature.get("paths", [])]
    content = {
        "flowtracker_points": (ground.get("points", []), point),
        "walk_paths": (walks, line),
        "survey_events": (events, point),
        "drone_positions": (air.get("stations", []), point),
        "drone_footprints": (air.get("stations", []), lambda row: Polygon(row["polygon"])),
        "drone_route": (air.get("route", []), line),
        "kayak_tracks": (kayak.get("tracks", []), line),
        "kayak_avoid": (kayak.get("avoid", []), multi),
        "temperature_points": (temperature.get("points", []), point),
        "temperature_patches": (temperature.get("patches", []), multi),
    }
    kinds = {_S: "str", _I: "int64", _F: "float64"}
    empty = {_S: "", _I: 0, _F: float("nan")}
    counts = {}
    try:
        for layer, (geometry_type, columns) in LAYERS.items():
            rows, make = content[layer]
            data = {column: pd.Series([_cell(row.get(key), kind, empty[kind]) for row in rows], dtype=kinds[kind]) for column, key, kind in columns}
            frame = gpd.GeoDataFrame(data, geometry=gpd.GeoSeries([make(row) for row in rows], crs=_crs(epsg)), crs=_crs(epsg))
            frame.to_file(path, layer=layer, driver="GPKG", geometry_type=geometry_type)
            counts[layer] = len(rows)
    except Exception as exc:  # noqa: BLE001 - a locked or read-only GeoPackage, whatever the driver calls it
        raise SurveyError(f"the plan could not be written to {Path(path).name}: {exc}", subject=str(path), remedy="Save the edits of the layer access_points and close other programs that write to the file, then plan again.") from exc
    return counts


def _cell(value, kind: str, default):
    if value is None:
        return default
    try:
        return {"str": str, "int": int, "float": float}[kind](value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# maps
# --------------------------------------------------------------------------- #

INK = "#1f1f1e"
MUTED = "#6b6a66"
HALO = "#ffffff"
#: Who walks, floats or flies: colors that stand on blue water, told apart by the labels and line styles too.
WHO = ("#c8501e", "#5b3fc4", "#0a6b2d", "#a3176b", "#7a5200", "#33414e")
DANGER = "#c62828"
WALKABLE_FILL, WALKABLE_EDGE = "#e3ecd3", "#7d9a55"
#: Slow water warm, fast water blue; shallow light, deep dark.
WATER_CLASS = {"slow, shallow": "#f6d9a3", "slow, deep": "#d99a2b", "fast, shallow": "#b4d3ee", "fast, deep": "#3f7fbf"}
DEPTH_MARKERS = {1: ("o", "one depth"), 2: ("^", "two depths"), 3: ("s", "three depths")}


def draw_map(flow: FlowField, kind: str, plan: dict, path: Path, *, access: dict | None = None, walkable=None, title: str = "", subtitle: str = "", epsg=None, pdf: bool = True) -> Path:
    """Draw the map of one kind of survey on an A4 sheet (PNG, and PDF for printing). *walkable* is the union of the walkable areas, drawn under the maps of the surveys that walk."""
    import matplotlib

    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt
    from matplotlib import patheffects
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from matplotlib.patches import Polygon as MplPolygon
    from matplotlib.transforms import offset_copy

    access = access or {}
    halo = [patheffects.withStroke(linewidth=2.2, foreground=HALO)]
    xs, ys = _extent_points(kind, plan, access)
    wet = flow.wet
    if not len(xs):
        xs, ys = flow.x[wet], flow.y[wet]
    x0, x1, y0, y1 = float(np.min(xs)), float(np.max(xs)), float(np.min(ys)), float(np.max(ys))
    pad = max(0.07 * max(x1 - x0, y1 - y0), 25.0)
    x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
    landscape = (x1 - x0) >= (y1 - y0)
    size = (11.69, 8.27) if landscape else (8.27, 11.69)
    # fill the sheet: widen the shorter side of the extent to the aspect of the frame
    frame = (size[0] - 0.9) / (size[1] - 1.9)
    if (x1 - x0) / (y1 - y0) < frame:
        grow = 0.5 * ((y1 - y0) * frame - (x1 - x0))
        x0, x1 = x0 - grow, x1 + grow
    else:
        grow = 0.5 * ((x1 - x0) / frame - (y1 - y0))
        y0, y1 = y0 - grow, y1 + grow

    with plt.rc_context({"font.family": _font(), "font.size": 8}):
        fig = plt.figure(figsize=size)
        ax = fig.add_axes([0.45 / size[0], 1.25 / size[1], 1 - 0.9 / size[0], 1 - 1.9 / size[1]])
        handles: list = []
        from matplotlib.tri import Triangulation

        tri = Triangulation(flow.x, flow.y, flow.triangles, mask=~wet[flow.triangles].all(axis=1))
        if walkable is not None and not walkable.is_empty and kind in ("ground", "temperature"):
            ax.add_patch(_patch(walkable, facecolor=WALKABLE_FILL, edgecolor=WALKABLE_EDGE, linewidth=0.6, zorder=0.5))
            handles.append(Patch(facecolor=WALKABLE_FILL, edgecolor=WALKABLE_EDGE, linewidth=0.6, label="Walkable area on land"))
        if kind == "temperature":
            ax.tripcolor(tri, np.ones(len(flow.x)), cmap=LinearSegmentedColormap.from_list("water", ["#e9eef2", "#e9eef2"]), rasterized=True, zorder=1)
            for patch in plan.get("patches", []):
                ax.add_patch(_patch(patch["geometry"], facecolor=WATER_CLASS[patch["water"]], edgecolor=HALO, linewidth=0.3, zorder=2))
            shares = plan.get("summary", {}).get("area_share", {})
            handles += [Patch(facecolor=color, edgecolor=MUTED, linewidth=0.4, label=f"{name.capitalize()} water ({shares.get(name, 0):.0%} of the wetted area)") for name, color in WATER_CLASS.items()]
        else:
            cmap = LinearSegmentedColormap.from_list("depth", ["#e4eff8", "#a9cbe6", "#5f9bcf", "#2f6aa8"])
            top = float(np.percentile(flow.depth[wet], 98)) if wet.any() else 1.0
            shade = ax.tripcolor(tri, flow.depth, cmap=cmap, vmin=0.0, vmax=max(top, 0.1), shading="gouraud", rasterized=True, zorder=1)
            bar = fig.add_axes([1 - 2.6 / size[0], 0.72 / size[1], 2.1 / size[0], 0.1 / size[1]])
            fig.colorbar(shade, cax=bar, orientation="horizontal")
            bar.set_title("Water depth of the model (m)", fontsize=7, color=MUTED, pad=2)
            bar.tick_params(labelsize=6.5, colors=MUTED, length=2)
            bar.spines[:].set_visible(False)

        def arrow_line(coords, color, *, style="-", width=1.3, z=5, arrows=1):
            c = np.asarray(coords, dtype=float)
            if len(c) < 2:
                return
            ax.plot(c[:, 0], c[:, 1], linestyle=style, color=color, linewidth=width, zorder=z, solid_capstyle="round", path_effects=[patheffects.withStroke(linewidth=width + 1.4, foreground=HALO, alpha=0.85)])
            s = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(c[:, 0]), np.diff(c[:, 1])))])
            if s[-1] < 0.012 * (x1 - x0):
                return
            for share in (np.arange(arrows) + 0.5) / arrows:
                at = share * s[-1]
                i = min(int(np.searchsorted(s, at)), len(c) - 1)
                j = max(i - 1, 0)
                if s[i] == s[j]:
                    continue
                t = (at - s[j]) / (s[i] - s[j])
                px, py = c[j] + t * (c[i] - c[j])
                dx, dy = (c[i] - c[j]) / (s[i] - s[j])
                reach = 0.004 * (x1 - x0)
                ax.annotate("", xy=(px + reach * dx, py + reach * dy), xytext=(px - reach * dx, py - reach * dy), arrowprops={"arrowstyle": "-|>", "color": color, "lw": 0, "mutation_scale": 9, "shrinkA": 0, "shrinkB": 0}, zorder=z + 1)

        def mark(x, y, marker, **style):
            """A marker beside ``(x, y)``, so that it does not hide the point it belongs to."""
            ax.plot(x, y, marker, transform=offset_copy(ax.transData, fig=fig, x=-8, y=8, units="points"), zorder=8, **style)

        def label(x, y, text, *, color=INK, dx=5, dy=5, size=7, weight="normal"):
            ax.annotate(str(text), (x, y), xytext=(dx, dy), textcoords="offset points", fontsize=size, color=color, fontweight=weight, path_effects=halo, zorder=9)

        if kind == "ground":
            surveyors = sorted({p["number"] for p in plan.get("paths", []) if p["who"] == "surveyor"}) or [1]
            for p in plan.get("paths", []):
                if str(p["who"]).lower() == "dgps":
                    arrow_line(p["coords"], WHO[5], style=(0, (4, 2)), width=1.0, z=4)
                else:
                    arrow_line(p["coords"], WHO[(p["number"] - 1) % 5], width=1.3 if p["kind"] == "walk" else 1.0, style="-" if p["kind"] == "walk" else (0, (1, 1.5)))
            for row in plan.get("points", []):
                marker = DEPTH_MARKERS[min(max(int(row["n_depths"]), 1), 3)][0]
                ax.plot(row["x"], row["y"], marker, markersize=6.5, markerfacecolor=HALO, markeredgecolor=WHO[(row["surveyor"] - 1) % 5], markeredgewidth=1.5, zorder=7)
                label(row["x"], row["y"], row["label"])
            for event in plan.get("events", []):
                pressure = event["kind"] == "pressure adjustment"
                mark(event["x"], event["y"], "*" if pressure else "P", markersize=11 if pressure else 8, markerfacecolor="#ffd23f" if pressure else HALO, markeredgecolor=INK, markeredgewidth=0.8)
            handles += [Line2D([], [], marker=m, linestyle="", markerfacecolor=HALO, markeredgecolor=INK, markeredgewidth=1.3, markersize=6.5, label=f"Vertical, {text}") for m, text in DEPTH_MARKERS.values()]
            handles += [Line2D([], [], color=WHO[(n - 1) % 5], linewidth=1.5, label=f"Walk of surveyor {n} (arrows: direction)" if len(surveyors) > 1 else "Walk (arrows: direction)") for n in surveyors]
            handles.append(Line2D([], [], color=WHO[0] if len(surveyors) == 1 else INK, linewidth=1.0, linestyle=(0, (1, 1.5)), label="Walk back to the access point"))
            if any(str(p["who"]).lower() == "dgps" for p in plan.get("paths", [])):
                handles.append(Line2D([], [], color=WHO[5], linewidth=1.0, linestyle=(0, (4, 2)), label="Walk of the DGPS surveyor"))
            handles.append(Line2D([], [], marker="*", linestyle="", markerfacecolor="#ffd23f", markeredgecolor=INK, markersize=11, label="Pressure adjustment of the probe"))
            if any(e["kind"] != "pressure adjustment" for e in plan.get("events", [])):
                handles.append(Line2D([], [], marker="P", linestyle="", markerfacecolor=HALO, markeredgecolor=INK, markersize=8, label="Break"))
        elif kind == "airborne":
            for leg in plan.get("route", []):
                arrow_line(leg["coords"], WHO[5], width=1.0, style="-" if leg["kind"] == "flight" else (0, (1, 1.5)), z=4)
            batteries = sorted({s["battery"] for s in plan.get("stations", [])})
            for s in plan.get("stations", []):
                color = WHO[(s["battery"] - 1) % 5]
                ax.add_patch(MplPolygon(np.asarray(s["polygon"]), closed=True, facecolor=color, alpha=0.16, edgecolor="none", zorder=5))
                ax.add_patch(MplPolygon(np.asarray(s["polygon"]), closed=True, facecolor="none", edgecolor=color, linewidth=1.4, zorder=6))
                ax.plot(s["x"], s["y"], "+", color=color, markersize=7, markeredgewidth=1.3, zorder=7)
                label(s["x"], s["y"], s["station"], size=8, weight="bold", dx=4, dy=4)
            handles += [Patch(facecolor="none", edgecolor=WHO[(b - 1) % 5], linewidth=1.4, label=f"Video frame of a hover station, battery {b}") for b in batteries]
            handles += [Line2D([], [], color=WHO[5], linewidth=1.0, label="Flight between the stations (arrows: direction)"), Line2D([], [], color=WHO[5], linewidth=1.0, linestyle=(0, (1, 1.5)), label="Flight back to the take-off point")]
            if plan.get("takeoff") is not None:
                ax.plot(*plan["takeoff"], "H", markersize=11, markerfacecolor=HALO, markeredgecolor=INK, markeredgewidth=1.4, zorder=8)
                label(*plan["takeoff"], "Take-off", weight="bold", dx=7, dy=-10)
                handles.append(Line2D([], [], marker="H", linestyle="", markerfacecolor=HALO, markeredgecolor=INK, markersize=9, label="Take-off and landing"))
        elif kind == "kayak":
            for patch in plan.get("avoid", []):
                ax.add_patch(_patch(patch["geometry"], facecolor="none", edgecolor=DANGER, hatch="////", linewidth=0.9, zorder=4))
            styles = ["-", (0, (5, 1.5)), (0, (2, 1.2)), (0, (6, 1.5, 1, 1.5))]
            for i, track in enumerate(plan.get("tracks", [])):
                arrow_line(track["coords"], WHO[i % 5], style=styles[i % 4], width=1.5, arrows=max(3, int(track["length"] / max(0.12 * (x1 - x0), 1.0))))
                handles.append(Line2D([], [], color=WHO[i % 5], linestyle=styles[i % 4], linewidth=1.5, label=f"Float {track['float']} (kayaker {track['kayaker']}, {track['start']} to {track['end']})"))
            for event in plan.get("events", []):
                mark(event["x"], event["y"], "P", markersize=8, markerfacecolor=HALO, markeredgecolor=INK, markeredgewidth=0.8)
            if plan.get("avoid"):
                limit = plan.get("summary", {}).get("max_velocity")
                handles.append(Patch(facecolor="none", edgecolor=DANGER, hatch="////", label="Avoid: faster than " + (f"{limit:g} m/s" if limit else "the limit")))
            if plan.get("events"):
                handles.append(Line2D([], [], marker="P", linestyle="", markerfacecolor=HALO, markeredgecolor=INK, markersize=8, label="Break (pull out at a bank)"))
            for key, text, marker in (("put_in", "Put-in", "D"), ("take_out", "Take-out", "X")):
                if plan.get(key) is not None:
                    ax.plot(*plan[key], marker, markersize=10, markerfacecolor=HALO, markeredgecolor=INK, markeredgewidth=1.4, zorder=8)
                    label(*plan[key], text, weight="bold", dx=7, dy=-10)
        elif kind == "temperature":
            for p in plan.get("paths", []):
                arrow_line(p["coords"], INK, width=1.0, style="-" if p["kind"] == "walk" else (0, (1, 1.5)))
            logger = plan.get("summary", {}).get("method") == "loggers"
            for row in plan.get("points", []):
                on_foot = row["reach"] == "wading"
                ax.plot(row["x"], row["y"], "s" if logger else "o", markersize=7, markerfacecolor=HALO if on_foot else "#ffd23f", markeredgecolor=INK, markeredgewidth=1.4, zorder=7)
                label(row["x"], row["y"], row["label"])
            handles.append(Line2D([], [], marker="s" if logger else "o", linestyle="", markerfacecolor=HALO, markeredgecolor=INK, markersize=7, label="Temperature logger" if logger else "Reading of the mobile probe"))
            for event in plan.get("events", []):
                mark(event["x"], event["y"], "P", markersize=8, markerfacecolor=HALO, markeredgecolor=INK, markeredgewidth=0.8)
            if plan.get("events"):
                handles.append(Line2D([], [], marker="P", linestyle="", markerfacecolor=HALO, markeredgecolor=INK, markersize=8, label="Break"))
            if any(row["reach"] != "wading" for row in plan.get("points", [])):
                handles.append(Line2D([], [], marker="s" if logger else "o", linestyle="", markerfacecolor="#ffd23f", markeredgecolor=INK, markersize=7, label="... not reached by wading"))
            if plan.get("paths"):
                handles.append(Line2D([], [], color=INK, linewidth=1.0, label="Walk (arrows: direction)"))

        if kind in ("ground", "temperature") and access.get("access") is not None:
            ax.plot(*access["access"], "D", markersize=10, markerfacecolor=HALO, markeredgecolor=INK, markeredgewidth=1.4, zorder=8)
            label(*access["access"], "Access", weight="bold", dx=7, dy=-10)
            handles.append(Line2D([], [], marker="D", linestyle="", markerfacecolor=HALO, markeredgecolor=INK, markersize=8, label="Access point"))

        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_aspect("equal", adjustable="box")
        ax.set_facecolor("#f4f3ef")
        ax.tick_params(labelsize=6.5, colors=MUTED, length=2, direction="in", pad=-2)
        ax.ticklabel_format(style="plain", useOffset=False)
        ax.locator_params(nbins=5)
        ax.set_xticks([t for t in ax.get_xticks() if x0 + 0.06 * (x1 - x0) <= t <= x1 - 0.06 * (x1 - x0)])
        ax.set_yticks([t for t in ax.get_yticks() if y0 + 0.08 * (y1 - y0) <= t <= y1 - 0.08 * (y1 - y0)])
        for item in ax.get_xticklabels():
            item.set_va("bottom")
            item.set_path_effects(halo)
        for item in ax.get_yticklabels():
            item.set_ha("left")
            item.set_rotation(90)
            item.set_va("center")
            item.set_path_effects(halo)
        ax.grid(color=MUTED, linewidth=0.3, alpha=0.35)
        for spine in ax.spines.values():
            spine.set_color(MUTED)
            spine.set_linewidth(0.6)

        # scale bar and north arrow (grid north of the coordinate system)
        span = _nice(0.2 * (x1 - x0))
        bx, by = x0 + 0.03 * (x1 - x0), y0 + 0.045 * (y1 - y0)
        ax.plot([bx, bx + span], [by, by], color=INK, linewidth=2.5, solid_capstyle="butt", zorder=10, path_effects=[patheffects.withStroke(linewidth=4.5, foreground=HALO)])
        ax.annotate(f"{span:g} m", (bx + 0.5 * span, by), xytext=(0, 4), textcoords="offset points", ha="center", fontsize=7.5, color=INK, path_effects=halo, zorder=10)
        nx, ny = x1 - 0.03 * (x1 - x0), y1 - 0.11 * (y1 - y0)
        ax.annotate("N", xy=(nx, ny + 0.07 * (y1 - y0)), xytext=(nx, ny), ha="center", va="center", fontsize=9, fontweight="bold", color=INK, arrowprops={"arrowstyle": "-|>", "color": INK, "lw": 1.2}, path_effects=halo, zorder=10)

        fig.text(0.45 / size[0], 1 - 0.36 / size[1], title or f"Survey plan: {KIND_TITLES.get(kind, kind)}", fontsize=13, fontweight="bold", color=INK, va="center")
        if subtitle:
            fig.text(0.45 / size[0], 1 - 0.56 / size[1], subtitle, fontsize=8, color=MUTED, va="center")
        if handles:
            fig.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.45 / size[0], 0.12 / size[1]), ncol=3 if landscape else 2, frameon=False, fontsize=7.5, handlelength=2.4, columnspacing=1.6, labelcolor=INK)
        crs = f"Coordinates: EPSG:{int(epsg)}, grid north up. " if epsg else ""
        fig.text(1 - 0.45 / size[0], 0.2 / size[1], f"{crs}Planned with aXqua on the flow field of the model.", fontsize=6.5, color=MUTED, ha="right", va="center")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=200)
        if pdf:
            fig.savefig(path.with_suffix(".pdf"), dpi=300)
        plt.close(fig)
    return path


def _font() -> list[str]:
    """Arial where the computer has it, else the closest installed sans-serif."""
    from matplotlib import font_manager

    have = {entry.name for entry in font_manager.fontManager.ttflist}
    return [name for name in ("Arial", "Liberation Sans", "Helvetica", "DejaVu Sans") if name in have] or ["sans-serif"]


def _patch(geometry, **style):
    """A polygon or multipolygon with its holes as one matplotlib patch."""
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MplPath

    vertices, codes = [], []
    for part in getattr(geometry, "geoms", [geometry]):
        for ring in [part.exterior, *part.interiors]:
            xy = np.asarray(ring.coords)[:, :2]
            vertices.append(xy)
            codes += [MplPath.MOVETO] + [MplPath.LINETO] * (len(xy) - 2) + [MplPath.CLOSEPOLY]
    return PathPatch(MplPath(np.vstack(vertices), codes), **style)


def _extent_points(kind: str, plan: dict, access: dict) -> tuple[np.ndarray, np.ndarray]:
    xs: list[float] = []
    ys: list[float] = []

    def add(coords) -> None:
        for x, y in coords:
            xs.append(float(x))
            ys.append(float(y))

    for key in ("paths", "route", "tracks"):
        for row in plan.get(key, []):
            add(row["coords"])
    for key in ("points", "stations"):
        add((row["x"], row["y"]) for row in plan.get(key, []))
    for row in plan.get("stations", []):
        add(row["polygon"])
    for key in ("takeoff", "put_in", "take_out"):
        if plan.get(key) is not None:
            add([plan[key]])
    if kind in ("ground", "temperature") and access.get("access") is not None and (xs or kind == "ground"):
        add([access["access"]])
    return np.asarray(xs), np.asarray(ys)


def _nice(length: float) -> float:
    """A round length for a scale bar, not longer than *length*."""
    if length <= 0:
        return 1.0
    power = 10.0 ** math.floor(math.log10(length))
    return max(step * power for step in (1.0, 2.0, 5.0) if step * power <= length)


__all__ = ["ACCESS_LAYER", "GPKG_NAME", "LAYERS", "ROLES", "WALKABLE_LAYER", "draw_map", "ensure_user_layers", "read_access", "read_walkable", "role_of", "write_layers"]
