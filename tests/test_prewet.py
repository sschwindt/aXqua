"""Channel pre-wetting (hotstart) tests.

Covers the two pieces that make up the pre-wetting option used by the
mesh-convergence study: the hotstart SELAFIN writer
(:func:`axqua.selafin.write_initial_state`) and the hotstart keywords the
steering writer emits (:func:`axqua.steering.write_cas`). The TELEMAC solver
is not involved.
"""

from __future__ import annotations

import numpy as np

from axqua import selafin


def test_write_initial_state_roundtrips(tmp_path):
    # a 2-triangle unit square; channel = the two left nodes pre-wetted to 0.5 m
    x = np.array([0.0, 1.0, 0.0, 1.0])
    y = np.array([0.0, 0.0, 1.0, 1.0])
    ikle = np.array([[1, 2, 3], [2, 4, 3]])
    ipobo = np.array([1, 2, 4, 3])
    depth = np.array([0.5, 0.0, 0.5, 0.0])

    p = selafin.write_initial_state(tmp_path / "ic.slf", x, y, ikle, ipobo, depth)
    data = selafin.read_slf(p)

    assert data["var_names"] == ["VELOCITY U", "VELOCITY V", "WATER DEPTH"]
    assert np.allclose(data["values"]["WATER DEPTH"], depth)
    assert np.allclose(data["values"]["VELOCITY U"], 0.0)
    assert np.allclose(data["values"]["VELOCITY V"], 0.0)
    # same mesh as the geometry it initializes
    assert np.allclose(data["x"], x) and np.allclose(data["y"], y)
    assert np.array_equal(data["ikle"], ikle - 1)


