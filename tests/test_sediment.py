"""Fine sediment over a fixed bed (:mod:`axqua.solvers.openfoam.sediment`).

No OpenFOAM and no solver: the meshes are small lattices built the way the mesher builds
them, and a result is three field files written by hand. What is pinned here is what
``sediDriftFoam2`` does not check itself and what a run on a real reach showed:

* the **mesh rules** the solver relies on, re-implemented from its source
  (``check_columns``), and that a mesh breaking one of them is refused;
* the **height of the first cell center above the bed**, which is negative on a sloping
  bed with layers of varying thickness and ended the first run on the Isar with a
  floating point exception, and what the bottom layer of one thickness does about it;
* the **formulas** of shear stress and pick-up, which were compared with the output of a
  copy of the solver that writes its source term (median difference 3e-6 for both of
  its transport formulas);
* the **budget**: what enters the reach is what leaves it plus what the bed takes up.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pytest

from axqua.config import FineSediment, load_config
from axqua.core import errors
from axqua.core.capabilities import Capability
from axqua.solvers.openfoam import mesh as ofmesh
from axqua.solvers.openfoam import polymesh, sediment
from test_openfoam import _plan_grid


# --------------------------------------------------------------------------- #
# a small channel, built as build_mesh builds one
# --------------------------------------------------------------------------- #


def _channel(nx=8, ny=3, dx=1.0, n_layers=4, *, bed=None, depth=None):
    """An ``OpenFoamMesh`` of a straight channel along x, inflow at x = 0."""
    grid = _plan_grid(nx, ny, dx)
    x, y = grid.vert_xy[:, 0], grid.vert_xy[:, 1]
    zb = bed(x, y) if bed is not None else np.zeros_like(x)
    lid = zb + (depth(x, y) if depth is not None else np.full_like(x, 0.4))
    (points, internal, bed_face, top_face, sides, z, centres, column,
     layer) = ofmesh.extrude(grid, zb, lid, n_layers)
    middle = sides["midpoint"]
    west, east = np.isclose(middle[:, 0], 0.0), np.isclose(middle[:, 0], nx * dx)
    bank = ~(west | east)
    boundary = [
        ("bed", "wall", bed_face[1], bed_face[0]),
        ("inlet-1", "patch", sides["owner"][west], sides["quads"][west]),
        ("outlet-1", "patch", sides["owner"][east], sides["quads"][east]),
        ("banks", "wall", sides["owner"][bank], sides["quads"][bank]),
        ("lid", "wall", top_face[1], top_face[0]),
    ]
    mesh = polymesh.assemble(points, internal, boundary, centres)
    assert polymesh.validate(mesh) == []
    columns = grid.n_columns
    return ofmesh.OpenFoamMesh(
        polymesh=mesh, grid=grid, n_layers=n_layers, z=z, bed=zb, lid=lid,
        cell_centres=centres, cell_column=column, cell_layer=layer,
        column_bed=np.zeros(columns), column_uv=np.tile([0.5, 0.0], (columns, 1)),
        bed_ks=np.full(columns, 0.05), rigid_lid=True,
        lid_step_median=0.02, lid_step_p99=0.1,
        inlet_patches=["inlet-1"], outlet_patches=["outlet-1"],
        inlet_discharge={"inlet-1": 0.6}, notes=["a note of the mesher"])


def _for_the_solver(mesh, bottom=None):
    """*mesh* as the sediment case writes it: bottom layer pinned, patches renamed."""
    if bottom is not None:
        sediment.pin_bottom_layer(mesh, bottom)
    sediment.rename_patches(mesh.polymesh)
    return mesh


class _State:
    """The two scales the turbulence fields are started from."""

    source = Path("r2d.slf")

    def velocity_scale(self, wet_depth=0.01):
        return 0.5

    def depth_scale(self, wet_depth=0.01):
        return 0.4


def _case(tmp_path, **fine) -> Path:
    settings = {"enabled": True, "grain_size": 0.0001, "inflow_concentration": 0.05,
                "pick_up": "off", "iterations": 50, **fine}
    path = tmp_path / "reach.axq-case"
    path.write_text(
        "project: {name: reach, crs_epsg: 25832}\n"
        "geodata: {dem_initial: dem.tif, boundary: outline.gpkg}\n"
        "boundaries: {prescribed_flowrate: 0.6}\n"
        "openfoam: {min_water_depth: 0.2}\n"
        f"fine_sediment: {json.dumps(settings)}\n", encoding="utf-8")
    return path


@pytest.fixture
def built(tmp_path, monkeypatch):
    """A fine sediment case of the small channel, without the geodata of a reach."""
    monkeypatch.setattr(ofmesh, "build_mesh", lambda cfg, state=None: _channel())
    monkeypatch.setattr("axqua.core.capabilities.refresh_markers", lambda cfg: [])
    cfg = load_config(_case(tmp_path))
    return cfg, sediment.build_case(cfg, state=_State())


# --------------------------------------------------------------------------- #
# the sediment
# --------------------------------------------------------------------------- #


def test_the_fall_velocity_is_that_of_the_solvers_own_example():
    """Olsen's example carries 2 mm grains at 0.2 m/s. Silt follows Stokes, and a
    coarser grain never settles more slowly than a finer one - which the formulas of
    van Rijn do at 0.1 mm, where they step down by 13 %."""
    assert sediment.fall_velocity(0.002) == pytest.approx(0.2, rel=0.03)
    stokes = 1.65 * 9.81 * 0.00001 ** 2 / 18e-6
    assert sediment.fall_velocity(0.00001) == pytest.approx(stokes, rel=0.02)
    assert sediment.fall_velocity(0.0002) == pytest.approx(0.0257, rel=0.12)   # van Rijn
    sizes = np.geomspace(1e-6, 2e-3, 200)
    assert (np.diff([sediment.fall_velocity(d) for d in sizes]) > 0).all()


def test_a_given_fall_velocity_replaces_the_formula(tmp_path):
    cfg = load_config(_case(tmp_path, fall_velocity=0.004))
    assert sediment.settling_velocity(cfg) == 0.004
    cfg.fine_sediment.fall_velocity = None
    assert sediment.settling_velocity(cfg) == sediment.fall_velocity(0.0001)


@pytest.mark.parametrize("given, expected", [
    (0.02, {"inlet-1": 0.02, "inlet-2": 0.02}),
    ({"inlet-1": 0.02, "inlet-2": 0}, {"inlet-1": 0.02, "inlet-2": 0.0}),
    ({"1": 0.02, "2": 0.01}, {"inlet-1": 0.02, "inlet-2": 0.01}),
])
def test_the_concentration_is_one_number_or_one_per_inflow_line(tmp_path, given, expected):
    cfg = load_config(_case(tmp_path, inflow_concentration=given))
    assert sediment.inflow_concentrations(cfg, ["inlet-1", "inlet-2"]) == expected


def test_a_line_without_a_concentration_is_not_given_clear_water(tmp_path):
    """Leaving a line out would mean clear water there without anybody having said so."""
    cfg = load_config(_case(tmp_path, inflow_concentration={"inlet-1": 0.02, "side": 0.1}))
    with pytest.raises(errors.ConfigError) as caught:
        sediment.inflow_concentrations(cfg, ["inlet-1", "inlet-2"])
    assert "no inflow line is called side" in str(caught.value)
    assert "no value for inlet-2" in str(caught.value)
    assert "inlet-1, inlet-2" in caught.value.remedy


def test_what_a_form_hands_over_is_read_as_it_was_typed(tmp_path):
    """The case editor writes the text of a line edit: a number stays a number there,
    and a mapping arrives as text."""
    cfg = load_config(_case(tmp_path, inflow_concentration="{inlet-1: 0.02, inlet-2: 0}"))
    assert cfg.fine_sediment.inflow_concentration == {"inlet-1": 0.02, "inlet-2": 0}
    cfg.fine_sediment.validate()


@pytest.mark.parametrize("change, said", [
    ({"inflow_concentration": 250}, "20 mg/l are 0.02"),       # typed in mg/l
    ({"grain_size": 0.2}, "given in meters"),                  # typed in mm
    ({"pick_up": "yes"}, "'suspended', 'bedload' or 'off'"),
    ({"porosity": 30}, "between 0 and 1"),
    ({"initial_fraction": 1.0}, "less than 1"),
    ({"bottom_layer": 0}, "larger than 0"),
])
def test_values_in_another_unit_are_named(change, said):
    with pytest.raises(ValueError, match=said):
        FineSediment(enabled=True, **change).validate()


def test_the_word_off_is_a_choice_and_not_a_no(tmp_path):
    """YAML reads an unquoted ``off`` as a boolean. Written the natural way, the choice
    was refused with "must be 'suspended', 'bedload' or 'off', got False"."""
    path = tmp_path / "plain.axq-case"
    path.write_text("project: {name: plain, crs_epsg: 25832}\n"
                    "geodata: {dem_initial: dem.tif, boundary: outline.gpkg}\n"
                    "fine_sediment:\n  enabled: true\n  pick_up: off\n",
                    encoding="utf-8")
    cfg = load_config(path)
    assert cfg.fine_sediment.pick_up == "off"
    cfg.fine_sediment.validate()
    # the editor of the case reads the file itself, and showed the choice as "False"
    from axqua.core.casecheck import read_raw

    assert read_raw(path)["fine_sediment"]["pick_up"] == "off"
    # `on` is no choice, and is named as it was understood
    path.write_text(path.read_text(encoding="utf-8").replace("pick_up: off",
                                                             "pick_up: on"),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="'suspended', 'bedload' or 'off'"):
        load_config(path).fine_sediment.validate()


def test_the_other_choices_called_off_are_read_the_same_way(tmp_path):
    """The exchange with a porous body has two of them. ``mode: off`` used to load as
    False there, which is neither of its three modes."""
    path = tmp_path / "exchange.axq-case"
    path.write_text("project: {name: exchange, crs_epsg: 25832}\n"
                    "geodata: {dem_initial: dem.tif, boundary: outline.gpkg}\n"
                    "gain_lose:\n  mode: off\n  water_table: off\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.gain_lose.mode == "off" and cfg.gain_lose.water_table == "off"
    # a setting that IS a yes or no keeps its meaning
    path.write_text(path.read_text(encoding="utf-8") + "  enabled: off\n",
                    encoding="utf-8")
    assert load_config(path).gain_lose.enabled is False


def test_a_block_that_is_switched_off_is_not_checked():
    FineSediment(enabled=False, pick_up="nonsense").validate()


# --------------------------------------------------------------------------- #
# what the solver assumes about the mesh
# --------------------------------------------------------------------------- #


def test_the_mesher_writes_what_the_solver_assumes():
    """Bed face k under surface face k, points rising along each vertical, and bed
    faces in one vertex order: the solver finds its neighbours by comparing vertex
    numbers, and here it finds every pair of bed faces that share an edge."""
    mesh = _for_the_solver(_channel(nx=5, ny=4))
    geometry = sediment.check_columns(mesh)
    bed = mesh.polymesh.faces[mesh.polymesh.patch_face_ids(sediment.BED_PATCH)]
    east, west, north, south, count = sediment.bed_neighbours(bed)
    assert int(count.sum()) // 2 == 4 * 4 + 5 * 3          # pairs along x and along y
    assert (count == 4).sum() == 3 * 2                     # the faces in the middle
    # Which way the solver's "east" points is of no consequence. That west is the
    # opposite side, north the other axis, and the vector of the bed points up, is.
    centre = geometry.centre[:, :2]
    inner = count == 4
    to_east = centre[east[inner]] - centre[inner]
    to_north = centre[north[inner]] - centre[inner]
    assert np.allclose(centre[west[inner]] - centre[inner], -to_east)
    assert np.allclose(centre[south[inner]] - centre[inner], -to_north)
    assert np.allclose(np.einsum("ij,ij->i", to_east, to_north), 0.0)
    assert np.allclose(np.linalg.norm(to_east, axis=1), 1.0)
    assert geometry.outlets["outlet-1"].size == 4 * mesh.n_layers
    assert geometry.plan_area == pytest.approx(1.0)
    assert geometry.vector[:, 2] == pytest.approx(1.0)


def test_bed_faces_in_another_vertex_order_are_refused():
    """With one face turned, the solver's search no longer finds its neighbours and
    would smooth the bed slope over the wrong faces without a word."""
    mesh = _for_the_solver(_channel(nx=5, ny=4))
    first = mesh.polymesh.patch(sediment.BED_PATCH).start_face
    mesh.polymesh.faces[first + 7] = np.roll(mesh.polymesh.faces[first + 7], 1)
    with pytest.raises(errors.MeshError, match="not in one order"):
        sediment.check_columns(mesh)


def test_a_surface_that_is_not_over_the_bed_is_refused():
    mesh = _for_the_solver(_channel(nx=5, ny=4))
    top = mesh.polymesh.patch(sediment.SURFACE_PATCH).start_face
    faces = mesh.polymesh.faces
    faces[[top, top + 1]] = faces[[top + 1, top]]
    with pytest.raises(errors.MeshError, match="do not stand over each other"):
        sediment.check_columns(mesh)


def _riffle():
    """A bed that rises by 0.8 m per cell under a level water surface: the depth, and
    with it the thickness of evenly spread layers, changes from vertical to vertical,
    from 3.4 m at the inflow to 0.2 m at the outflow."""
    return _channel(nx=4, ny=3, bed=lambda x, y: 0.8 * x,
                    depth=lambda x, y: 3.4 - 0.8 * x)


def test_evenly_spread_layers_put_the_cell_center_below_the_bed_face():
    """Why the bottom layer is pinned. The solver takes the height of the cell center
    above the center of the bed face, vertically. With layers a quarter of the depth
    thick the bed cell is a wedge whose center lies towards its thick end, which is
    the low end of the bed - below the middle of the bed face."""
    even = sediment.check_columns(_for_the_solver(_riffle()))
    assert (even.height <= 0).any()
    with pytest.raises(errors.MeshError) as caught:
        sediment.check_heights(even, roughness=0.05, bottom=0.1)
    assert "floating point exception" in str(caught.value)
    assert caught.value.subject == "fine_sediment.bottom_layer"

    pinned = sediment.check_columns(_for_the_solver(_riffle(), bottom=0.1))
    assert pinned.height == pytest.approx(0.05)            # half the layer, everywhere
    assert sediment.check_heights(pinned, roughness=0.05, bottom=0.1) == []
    # the volume of a bed cell is its plan area times the layer, so a rate per plan
    # area is the same whether it is booked through a face or through a volume
    assert pinned.volume / pinned.plan_area == pytest.approx(0.1)


def test_the_bottom_layer_changes_levels_and_nothing_else():
    mesh = _riffle()
    faces, owner = mesh.polymesh.faces.copy(), mesh.polymesh.owner.copy()
    plan, bed, top = (mesh.polymesh.points[:, :2].copy(), mesh.z[:, 0].copy(),
                      mesh.z[:, -1].copy())
    sediment.pin_bottom_layer(mesh, 0.1)
    assert np.array_equal(mesh.polymesh.faces, faces)
    assert np.array_equal(mesh.polymesh.owner, owner)
    assert np.array_equal(mesh.polymesh.points[:, :2], plan)
    assert np.array_equal(mesh.z[:, 0], bed) and np.array_equal(mesh.z[:, -1], top)
    assert mesh.z[:, 1] - mesh.z[:, 0] == pytest.approx(0.1)
    assert (np.diff(mesh.z, axis=1) > 0).all()
    assert polymesh.validate(mesh.polymesh) == []


def test_a_bottom_layer_thicker_than_half_the_shallowest_column_is_refused():
    """It would be thinner there than elsewhere, which is the thing to avoid."""
    with pytest.raises(errors.ConfigError) as caught:
        sediment.pin_bottom_layer(_riffle(), 0.3)          # the column is 0.2 m deep
    assert "0.100 m or less" in caught.value.remedy


def test_a_face_close_to_the_roughness_limit_is_reported():
    """Above the limit of the logarithm the solver runs, and the shear stress it
    computes there is too high. That is said, with the share of faces."""
    geometry = sediment.check_columns(_for_the_solver(_riffle(), bottom=0.1))
    notes = sediment.check_heights(geometry, roughness=0.9, bottom=0.1)
    assert len(notes) == 1 and "the shear stress the solver computes" in notes[0]
    with pytest.raises(errors.MeshError):
        sediment.check_heights(geometry, roughness=1.6, bottom=0.1)


def _twisted():
    """A bed that is steeper towards one corner of every cell, as terrain is. Such a
    face is not flat, and its center lies towards its steep, high part."""
    return _channel(nx=6, ny=4, bed=lambda x, y: 0.5 * x * y,
                    depth=lambda x, y: np.full_like(x, 0.4))


def test_a_steep_twisted_face_shortens_the_height_by_an_offset_of_its_own():
    """The height is half the bottom layer plus an offset of the face that does not
    depend on the layer. That is what lets a thicker layer be computed instead of
    tried: on the Isar example one face of 2720 has -0.026 m."""
    offsets = []
    for bottom in (0.05, 0.1, 0.2):
        geometry = sediment.check_columns(_for_the_solver(_twisted(), bottom=bottom))
        offsets.append(geometry.height - 0.5 * bottom)
    assert np.allclose(offsets[0], offsets[1]) and np.allclose(offsets[0], offsets[2])
    assert offsets[0].min() < -0.02 and offsets[0].max() < 1e-9   # never longer here
    thin = sediment.check_columns(_for_the_solver(_twisted(), bottom=0.05))
    needed = sediment.required_bottom_layer(thin, roughness=0.05, bottom=0.05)
    assert needed == pytest.approx(2 * (0.05 * math.e / 30 - offsets[0].min()))
    enough = sediment.check_columns(_for_the_solver(_twisted(), bottom=needed))
    assert (30 * enough.height / 0.05).min() == pytest.approx(math.e)
    assert sediment.check_heights(enough, roughness=0.05, bottom=needed) == []


def test_the_build_makes_the_bottom_layer_as_thick_as_the_steepest_face_needs(
        tmp_path, monkeypatch):
    """Left open in the case, the layer is the one of the shallowest water column, and
    thicker where a bed face needs it. The first chain of jobs on the example ended
    here, with a refusal for one face of 2720."""
    monkeypatch.setattr(ofmesh, "build_mesh", lambda cfg, state=None: _twisted())
    monkeypatch.setattr("axqua.core.capabilities.refresh_markers", lambda cfg: [])
    art = sediment.build_case(load_config(_case(tmp_path)), state=_State())
    assert 0.05 < art.record["bottom_layer"] <= 0.2        # 0.2 m is half the column
    assert art.record["lowest_height"] == pytest.approx(0.05 * math.e / 30)
    assert "the bottom layer is" in art.notes[0] and "and not 0.050 m" in art.notes[0]
    assert art.mesh.z[:, 1] - art.mesh.z[:, 0] == pytest.approx(art.record["bottom_layer"])


def test_a_named_bottom_layer_is_kept_or_refused_with_the_one_that_would_do(
        tmp_path, monkeypatch):
    monkeypatch.setattr(ofmesh, "build_mesh", lambda cfg, state=None: _twisted())
    monkeypatch.setattr("axqua.core.capabilities.refresh_markers", lambda cfg: [])
    with pytest.raises(errors.MeshError) as caught:
        sediment.build_case(load_config(_case(tmp_path, bottom_layer=0.03)),
                            state=_State())
    assert "below the bed for the solver" in str(caught.value)
    assert "Set fine_sediment.bottom_layer to" in caught.value.remedy
    assert "leave it empty" in caught.value.remedy
    art = sediment.build_case(load_config(_case(tmp_path, bottom_layer=0.15)),
                              state=_State())
    assert art.record["bottom_layer"] == 0.15 and art.notes == []


def test_a_bed_too_steep_for_any_bottom_layer_asks_for_smaller_cells(tmp_path,
                                                                    monkeypatch):
    steep = lambda cfg, state=None: _channel(                        # noqa: E731
        nx=6, ny=4, bed=lambda x, y: 3.0 * x * y, depth=lambda x, y: np.full_like(x, 0.4))
    monkeypatch.setattr(ofmesh, "build_mesh", steep)
    monkeypatch.setattr("axqua.core.capabilities.refresh_markers", lambda cfg: [])
    with pytest.raises(errors.MeshError) as caught:
        sediment.build_case(load_config(_case(tmp_path)), state=_State())
    assert "Use smaller cells" in caught.value.remedy
    assert "the shallowest water column allows 0.200 m" in caught.value.remedy


# --------------------------------------------------------------------------- #
# the case
# --------------------------------------------------------------------------- #


def test_the_case_is_what_the_solver_reads(built):
    cfg, art = built
    case = art.case_dir
    assert case == cfg.fine_sediment_case_dir and case.name == "openfoam-sediment"
    assert polymesh.read_patch_names(case) == [
        "bedWall", "inlet-1", "outlet-1", "banks", "freeSurface"]
    boundary = (case / "constant" / "polyMesh" / "boundary").read_text()
    surface = boundary[boundary.index("freeSurface"):]
    assert "patch" in surface.split("}")[0] and "wall" not in surface.split("}")[0]
    for name in ("U", "Umod", "p", "k", "epsilon", "nut", "Conc", "S_explicit",
                 "S_implicit"):
        text = (case / "0" / name).read_text()
        for patch in ("bedWall", "inlet-1", "outlet-1", "banks", "freeSurface"):
            assert f"    {patch}\n" in text, (name, patch)
    properties = (case / "constant" / "sedimentProperties").read_text()
    assert "particleSize    0.0001;" in properties
    assert f"fallVelocity    {-sediment.fall_velocity(0.0001):.8g};" in properties
    assert "sedimentFormula 2;" in properties              # off: nothing is picked up
    assert f"initialIterations {sediment.NEVER};" in properties
    control = (case / "system" / "controlDict").read_text()
    assert "application     sediDriftFoam2;" in control and "endTime         50;" in control
    assert "steadyState" in (case / "system" / "fvSchemes").read_text()
    assert sediment.is_built(case) and sediment.latest_time(case) is None


def test_the_concentration_enters_as_a_share_of_the_volume(built):
    """The case file gives kg/m3, the solver carries m3 of grains per m3 of water."""
    _cfg, art = built
    conc = (art.case_dir / "0" / "Conc").read_text()
    assert f"uniform {0.05 / 2650:.8g}" in conc
    velocity = (art.case_dir / "0" / "U").read_text()
    assert "volumetricFlowRate constant 0.6" in velocity


def test_the_bed_has_one_roughness(built):
    """One value, in the wall function of the flow and in the shear stress on the
    grains. With the roughness of each zone the residuals did not fall on the Isar."""
    _cfg, art = built
    nut = (art.case_dir / "0" / "nut").read_text()
    bed = nut[nut.index("    bedWall\n"):]
    bed = bed[:bed.index("}")]
    assert "Ks              uniform 0.05;" in bed and "nonuniform" not in bed
    assert "roughness       0.05;" in (
        art.case_dir / "constant" / "sedimentProperties").read_text()
    assert art.record["roughness"] == 0.05


@pytest.mark.parametrize("pick_up, number", [("suspended", 0), ("bedload", 1), ("off", 2)])
def test_the_pick_up_is_a_number_of_the_solver(tmp_path, pick_up, number):
    cfg = load_config(_case(tmp_path, pick_up=pick_up))
    text = sediment.sediment_properties(cfg, roughness=0.05, velocity=0.01)
    assert f"sedimentFormula {number};" in text


def test_a_case_without_the_block_is_not_built(tmp_path):
    cfg = load_config(_case(tmp_path, enabled=False))
    with pytest.raises(errors.ConfigError) as caught:
        sediment.build_case(cfg, state=_State())
    assert caught.value.subject == "fine_sediment.enabled"


def test_without_a_2d_result_there_is_no_water_surface(tmp_path):
    cfg = load_config(_case(tmp_path))
    with pytest.raises(errors.ConfigError, match="needs the steady 2D result"):
        sediment.build_case(cfg)


def test_the_refusal_of_the_lid_says_what_it_means_here(tmp_path, monkeypatch):
    """The mesher offers the two-phase mode as the way out. This solver has none."""
    def refuse(cfg, state=None):
        raise ValueError("RIGID LID DOES NOT APPLY HERE: the surface steps 180%.\n"
                         "  Use openfoam.mode: vof where the surface has to move")
    monkeypatch.setattr(ofmesh, "build_mesh", refuse)
    cfg = load_config(_case(tmp_path))
    with pytest.raises(errors.MeshError) as caught:
        sediment.build_case(cfg, state=_State())
    assert "vof" not in str(caught.value) and "no other mode" in caught.value.remedy
    assert caught.value.subject == "openfoam.allow_stepped_lid"


def test_the_mesh_is_built_under_a_lid_whatever_the_case_says(tmp_path, monkeypatch):
    seen = {}

    def build(cfg, state=None):
        seen["mode"], seen["solver"] = cfg.openfoam.mode, cfg.openfoam.solver
        return _channel()
    monkeypatch.setattr(ofmesh, "build_mesh", build)
    monkeypatch.setattr("axqua.core.capabilities.refresh_markers", lambda cfg: [])
    cfg = load_config(_case(tmp_path))
    assert cfg.openfoam.mode == "vof"
    sediment.build_case(cfg, state=_State())
    assert seen == {"mode": "rigid-lid", "solver": "interFoam"}
    assert cfg.openfoam.mode == "vof"                      # the case is left as it is


def test_a_new_build_removes_the_result_of_the_old_one(built):
    cfg, art = built
    case = art.case_dir
    (case / "40").mkdir()
    (case / "40" / "Conc").write_text("old")
    (case / sediment.BED_LAYER_NAME).write_text("old")
    (case / "notes-of-the-user.txt").write_text("keep")
    sediment.build_case(cfg, state=_State())
    assert not (case / "40").exists() and not (case / sediment.BED_LAYER_NAME).exists()
    assert (case / "notes-of-the-user.txt").read_text() == "keep"


# --------------------------------------------------------------------------- #
# shear stress and pick-up, as the solver computes them
# --------------------------------------------------------------------------- #


def _flat_bed(n=5, *, height=0.025, x=100.0):
    return sediment.BedGeometry(
        cells=np.arange(n), corners=np.zeros((n, 4, 2)),
        centre=np.column_stack([np.full(n, x), np.zeros(n), np.zeros(n)]),
        plan_area=np.ones(n), height=np.full(n, height), volume=np.full(n, 2 * height),
        vector=np.tile([0.0, 0.0, 1.0], (n, 1)), depth=np.full(n, 0.4),
        roughness_zone=np.full(n, 0.05))


def _exchange(speeds, pick_up="suspended", **kw):
    geometry = kw.pop("geometry", None) or _flat_bed(len(speeds))
    velocity = np.column_stack([speeds, np.zeros(len(speeds)), np.zeros(len(speeds))])
    return sediment.bed_exchange(geometry, velocity, grain_size=0.0002, fall=0.02,
                                 roughness=0.05, pick_up=pick_up, **kw)


def test_the_pick_up_follows_the_formulas_of_the_solver():
    """One bed face, by hand: shear velocity from the logarithmic law at the height of
    the cell center, the fitted Shields curve, Dey's factor of 0.945 on a level bed,
    van Rijn's transport parameter and his reference concentration."""
    out = _exchange([0.5])
    shear_velocity = 0.4 * 0.5 / math.log(30 * 0.025 / 0.05)
    stress = 1000 * shear_velocity ** 2
    a = math.log10(shear_velocity * 0.0002 * 1e6)
    theta = 10 ** (-0.99863612 - 0.9253539586 * a + 0.54283631 * a * a
                   - 0.084544063 * a ** 3)
    critical = theta * 1650 * 9.81 * 0.0002 * 0.945
    transport = (stress - critical) / critical
    reference = 0.015 * 0.0002 / 0.025 * transport ** 1.5 / (0.0002 * 25296) ** 0.3
    assert out["stress"][0] == pytest.approx(stress)
    assert out["critical"][0] == pytest.approx(critical)
    assert out["transport"][0] == pytest.approx(transport)
    assert out["reference"][0] == pytest.approx(reference)
    assert out["source"][0] == pytest.approx(0.02 / (2 * 0.025) * reference)


