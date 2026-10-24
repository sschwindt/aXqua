"""Where the data of a hydrology case come from: national agencies first, then global.

The rain of a catchment is best measured close by: rain gauges and weather radar of the
national meteorological agency resolve the minutes and kilometers a storm has, and the
design storms engineers work with are national products (KOSTRA-DWD in Germany, NOAA
Atlas 14 in the USA). Global data sets fill in where nothing else is at hand, with a
caution: reanalysis spreads a storm over cells of 10 to 30 km and an hour, and
underestimates short intensities.

The list is data. Every link was opened when it was written (2026-10-24); a country is
told from the location of the catchment by coarse bounding boxes, so a catchment near
a border gets the agencies of both countries.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

#: Kinds of data a portal offers.
RAIN, DESIGN, SOIL, LANDCOVER, WEATHER, TERRAIN = (
    "rain", "design storms", "soil", "land cover", "weather", "terrain")


@dataclass(frozen=True)
class Portal:
    name: str
    url: str
    offers: tuple[str, ...]
    note: str = ""
    country: str = ""             # ISO 3166 code; empty for a global data set

    def as_dict(self) -> dict:
        data = asdict(self)
        data["offers"] = list(self.offers)
        return data


#: Coarse bounding boxes (lon min, lat min, lon max, lat max) of the countries listed.
COUNTRIES = {
    "DE": ("Germany", (5.87, 47.27, 15.04, 55.06)),
    "AT": ("Austria", (9.53, 46.37, 17.16, 49.02)),
    "CH": ("Switzerland", (5.96, 45.82, 10.49, 47.81)),
    "FR": ("France", (-5.14, 41.33, 9.56, 51.09)),
    "NL": ("Netherlands", (3.36, 50.75, 7.23, 53.56)),
    "GB": ("United Kingdom", (-8.65, 49.86, 1.77, 60.86)),
    "IT": ("Italy", (6.63, 35.49, 18.52, 47.09)),
    "US": ("United States", (-124.8, 24.4, -66.9, 49.4)),
    "CA": ("Canada", (-141.0, 41.7, -52.6, 83.1)),
    "AU": ("Australia", (112.9, -43.7, 153.6, -10.7)),
    "NZ": ("New Zealand", (166.4, -47.3, 178.6, -34.4)),
}

NATIONAL: tuple[Portal, ...] = (
    Portal("DWD Climate Data Center: hourly precipitation of the stations",
           "https://opendata.dwd.de/climate_environment/CDC/observations_germany/"
           "climate/hourly/precipitation/", (RAIN,),
           "Station records; 10-minute data in the folder 10_minutes.", "DE"),
    Portal("DWD RADOLAN: hourly radar precipitation",
           "https://opendata.dwd.de/climate_environment/CDC/grids_germany/hourly/"
           "radolan/", (RAIN,), "1 km grid, calibrated with the gauges.", "DE"),
    Portal("DWD KOSTRA-DWD-2020: design rainfall depths",
           "https://opendata.dwd.de/climate_environment/CDC/grids_germany/"
           "return_periods/precipitation/KOSTRA/KOSTRA_DWD_2020/", (DESIGN,),
           "Depth per duration and return period; the table the design storm reads.",
           "DE"),
    Portal("Bavarian Environment Agency (LfU): heavy rain and flash floods",
           "https://www.lfu.bayern.de/wasser/starkregen_und_sturzfluten/index.htm",
           (RAIN, SOIL), "Hazard maps and guidance for Bavaria.", "DE"),
    Portal("GeoSphere Austria Data Hub", "https://data.hub.geosphere.at/",
           (RAIN, WEATHER), "Station and gridded data (INCA).", "AT"),
    Portal("eHYD: design rainfall of Austria", "https://ehyd.gv.at/", (DESIGN, RAIN),
           "", "AT"),
    Portal("MeteoSwiss open data", "https://opendatadocs.meteoswiss.ch/",
           (RAIN, WEATHER), "", "CH"),
    Portal("Météo-France public data", "https://meteo.data.gouv.fr/", (RAIN, WEATHER),
           "", "FR"),
    Portal("KNMI Data Platform", "https://dataplatform.knmi.nl/", (RAIN, WEATHER), "",
           "NL"),
    Portal("Met Office MIDAS through the CEDA archive", "https://catalogue.ceda.ac.uk/",
           (RAIN, WEATHER), "Free registration.", "GB"),
    Portal("Flood Estimation Handbook web service", "https://fehweb.ceh.ac.uk/",
           (DESIGN,), "Design rainfall of the UK; licensed.", "GB"),
    Portal("ISPRA SCIA climate data", "https://www.scia.isprambiente.it/",
           (RAIN, WEATHER), "", "IT"),
    Portal("NOAA Atlas 14 Precipitation Frequency Data Server",
           "https://hdsc.nws.noaa.gov/pfds/", (DESIGN,),
           "Depth per duration and return period, as a CSV table.", "US"),
    Portal("NOAA NCEI Climate Data Online", "https://www.ncei.noaa.gov/cdo-web/",
           (RAIN, WEATHER), "Hourly station precipitation.", "US"),
    Portal("NOAA MRMS radar precipitation", "https://mrms.ncep.noaa.gov/", (RAIN,), "",
           "US"),
    Portal("NRCS Web Soil Survey (SSURGO)", "https://websoilsurvey.nrcs.usda.gov/",
           (SOIL,), "Soil maps with texture and Ksat.", "US"),
    Portal("Environment and Climate Change Canada: historical data and IDF files",
           "https://climate.weather.gc.ca/", (RAIN, DESIGN, WEATHER), "", "CA"),
    Portal("Bureau of Meteorology: design rainfalls",
           "https://www.bom.gov.au/water/designRainfalls/", (DESIGN,), "", "AU"),
    Portal("NIWA HIRDS: design rainfall of New Zealand", "https://hirds.niwa.co.nz/",
           (DESIGN,), "", "NZ"),
)

GLOBAL: tuple[Portal, ...] = (
    Portal("Open-Meteo historical weather (ERA5, ERA5-Land)",
           "https://open-meteo.com/en/docs/historical-weather-api",
           (RAIN, WEATHER, SOIL),
           "What the data wizard fetches: hourly rain, wind, radiation, ET0 and soil "
           "moisture, no key. Data CC BY 4.0; the free service is for non-commercial "
           "use. Reanalysis underestimates short rain intensities."),
    Portal("Copernicus Climate Data Store: ERA5-Land",
           "https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land",
           (RAIN, WEATHER, SOIL), "The source of Open-Meteo's archive; free account."),
    Portal("NASA GPM IMERG", "https://gpm.nasa.gov/data/imerg", (RAIN,),
           "Satellite rain every 30 minutes on 0.1 deg; Earthdata login."),
    Portal("CHIRPS", "https://www.chc.ucsb.edu/data/chirps", (RAIN,),
           "Daily rain on 0.05 deg between 50 deg S and 50 deg N; too coarse in time "
           "for a storm, useful for antecedent conditions."),
    Portal("ISRIC SoilGrids", "https://soilgrids.org", (SOIL,),
           "Sand, silt and clay on 250 m, CC BY 4.0; what the data wizard fetches."),
    Portal("HiHydroSoil", "https://www.futurewater.eu/projects/hihydrosoil/", (SOIL,),
           "Global saturated conductivity and water contents on 250 m."),
    Portal("EU-SoilHydroGrids (ESDAC)",
           "https://esdac.jrc.ec.europa.eu/content/3d-soil-hydraulic-database-europe-1-"
           "km-and-250-m-resolution", (SOIL,),
           "Soil hydraulic parameters of Europe on 250 m; registration."),
    Portal("ESA WorldCover", "https://esa-worldcover.org/en", (LANDCOVER,),
           "Land cover on 10 m, CC BY 4.0; its built-up class marks sealed surfaces, "
           "which the data wizard fetches."),
    Portal("Copernicus DEM GLO-30",
           "https://dataspace.copernicus.eu/explore-data/data-collections/"
           "copernicus-contributing-missions/collections-description/COP-DEM",
           (TERRAIN,), "A global terrain model on 30 m, where no lidar model exists."),
)


def countries_at(lon: float, lat: float) -> list[str]:
    """ISO codes of the countries whose box holds the point, smallest box first."""
    hits = []
    for code, (_name, (x0, y0, x1, y1)) in COUNTRIES.items():
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            hits.append(((x1 - x0) * (y1 - y0), code))
    return [code for _area, code in sorted(hits)]


def portals(countries: list[str] | None = None) -> dict:
    """The national portals of *countries*, then the global ones."""
    codes = [c.upper() for c in (countries or [])]
    national = [p.as_dict() | {"country_name": COUNTRIES[p.country][0]}
                for code in codes for p in NATIONAL if p.country == code]
    return {
        "countries": [{"code": c, "name": COUNTRIES.get(c, (c,))[0]} for c in codes],
        "advice": ("Ask your national meteorological agency first: its gauges and "
                   "radar resolve the minutes and kilometers of a storm, and its design "
                   "rainfall is what engineering practice expects. Global data sets "
                   "fill in where nothing else is at hand."),
        "national": national,
        "global": [p.as_dict() for p in GLOBAL],
    }
