"""Tests for reading CAD surfaces and turning them into a height field.

The fixtures are written by the tests themselves - a ramp, a box, a vertical wall, a
lid - because the real inputs are hundred-megabyte CAD exports and the properties worth
pinning are geometric, not file-specific. What is pinned is the part a modeller cannot
check by eye on a million facets: that the rasterised elevation is the *surface* at the
cell centre rather than a nearby vertex, that a wall never contaminates the bed, that
the crest a footprint reports is the top of the wall, and that a multi-valued surface
resolves the way the mode says it does.
"""
import math
import struct

import numpy as np
import pytest

from axqua.core.surfaces import (
    Surface,
    Transform,
    coverage_polygon,
    is_binary_stl,
    patch_line,
    rasterize,
    read_stl,
    report,
    split_by_slope,
    stl_triangle_count,
    wall_footprints,
)


# --------------------------------------------------------------------------- #
# fixtures: STL writers, so the tests exercise the readers on real bytes
# --------------------------------------------------------------------------- #
def write_binary_stl(path, triangles, header=b"axqua test"):
    triangles = np.asarray(triangles, dtype="<f4")
    with open(path, "wb") as handle:
        handle.write(header.ljust(80, b"\0"))
        handle.write(struct.pack("<I", len(triangles)))
        for tri in triangles:
            normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
            length = np.linalg.norm(normal)
            normal = normal / length if length else normal
            handle.write(np.asarray(normal, dtype="<f4").tobytes())
            handle.write(np.asarray(tri, dtype="<f4").tobytes())
            handle.write(struct.pack("<H", 0))
    return path


def write_ascii_stl(path, triangles, name="test"):
    lines = [f"solid {name}"]
    for tri in np.asarray(triangles, dtype=float):
        lines.append("  facet normal 0 0 0")
        lines.append("    outer loop")
        for vertex in tri:
            lines.append("      vertex {:.6f} {:.6f} {:.6f}".format(*vertex))
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {name}")
    path.write_text("\n".join(lines) + "\n")
    return path


def ramp(slope=0.1, size=4.0, z0=100.0):
    """A planar bed over [0, size]^2, rising with x: z = z0 + slope * x."""
    corners = [(0.0, 0.0), (size, 0.0), (size, size), (0.0, size)]
    z = {c: z0 + slope * c[0] for c in corners}
    a, b, c, d = corners
    return np.array([
        [(*a, z[a]), (*b, z[b]), (*c, z[c])],
        [(*a, z[a]), (*c, z[c]), (*d, z[d])],
    ])


def vertical_wall(x=2.0, y0=0.0, y1=4.0, z0=100.0, z1=101.5):
    """A wall standing in the y direction at x, from z0 to z1."""
    return np.array([
        [(x, y0, z0), (x, y1, z0), (x, y1, z1)],
        [(x, y0, z0), (x, y1, z1), (x, y0, z1)],
    ])


def lid(size=4.0, z=102.0):
    """A downward-facing horizontal surface: the overhang case."""
    return np.array([
        [(0.0, 0.0, z), (size, size, z), (size, 0.0, z)],
        [(0.0, 0.0, z), (0.0, size, z), (size, size, z)],
    ])


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def test_binary_and_ascii_stl_read_identically(tmp_path):
    triangles = np.vstack([ramp(), vertical_wall()])
    binary = write_binary_stl(tmp_path / "part.stl", triangles)
    ascii_file = write_ascii_stl(tmp_path / "part-ascii.stl", triangles)

    from_binary = read_stl(binary)
    from_ascii = read_stl(ascii_file)

    assert len(from_binary) == len(from_ascii) == 4
    assert np.allclose(from_binary.triangles, from_ascii.triangles, atol=1e-5)
    assert from_binary.name == "part"


def test_a_binary_header_starting_with_solid_is_still_binary(tmp_path):
    """Several exporters write 'solid ...' into the binary header; the file size,
    not the magic word, is what decides."""
    path = write_binary_stl(tmp_path / "tricky.stl", ramp(), header=b"solid COLOR=1")

    assert is_binary_stl(path) is True
    assert stl_triangle_count(path) == 2
    assert len(read_stl(path)) == 2


def test_the_triangle_count_is_read_without_the_vertices(tmp_path):
    path = write_binary_stl(tmp_path / "part.stl", np.vstack([ramp(), lid()]))
    assert stl_triangle_count(path) == 4


def test_a_truncated_ascii_file_is_rejected(tmp_path):
    path = tmp_path / "broken.stl"
    path.write_text("solid x\n facet normal 0 0 0\n outer loop\n"
                    "  vertex 0 0 0\n  vertex 1 0 0\nendsolid x\n")
    with pytest.raises(ValueError, match="not a whole number of triangles"):
        read_stl(path)


def test_the_triangle_limit_is_enforced_before_reading(tmp_path):
    path = write_binary_stl(tmp_path / "part.stl", np.vstack([ramp(), lid()]))
    with pytest.raises(ValueError, match="over the 3 limit"):
        read_stl(path, max_triangles=3)