def test_nothing_is_picked_up_below_the_critical_shear_stress():
    out = _exchange([0.0, 0.01, 0.05, 0.5, 1.0, 3.0])
    assert out["source"][0] == 0 and out["source"][1] == 0
    assert (np.diff(out["transport"]) >= 0).all() and out["transport"][-1] > 0
    assert out["reference"].max() == pytest.approx(0.1)    # the solver's limit
    assert np.isfinite(out["source"]).all()


def test_no_pick_up_means_none_and_bed_load_does_not_divide_by_a_still_cell():
    assert (_exchange([0.5, 2.0], "off")["source"] == 0).all()
    bedload = _exchange([0.0, 0.5, 2.0], "bedload")
    assert bedload["source"][0] == 0 and np.isfinite(bedload["source"]).all()
    assert bedload["source"][2] > bedload["source"][1] > 0


def test_flow_up_a_slope_moves_the_grains_later_than_flow_down_it():
    """Dey's correction: the critical shear stress rises against the slope."""
    tilted = _flat_bed(2)
    tilted.vector = np.tile([0.3, 0.0, math.sqrt(1 - 0.09)], (2, 1))   # falls along x
    velocity = np.array([[0.5, 0.0, 0.0], [-0.5, 0.0, 0.0]])            # down, then up
    out = sediment.bed_exchange(tilted, velocity, grain_size=0.0002, fall=0.02,
                                roughness=0.05, pick_up="suspended")
    assert out["critical"][1] > out["critical"][0]
    assert out["stress"][0] == pytest.approx(out["stress"][1])


