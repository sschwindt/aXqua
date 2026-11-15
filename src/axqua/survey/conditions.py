"""The conditions of a survey day: daylight, weather, and network coverage for the DGPS.

Three things outside the river decide when a survey can run. **Daylight** is computed here from the position and the date (NOAA's solar equations, within about two minutes), so it never depends on a network. The **weather** forecast comes from Open-Meteo (CC BY 4.0) when the day lies within its horizon of about two weeks. The **network** matters because a DGPS rover with real-time kinematic corrections receives them over mobile internet (NTRIP): without mobile coverage on the river there is no centimeter position, and the DGPS is then what takes time.

Nobody publishes one map of mobile coverage for the world. For Switzerland the federal coverage model is queried at the survey position. Elsewhere the report names the national coverage map and the correction services to check, and gives the nearest mobile mast that OpenStreetMap knows as an indication. Every lookup is optional: without a network, or with ``online`` off, the plan is the same and the report says what could not be looked up.

Which country a position lies in is asked from OpenStreetMap, and without a network read from the coarse outlines of ``outlines.py`` (twelve countries, Natural Earth, written by ``scripts/build_country_outlines.py``). Close to a border the report says that the country is a guess and names the neighbor.
"""

from __future__ import annotations

import datetime
import functools
import json
import math
import re
import urllib.parse
import urllib.request

import numpy as np

TIMEOUT = 20
USER_AGENT = "aXqua (https://axqua.eu)"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
#: Overpass servers, asked in this order until one answers.
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter")
#: Seconds one Overpass server gets; the masts are an indication a plan does not wait long for.
OVERPASS_TIMEOUT = 12
SWISS_WMS = "https://wms.geo.admin.ch/"
#: Radius in which mobile masts are looked for in OpenStreetMap [m].
MAST_RADIUS = 5000.0

#: Limits behind the remarks on the weather.
GUST_LIMIT_DRONE = 10.0     # m/s, small camera drones hold their position below this
GUST_LIMIT = 15.0           # m/s, no work in or on the river
RAIN_LIMIT = 2.0            # mm/h

WEATHER_CODES = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog", 51: "light drizzle", 53: "drizzle", 55: "dense drizzle", 56: "freezing drizzle", 57: "freezing drizzle", 61: "slight rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "slight snowfall", 73: "snowfall", 75: "heavy snowfall", 77: "snow grains", 80: "slight rain showers", 81: "rain showers", 82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail"}

