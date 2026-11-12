"""Which reading of a multi-depth vertical becomes a calibration target.

The load-bearing test in here is backwards compatibility: ``profile="single"``
must reproduce the historic behavior exactly, because every existing case config
relies on it without saying so.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from axqua.config import PROFILE_RULES as CONFIG_RULES
from axqua.config import GroundTruthSource
from axqua.ground_truth import PROFILE_RULES, select_profile_rows


def _profile_frame() -> pd.DataFrame:
    """Three verticals: two with three readings, one with a single reading.

    ``pct_depth`` is measured DOWN FROM THE SURFACE, and the readings are
    deliberately NOT in depth order - that is how the real workbook stores them.
    """
    rows = [
        # ID,   pct_depth,  u,     v
        (1, 0.93, 0.061, 0.083),      # nearest the bed
        (1, 0.60, 0.165, -0.055),
        (1, 0.20, 0.251, -0.089),     # nearest the surface
        (2, 0.20, 0.400, 0.000),
        (2, 0.93, 0.100, 0.000),
        (2, 0.60, 0.300, 0.000),
        (3, 0.60, 0.222, 0.010),      # lone reading
    ]
    return pd.DataFrame(rows, columns=["ID", "pct_depth", "u", "v"])


def test_config_rules_match_the_implementation():
    """config.py spells the rules out to stay import-light; they must not drift."""
    assert CONFIG_RULES == PROFILE_RULES


def test_single_is_a_no_op_on_one_row_per_vertical():
    """The historic shape: one row per vertical in, the same rows out."""
    df = pd.DataFrame({"ID": [1, 2, 3], "pct_depth": [0.6, 0.6, 0.6],
                       "u": [0.1, 0.2, 0.3], "v": [0.0, 0.0, 0.0]})
    out = select_profile_rows(df, "single")
    pd.testing.assert_frame_equal(out, df)


def test_single_keeps_one_row_per_vertical():
    out = select_profile_rows(_profile_frame(), "single")
    assert len(out) == 3
    assert out["ID"].tolist() == [1, 2, 3]
    # the 0.6-depth reading is the conventional single one
    assert out.set_index("ID").loc[1, "pct_depth"] == pytest.approx(0.60)


def test_all_keeps_every_reading():
    assert len(select_profile_rows(_profile_frame(), "all")) == 7


def test_drop_lowest_keeps_a_lone_reading():
    """'Drop the lowest' means 'when there is more than one'.

    Dropping single-reading verticals outright would discard KB15's entire
    downstream pool (IDs 1513-1520), which is 10 of its 30 verticals.
    """
    out = select_profile_rows(_profile_frame(), "drop-lowest")
    assert len(out) == 5                      # 2 + 2 + 1
    assert 3 in set(out["ID"])                # the lone reading survived
    assert out.groupby("ID").size().to_dict() == {1: 2, 2: 2, 3: 1}


def test_drop_lowest_removes_the_near_bed_reading():
    out = select_profile_rows(_profile_frame(), "drop-lowest")
    assert (out["pct_depth"] > 0.9).sum() == 0


def test_drop_lowest_is_by_height_not_row_order():
    """The workbook has a column literally called 'entry order fixed'."""
    df = _profile_frame()
    shuffled = df.sample(frac=1.0, random_state=11).reset_index(drop=True)
    key = ["ID", "pct_depth"]
    a = select_profile_rows(df, "drop-lowest").sort_values(key).reset_index(drop=True)
    b = select_profile_rows(shuffled, "drop-lowest").sort_values(key).reset_index(drop=True)
    pd.testing.assert_frame_equal(a[key], b[key])


def test_depth_average_reproduces_the_usgs_three_point_rule():
    """(u_0.2 + 2*u_0.6 + u_0.8) / 4, applied to the COMPONENTS."""
    out = select_profile_rows(_profile_frame(), "depth-average").set_index("ID")
    # vertical 2 flows along x only, so components and speeds agree there and the
    # arithmetic is checkable by hand
    assert out.loc[2, "u"] == pytest.approx((0.400 + 2 * 0.300 + 0.100) / 4.0)


def test_depth_average_uses_components_not_speeds():
    """Averaging speeds is a DIFFERENT, always-larger quantity when flow shears.

    TELEMAC's SCALAR VELOCITY is sqrt(U^2+V^2) built from depth-averaged
    components, i.e. the speed of the mean vector. On the real KB15 profiles,
    averaging the per-reading speeds instead reads high by up to +9.9%.
    """
    df = _profile_frame()
    out = select_profile_rows(df, "depth-average").set_index("ID")
    speed_of_mean = np.hypot(out.loc[1, "u"], out.loc[1, "v"])

    g = df[df.ID == 1]
    order = [g.index[(g.pct_depth - d).abs().argmin()] for d in (0.2, 0.6, 0.8)]
    spd = np.hypot(g.u, g.v)
    mean_speed = sum(w * spd.loc[i] for w, i in zip((1, 2, 1), order)) / 4.0

    assert mean_speed > speed_of_mean            # triangle inequality, always
    assert speed_of_mean == pytest.approx(0.1632, abs=1e-3)


def test_depth_average_falls_back_to_the_mean_below_three_readings():
    df = pd.DataFrame({"ID": [1, 1], "pct_depth": [0.2, 0.8],
                       "u": [0.4, 0.2], "v": [0.0, 0.0]})
    out = select_profile_rows(df, "depth-average")
    assert len(out) == 1
    assert out["u"].iloc[0] == pytest.approx(0.3)


def test_unknown_rule_is_refused():
    with pytest.raises(ValueError, match="unknown ground-truth profile rule"):
        select_profile_rows(_profile_frame(), "lowest-only")


def test_source_refuses_an_unknown_rule_at_config_time():
    with pytest.raises(ValueError, match="unknown profile rule"):
        GroundTruthSource(category="hydraulics", profile="every-other-one")


def test_source_defaults():
    src = GroundTruthSource(category="hydraulics")
    assert (src.profile, src.sheet) == ("log-profile", None)
    assert src.group_key == src.join_key


def test_missing_group_key_passes_through_untouched():
    df = pd.DataFrame({"u": [0.1, 0.2]})
    pd.testing.assert_frame_equal(select_profile_rows(df, "drop-lowest"), df)


# --------------------------------------------------------------------------- #
# category= on the calibration CSV
# --------------------------------------------------------------------------- #
def test_category_selects_a_tab_by_name(tmp_path, monkeypatch):
    """A 2D and a 3D calibration read one survey under different profile rules.

    They must live in SEPARATE categories: compile_ground_truth concatenates
    sources sharing one, which would hand both the union of the row sets.
    """
    from axqua import calibration, ground_truth

    two_d = pd.DataFrame({"x": [0.0], "y": [0.0], "z": [1.0], "u": [0.5], "v": [0.0]})
    three_d = pd.DataFrame({"x": [0.0, 0.0], "y": [0.0, 0.0], "z": [1.0, 1.2],
                            "u": [0.4, 0.6], "v": [0.0, 0.0]})
    table = tmp_path / "ground-truth.xlsx"
    ground_truth.write_tidy({"hydraulics": two_d, "hydraulics-3d": three_d}, table)

    class _Cal:
        calibration_quantities = ["SCALAR VELOCITY"]
        measurement_error = 0.1

    class _GT:
        sources = [object()]
        measurements = table
        targets = None

    class _Cfg:
        ground_truth_path = table
        ground_truth = _GT()
        calibration = _Cal()

        def calibration_path(self, name):
            return tmp_path / name

    monkeypatch.setattr(calibration, "compile_ground_truth", lambda cfg: table)
    cfg = _Cfg()

    two = pd.read_csv(calibration.build_calibration_csv(
        cfg, path=tmp_path / "a.csv", category="hydraulics"))
    three = pd.read_csv(calibration.build_calibration_csv(
        cfg, path=tmp_path / "b.csv", category="hydraulics-3d"))
    assert len(two) == 1 and len(three) == 2

    with pytest.raises(ValueError, match="is not in"):
        calibration.build_calibration_csv(
            cfg, path=tmp_path / "c.csv", category="nope")


# --------------------------------------------------------------------------- #
# log-profile: the velocity at 0.6 of the depth above the bed (user, 2026-11-11)
# --------------------------------------------------------------------------- #
KAPPA = 0.41


def _log_law(z, ustar=0.05, z0=0.003):
    return ustar / KAPPA * np.log(z / z0)


def _log_frame(z0=0.003) -> pd.DataFrame:
    """Vertical 1: three readings on an exact log law, the flow turned 30 degrees.

    Vertical 2: one reading 0.1 m above the bed of a 0.5 m column. MeasD (``h_meas``)
    counts from the surface, as a FlowTracker records it.
    """
    h = 0.8
    turn = np.deg2rad(30.0)
    rows = []
    for z in (0.06, 0.30, 0.55):
        speed = _log_law(z, z0=z0)
        rows.append((1, h, h - z, speed * np.cos(turn), speed * np.sin(turn), 0.01, 0.01))
    rows.append((2, 0.5, 0.4, _log_law(0.1, z0=z0), 0.0, 0.02, 0.02))
    return pd.DataFrame(rows, columns=["ID", "h", "h_meas", "u", "v", "u_err", "v_err"])


def test_log_profile_reads_the_fit_at_0_6_of_the_depth_above_the_bed():
    out = select_profile_rows(_log_frame(), "log-profile").set_index("ID")
    expected = _log_law(0.6 * 0.8)
    assert np.hypot(out.loc[1, "u"], out.loc[1, "v"]) == pytest.approx(expected)
    # the direction of the flow is kept: the components are fitted, not the speed
    assert np.degrees(np.arctan2(out.loc[1, "v"], out.loc[1, "u"])) == pytest.approx(30.0)
    # the row now stands at 0.6 h above the bed, and says so
    assert out.loc[1, "h_meas"] == pytest.approx(0.4 * 0.8)


def test_log_profile_moves_a_single_reading_with_the_roughness_of_the_survey():
    """The lone reading at 0.1 m of a 0.5 m column goes to 0.3 m with z0 of vertical 1."""
    out = select_profile_rows(_log_frame(), "log-profile").set_index("ID")
    assert out.loc[2, "u"] == pytest.approx(_log_law(0.3))
    assert out.loc[2, "u_err"] == pytest.approx(0.02 * np.log(0.3 / 0.003) / np.log(0.1 / 0.003))
    assert out.loc[2, "h_meas"] == pytest.approx(0.2)


def test_log_profile_keeps_a_single_reading_when_no_vertical_gives_a_roughness():
    df = _log_frame()
    df = df[df["ID"] == 2]
    out = select_profile_rows(df, "log-profile")
    pd.testing.assert_frame_equal(out, df.reset_index(drop=True))


def test_log_profile_keeps_a_reading_inside_the_roughness_as_measured():
    """1.5 z0 above the bed the log law does not hold; the factor would explode."""
    df = _log_frame(z0=0.003)
    df.loc[df["ID"] == 2, "h_meas"] = 0.5 - 1.5 * 0.003
    out = select_profile_rows(df, "log-profile").set_index("ID")
    assert out.loc[2, "u"] == pytest.approx(df.loc[df["ID"] == 2, "u"].iloc[0])


def test_log_profile_is_the_default_of_a_ground_truth_source():
    assert GroundTruthSource(category="hydraulics").profile == "log-profile"


def test_single_takes_the_reading_nearest_0_6_of_the_depth_above_the_bed():
    """% Depth 0.4 from the surface is 0.6 above the bed."""
    df = pd.DataFrame({"ID": [1, 1, 1], "pct_depth": [0.2, 0.4, 0.8],
                       "u": [0.3, 0.25, 0.1], "v": [0.0, 0.0, 0.0]})
    assert select_profile_rows(df, "single")["pct_depth"].tolist() == [0.4]


def test_log_profile_never_extrapolates_the_fit():
    """Two readings both below 0.6 h with a noisy slope: the nearest reading is moved
    along the log law with the vertical's own z0, not the fitted line extended."""
    h = 1.0
    df = pd.DataFrame({"ID": [1, 1], "h": [h, h], "h_meas": [h - 0.1, h - 0.25],
                       "u": [0.30, 0.42], "v": [0.0, 0.0]})
    out = select_profile_rows(df, "log-profile")
    a = (0.42 - 0.30) / np.log(0.25 / 0.1)
    z0 = np.exp(-(0.30 - a * np.log(0.1)) / a)
    assert out.loc[0, "u"] == pytest.approx(0.42 * np.log(0.6 / z0) / np.log(0.25 / z0))
    assert out.loc[0, "h_meas"] == pytest.approx(0.4)