def test_the_rule_for_the_inflow_of_a_flume_is_followed_where_it_applies():
    """Below x = 0.5 m the solver raises the critical shear stress. No projected
    coordinate system has such coordinates, but a flume drawn from its inflow has."""
    there = _exchange([1.0], geometry=_flat_bed(1, x=0.1))
    elsewhere = _exchange([1.0], geometry=_flat_bed(1, x=100.0))
    factor = (1.0 + 10.0 * 0.4) / 0.945
    assert there["critical"][0] == pytest.approx(elsewhere["critical"][0] * factor)


def test_the_shields_curve_is_the_fit_of_the_solver():
    values = sediment.shields(np.array([0.05, 0.2, 10.0, 499.0, 500.0, 5000.0]))
    assert values[0] == 0.6 and values[-1] == 0.06 and values[-2] == 0.06
    assert values[2] == pytest.approx(0.034, abs=0.002)    # the dip of the curve
    assert 0.05 < values[3] < 0.07                         # joins the constant


# --------------------------------------------------------------------------- #
# the bed layer
# --------------------------------------------------------------------------- #


def test_the_share_of_fines_follows_from_the_mass_the_layer_takes_up():
    """A layer 0.1 m thick with 30 % pores holds 185.5 kg/m2 of coarse grains. With
    20 % fines it holds 46.4 kg/m2 of them, and 10 kg/m2 more make 23.3 %."""
    coarse = 2650 * 0.1 * 0.7
    held = coarse * 0.2 / 0.8
    out = sediment.bed_layer(np.array([10.0, 0.0, -10.0]) / 86400, period=86400.0,
                             thickness=0.1, porosity=0.3, initial=0.2)
    assert out["mass"] == pytest.approx([10.0, 0.0, -10.0])
    assert out["fraction"] == pytest.approx(
        [(held + 10) / (coarse + held + 10), 0.2, (held - 10) / (coarse + held - 10)])
    assert out["change"][1] == pytest.approx(0.0, abs=1e-12)
    assert list(out["limit"]) == [1, 0, 0]                 # 56.4 kg/m2 exceed the pores