#: Where to check mobile coverage and real-time GNSS corrections, by country.
COUNTRIES = {
    "ch": {"name": "Switzerland", "mobile": ("Federal Office of Communications (OFCOM), mobile coverage maps on map.geo.admin.ch", "https://map.geo.admin.ch/"), "rtk": "swipos (swisstopo)"},
    "at": {"name": "Austria", "mobile": ("Breitbandatlas of the Federal Ministry of Finance", "https://breitbandatlas.gv.at/"), "rtk": "APOS (BEV), EPOSA"},
    "nl": {"name": "the Netherlands", "mobile": ("Antenneregister of the Rijksinspectie Digitale Infrastructuur (antenna sites, not coverage)", "https://www.antenneregister.nl/"), "rtk": "NETPOS (Kadaster), 06-GPS"},
    "dk": {"name": "Denmark", "mobile": ("Tjekditnet of the Danish Agency for Digital Government", "https://tjekditnet.dk/"), "rtk": "GPSnet.dk (Geoteam), SmartNet"},
    "cz": {"name": "the Czech Republic", "mobile": ("coverage maps of the Czech Telecommunication Office (CTU)", "https://ctu.gov.cz/"), "rtk": "CZEPOS (CUZK)"},
    "de": {"name": "Germany", "mobile": ("Mobilfunk-Monitoring of the Bundesnetzagentur in the Gigabit-Grundbuch", "https://gigabitgrundbuch.bund.de/GIGA/DE/MobilfunkMonitoring/start.html"), "rtk": "SAPOS of the state surveying authorities (HEPS)"},
    "it": {"name": "Italy", "mobile": ("broadband map of AGCOM", "https://maps.agcom.it/"), "rtk": "the GNSS network of the region, or SmartNet Italy (HxGN SmartNet)"},
    "fr": {"name": "France", "mobile": ("Mon reseau mobile of Arcep", "https://www.arcep.fr/"), "rtk": "Teria, Orpheon, or the open Centipede network"},
    "se": {"name": "Sweden", "mobile": ("Bredbandskartan of the Swedish Post and Telecom Authority (PTS)", "https://bredbandskartan.pts.se/"), "rtk": "SWEPOS (Lantmateriet)"},
    "ca": {"name": "Canada", "mobile": ("National Broadband Internet Service Availability Map of ISED", "https://ised-isde.canada.ca/app/scr/sittibc/web/bbmap"), "rtk": "the provincial and commercial RTK networks listed by Natural Resources Canada (Can-Net, SmartNet)"},
    "us": {"name": "the United States", "mobile": ("National Broadband Map of the FCC (mobile broadband layer)", "https://broadbandmap.fcc.gov/"), "rtk": "the real-time network of the state (NOAA CORS partners), or SmartNet"},
    "cn": {"name": "China", "mobile": ("no public coverage map of the regulator is known to aXqua; ask the mobile operator", ""), "rtk": "Qianxun SI FindCM or a provincial CORS network"},
}
RTK_URLS = {"ch": "https://www.swisstopo.admin.ch/", "at": "https://www.bev.gv.at/Services/Produkte/Grundlagenvermessung/APOS.html", "nl": "https://www.kadaster.nl/", "dk": "https://www.geoteam.dk/", "cz": "https://czepos.cuzk.cz/", "de": "https://sapos.de/", "it": "https://hxgnsmartnet.com/", "fr": "https://centipede-rtk.org/", "se": "https://www.lantmateriet.se/swepos", "ca": "https://webapp.csrs-scrs.nrcan-rncan.gc.ca/geod/data-donnees/rtk.php", "us": "https://geodesy.noaa.gov/CORS/", "cn": "https://www.qxwz.com/"}


def _get(url: str, data: bytes | None = None, timeout: float = TIMEOUT) -> bytes:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:   # noqa: S310
        return response.read()


# --------------------------------------------------------------------------- #
# position and sun
# --------------------------------------------------------------------------- #


def site_lonlat(cfg, flow) -> tuple[float, float]:
    """Longitude and latitude of the middle of the wetted reach."""
    from pyproj import Transformer

    wet = flow.wet if flow.wet.any() else np.ones(len(flow.x), dtype=bool)
    to = Transformer.from_crs(f"EPSG:{cfg.crs_epsg}", "EPSG:4326", always_xy=True)
    lon, lat = to.transform(float(np.median(flow.x[wet])), float(np.median(flow.y[wet])))
    return float(lon), float(lat)


def sun_times(lat: float, lon: float, date: datetime.date, utc_offset_hours: float) -> tuple[float | None, float | None]:
    """Sunrise and sunset in minutes after local midnight (NOAA's equations). ``(None, None)`` where the sun does not rise or set that day."""
    gamma = 2.0 * math.pi / 365.0 * (date.timetuple().tm_yday - 1)
    equation = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma) - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    declination = 0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma) - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    phi = math.radians(lat)
    cosine = math.cos(math.radians(90.833)) / (math.cos(phi) * math.cos(declination)) - math.tan(phi) * math.tan(declination)
    if not -1.0 <= cosine <= 1.0:
        return None, None
    half = math.degrees(math.acos(cosine))
    shift = float(utc_offset_hours) * 60.0
    return 720.0 - 4.0 * (lon + half) - equation + shift, 720.0 - 4.0 * (lon - half) - equation + shift


def computer_utc_offset(date: datetime.date) -> float:
    """The offset of this computer's time zone from UTC on *date*, in hours."""
    offset = datetime.datetime(date.year, date.month, date.day, 12).astimezone().utcoffset()
    return offset.total_seconds() / 3600.0 if offset is not None else 0.0


def _minutes(iso: str) -> float | None:
    match = re.search(r"T(\d{2}):(\d{2})", str(iso))
    return int(match.group(1)) * 60.0 + int(match.group(2)) if match else None


# --------------------------------------------------------------------------- #
# weather
# --------------------------------------------------------------------------- #


