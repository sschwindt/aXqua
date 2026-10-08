"""A point layer as ground truth: named the way a GIS names things, one point per reading.

The September-2025 campaign of the Isar case is such a layer. It heads its columns with
units (``v(x) [m/s]``, ``Total Depth [m]``) and holds one point per *reading*, three on
top of each other where a vertical was sampled at 0.2, 0.6 and 0.8 of the depth. Before
these tests existed it loaded without a word and yielded nothing: no column was
recognised, and each vertical would have counted three times.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from axqua.ground_truth import (_canonical_columns, read_points, select_point_profiles)
from axqua.ground_truth_qa import check_ground_truth_elevations


def _layer(tmp_path, rows, name="campaign.gpkg"):
    import geopandas as gpd
    from shapely.geometry import Point

    frame = pd.DataFrame(rows)
    geometry = [Point(x, y) for x, y in zip(frame.pop("_x"), frame.pop("_y"))]
    path = tmp_path / name
    gpd.GeoDataFrame(frame, geometry=geometry, crs="EPSG:25832").to_file(path, driver="GPKG")
    return path


def _reading(x, y, measured, total, vx, vy=0.0, point="p"):
    return {"_x": x, "_y": y, "Point": point, "Meas. Depth [m]": measured,
            "Total Depth [m]": total, "v(x) [m/s]": vx, "v(y) [m/s]": vy,
            "v(z) [m/s]": 0.0, "TKE [m²/s²]": 0.01}


#: one vertical with three readings (0.2 / 0.6 / 0.8 of a 0.5 m column, from the
#: surface) and one vertical with a single reading
THREE_AND_ONE = [
    _reading(100.0, 200.0, 0.10, 0.50, 1.00, point="a-0.2"),
    _reading(100.0, 200.0, 0.30, 0.50, 0.80, point="a-0.6"),
    _reading(100.0, 200.0, 0.40, 0.50, 0.50, point="a-0.8"),
    _reading(110.0, 200.0, 0.12, 0.20, 0.30, point="b-0.6"),
]


def test_a_unit_in_brackets_is_not_part_of_the_name():
    frame = pd.DataFrame(columns=["v(x) [m/s]", "v(y) [m/s]", "v(z) [m/s]", "Total Depth [m]",
                                  "Meas. Depth [m]", "TKE [m²/s²]", "East.", "North.",
                                  "Something Else [-]"])
    assert list(_canonical_columns(frame).columns) == [
        "x", "y", "u", "v", "w", "h", "h_meas", "tke", "something else"]


def test_two_headers_for_one_quantity_do_not_produce_a_table_in_a_column():
    frame = pd.DataFrame({"x": [1.0], "East.": [9.0], "y": [2.0]})
    out = _canonical_columns(frame)
    assert list(out.columns) == ["x", "y"] and out["x"].iloc[0] == 1.0


def test_a_layer_with_units_in_its_headers_is_read(tmp_path):
    table = read_points(_layer(tmp_path, THREE_AND_ONE), 25832)
    assert {"x", "y", "z", "u", "v", "w", "h", "h_meas", "tke"} <= set(table.columns)
    assert len(table) == 4
    assert table["x"].tolist() == [100.0, 100.0, 100.0, 110.0]   # the geometry


def test_a_vertical_contributes_one_target_the_reading_nearest_0_6(tmp_path, caplog):
    table = read_points(_layer(tmp_path, THREE_AND_ONE), 25832)
    with caplog.at_level(logging.INFO, logger="axqua"):
        chosen = select_point_profiles(table)
    assert chosen["point"].tolist() == ["a-0.6", "b-0.6"]
    assert "2 of 4 reading(s) kept" in caplog.text


def test_the_other_rules_apply_to_a_point_layer_as_they_do_to_a_workbook(tmp_path):
    table = read_points(_layer(tmp_path, THREE_AND_ONE), 25832)
    assert len(select_point_profiles(table, "all")) == 4
    assert select_point_profiles(table, "drop-lowest")["point"].tolist() == \
        ["a-0.2", "a-0.6", "b-0.6"]
    averaged = select_point_profiles(table, "depth-average")
    # USGS three-point rule on the components: (u_0.2 + 2 u_0.6 + u_0.8) / 4
    assert averaged["u"].tolist() == pytest.approx([(1.00 + 2 * 0.80 + 0.50) / 4, 0.30])


def test_a_layer_with_one_reading_per_position_comes_back_as_it_was(tmp_path):
    rows = [_reading(100.0 + i, 200.0, 0.12, 0.20, 0.3, point=f"p{i}") for i in range(4)]
    table = read_points(_layer(tmp_path, rows), 25832)
    pd.testing.assert_frame_equal(select_point_profiles(table), table)


def test_a_named_column_decides_what_a_vertical_is(tmp_path):
    """Two rods a few centimetres apart are two verticals unless the layer says not."""
    rows = [dict(_reading(100.00, 200.0, 0.10, 0.50, 1.0), Vertical="V1"),
            dict(_reading(100.30, 200.0, 0.30, 0.50, 0.8), Vertical="V1"),
            dict(_reading(105.00, 200.0, 0.12, 0.20, 0.3), Vertical="V2")]
    table = read_points(_layer(tmp_path, rows), 25832)
    assert len(select_point_profiles(table)) == 3
    assert len(select_point_profiles(table, group_key="Vertical")) == 2


def test_a_source_of_kind_points_compiles_to_one_row_per_vertical(tmp_path):
    from axqua.config import GroundTruthSource
    from axqua.ground_truth import _compile_source

    layer = _layer(tmp_path, THREE_AND_ONE)
    source = GroundTruthSource(category="hydraulics", kind="points", positions=layer)
    assert len(_compile_source(source, 25832)) == 2
    every = GroundTruthSource(category="hydraulics", kind="points", positions=layer,
                              profile="all")
    assert len(_compile_source(every, 25832)) == 4


def test_absent_elevations_are_not_judged_as_a_survey(tmp_path, caplog):
    """The placeholder column of zeros once read as a constant offset of -816 m."""
    class Cfg:
        ground_truth_path = tmp_path / "unused.xlsx"

    table = pd.DataFrame({"x": np.arange(8.0), "y": 0.0, "z": 0.0, "h": 0.3})
    with caplog.at_level(logging.DEBUG, logger="axqua"):
        assert check_ground_truth_elevations(Cfg(), tables={"hydraulics": table}) == []
    assert "no elevations" in caplog.text
