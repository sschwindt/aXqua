"""Tests for stage 0: a CAD assembly becoming the geodata of a case.

The fixture is a miniature fish pass - a sloping bed, two sidewalls, a baffle with a
slot, and inlet/outlet lids - written as STL parts in the test. What is pinned is the
contract with everything downstream: that the artifacts land where the config says the
geodata is, that a case validates *before* they exist (so ``--check`` is usable on a
CAD case), that walls stay out of the DEM and arrive as solid footprints with a crest,
and that the stage does not redo minutes of rasterising when nothing changed.
"""
import numpy as np
import pytest

from axqua.config import Surfaces, SurfacePart, load_config
from axqua import surface_stage

from test_surfaces import write_binary_stl

gpd = pytest.importorskip("geopandas")


# --------------------------------------------------------------------------- #
# a miniature fish pass, in parts
# --------------------------------------------------------------------------- #
def bed_part(length=10.0, width=2.0, slope=0.02, z0=100.0):
    """A bed sloping down along x, as two triangles."""
    corners = [(0.0, 0.0), (length, 0.0), (length, width), (0.0, width)]
    z = {c: z0 - slope * c[0] for c in corners}
    a, b, c, d = corners
    return np.array([[(*a, z[a]), (*b, z[b]), (*c, z[c])],
                     [(*a, z[a]), (*c, z[c]), (*d, z[d])]])


def sidewalls(length=10.0, width=2.0, z0=100.0, height=1.0):
    """Two vertical walls along the channel."""
    out = []
    for y in (0.0, width):
        out += [[(0.0, y, z0 - 1.0), (length, y, z0 - 1.0), (length, y, z0 + height)],
                [(0.0, y, z0 - 1.0), (length, y, z0 + height), (0.0, y, z0 + height)]]
    return np.array(out)


def patch(x, width=2.0, z0=100.0, height=1.0):
    """A vertical lid across the channel: an inlet or outlet patch."""
    return np.array([
        [(x, 0.0, z0 - 1.0), (x, width, z0 - 1.0), (x, width, z0 + height)],
        [(x, 0.0, z0 - 1.0), (x, width, z0 + height), (x, 0.0, z0 + height)]])


def write_case(tmp_path, *, extra_config="", roughness=True):
    """A complete little case: STL parts plus the YAML that names them."""
    cad = tmp_path / "user-sources" / "cad"
    cad.mkdir(parents=True)
    write_binary_stl(cad / "bed.stl", bed_part())
    write_binary_stl(cad / "walls.stl", sidewalls())
    write_binary_stl(cad / "inlet.stl", patch(0.0))
    write_binary_stl(cad / "outlet.stl", patch(10.0))
    write_binary_stl(cad / "air.stl", bed_part(z0=110.0))

    zone = "      zone_id: 1\n      ks: 0.03\n" if roughness else ""
    pysource = tmp_path / "pysource.sh"
    pysource.write_text("# a stand-in for the TELEMAC environment script\n")
    config = tmp_path / "case-config.yml"
    config.write_text(f"""
project:
  name: mini-fishway
  crs_epsg: 25832
telemac:
  pysource: pysource.sh
surfaces:
  resolution: 0.1
  parts:
    - file: user-sources/cad/bed.stl
      role: bed
{zone}    - file: user-sources/cad/walls.stl
      role: wall
    - file: user-sources/cad/inlet.stl
      role: inflow
    - file: user-sources/cad/outlet.stl
      role: outflow
    - file: user-sources/cad/air.stl
      role: ignore
boundaries:
  prescribed_flowrate: 0.135
  outflow_condition: elevation
  prescribed_elevation: 99.9
{extra_config}
""")
    return config


# --------------------------------------------------------------------------- #
# the config contract
# --------------------------------------------------------------------------- #
def test_the_surfaces_block_supplies_the_geodata_a_surveyed_case_would_have(tmp_path):
    cfg = load_config(write_case(tmp_path))

    assert cfg.surfaces.active
    assert cfg.geodata.dem_initial == cfg.preprocessing_path("dem-from-surfaces.tif")
    assert cfg.geodata.boundary == cfg.preprocessing_path("roi-from-surfaces.gpkg")
    assert cfg.geodata.structures == cfg.preprocessing_path(
        "structures-from-surfaces.gpkg")
    assert cfg.boundaries.liquid_boundaries == cfg.preprocessing_path(
        "liquid-boundaries-from-surfaces.gpkg")
    assert cfg.geodata.roughness_zones is not None


def test_a_cad_case_validates_before_its_geodata_exists(tmp_path):
    """`axqua --check` has to work on a case whose DEM is produced by the build - that
    is exactly the case most worth checking before spending the build."""
    cfg = load_config(write_case(tmp_path))

    assert not cfg.geodata.dem_initial.exists()
    cfg.validate()          # must not raise


def test_a_missing_stl_part_is_caught_at_validation(tmp_path):
    config = write_case(tmp_path)
    (tmp_path / "user-sources" / "cad" / "bed.stl").unlink()
    cfg = load_config(config)

    with pytest.raises(FileNotFoundError, match="bed.stl"):
        cfg.validate()


def test_an_explicit_geodata_entry_wins_over_the_derived_one(tmp_path):
    """A case may take its bed from CAD and its roughness zones from QGIS."""
    drawn = tmp_path / "user-sources" / "my-zones.gpkg"
    config = write_case(tmp_path, extra_config=(
        "geodata:\n  roughness_zones: user-sources/my-zones.gpkg\n"))
    cfg = load_config(config)

    assert cfg.geodata.roughness_zones == drawn
    assert "roughness_zones" not in cfg.surfaces_produce()
    assert "dem_initial" in cfg.surfaces_produce()


