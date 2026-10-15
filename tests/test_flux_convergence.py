"""Flux / mass-balance (hotstart) convergence analysis.

Everything under test is axqua's own: the ``.sortie`` listing parser
(:mod:`axqua.sortie`), the convergence maths (relative flux imbalance,
convergence rate, the converged printout), the absolute steady-window detection and
the derived hotstart steering file. The analysis used to delegate to the ``pythomac``
package; it no longer does (see the :mod:`axqua.flux_convergence` docstring), so
these tests drive it end to end from a **synthetic listing written in TELEMAC's real
output format** rather than by stubbing out the parser.

Run via:
    mamba run -n axqua-env pytest tests/test_flux_convergence.py
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from axqua.flux_convergence import (
    _mean_balance_time,
    analyze_flux_convergence,
    convergence_index,
    convergence_rate,
    find_steady_window,
    relative_imbalance,
)
from axqua.sortie import latest_sortie, read_sortie


def _stub_cfg(model_dir, *, period=50, dt=1.0, n_steps=10000, cas="steady2d.cas"):
    """A minimal duck-typed Config carrying only what the analyzer reads."""
    return SimpleNamespace(
        model_dir=model_dir,
        cas_file=cas,
        results_slf="r2d.slf",
        hydrodynamics=SimpleNamespace(
            listing_printout_period=period, time_step=dt, n_time_steps=n_steps),
        model_path=lambda name: Path(model_dir) / name,
    )


# a minimal built steady case for the hotstart derivation (write_hotstart_cas
# rewrites the initial-conditions / duration / results lines and keeps the rest)
_STEADY_CAS = """\
TITLE : 'stub steady'
GEOMETRY FILE : geometry.slf
BOUNDARY CONDITIONS FILE : boundaries.cli
RESULTS FILE : r2d.slf
DURATION : 5000.0
PRESCRIBED FLOWRATES : 0.;47.0000
PRESCRIBED ELEVATIONS : 329.2400;0.
PREVIOUS COMPUTATION FILE : initial-conditions.slf
INITIAL TIME SET TO ZERO : YES
&ETA
"""


def _write_sortie(model_dir, fluxes, *, times=None, volumes=None,
                  cas="steady2d.cas", stamp="2026-01-01-00h00min00s",
                  study="stub steady", per_processor=0):
    """Write a listing in TELEMAC's real ``.sortie`` format.

    *fluxes* is (n_printouts, n_boundaries), signed as TELEMAC prints them (inflow
    positive, outflow negative). Optionally also writes *per_processor* rank copies,
    which the analysis is expected to delete.
    """
    fluxes = np.atleast_2d(np.asarray(fluxes, dtype=float))
    n, n_bnd = fluxes.shape
    times = np.arange(1, n + 1, dtype=float) if times is None else np.asarray(times, float)
    volumes = np.full(n, 1234.5) if volumes is None else np.asarray(volumes, float)

    lines = [" EXITING LECDON. NAME OF THE STUDY:", f"    {study}", ""]
    for i in range(n):
        lines.append(f" ITERATION {(i + 1) * 50:8d}    TIME: {times[i]:9.4f} S")
        lines.append("                       BALANCE OF WATER VOLUME    ")
        lines.append(f"     VOLUME IN THE DOMAIN : {volumes[i]:14.6f}     M3")
        for b in range(n_bnd):
            lines.append(f"     FLUX BOUNDARY {b + 1:4d}: {fluxes[i, b]:15.7f}     "
                         "M3/S  ( >0 : ENTERING  <0 : EXITING )")
        lines.append(f"     RELATIVE ERROR IN VOLUME AT T = {times[i]:12.2f}     S :"
                     f"   -0.1008281E-14")
        lines.append("     MAXIMUM COURANT NUMBER:    0.1739105    ")
    lines += ["", " 3 MINUTES", ""]

    main = Path(model_dir) / f"{cas}_{stamp}.sortie"
    main.write_text("\n".join(lines) + "\n")
    for rank in range(1, per_processor + 1):
        (Path(model_dir) / f"{cas}_{stamp}_p{rank:05d}.sortie").write_text("partial\n")
    return main


def _two_boundary(imbalance, q=47.0):
    """Signed (inflow, outflow) columns whose relative imbalance follows *imbalance*."""
    imbalance = np.asarray(imbalance, dtype=float)
    return np.column_stack([np.full(imbalance.size, q), -q * (1.0 - imbalance)])


# --------------------------------------------------------------------------- #
# the listing parser
# --------------------------------------------------------------------------- #


def test_sortie_parses_blocks_and_aggregates_by_sign(tmp_path):
    """Three boundaries, two inflow and one outflow: the gross totals sum by sign,
    and every array stays aligned with the time read from inside each block."""
    fluxes = np.array([[30.0, 17.0, -45.0],
                       [31.0, 16.0, -47.0],
                       [30.5, 16.5, -47.0]])
    path = _write_sortie(tmp_path, fluxes, times=[10.0, 20.0, 30.0],
                         volumes=[100.0, 110.0, 111.0])
    s = read_sortie(path)

    assert s.study == "stub steady"
    assert s.n_boundaries == 3
    assert s.time.tolist() == [10.0, 20.0, 30.0]
    assert s.volume.tolist() == [100.0, 110.0, 111.0]
    assert s.gross_in == pytest.approx([47.0, 47.0, 47.0])
    assert s.gross_out == pytest.approx([45.0, 47.0, 47.0])
    assert s.iteration.tolist() == [50, 100, 150]
    assert s.printout_interval == pytest.approx(10.0)
    assert s.exec_seconds == 180.0                       # "3 MINUTES"
    # no fabricated leading sample: one row per printed block
    assert s.fluxes.shape == (3, 3)


def test_sortie_skips_a_block_truncated_mid_write(tmp_path):
    """A run killed part-way through a balance block must not yield a half row."""
    path = _write_sortie(tmp_path, _two_boundary(np.array([0.5, 0.1])))
    with open(path, "a") as fh:                          # start a block, never finish
        fh.write("                       BALANCE OF WATER VOLUME    \n")
        fh.write("     VOLUME IN THE DOMAIN :    9999.000     M3\n")
        fh.write("     FLUX BOUNDARY    1:    47.0000000     M3/S\n")
    s = read_sortie(path)
    assert s.time.size == 2                              # the partial block is dropped
    assert 9999.0 not in s.volume.tolist()


def test_sortie_rejects_a_listing_without_flux_printouts(tmp_path):
    p = tmp_path / "steady2d.cas_2026-01-01-00h00min00s.sortie"
    p.write_text(" ITERATION      500    TIME:  28.5672 S\n")
    with pytest.raises(ValueError, match="PRINTING CUMULATED FLOWRATES"):
        read_sortie(p)


def test_latest_sortie_ignores_per_processor_copies(tmp_path):
    _write_sortie(tmp_path, _two_boundary(np.array([0.1])),
                  stamp="2026-01-01-00h00min00s", per_processor=3)
    newest = _write_sortie(tmp_path, _two_boundary(np.array([0.1])),
                           stamp="2026-01-02-00h00min00s", per_processor=3)
    found = latest_sortie(tmp_path, "steady2d.cas")
    assert found == newest and "_p0" not in found.name


# --------------------------------------------------------------------------- #
# convergence maths
# --------------------------------------------------------------------------- #


def test_relative_imbalance_and_convergence_index():
    eps = np.array([1.0, 1e-2, 1e-5, 1e-7, 1e-8])
    fluxes = _two_boundary(eps)
    assert relative_imbalance(fluxes[:, 0], -fluxes[:, 1]) == pytest.approx(eps)
    assert convergence_index(eps, 1e-6) == 3              # 1e-5 is still above 1e-6
    assert convergence_index(eps, 1e-9) is None           # never reached
    # a transient dip that is later lost is not convergence
    assert convergence_index(np.array([1.0, 1e-9, 1e-2]), 1e-6) is None


def test_convergence_rate_is_nan_while_the_imbalance_is_flat():
    flat = convergence_rate(np.full(10, 1.0))
    assert flat.size == 9 and np.all(np.isnan(flat))      # log base 1 -> undefined
    decaying = convergence_rate(10.0 ** (-np.arange(5.0)))
    assert np.isfinite(decaying[1:]).all()


def test_find_steady_window_requires_consecutive_printouts():
    t = np.arange(20, dtype=float) * 10.0
    gin = np.full(20, 2.0)
    gout = np.full(20, 2.0) + 5e-4                  # balanced within 1e-3 throughout
    gout[:8] = 1.5                                  # ...except the first 8 printouts
    # strict window of 10 starts at the first balanced printout (index 8, t=80)
    assert find_steady_window(t, gin, gout, abs_tolerance=1e-3, window=10) == 80.0
    # a lone dip below tolerance does not count as steady
    gout = np.full(20, 2.005)
    gout[10] = 2.0
    assert find_steady_window(t, gin, gout, abs_tolerance=1e-3, window=10) is None


def test_mean_balance_time_averages_out_steady_noise():
    # imbalance oscillates +/-3e-3 around zero: never below 1e-3 per printout,
    # but balanced in the 10-printout mean (the ering-revised situation)
    t = np.arange(40, dtype=float) * 10.0
    gin = np.full(40, 2.0)
    gout = 2.0 + 3e-3 * np.where(np.arange(40) % 2 == 0, 1.0, -1.0)
    assert find_steady_window(t, gin, gout, abs_tolerance=1e-3, window=10) is None
    secs = _mean_balance_time(t, gin, gout, abs_tolerance=1e-3, window=10)
    assert secs == 0.0                              # balanced in the mean from the start


# --------------------------------------------------------------------------- #
# end to end
# --------------------------------------------------------------------------- #


def test_converges_and_recommends_time_steps(tmp_path):
    eps = 10.0 ** (-(np.arange(60) / 6.0))         # 1e0 .. 1e-9.8
    times = np.arange(1, 61, dtype=float) * 20.0
    _write_sortie(tmp_path, _two_boundary(eps), times=times, per_processor=4)
    (tmp_path / "steady2d.cas").write_text(_STEADY_CAS)

    fc = analyze_flux_convergence(_stub_cfg(tmp_path), tolerance=1e-6)

    idx = convergence_index(eps, 1e-6)
    assert fc.converged is True
    # the converged time/iteration are read off the listing at that printout, not
    # estimated from a nominal spacing (a CFL-adaptive run has none)
    assert fc.converged_seconds == pytest.approx(times[idx])
    assert fc.converged_time_steps == (idx + 1) * 50
    assert fc.final_imbalance < 1e-6
    for p in (fc.fluxes_csv, fc.flux_plot, fc.rate_csv, fc.rate_plot):
        assert p is not None and p.exists(), f"missing convergence output: {p}"
    assert {p.name for p in (fc.fluxes_csv, fc.flux_plot, fc.rate_csv, fc.rate_plot)} == {
        "extracted-fluxes.csv", "flux-convergence.png",
        "convergence-rate.csv", "convergence-rate.png"}
    # the per-processor listing copies are cleaned up, the main one kept
    assert not list(tmp_path.glob("*_p0*.sortie"))
    assert latest_sortie(tmp_path, "steady2d.cas") is not None

    # the absolute steady window is found strictly and the hotstart case is derived
    assert fc.steady_seconds is not None and fc.steady_strict is True
    assert fc.hotstart_cas is not None and fc.hotstart_cas.exists()
    hot = fc.hotstart_cas.read_text()
    assert "PREVIOUS COMPUTATION FILE : r2d.slf" in hot          # continues the run
    assert "RESULTS FILE : r2d-hotstart.slf" in hot              # no self-clobber
    assert f"DURATION : {np.ceil(fc.steady_seconds)}" in hot     # steady end time
    assert "PRESCRIBED FLOWRATES : 0.;47.0000" in hot            # Q kept alive
    assert "PRESCRIBED ELEVATIONS : 329.2400;0." in hot          # H kept alive


def test_not_converged_when_tolerance_never_met(tmp_path):
    _write_sortie(tmp_path, _two_boundary(np.full(40, 1e-3)))
    fc = analyze_flux_convergence(_stub_cfg(tmp_path), tolerance=1e-6)
    assert fc.converged is False
    assert fc.converged_time_steps is None
    assert fc.converged_seconds is None


def test_filling_phase_still_writes_all_four_files(tmp_path):
    """A still-filling run has a flat imbalance (~1, outflow not yet established), so
    the log-based convergence rate is undefined everywhere. The analysis must still
    produce all four files - the imbalance panel is exactly what one needs to see."""
    _write_sortie(tmp_path, _two_boundary(np.full(20, 1.0)))
    fc = analyze_flux_convergence(_stub_cfg(tmp_path), tolerance=1e-4)
    assert fc.converged is False
    for p in (fc.fluxes_csv, fc.flux_plot, fc.rate_csv, fc.rate_plot):
        assert p is not None and p.exists(), f"missing convergence output: {p}"


def test_dry_start_rows_without_inflow_are_dropped(tmp_path):
    """Before the inflow establishes, the relative imbalance is 0/0; those printouts
    must be dropped rather than poisoning the series with NaN."""
    eps = 10.0 ** (-(np.arange(30) / 4.0))
    fluxes = _two_boundary(eps)
    fluxes[:3] = 0.0                                     # dry start: nothing flowing
    _write_sortie(tmp_path, fluxes)
    fc = analyze_flux_convergence(_stub_cfg(tmp_path), tolerance=1e-4,
                                  write_hotstart=False)
    assert fc.converged is True
    assert np.isfinite(fc.final_imbalance)


# --------------------------------------------------------------------------- #
# GAIA / tracer mass balances (adopted from pythomac, re-derived from the
# TELEMAC v9.1.1 FORMAT statements in gaia/mass_balance.f and telemac3d/bil3d.f)
# --------------------------------------------------------------------------- #

_GAIA_BLOCK = """\
                    GAIA MASS-BALANCE OF SEDIMENTS PER CLASS:
     SEDIMENT CLASS NUMBER                     =        1
     INITIAL MASS                              =   0.1000000E+05  ( KG )
     TOTAL MASS                                =   {total}  ( KG )
     LOST MASS                                 =   {lost}  ( KG )
     CUMULATED LOST MASS                       =   {cumlost}  ( KG )
     EROSION FLUX                              =   0.2500000E+01  ( KG/S )
     DEPOSITION FLUX                           =   0.1500000E+01  ( KG/S )
     BEDLOAD FLUX BOUNDARY    1                =   0.7000000E+00  ( KG/S  >0 = ENTERING )
     RELATIVE ERROR TO INITIAL ACT LAYER MASS  =   {relerr}
                    GAIA MASS-BALANCE OF SEDIMENTS OVER ALL CLASSES:
     TOTAL MASS                                =   {total}  ( KG )
     LOST MASS                                 =   {lost}  ( KG )
