"""The TELEMAC build of a hydrology case: rain, soil, weather and the outlet.

A hydrology case (``hydrology.enabled``) is built by the same pipeline as a river reach (:mod:`axqua.solvers.telemac.pipeline`): terrain, mesh, boundary conditions, steering. This module supplies what differs:

* the soil fields of the geometry, read by TELEMAC as private variables (:func:`soil_variables`);
* the forcing files: rain and evaporation for the generated runoff routine (``forcing.txt``), wind for TELEMAC's meteo module (``atmosphere.txt``) (:func:`write_forcing`);
* the outlet on TELEMAC's stage-discharge curve, one Z(Q) curve per outflow line from Manning's normal flow through the section of that line, or the measured rating of ``boundaries.stage_discharge`` (:func:`write_outlet_curves`);
* the keywords of ``hydrology2d.cas`` (:func:`steering_block`) and the case itself (:func:`write_case`).

The outline is a drainage divide and so a solid wall; water leaves through the outflow lines. The default outlet is ``normal_depth``: the level of the outflow line follows TELEMAC's Z(Q) curve of normal flow through its section, so the outlet holds the level the discharge needs and no more. Its level depends on the roughness of the outlet section (a 20 % error in Manning's n moves the depth by about 12 %), and that influence fades upstream over about ``h / S``, so the outlet goes a few such lengths downstream of where results are read. ``free`` (zero gradient of depth and velocity) holds only where the flow leaves supercritical: on the synthetic V-catchment of the tests, a channel at 2 % with Manning's n of 0.03 runs near critical, and its free outlet filled up into a pond with the finite-volume kernel and blew up with the finite-element one, while ``normal_depth`` drained the equilibrium discharge i A to 0.01 %. ``overfall`` is for an outline that is no divide but the lower edge of a field, a terrace or a bank, over which the terrain falls away: the outflow lines hold a depth of zero (:func:`overfall_level`), so the water leaves as over a brink and none comes back. A Z(Q) curve does not fit there, because one level for a whole edge that runs across the slope is the level of its lowest point only, and the free outlet of the finite-volume kernel copies the state inside, which lets water in wherever the velocity at the edge points inward; on the arable field of the example ``example-agrohydrology`` it took in 2.7 m3/s through the lower edge of the field 16 minutes after the runoff began. ``LAW OF FRICTION ON LATERAL BOUNDARIES`` plays no part: TELEMAC applies it to solid walls only.

The kernel is TELEMAC's finite-volume scheme (HLLC) with the hydrostatic reconstruction of Chen and Noelle (2017), the combination of TELEMAC's own rain-runoff validation case: the classical reconstruction fails where the water is shallower than the step of the bed between two nodes, which is what sheet flow on a hillslope is.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from axqua.config import Config
from axqua.hydrology import rainfall, soil, weather
from axqua.solvers.telemac import greenampt

log = logging.getLogger("axqua")

#: Wind formulas of TELEMAC's ``COEFFICIENT OF WIND INFLUENCE FORMULA``.
WIND_FORMULAS = {"constant": 0, "flather": 1, "wu": 2}

#: Graphic printouts of a hydrology run: the flow, the four private arrays (soil and infiltrated depth) and TELEMAC's maxima of the free surface and of the velocity.
PRINTOUTS = "U,V,S,B,H,M,N,O,R,Z,MAXZ,TMXZ,MAXV,TMXV"


def soil_variables(cfg: Config, mesh) -> list[tuple[str, str, np.ndarray]]:
    """The private variables of the geometry: Ks, a, dtheta and the infiltrated depth.

    The last one is zero in the geometry and written by the runoff routine.
    """
    fields = soil.node_soil(cfg, np.asarray(mesh.x), np.asarray(mesh.y))
    zones = sorted(set(fields.zone.tolist()))
    log.info("  soil: %d zone(s) %s, Ks %.3g..%.3g mm/h, a %.3g..%.3g mm, %d sealed "
             "node(s) of %d", len(zones), zones, float(fields.ks_mm_h.min()),
             float(fields.ks_mm_h.max()), 1000 * float(fields.a.min()),
             1000 * float(fields.a.max()), fields.sealed_nodes, fields.ks_mm_h.size)
    values = (fields.ks_mm_h, fields.a, fields.dtheta, np.zeros(fields.ks_mm_h.size))
    return [(name, unit, v) for (name, unit), v in zip(greenampt.PRIVATE_VARIABLES,
                                                        values)]


def rain_series(cfg: Config) -> rainfall.RainSeries:
    h = cfg.hydrology
    return rainfall.read_series(h.rainfall, units=h.rainfall_units,
                                stamp=h.rainfall_stamp, start=h.rainfall_start)


def weather_series(cfg: Config, rain: rainfall.RainSeries) -> weather.WeatherSeries | None:
    h = cfg.hydrology
    if h.weather is None:
        return None
    return weather.read_weather(h.weather, stamp=h.rainfall_stamp, origin=rain.origin)


def catchment_location(cfg: Config) -> tuple[float, float, float]:
    """Latitude and longitude [deg] of the catchment's centroid and its elevation [m]."""
    from pyproj import Transformer

    from axqua.solvers.telemac.mesh import roi_polygon

    centre = roi_polygon(cfg).centroid
    lon, lat = Transformer.from_crs(f"EPSG:{cfg.crs_epsg}", "EPSG:4326",
                                    always_xy=True).transform(centre.x, centre.y)
    elevation = 0.0
    try:
        import rasterio

        with rasterio.open(cfg.geodata.dem_initial) as dem:
            value = next(dem.sample([(centre.x, centre.y)]))[0]
            if np.isfinite(value) and (dem.nodata is None or value != dem.nodata):
                elevation = float(value)
    except Exception as exc:  # noqa: BLE001 - elevation only refines the pressure
        log.debug("catchment elevation unavailable: %s", exc)
    return float(lat), float(lon), elevation