def test_a_bed_gives_off_no_more_fines_than_its_layer_holds():
    held = 2650 * 0.1 * 0.7 * 0.05 / 0.95
    out = sediment.bed_layer(np.array([-1.0]), period=1000.0, thickness=0.1,
                             porosity=0.3, initial=0.05)
    assert out["mass"][0] == pytest.approx(-held)
    assert out["fraction"][0] == pytest.approx(0.0, abs=1e-12)
    assert out["limit"][0] == -1


def test_clean_gravel_can_only_gain():
    out = sediment.bed_layer(np.array([-1e-3, 1e-6]), period=3600.0, thickness=0.1,
                             porosity=0.3, initial=0.0)
    assert out["mass"][0] == 0 and out["fraction"][0] == 0 and out["limit"][0] == -1
    assert out["fraction"][1] > 0 and out["limit"][1] == 0


# --------------------------------------------------------------------------- #
# a result
# --------------------------------------------------------------------------- #

_PHI = """FoamFile
{{
    version     2.0;
    format      ascii;
    class       surfaceScalarField;
    object      phi;
}}
dimensions      [0 3 -1 0 0 0 0];

internalField   uniform 0;

boundaryField
{{
    bedWall
    {{
        type            calculated;
        value           uniform 0;
    }}
    inlet-1
    {{
        type            calculated;
        value           uniform -0.05;
    }}
    outlet-1
    {{
        type            calculated;
        value           nonuniform List<scalar> 
{n}
(
{values}
)
;
    }}
}}
"""


