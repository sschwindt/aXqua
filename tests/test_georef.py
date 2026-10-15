"""Tests for recovering the CAD-to-map transform from a drawing.

The fixture is a structure drawn twice: once as an STL in millimetres about the origin,
once as a DXF in metres on the national grid. What is pinned is that the recovered
transform actually lands the STL on the drawing, and - just as important - that the
cases a bounding box *cannot* resolve are reported rather than guessed: a footprint
whose shape does not match, and the rotation ambiguity every rectangle has.
"""
import numpy as np
import pytest

from axqua import georef
from axqua.core.surfaces import read_stl

from test_surfaces import write_binary_stl

gpd = pytest.importorskip("geopandas")
pytest.importorskip("shapely")


def write_dxf(path, *, x0, y0, width, height, layer="STRUCTURE"):
    """A rectangle on the national grid, written as a DXF via GDAL's DXF driver."""
    from shapely.geometry import Polygon

    frame = gpd.GeoDataFrame(
        {"Layer": [layer]},
        geometry=[Polygon([(x0, y0), (x0 + width, y0), (x0 + width, y0 + height),
                           (x0, y0 + height)])],
        crs="EPSG:25832")
    frame.to_file(path, driver="DXF")
    return path


def cad_block(width_mm, height_mm, z=0.0):
    """The same rectangle as an STL, in millimetres, cornered at the origin."""
    return np.array([
        [(0.0, 0.0, z), (width_mm, 0.0, z), (width_mm, height_mm, z)],
        [(0.0, 0.0, z), (width_mm, height_mm, z), (0.0, height_mm, z)]])


def test_a_drawing_in_metres_and_a_part_in_millimetres_give_the_scale(tmp_path):
    stl = write_binary_stl(tmp_path / "part.stl", cad_block(30000.0, 6000.0))
    dxf = write_dxf(tmp_path / "plan.dxf", x0=692000.0, y0=5334000.0,
                    width=30.0, height=6.0)

    result = georef.match(stl, dxf)

    assert result.verdict == "match"
    assert result.transform.scale == pytest.approx(0.001)
    assert "millimetres" in result.message


def test_the_recovered_transform_lands_the_part_on_the_drawing(tmp_path):
    """The point of the exercise: applying it must put the CAD footprint where the
    drawing says the structure is."""
    stl = write_binary_stl(tmp_path / "part.stl", cad_block(30000.0, 6000.0))
    dxf = write_dxf(tmp_path / "plan.dxf", x0=692000.0, y0=5334000.0,
                    width=30.0, height=6.0)

    result = georef.match(stl, dxf, rotation_deg=0.0)
    moved = read_stl(stl).transformed(result.transform)

    xmin, ymin, _, xmax, ymax, _ = moved.bounds
    assert (xmin, ymin) == pytest.approx((692000.0, 5334000.0), abs=1e-3)
    assert (xmax, ymax) == pytest.approx((692030.0, 5334006.0), abs=1e-3)


def test_a_metre_drawing_and_a_metre_part_need_no_rescaling(tmp_path):
    stl = write_binary_stl(tmp_path / "part.stl", cad_block(30.0, 6.0))
    dxf = write_dxf(tmp_path / "plan.dxf", x0=692000.0, y0=5334000.0,
                    width=30.0, height=6.0)

    result = georef.match(stl, dxf, rotation_deg=0.0)
    moved = read_stl(stl).transformed(result.transform)

    assert result.transform.scale == pytest.approx(1.0)
    assert moved.bounds[0] == pytest.approx(692000.0, abs=1e-3)
    assert moved.bounds[3] == pytest.approx(692030.0, abs=1e-3)


def test_the_rotation_ambiguity_of_a_rectangle_is_reported_not_guessed(tmp_path):
    """A bounding box cannot tell 0 from 180 degrees. Saying so is the honest answer;
    picking one silently produces a plausible, wrong model."""
    stl = write_binary_stl(tmp_path / "part.stl", cad_block(30000.0, 6000.0))
    dxf = write_dxf(tmp_path / "plan.dxf", x0=692000.0, y0=5334000.0,
                    width=30.0, height=6.0)

    result = georef.match(stl, dxf)

    assert result.rotation_candidates == [0.0, 180.0]
    assert "cannot tell 0 from 180" in result.recommendation


def test_a_quarter_turn_shows_up_as_a_swapped_aspect(tmp_path):
    stl = write_binary_stl(tmp_path / "part.stl", cad_block(30000.0, 6000.0))
    dxf = write_dxf(tmp_path / "plan.dxf", x0=692000.0, y0=5334000.0,
                    width=6.0, height=30.0)

    result = georef.match(stl, dxf)

    assert result.rotation_candidates == [90.0, 270.0]
    moved = read_stl(stl).transformed(result.transform)
    xmin, ymin, _, xmax, ymax, _ = moved.bounds
    assert (xmax - xmin) == pytest.approx(6.0, abs=1e-3)
    assert (ymax - ymin) == pytest.approx(30.0, abs=1e-3)


def test_footprints_of_different_shape_are_refused(tmp_path):
    """Usually means the drawing extent is the whole sheet rather than the structure."""
    stl = write_binary_stl(tmp_path / "part.stl", cad_block(30000.0, 6000.0))
    dxf = write_dxf(tmp_path / "plan.dxf", x0=692000.0, y0=5334000.0,
                    width=40.0, height=40.0)

    result = georef.match(stl, dxf)

    assert result.verdict == "shape_mismatch"
    assert "--layer" in result.message
    assert "local coordinates" in result.recommendation