@dataclass
class Forcing:
    """What :func:`write_forcing` wrote, and the duration of the run."""

    forcing: Path
    atmosphere: Path | None
    duration: float
    rain: rainfall.RainSeries
    evaporation_mm: float          # potential evaporation over the run [mm]


def write_forcing(cfg: Config, *, until: float | None = None) -> Forcing:
    """Write ``forcing.txt`` (and ``atmosphere.txt`` with wind); the run's duration.

    The duration is the end of the last rain plus ``hydrology.drain_time``, or *until* when that is later (the end of an inflow hydrograph).
    """
    h = cfg.hydrology
    rain = rain_series(cfg)
    problems = rain.problems()
    if problems:
        raise ValueError(f"hydrology.rainfall: {'; '.join(problems)}")
    duration = rain.last_rain + float(h.drain_time)
    if until is not None:
        duration = max(duration, float(until))
    meteo = weather_series(cfg, rain)
    evaporation = None
    if h.evaporation != "none":
        lat = lon = None
        elevation = 0.0
        if h.evaporation == "penman":
            lat, lon, elevation = catchment_location(cfg)
        evaporation = weather.evaporation_rate(meteo, h.evaporation,
                                               factor=h.open_water_factor,
                                               latitude=lat, longitude=lon,
                                               elevation=elevation)
    table = weather.forcing_table(rain, meteo, evaporation, until=duration)
    forcing = weather.write_forcing(cfg.model_path(cfg.forcing_file), table)
    seconds = np.diff(table["time_s"].to_numpy())
    evap_mm = float(np.sum(table["evaporation_mm_h"].to_numpy()[:-1] * seconds / 3600))
    atmosphere = None
    if h.wind:
        atmosphere = weather.write_atmosphere(cfg.model_path(cfg.atmosphere_file), meteo)
    log.info("  forcing: %.1f mm of rain (peak %.1f mm/h) ending at %.0f s, "
             "evaporation %s (%.2f mm potential), wind %s; run for %.0f s",
             rain.total_mm, rain.peak_mm_h, rain.last_rain, h.evaporation, evap_mm,
             "on" if h.wind else "off", duration)
    return Forcing(forcing, atmosphere, duration, rain, evap_mm)