def _write_result(case: Path, time: str, *, conc, velocity=(0.5, 0.0, 0.0),
                  outflow=None, n_cells=96) -> None:
    """Three files of a time folder, in the layout OpenFOAM writes them."""
    from axqua.solvers.openfoam.fields import render_field, vector_list

    folder = case / time
    folder.mkdir()
    conc = np.broadcast_to(np.asarray(conc, dtype=float), (n_cells,))
    listed = (f"nonuniform List<scalar> \n{n_cells}\n(\n"
              + "\n".join(f"{c:.12g}" for c in conc) + "\n)\n")   # as the solver writes
    (folder / "Conc").write_text(render_field(
        "volScalarField", "Conc", "[0 0 0 0 0 0 0]", listed, {}))
    (folder / "U").write_text(render_field(
        "volVectorField", "U", "[0 1 -1 0 0 0 0]",
        vector_list(np.tile(velocity, (n_cells, 1))), {}))
    outflow = np.full(12, 0.05) if outflow is None else np.asarray(outflow, dtype=float)
    (folder / "phi").write_text(_PHI.format(
        n=outflow.size, values="\n".join(f"{v:.10g}" for v in outflow)))


#: A concentration at which the channel of the fixture is in balance: what enters with
#: 0.6 m3/s leaves with the same discharge or settles on 24 m2 of bed.
def _balanced() -> float:
    fall = sediment.fall_velocity(0.0001)
    return 0.05 / 2650 * 0.6 / (0.6 + fall * 24.0)


