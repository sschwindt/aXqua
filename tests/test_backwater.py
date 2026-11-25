"""How far upstream a change of the outflow level reaches (:mod:`axqua.solvers.telemac.backwater`).

A straight rectangular channel in uniform flow, written with aXqua's own SELAFIN writer: the linearized decay rate is known in closed form there, ``kappa = (3 + 2 / ln(11.036 h / ks)) S / (h (1 - Fr^2))``, so the reach the check measures has to match it.
"""

from __future__ import annotations

import json
import logging
import math

import numpy as np
import pytest

from axqua.core import selafin

LENGTH, HALF, DX = 1000.0, 10.0, 5.0
DEPTH, KS, SLOPE = 1.0, 0.1, 1e-3
G, KARMAN = 9.81, 0.41
SPEED = math.sqrt(G * DEPTH * SLOPE) * math.log(11.036 * DEPTH / KS) / KARMAN


def _grid():
    s = np.arange(0.0, LENGTH + DX / 2, DX)
    c = np.arange(-HALF, HALF + 0.5, 1.0)
    S, C = np.meshgrid(s, c, indexing="ij")
    nc = c.size
    tri = []
    for i in range(s.size - 1):
        for j in range(nc - 1):
            a, b = i * nc + j, i * nc + j + 1
            d, e = (i + 1) * nc + j, (i + 1) * nc + j + 1
            tri += [[a, b, d], [b, e, d]]
    return S.ravel(), C.ravel(), np.array(tri) + 1


def _case(tmp_path, *, depth=None, speed=None, patch=False):
    """A built case: geometry, steady result, centerline and liquid lines.

    *patch* adds still water beside the channel at x 90 to 110 m whose level is that of
    the channel 600 m from the outlet (a pool behind a bar, fed from upstream), and a
    dry bank at x 200 to 210 m.
    """
    import geopandas as gpd
    from shapely.geometry import LineString

    from axqua.config import (
        Boundaries, Calibration, Config, Friction, Geodata, GroundTruth,
        Hydrodynamics, Initialization, MeshConfig, Morphodynamics, TelemacEnv,
    )

    x, y, ikle = _grid()
    bottom = SLOPE * x                       # the outlet is at x = 0
    h = np.full(x.size, DEPTH) if depth is None else depth(x)
    u = -(np.full(x.size, SPEED) if speed is None else speed(x))
    if patch:
        pool = (x >= 90) & (x <= 110) & (y >= 6)
        bottom = np.where(pool, SLOPE * 600.0 + DEPTH - 0.3, bottom)
        h = np.where(pool, 0.3, h)
        u = np.where(pool, 0.0, u)
        bank = (x >= 200) & (x <= 210) & (y >= 8)
        bottom = np.where(bank, 2.0, bottom)
        h = np.where(bank, 0.0, h)
        u = np.where(bank, 0.0, u)
    model = tmp_path / "model"
    model.mkdir()
    ipobo = np.zeros(x.size, dtype=int)
    selafin._write_selafin(model / "geometry.slf", x, y, ikle, ipobo,
                           [("BOTTOM", "M", bottom),
                            ("BOTTOM FRICTION", "", np.full(x.size, KS))],
                           title="backwater test", date=(2026, 1, 1, 0, 0, 0),
                           double_precision=True)
    selafin._write_selafin(model / "r2d.slf", x, y, ikle, ipobo,
                           [("VELOCITY U", "M/S", u), ("VELOCITY V", "M/S", 0 * u),
                            ("WATER DEPTH", "M", h), ("FREE SURFACE", "M", bottom + h),
                            ("BOTTOM", "M", bottom)],
                           title="backwater test", date=(2026, 1, 1, 0, 0, 0),
                           double_precision=False)
    lb = tmp_path / "lb.gpkg"
    gpd.GeoDataFrame(
        {"Type (inflow/outflow)": ["outflow", "inflow"]},
        geometry=[LineString([(0.0, -HALF), (0.0, HALF)]),
                  LineString([(LENGTH, -HALF), (LENGTH, HALF)])],
        crs="EPSG:25832").to_file(lb, driver="GPKG")
    centerline = tmp_path / "centerline.gpkg"
    # digitized from the outlet upstream on purpose: the check orients the line itself
    gpd.GeoDataFrame(geometry=[LineString([(0.0, 0.0), (LENGTH, 0.0)])],
                     crs="EPSG:25832").to_file(centerline, driver="GPKG")
    dummy = tmp_path / "dummy"
    dummy.write_text("")
    return Config(
        name="backwater-test", crs_epsg=25832, config_dir=tmp_path,
        preprocessing_dir=tmp_path, model_dir=model, postprocessing_dir=tmp_path,
        calibration_dir=tmp_path / "calibration",
        telemac=TelemacEnv(pysource=dummy),
        geodata=Geodata(dem_initial=dummy, boundary=dummy, channel_centerline=centerline),
        boundaries=Boundaries(liquid_boundaries=lb),
        initialization=Initialization(), mesh=MeshConfig(), friction=Friction(),
        hydrodynamics=Hydrodynamics(), morphodynamics=Morphodynamics(),
        ground_truth=GroundTruth(), calibration=Calibration(),
    )


def _expected_rate():
    froude2 = SPEED ** 2 / (G * DEPTH)
    return (3 + 2 / math.log(11.036 * DEPTH / KS)) * SLOPE / (DEPTH * (1 - froude2))


def _reach(cfg, **kwargs):
    from axqua.solvers.telemac.backwater import backwater_reach

    return backwater_reach(cfg, cfg.model_path("r2d.slf"),
                           geometry=cfg.model_path("geometry.slf"), **kwargs)


