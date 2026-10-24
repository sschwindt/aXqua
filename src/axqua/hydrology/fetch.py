"""The downloads of the data wizard: rain and weather, soil moisture, soil, sealing.

All four take what the case already knows - its outline and its CRS - and write files
the case can use as they are, each with a provenance file beside it (source, address,
license, the text an attribution needs). The national agency comes first; these are
the global data sets that cover any catchment, roughly:

* **rain and weather** of an event, hourly, from Open-Meteo's archive of ERA5 and
  ERA5-Land at the centroid of the catchment (no key; data CC BY 4.0; the free service
  is for non-commercial use);
* **soil moisture** at the start of the event, ERA5-Land's layer 0 to 7 cm, as the
  initial water content of Green-Ampt;
* **soil texture** from ISRIC SoilGrids (250 m, CC BY 4.0), sand, silt and clay of
  the top 15 cm, classified to the USDA texture classes and so to Rawls et al. (1983);
* **sealed surfaces** from ESA WorldCover (10 m, CC BY 4.0), its class 50 "built-up".

Network access uses the standard library with timeouts; rasters are read by GDAL.
"""

from __future__ import annotations

import json
import logging
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("axqua")

OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"
SOILGRIDS = "https://maps.isric.org/mapserv"
WORLDCOVER = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
              "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")
TIMEOUT = 60
USER_AGENT = "aXqua (https://axqua.eu)"

#: Open-Meteo's hourly variables and the weather columns of aXqua they fill.
OPEN_METEO_WEATHER = {
    "wind_speed_10m": "wind_speed_10m",
    "wind_direction_10m": "wind_direction_10m",
    "shortwave_radiation": "shortwave_radiation",
    "temperature_2m": "temperature",
    "relative_humidity_2m": "relative_humidity",
    "et0_fao_evapotranspiration": "et0",
}
OPEN_METEO_CREDIT = ("Weather data by Open-Meteo.com (https://open-meteo.com/), "
                     "generated using Copernicus Climate Change Service information "
                     "(ERA5, ERA5-Land).")


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:   # noqa: S310
        return response.read()