# --------------------------------------------------------------------------- #
# the outlet on TELEMAC's stage-discharge curve
# --------------------------------------------------------------------------- #


def boundary_line(mesh, lb):
    """The contour of a liquid boundary as a line through its nodes."""
    from shapely.geometry import LineString

    nodes = list(lb.nodes)
    if len(nodes) < 2:
        raise ValueError(f"liquid boundary {lb.index} has fewer than two nodes")
    return LineString([(float(mesh.x[n]), float(mesh.y[n])) for n in nodes])


def outlet_slope(cfg: Config, line, *, length: float | None = None) -> float:
    """Bed slope upstream of an outflow line, from the terrain model.

    From the lowest point of the line, the DEM is sampled along the normal of the line into the catchment, up to *length* (five default edge lengths, at least 20 m), and the slope is the fit of elevation over distance. Never below 1e-4.
    """
    import rasterio

    from axqua.rating import _sample_dem, _valid
    from axqua.solvers.telemac.mesh import roi_polygon

    if cfg.hydrology.outlet_slope is not None:
        return float(cfg.hydrology.outlet_slope)
    if length is None:
        length = max(5.0 * cfg.mesh.default_size * cfg.mesh.size_scale, 20.0)
    poly = roi_polygon(cfg)
    stations = np.linspace(0.0, line.length, 60)
    points = [line.interpolate(t) for t in stations]
    with rasterio.open(cfg.geodata.dem_initial) as dem:
        bed = _sample_dem(cfg, dem, points)
        good = _valid(bed, dem)
        if not good.any():
            raise ValueError("the outflow line lies outside the terrain model")
        k = int(np.argmin(np.where(good, bed, np.inf)))
        lowest = points[k]
        a = line.interpolate(max(stations[k] - 1.0, 0.0))
        b = line.interpolate(min(stations[k] + 1.0, line.length))
        tx, ty = b.x - a.x, b.y - a.y
        norm = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / norm, tx / norm
        from shapely.geometry import Point

        probe = Point(lowest.x + nx * 0.25 * length, lowest.y + ny * 0.25 * length)
        if not poly.contains(probe):
            nx, ny = -nx, -ny
        distances = np.linspace(0.0, length, 11)
        samples = [Point(lowest.x + nx * d, lowest.y + ny * d) for d in distances]
        z = _sample_dem(cfg, dem, samples)
        ok = _valid(z, dem)
    if ok.sum() < 3:
        log.warning("  outlet slope: too few terrain samples upstream of the outflow "
                    "line; using 1e-3")
        return 1.0e-3
    slope = float(np.polyfit(distances[ok], z[ok], 1)[0])
    if slope < 1.0e-4:
        log.warning("  outlet slope from the terrain is %.2g; using 1e-4 (set "
                    "hydrology.outlet_slope to give it)", slope)
        slope = 1.0e-4
    return slope


def design_discharge(cfg: Config, rain: rainfall.RainSeries, inflow=None) -> float:
    """Upper end of the outlet curves [m3/s]: three times the peak rain on the whole catchment, plus the largest inflow. TELEMAC holds the last level of a curve above its range, so the range is generous on purpose."""
    from axqua.solvers.telemac.mesh import roi_polygon

    area = roi_polygon(cfg).area
    q = 3.0 * rain.peak_mm_h / 3.6e6 * area
    if inflow is not None:
        q += float(np.max(inflow.discharge))
    return max(q, 0.01)