def forecast(lat: float, lon: float, date: datetime.date, days: int = 7) -> dict:
    """Open-Meteo's forecast from *date* on. Raises OSError or ValueError where it cannot be had."""
    query = {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "timezone": "auto", "wind_speed_unit": "ms",
        "daily": "sunrise,sunset,weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max",
        "hourly": "temperature_2m,precipitation,precipitation_probability,weather_code,wind_speed_10m,wind_gusts_10m",
        "start_date": date.isoformat(), "end_date": (date + datetime.timedelta(days=max(days, 1) - 1)).isoformat(),
    }
    return json.loads(_get(OPEN_METEO + "?" + urllib.parse.urlencode(query)))


def _weather_days(data: dict) -> list[dict]:
    daily, hourly = data.get("daily") or {}, data.get("hourly") or {}
    days = []
    for i, day in enumerate(daily.get("time") or []):
        pick = lambda key: (daily.get(key) or [None] * (i + 1))[i]  # noqa: E731
        hours = []
        for j, stamp in enumerate(hourly.get("time") or []):
            if not str(stamp).startswith(day):
                continue
            value = lambda key: (hourly.get(key) or [None] * (j + 1))[j]  # noqa: E731
            hours.append({"minute": _minutes(stamp), "temperature": value("temperature_2m"), "precipitation": value("precipitation"), "probability": value("precipitation_probability"), "code": value("weather_code"), "wind": value("wind_speed_10m"), "gusts": value("wind_gusts_10m")})
        code = pick("weather_code")
        days.append({"date": day, "sunrise": _minutes(pick("sunrise")), "sunset": _minutes(pick("sunset")), "code": code, "weather": WEATHER_CODES.get(int(code), "") if code is not None else "", "t_max": pick("temperature_2m_max"), "t_min": pick("temperature_2m_min"), "precipitation": pick("precipitation_sum"), "probability": pick("precipitation_probability_max"), "wind": pick("wind_speed_10m_max"), "gusts": pick("wind_gusts_10m_max"), "hours": hours})
    return days


def bad_hour(hour: dict, *, drone: bool = False) -> str:
    """Why an hour is no hour for a survey, or ''."""
    code, rain, gusts = hour.get("code"), hour.get("precipitation") or 0.0, hour.get("gusts") or 0.0
    if code is not None and int(code) >= 95:
        return "thunderstorm"
    if gusts >= GUST_LIMIT:
        return "storm gusts"
    if rain >= RAIN_LIMIT:
        return "rain"
    if drone and (rain > 0.1 or gusts >= GUST_LIMIT_DRONE):
        return "rain" if rain > 0.1 else "gusts"
    return ""


def suggest_window(day: dict, day_hours: float, *, drone: bool = False) -> dict:
    """The working window of a day: the longest run of daylight hours without bad weather, cut to the working hours."""
    from axqua.survey.schedule import fmt_clock

    sunrise, sunset = day.get("sunrise"), day.get("sunset")
    if sunrise is None or sunset is None:
        return {}
    first = math.ceil(sunrise / 30.0) * 30.0
    last = math.floor(sunset / 30.0) * 30.0
    reason = "from sunrise, within the working hours"
    hours = [h for h in day.get("hours") or [] if h.get("minute") is not None]
    if hours:
        runs, run, causes = [], None, set()
        for hour in hours:
            begin, end = max(hour["minute"], first), min(hour["minute"] + 60.0, last)
            if end <= begin:
                continue
            cause = bad_hour(hour, drone=drone)
            if cause:
                causes.add(cause)
                run = None
                continue
            if run is None:
                run = [begin, end]
                runs.append(run)
            else:
                run[1] = end
        if not runs:
            return {"start": "", "end": "", "reason": f"no hour of daylight without {' or '.join(sorted(causes))} is forecast: choose another day"}
        first, last = max(runs, key=lambda r: r[1] - r[0])
        if causes:
            reason = f"the longest daylight window without {' or '.join(sorted(causes))}"
    end = min(first + float(day_hours) * 60.0, last)
    return {"start": fmt_clock(first), "end": fmt_clock(end), "reason": reason}