def _provenance(path: Path, **fields) -> Path:
    meta = Path(str(path) + ".json")
    fields.setdefault("fetched", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    meta.write_text(json.dumps(fields, indent=2) + "\n")
    return meta


def catchment_lonlat(cfg) -> tuple[float, float]:
    from axqua.solvers.telemac.hydrology import catchment_location

    lat, lon, _ = catchment_location(cfg)
    return lon, lat


def catchment_bounds_lonlat(cfg) -> tuple[float, float, float, float]:
    from pyproj import Transformer

    from axqua.solvers.telemac.mesh import roi_polygon

    minx, miny, maxx, maxy = roi_polygon(cfg).bounds
    to = Transformer.from_crs(f"EPSG:{cfg.crs_epsg}", "EPSG:4326", always_xy=True)
    xs, ys = to.transform([minx, maxx, minx, maxx], [miny, miny, maxy, maxy])
    return min(xs), min(ys), max(xs), max(ys)


# --------------------------------------------------------------------------- #
# rain and weather of an event
# --------------------------------------------------------------------------- #


@dataclass
class Event:
    rainfall: Path
    weather: Path
    origin: str               # calendar time of t = 0, UTC
    total_mm: float
    peak_mm_h: float


def _open_meteo(lon: float, lat: float, start: pd.Timestamp, end: pd.Timestamp,
                variables: list[str]) -> pd.DataFrame:
    query = {"latitude": f"{lat:.5f}", "longitude": f"{lon:.5f}",
             "start_date": start.strftime("%Y-%m-%d"), "end_date": end.strftime("%Y-%m-%d"),
             "hourly": ",".join(variables), "wind_speed_unit": "ms", "timezone": "UTC"}
    url = OPEN_METEO + "?" + urllib.parse.urlencode(query)
    data = json.loads(_get(url))
    if "hourly" not in data:
        raise ValueError(f"Open-Meteo answered without data: {data.get('reason', data)}")
    frame = pd.DataFrame(data["hourly"])
    frame["time"] = pd.to_datetime(frame["time"])
    frame.attrs["url"] = url
    return frame


def fetch_event(cfg, start: str, end: str, out_dir: str | Path) -> Event:
    """Hourly rain and weather of an event at the catchment, from Open-Meteo.

    *start* and *end* are dates and times (UTC). Open-Meteo's hourly values are those
    of the hour that ends at their time stamp, so the first hour written is the one
    starting at *start*. Writes ``rainfall.csv`` and ``weather.csv`` (intervals in
    seconds from *start*) with provenance files.
    """
    t0 = pd.Timestamp(start).floor("h")
    t1 = pd.Timestamp(end).ceil("h")
    if t1 <= t0:
        raise ValueError("the end of the event must be after its start")
    lon, lat = catchment_lonlat(cfg)
    frame = _open_meteo(lon, lat, t0, t1, ["precipitation", *OPEN_METEO_WEATHER])
    ends = frame["time"]
    keep = (ends > t0) & (ends <= t1)
    frame = frame[keep].reset_index(drop=True)
    if frame.empty:
        raise ValueError("Open-Meteo returned no hours for that event (its archive "
                         "lags about five days behind today)")
    end_s = (frame["time"] - t0).dt.total_seconds().to_numpy()
    start_s = end_s - 3600.0
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rain = pd.DataFrame({"start_s": start_s, "end_s": end_s,
                         "rain_mm_h": frame["precipitation"].fillna(0.0).clip(lower=0.0)})
    rain_path = out / "rainfall.csv"
    rain.to_csv(rain_path, index=False, float_format="%.4g")
    meteo = pd.DataFrame({"start_s": start_s, "end_s": end_s})
    for source, column in OPEN_METEO_WEATHER.items():
        meteo[column] = frame[source]
    weather_path = out / "weather.csv"
    meteo.to_csv(weather_path, index=False, float_format="%.5g")
    common = dict(source="Open-Meteo historical weather API (ERA5, ERA5-Land)",
                  url=frame.attrs["url"], license="CC BY 4.0",
                  attribution=OPEN_METEO_CREDIT, origin_utc=t0.isoformat(),
                  latitude=lat, longitude=lon,
                  caution=("Reanalysis spreads a storm over cells of 10 to 30 km and "
                           "an hour and underestimates short intensities."))
    _provenance(rain_path, **common)
    _provenance(weather_path, **common)
    total = float((rain["rain_mm_h"] * (rain["end_s"] - rain["start_s"]) / 3600).sum())
    log.info("fetched %d hours of rain (%.1f mm, peak %.1f mm/h) and weather at "
             "%.4f N %.4f E", len(rain), total, float(rain["rain_mm_h"].max()), lat, lon)
    return Event(rain_path, weather_path, t0.isoformat(), total,
                 float(rain["rain_mm_h"].max()))


def fetch_moisture(cfg, at: str) -> dict:
    """ERA5-Land soil moisture (0 to 7 cm) at the catchment at the hour *at* (UTC)."""
    when = pd.Timestamp(at).floor("h")
    lon, lat = catchment_lonlat(cfg)
    frame = _open_meteo(lon, lat, when, when, ["soil_moisture_0_to_7cm"])
    hit = frame[frame["time"] == when]
    if hit.empty or pd.isna(hit["soil_moisture_0_to_7cm"].iloc[0]):
        raise ValueError(f"Open-Meteo has no soil moisture at {when}")
    value = float(hit["soil_moisture_0_to_7cm"].iloc[0])
    return {"initial_water_content": round(value, 4), "at_utc": when.isoformat(),
            "source": "ERA5-Land soil moisture 0-7 cm through Open-Meteo",
            "url": frame.attrs["url"], "license": "CC BY 4.0",
            "attribution": OPEN_METEO_CREDIT,
            "caution": ("A mean over a cell of about 10 km and the top 7 cm; a field "
                        "measurement of the catchment is better.")}


# --------------------------------------------------------------------------- #
# soil texture (SoilGrids) and sealed surfaces (WorldCover)
# --------------------------------------------------------------------------- #


def _soilgrids_layer(name: str, depth: str, bounds, out: Path) -> Path:
    x0, y0, x1, y1 = bounds
    query = [("map", f"/map/{name}.map"), ("SERVICE", "WCS"), ("VERSION", "2.0.1"),
             ("REQUEST", "GetCoverage"), ("COVERAGEID", f"{name}_{depth}_mean"),
             ("FORMAT", "image/tiff"), ("SUBSET", f"long({x0:.6f},{x1:.6f})"),
             ("SUBSET", f"lat({y0:.6f},{y1:.6f})"),
             ("SUBSETTINGCRS", "http://www.opengis.net/def/crs/EPSG/0/4326"),
             ("OUTPUTCRS", "http://www.opengis.net/def/crs/EPSG/0/4326")]
    path = out / f"soilgrids-{name}-{depth}.tif"
    path.write_bytes(_get(SOILGRIDS + "?" + urllib.parse.urlencode(query)))
    return path


def fetch_soil(cfg, out_dir: str | Path) -> dict:
    """Soil zones by USDA texture class from SoilGrids, with their Rawls parameters.

    Sand, silt and clay of the top 15 cm (0-5 and 5-15 cm averaged) are classified per
    250 m cell, the classes turned into polygons over the catchment
    (``soil-zones.gpkg``, field ``Zone ID``) and ``soil-table.csv`` names the texture
    of each zone, whose values come from Rawls et al. (1983) when the case is built.
    """
    import geopandas as gpd
    import rasterio
    from rasterio.features import shapes
    from shapely.geometry import shape

    from axqua.hydrology import soil
    from axqua.solvers.telemac.mesh import roi_polygon

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    x0, y0, x1, y1 = catchment_bounds_lonlat(cfg)
    pad = 0.003
    bounds = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    stacks = {}
    transform = crs = None
    for name in ("sand", "silt", "clay"):
        layers = []
        for depth in ("0-5cm", "5-15cm"):
            path = _soilgrids_layer(name, depth, bounds, out)
            with rasterio.open(path) as src:
                layers.append(src.read(1).astype(float))
                transform, crs = src.transform, src.crs
            path.unlink()
        stacks[name] = np.mean(layers, axis=0)
    classes = soil.classify_texture(stacks["sand"], stacks["silt"], stacks["clay"])
    valid = classes != ""
    if not valid.any():
        raise ValueError("SoilGrids has no texture over the catchment (water or "
                         "settlement only)")
    names, counts = np.unique(classes[valid], return_counts=True)
    common = names[int(np.argmax(counts))]
    classes[~valid] = common                     # cells without soil take the commonest
    order = sorted(set(classes.ravel().tolist()), key=lambda n: list(soil.RAWLS_1983).index(
        soil.texture(n).name) if soil.texture(n) else 99)
    ids = {name: k + 1 for k, name in enumerate(order)}
    grid = np.vectorize(ids.get)(classes).astype("int32")
    polys, values = [], []
    for geom, value in shapes(grid, transform=transform):
        polys.append(shape(geom))
        values.append(int(value))
    layer = gpd.GeoDataFrame({"Zone ID": values}, geometry=polys, crs=crs)
    layer = layer.to_crs(epsg=cfg.crs_epsg).dissolve(by="Zone ID").reset_index()
    outline = roi_polygon(cfg).buffer(50.0)
    layer["geometry"] = layer.geometry.intersection(outline)
    layer = layer[~layer.geometry.is_empty]
    zones_path = out / "soil-zones.gpkg"
    layer.to_file(zones_path)
    table = pd.DataFrame({"zone_id": [ids[n] for n in order],
                          "texture": [soil.texture(n).name if soil.texture(n) else n
                                      for n in order],
                          "ks_mm_h": "", "suction_m": "", "theta_s": "", "theta_i": ""})
    table_path = out / "soil-table.csv"
    table.to_csv(table_path, index=False)
    meta = dict(source="ISRIC SoilGrids 2.0, sand/silt/clay 0-5 and 5-15 cm, mean",
                url="https://soilgrids.org", license="CC BY 4.0",
                attribution=("Poggio, L., et al. (2021). SoilGrids 2.0. SOIL 7, "
                             "217-240. https://doi.org/10.5194/soil-7-217-2021"),
                caution="250 m cells; a soil map of the agency is more detailed.")
    _provenance(zones_path, **meta)
    _provenance(table_path, **meta)
    shares = {soil.texture(n).name if soil.texture(n) else n:
              round(float((classes == n).mean()), 3) for n in order}
    return {"soil_zones": str(zones_path), "soil_table": str(table_path),
            "textures": shares}


def worldcover_tiles(bounds) -> list[str]:
    """Names of the 3 x 3 deg WorldCover tiles a box in degrees touches."""
    x0, y0, x1, y1 = bounds
    tiles = []
    for lat in range(int(math.floor(y0 / 3) * 3), int(math.floor(y1 / 3) * 3) + 1, 3):
        for lon in range(int(math.floor(x0 / 3) * 3), int(math.floor(x1 / 3) * 3) + 1, 3):
            tiles.append(f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"
                         f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}")
    return tiles


def fetch_sealed(cfg, out_dir: str | Path, *, classes: tuple[int, ...] = (50,)) -> dict:
    """Sealed surfaces from ESA WorldCover: its class 50 "built-up" as polygons."""
    import geopandas as gpd
    import rasterio
    from rasterio.features import shapes
    from rasterio.windows import from_bounds
    from shapely.geometry import shape

    from axqua.solvers.telemac.mesh import roi_polygon

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bounds = catchment_bounds_lonlat(cfg)
    polys = []
    crs = None
    for tile in worldcover_tiles(bounds):
        url = "/vsicurl/" + WORLDCOVER.format(tile=tile)
        try:
            with rasterio.open(url) as src:
                window = from_bounds(*bounds, transform=src.transform).round_offsets() \
                    .round_lengths()
                data = src.read(1, window=window, boundless=True, fill_value=0)
                transform = src.window_transform(window)
                crs = src.crs
        except rasterio.errors.RasterioIOError as exc:
            log.warning("WorldCover tile %s unavailable: %s", tile, exc)
            continue
        built = np.isin(data, classes).astype("uint8")
        for geom, value in shapes(built, mask=built.astype(bool), transform=transform):
            polys.append(shape(geom))
    path = out / "sealed-surfaces.gpkg"
    if not polys:
        gpd.GeoDataFrame(geometry=[], crs=f"EPSG:{cfg.crs_epsg}").to_file(path)
        area = 0.0
    else:
        layer = gpd.GeoDataFrame(geometry=polys, crs=crs).to_crs(epsg=cfg.crs_epsg)
        layer["geometry"] = layer.geometry.intersection(roi_polygon(cfg))
        layer = layer[~layer.geometry.is_empty]
        layer.to_file(path)
        area = float(layer.area.sum())
    _provenance(path, source="ESA WorldCover 10 m 2021 v200, class 50 built-up",
                url="https://esa-worldcover.org", license="CC BY 4.0",
                attribution=("ESA WorldCover project 2021 / Contains modified "
                             "Copernicus Sentinel data (2021) processed by ESA "
                             "WorldCover consortium."),
                caution=("Built-up covers roofs, roads and yards alike; drained roofs "
                         "do not run off over the ground."))
    total = roi_polygon(cfg).area
    return {"sealed_surfaces": str(path), "sealed_m2": area,
            "sealed_share": area / total if total else 0.0}
