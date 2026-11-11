"""Unit tests for the multi-campaign velocity-CSV primitives (no solver)."""

import pytest
import numpy as np

from axqua import campaigns


def test_velocity_magnitude_and_error_floor():
    # horizontal speed = hypot(u, v); error propagated then floored
    vel, err = campaigns.velocity_and_error(
        u=[3.0, 0.0], v=[4.0, 0.0], u_err=[0.03, 0.0], v_err=[0.04, 0.0],
        vel_err_floor=0.01)
    assert np.allclose(vel, [5.0, 0.0])
    # point 0: sqrt((3*.03)^2+(4*.04)^2)/5 = sqrt(0.0081+0.0256)/5 = 0.03672...
    assert err[0] > 0.01
    assert np.isclose(err[0], np.sqrt(0.0081 + 0.0256) / 5.0)
    # point 1 (zero velocity / zero error) is lifted to the floor
    assert err[1] == 0.01


def test_error_floor_downweights_campaign():
    _, low = campaigns.velocity_and_error([0.1], [0.0], [0.001], [0.0], 0.01)
    _, high = campaigns.velocity_and_error([0.1], [0.0], [0.001], [0.0], 0.05)
    assert high[0] == 0.05 and low[0] == 0.01   # a higher floor => larger variance


def test_assemble_velocity_csv_schema():
    df = campaigns.assemble_velocity_csv(
        x=[1.0, 2.0], y=[3.0, 4.0], z=[10.0, 11.0],
        u=[0.3, 0.6], v=[0.4, 0.8], u_err=[0.0, 0.0], v_err=[0.0, 0.0],
        h=[0.5, 0.9], labels=["a", "b"], vel_err_floor=0.02, depth_err_floor=0.02)
    # HydroBayesCal reads the first four columns by position, then <QTY>_DATA/_ERROR
    assert list(df.columns)[:4] == ["id", "x", "y", "z"]
    assert list(df["id"]) == [1, 2]
    assert {"SCALAR VELOCITY_DATA", "SCALAR VELOCITY_ERROR",
            "WATER DEPTH_DATA", "WATER DEPTH_ERROR"} <= set(df.columns)
    assert np.allclose(df["SCALAR VELOCITY_DATA"], [0.5, 1.0])
    assert (df["WATER DEPTH_ERROR"] == 0.02).all()
    assert (df["SCALAR VELOCITY_ERROR"] == 0.02).all()   # zero prop error -> floor


def test_a_vertical_is_read_at_0_6_of_the_depth_above_the_bed():
    """% Depth counts from the surface; the target is 0.6 h above the bed on a log fit."""
    h, z0, ustar = 1.0, 0.003, 0.05
    readings = [{"pct": p, "h": h, "u": ustar / 0.41 * np.log((1 - p) * h / z0),
                 "v": 0.0, "u_err": 0.01, "v_err": 0.01} for p in (0.2, 0.6, 0.8)]
    rows = campaigns._at_profile_height({7: readings})
    assert list(rows) == [7]
    assert rows[7]["u"] == pytest.approx(ustar / 0.41 * np.log(0.6 * h / z0))


def test_compile_campaign_rejects_unknown_layout():
    try:
        campaigns.compile_campaign("bogus", values_xlsx="x.xlsx")
    except ValueError as e:
        assert "unknown campaign layout" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown layout")


def test_a_point_layer_gives_one_calibration_point_per_vertical(tmp_path):
    # the layout of the corrected campaign layers of 2026-10: one point per reading
    import geopandas as gpd
    from shapely.geometry import Point

    from axqua.campaigns import compile_campaign

    rows = [(100.0, 200.0, 0.10, 0.50, 1.0), (100.0, 200.0, 0.30, 0.50, 0.8),
            (100.0, 200.0, 0.40, 0.50, 0.6), (110.0, 200.0, 0.12, 0.20, 0.3)]
    layer = tmp_path / "campaign.gpkg"
    gpd.GeoDataFrame({"Meas. Depth (m)": [r[2] for r in rows],
                      "Total Depth (m)": [r[3] for r in rows],
                      "v_x (m/s)": [r[4] for r in rows], "v_y (m/s)": 0.0},
                     geometry=[Point(r[0], r[1]) for r in rows],
                     crs="EPSG:25832").to_file(layer, driver="GPKG")
    out = compile_campaign("points", layer=layer, crs_epsg=25832, label="sep")
    assert list(out["label"]) == ["sep-1", "sep-2"]
    # first vertical: 1.0, 0.8, 0.6 m/s at 0.4, 0.2, 0.1 m above the bed lie on
    # u = a ln z + b, read at 0.6 of 0.5 m above the bed; the second, one reading
    # 0.08 m above the bed of a 0.2 m column, goes to 0.12 m with the z0 of the first
    a = 0.2 / np.log(2.0)
    b = 1.0 - a * np.log(0.4)
    z0 = np.exp(-b / a)
    expected = [a * np.log(0.3) + b, 0.3 * np.log(0.12 / z0) / np.log(0.08 / z0)]
    assert out["SCALAR VELOCITY_DATA"].tolist() == pytest.approx(expected, abs=1e-6)
    assert out["WATER DEPTH_DATA"].tolist() == pytest.approx([0.5, 0.2])