def test_log_profile_does_not_fit_a_profile_that_slows_upward():
    """An inverted vertical gives no z0; its reading nearest 0.6 h is moved with the
    survey's z0 (from vertical 1 of the log frame)."""
    df = _log_frame()
    inverted = pd.DataFrame({"ID": [3, 3], "h": [1.0, 1.0], "h_meas": [0.8, 0.3],
                             "u": [0.5, 0.4], "v": [0.0, 0.0],
                             "u_err": [0.01, 0.01], "v_err": [0.01, 0.01]})
    out = select_profile_rows(pd.concat([df, inverted], ignore_index=True),
                              "log-profile").set_index("ID")
    factor = np.log(0.6 / 0.003) / np.log(0.7 / 0.003)
    assert out.loc[3, "u"] == pytest.approx(0.4 * factor)
    assert out.loc[3, "u"] > 0


def test_log_profile_averages_repeat_readings_at_one_height():
    df = pd.DataFrame({"ID": [1, 1], "h": [0.5, 0.5], "h_meas": [0.2, 0.2],
                       "u": [0.40, 0.50], "v": [0.0, 0.0],
                       "u_err": [0.01, 0.01], "v_err": [0.01, 0.01]})
    out = select_profile_rows(df, "log-profile")
    assert out.loc[0, "u"] == pytest.approx(0.45)
    assert out.loc[0, "u_err"] == pytest.approx(np.hypot(0.01, 0.05))