def test_normals_come_from_the_winding_not_the_file(tmp_path):
    """A file whose stored normals are zero - which some exporters write - must still
    classify correctly, because every bed/wall decision hangs on the normal."""
    path = write_ascii_stl(tmp_path / "flat.stl", ramp(slope=0.0))
    surface = read_stl(path)

    assert np.allclose(np.abs(surface.normals[:, 2]), 1.0)
    assert np.allclose(surface.slope_deg(), 0.0, atol=1e-6)


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
def test_slope_splits_bed_from_wall_from_overhang():
    surface = Surface(np.vstack([ramp(slope=0.1), vertical_wall(), lid()]))
    groups = split_by_slope(surface)

    assert groups["bed"].sum() == 2        # the ramp, upward facing and shallow
    assert groups["wall"].sum() == 2       # the vertical wall
    assert groups["overhang"].sum() == 2   # the lid, facing down
    assert groups["other"].sum() == 0


def test_an_inverted_part_still_yields_its_horizontal_facets():
    """A closed solid exported inside-out has every top facet classified as overhang.
    The DEM is built from the winding-independent mask for exactly that reason - a part
    whose normals point the wrong way must not silently contribute nothing."""
    flipped = ramp(slope=0.0)[:, ::-1, :]          # reverse the winding
    groups = split_by_slope(Surface(flipped))

    assert groups["bed"].sum() == 0                # nothing reads as upward-facing
    assert groups["overhang"].sum() == 2
    assert groups["horizontal"].sum() == 2         # but the DEM still sees them


def test_a_report_flags_overhangs_as_the_geometry_a_height_field_drops():
    surface = Surface(np.vstack([ramp(), lid()]), name="part")
    result = report(surface)

    assert result["triangles"] == 4
    assert result["overhang_fraction"] == pytest.approx(0.5)
    assert "overhang" in result["message"]
    assert result["size"][0] == pytest.approx(4.0)


# --------------------------------------------------------------------------- #
# rasterisation
# --------------------------------------------------------------------------- #
def test_the_raster_reproduces_the_analytic_plane_at_cell_centres():
    """The value in a cell must be the surface at the cell centre - a nearest-vertex
    or per-triangle-mean approximation would fail this by half a cell of slope."""
    surface = Surface(ramp(slope=0.1, size=4.0, z0=100.0))
    grid, transform = rasterize(surface, resolution=0.25)

    rows, cols = np.indices(grid.shape)
    x = transform.c + (cols + 0.5) * 0.25
    expected = 100.0 + 0.1 * x

    assert grid.shape == (16, 16)
    assert np.isfinite(grid).all()
    assert np.allclose(grid, expected, atol=1e-9)


def test_a_vertical_wall_contributes_nothing_to_the_bed():
    """A wall has no area in plan; letting it into the height field is exactly how a
    bed acquires a spurious ridge."""
    bed = Surface(ramp(slope=0.0, z0=100.0))
    with_wall = Surface(np.vstack([ramp(slope=0.0, z0=100.0), vertical_wall(z1=105.0)]))

    plain, _ = rasterize(bed, resolution=0.5)
    combined, _ = rasterize(with_wall, resolution=0.5)

    assert np.allclose(plain, combined)
    assert combined.max() == pytest.approx(100.0)


def test_mode_decides_which_of_two_stacked_surfaces_wins():
    """A CAD assembly is multi-valued in z; 'max' is the bed seen from above and
    'min' the soffit seen from below."""
    stacked = Surface(np.vstack([ramp(slope=0.0, z0=100.0), lid(z=102.0)]))

    highest, _ = rasterize(stacked, resolution=0.5, mode="max")
    lowest, _ = rasterize(stacked, resolution=0.5, mode="min")

    assert highest.max() == pytest.approx(102.0)
    assert lowest.min() == pytest.approx(100.0)


def test_cells_no_facet_covers_are_nodata_rather_than_zero():
    """Half a domain with no geometry must read as absent, not as a bed at 0 m."""
    half = ramp(slope=0.0, size=2.0, z0=100.0)
    grid, _ = rasterize(Surface(half), resolution=0.5, bounds=(0.0, 0.0, 4.0, 4.0))

    assert (grid == -9999.0).any()
    assert grid[grid != -9999.0].min() == pytest.approx(100.0)
    assert not (grid == 0.0).any()


def test_two_parts_rasterised_apart_share_one_grid():
    """The bed of a real case comes from several STL parts, so the grids have to be
    combinable cell by cell."""
    left = Surface(ramp(slope=0.0, size=2.0, z0=100.0))
    right = Surface(ramp(slope=0.0, size=2.0, z0=101.0).copy())
    right.triangles[:, :, 0] += 2.0
    bounds = (0.0, 0.0, 4.0, 2.0)

    grid_l, transform_l = rasterize(left, 0.5, bounds=bounds)
    grid_r, transform_r = rasterize(right, 0.5, bounds=bounds)

    assert grid_l.shape == grid_r.shape
    assert transform_l == transform_r
    merged = np.where(grid_r != -9999.0, grid_r, grid_l)
    assert merged.min() == pytest.approx(100.0)
    assert merged.max() == pytest.approx(101.0)