# --------------------------------------------------------------------------- #
# country and coverage
# --------------------------------------------------------------------------- #


#: Closer than this to another of the countries, the country of a position is called a guess [degrees, about 10 km].
BORDER = 0.1
#: A position this far outside an outline still belongs to it, because the outlines are simplified [degrees].
OUTLINE_SLACK = {"ca": 0.3, "cn": 0.3, "us": 0.3}
OUTLINE_SLACK_DEFAULT = 0.05


@functools.lru_cache(maxsize=1)
def _country_shapes() -> dict:
    """The outlines of ``outlines.py`` as shapes."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    from .outlines import OUTLINES

    # buffer(0) repairs a ring that the rounding of its coordinates made cross itself
    return {code: unary_union([Polygon(list(zip(ring[0::2], ring[1::2]))).buffer(0) for ring in rings]) for code, rings in OUTLINES.items()}


def _country_distances(lon: float, lat: float) -> dict[str, float]:
    """Degrees from the position to each outline; negative inside, by the distance to its border."""
    from shapely.geometry import Point

    here = Point(float(lon), float(lat))
    out = {}
    for code, shape in _country_shapes().items():
        west, south, east, north = shape.bounds
        if not (west - 1.0 <= lon <= east + 1.0 and south - 1.0 <= lat <= north + 1.0):
            continue
        out[code] = -shape.boundary.distance(here) if shape.contains(here) else shape.distance(here)
    return out


def country_offline(lon: float, lat: float) -> str:
    """The country, of those aXqua names sources for, whose outline holds the position. '' where none does."""
    near = {code: d for code, d in _country_distances(lon, lat).items() if d <= OUTLINE_SLACK.get(code, OUTLINE_SLACK_DEFAULT)}
    return min(near, key=near.get) if near else ""


def countries_nearby(lon: float, lat: float, code: str) -> list[str]:
    """The other countries whose outline lies so close that *code* is a guess."""
    reach = max(BORDER, OUTLINE_SLACK.get(code, OUTLINE_SLACK_DEFAULT))
    return [other for other, d in _country_distances(lon, lat).items() if other != code and d <= reach]


def country_online(lon: float, lat: float) -> tuple[str, str]:
    """``(code, name)`` from OpenStreetMap's Nominatim."""
    data = json.loads(_get(NOMINATIM + "?" + urllib.parse.urlencode({"format": "jsonv2", "lat": f"{lat:.4f}", "lon": f"{lon:.4f}", "zoom": 5, "accept-language": "en"})))
    address = data.get("address") or {}
    return str(address.get("country_code") or "").lower(), str(address.get("country") or "")


def swiss_coverage(lon: float, lat: float) -> dict:
    """The number of operators with outdoor coverage at the position, per generation, from the federal model."""
    out = {}
    d = 0.0005
    for generation in ("4g", "5g"):
        layer = f"ch.bakom.mobilnetz-{generation}"
        query = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo", "LAYERS": layer, "QUERY_LAYERS": layer, "CRS": "EPSG:4326", "BBOX": f"{lat - d},{lon - d},{lat + d},{lon + d}", "WIDTH": 3, "HEIGHT": 3, "I": 1, "J": 1, "INFO_FORMAT": "text/plain"}
        text = _get(SWISS_WMS + "?" + urllib.parse.urlencode(query)).decode("utf-8", "replace")
        match = re.search(r"name\s*=\s*'?(\d+)", text)
        out[generation] = int(match.group(1)) if match else 0
    return out


def nearest_masts(lon: float, lat: float, radius: float = MAST_RADIUS) -> dict:
    """The mobile masts OpenStreetMap knows within *radius*: how many, and how far the nearest one is."""
    around = f"(around:{radius:.0f},{lat:.5f},{lon:.5f})"
    query = f'[out:json][timeout:10];(node["communication:mobile_phone"="yes"]{around};node["tower:type"="communication"]{around};node["man_made"="mast"]["communication:mobile_phone"]{around};);out body 200;'
    data, failure = None, None
    for server in OVERPASS:
        try:
            data = json.loads(_get(server, data=urllib.parse.urlencode({"data": query}).encode(), timeout=OVERPASS_TIMEOUT))
            break
        except (OSError, ValueError) as exc:
            failure = exc
    if data is None:
        raise OSError(_reason(failure))
    best, operators = None, ""
    elements = data.get("elements") or []
    for element in elements:
        if "lat" not in element:
            continue
        dx = math.radians(element["lon"] - lon) * math.cos(math.radians(lat)) * 6371000.0
        dy = math.radians(element["lat"] - lat) * 6371000.0
        distance = math.hypot(dx, dy)
        if best is None or distance < best:
            best, operators = distance, str((element.get("tags") or {}).get("operator") or "")
    return {"count": len(elements), "nearest_m": None if best is None else round(best), "operator": operators, "radius_m": radius}