def test_log_profile_takes_the_other_columns_from_the_reading_nearest_0_6_h():
    df = _log_frame()
    df["tke"] = [0.03, 0.02, 0.01, 0.04]
    out = select_profile_rows(df, "log-profile").set_index("ID")
    # vertical 1 reads at 0.06, 0.30 and 0.55 m of 0.8 m; 0.55 m is nearest 0.48 m
    assert out.loc[1, "tke"] == 0.01


def test_log_profile_rewrites_the_height_of_a_label():
    """relative_height reads a '-<p>h' label suffix (from the surface): it has to say
    0.40 once the row stands at 0.6 h above the bed."""
    from axqua.ground_truth import relative_height
    df = pd.DataFrame({"ID": [1, 1, 1], "h": [0.8] * 3,
                       "label": ["q-1-0.92h", "q-1-0.62h", "q-1-0.31h"],
                       "u": [_log_law(z) for z in (0.064, 0.304, 0.552)],
                       "v": [0.0] * 3})
    out = select_profile_rows(df, "log-profile")
    assert out.loc[0, "label"] == "q-1-0.40h"
    assert relative_height(out)[0].iloc[0] == pytest.approx(0.6)
    assert out.loc[0, "u"] == pytest.approx(_log_law(0.48), rel=1e-3)