def test_the_coverage_polygon_is_the_domain_the_surface_defines():
    grid, transform = rasterize(Surface(ramp(size=4.0)), resolution=0.5,
                                bounds=(0.0, 0.0, 8.0, 4.0))
    polygon = coverage_polygon(grid, transform)

    assert polygon.area == pytest.approx(16.0, rel=0.05)
    assert polygon.contains(__import__("shapely.geometry", fromlist=["Point"])
                            .Point(1.0, 1.0))


def test_writing_a_dem_round_trips_through_rasterio(tmp_path):
    from axqua.core.raster import sample_raster_at
    from axqua.core.surfaces import write_dem

    grid, transform = rasterize(Surface(ramp(slope=0.1)), resolution=0.25)
    path = write_dem(grid, transform, tmp_path / "dem.tif", crs="EPSG:25832")

    z = sample_raster_at(path, np.array([1.0, 3.0]), np.array([2.0, 2.0]))
    assert z == pytest.approx([100.1, 100.3], abs=1e-3)


# --------------------------------------------------------------------------- #
# walls and boundary patches
# --------------------------------------------------------------------------- #
def test_a_wall_becomes_a_footprint_carrying_its_crest():
    surface = Surface(np.vstack([ramp(slope=0.0), vertical_wall(x=2.0, z1=101.5)]))
    footprints = wall_footprints(surface, resolution=0.1)

    assert len(footprints) == 1
    polygon, crest = footprints[0]
    assert crest == pytest.approx(101.5, abs=1e-3)
    assert polygon.bounds[1] == pytest.approx(0.0, abs=0.2)   # spans the y extent
    assert polygon.bounds[3] == pytest.approx(4.0, abs=0.2)
    assert polygon.bounds[2] - polygon.bounds[0] < 0.5        # thin in x


def test_a_surface_with_no_wall_yields_no_footprints():
    assert wall_footprints(Surface(ramp()), resolution=0.1) == []


def test_a_boundary_patch_becomes_a_line_across_the_channel():
    """inlet.stl is a lid across the flow; what the boundary layer wants is the line
    along that cross-section."""
    patch = np.array([
        [(1.0, 0.0, 100.0), (1.0, 3.0, 100.0), (1.0, 3.0, 101.0)],
        [(1.0, 0.0, 100.0), (1.0, 3.0, 101.0), (1.0, 0.0, 101.0)],
    ])
    line = patch_line(Surface(patch))

    assert line.length == pytest.approx(3.0, abs=0.05)
    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    assert x0 == pytest.approx(1.0, abs=0.05)
    assert x1 == pytest.approx(1.0, abs=0.05)
    assert abs(y1 - y0) == pytest.approx(3.0, abs=0.05)


# --------------------------------------------------------------------------- #
# transforms
# --------------------------------------------------------------------------- #
def test_a_transform_places_local_cad_coordinates_on_the_map():
    transform = Transform(scale=0.001, rotation_deg=90.0, dx=692000.0, dy=5334000.0,
                          dz=0.0)
    points = np.array([[1000.0, 0.0, 500.0]])          # 1 m east, 0.5 m up, in mm

    moved = transform.apply(points)

    assert moved[0, 0] == pytest.approx(692000.0, abs=1e-6)   # rotated onto +y
    assert moved[0, 1] == pytest.approx(5334001.0, abs=1e-6)
    assert moved[0, 2] == pytest.approx(0.5)


def test_the_inverse_transform_round_trips():
    transform = Transform(scale=0.001, rotation_deg=37.0, dx=692000.0, dy=5334000.0,
                          dz=-2.5)
    points = np.array([[1234.0, -567.0, 890.0], [0.0, 0.0, 0.0]])

    back = transform.inverse().apply(transform.apply(points))

    assert np.allclose(back, points, atol=1e-6)


def test_transforming_a_surface_moves_its_bounds_but_not_its_shape(tmp_path):
    surface = read_stl(write_binary_stl(tmp_path / "p.stl", ramp(slope=0.1)))
    moved = surface.transformed(Transform(scale=2.0, dx=10.0, dz=5.0))

    xmin, ymin, zmin, xmax, ymax, zmax = moved.bounds
    assert (xmin, ymin) == pytest.approx((10.0, 0.0))
    assert (xmax, ymax) == pytest.approx((18.0, 8.0))
    assert zmin == pytest.approx(205.0)          # 2 * 100 + 5
    assert math.isclose(moved.slope_deg().max(), surface.slope_deg().max(), abs_tol=1e-9)


def test_the_identity_transform_is_recognised_as_such():
    assert Transform().is_identity is True
    assert Transform(dx=1.0).is_identity is False