def coverage(lon: float, lat: float, *, online: bool = True) -> dict:
    """What can be said about mobile coverage and real-time GNSS corrections at the position, with its sources."""
    notes: list[str] = []
    code, name = "", ""
    if online:
        try:
            code, name = country_online(lon, lat)
        except (OSError, ValueError) as exc:
            notes.append(f"the country could not be looked up ({_reason(exc)}); it is taken from the position")
    if not code:
        code = country_offline(lon, lat)
        others = countries_nearby(lon, lat, code) if code else []
        if code and others:
            notes.append(f"the country is a guess from the position, which is close to a border ({', '.join(COUNTRIES[c]['name'] for c in others)} would also be possible); the sources named for the network follow that guess")
    entry = COUNTRIES.get(code)
    out = {"country": code.upper(), "country_name": (entry or {}).get("name") or name, "live": "", "mast": None, "sources": [], "checked": False}
    if entry:
        out["mobile_map"], out["mobile_url"] = entry["mobile"]
        out["rtk"], out["rtk_url"] = entry["rtk"], RTK_URLS.get(code, "")
    else:
        out["mobile_map"], out["mobile_url"] = "the coverage maps of the mobile operators or of the national regulator", ""
        out["rtk"], out["rtk_url"] = "the national or a commercial real-time GNSS network", ""
    if online and code == "ch":
        try:
            found = swiss_coverage(lon, lat)
            out["live"] = f"outdoor coverage by {_many(found['4g'], 'operator')} with 4G and by {found['5g']} with 5G at the middle of the reach (100 m cells of a coverage model, not a measurement)"
            out["operators_4g"], out["operators_5g"] = found["4g"], found["5g"]
            out["checked"] = True
            out["sources"].append("Federal Office of Communications (OFCOM), mobile coverage, via wms.geo.admin.ch")
        except (OSError, ValueError, KeyError) as exc:
            notes.append(f"the Swiss coverage model could not be queried ({_reason(exc)})")
    if online:
        try:
            out["mast"] = nearest_masts(lon, lat)
            out["sources"].append("OpenStreetMap contributors (ODbL), mobile masts via the Overpass API; the map of masts is incomplete")
        except (OSError, ValueError, KeyError) as exc:
            notes.append(f"OpenStreetMap could not be asked for mobile masts ({_reason(exc)})")
    out["notes"] = notes
    out["statement"] = _coverage_statement(out)
    return out