"""


def test_sediment_mass_profile_reads_gaia_blocks(tmp_path):
    from axqua.sortie import sediment_mass_profile

    p = tmp_path / "gaia.sortie"
    p.write_text("".join(
        _GAIA_BLOCK.format(total=f"0.{9000 + i}000E+04", lost=f"0.{i}000000E+01",
                           cumlost=f"0.{i}500000E+01", relerr=f"0.{i}000000E-06")
        for i in range(1, 4)))
    mass = sediment_mass_profile(p)

    assert set(mass) == {0, 1}                       # class 1 + the all-classes block
    cls1 = mass[1]
    assert cls1["total_mass"].size == 3
    # 'CUMULATED LOST MASS' must not be swallowed by the shorter 'LOST MASS' label
    assert cls1["lost_mass"] == pytest.approx([1.0, 2.0, 3.0])
    assert cls1["cumulated_lost_mass"] == pytest.approx([1.5, 2.5, 3.5])
    assert cls1["erosion_flux"] == pytest.approx([2.5] * 3)
    assert cls1["deposition_flux"] == pytest.approx([1.5] * 3)
    assert cls1["relative_error_active_layer"] == pytest.approx([1e-7, 2e-7, 3e-7])
    # per-boundary bedload flux is keyed by boundary number
    assert cls1["bedload_flux_boundary_1"] == pytest.approx([0.7] * 3)
    assert mass[0]["total_mass"].size == 3


def test_sediment_mass_profile_empty_without_gaia(tmp_path):
    path = _write_sortie(tmp_path, _two_boundary(np.array([0.1, 0.05])))
    from axqua.sortie import sediment_mass_profile
    assert sediment_mass_profile(path) == {}


def test_tracer_mass_profile_skips_the_water_block(tmp_path):
    from axqua.sortie import tracer_mass_profile

    p = tmp_path / "tracer.sortie"
    p.write_text(
        "  WATER :\n"
        "QUANTITY AT THE PRESENT TIME STEP             :   0.9999000E+09\n"
        "  TRACER  1: SUSPENDED SED  , UNIT :  KG/M3\n"
        "QUANTITY AT THE PRESENT TIME STEP             :   0.1200000E+03\n"
        "ERROR ON THE QUANTITY DURING THIS TIME STEP   :   0.1000000E-08\n"
        "  TRACER  2: TEMPERATURE    , UNIT :  DEG C\n"
        "QUANTITY AT THE PRESENT TIME STEP             :   0.3400000E+02\n"
    )
    tracers = tracer_mass_profile(p)
    assert set(tracers) == {1, 2}
    assert tracers[1]["total"] == pytest.approx([120.0])
    assert tracers[1]["error"] == pytest.approx([1e-9])
    assert tracers[2]["total"] == pytest.approx([34.0])
    # the WATER balance uses the same wording and must not become a tracer
    assert all(9.999e8 not in t["total"] for t in tracers.values())


def test_find_lines_extracts_user_fortran_printouts(tmp_path):
    from axqua.sortie import find_lines

    p = tmp_path / "run.sortie"
    p.write_text(
        " ITERATION      500    TIME:  28.5672 S\n"
        " USER_RAIN PERCOLATION: CALL 500 DELIVERED 0.065 M3/S WET AREA 167.5 M2\n"
        " USER_RAIN PERCOLATION: CALL 1000 DELIVERED 0.064 M3/S WET AREA 165.4 M2\n"
    )
    hits = find_lines(p, r"USER_RAIN PERCOLATION")
    assert len(hits) == 2 and hits[0].startswith("USER_RAIN")
    hits, numbers = find_lines(p, r"USER_RAIN", with_line_numbers=True)
    assert numbers == [2, 3]


def test_tolerance_grade_comes_from_the_config_and_scales_the_absolute_criterion(tmp_path):
    """The relative tolerance defaults to hydrodynamics.flux_tolerance, and the
    absolute steady criterion is derived from it times the discharge - so the
    hotstart gate means the same thing on a 2 m3/s side channel and a 200 m3/s river
    instead of being decided by the size of the case."""
    from axqua import flux_convergence as fcmod

    eps = np.concatenate([np.full(10, 1e-2), np.full(30, 5e-4)])   # settles at 5e-4
    _write_sortie(tmp_path, _two_boundary(eps, q=2.0))
    (tmp_path / "steady2d.cas").write_text(_STEADY_CAS)

    cfg = _stub_cfg(tmp_path)
    cfg.hydrodynamics.flux_tolerance = fcmod.HYDRAULIC_TOLERANCE       # 1e-3
    cfg.boundaries = SimpleNamespace(prescribed_flowrate=2.0)

    loose = analyze_flux_convergence(cfg)
    assert loose.converged is True                       # 5e-4 clears the 1e-3 grade
    assert loose.tolerance == pytest.approx(1e-3)
    # abs criterion = 1e-3 * 2.0 m3/s; the imbalance is 5e-4*2.0 = 1e-3 m3/s, inside it
    assert loose.steady_abs_tolerance == pytest.approx(2e-3)
    assert loose.steady_seconds is not None and loose.hotstart_cas is not None

    strict = analyze_flux_convergence(cfg, tolerance=fcmod.HOTSTART_TOLERANCE)
    assert strict.converged is False                     # 5e-4 misses the 1e-4 grade
    assert strict.steady_abs_tolerance == pytest.approx(2e-4)


# --------------------------------------------------------------------------- #
# the TELEMAC-3D listing
#
# A different block entirely (telemac3d/bil3d.f), and until it was read a 3D seed's
# flux balance could not be judged at all: read_sortie raised, prerun._judge swallowed
# it, and `openfoam.pre_run.require: error` could never fire under `dimension: 3d`.
# The shapes below are transcribed from real listings on this machine
# (hotstart3d_hydrostatic.cas_*.sortie, inn3d.cas_*.sortie).
# --------------------------------------------------------------------------- #


def _write_sortie_3d(model_dir, fluxes, *, times=None, volumes=None,
                     cas="hotstart3d_hydrostatic.cas",
                     stamp="2026-01-01-00h00min00s", study="stub 3d",
                     initial_block=True, final_block=True, totals=True):
    """A listing in TELEMAC-3D's format.

    *initial_block* writes the ``INITIAL VOLUME OF WATER IN THE DOMAIN`` block the run
    opens with (no fluxes, so it must be dropped); *final_block* the end-of-run ``FINAL
    MASS BALANCE`` summary (which is not a printout and must not become a row);
    *totals* controls whether the ITERATION header carries the cumulated ``( N S)``
    form or only the D/H/MN breakdown.
    """
    fluxes = np.atleast_2d(np.asarray(fluxes, dtype=float))
    n, n_bnd = fluxes.shape
    times = np.arange(1, n + 1, dtype=float) if times is None else np.asarray(times, float)
    volumes = np.full(n, 105.0) if volumes is None else np.asarray(volumes, float)

    def header(step, t):
        mn, sec = divmod(float(t), 60.0)
        hr, mn = divmod(mn, 60.0)
        day, hr = divmod(hr, 24.0)
        line = (f"ITERATION {step:8d} TIME {int(day):4d} D {int(hr):2d} H "
                f"{int(mn):2d} MN {sec:8.4f} S")
        return line + (f"   ({t:16.4f} S)" if totals else "")

    lines = [" EXITING LECDON. NAME OF THE STUDY:", f"    {study}", ""]
    if initial_block:
        lines += [header(0, 0.0), "                MASS BALANCE",
                  " INITIAL VOLUME OF WATER IN THE DOMAIN :   106.70328359999993"]
    for i in range(n):
        lines.append(header((i + 1) * 100, times[i]))
        lines.append(" GRACJG (BIEF) :      111 ITERATIONS, RELATIVE PRECISION:   0.9E-08")
        lines += ["                MASS BALANCE", "", "  WATER",
                  f"VOLUME AT THE PREVIOUS TIME STEP              : {volumes[i] + 1:13.7g}",
                  f"VOLUME AT THE PRESENT TIME STEP               : {volumes[i]:13.7g}",
                  "VOLUME LEAVING DOMAIN DURING THIS TIME STEP   :    0.7793448E-02",
                  "ERROR ON THE VOLUME DURING THIS TIME STEP     :   -0.1874828E-12",
                  "  BOUNDARY FLUXES FOR WATER IN M3/S ( >0 : ENTERING )"]
        for b in range(n_bnd):
            lines.append(f"FLUX BOUNDARY {b + 1:6d}                          : "
                         f"{fluxes[i, b]:13.7f}")
    if final_block:
        lines += ["                FINAL MASS BALANCE", f"T = {times[-1]:16.4f}", "",
                  "--- WATER ---", "INITIAL VOLUME                      :     106.7033",
                  f"FINAL VOLUME                        : {volumes[-1]:13.7g}",
                  "VOLUME EXITING (BOUNDARY OR SOURCE) :     16.54855"]
    lines += ["", " 3 MINUTES", ""]

    main = Path(model_dir) / f"{cas}_{stamp}.sortie"
    main.write_text("\n".join(lines) + "\n")
    return main


def test_sortie_reads_the_telemac_3d_mass_balance(tmp_path):
    """The 3D block names its volume differently, carries no time of its own, and has
    no closing line - so it is committed when the next one starts."""
    fluxes = np.array([[0.135, -0.4133], [0.135, -0.1350], [0.135, -0.1349]])
    path = _write_sortie_3d(tmp_path, fluxes, times=[2.8, 5.6, 8.4],
                            volumes=[105.4497, 104.9062, 104.2838])
    s = read_sortie(path)

    assert s.dimension == "3d"
    assert s.n_boundaries == 2
    # the time comes from the ITERATION header above each block, in order
    assert s.time == pytest.approx([2.8, 5.6, 8.4])
    assert s.iteration.tolist() == [100, 200, 300]
    assert s.volume == pytest.approx([105.4497, 104.9062, 104.2838])
    assert s.gross_in == pytest.approx([0.135, 0.135, 0.135])
    assert s.gross_out == pytest.approx([0.4133, 0.1350, 0.1349])
    assert s.exec_seconds == 180.0
    # the opening INITIAL VOLUME block and the closing FINAL MASS BALANCE are not
    # printouts and must not become rows
    assert s.fluxes.shape == (3, 2)


def test_sortie_3d_falls_back_to_the_time_breakdown(tmp_path):
    """Without the cumulated '( N S)' the D/H/MN/S breakdown has to be summed, or a
    run past the first minute would report the wrong instant."""
    path = _write_sortie_3d(tmp_path, [[0.135, -0.135]], times=[3725.5], totals=False)
    assert read_sortie(path).time == pytest.approx([3725.5])


def test_sortie_3d_drops_a_block_with_no_time_above_it(tmp_path):
    """Nothing is fabricated: a block whose ITERATION header is missing has no
    defensible time, so it is dropped rather than committed at a guessed one."""
    path = _write_sortie_3d(tmp_path, [[0.135, -0.135]], initial_block=False,
                            final_block=False)
    text = path.read_text().splitlines()
    path.write_text("\n".join(ln for ln in text if not ln.startswith("ITERATION")))
    with pytest.raises(ValueError, match="PRINTING CUMULATED FLOWRATES"):
        read_sortie(path)


def test_sortie_3d_does_not_mistake_a_gaia_mass_balance_for_a_block(tmp_path):
    """GAIA prints 'MASS-BALANCE' headings of its own. The 3D header is the whole
    line, so they cannot collide - this pins that."""
    path = _write_sortie_3d(tmp_path, [[0.135, -0.135], [0.135, -0.134]])
    with open(path, "a") as fh:
        fh.write("        GAIA MASS-BALANCE OF SEDIMENTS OVER ALL CLASSES\n")
        fh.write("TOTAL MASS                              =   1234.5\n")
    s = read_sortie(path)
    assert s.dimension == "3d" and s.time.size == 2


def test_sortie_2d_listing_is_still_read_as_2d(tmp_path):
    """The 3D shape is additive: nothing about the 2D path changes, including that its
    volume_error is the relative one TELEMAC-2D prints."""
    path = _write_sortie(tmp_path, _two_boundary(np.array([0.5, 0.1])))
    s = read_sortie(path)
    assert s.dimension == "2d"
    assert s.volume_error == pytest.approx([-0.1008281e-14, -0.1008281e-14])