def test_a_reading_without_a_recorded_depth_stands_at_0_6_h_above_the_bed():
    """The user's decision of 2026-11-12: such a reading is used as it is, not moved."""
    from axqua.ground_truth import DEFAULT_RELATIVE_HEIGHT
    assert DEFAULT_RELATIVE_HEIGHT == 0.6
    df = _log_frame()
    unknown = pd.DataFrame({"ID": [4], "h": [0.5], "h_meas": [np.nan],
                            "u": [_log_law(0.2)], "v": [0.0],
                            "u_err": [0.02], "v_err": [0.02]})
    out = select_profile_rows(pd.concat([df, unknown], ignore_index=True),
                              "log-profile").set_index("ID")
    assert out.loc[4, "u"] == pytest.approx(_log_law(0.2))


def test_a_vertical_whose_readings_record_different_depths_stands_at_0_6_h():
    """KB15 September 2025: a repeat reading recorded 0.639 m, the others 0.670 m. The
    first depth decided z* while the row kept 0.670 m, so it stood at 0.62 h."""
    from axqua.ground_truth import relative_height
    df = pd.DataFrame({"ID": [1, 1, 1, 1], "h": [0.639, 0.670, 0.670, 0.670],
                       "h_meas": [0.591, 0.621, 0.409, 0.200],
                       "u": [0.004, 0.30, 0.289, 0.286], "v": [0.0] * 4})
    out = select_profile_rows(df, "log-profile")
    assert len(out) == 1
    assert out.loc[0, "h"] == pytest.approx(0.670)
    assert relative_height(out)[0].iloc[0] == pytest.approx(0.6)
