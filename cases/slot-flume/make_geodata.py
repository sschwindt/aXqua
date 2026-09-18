"""Generate the geodata for the synthetic vertical-slot flume.

Nothing here is measured: every number is a DESIGN dimension, so the case can be
rebuilt anywhere from this file alone. That is the point of the study - the question
("does our 2D setup over-resist a vertical slot?") must be answerable without any of
the Munich data, so that the answer is about the model and not about one reach.

The geometry, from the Munich VSF design (GitHub issue #3, lww-134):

    clear width      1.150 m
    bed slope        7.87 %
    baffles          14, at 1.650 m pitch, 0.15 m thick
    slot             0.380 m, all on the same side (this design does not alternate)
    discharge        0.135 m3/s
    bed ks           0.08 m

The 13 pitches between the 14 baffles fall 13 x 1.650 x 0.0787 = 1.690 m, which is
the head the structure is designed to shed at this discharge - 0.130 m per pool.

The baffles are NOT burnt into the DEM here. They are written as a structures layer
and applied by `core.structures`, because that is the path munich-vsf itself uses:
the question is whether *our setup* over-resists, and our setup includes how a wall
thinner than an element reaches the bed.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, Polygon

# ---- design ---------------------------------------------------------------- #
CLEAR_WIDTH = 1.150      # [m] between the flume walls
SLOPE = 0.0787           # [-] bed slope
N_BAFFLES = 14
PITCH = 1.650            # [m] baffle spacing
WALL_THICKNESS = 0.15    # [m]
SLOT_WIDTH = 0.380       # [m], against the y = CLEAR_WIDTH side
BAFFLE_HEIGHT = 1.00     # [m] above the local bed - never overtopped at this Q
DISCHARGE = 0.135        # [m3/s]
BED_KS = 0.08            # [m] Nikuradse, the Munich pass bed

LEAD_IN = 2.0            # [m] upstream of the first baffle, for the inflow to develop
LEAD_OUT = 2.0           # [m] downstream of the last, so the exit is not a control
DEM_RES = 0.02           # [m] the synthetic bed raster

# LOCAL coordinates, and deliberately so. The CRS is declared (nothing here is
# reprojected, so it only has to be consistent), but a 25 m flume meshed at 25 mm
# placed at a real UTM origin asks the mesher to resolve 1 part in 2e8: BAMG fails
# outright there ("Fatal error in the meshgenerator 1001") through every rung of its
# retry ladder, on a rectangle. munich-vsf itself runs in local coordinates for the
# same reason, so this also matches the frame of the case the study is about.
X0, Y0 = 0.0, 0.0
Z0 = 3.0                 # [m] bed at the upstream end


def baffle_x(index: int) -> float:
    """Streamwise station of baffle *index*, local coordinates."""
    return LEAD_IN + index * PITCH


LENGTH = baffle_x(N_BAFFLES - 1) + LEAD_OUT
DESIGN_HEAD = (N_BAFFLES - 1) * PITCH * SLOPE      # 1.690 m over 13 pitches
DESIGN_PER_POOL = PITCH * SLOPE                    # 0.130 m


def bed_z(x_local: np.ndarray | float):
    """Bed elevation, sloping uniformly downstream."""
    return Z0 - SLOPE * np.asarray(x_local, dtype=float)


def write_dem(path: Path) -> Path:
    """A plain sloping plane. The baffles arrive as structures, not as terrain."""
    nx = int(np.ceil(LENGTH / DEM_RES)) + 1
    ny = int(np.ceil(CLEAR_WIDTH / DEM_RES)) + 1
    xs = (np.arange(nx) + 0.5) * DEM_RES
    band = np.tile(bed_z(xs), (ny, 1)).astype("float32")
    # north-up: row 0 is the top (max y)
    transform = from_origin(X0, Y0 + ny * DEM_RES, DEM_RES, DEM_RES)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=ny, width=nx, count=1,
                       dtype="float32", crs="EPSG:25832", transform=transform,
                       nodata=-9999.0) as dst:
        dst.write(band, 1)
    return path


def _world(points):
    return [(X0 + x, Y0 + y) for x, y in points]


def write_vectors(folder: Path) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}

    roi = Polygon(_world([(0, 0), (LENGTH, 0), (LENGTH, CLEAR_WIDTH),
                          (0, CLEAR_WIDTH)]))
    out["boundary"] = folder / "roi.gpkg"
    gpd.GeoDataFrame({"name": ["flume"]}, geometry=[roi],
                     crs="EPSG:25832").to_file(out["boundary"], driver="GPKG")

    # The two ends only. The flume sides are ordinary solid wall, which is what every
    # contour node that matches no liquid line becomes.
    out["liquid"] = folder / "liquid-boundaries.gpkg"
    gpd.GeoDataFrame(
        {"Type (inflow/outflow)": ["inflow", "outflow"]},
        geometry=[LineString(_world([(0, 0), (0, CLEAR_WIDTH)])),
                  LineString(_world([(LENGTH, 0), (LENGTH, CLEAR_WIDTH)]))],
        crs="EPSG:25832").to_file(out["liquid"], driver="GPKG")

    # One zone over the whole flume, named `channel` so pre-wetting has a region.
    # `Max Edge Length (m)` is deliberately ABSENT: the sweep drives the cell size
    # through mesh.channel_size, and a value here would override it.
    out["mesh_zones"] = folder / "mesh-zones.gpkg"
    gpd.GeoDataFrame({"Zone Name": ["channel"]}, geometry=[roi],
                     crs="EPSG:25832").to_file(out["mesh_zones"], driver="GPKG")

    out["centerline"] = folder / "centerline.gpkg"
    gpd.GeoDataFrame(
        {"name": ["axis"]},
        geometry=[LineString(_world([(0, CLEAR_WIDTH / 2),
                                     (LENGTH, CLEAR_WIDTH / 2)]))],
        crs="EPSG:25832").to_file(out["centerline"], driver="GPKG")

    out["roughness_zones"] = folder / "roughness-zones.gpkg"
    gpd.GeoDataFrame({"Zone ID": [1]}, geometry=[roi],
                     crs="EPSG:25832").to_file(out["roughness_zones"], driver="GPKG")
    out["roughness_table"] = folder / "roughness-table.csv"
    out["roughness_table"].write_text(f"zone_id,ks\n1,{BED_KS}\n")

    # The baffles. Each is a line across the flume from the y = 0 wall, stopping
    # SLOT_WIDTH short of the far one, buffered to WALL_THICKNESS. It starts slightly
    # outside the domain so the wall is sealed against the side rather than ending on
    # it, which would leave the corner element open.
    geoms, rows = [], []
    for i in range(N_BAFFLES):
        x = baffle_x(i)
        geoms.append(LineString(_world([(x, -0.05),
                                        (x, CLEAR_WIDTH - SLOT_WIDTH)])))
        rows.append({"Name": f"baffle-{i:02d}", "Type": "wall",
                     "Width (m)": WALL_THICKNESS,
                     # a LEVEL crest: the wall top, one metre over its own bed
                     "Crest (m)": float(bed_z(x)) + BAFFLE_HEIGHT})
    out["structures"] = folder / "baffles.gpkg"
    gpd.GeoDataFrame(rows, geometry=geoms,
                     crs="EPSG:25832").to_file(out["structures"], driver="GPKG")
    return out


def main() -> None:
    here = Path(__file__).resolve().parent
    geodata = here / "user-sources" / "geodata"
    dem = write_dem(geodata / "bed.tif")
    paths = write_vectors(geodata)
    print(f"flume: {LENGTH:.3f} m long, {CLEAR_WIDTH:g} m wide, slope {SLOPE:.4f}")
    print(f"  {N_BAFFLES} baffles at {PITCH:g} m, {WALL_THICKNESS:g} m thick, "
          f"{SLOT_WIDTH:g} m slot")
    print(f"  bed {bed_z(0):.3f} -> {bed_z(LENGTH):.3f} m")
    print(f"  design head over {N_BAFFLES - 1} pitches: {DESIGN_HEAD:.3f} m "
          f"({DESIGN_PER_POOL:.4f} m per pool)")
    print(f"  wrote {dem}")
    for key, path in paths.items():
        print(f"  wrote {path}  ({key})")


if __name__ == "__main__":
    main()
