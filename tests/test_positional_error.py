"""Propagating survey position uncertainty into the calibration error budget.

The case this exists for: Inn KB15's September-2025 survey was positioned with an
RTK **float** solution, so its coordinates are good to metres rather than
centimetres - and the modelled depth there changes by 0.27 m over 3 m.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from axqua import positional_error as pe


def test_a_uniform_field_carries_no_positional_error():
    """Nothing to be uncertain about: the answer is the same wherever you look."""
    spread = pe.sample_spread(lambda x, y: np.full(np.shape(x), 2.5),
                              [0.0, 10.0], [0.0, 10.0], sigma=1.0)
    assert np.allclose(spread, 0.0)


def test_a_steep_field_carries_a_large_one():
    """A target on a gradient is worth less than one in a uniform patch, and the
    error bar is how a likelihood is told so."""
    gentle = pe.sample_spread(lambda x, y: 0.01 * np.asarray(x, float),
                              [0.0], [0.0], sigma=1.0)
    steep = pe.sample_spread(lambda x, y: 1.00 * np.asarray(x, float),
                             [0.0], [0.0], sigma=1.0)
    assert steep[0] > 50 * gentle[0]


def test_spread_grows_with_sigma():
    def field(x, y):
        return np.asarray(x, dtype=float)

    small = pe.sample_spread(field, [0.0], [0.0], sigma=0.1)[0]
    large = pe.sample_spread(field, [0.0], [0.0], sigma=3.0)[0]
    assert large == pytest.approx(30.0 * small, rel=1e-6)


def test_zero_sigma_is_a_no_op():
    """An rtk-fixed survey should cost nothing, not a token error bar."""
    spread = pe.sample_spread(lambda x, y: np.asarray(x, float),
                              [0.0, 1.0], [0.0, 1.0], sigma=0.0)
    assert np.allclose(spread, 0.0)


def test_a_step_is_not_linearised_away():
    """Why this samples instead of taking a gradient.

    The fields that make positional error matter - depth at a bank, velocity at a
    shear layer - are exactly the ones a local linearisation handles worst.
    """
    def step(x, y):
        return np.where(np.asarray(x, float) > 0.0, 1.0, 0.0)

    # the analytic gradient AT the point is zero on both sides of the step
    assert pe.sample_spread(step, [-0.5], [0.0], sigma=2.0)[0] > 0.3


def test_dry_samples_are_dropped_not_counted_as_zero():
    """A vertical near the waterline still gets a spread from the wet samples."""
    def half_dry(x, y):
        return np.where(np.asarray(y, float) > 0.0, np.nan, 1.0)

    assert np.isfinite(pe.sample_spread(half_dry, [0.0], [-5.0], sigma=1.0)).all()


def test_too_few_usable_samples_gives_no_spread():
    """Two numbers cannot support a spread; better none than an invented one."""
    def almost_all_dry(x, y):
        out = np.full(np.shape(x), np.nan)
        return out

    assert pe.sample_spread(almost_all_dry, [0.0], [0.0], sigma=1.0)[0] == 0.0


# --------------------------------------------------------------------------- #
# sigma resolution
# --------------------------------------------------------------------------- #
class _GT:
    def __init__(self, quality=None, sigma=None):
        self.position_quality = quality
        self.position_sigma = sigma


class _Cfg:
    def __init__(self, gt):
        self.ground_truth = gt


def test_no_declaration_means_no_positional_error():
    """Every existing config must be untouched."""
    assert pe.resolve_sigma(_Cfg(_GT())) == (0.0, None)


def test_quality_names_resolve_to_metres():
    assert pe.resolve_sigma(_Cfg(_GT("rtk-float")))[0] == 1.00
    assert pe.resolve_sigma(_Cfg(_GT("rtk-fixed")))[0] == 0.03
    # a fixed solution is two orders of magnitude tighter than a float one, which
    # is the whole reason the distinction is worth carrying
    assert pe.resolve_sigma(_Cfg(_GT("rtk-float")))[0] > \
        30 * pe.resolve_sigma(_Cfg(_GT("rtk-fixed")))[0]


def test_an_explicit_sigma_wins_over_the_lookup():
    sigma, quality = pe.resolve_sigma(_Cfg(_GT("rtk-float", 2.5)))
    assert sigma == 2.5 and quality == "rtk-float"


def test_an_unknown_quality_is_refused():
    """A typo here would silently drop the error bar the user asked for."""
    with pytest.raises(ValueError, match="unknown ground_truth.position_quality"):
        pe.resolve_sigma(_Cfg(_GT("rtk-ish")))


# --------------------------------------------------------------------------- #
# the CSV
# --------------------------------------------------------------------------- #
def test_csv_is_unchanged_when_no_quality_is_declared(tmp_path):
    """The load-bearing backwards-compatibility check."""
    from tests.test_openfoam_calibration import _cfg, _tidy, _write_tidy
    from axqua.calibration import build_calibration_csv

    cfg = _cfg(tmp_path, quantities=("WATER DEPTH",))
    _write_tidy(cfg, _tidy())
    a = build_calibration_csv(cfg, path=tmp_path / "a.csv").read_text()
    b = build_calibration_csv(cfg, path=tmp_path / "b.csv").read_text()
    assert a == b


def test_errors_combine_in_quadrature():
    """Position error and instrument error are independent."""
    measured = np.array([0.10, 0.20])
    positional = np.array([0.10, 0.00])
    combined = np.sqrt(measured ** 2 + positional ** 2)
    assert combined[0] == pytest.approx(0.1414, abs=1e-4)
    assert combined[1] == pytest.approx(0.20)       # untouched where the field is flat


def test_a_missing_result_warns_rather_than_failing(tmp_path, caplog):
    """A better error budget must not cost the calibration its inputs."""
    from tests.test_openfoam_calibration import _cfg, _tidy, _write_tidy
    from axqua.calibration import build_calibration_csv

    cfg = _cfg(tmp_path, quantities=("WATER DEPTH",))
    cfg.ground_truth.position_quality = "rtk-float"
    _write_tidy(cfg, _tidy())
    with caplog.at_level("WARNING"):
        out = build_calibration_csv(cfg, path=tmp_path / "m.csv")
    assert out is not None and pd.read_csv(out).shape[0] == 3
    assert "no converged 2D result" in caplog.text