def bed_roughness_ks(cfg: Config) -> float:
    """Nikuradse ks [m] of the bed where no roughness zones say otherwise.

    From ``friction.default_law`` / ``default_coefficient`` through :func:`axqua.rating.ks_from_friction`. Not the lateral wall roughness: TELEMAC applies that to solid walls only, and an outlet is the bed of its section.
    """
    from axqua.rating import ks_from_friction

    law = int(cfg.friction.default_law)
    ks = ks_from_friction(law, cfg.friction.default_coefficient)
    if ks is not None:
        return ks
    log.warning("  friction law %d has no Nikuradse equivalent; the outlet section takes "
                "ks = 0.1 m", law)
    return 0.1


def outlet_curve(cfg: Config, line, q_max: float):
    """``(Q, Z)`` of one outflow line: measured, or from Manning's normal flow."""
    from axqua.hydraulics import read_rating_pairs
    from axqua.rating import line_section, stage_for_discharge

    if cfg.boundaries.stage_discharge is not None:
        q, z = read_rating_pairs(cfg.boundaries.stage_discharge)
        return q, z
    station, bed, ks, _pts = line_section(cfg, line, ks_fallback=bed_roughness_ks(cfg))
    slope = outlet_slope(cfg, line)
    flows = np.concatenate([[0.0], np.geomspace(q_max / 1000.0, q_max, 30)])
    stages = np.array([stage_for_discharge(q, station=station, bed=bed, ks=ks,
                                           slope=slope) for q in flows])
    stages = np.maximum.accumulate(stages)      # a rating never falls with Q
    log.info("  outlet curve: section %.1f m wide, bed %.3f m, ks %.3g..%.3g m, slope "
             "%.4g; Z(0) %.3f m, Z(%.3g m3/s) %.3f m", float(station.max()),
             float(bed.min()), float(np.min(ks)), float(np.max(ks)), slope, stages[0],
             q_max, stages[-1])
    return flows, stages


def write_outlet_curves(cfg: Config, mesh, liquids, q_max: float) -> Path:
    """TELEMAC's ``STAGE-DISCHARGE CURVES FILE``: one Z(Q) block per outflow line.

    Also writes each curve as ``outlet-rating-<n>.csv`` (Q, WSE) into the preprocessing folder, for the checkup and the report.
    """
    blocks = ["# stage-discharge curves of the outlet, generated by axqua",
              "# (Manning's normal flow through the section of each outflow line,",
              "#  or the measured boundaries.stage_discharge)"]
    for lb in sorted(liquids, key=lambda b: b.index):
        if lb.kind != "outflow":
            continue
        q, z = outlet_curve(cfg, boundary_line(mesh, lb), q_max)
        blocks += ["#", f"Q({lb.index}) Z({lb.index})", "m3/s m"]
        blocks += [f"{a:.6g} {b:.4f}" for a, b in zip(q, z)]
        csv = cfg.preprocessing_path(f"outlet-rating-{lb.index}.csv")
        csv.parent.mkdir(parents=True, exist_ok=True)
        csv.write_text("Q,WSE\n" + "".join(f"{a:.6g},{b:.4f}\n" for a, b in zip(q, z)))
    path = cfg.model_path(cfg.outlet_rating_file)
    path.write_text("\n".join(blocks) + "\n#\n")
    return path


def overfall_level(mesh) -> float:
    """The level of an ``overfall`` outlet: 1 m below the lowest node of the mesh.

    TELEMAC sets the depth of a prescribed level to ``max(0, level - bed)`` at each node of the line (``bord.f``), so a level below every node gives a depth of zero along the whole line. The finite-volume kernel takes that as a dry state outside the cell (``cdl_fv.f``): water leaves as it would over a brink, at critical flow at most, and none can come in, since there is none outside. The finite-element kernel holds the nodes of the line at a depth of zero; on the field of ``example-agrohydrology`` it, too, took no water in and closed the volume to 1e-15.
    """
    return float(np.min(np.asarray(mesh.bottom, dtype=float))) - 1.0