def test_the_budget_of_the_reach_closes(built):
    """What enters is what leaves plus what the bed takes up. Each term by hand."""
    cfg, art = built
    conc = _balanced()
    _write_result(art.case_dir, "37", conc=conc)
    result = sediment.evaluate(cfg)
    b = result.budget
    fall = sediment.fall_velocity(0.0001)
    assert b["time"] == "37" and b["iterations"] == 37     # no listing: the time folder
    assert b["load_in"] == pytest.approx(0.6 * 0.05)
    assert b["load_out"] == pytest.approx(2650 * 0.6 * conc)
    assert b["deposition"] == pytest.approx(2650 * fall * conc * 24.0)
    assert b["pick_up"] == 0.0
    assert b["closure"] < 1e-9
    assert b["bed_area"] == pytest.approx(24.0)
    assert result.table["settling"] == pytest.approx(2650 * fall * conc * 86400)
    assert result.table["conc_bed"] == pytest.approx(2650 * conc)
    assert result.table["depth"] == pytest.approx(0.4)
    assert "mass" not in result.table                      # the case names no period


def test_an_open_budget_and_a_run_that_did_not_converge_are_said(built):
    cfg, art = built
    _write_result(art.case_dir, "50", conc=3 * _balanced())
    notes = sediment.evaluate(cfg, write=False).budget["notes"]
    assert any("the sediment budget is open by" in note for note in notes)
    assert any("without reaching the residual" in note for note in notes)


def test_water_that_enters_through_the_outflow_brings_no_sediment(built):
    cfg, art = built
    flux = np.full(12, 0.05)
    flux[:3] = -0.02                                       # a return current
    _write_result(art.case_dir, "9", conc=_balanced(), outflow=flux)
    budget = sediment.evaluate(cfg, write=False).budget
    assert budget["load_out"] == pytest.approx(2650 * 9 * 0.05 * _balanced())


def test_a_period_turns_the_rate_into_a_share_of_fines(tmp_path, monkeypatch):
    monkeypatch.setattr(ofmesh, "build_mesh", lambda cfg, state=None: _channel())
    monkeypatch.setattr("axqua.core.capabilities.refresh_markers", lambda cfg: [])
    cfg = load_config(_case(tmp_path, period=864000, layer_thickness=0.2, porosity=0.25,
                            initial_fraction=0.1))
    art = sediment.build_case(cfg, state=_State())
    _write_result(art.case_dir, "12", conc=_balanced())
    table = sediment.evaluate(cfg, write=False).table
    net = 2650 * sediment.fall_velocity(0.0001) * _balanced()
    expected = sediment.bed_layer(np.array([net]), period=864000, thickness=0.2,
                                  porosity=0.25, initial=0.1)
    assert table["mass"] == pytest.approx(net * 864000)
    assert table["fraction"] == pytest.approx(expected["fraction"][0])
    assert (table["change"] > 0).all()


def test_the_bed_is_written_as_a_layer_in_the_coordinates_of_the_case(built):
    import geopandas as gpd

    cfg, art = built
    _write_result(art.case_dir, "5", conc=np.linspace(1e-6, 2e-5, 96))
    result = sediment.evaluate(cfg)
    assert result.layer == art.case_dir / sediment.BED_LAYER_NAME
    frame = gpd.read_file(result.layer)
    assert len(frame) == 24 and frame.crs.to_epsg() == 25832
    assert frame.geometry.area.to_numpy() == pytest.approx(1.0)
    assert frame.total_bounds == pytest.approx([0.0, 0.0, 8.0, 3.0])
    for name in ("depth", "velocity", "shear", "conc_bed", "settling", "pick_up", "net"):
        assert name in frame.columns
    assert frame["settling"].nunique() > 1
    budget = json.loads((art.case_dir / sediment.BUDGET_NAME).read_text())
    assert budget["time"] == "5"


def test_the_last_time_folder_with_a_concentration_is_the_result(built):
    _cfg, art = built
    _write_result(art.case_dir, "100", conc=1e-5)
    _write_result(art.case_dir, "25", conc=1e-5)
    (art.case_dir / "300").mkdir()                         # written in part, then stopped
    assert sediment.latest_time(art.case_dir) == "100"