def test_a_part_with_a_zone_needs_a_roughness_and_the_other_way_round(tmp_path):
    part = SurfacePart(file=tmp_path / "x.stl", role="bed", zone_id=1)
    (tmp_path / "x.stl").write_bytes(b"")
    with pytest.raises(ValueError, match="zone_id and ks go together"):
        part.validate()


def test_the_slope_thresholds_may_not_overlap():
    block = Surfaces(parts=[SurfacePart(file="x.stl")], bed_max_slope_deg=80.0,
                     wall_min_slope_deg=70.0)
    with pytest.raises(ValueError, match="must not be below bed_max_slope_deg"):
        block.validate()


def test_one_zone_may_not_carry_two_roughnesses(tmp_path):
    (tmp_path / "a.stl").write_bytes(b"")
    (tmp_path / "b.stl").write_bytes(b"")
    block = Surfaces(parts=[
        SurfacePart(file=tmp_path / "a.stl", role="bed", zone_id=1, ks=0.03),
        SurfacePart(file=tmp_path / "b.stl", role="bed", zone_id=1, ks=0.08)])
    with pytest.raises(ValueError, match="two different ks values"):
        block.validate()


# --------------------------------------------------------------------------- #
# what the stage writes
# --------------------------------------------------------------------------- #
def test_the_stage_writes_every_artifact_the_case_points_at(tmp_path):
    cfg = load_config(write_case(tmp_path))
    cfg.ensure_dirs()

    produced = surface_stage.run(cfg)

    assert produced.dem == cfg.geodata.dem_initial and produced.dem.exists()
    assert produced.roi == cfg.geodata.boundary and produced.roi.exists()
    assert produced.liquid_boundaries == cfg.boundaries.liquid_boundaries
    assert produced.structures.exists() and produced.roughness_table.exists()
    cfg.validate()          # now for real: every file is there


def test_the_dem_carries_the_bed_and_not_the_walls(tmp_path):
    from axqua.core.raster import sample_raster_at

    cfg = load_config(write_case(tmp_path))
    cfg.ensure_dirs()
    produced = surface_stage.run(cfg)

    # the bed drops 0.02 m/m along x, from 100 m
    z = sample_raster_at(produced.dem, np.array([1.0, 9.0]), np.array([1.0, 1.0]))
    assert z == pytest.approx([99.98, 99.82], abs=0.02)
    import rasterio

    with rasterio.open(produced.dem) as src:
        band = src.read(1)
        valid = band[band != src.nodata]
    assert valid.max() < 100.01          # no wall crest at 101 m in the bed


def test_walls_become_solid_structures_with_their_crest(tmp_path):
    cfg = load_config(write_case(tmp_path))
    cfg.ensure_dirs()
    produced = surface_stage.run(cfg)

    walls = gpd.read_file(produced.structures)
    assert len(walls) == 2                            # one per sidewall
    assert set(walls["Mode"]) == {"solid"}
    assert walls["Crest (m)"].max() == pytest.approx(101.0, abs=0.01)


def test_the_boundary_patches_become_typed_lines_across_the_channel(tmp_path):
    cfg = load_config(write_case(tmp_path))
    cfg.ensure_dirs()
    produced = surface_stage.run(cfg)

    lines = gpd.read_file(produced.liquid_boundaries)
    assert sorted(lines["Type (inflow/outflow)"]) == ["inflow", "outflow"]
    for _, row in lines.iterrows():
        assert row.geometry.length == pytest.approx(2.0, abs=0.05)   # channel width


def test_the_roi_is_one_closed_polygon_covering_the_bed(tmp_path):
    cfg = load_config(write_case(tmp_path))
    cfg.ensure_dirs()
    produced = surface_stage.run(cfg)

    roi = gpd.read_file(produced.roi)
    assert len(roi) == 1
    assert roi.geometry.iloc[0].geom_type == "Polygon"
    assert roi.geometry.iloc[0].area == pytest.approx(20.0, rel=0.05)


def test_a_second_run_reuses_the_artifacts_unless_the_cad_changed(tmp_path):
    """Rasterising several million facets is minutes; repeating it for an unchanged
    part on every build would be paid on every single run of the case."""
    import os
    import time

    cfg = load_config(write_case(tmp_path))
    cfg.ensure_dirs()
    first = surface_stage.run(cfg)
    stamp = first.dem.stat().st_mtime

    assert surface_stage.run(cfg).skipped is True
    assert first.dem.stat().st_mtime == stamp

    later = time.time() + 10
    os.utime(cfg.surfaces.parts[0].file, (later, later))
    assert surface_stage.run(cfg).skipped is False
    assert first.dem.stat().st_mtime != stamp


def test_without_a_transform_the_artifacts_stay_in_local_coordinates(tmp_path):
    """An unreferenced CAD case is a supported way to run; stamping a UTM code on
    drawing coordinates would be a lie QGIS would then act on."""
    cfg = load_config(write_case(tmp_path))
    cfg.ensure_dirs()
    produced = surface_stage.run(cfg)

    assert gpd.read_file(produced.roi).crs is None


def test_a_transform_places_the_case_on_the_map(tmp_path):
    config = write_case(tmp_path, extra_config="")
    text = config.read_text().replace(
        "  resolution: 0.1",
        "  resolution: 0.1\n  dx: 692000.0\n  dy: 5334000.0")
    config.write_text(text)
    cfg = load_config(config)
    cfg.ensure_dirs()

    produced = surface_stage.run(cfg)

    roi = gpd.read_file(produced.roi)
    assert roi.crs.to_epsg() == 25832
    assert roi.total_bounds[0] == pytest.approx(692000.0, abs=0.2)
    assert roi.total_bounds[1] == pytest.approx(5334000.0, abs=0.2)