def _many(number, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _coverage_statement(c: dict) -> str:
    where = f" in {c['country_name']}" if c.get("country_name") else ""
    parts = []
    if c.get("live"):
        parts.append(f"Mobile network{where}: {c['live']}.")
    mast = c.get("mast")
    if mast and mast.get("nearest_m") is not None:
        who = f" ({mast['operator']})" if mast.get("operator") else ""
        parts.append(f"OpenStreetMap shows {_many(mast['count'], 'mobile mast')} within {mast['radius_m'] / 1000:g} km, the nearest {mast['nearest_m'] / 1000:.1f} km from the middle of the reach{who}. This is an indication, not a coverage map.")
    elif mast:
        parts.append(f"OpenStreetMap shows no mobile mast within {mast['radius_m'] / 1000:g} km of the reach. Its map of masts is incomplete, but expect weak reception.")
    if not c.get("checked"):
        url = f" ({c['mobile_url']})" if c.get("mobile_url") else ""
        lead = "No coverage map could be queried for this position. " if not parts else ""
        parts.append(f"{lead}The surveyors should check the mobile coverage on the river before the survey: {c['mobile_map']}{url}.")
    url = f" ({c['rtk_url']})" if c.get("rtk_url") else ""
    parts.append(f"Real-time corrections for the DGPS{where}: {c['rtk']}{url}. The rover receives them over mobile internet, so both the correction service and the mobile network must reach the river; narrow valleys and tree cover also shade the satellites.")
    return " ".join(parts)


def _reason(exc: Exception) -> str:
    text = str(getattr(exc, "reason", "") or exc).strip()
    return text[:120] or type(exc).__name__


# --------------------------------------------------------------------------- #
# everything about the day
# --------------------------------------------------------------------------- #


def look_up(cfg, flow, settings, *, online: bool | None = None) -> dict:
    """Daylight, weather and coverage for the plan of *settings*. Never raises for a network that does not answer."""
    from axqua.survey.schedule import fmt_clock

    online = settings.online if online is None else online
    lon, lat = site_lonlat(cfg, flow)
    notes: list[str] = []
    sources: list[str] = []
    out = {"lon": round(lon, 5), "lat": round(lat, 5), "date": settings.time.date, "days": [], "timezone": "", "suggestion": {}, "weather": False}
    date = None
    if settings.time.date:
        try:
            date = datetime.date.fromisoformat(settings.time.date.strip())
        except ValueError:
            notes.append(f"the date {settings.time.date!r} is not of the form YYYY-MM-DD; the plan has no calendar day")
    if date is not None:
        days = []
        if online:
            for span in (7, 1):
                try:
                    data = forecast(lat, lon, date, span)
                    days = _weather_days(data)
                    out["timezone"] = f"{data.get('timezone', '')} (UTC{data.get('utc_offset_seconds', 0) / 3600.0:+g} h)"
                    out["weather"] = True
                    sources.append("Weather forecast, sunrise and sunset: Open-Meteo.com (CC BY 4.0)")
                    break
                except (OSError, ValueError) as exc:
                    reason = _reason(exc)
            if not days:
                ahead = (date - datetime.date.today()).days
                notes.append("there is no weather forecast for this day yet (forecasts reach about two weeks ahead); look it up again closer to the survey" if ahead > 14 else ("the day lies in the past; no forecast" if ahead < 0 else f"the weather forecast could not be fetched ({reason})"))
        if not days:
            offset = computer_utc_offset(date)
            out["timezone"] = f"the time zone of this computer (UTC{offset:+g} h)"
            for i in range(7):
                day = date + datetime.timedelta(days=i)
                sunrise, sunset = sun_times(lat, lon, day, computer_utc_offset(day))
                days.append({"date": day.isoformat(), "sunrise": sunrise, "sunset": sunset, "hours": []})
            sources.append("Sunrise and sunset: computed by aXqua (NOAA solar equations)")
        for day in days:
            day["sunrise_text"] = fmt_clock(day["sunrise"]) if day.get("sunrise") is not None else ""
            day["sunset_text"] = fmt_clock(day["sunset"]) if day.get("sunset") is not None else ""
        out["days"] = days
        out["suggestion"] = suggest_window(days[0], settings.time.day_hours, drone="airborne" in settings.kinds)
    else:
        notes.append("no survey day is set: the plan has no sunrise, sunset or weather, and its times count from the start of the survey")
    cover = coverage(lon, lat, online=online)
    notes += cover.pop("notes")
    sources += cover.pop("sources")
    if not online:
        notes.append("the lookups in the internet are switched off: weather and network coverage were not queried")
    out.update(coverage=cover, notes=notes, sources=sources)
    return out


def sun_of(conditions: dict | None) -> list[dict]:
    """The ``sun`` list :func:`axqua.survey.schedule.day_window` takes."""
    return [{"date": d.get("date"), "sunrise": d.get("sunrise"), "sunset": d.get("sunset")} for d in (conditions or {}).get("days") or []]


__all__ = ["COUNTRIES", "WEATHER_CODES", "bad_hour", "computer_utc_offset", "countries_nearby", "country_offline", "coverage", "forecast", "look_up", "site_lonlat", "suggest_window", "sun_of", "sun_times", "swiss_coverage"]