def outlet_rating_csv(cfg: Config) -> Path:
    """The rating of the first outflow line (for the starting level of the outlet)."""
    if cfg.boundaries.stage_discharge is not None:
        return Path(cfg.boundaries.stage_discharge)
    found = sorted(Path(cfg.preprocessing_dir).glob("outlet-rating-*.csv"))
    if not found:
        raise ValueError("the outlet curves have not been built yet")
    return found[0]


def outlet_levels(cfg: Config, liquids) -> dict[int, float]:
    """The level at no discharge of each outflow line on its curve (its thalweg)."""
    from axqua.hydraulics import read_rating_pairs

    out = {}
    for lb in liquids:
        if lb.kind != "outflow":
            continue
        path = (Path(cfg.boundaries.stage_discharge) if cfg.boundaries.stage_discharge
                else cfg.preprocessing_path(f"outlet-rating-{lb.index}.csv"))
        q, z = read_rating_pairs(path)
        out[lb.index] = float(np.interp(0.0, q, z))
    return out


# --------------------------------------------------------------------------- #
# the steering file
# --------------------------------------------------------------------------- #


def steering_block(cfg: Config, liquids, forcing: Forcing) -> list[str]:
    """The keywords a hydrology run adds to the steering file.

    Values that do not fit DAMOCLES' 72 columns go on lines of their own: it reads a keyword's values on as many lines as they take.
    """
    h = cfg.hydrology
    lines = [
        "/",
        "/ HYDROLOGY: rain, Green-Ampt infiltration and evaporation in the",
        "/ generated RUNOFF_GREENAMPT (FORTRAN FILE); soil per node as private",
        "/ variables",
        "RAIN OR EVAPORATION : YES",
        "RAINFALL-RUNOFF MODEL : 3",
        f"FORTRAN FILE : '{cfg.user_fortran_dir}'",
        f"FORMATTED DATA FILE 1 : {forcing.forcing.name}",
        f"NUMBER OF PRIVATE ARRAYS : {len(greenampt.PRIVATE_VARIABLES)}",
        "NAMES OF PRIVATE VARIABLES :",
    ]
    names = [f"'{greenampt.private_name(i)}'"
             for i in range(len(greenampt.PRIVATE_VARIABLES))]
    lines += [name + (";" if k < len(names) - 1 else "") for k, name in enumerate(names)]
    if finite_volumes(cfg):
        lines += [
            # the runoff models stop without tidal flats; the FE block writes it already
            "TIDAL FLATS : YES",
            # Chen and Noelle (2017): the classical reconstruction fails where the
            # water is shallower than the step of the bed between two nodes, which is
            # the sheet flow of a hillslope (TELEMAC's rain_runoff validation case)
            "OPTION OF THE HYDROSTATIC RECONSTRUCTION : 2",
        ]
    if h.wind and forcing.atmosphere is not None:
        lines += [
            "/ wind from the weather series (components at 10 m)",
            "WIND : YES",
            "OPTION FOR WIND : 2",
            f"ASCII ATMOSPHERIC DATA FILE : {forcing.atmosphere.name}",
            f"COEFFICIENT OF WIND INFLUENCE FORMULA : {WIND_FORMULAS[h.wind_formula]}",
            f"THRESHOLD DEPTH FOR WIND : {h.wind_threshold_depth:g}",
        ]
    if cfg.outlet_level == "curve" and any(lb.kind == "outflow" for lb in liquids):
        curves = ["1" if lb.kind == "outflow" else "0"
                  for lb in sorted(liquids, key=lambda b: b.index)]
        lines += [
            "/ outlet on Z(Q): Manning's normal flow through the outlet section",
            f"STAGE-DISCHARGE CURVES : {';'.join(curves)}",
            f"STAGE-DISCHARGE CURVES FILE : {cfg.outlet_rating_file}",
        ]
    return lines


