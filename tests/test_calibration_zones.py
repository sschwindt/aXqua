"""Which roughness zones a calibration varies, from where its points are (calibration_zones).

The user's decisions of 2026-11-11: a zone without a calibration point is not calibrated,
and a zone whose measured water is shallower than its largest ks gets that depth as its
largest ks (or is not calibrated when nothing is left above its smallest ks).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from test_roughness import _write_fixtures

X0, Y0, W, H = 700000.0, 5340000.0, 200.0, 60.0
LEFT, RIGHT = X0 + W * 0.25, X0 + W * 0.75        # in zone 1 and in zone 2


def _case(tmp_path, table="zone_id,ks_min,ks_max\n1,0.01,0.3\n2,0.05,0.5\n"):
    from axqua.config import load_config

    cfg = load_config(_write_fixtures(tmp_path))
    Path(cfg.geodata.roughness_table).write_text(table)
    return cfg


def _points(xs, depths=None):
    table = pd.DataFrame({"id": range(1, len(xs) + 1), "x": xs,
                          "y": [Y0 + H / 2] * len(xs), "z": 0.0})
    if depths is not None:
        table["WATER DEPTH_DATA"] = depths
    return table


def test_points_are_counted_in_the_zone_the_mesh_gives_them(tmp_path):
    from axqua.calibration_zones import point_zones

    cfg = _case(tmp_path)
    # the last point lies outside both polygons, nearest to zone 2
    ids, depths = point_zones(cfg, [_points([LEFT, RIGHT]),
                                    _points([X0 + W + 5.0], [0.4])])
    assert ids.tolist() == [1, 2, 2]
    assert np.isnan(depths[:2]).all() and depths[2] == 0.4


def test_a_zone_without_a_point_is_not_calibrated(tmp_path, caplog):
    from axqua.calibration_zones import EXCLUDED, zone_plan

    cfg = _case(tmp_path)
    plan = zone_plan(cfg, [_points([LEFT, LEFT], [0.8, 0.9])])
    assert [p.name for p in plan.parameters] == ["zone1"]
    (zone2,) = plan.excluded
    assert zone2.zone == 2 and zone2.decision == EXCLUDED and zone2.points == 0
    assert "none of the calibration points lies in it" in plan.warnings[0]
    assert plan.as_dict()["excluded_zones"] == [2]


def test_a_zone_shallower_than_its_largest_ks_is_capped_at_the_measured_depth(tmp_path):
    from axqua.calibration_zones import CAPPED, zone_plan

    cfg = _case(tmp_path)
    # zone 1: median 0.19 m below ks_max 0.30 m; zone 2: 0.6 m, deeper than 0.5 m
    plan = zone_plan(cfg, [_points([LEFT] * 3 + [RIGHT] * 2, [0.15, 0.19, 0.6, 0.6, 0.7])])
    ranges = {p.name: (p.min, p.max) for p in plan.parameters}
    assert ranges == {"zone1": (0.01, 0.19), "zone2": (0.05, 0.5)}
    (zone1,) = plan.capped
    assert zone1.decision == CAPPED and zone1.ks_max == 0.3 and zone1.ks_max_used == 0.19
    assert zone1.shallow_share == pytest.approx(2 / 3)
    assert "lowered from 0.3 m to 0.19 m" in plan.warnings[0]


def test_a_zone_whose_water_is_not_deeper_than_its_smallest_ks_is_not_calibrated(tmp_path):
    from axqua.calibration_zones import zone_plan

    cfg = _case(tmp_path, "zone_id,ks_min,ks_max\n1,0.01,0.3\n2,0.05,0.5\n")
    plan = zone_plan(cfg, [_points([LEFT, RIGHT, RIGHT], [0.5, 0.04, 0.05])])
    assert [p.name for p in plan.parameters] == ["zone1"]
    assert plan.excluded[0].zone == 2 and "not above ks_min" in plan.excluded[0].reason


def test_without_measured_depths_only_the_points_decide(tmp_path):
    from axqua.calibration_zones import zone_plan

    cfg = _case(tmp_path)
    plan = zone_plan(cfg, [_points([LEFT, RIGHT])])
    assert {p.name: p.max for p in plan.parameters} == {"zone1": 0.3, "zone2": 0.5}
    assert not plan.warnings


def test_other_parameters_and_fixed_zones_pass_through(tmp_path):
    from axqua.calibration_zones import zone_plan
    from axqua.config import CalibrationParameter

    cfg = _case(tmp_path, "zone_id,ks_min,ks_max,calibration\n1,0.01,0.3,True\n"
                          "2,0.05,0.5,False\n")
    cfg.calibration.parameters = [CalibrationParameter(name="VELOCITY DIFFUSIVITY",
                                                       min=0.001, max=0.05)]
    plan = zone_plan(cfg, [_points([LEFT], [0.9])])
    assert [p.name for p in plan.parameters] == ["VELOCITY DIFFUSIVITY", "zone1"]
    assert [z.zone for z in plan.zones] == [1]


def test_the_plan_is_written_and_a_resume_with_another_plan_is_refused(tmp_path):
    from axqua.bayescal import settle_zone_plan
    from axqua.calibration_zones import read_plan
    from axqua.core.errors import ConfigError

    cfg = _case(tmp_path)
    folder = tmp_path / "out"
    folder.mkdir()
    both = [_points([LEFT, RIGHT], [0.8, 0.9])]
    assert [p.name for p in settle_zone_plan(cfg, both, folder)] == ["zone1", "zone2"]
    assert read_plan(folder)["calibrated_zones"] == [1, 2]
    # the initial simulations of that calibration varied both zones
    with pytest.raises(ConfigError, match="other calibration parameters"):
        settle_zone_plan(cfg, [_points([LEFT], [0.8])], folder, resume=True)
    assert [p.name for p in settle_zone_plan(cfg, both, folder, resume=True)] == [
        "zone1", "zone2"]


def test_a_calibration_without_any_zone_left_is_refused(tmp_path):
    from axqua.bayescal import settle_zone_plan
    from axqua.core.errors import ConfigError

    cfg = _case(tmp_path, "zone_id,ks_min,ks_max\n1,0.01,0.3\n")
    with pytest.raises(ConfigError, match="no calibration parameter is left"):
        settle_zone_plan(cfg, [_points([RIGHT], [0.8])], tmp_path)


def test_the_plan_reaches_the_hydrobayescal_config(tmp_path):
    from axqua.calibration import emit_hbc_config
    from axqua.config import CalibrationParameter

    cfg = _case(tmp_path)
    cfg.ensure_dirs()
    path = emit_hbc_config(cfg, None, parameters=[
        CalibrationParameter(name="zone1", min=0.01, max=0.19)])
    text = Path(path).read_text()
    assert "'parameters': ['zone1']" in text and "[[0.01, 0.19]]" in text
