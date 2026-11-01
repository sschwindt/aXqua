"""The hydrology checkup: what a hydrology case needs before it is built.

Returns :class:`~axqua.core.diagnostics.Finding` objects and never raises, so the case settings and the plugin can show every problem at once; the build runs the same checks and stops on an error. Each code has an anchor in ``docs/troubleshooting/``.

*terrain* switches on the part that takes seconds: the analysis of the DEM (:mod:`axqua.hydrology.terrain`) and the estimate of the mesh size from the size grid. The case settings and the refresh of the plugin leave it out, ``axqua hydrology check``, the *Check* button and the build run it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from axqua.core.diagnostics import ERROR, WARNING, Finding

#: Every code this module can emit, with its severity (the docs test reads this).
CODES = {
    "axqua.hydrology.rainfall_unreadable": ERROR,
    "axqua.hydrology.rainfall_intense": WARNING,
    "axqua.hydrology.rainfall_gaps": WARNING,
    "axqua.hydrology.reanalysis_rain": WARNING,
    "axqua.hydrology.weather_unreadable": ERROR,
    "axqua.hydrology.weather_alignment": WARNING,
    "axqua.hydrology.weather_gaps": WARNING,
    "axqua.hydrology.weather_short": WARNING,
    "axqua.hydrology.wind_strong": WARNING,
    "axqua.hydrology.evaporation_high": WARNING,
    "axqua.hydrology.soil_invalid": ERROR,
    "axqua.hydrology.soil_texture_mismatch": WARNING,
    "axqua.hydrology.initial_moisture_default": WARNING,
    "axqua.hydrology.closed_catchment": WARNING,
    "axqua.hydrology.free_outlet": WARNING,
    "axqua.hydrology.outlet_unknown": ERROR,
    "axqua.hydrology.river_without_outlet": WARNING,
    "axqua.hydrology.sediment_undecided": WARNING,
    "axqua.hydrology.sediment_out_of_range": WARNING,
    "axqua.hydrology.sediment_with_river": ERROR,
    "axqua.hydrology.finite_elements": WARNING,
    "axqua.hydrology.drain_short": WARNING,
    "axqua.hydrology.large_mesh": WARNING,
    "axqua.hydrology.flow_across_wall": WARNING,
    "axqua.hydrology.depressions": WARNING,
    "axqua.hydrology.terrain_unreadable": WARNING,
}

#: Thresholds of the plausibility warnings.
PEAK_RAIN_MM_H = 150.0          # convective cloudbursts reach this for minutes only
PEAK_WIND_M_S = 40.0
PEAK_EVAPORATION_MM_H = 1.5     # open water rarely evaporates more in an hour
MESH_ELEMENTS = 2_000_000


def _finding(code: str, message: str, subject: str = "", remedy: str = "") -> Finding:
    return Finding(CODES[code], code, message, subject=subject, remedy=remedy)


def _provenance(path) -> dict:
    """The provenance file the data wizard writes beside a file it fetched."""
    if path is None:
        return {}
    meta = Path(str(path) + ".json")
    if not meta.exists():
        meta = Path(path).with_suffix(".json")
    try:
        return json.loads(meta.read_text()) if meta.exists() else {}
    except (OSError, ValueError):
        return {}


def run_checks(cfg, *, terrain: bool = False) -> list[Finding]:
    """All findings of the hydrology checkup; empty when the case is not in the mode."""
    if not getattr(cfg, "hydrology_mode", False):
        return []
    found: list[Finding] = []
    rain = _check_rain(cfg, found)
    _check_weather(cfg, rain, found)
    _check_soil(cfg, found)
    _check_outlet_and_run(cfg, rain, found, terrain=terrain)
    _check_sediment(cfg, found)
    if terrain:
        _check_terrain(cfg, found)
    return found


def _check_rain(cfg, found: list[Finding]):
    from axqua.hydrology import rainfall

    h = cfg.hydrology
    if h.rainfall is None:
        found.append(_finding("axqua.hydrology.rainfall_unreadable",
                              "No rain series is set.", "hydrology.rainfall",
                              "Set hydrology.rainfall, or use the data wizard."))
        return None
    try:
        series = rainfall.read_series(h.rainfall, units=h.rainfall_units,
                                      stamp=h.rainfall_stamp, start=h.rainfall_start)
    except Exception as exc:  # noqa: BLE001 - every reason is the user's to read
        found.append(_finding("axqua.hydrology.rainfall_unreadable",
                              f"The rain series cannot be read: {exc}",
                              "hydrology.rainfall",
                              "A table of time and rain, in mm/h or mm per interval."))
        return None
    problems = series.problems()
    if problems:
        found.append(_finding("axqua.hydrology.rainfall_unreadable",
                              f"The rain series is not usable: {'; '.join(problems)}.",
                              "hydrology.rainfall"))
        return None
    if series.gaps:
        found.append(_finding(
            "axqua.hydrology.rainfall_gaps",
            f"The rain record has no value for {series.gaps} intervals "
            f"({series.gap_hours:g} h); they are taken as dry.", "hydrology.rainfall",
            "Fill the gaps from a neighboring gauge or the radar product of the "
            "national meteorological agency, or check that the event lies outside them."))
    if series.peak_mm_h > PEAK_RAIN_MM_H:
        found.append(_finding(
            "axqua.hydrology.rainfall_intense",
            f"The rain peaks at {series.peak_mm_h:.0f} mm/h, more than a cloudburst "
            f"holds for longer than minutes ({PEAK_RAIN_MM_H:.0f} mm/h).",
            "hydrology.rainfall_units",
            "Check that depths per interval are not read as intensities (mm vs mm/h)."))
    source = str(_provenance(h.rainfall).get("source", "")).lower()
    if "open-meteo" in source or "era5" in source:
        found.append(_finding(
            "axqua.hydrology.reanalysis_rain",
            "The rain comes from a reanalysis (ERA5). Reanalysis spreads a storm over "
            "cells of 10 to 30 km and an hour, and underestimates short intensities "
            "(1-h extremes by about 40 %).", "hydrology.rainfall",
            "Use gauge or radar data of the national meteorological agency where "
            "they exist, or a design storm."))
    return series


def _check_weather(cfg, rain, found: list[Finding]) -> None:
    from axqua.hydrology import weather
    from axqua.hydrology.rainfall import _count

    h = cfg.hydrology
    if not (h.wind or h.evaporation != "none"):
        return
    if h.weather is None:
        found.append(_finding("axqua.hydrology.weather_unreadable",
                              "Wind or evaporation is on, but no weather series is set.",
                              "hydrology.weather"))
        return
    try:
        meteo = weather.read_weather(h.weather, stamp=h.rainfall_stamp,
                                     origin=rain.origin if rain is not None else None)
        evaporation = None
        if h.evaporation != "none":
            lat = lon = None
            if h.evaporation == "penman":
                from axqua.solvers.telemac.hydrology import catchment_location
                lat, lon, _ = catchment_location(cfg)
            evaporation = weather.evaporation_rate(meteo, h.evaporation,
                                                   factor=h.open_water_factor,
                                                   latitude=lat, longitude=lon)
        if h.wind and not (meteo.has("wind_speed_10m")
                           and meteo.has("wind_direction_10m")):
            raise ValueError("wind needs the columns wind_speed_10m and "
                             "wind_direction_10m")
    except Exception as exc:  # noqa: BLE001
        found.append(_finding("axqua.hydrology.weather_unreadable",
                              f"The weather series cannot be used: {exc}",
                              "hydrology.weather"))
        return
    if meteo.gaps:
        found.append(_finding(
            "axqua.hydrology.weather_gaps",
            "The weather series has no value for "
            + ", ".join(f"{_count(n, 'reading')} of {name}"
                        for name, n in meteo.gaps.items())
            + "; they are interpolated between the readings around them.",
            "hydrology.weather",
            "Fill longer gaps from a neighboring station, or make sure the event lies "
            "outside them."))
    if rain is not None and rain.origin is None and meteo.origin is not None:
        found.append(_finding(
            "axqua.hydrology.weather_alignment",
            f"The rain has no date, the weather does (it starts "
            f"{meteo.origin:%Y-%m-%d %H:%M}); the weather is applied from the start of "
            "the rain.", "hydrology.rainfall_start",
            "Set hydrology.rainfall_start to place the rain in the time of the "
            "weather, or remove the weather series if it belongs to another event."))
    if rain is not None:
        run_end = rain.last_rain + float(h.drain_time)
        if meteo.end_s.size and float(meteo.end_s[-1]) < run_end - 1.0:
            found.append(_finding(
                "axqua.hydrology.weather_short",
                f"The weather ends at {meteo.end_s[-1]:.0f} s, the run at {run_end:.0f} "
                "s; after that there is no wind and no evaporation.",
                "hydrology.weather"))
    if h.wind and meteo.has("wind_speed_10m"):
        peak = float(np.nanmax(meteo.values["wind_speed_10m"]))
        if peak > PEAK_WIND_M_S:
            found.append(_finding("axqua.hydrology.wind_strong",
                                  f"The wind reaches {peak:.0f} m/s at 10 m.",
                                  "hydrology.weather",
                                  "Check the unit: m/s, not km/h."))
    if evaporation is not None and evaporation.size \
            and float(np.max(evaporation)) > PEAK_EVAPORATION_MM_H:
        found.append(_finding(
            "axqua.hydrology.evaporation_high",
            f"Evaporation reaches {float(np.max(evaporation)):.2f} mm/h.",
            "hydrology.weather", "Check the unit of et0 or evaporation: mm/h."))


def _check_soil(cfg, found: list[Finding]) -> None:
    from axqua.hydrology import soil

    h = cfg.hydrology
    if h.infiltration == "none":
        return
    try:
        zones = soil.case_zones(cfg)
    except Exception as exc:  # noqa: BLE001
        found.append(_finding("axqua.hydrology.soil_invalid",
                              f"The soil cannot be read: {exc}", "hydrology.soil_table"))
        return
    for zone in zones.values():
        problems = zone.problems()
        if problems:
            found.append(_finding("axqua.hydrology.soil_invalid",
                                  f"Soil zone {zone.zone_id}: {'; '.join(problems)}.",
                                  "hydrology.soil_table"))
        row = soil.texture(zone.texture) if zone.texture else None
        if row is not None and zone.ks_mm_h > 0 and not (
                0.1 <= zone.ks_mm_h / row.ks_mm_h <= 10.0):
            found.append(_finding(
                "axqua.hydrology.soil_texture_mismatch",
                f"Soil zone {zone.zone_id} is {zone.texture} with Ks = "
                f"{zone.ks_mm_h:g} mm/h; Rawls et al. (1983) give {row.ks_mm_h:g} mm/h "
                "for that texture, more than ten times apart.", "hydrology.soil_table",
                "Check the unit of Ks: mm/h (1 cm/h = 10 mm/h, 1e-6 m/s = 3.6 mm/h)."))
    if h.soil_zones is not None:
        try:
            import geopandas as gpd

            layer = gpd.read_file(h.soil_zones)
            field = next((c for c in layer.columns
                          if str(c).lower() == str(h.soil_zone_field).lower()), None)
            if field is None:
                raise ValueError(f"no '{h.soil_zone_field}' column")
            missing = sorted({int(v) for v in layer[field].dropna()} - set(zones))
            if missing:
                raise ValueError(f"zone id(s) {missing} of the layer are not in the "
                                 "soil table")
        except Exception as exc:  # noqa: BLE001
            found.append(_finding("axqua.hydrology.soil_invalid",
                                  f"The soil zones cannot be used: {exc}",
                                  "hydrology.soil_zones"))
    has_own = any(_has_own_theta(cfg))
    if h.initial_water_content is None and not has_own:
        found.append(_finding(
            "axqua.hydrology.initial_moisture_default",
            "No initial water content is given, so the soil starts at an effective "
            f"saturation of {h.initial_saturation:g}. The runoff of a storm depends "
            "strongly on it.", "hydrology.initial_water_content",
            "Give the soil moisture at the start of the event (the data wizard fetches "
            "the ERA5-Land value), or a theta_i per zone."))


def _has_own_theta(cfg):
    import pandas as pd

    h = cfg.hydrology
    if h.soil_table is None:
        return [False]
    try:
        frame = pd.read_csv(h.soil_table, comment="#", sep=None, engine="python")
    except Exception:  # noqa: BLE001
        return [False]
    for col in frame.columns:
        if str(col).lower().strip() in ("theta_i", "thetai", "initial_water_content"):
            return [bool(pd.to_numeric(frame[col], errors="coerce").notna().any())]
    return [False]


def _check_sediment(cfg, found: list[Finding]) -> None:
    """Sediment transport is strongly recommended in the mode: a case that has not decided is told so. A block ``morphodynamics`` with ``enabled: false`` is a decision and is left alone. What TELEMAC cannot run with a river inflow (:func:`axqua.hydrology.sediment.river_conflicts`) is an error. A grain size outside the range of the chosen formula is named."""
    m = cfg.morphodynamics
    if "morphodynamics" not in (cfg.declared_blocks or ()) and not m.enabled:
        found.append(_finding(
            "axqua.hydrology.sediment_undecided",
            "The case does not transport sediment. The runoff of a catchment carries "
            "soil, and how much of it reaches the outlet is often the question of the "
            "study.",
            "morphodynamics.enabled",
            "Set up sediment transport with the Sediment transport wizard on the "
            "sub-tab Simulations > Hydrology (axqua hydrology sediment), or switch it "
            "off there to keep the case hydraulic only."))
        return
    if not m.enabled:
        return
    from axqua.hydrology.sediment import (GRAVEL_D50, MEYER_PETER_MUELLER, VAN_RIJN_BEDLOAD,
                                          has_river, river_conflicts)

    if has_river(cfg):
        for message, remedy in river_conflicts(m):
            found.append(_finding("axqua.hydrology.sediment_with_river", message,
                                  "morphodynamics.sediment_classes", remedy))

    for c in m.sediment_classes or []:
        d = float(c.get("diameter", 0.0) or 0.0)
        kind = c.get("type") or m.sediment_type
        if kind != "NCO" or not m.bedload:
            continue
        if m.bedload_formula == VAN_RIJN_BEDLOAD and d > GRAVEL_D50:
            found.append(_finding(
                "axqua.hydrology.sediment_out_of_range",
                f"A class of {1000 * d:g} mm is coarser than the 2 mm up to which van "
                "Rijn derived his bed-load formula.",
                "morphodynamics.bedload_formula",
                "Use the formula of Meyer-Peter and Müller (1) for gravel."))
        elif m.bedload_formula == MEYER_PETER_MUELLER and d < 0.0004:
            found.append(_finding(
                "axqua.hydrology.sediment_out_of_range",
                f"A class of {1000 * d:g} mm is finer than the gravel and coarse sand for "
                "which Meyer-Peter and Müller derived their formula.",
                "morphodynamics.bedload_formula",
                "Use van Rijn's formula (7) for the sand of a soil."))


def _check_river(cfg, found: list[Finding]) -> None:
    """The outlet column of the liquid boundaries, and a river that flows through the catchment: an inflow line carries a discharge into the domain, which needs an outflow line that holds a level for it."""
    if cfg.boundaries.liquid_boundaries is None:
        return
    try:
        from axqua.core.boundaries import (HYDROLOGY_OUTLETS, line_conditions,
                                           liquid_line_details, liquid_lines,
                                           outlet_level)

        lines = line_conditions(cfg) or []
        kinds = liquid_lines(cfg)
        details = liquid_line_details(cfg) or []
    except Exception:  # noqa: BLE001 - the case check names an unreadable layer
        return
    for line in lines:
        if line["condition"] is not None and line["condition"] not in HYDROLOGY_OUTLETS:
            found.append(_finding(
                "axqua.hydrology.outlet_unknown",
                f"An outflow line has the outlet {line['value']!r} in the column Outlet.",
                "boundaries.liquid_boundaries",
                f"Write one of {', '.join(HYDROLOGY_OUTLETS)}, or leave the cell empty "
                f"for hydrology.outlet_condition."))
    river = "inflow" in kinds and (
        cfg.boundaries.prescribed_flowrate is not None or cfg.boundaries.inflow is not None
        or any(d["kind"] == "inflow" and d["discharge"] for d in details))
    if not river or "outflow" not in kinds:
        return
    conditions = [line["condition"] or cfg.outflow_condition for line in lines] \
        if lines else [cfg.outflow_condition]
    if all(outlet_level(c) in (None, "mesh") for c in conditions
           if c in HYDROLOGY_OUTLETS):
        found.append(_finding(
            "axqua.hydrology.river_without_outlet",
            "Water flows into the catchment through an inflow line, but every outflow "
            "line is free or an overfall, so the river leaves the model as over a brink "
            "and its level drops toward the outlet.",
            "boundaries.liquid_boundaries",
            "Give the outflow line of the river the outlet normal_depth (column Outlet "
            "of the liquid boundaries), or elevation where it flows into a lake or a "
            "larger river."))


def _check_outlet_and_run(cfg, rain, found: list[Finding], *, terrain: bool) -> None:
    h = cfg.hydrology
    outflow = 0
    if cfg.boundaries.liquid_boundaries is not None:
        try:
            from axqua.core.boundaries import liquid_lines

            outflow = int("outflow" in liquid_lines(cfg))
        except Exception:  # noqa: BLE001 - the case check names an unreadable layer
            outflow = 1
    if not outflow:
        found.append(_finding(
            "axqua.hydrology.closed_catchment",
            "The case has no outflow line: all rain stays in the catchment.",
            "boundaries.liquid_boundaries",
            "Draw an outflow line across the outlet of the catchment, on the outline."))
    _check_river(cfg, found)
    if h.outlet_condition == "free":
        found.append(_finding(
            "axqua.hydrology.free_outlet",
            "A free outlet holds only where the flow leaves supercritical; a slower "
            "outlet fills up into a pond, or blows up with finite elements.",
            "hydrology.outlet_condition",
            "Use normal_depth unless the outlet is steep and smooth, and overfall at "
            "the lower edge of a field over which the terrain falls away."))
    if h.kernel == "finite-elements":
        found.append(_finding(
            "axqua.hydrology.finite_elements",
            "The finite-element kernel lets thin films drain slowly and its free "
            "outlet is fragile; TELEMAC validates rain-runoff with finite volumes.",
            "hydrology.kernel"))
    try:
        from axqua.solvers.telemac.mesh import roi_polygon

        poly = roi_polygon(cfg)
        minx, miny, maxx, maxy = poly.bounds
        length = math.hypot(maxx - minx, maxy - miny)
        # overland flow and small streams run at some tenths of a meter per second
        travel = length / 0.5
        if rain is not None and float(h.drain_time) < 0.5 * travel:
            found.append(_finding(
                "axqua.hydrology.drain_short",
                f"The run ends {h.drain_time:g} s after the rain; water from the far "
                f"end of the catchment (about {length:.0f} m) needs about "
                f"{travel:.0f} s at 0.5 m/s, so the recession is cut off.",
                "hydrology.drain_time"))
        from axqua.solvers.telemac.mesh import estimate_hydrology_elements

        elements = estimate_hydrology_elements(cfg) if terrain else 0
        if elements > MESH_ELEMENTS:
            found.append(_finding(
                "axqua.hydrology.large_mesh",
                f"The mesh will have about {elements:,} elements.",
                "mesh.default_size",
                "Raise mesh.default_size or the edge lengths of the mesh zones."))
    except Exception:  # noqa: BLE001 - the case check names a missing outline
        pass


def _check_terrain(cfg, found: list[Finding]) -> None:
    from axqua.hydrology import terrain

    try:
        report = terrain.analyze(cfg)
    except Exception as exc:  # noqa: BLE001
        found.append(_finding("axqua.hydrology.terrain_unreadable",
                              f"The terrain model could not be analyzed: {exc}",
                              "geodata.dem_initial"))
        return
    share = report.crossing_m2 / report.catchment_m2 if report.catchment_m2 else 0.0
    if share > 0.02:
        where = "; ".join(f"x {c.x:.0f}, y {c.y:.0f} ({c.area_m2:,.0f} m2)"
                          for c in report.crossings[:3])
        found.append(_finding(
            "axqua.hydrology.flow_across_wall",
            f"{100 * share:.0f} % of the catchment ({report.crossing_m2:,.0f} m2) drains "
            f"across the outline where it is a wall, where the water will pond. "
            f"Largest: {where}.", "geodata.boundary",
            "Make those stretches outflow lines, or move the outline to the divide."))
    big = [d for d in report.depressions if d.volume_m3 > 1.0]
    if big:
        where = "; ".join(f"x {d.x:.0f}, y {d.y:.0f} ({d.volume_m3:,.0f} m3, "
                          f"{d.depth_m:.2f} m deep)" for d in big[:3])
        found.append(_finding(
            "axqua.hydrology.depressions",
            f"The terrain holds {len(report.depressions)}+ closed depressions with "
            f"{report.depression_m3:,.0f} m3 of storage. Largest: {where}. A depression "
            "keeps the rain until it is full: right for a pond, wrong where the terrain "
            "model blocks a culvert or a bridge.", "geodata.dem_initial",
            "Cut culverts and bridge openings into the terrain model (structures with "
            "mode solid, or a breakline), and leave real ponds as they are."))