def test_longitudinal_prewet_smooths_surface():
    """The thalweg-referenced seed yields a smooth surface following the reach slope:
    no negative depths, ~prewet_depth over the thalweg, deeper there than at the
    banks, and a thalweg depth ~constant along the reach (a smooth surface following
    the gradient, not the jagged constant-depth seed)."""
    from axqua.steering import _longitudinal_prewet_depth

    ns, nc = 60, 21
    s = np.linspace(0.0, 300.0, ns)
    cross = np.linspace(-1.0, 1.0, nc)              # cross-channel position
    S, _ = np.meshgrid(s, cross, indexing="ij")
    bottom = (0.02 * S + (cross ** 2)[None, :]).ravel()   # slope + V cross-section
    s_node = S.ravel()
    mask = np.ones(bottom.size, dtype=bool)
    mask[: nc] = False                             # first section = dry floodplain

    depth = _longitudinal_prewet_depth(s_node, bottom, mask, depth_val=1.0)

    assert (depth >= 0).all()
    assert np.all(depth[~mask] == 0.0)             # floodplain stays dry
    sec = depth.reshape(ns, nc)
    thalweg = sec[:, nc // 2]                       # deepest column (V thalweg)
    assert (thalweg[ns // 5: -ns // 5] > sec[ns // 5: -ns // 5, 0]).all()  # > banks
    assert abs(thalweg[ns // 5: -ns // 5].mean() - 1.0) < 0.3   # ~ prewet_depth
    assert thalweg[ns // 5: -ns // 5].std() < 0.05 * thalweg.mean()  # smooth


def _straight_reach(tmp_path, *, length=300.0, slope=0.005, ks=0.05):
    """A straight trapezoidal reach: mesh, centerline, channel zone and a config.

    Returns ``(cfg, mesh, cross)`` where *cross* is the cross-channel coordinate of
    each node column, so a test can pick out the thalweg and the banks.
    """
    import geopandas as gpd
    from shapely.geometry import LineString, Polygon

    from axqua.config import (
        Boundaries, Calibration, Config, Friction, Geodata, GroundTruth,
        Hydrodynamics, Initialization, MeshConfig, Morphodynamics, TelemacEnv,
    )
    from axqua.mesh import Mesh

    ns, nc = 61, 41
    s = np.linspace(0.0, length, ns)
    cross = np.linspace(-20.0, 20.0, nc)
    S, C = np.meshgrid(s, cross, indexing="ij")
    # bed: constant downstream slope + a V-shaped section 2 m deep at the thalweg
    bottom = (100.0 - slope * S + 0.005 * C ** 2).ravel()
    x, y = S.ravel(), C.ravel()

    tri = []
    for i in range(ns - 1):
        for j in range(nc - 1):
            a, b = i * nc + j, i * nc + j + 1
            c, d = (i + 1) * nc + j, (i + 1) * nc + j + 1
            tri += [[a, b, c], [b, d, c]]
    mesh = Mesh(x=x, y=y, triangles=np.array(tri), bottom=bottom,
                ipobo=np.zeros(x.size, int), boundary_nodes=np.array([], int),
                element_matid=np.ones(len(tri), int), node_matid=np.ones(x.size, int),
                roughness=np.full(x.size, ks))

    centerline = tmp_path / "centerline.gpkg"
    gpd.GeoDataFrame(geometry=[LineString([(0.0, 0.0), (length, 0.0)])],
                     crs="EPSG:25832").to_file(centerline, driver="GPKG")
    # real liquid boundaries: the seed must keep the inflow section wet or DEBIMP
    # aborts the run at t=0
    liquid = tmp_path / "liquid.gpkg"
    gpd.GeoDataFrame(
        {"Type (inflow/outflow)": ["inflow", "outflow",
                                   "int-outflow-lose", "int-inflow-gain"],
         "Target flow": [10.0, 10.0, 0.065, 0.065]},
        geometry=[LineString([(length, -20.0), (length, 20.0)]),
                  LineString([(0.0, -20.0), (0.0, 20.0)]),
                  # internal exchange lines bracketing the bar at x = 85 .. 115
                  LineString([(85.0, -15.0), (85.0, 15.0)]),
                  LineString([(115.0, -15.0), (115.0, 15.0)])],
        crs="EPSG:25832").to_file(liquid, driver="GPKG")
    zones = tmp_path / "zones.gpkg"
    gpd.GeoDataFrame(
        {"Zone Name": ["Main channel"]},
        geometry=[Polygon([(-1, -20), (length + 1, -20),
                           (length + 1, 20), (-1, 20)])],
        crs="EPSG:25832").to_file(zones, driver="GPKG")

    model = tmp_path / "model"
    model.mkdir(exist_ok=True)
    dummy = tmp_path / "dummy"
    dummy.write_text("")
    cfg = Config(
        name="prewet-reach", crs_epsg=25832, config_dir=tmp_path,
        preprocessing_dir=tmp_path, model_dir=model, postprocessing_dir=tmp_path,
        calibration_dir=tmp_path,
        telemac=TelemacEnv(pysource=dummy),
        geodata=Geodata(dem_initial=dummy, boundary=dummy, mesh_zones=zones,
                        channel_centerline=centerline),
        boundaries=Boundaries(liquid_boundaries=liquid, prescribed_flowrate=10.0),
        initialization=Initialization(prewet_depth=0.30, drainable_prewet=False,
                                      dry_start_extent=3.0, dry_start_depth=0.5),
        mesh=MeshConfig(), friction=Friction(), hydrodynamics=Hydrodynamics(),
        morphodynamics=Morphodynamics(), ground_truth=GroundTruth(),
        calibration=Calibration(),
    )
    return cfg, mesh, C.ravel()


def test_normal_depth_prewet_never_seeds_above_its_own_surface(tmp_path):
    """The normal-depth seed lays water only where the target surface is above the
    bed, and the seeded surface is (per cross-section) flat - so no water sits on
    ground higher than the level that carries the discharge."""
    from axqua.steering import _normal_depth_prewet_depth

    cfg, mesh, cross = _straight_reach(tmp_path)
    mask = np.ones(mesh.x.size, dtype=bool)
    depth = _normal_depth_prewet_depth(cfg, mesh, mask, discharge=10.0)

    assert (depth >= 0).all()
    wet = depth > 0
    assert wet.any(), "the seed must wet the channel"
    surface = mesh.bottom + depth
    # per cross-section the seeded surface is one level (flat), and every dry node
    # of that section stands above it
    for i in range(0, mesh.x.size, 41):
        sec = slice(i, i + 41)
        sec_wet = wet[sec]
        if sec_wet.sum() < 2:
            continue
        level = surface[sec][sec_wet]
        assert level.max() - level.min() < 1e-6
        assert (mesh.bottom[sec][~sec_wet] >= level.max() - 1e-6).all()
    # the thalweg (cross == 0) is the deepest column
    thalweg = np.isclose(cross, 0.0)
    assert depth[thalweg & wet].mean() > depth[wet].mean()


def test_normal_depth_prewet_is_monotonic_in_fill(tmp_path):
    """prewet_fill scales the depth above the thalweg, so a lower fill seeds
    strictly less water - the knob that keeps the seed under the converged
    surface."""
    from axqua.steering import _normal_depth_prewet_depth

    cfg, mesh, _ = _straight_reach(tmp_path)
    mask = np.ones(mesh.x.size, dtype=bool)
    volumes = []
    for fill in (0.5, 0.7, 1.0):
        cfg.initialization.prewet_fill = fill
        volumes.append(_normal_depth_prewet_depth(cfg, mesh, mask, 10.0).sum())
    assert volumes[0] < volumes[1] < volumes[2]


def test_prewet_min_depth_floor_drops_the_feathered_margin(tmp_path):
    """Nodes that would get less than prewet_min_depth are left dry: a feathered
    seed margin carries no flow and is what stalls as stagnant film."""
    from axqua.steering import write_initial_conditions
    from axqua import selafin

    cfg, mesh, _ = _straight_reach(tmp_path)
    cfg.initialization.prewet_min_depth = 0.0
    depth_all = selafin.read_slf(
        write_initial_conditions(cfg, mesh))["values"]["WATER DEPTH"]
    cfg.initialization.prewet_min_depth = 0.15
    depth_floor = selafin.read_slf(
        write_initial_conditions(cfg, mesh))["values"]["WATER DEPTH"]

    # the inflow plug is re-imposed after the floor, so exclude it from the comparison
    from axqua.steering import _inflow_plug_mask

    plug = _inflow_plug_mask(cfg, mesh)
    thin = (depth_all > 0) & (depth_all < 0.15) & ~plug
    assert thin.any(), "the V section must produce a thin margin to drop"
    assert np.all(depth_floor[thin] == 0.0)
    # nothing below the floor survives, and the deep water is untouched
    kept = (depth_floor > 0) & ~plug
    assert depth_floor[kept].min() >= 0.15
    assert np.allclose(depth_floor[kept], depth_all[kept])


def test_prewet_always_wets_the_inflow_section(tmp_path):
    """Whatever the seed and its filters leave behind, the inflow cross-section must
    end up wet: TELEMAC's DEBIMP scales the prescribed discharge by the depth
    integral along that section, so a dry inflow aborts the run at t=0."""
    from axqua import selafin
    from axqua.steering import _inflow_plug_mask, write_initial_conditions

    cfg, mesh, _ = _straight_reach(tmp_path)
    plug = _inflow_plug_mask(cfg, mesh)
    assert plug.any(), "the fixture must produce an inflow plug"

    # a floor high enough to wipe out the whole synthetic seed
    cfg.initialization.prewet_min_depth = 5.0
    depth = selafin.read_slf(
        write_initial_conditions(cfg, mesh))["values"]["WATER DEPTH"]

    assert (depth[plug] >= cfg.initialization.dry_start_depth - 1e-9).all()
    assert np.all(depth[~plug] == 0.0)     # nothing else survived that floor


def test_prewet_falls_back_to_constant_without_a_centerline(tmp_path):
    """No centerline (or no discharge) -> the older constant-depth seed, so a case
    that cannot size a normal depth still builds."""
    from axqua import selafin
    from axqua.steering import _inflow_plug_mask, write_initial_conditions

    cfg, mesh, _ = _straight_reach(tmp_path)
    cfg.geodata.channel_centerline = None
    depth = selafin.read_slf(
        write_initial_conditions(cfg, mesh))["values"]["WATER DEPTH"]
    # constant prewet_depth on the mask (the inflow plug is deeper by design)
    plug = _inflow_plug_mask(cfg, mesh)
    assert np.isclose(depth[~plug].max(), 0.30)
    assert np.allclose(depth[~plug][depth[~plug] > 0], 0.30)


def test_water_table_pool_survives_both_seed_filters(tmp_path):
    """A closed depression fed by the bar's water table must be seeded and must
    survive the filters that exist to remove *unsupported* water.

    It cannot fill any other way: it sits on a bar above the channel, so no surface
    flow reaches it, and `drainable_prewet` refuses by design to seed behind a rim.
    Ordinary trapped ground with no water table must still be dropped.
    """
    import geopandas as gpd
    from shapely.geometry import Polygon

    from axqua import selafin
    from axqua.steering import write_initial_conditions

    cfg, mesh, cross = _straight_reach(tmp_path)
    bottom = np.asarray(mesh.bottom, float).copy()
    # two identical bowls 0.5 m deep, both closed; only the first is inside a patch
    for cx, cy in ((100.0, 0.0), (200.0, 0.0)):
        bowl = np.exp(-(((mesh.x - cx) / 6.0) ** 2 + ((cross - cy) / 6.0) ** 2))
        bottom -= 0.5 * bowl
    mesh.bottom = bottom

    zone = tmp_path / "zone.gpkg"
    gpd.GeoDataFrame(
        {"Patch name": ["bar"], "porous depth (m)": [0.5]},
        geometry=[Polygon([(85, -15), (115, -15), (115, 15), (85, 15)])],
        crs="EPSG:25832").to_file(zone, driver="GPKG")
    cfg.percolation.zone = zone
    cfg.percolation.mode = "fortran"
    cfg.percolation.water_table = "phreatic"
    # a table just above the first bowl's floor (bed there ~ 100 - 0.005*100 - 0.5)
    level = float(bottom[np.argmin(np.hypot(mesh.x - 100.0, cross))]) + 0.25
    cfg.percolation.water_table_levels = {"losing": level, "gaining": level}
    cfg.initialization.drainable_prewet = True

    depth = np.asarray(selafin.read_slf(
        write_initial_conditions(cfg, mesh))["values"]["WATER DEPTH"], float)

    in_patch = (np.abs(mesh.x - 100.0) < 8) & (np.abs(cross) < 8)
    elsewhere = (np.abs(mesh.x - 200.0) < 8) & (np.abs(cross) < 8)
    assert depth[in_patch].max() > 0.2, "the supported pool must be seeded"
    # the unsupported twin is dropped by the drainable filter (it is behind a rim
    # and has no source), which is what makes this a real test of the exemption
    assert depth[elsewhere].max() < depth[in_patch].max()


def test_prescribed_flowrates_split_across_inflows(tmp_path):
    """The total reach Q is distributed across multiple inflow boundaries by node
    count (not prescribed in full on each, which would multiply the supplied
    discharge and flood the domain)."""
    from axqua.boundary import LiquidBoundary
    from axqua.steering import _prescribed_arrays

    cfg = _minimal_cfg(tmp_path)
    # two inflows (75 + 25 nodes) and one outflow
    liquids = [LiquidBoundary(index=1, kind="inflow", n_nodes=75),
               LiquidBoundary(index=2, kind="outflow", n_nodes=80),
               LiquidBoundary(index=3, kind="inflow", n_nodes=25)]
    flow, elev, prof = _prescribed_arrays(cfg, liquids, inflow_q=47.0, outflow_wse=373.8)

    shares = [float(v) for v in flow.split(";")]
    assert shares == [35.25, 0.0, 11.75]          # 47 * 75/100, outflow 0, 47 * 25/100
    assert abs(sum(shares) - 47.0) < 1e-9         # total inflow == reach Q
    assert elev.split(";")[1] == "373.8000"       # outflow stage on the outflow slot


def test_steering_finite_element_default_vs_finite_volume(tmp_path):
    """Default steering is now finite elements (the FE numerics + lateral wall
    friction + the configured turbulence model); opting into finite_volumes=True
    emits the HLLC FV numerics and forces turbulence model 1 instead."""
    from axqua.boundary import LiquidBoundary
    from axqua.steering import write_cas

    cfg = _minimal_cfg(tmp_path)
    liquids = [LiquidBoundary(index=1, kind="inflow", n_nodes=10),
               LiquidBoundary(index=2, kind="outflow", n_nodes=10)]

    # default kernel: finite elements with the resolved turbulence model (here 3)
    fe = write_cas(cfg, liquids, inflow_q=47.0, outflow_wse=373.8,
                   turbulence_model=3).read_text()
    assert "SAINT-VENANT FV" not in fe
    assert "SCHEME FOR ADVECTION OF VELOCITIES : 14" in fe
    assert "TIDAL FLATS : YES" in fe and "SOLVER : 2" in fe
    assert "LAW OF FRICTION ON LATERAL BOUNDARIES" in fe   # FE supports wall friction
    assert "TURBULENCE MODEL : 3" in fe                    # FE keeps the resolved model
    assert "PRINTING CUMULATED FLOWRATES : YES" in fe
    assert "VARIABLE TIME-STEP : YES" in fe

    # opting into finite volumes: HLLC + constant viscosity, no FE-only keywords
    cfg.hydrodynamics.finite_volumes = True
    fv = write_cas(cfg, liquids, inflow_q=47.0, outflow_wse=373.8,
                   turbulence_model=3).read_text()
    assert "EQUATIONS : 'SAINT-VENANT FV'" in fv
    assert "FINITE VOLUME SCHEME : 5" in fv and "FINITE VOLUME SCHEME SPACE ORDER : 2" in fv
    assert "TURBULENCE MODEL : 1" in fv                    # forced (constant viscosity)
    assert "SCHEME FOR ADVECTION OF VELOCITIES" not in fv
    assert "TIDAL FLATS" not in fv and "SOLVER :" not in fv
    assert "LAW OF FRICTION ON LATERAL BOUNDARIES" not in fv


def test_turbulence_model_auto_selection(tmp_path):
    """'auto' picks Smagorinski LES once the filter width is at most one flow depth and
    k-epsilon on every coarser mesh, never Spalart-Allmaras; an explicit setting
    overrides."""
    from axqua.steering import select_turbulence_model

    cfg = _minimal_cfg(tmp_path)
    cfg.hydrodynamics.turbulence_length_scale = 1.0     # depth 1 m

    cfg.mesh.channel_size = 0.9                          # finer than the depth -> LES
    assert select_turbulence_model(cfg, None)[0] == 4
    cfg.mesh.channel_size = 1.1                          # coarser than the depth
    assert select_turbulence_model(cfg, None)[0] == 3   # k-epsilon
    cfg.mesh.channel_size = 20.0                         # very coarse: still k-epsilon
    assert select_turbulence_model(cfg, None)[0] == 3
    cfg.mesh.channel_size = 0.9
    cfg.hydrodynamics.les_depth_ratio = 0.5              # a stricter LES gate
    assert select_turbulence_model(cfg, None)[0] == 3

    cfg.hydrodynamics.turbulence_model = "k-epsilon"    # explicit name overrides
    assert select_turbulence_model(cfg, None)[0] == 3
    cfg.hydrodynamics.turbulence_model = "mixing-length"
    assert select_turbulence_model(cfg, None)[0] == 5
    cfg.hydrodynamics.turbulence_model = 6              # explicit int overrides
    model, why = select_turbulence_model(cfg, None)
    assert model == 6 and "not a river" in why          # width unknown: warned


def test_spalart_allmaras_fits_a_flume_and_not_a_river():
    from axqua.steering import FlowScale, spalart_allmaras_note

    assert spalart_allmaras_note(None, FlowScale(1.0, "x", width=8.0)) == ""
    note = spalart_allmaras_note(None, FlowScale(0.9, "x", width=90.0))
    assert "100 depths wide" in note and "not a river" in note


def test_filter_width_is_that_of_telemac_smagorinski():
    """sqrt(2 A): 0.93 times the edge of an equilateral triangle, the edge of a
    right-isosceles one; restricted to the triangles with two masked nodes."""
    import numpy as np

    from axqua.steering import filter_width

    a = 2.0
    x = np.array([0.0, a, a / 2, 10.0, 10.0 + a, 10.0])
    y = np.array([0.0, 0.0, a * np.sqrt(3) / 2, 0.0, 0.0, a])
    tri = np.array([[0, 1, 2], [3, 4, 5]])
    assert abs(filter_width(x, y, tri[:1]) - a * np.sqrt(np.sqrt(3) / 2)) < 1e-12
    assert abs(filter_width(x, y, tri[1:]) - a) < 1e-12
    mask = np.array([False, False, False, True, True, True])
    assert abs(filter_width(x, y, tri, mask) - a) < 1e-12


def test_flow_scale_reads_the_steady_result(tmp_path, caplog):
    """The depth is the median wetted depth of the steady result; without one, the
    pre-wetting depth, and without that 1 m with a warning."""
    import logging

    import numpy as np

    from axqua.core import selafin
    from axqua.steering import flow_scale

    cfg = _minimal_cfg(tmp_path)
    with caplog.at_level(logging.WARNING, logger="axqua"):
        scale = flow_scale(cfg)
    assert scale.depth == 1.0 and "assumed" in scale.source
    assert any("no flow depth known" in r.message for r in caplog.records)

    cfg.initialization.prewet_depth = 0.4
    assert flow_scale(cfg).depth == 0.4

    x = np.array([0.0, 1.0, 0.0, 1.0, 2.0])
    y = np.array([0.0, 0.0, 1.0, 1.0, 0.0])
    selafin.write_initial_state(
        cfg.model_path(cfg.results_slf), x=x, y=y,
        ikle=np.array([[1, 2, 3], [2, 4, 3], [2, 5, 4]]), ipobo=np.array([1, 5, 4, 3, 0]),
        depth=np.array([0.5, 0.7, 0.9, 1.1, 0.0]),
        velocity_u=np.array([0.3, 0.4, 0.5, 0.6, 0.0]), velocity_v=np.zeros(5))
    scale = flow_scale(cfg)
    assert abs(scale.depth - 0.8) < 1e-6            # the dry node does not count
    assert abs(scale.velocity - 0.45) < 1e-6
    assert "steady result" in scale.source
    cfg.hydrodynamics.turbulence_length_scale = 2.5  # an explicit depth wins
    assert flow_scale(cfg).depth == 2.5


def test_turbulence_routines_follow_the_model(tmp_path):
    """LES writes user_fortran/smagor.f with the bed term, k-epsilon corvis.f with the
    shallow-node reset; each names the folder once, a change of model removes the
    generated routine of the other, and a routine of the user is never touched."""
    from axqua import steering
    from axqua.boundary import LiquidBoundary
    from axqua.solvers.telemac import turbulence_fortran as tf

    cfg = _minimal_cfg(tmp_path)
    liquids = [LiquidBoundary(index=1, kind="inflow", n_nodes=10),
               LiquidBoundary(index=2, kind="outflow", n_nodes=10)]
    folder = cfg.model_path(cfg.user_fortran_dir)
    smagor, corvis = folder / tf.SMAGOR, folder / tf.CORVIS

    def cas(model):
        return steering.write_cas(cfg, liquids, inflow_q=40.0, outflow_wse=373.4,
                                  turbulence_model=model).read_text()

    text = cas(4)
    assert "TURBULENCE MODEL : 4" in text and ",D'" in text
    assert text.count("FORTRAN FILE : 'user_fortran'") == 1
    src = smagor.read_text()
    assert src.startswith(tf.MARKER) and "SUBROUTINE SMAGOR" in src
    assert "ALPHAB = 6.670000D-02" in src and "ALPHAB*USBEDL*HBEDLE" in src
    assert max(len(line) for line in src.splitlines()) <= 72         # fixed form
    assert not corvis.exists() and not tf.three_d_routines(folder)  # 2D only

    text = cas(3)
    assert text.count("FORTRAN FILE : 'user_fortran'") == 1 and ",K,E'" in text
    src = corvis.read_text()
    assert "SUBROUTINE CORVIS" in src and "HSHALW = 2.000000D-02" in src
    assert "RLMAXK = 1.000000D+00" in src and "CMU34*MAX(AK%R(N),0.D0)**1.5D0" in src
    assert max(len(line) for line in src.splitlines()) <= 72
    assert not smagor.exists() and not tf.three_d_routines(folder)

    text = cas(1)                                       # no routine, no folder
    assert "FORTRAN FILE" not in text and not folder.exists()

    cfg.hydrodynamics.les_bed_coefficient = 0.0          # TELEMAC's own Smagorinski
    assert "FORTRAN FILE" not in cas(4) and not folder.exists()
    cfg.hydrodynamics.keps_shallow_depth = 0.0           # the limiter alone
    assert "FORTRAN FILE" in cas(3)
    cfg.hydrodynamics.keps_length_limit = 0.0            # TELEMAC's own k-epsilon
    assert "FORTRAN FILE" not in cas(3)

    folder.mkdir(parents=True)
    smagor.write_text("      SUBROUTINE SMAGOR\n")       # the user's own routine
    cfg.hydrodynamics.les_bed_coefficient = 0.0667
    cas(4)
    assert smagor.read_text() == "      SUBROUTINE SMAGOR\n"
    assert tf.three_d_routines(folder)
    cas(3)
    assert smagor.exists()


def test_unsteady_cas_and_liquid_boundaries(tmp_path):
    """A hydrograph yields a liquid-boundaries file (Q(t)/SL(t)) and an unsteady
    cas referencing it with a DURATION; the steady writer is unaffected."""
    import numpy as np

    from axqua import steering
    from axqua.boundary import LiquidBoundary
    from axqua.hydraulics import Inflow

    cfg = _minimal_cfg(tmp_path)
    liquids = [LiquidBoundary(index=1, kind="inflow", n_nodes=30),
               LiquidBoundary(index=2, kind="outflow", n_nodes=10),
               LiquidBoundary(index=3, kind="inflow", n_nodes=10)]
    inflow = Inflow(times_s=np.array([0.0, 3600.0, 7200.0]),
                    discharge=np.array([40.0, 60.0, 50.0]), steady_value=50.0)

    lb = steering.write_liquid_boundaries(cfg, liquids, inflow,
                                          outflow_wse_fn=lambda q: 373.0 + 0.01 * q)
    txt = lb.read_text()
    assert lb.name == cfg.liquid_boundaries_file
    header = txt.splitlines()[1].split()
    assert header == ["T", "Q(1)", "SL(2)", "Q(3)"]     # boundary-index order
    # total reach Q split across the two inflows by node share (30:10 of 40 at t=0)
    first = txt.splitlines()[3].split()
    assert float(first[1]) == 30.0 and float(first[3]) == 10.0

    cas = steering.write_cas(
        cfg, liquids, inflow_q=40.0, outflow_wse=373.4, turbulence_model=3,
        unsteady=True, liquid_boundaries_file=lb.name, duration=7200.0,
        out_name=cfg.unsteady_cas_file).read_text()
    assert f"LIQUID BOUNDARIES FILE : {cfg.liquid_boundaries_file}" in cas
    assert "DURATION : 7200.0" in cas and "NUMBER OF TIME STEPS" not in cas
    assert "TITLE : 'prewet-test unsteady'" in cas


def _minimal_cfg(tmp_path):
    from axqua.config import (
        Boundaries, Calibration, Config, Friction, Geodata, GroundTruth,
        Hydrodynamics, Initialization, MeshConfig, Morphodynamics, TelemacEnv,
    )

    model = tmp_path / "model"
    model.mkdir()
    dummy = tmp_path / "dummy"
    dummy.write_text("")
    return Config(
        name="prewet-test", crs_epsg=25832, config_dir=tmp_path,
        preprocessing_dir=tmp_path, model_dir=model, postprocessing_dir=tmp_path,
        calibration_dir=tmp_path,
        telemac=TelemacEnv(pysource=dummy),
        geodata=Geodata(dem_initial=dummy, boundary=dummy),
        boundaries=Boundaries(liquid_boundaries=dummy),
        initialization=Initialization(),
        mesh=MeshConfig(), friction=Friction(), hydrodynamics=Hydrodynamics(),
        morphodynamics=Morphodynamics(), ground_truth=GroundTruth(),
        calibration=Calibration(),
    )


def test_write_cas_hotstart_keywords(tmp_path):
    from axqua import steering
    from axqua.boundary import LiquidBoundary

    cfg = _minimal_cfg(tmp_path)
    liquids = [LiquidBoundary(index=1, kind="inflow", n_nodes=5),
               LiquidBoundary(index=2, kind="outflow", n_nodes=5)]

    # without pre-wetting: the analytical initial condition is used
    plain = steering.write_cas(cfg, liquids, inflow_q=47.0, outflow_wse=379.5).read_text()
    assert "INITIAL CONDITIONS :" in plain
    assert "COMPUTATION CONTINUED" not in plain

    # with a hotstart file: continue the run from it, clock reset to zero.
    # Since TELEMAC 9.0 the PREVIOUS COMPUTATION FILE keyword alone triggers the
    # continuation (the boolean COMPUTATION CONTINUED keyword was removed).
    hot = steering.write_cas(cfg, liquids, inflow_q=47.0, outflow_wse=379.5,
                             previous_computation="initial-conditions.slf").read_text()
    assert "COMPUTATION CONTINUED" not in hot
    assert "PREVIOUS COMPUTATION FILE : initial-conditions.slf" in hot
    assert "INITIAL TIME SET TO ZERO : YES" in hot
    assert "INITIAL CONDITIONS :" not in hot


def test_steady_state_auto_stop_is_opt_in_and_fixed_step_only(tmp_path):
    """The steady-state auto-stop is OFF by default. Even opted in it is suppressed
    with a variable time step (TELEMAC's absolute per-step STOP CRITERIA false-fires on
    the tiny CFL dt); it is emitted only for a steady, fixed-step run, never unsteady."""
    from axqua import steering
    from axqua.boundary import LiquidBoundary

    cfg = _minimal_cfg(tmp_path)
    liquids = [LiquidBoundary(index=1, kind="inflow", n_nodes=5),
               LiquidBoundary(index=2, kind="outflow", n_nodes=5)]

    # off by default
    default = steering.write_cas(cfg, liquids, inflow_q=47.0, outflow_wse=379.5).read_text()
    assert "STOP IF A STEADY STATE IS REACHED" not in default

    # opted in but still variable time step -> suppressed (the unreliable case)
    cfg.hydrodynamics.stop_if_steady = True
    var = steering.write_cas(cfg, liquids, inflow_q=47.0, outflow_wse=379.5).read_text()
    assert "STOP IF A STEADY STATE IS REACHED" not in var

    # opted in with a fixed time step -> emitted
    cfg.hydrodynamics.variable_timestep = False
    fixed = steering.write_cas(cfg, liquids, inflow_q=47.0, outflow_wse=379.5).read_text()
    assert "STOP IF A STEADY STATE IS REACHED : YES" in fixed
    assert "STOP CRITERIA : 1.E-4;1.E-4;1.E-4" in fixed

    # never on the unsteady hydrograph run, even fixed-step + opted in
    uns = steering.write_cas(cfg, liquids, inflow_q=47.0, outflow_wse=379.5,
                             unsteady=True, duration=3600.0,
                             liquid_boundaries_file="hg.liq",
                             out_name=cfg.unsteady_cas_file).read_text()
    assert "STOP IF A STEADY STATE IS REACHED" not in uns


def test_turbulence_solver_accuracy(tmp_path):
    """The turbulence-transport solver accuracy is loosened from TELEMAC's tight 1e-9
    default (ACCURACY OF K/EPSILON for k-epsilon, ACCURACY OF SPALART-ALLMARAS for S-A);
    constant viscosity / Smagorinski solve no transport equation, so none is written."""
    from axqua import steering
    from axqua.boundary import LiquidBoundary

    cfg = _minimal_cfg(tmp_path)
    liquids = [LiquidBoundary(index=1, kind="inflow", n_nodes=5),
               LiquidBoundary(index=2, kind="outflow", n_nodes=5)]

    keps = steering.write_cas(cfg, liquids, 47.0, 379.5, turbulence_model=3).read_text()
    assert "ACCURACY OF K : 1e-06" in keps and "ACCURACY OF EPSILON : 1e-06" in keps
    assert "SOLVER FOR K-EPSILON MODEL : 7" in keps      # GMRES, not symmetric

    sa = steering.write_cas(cfg, liquids, 47.0, 379.5, turbulence_model=6).read_text()
    assert "ACCURACY OF SPALART-ALLMARAS : 1e-06" in sa and "ACCURACY OF K" not in sa
    assert "SOLVER FOR K-EPSILON MODEL" not in sa

    const = steering.write_cas(cfg, liquids, 47.0, 379.5, turbulence_model=1).read_text()
    assert "ACCURACY OF" not in const


def test_variable_timestep_steady_run_bounded_by_duration(tmp_path):
    """A VARIABLE TIME-STEP steady run is bounded by DURATION (NUMBER OF TIME STEPS
    does not terminate a CFL-driven variable-dt run); a fixed-step run keeps using
    NUMBER OF TIME STEPS."""
    from axqua import steering
    from axqua.boundary import LiquidBoundary

    cfg = _minimal_cfg(tmp_path)
    cfg.hydrodynamics.n_time_steps = 15000
    cfg.hydrodynamics.time_step = 1.0
    liquids = [LiquidBoundary(index=1, kind="inflow", n_nodes=5),
               LiquidBoundary(index=2, kind="outflow", n_nodes=5)]

    var = steering.write_cas(cfg, liquids, 47.0, 379.5).read_text()
    assert "DURATION : 15000.0" in var and "NUMBER OF TIME STEPS" not in var

    cfg.hydrodynamics.variable_timestep = False
    fix = steering.write_cas(cfg, liquids, 47.0, 379.5).read_text()
    assert "NUMBER OF TIME STEPS : 15000" in fix and "DURATION" not in fix
