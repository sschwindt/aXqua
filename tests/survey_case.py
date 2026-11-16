"""A small built case with a steady result, for the tests of the survey planner.

The reach is a straight channel of 200 m by 60 m on a mesh of 2 m: a parabolic cross section that is dry at both banks, 0.9 m deep in the middle and 1.6 m deep in a pool between x = 140 m and x = 170 m, which cannot be waded. The water flows in the direction of x, the faster the deeper. The inflow is the line x = 0, the outflow the line x = 200 m, and roughness zone 1 is the upstream half, zone 2 the downstream half. No simulation program is needed: the geometry and the result are written with the SELAFIN writers of aXqua.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

X0, Y0, WIDTH, HEIGHT, STEP = 700000.0, 5340000.0, 200.0, 60.0, 2.0
#: A point on the left bank beside the middle of the reach, one at its downstream end.
ACCESS = (X0 + 60.0, Y0 + 1.0)
LEAVE = (X0 + 160.0, Y0 + 1.0)


def channel():
    """``x, y, ikle (1-based), depth, u, bed`` of the channel."""
    xs, ys = np.arange(0.0, WIDTH + STEP / 2, STEP), np.arange(0.0, HEIGHT + STEP / 2, STEP)
    gx, gy = np.meshgrid(xs, ys)
    x, y = gx.ravel(), gy.ravel()
    nx, ny = len(xs), len(ys)
    ikle = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a, b, c, d = j * nx + i, j * nx + i + 1, (j + 1) * nx + i + 1, (j + 1) * nx + i
            ikle += [[a + 1, b + 1, c + 1], [a + 1, c + 1, d + 1]]
    deepest = np.where((x >= 140.0) & (x <= 170.0), 1.6, 0.9)
    depth = np.maximum(deepest * (1.0 - ((y - HEIGHT / 2) / 27.0) ** 2), 0.0)
    u = 1.2 * (depth / 0.9) ** (2.0 / 3.0)
    return X0 + x, Y0 + y, np.array(ikle), depth, u, 400.0 - depth


def write_case(folder: Path, *, calibration: bool = True, result: bool = True, zones: bool = True) -> Path:
    """Write the case file, its zone layer and the built model with its result. Returns the case file."""
    import geopandas as gpd
    from shapely.geometry import Polygon

    from axqua.core import selafin

    folder = Path(folder)
    geo = folder / "geo"
    geo.mkdir(parents=True, exist_ok=True)
    xm = X0 + WIDTH / 2
    left = Polygon([(X0, Y0), (xm, Y0), (xm, Y0 + HEIGHT), (X0, Y0 + HEIGHT)])
    right = Polygon([(xm, Y0), (X0 + WIDTH, Y0), (X0 + WIDTH, Y0 + HEIGHT), (xm, Y0 + HEIGHT)])
    gpd.GeoDataFrame({"Zone ID": [1, 2]}, geometry=[left, right], crs="EPSG:25832").to_file(geo / "roughness-zones.gpkg", driver="GPKG")
    (geo / "roughness-table.csv").write_text("zone_id,ks\n1,0.2\n2,0.5\n")
    case = folder / "survey-test.yml"
    text = f"""
project:
  name: survey-test
  crs_epsg: 25832
telemac:
  pysource: {geo / "roughness-table.csv"}
geodata:
  dem_initial: geo/dem.tif
  boundary: geo/boundary.shp
"""
    if zones:
        text += "  roughness_zones: geo/roughness-zones.gpkg\n  roughness_table: geo/roughness-table.csv\n"
    text += "boundaries:\n  liquid_boundaries: geo/lb.shp\n"
    if calibration:
        text += """calibration:
  parameters:
    - {name: zone1, min: 0.02, max: 0.30}
    - {name: zone2, min: 0.05, max: 0.60}
    - {name: VELOCITY DIFFUSIVITY, min: 0.000001, max: 0.01}
"""
    case.write_text(text)

    from axqua.config import load_config

    cfg = load_config(case)
    model = Path(cfg.model_dir)
    model.mkdir(parents=True, exist_ok=True)
    x, y, ikle, depth, u, bed = channel()
    selafin.write_geometry(model / cfg.geometry_slf, x, y, ikle, np.zeros(x.size, dtype=int), bottom=bed)
    nx = int(round(WIDTH / STEP)) + 1
    ny = x.size // nx
    rows = [f"4 5 5  0 0 0 0  2  0 0 0  {j * nx + 1}  {j + 1}  # inflow" for j in range(ny)]
    rows += [f"5 4 4  0 0 0 0  2  0 0 0  {j * nx + nx}  {len(rows) + j + 1}  # outflow" for j in range(ny)]
    (model / cfg.boundary_cli).write_text("\n".join(rows) + "\n")
    if result:
        selafin.write_initial_state(model / cfg.results_slf, x, y, ikle, np.zeros(x.size, dtype=int), depth, velocity_u=u, velocity_v=np.zeros(x.size), bottom=bed)
    return case


def set_areas(cfg, *polygons) -> Path:
    """Write polygons (lists of ``(x, y)``) into the layer ``walkable_areas`` of the case."""
    import geopandas as gpd
    from shapely.geometry import Polygon

    from axqua.survey import output, planner

    path = planner.gpkg_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = gpd.GeoDataFrame({"name": [f"area {n}" for n in range(1, len(polygons) + 1)]}, geometry=[Polygon(p) for p in polygons], crs=f"EPSG:{cfg.crs_epsg}")
    frame.to_file(path, layer=output.WALKABLE_LAYER, driver="GPKG")
    return path


def set_points(cfg, **roles) -> Path:
    """Write points into the layer ``access_points`` of the case: ``set_points(cfg, access=(x, y), leave=(x, y))``."""
    import geopandas as gpd
    from shapely.geometry import Point

    from axqua.survey import output, planner

    path = planner.gpkg_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = gpd.GeoDataFrame({"role": list(roles), "name": [f"the {role}" for role in roles]}, geometry=[Point(*xy) for xy in roles.values()], crs=f"EPSG:{cfg.crs_epsg}")
    frame.to_file(path, layer=output.ACCESS_LAYER, driver="GPKG")
    return path