def finite_volumes(cfg: Config) -> bool:
    """Whether the hydrology run uses TELEMAC's finite-volume kernel."""
    return cfg.hydrology.kernel == "finite-volumes"


def printout_steps(cfg: Config) -> tuple[int, int]:
    """Graphic and listing printout periods in time steps.

    TELEMAC counts them in steps of ``TIME STEP``, and with ``VARIABLE TIME-STEP`` both kernels adapt the step so that the printouts land on multiples of that time (``ADAPTATION OF TIME STEP TO GRAPHIC PRINTOUTS``), so the period is exact.
    """
    dt = float(cfg.hydrodynamics.time_step)
    graphic = max(1, int(round(float(cfg.hydrology.printout_period) / dt)))
    listing = max(1, int(round(min(60.0, float(cfg.hydrology.printout_period)) / dt)))
    return graphic, listing


def write_case(cfg: Config, mesh, liquids, inflow, *, turbulence_model: int,
               previous_computation: str | None = None) -> dict[str, Path]:
    """Write everything of the hydrology run: forcing, routine, curves and the .cas.

    *inflow* is the inflow series of the case, or None; with a varying one the inflow lines follow it through the liquid-boundaries file.
    """
    from axqua.solvers.telemac import steering
    from axqua.solvers.telemac.pipeline import _has_hydrograph, _hydrograph_duration

    out: dict[str, Path] = {}
    until = _hydrograph_duration(inflow) if _has_hydrograph(inflow) else None
    forcing = write_forcing(cfg, until=until)
    out["forcing"] = forcing.forcing
    if forcing.atmosphere is not None:
        out["atmosphere"] = forcing.atmosphere
    out["user_fortran"] = greenampt.write_runoff_routine(cfg)
    q_max = design_discharge(cfg, forcing.rain, inflow)
    level = None
    has_outlet = any(lb.kind == "outflow" for lb in liquids)
    if cfg.outlet_level == "curve" and has_outlet:
        out["outlet_curves"] = write_outlet_curves(cfg, mesh, liquids, q_max)
        levels = outlet_levels(cfg, liquids)
        level = min(levels.values()) if levels else None
    elif cfg.outlet_level == "fixed":
        level = float(cfg.boundaries.prescribed_elevation)
    elif cfg.outlet_level == "mesh":
        level = overfall_level(mesh)
        log.info("  outlet overfall: the level of the outflow lines is %.2f m, 1 m below "
                 "the lowest node, so TELEMAC holds no water there", level)
    has_inflow = any(lb.kind == "inflow" for lb in liquids)
    inflow_q = 0.0
    if has_inflow:
        if cfg.boundaries.prescribed_flowrate is not None:
            inflow_q = float(cfg.boundaries.prescribed_flowrate)
        elif inflow is not None:
            inflow_q = float(inflow.discharge[0])
    liquid_file = None
    if _has_hydrograph(inflow):
        out["liquid_boundaries"] = steering.write_liquid_boundaries(
            cfg, liquids, inflow,
            (lambda _q, z=level: z) if cfg.outlet_level in ("fixed", "mesh") else None)
        liquid_file = out["liquid_boundaries"].name
    graphic, listing = printout_steps(cfg)
    out["cas"] = steering.write_cas(
        cfg, liquids, inflow_q, level,
        previous_computation=previous_computation,
        turbulence_model=turbulence_model,
        unsteady=liquid_file is not None, liquid_boundaries_file=liquid_file,
        duration=forcing.duration, out_name=cfg.hydrology_cas_file,
        results_name=cfg.results_hydrology_slf,
        extra_block=steering_block(cfg, liquids, forcing),
        printouts=PRINTOUTS, printout_periods=(graphic, listing),
        regime_label="hydrology", finite_volumes=finite_volumes(cfg))
    log.info("  hydrology case -> %s (%.0f s, maps every %d steps)", out["cas"].name,
             forcing.duration, graphic)
    return out