def test_a_field_file_that_does_not_fit_the_mesh_is_named(built):
    cfg, art = built
    _write_result(art.case_dir, "8", conc=1e-5, n_cells=95)
    with pytest.raises(errors.SolverError, match="95 values for a mesh of 96 cells"):
        sediment.evaluate(cfg, write=False)


def test_a_case_without_a_result_says_what_to_do(built):
    cfg, _art = built
    with pytest.raises(errors.SolverError) as caught:
        sediment.evaluate(cfg)
    assert caught.value.remedy == "Run the fine sediment simulation."


def test_uniform_and_listed_values_are_read_alike(tmp_path):
    path = tmp_path / "field"
    path.write_text("internalField   uniform (1 2 3);\nboundaryField\n{\n    a\n    {\n"
                    "        type x;\n        value           uniform 4.5;\n    }\n}\n")
    assert sediment.read_internal(path, 3).tolist() == [[1, 2, 3]] * 3
    assert sediment.read_patch(path, "a", 2).tolist() == [4.5, 4.5]
    path.write_text("internalField   nonuniform List<scalar> \n3\n(\n1\n2.5\n-3e-2\n)\n;\n"
                    "boundaryField\n{\n}\n")
    assert sediment.read_internal(path, 3).tolist() == [1.0, 2.5, -0.03]


def test_the_listing_gives_iterations_residuals_and_how_the_run_ended(tmp_path):
    log = tmp_path / "log"
    log.write_text(
        "Time = 1\n\nsmoothSolver:  Solving for Ux, Initial residual = 1, Final residual"
        " = 0.05, No Iterations 3\n"
        "Time = 2\n\nsmoothSolver:  Solving for Ux, Initial residual = 0.004, Final "
        "residual = 1e-5, No Iterations 2\n"
        "GAMG:  Solving for p, Initial residual = 2e-3, Final residual = 1e-4, No "
        "Iterations 1\nGAMG:  Solving for p, Initial residual = 9e-4, Final residual = "
        "1e-5, No Iterations 1\n\nSIMPLE solution converged in 2 iterations\n\nEnd\n")
    run = sediment.read_log(log)
    assert run["iterations"] == 2 and run["converged"] and run["ended"]
    assert run["residuals"] == {"Ux": 0.004, "p": 0.002}   # the first solve of each
    assert sediment.read_log(tmp_path / "none")["iterations"] == 0


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #


class _Runtime:
    """What ``run_case`` asks of the OpenFOAM installation, without one."""

    def __init__(self, *, found=True, code=0, lines=(), usable=True, write=None):
        self.found, self.code, self.lines, self.usable = found, code, lines, usable
        self.write, self.commands = write, []

    def run(self, command, cwd=None, check=True, on_line=None, should_stop=None, **_kw):
        self.commands.append(command)
        if command.startswith("command -v"):
            return subprocess.CompletedProcess(
                command, 0 if self.found else 1,
                stdout="/opt/stack/bin/sediDriftFoam2\n" if self.found else "", stderr="")
        for line in self.lines:
            on_line(line)
        if self.write is not None:
            self.write(Path(cwd))
        return subprocess.CompletedProcess(command, self.code,
                                           stdout="\n".join(self.lines), stderr="")

    def check_mesh(self, case_dir):
        return self.usable, ([] if self.usable else ["***Zero or negative cell volume"]), ""


def test_a_run_starts_from_the_first_iteration_and_keeps_its_listing(built):
    cfg, art = built
    case = art.case_dir
    _write_result(case, "50", conc=1e-5)                   # of an earlier run
    (case / sediment.BED_LAYER_NAME).write_text("of an earlier run")
    runtime = _Runtime(lines=["Time = 1", "Time = 2", "End"],
                       write=lambda folder: _write_result(folder, "2", conc=_balanced()))
    sediment.run_case(cfg, runtime=runtime, show_progress=False)
    assert runtime.commands == ["command -v sediDriftFoam2", "sediDriftFoam2"]
    assert not (case / "50").exists() and not (case / sediment.BED_LAYER_NAME).exists()
    assert (case / sediment.LOG_NAME).read_text() == "Time = 1\nTime = 2\nEnd\n"
    assert sediment.evaluate(cfg, write=False).budget["iterations"] == 2


def test_the_number_of_iterations_can_be_set_for_one_run(built):
    cfg, art = built
    sediment.run_case(cfg, runtime=_Runtime(), iterations=800, show_progress=False)
    control = (art.case_dir / "system" / "controlDict").read_text()
    assert "endTime         800;" in control and "writeInterval   200;" in control


def test_an_installation_without_the_solver_says_where_it_comes_from(built):
    cfg, _art = built
    with pytest.raises(errors.EnvironmentError) as caught:
        sediment.run_case(cfg, runtime=_Runtime(found=False), show_progress=False)
    assert "no sediDriftFoam2" in str(caught.value)
    assert "axqua install openfoam" in caught.value.remedy


def test_a_mesh_openfoam_cannot_use_is_not_run(built):
    cfg, _art = built
    runtime = _Runtime(usable=False)
    with pytest.raises(errors.MeshError):
        sediment.run_case(cfg, runtime=runtime, show_progress=False)
    assert "sediDriftFoam2" not in runtime.commands


@pytest.mark.parametrize("lines, said, advice", [
    (["Time = 1", "Time = 2", "#1  Foam::sigFpe::sigHandler(int)",
      "Floating point exception"],
     "floating point exception after 2 iterations", "larger cells"),
    (["--> FOAM FATAL IO ERROR: (openfoam-2406)", "keyword particleSize is undefined in "
      "dictionary sedimentProperties", "", "FOAM exiting"],
     "keyword particleSize is undefined", "for the first error"),
])
def test_a_run_that_ends_with_an_error_says_which(built, lines, said, advice):
    cfg, _art = built
    with pytest.raises(errors.SolverError) as caught:
        sediment.run_case(cfg, runtime=_Runtime(code=136, lines=lines),
                          show_progress=False)
    assert said in str(caught.value) and advice in caught.value.remedy


def test_progress_counts_iterations(built):
    cfg, _art = built

    class Sink:
        seen: list = []

        def progress(self, **fields):
            self.seen.append(fields)

        def line(self, text):
            pass

    sediment.run_case(cfg, runtime=_Runtime(lines=["Time = 10", "Time = 20"]),
                      sink=Sink(), show_progress=False)
    times = [f.get("simulated_time") for f in Sink.seen if f.get("simulated_time")]
    assert times and times[-1] == 20.0
    assert any(f.get("duration") == 50.0 for f in Sink.seen)


# --------------------------------------------------------------------------- #
# the backend, the capability and the jobs
# --------------------------------------------------------------------------- #


