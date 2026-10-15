"""Which reading of a multi-depth vertical becomes a calibration target.

The load-bearing test in here is backwards compatibility: ``profile="single"``
must reproduce the historic behaviour exactly, because every existing case config
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


def test_source_defaults_are_backwards_compatible():
    src = GroundTruthSource(category="hydraulics")
    assert (src.profile, src.sheet) == ("single", None)
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