def test_a_layer_filter_reduces_the_drawing_to_the_structure(tmp_path):
    """A real drawing carries a title block and a north arrow; the extent has to be
    the structure's own layer or the scale comes out of the sheet size."""
    from shapely.geometry import Polygon

    frame = gpd.GeoDataFrame(
        {"Layer": ["STRUCTURE", "TITLEBLOCK"]},
        geometry=[Polygon([(692000, 5334000), (692030, 5334000),
                           (692030, 5334006), (692000, 5334006)]),
                  Polygon([(691000, 5333000), (693000, 5333000),
                           (693000, 5335000), (691000, 5335000)])],
        crs="EPSG:25832")
    dxf = tmp_path / "plan.dxf"
    frame.to_file(dxf, driver="DXF")
    stl = write_binary_stl(tmp_path / "part.stl", cad_block(30000.0, 6000.0))

    assert georef.match(stl, dxf).verdict == "shape_mismatch"

    filtered = georef.match(stl, dxf, layers=["STRUCTURE"])
    assert filtered.verdict == "match"
    assert filtered.transform.scale == pytest.approx(0.001)


def test_the_layers_of_a_drawing_can_be_listed(tmp_path):
    from shapely.geometry import Point

    frame = gpd.GeoDataFrame({"Layer": ["A", "B", "B"]},
                             geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
                             crs="EPSG:25832")
    dxf = tmp_path / "plan.dxf"
    frame.to_file(dxf, driver="DXF")

    assert dict(georef.dxf_layers(dxf)) == {"A": 1, "B": 2}


def test_the_proposed_block_is_config_ready(tmp_path):
    import yaml

    stl = write_binary_stl(tmp_path / "part.stl", cad_block(30000.0, 6000.0))
    dxf = write_dxf(tmp_path / "plan.dxf", x0=692000.0, y0=5334000.0,
                    width=30.0, height=6.0)

    snippet = georef.match(stl, dxf, rotation_deg=0.0).as_yaml()
    parsed = yaml.safe_load(snippet)

    assert parsed["surfaces"]["scale"] == pytest.approx(0.001)
    assert parsed["surfaces"]["dx"] == pytest.approx(692000.0, abs=1e-3)


def test_a_plot_scale_drawing_is_flagged_rather_than_accepted(tmp_path):
    """A ratio that is not a unit conversion means the two extents are not the same
    thing - that has to be seen, not absorbed into a scale factor."""
    stl = write_binary_stl(tmp_path / "part.stl", cad_block(30000.0, 6000.0))
    dxf = write_dxf(tmp_path / "plan.dxf", x0=692000.0, y0=5334000.0,
                    width=7.5, height=1.5)

    result = georef.match(stl, dxf)

    assert result.verdict == "scale_unclear"
    assert "not within 2%" in result.message


# --------------------------------------------------------------------------- #
# control points - the path for a drawing sheet whose extent is not the structure
# --------------------------------------------------------------------------- #
def test_two_control_points_fix_the_transform_exactly():
    """A construction sheet carries plans, sections and a title block, so its bounding
    box is not the structure's. Two identified features settle it instead."""
    from axqua.core.surfaces import Transform

    truth = Transform(scale=1.0, rotation_deg=37.0, dx=4473100.0, dy=5332080.0)
    cad = np.array([[2.81, 19.22], [26.69, 106.77]])
    world = truth.apply(np.column_stack([cad, np.zeros(2)]))[:, :2]

    fitted = Transform.from_control_points(cad, world)

    assert fitted.scale == pytest.approx(1.0, abs=1e-9)
    assert fitted.rotation_deg == pytest.approx(37.0, abs=1e-6)
    assert fitted.dx == pytest.approx(4473100.0, abs=1e-3)
    assert fitted.residuals(cad, world).max() < 1e-6


def test_a_mis_picked_third_point_shows_up_as_a_residual():
    """Two points always fit exactly; a third is what reveals a wrong pick."""
    from axqua.core.surfaces import Transform

    truth = Transform(scale=0.001, rotation_deg=-12.0, dx=4473000.0, dy=5332000.0)
    cad = np.array([[0.0, 0.0], [30000.0, 0.0], [0.0, 6000.0]])
    world = truth.apply(np.column_stack([cad, np.zeros(3)]))[:, :2]
    world[2] += [0.4, -0.3]                      # the third point picked wrongly

    fitted = Transform.from_control_points(cad, world)

    assert fitted.residuals(cad, world).max() > 0.1


def test_control_points_are_parsed_from_the_command_line_spelling():
    cad, world = georef.parse_control_point("12.5,40.0:4473100.2,5332090.7")

    assert cad == (12.5, 40.0)
    assert world == (4473100.2, 5332090.7)
    with pytest.raises(ValueError, match="CADX,CADY:MAPX,MAPY"):
        georef.parse_control_point("12.5 40.0 4473100.2")


def test_a_single_control_point_is_refused():
    from axqua.core.surfaces import Transform

    with pytest.raises(ValueError, match="at least two"):
        Transform.from_control_points([[0.0, 0.0]], [[1.0, 1.0]])


def test_the_fit_never_mirrors_the_geometry():
    """A reflected fit would place the left bank on the right and still look plausible."""
    from axqua.core.surfaces import Transform

    cad = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0]])
    world = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, -10.0]])   # mirrored in y

    fitted = Transform.from_control_points(cad, world)

    assert fitted.scale > 0
    assert fitted.residuals(cad, world).max() > 1.0     # refuses to fit it exactly