def test_openfoam_offers_morphodynamics_as_fine_sediment(tmp_path):
    from axqua.core.capabilities import Support
    from axqua.solvers.openfoam.spec import SPEC

    entry = SPEC.capabilities[Capability.MORPHODYNAMICS]
    assert entry.support is Support.SUPPORTED
    cfg = load_config(_case(tmp_path))
    assert entry.configured(cfg) is True
    assert entry.built(cfg) is False and entry.run(cfg) is False
    case = cfg.fine_sediment_case_dir
    (case / "constant" / "polyMesh").mkdir(parents=True)
    (case / "constant" / "polyMesh" / "faces").write_text("")
    (case / "constant" / "sedimentProperties").write_text("")
    assert entry.built(cfg) is True and entry.run(cfg) is False
    (case / sediment.BED_LAYER_NAME).write_text("")
    assert entry.run(cfg) is True
    off = load_config(_case(tmp_path, enabled=False))
    assert entry.configured(off) is False


def test_a_case_that_asks_for_fine_sediment_uses_openfoam(tmp_path):
    """Also without a block ``openfoam``: the mesh settings then have their defaults."""
    from axqua.solvers.openfoam.spec import SPEC

    path = tmp_path / "only.axq-case"
    path.write_text("project: {name: only, crs_epsg: 25832}\n"
                    "geodata: {dem_initial: dem.tif, boundary: outline.gpkg}\n"
                    "fine_sediment: {enabled: true}\n", encoding="utf-8")
    assert SPEC.is_enabled(load_config(path)) is True
    path.write_text("project: {name: only, crs_epsg: 25832}\n"
                    "geodata: {dem_initial: dem.tif, boundary: outline.gpkg}\n",
                    encoding="utf-8")
    assert SPEC.is_enabled(load_config(path)) is False


def test_the_block_survives_being_written_and_read(tmp_path):
    from axqua.config import dump_config

    cfg = load_config(_case(tmp_path, inflow_concentration={"inlet-1": 0.02, "inlet-2": 0},
                            period=86400))
    copy = tmp_path / "copy.axq-case"
    dump_config(cfg, copy, portable=True)
    again = load_config(copy)
    assert again.fine_sediment == cfg.fine_sediment
    assert "fine_sediment" in again.declared_blocks


def test_a_case_that_never_asked_is_not_given_the_block(tmp_path):
    from axqua.config import config_to_dict

    path = tmp_path / "plain.axq-case"
    path.write_text("project: {name: plain, crs_epsg: 25832}\n"
                    "geodata: {dem_initial: dem.tif, boundary: outline.gpkg}\n",
                    encoding="utf-8")
    assert "fine_sediment" not in config_to_dict(load_config(path))


def test_the_job_kinds_are_a_build_and_a_run_of_the_openfoam_backend():
    from axqua.jobs.model import KIND_META, JobKind, parse_kind

    build = KIND_META[parse_kind("openfoam-sediment-build")]
    run = KIND_META[parse_kind("openfoam-sediment")]
    assert (build.solver, build.verb, build.capability) == (
        "openfoam", "build", Capability.MORPHODYNAMICS)
    assert (run.solver, run.verb, run.capability) == (
        "openfoam", "run", Capability.MORPHODYNAMICS)
    assert parse_kind("fine-sediment") is JobKind.OPENFOAM_SEDIMENT_RUN
    assert run.options.from_dict({"iterations": 500}).iterations == 500
    with pytest.raises(errors.ConfigError):
        run.options.from_dict({"n_processors": 4})          # one process only
    assert build.options.from_dict({"pre_run": False}).pre_run is False


def test_the_backend_sends_a_morphodynamics_job_to_the_sediment_module(tmp_path,
                                                                      monkeypatch):
    from axqua.solvers.openfoam.backend import BACKEND

    cfg = load_config(_case(tmp_path))
    calls = {}
    monkeypatch.setattr("axqua.solvers.openfoam.hotstart.load_hotstart",
                        lambda cfg, path=None: f"state of {path}")
    monkeypatch.setattr(sediment, "build_case", lambda cfg, state=None: calls.update(
        build=state) or sediment.SedimentArtifacts(case_dir=tmp_path))
    monkeypatch.setattr(sediment, "run_case", lambda cfg, **kw: calls.update(run=kw))
    BACKEND.build(cfg, Capability.MORPHODYNAMICS, seed="r2d.slf")
    BACKEND.run(cfg, Capability.MORPHODYNAMICS, iterations=300, check_mesh=False)
    assert calls["build"] == "state of r2d.slf"
    assert calls["run"]["iterations"] == 300 and calls["run"]["check_mesh"] is False


class _Context:
    """The part of an execution context the backend uses after a run."""

    capability = Capability.MORPHODYNAMICS

    def __init__(self, verb):
        from types import SimpleNamespace

        self.spec = SimpleNamespace(meta=SimpleNamespace(verb=verb))
        self.manifest = SimpleNamespace(summary={})
        self.recorded = []

    def record(self, name, path, **kwargs):
        self.recorded.append((name, Path(path).name, kwargs))


def test_after_a_run_the_bed_is_booked_and_listed_for_qgis(built):
    from axqua.solvers.openfoam.backend import BACKEND

    cfg, art = built
    _write_result(art.case_dir, "20", conc=_balanced())
    ctx = _Context("run")
    BACKEND.postprocess(cfg, ctx)
    assert ctx.manifest.summary["fine_sediment"]["closure"] < 1e-9
    assert BACKEND.extract_objective(cfg, ctx) < 1e-9
    BACKEND.export_qgis_results(cfg, ctx)
    name, file, how = ctx.recorded[0]
    assert file == sediment.BED_LAYER_NAME
    assert how["kind"] == "vector" and how["style"] == "graduated"
    assert how["variable"] == "net"                        # no period: a rate
    cfg.fine_sediment.period = 86400.0
    again = _Context("run")
    BACKEND.export_qgis_results(cfg, again)
    assert again.recorded[0][2]["variable"] == "change"


def test_after_a_build_there_is_nothing_to_book(built):
    from axqua.solvers.openfoam.backend import BACKEND

    cfg, _art = built
    ctx = _Context("build")
    assert BACKEND.postprocess(cfg, ctx) == {}
    assert ctx.manifest.summary == {}


def test_a_run_whose_result_cannot_be_booked_is_not_a_finished_job(built):
    """The evaluation is the result. Unlike a figure, it may not be skipped."""
    from axqua.solvers.openfoam.backend import BACKEND

    cfg, _art = built
    with pytest.raises(errors.SolverError):
        BACKEND.postprocess(cfg, _Context("run"))