def test_uniform_flow_decays_at_the_rate_of_the_linear_theory(tmp_path):
    reach = _reach(_case(tmp_path))
    expected = math.log(10) / _expected_rate()               # 584 m
    assert reach.reach(0.1) == pytest.approx(expected, rel=0.03)
    assert reach.reach(0.1, long=False) == pytest.approx(expected, rel=0.03)
    # the conveyance gives the friction slope of the uniform flow, and the result's
    # energy line falls at the same slope
    assert np.median(reach.friction_slope) == pytest.approx(SLOPE, rel=0.02)
    assert np.median(reach.energy_slope) == pytest.approx(SLOPE, rel=0.02)
    assert reach.outlet_depth == pytest.approx(DEPTH, rel=0.01)
    assert reach.distance[0] < reach.distance[-1]
    assert np.all(np.diff(reach.long) <= 1e-12)


def test_a_supercritical_section_passes_no_change_upstream(tmp_path):
    shallow = lambda x: np.where(np.abs(x - 300.0) <= 6.0, 0.2, DEPTH)  # noqa: E731
    fast = lambda x: np.where(np.abs(x - 300.0) <= 6.0, 2.5, SPEED)    # noqa: E731
    reach = _reach(_case(tmp_path, depth=shallow, speed=fast))
    assert reach.froude.max() > 1
    assert np.all(reach.long[reach.distance > 310.0] == 0.0)
    assert reach.long[reach.distance < 290.0].min() > 0.2


def test_points_within_the_reach_of_a_fixed_outflow_level_are_warned_about(tmp_path,
                                                                           caplog):
    from axqua.solvers.telemac import backwater

    cfg = _case(tmp_path)
    points = {"near": {"x": [100.0, 150.0], "y": [0.0, 2.0]},
              "far": {"x": [900.0], "y": [-3.0]}}
    reach = _reach(cfg, points=points)
    near, far = reach.points
    rate = _expected_rate()
    assert near.long == pytest.approx(np.exp(-rate * np.array([100.0, 150.0])), abs=0.03)
    assert far.long[0] < backwater.REACH_SHARE
    assert near.depth_change[0] == pytest.approx(
        near.long[0] * backwater.OUTLET_CHANGE * DEPTH)
    found = reach.warnings()
    assert len(found) == 1
    assert found[0].startswith("2 of 2 points of near")
    assert "calibration.outflow_slope" in found[0]

    with caplog.at_level(logging.WARNING, logger="axqua.backwater"):
        backwater.check(cfg, cfg.model_path("r2d.slf"),
                        geometry=cfg.model_path("geometry.slf"), points=points)
    assert "points of near" in caplog.text

    cfg.calibration.outflow_slope = [1e-5, 2e-3]
    caplog.clear()
    reach = _reach(cfg, points=points)
    assert reach.outflow_calibrated and reach.warnings() == []
    assert "the outflow level is a calibration parameter" in reach.summary()[-1]


def test_a_point_takes_the_place_where_the_main_channel_has_its_water_level(tmp_path):
    """A pool beside the channel, fed from 600 m upstream, feels the outlet only
    through that place; a dry point belongs to the nearest water."""
    cfg = _case(tmp_path, patch=True)
    points = {"channel": {"x": [100.0], "y": [0.0]},
              "pool": {"x": [100.0], "y": [8.0]},
              "bank": {"x": [205.0], "y": [9.5]}}
    reach = _reach(cfg, points=points)
    channel, pool, bank = reach.points
    assert channel.distance[0] == pytest.approx(100.0, abs=10.0)
    assert not channel.by_level[0]
    assert pool.projected[0] == pytest.approx(100.0, abs=1.0)
    assert pool.distance[0] == pytest.approx(600.0, abs=10.0)
    assert pool.above[0] == pytest.approx(0.5, abs=0.02) and pool.by_level[0]
    rate = _expected_rate()
    assert pool.short[0] == pytest.approx(math.exp(-rate * 600.0), abs=0.03)
    assert channel.short[0] > 4 * pool.short[0]
    assert bank.distance[0] == pytest.approx(205.0, abs=15.0)
    assert "1 of them with the water level the main channel has" in "\n".join(
        reach.summary())


def test_the_steady_run_writes_and_records_the_reach(tmp_path):
    from axqua.solvers.telemac.backend import _record_backwater
    from axqua.workflow import report_backwater

    cfg = _case(tmp_path)
    lines = report_backwater(cfg, points={"near": {"x": [100.0], "y": [0.0]}})
    assert lines[0].startswith("backwater reach ->")
    assert "down to 50 %" in lines[1]
    folder = cfg.model_dir
    for suffix in (".csv", ".json", ".png"):
        assert (folder / f"backwater-reach{suffix}").exists()
    data = json.loads((folder / "backwater-reach.json").read_text())
    assert data["points"][0]["within"] == 1 and len(data["warnings"]) == 1
    header = (folder / "backwater-reach.csv").read_text().splitlines()[0]
    assert header.startswith("distance_from_outlet_m,") and "share_energy_slope" in header

    recorded = []

    class Ctx:
        def record(self, name, path, **kwargs):
            recorded.append((name, path.name, kwargs.get("kind")))

    _record_backwater(folder, Ctx())
    assert ("Backwater reach of the outflow level", "backwater-reach.png",
            "figure") in recorded


def test_without_a_result_or_a_centerline_the_steady_run_goes_on(tmp_path):
    from axqua.workflow import report_backwater

    cfg = _case(tmp_path)
    cfg.geodata.channel_centerline = None
    assert report_backwater(cfg)[0].startswith("backwater reach skipped")
    cfg.model_path("r2d.slf").unlink()
    assert report_backwater(cfg) == []


def test_the_warnings_reach_the_job_results():
    from axqua.jobs.executor import SURFACED_LOGGERS

    assert "axqua.backwater" in SURFACED_LOGGERS
