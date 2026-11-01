"""The hydrology mode: Green-Ampt, rain, weather, soil, and the case it builds.

No TELEMAC here. What is pinned:

* the **infiltration kernels** of the generated routine, through their Python reference:
  the exact one against the exact Green-Ampt solution, Sadeghi's solution as de Vos et
  al. (2024) give it within 5 % of it, ponded water that infiltrates after the rain,
  and evaporation that takes only the water there is;
* **rain series**: time stamps at the start or the end of an interval, depths and
  intensities, and design storms whose windows from the peak hold the table depths;
* **FAO-56** example 19 for the hourly reference evapotranspiration, and the wind
  components of a wind blowing from a direction;
* **soil**: the Rawls et al. (1983) table as the paper prints it, the USDA texture
  triangle, and a soil table filled from the texture of each zone;
* the **case**: a synthetic catchment built through the pipeline, its geometry with the
  soil fields, its steering file inside DAMOCLES' 72 columns, and the routine.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from axqua.hydrology import greenampt as ga
from axqua.hydrology import rainfall, soil, weather

MM_H = 1.0 / 3.6e6


# --------------------------------------------------------------------------- #
# Green-Ampt
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("texture", ["loam", "sandy loam", "clay"])
def test_the_exact_kernel_is_the_exact_green_ampt_solution(texture):
    row = soil.texture(texture)
    s = ga.Soil.from_parameters(row.ks_mm_h, row.suction_m, row.porosity,
                                row.porosity - 0.25)
    rain = 30 * MM_H
    result = ga.simulate_column(s, rain, duration=3 * 3600, dt=1.0, kernel="exact")
    exact = ga.exact_ponded_infiltration(s.ks, s.a, rain, 3 * 3600)
    assert result.infiltrated[-1] == pytest.approx(exact, rel=2e-3)


@pytest.mark.parametrize("texture", ["loam", "sandy loam", "clay"])
def test_the_sadeghi_kernel_stays_within_five_percent_of_the_exact_solution(texture):
    row = soil.texture(texture)
    s = ga.Soil.from_parameters(row.ks_mm_h, row.suction_m, row.porosity,
                                row.porosity - 0.25)
    rain = 30 * MM_H
    result = ga.simulate_column(s, rain, duration=3 * 3600, dt=1.0, kernel="sadeghi")
    exact = ga.exact_ponded_infiltration(s.ks, s.a, rain, 3 * 3600)
    assert result.infiltrated[-1] == pytest.approx(exact, rel=0.05)


def test_sadeghi_capacity_against_the_exact_capacity_over_the_ponded_curve():
    from scipy.optimize import brentq

    ks, a = 1.0, 1.0
    for tau in (1e-3, 1e-2, 0.1, 1.0, 10.0, 100.0):
        f_over_a = brentq(lambda x: x - math.log1p(x) - tau, 1e-12, 1e6)
        exact = 1.0 + 1.0 / f_over_a
        assert ga.sadeghi_capacity(ks, a, tau) == pytest.approx(exact, rel=0.05)
    # and the time shift is the exact inverse of the ponded curve
    assert ga.ponded_time(1.0, 1.0, 2.0) == pytest.approx(2.0 - math.log(3.0))


def _step_storm(t):
    return 100 * MM_H if t < 1800 else 0.0


def test_ponded_water_infiltrates_after_the_rain_except_in_telemacs_own_equations():
    s = ga.Soil.from_parameters(10.0, 0.11, 0.45, 0.2)
    after = {}
    for kernel in ga.KERNELS:
        r = ga.simulate_column(s, _step_storm, duration=7200, dt=1.0, kernel=kernel)
        at_end_of_rain = r.depth[1799]
        after[kernel] = (at_end_of_rain, r.depth[-1])
    for kernel in ("sadeghi", "exact"):
        start, end = after[kernel]
        assert start > 0.01 and end < start           # the pond drains into the soil
    start, end = after["travert"]
    assert end == pytest.approx(start)               # as shipped: it stays


def test_a_sealed_soil_lets_all_rain_run_off():
    s = ga.Soil(ks_mm_h=0.0, a=0.05, dtheta=0.3)
    r = ga.simulate_column(s, 20 * MM_H, duration=3600, dt=2.0)
    assert r.infiltrated[-1] == 0.0
    assert r.depth[-1] == pytest.approx(0.020, rel=1e-6)


def test_evaporation_takes_only_the_surface_water_there_is():
    s = ga.Soil(ks_mm_h=0.0, a=0.0)
    r = ga.simulate_column(s, lambda t: 10 * MM_H if t < 360 else 0.0,
                           evaporation_rate=0.5 * MM_H, duration=36000, dt=10.0)
    assert r.depth.min() >= 0.0
    assert r.evaporated[-1] == pytest.approx(0.001, rel=1e-6)    # all the 1 mm of rain
    assert r.depth[-1] == pytest.approx(0.0, abs=1e-12)


def test_a_step_repeated_from_the_same_state_gives_the_same_result():
    """TELEMAC calls the routine once per sub-iteration of a step."""
    s = ga.Soil.from_parameters(5.0, 0.1, 0.45, 0.2)
    state = ga.NodeState(infiltrated=0.004, ponded=True, t_ponding=100.0,
                         f_ponding=0.003)
    one = ga.step(state, s, t_end=200.0, dt=1.0, rain=50 * MM_H, evaporation=0.0,
                  depth=0.002, rain_total=0.01)
    two = ga.step(state, s, t_end=200.0, dt=1.0, rain=50 * MM_H, evaporation=0.0,
                  depth=0.002, rain_total=0.01)
    assert one.infiltration == two.infiltration
    assert state.infiltrated == 0.004                   # the input state is untouched


def test_runon_ponds_a_node_that_the_rain_alone_would_not():
    s = ga.Soil.from_parameters(10.0, 0.11, 0.45, 0.2)
    state = ga.NodeState(infiltrated=0.001)
    dry = ga.step(state, s, t_end=10.0, dt=1.0, rain=1 * MM_H, evaporation=0.0,
                  depth=0.0, rain_total=0.0)
    wet = ga.step(state, s, t_end=10.0, dt=1.0, rain=1 * MM_H, evaporation=0.0,
                  depth=0.01, rain_total=0.0)
    off = ga.step(state, s, t_end=10.0, dt=1.0, rain=1 * MM_H, evaporation=0.0,
                  depth=0.01, rain_total=0.0, ponding_by_runon=False)
    assert not dry.state.ponded
    assert wet.state.ponded and wet.state.t_ponding == 10.0
    assert not off.state.ponded


# --------------------------------------------------------------------------- #
# rain
# --------------------------------------------------------------------------- #


def test_a_series_stamped_at_the_end_of_each_interval(tmp_path):
    path = tmp_path / "rain.csv"
    path.write_text("Datum;Niederschlag\n2024-06-02 13:00;2,0\n2024-06-02 14:00;10,0\n"
                    "2024-06-02 15:00;0,0\n")
    series = rainfall.read_series(path, units="mm", stamp="end")
    assert list(series.start_s) == [0.0, 3600.0, 7200.0]
    assert list(series.intensity_mm_h) == [2.0, 10.0, 0.0]
    assert series.origin == pd.Timestamp("2024-06-02 12:00")
    assert series.total_mm == pytest.approx(12.0)
    assert series.last_rain == 7200.0


def test_a_series_stamped_at_the_start_and_given_as_intensity(tmp_path):
    path = tmp_path / "rain.csv"
    path.write_text("time,rain\n0,60\n1800,0\n")
    series = rainfall.read_series(path, units="mm/h", stamp="start")
    assert list(series.end_s) == [1800.0, 3600.0]
    assert series.total_mm == pytest.approx(30.0)
    assert series.rate_at(900.0) == 60.0 and series.rate_at(2000.0) == 0.0


def test_telemacs_own_hyetograph_file_is_read_as_telemac_reads_it(tmp_path):
    """RUNOFF_GREENAMPT with RAINDEF = 3: two lines skipped, then time [s] and the depth [mm] of the interval that ends there; the first depth belongs to no interval. Space separated, with a blank at the end of a line, as in the file of the example example-agrohydrology."""
    path = tmp_path / "hyetograph.txt"
    path.write_text("#HYETOGRAPH FILE\n#T (s) RAINFALL (mm)\n0. 0\n3600 6.72\n"
                    "7200 4.11 \n10800 0\n\n\n")
    series = rainfall.read_series(path, units="mm/h", stamp="start")
    assert list(series.start_s) == [0.0, 3600.0, 7200.0]
    assert list(series.end_s) == [3600.0, 7200.0, 10800.0]
    assert np.allclose(series.intensity_mm_h, [6.72, 4.11, 0.0])
    assert series.total_mm == pytest.approx(10.83)
    assert series.last_rain == 7200.0 and series.origin is None
    assert "TELEMAC's hyetograph" in series.notes[0]
    with pytest.raises(ValueError, match="no dates"):
        rainfall.read_series(path, start="2024-06-02T14:00")
    path.write_text("#HYETOGRAPH FILE\n#T (s) RAINFALL (mm)\n0 0\n3600 6.72\nend\n")
    with pytest.raises(ValueError, match="line 5"):
        rainfall.read_series(path)
    # TELEMAC reads only the time of the first line (READ(UL,*) AT1)
    path.write_text("#HYETOGRAPH FILE\n#T (s) RAINFALL (mm)\n0.\n3600 6.72\n")
    assert list(rainfall.read_series(path).intensity_mm_h) == [6.72]
    # a byte order mark, a space after '#' and lower case are TELEMAC's file all the same
    for header in ("\ufeff#HYETOGRAPH FILE\n", "# hyetograph file\n", "# HYETOGRAPH\n"):
        path.write_text(header + "#T (s) RAINFALL (mm)\n0. 0\n3600 6.72\n", encoding="utf-8")
        series = rainfall.read_series(path, units="mm", stamp="start")
        assert list(series.intensity_mm_h) == [6.72], header


def test_a_rain_table_that_only_names_a_hyetograph_is_read_as_a_table(tmp_path):
    """Only a file that begins with TELEMAC's #HYETOGRAPH is its hyetograph file; a comment that names a hyetograph is a comment of an ordinary table, with its own units and stamps."""
    table = "time,rain\n0,0\n600,2\n1200,3\n"
    plain, named = tmp_path / "plain.csv", tmp_path / "named.csv"
    plain.write_text(table)
    expected = rainfall.read_series(plain, units="mm", stamp="end")
    for comments in ("# Hyetograph, DWD station 3668\n",
                     "# Hyetograph, station 3668\n# exported 2024-06-02\n"):
        named.write_text(comments + table)
        series = rainfall.read_series(named, units="mm", stamp="end")
        assert list(series.end_s) == list(expected.end_s) == [600.0, 1200.0, 1800.0]
        assert list(series.intensity_mm_h) == list(expected.intensity_mm_h) == [
            0.0, 12.0, 18.0]
        assert not any("TELEMAC" in note for note in series.notes)


def test_the_canonical_file_round_trips(tmp_path):
    series = rainfall.RainSeries([0, 600], [600, 1200], [5.0, 12.0])
    back = rainfall.read_series(series.write(tmp_path / "r.csv"))
    assert list(back.intensity_mm_h) == [5.0, 12.0]


def test_a_series_with_negative_rain_is_named():
    series = rainfall.RainSeries([0, 60], [60, 120], [1.0, -2.0])
    assert "negative rain" in series.problems()


KOSTRA = ([5, 10, 15, 20, 30, 45, 60, 90, 120], [9.8, 14.9, 18.3, 20.8, 24.4, 28.0,
                                                  30.6, 33.8, 36.4])


def test_euler_type_ii_holds_the_table_depth_in_every_window_from_its_peak():
    storm = rainfall.design_storm(*KOSTRA, duration_min=60, step_min=5)
    depths = storm.depth_mm
    peak = int(np.argmax(depths))
    assert peak == math.ceil(0.3 * 12) - 1                    # at 30 % of the duration
    for k in range(1, depths.size - peak + 1):
        expected = rainfall.depth_at(*KOSTRA, k * 5)
        assert depths[peak:peak + k].sum() == pytest.approx(expected, rel=1e-9)
    assert storm.total_mm == pytest.approx(30.6)
    assert np.all(np.diff(depths[:peak]) >= 0)                # rising before the peak


def test_the_alternating_block_storm_peaks_in_the_middle_and_holds_the_total():
    storm = rainfall.design_storm(*KOSTRA, duration_min=60, step_min=10,
                                  distribution="alternating")
    assert int(np.argmax(storm.depth_mm)) in (2, 3)
    assert storm.total_mm == pytest.approx(30.6)


def test_a_block_storm_is_one_intensity():
    storm = rainfall.design_storm(*KOSTRA, duration_min=30, step_min=10,
                                  distribution="block")
    assert np.allclose(storm.intensity_mm_h, 24.4 * 2)


# --------------------------------------------------------------------------- #
# wind and sun
# --------------------------------------------------------------------------- #


def test_fao56_example_19_hourly_reference_evapotranspiration():
    """N'Diaye, Senegal, 1 October: 16 deg 13' N, 16 deg 15' W, 8 m; the time zone is
    15 deg W, so 14:00-15:00 local time is 15:00-16:00 UTC."""
    ends = pd.DatetimeIndex(["2025-10-01 16:00", "2025-10-01 04:00"])
    ra = weather.extraterrestrial_radiation_hourly(ends, 16 + 13 / 60, -(16 + 15 / 60))
    assert ra[0] == pytest.approx(3.543, abs=0.002)
    assert ra[1] == 0.0
    et0 = weather.penman_monteith_hourly(radiation_w_m2=[2.450e6 / 3600, 0.0],
                                         temperature_c=[38, 28], humidity_pct=[52, 90],
                                         wind_2m=[3.3, 1.9], ra_mj=ra, elevation=8)
    assert et0[0] == pytest.approx(0.63, abs=0.01)
    assert et0[1] == pytest.approx(0.0, abs=0.01)


def test_a_wind_from_the_north_blows_to_the_south():
    u, v = weather.wind_components([10.0, 4.0], [0.0, 270.0])
    assert u[0] == pytest.approx(0.0, abs=1e-12) and v[0] == pytest.approx(-10.0)
    assert u[1] == pytest.approx(4.0) and v[1] == pytest.approx(0.0, abs=1e-12)
    assert weather.wind_at_2m(10.0) == pytest.approx(7.48, abs=0.01)


def test_the_forcing_table_holds_rain_and_evaporation_and_ends_with_zeros(tmp_path):
    rain = rainfall.RainSeries([0, 600], [600, 1200], [5.0, 12.0])
    meteo = weather.WeatherSeries(np.array([0.0, 900.0]), np.array([900.0, 1800.0]),
                                  {"et0": np.array([0.2, 0.4])})
    evap = weather.evaporation_rate(meteo, "et0", factor=1.05)
    table = weather.forcing_table(rain, meteo, evap, until=3000.0)
    assert list(table["time_s"]) == [0.0, 600.0, 900.0, 1200.0, 1800.0, 3000.0]
    assert list(table["rain_mm_h"]) == [5.0, 12.0, 12.0, 0.0, 0.0, 0.0]
    assert table["evaporation_mm_h"].iloc[2] == pytest.approx(0.42)
    path = weather.write_forcing(tmp_path / "forcing.txt", table)
    lines = path.read_text().splitlines()
    assert lines[0].startswith("#") and lines[1].startswith("#")
    assert int(lines[2]) == len(table)
    assert lines[-1].split() == ["3000.000", "0", "0"]


def test_the_atmospheric_file_has_telemacs_header(tmp_path):
    meteo = weather.WeatherSeries(np.array([0.0, 3600.0]), np.array([3600.0, 7200.0]),
                                  {"wind_speed_10m": np.array([5.0, 8.0]),
                                   "wind_direction_10m": np.array([270.0, 180.0])})
    lines = weather.write_atmosphere(tmp_path / "a.txt", meteo).read_text().splitlines()
    assert lines[1].split() == ["T", "WINDX", "WINDY"]
    assert lines[2].split() == ["s", "m/s", "m/s"]
    assert [float(v) for v in lines[4].split()] == pytest.approx([1800.0, 5.0, 0.0])


# --------------------------------------------------------------------------- #
# soil
# --------------------------------------------------------------------------- #


def test_the_rawls_table_is_the_one_of_the_paper():
    """Rawls, Brakensiek and Miller (1983), Table 2, all horizons; the loam and silt
    loam conductivities are the ones copies most often get wrong."""
    loam = soil.texture("Loam")
    assert (loam.porosity, loam.effective_porosity, loam.suction_cm, loam.ks_cm_h) == \
        (0.463, 0.434, 8.89, 0.34)
    silt_loam = soil.texture("silt_loam")
    assert (silt_loam.suction_cm, silt_loam.ks_cm_h) == (16.68, 0.65)
    assert soil.texture("clay").ks_mm_h == pytest.approx(0.3)
    assert soil.texture("silt") is soil.texture("silt loam")
    assert len(soil.RAWLS_1983) == 11


@pytest.mark.parametrize("sand,silt,clay,name", [
    (92, 5, 3, "sand"), (82, 12, 6, "loamy sand"), (65, 25, 10, "sandy loam"),
    (40, 40, 20, "loam"), (20, 65, 15, "silt loam"), (5, 88, 7, "silt"),
    (60, 15, 25, "sandy clay loam"), (35, 33, 32, "clay loam"),
    (10, 58, 32, "silty clay loam"), (50, 5, 45, "sandy clay"),
    (5, 50, 45, "silty clay"), (20, 20, 60, "clay"),
])
def test_the_usda_texture_triangle(sand, silt, clay, name):
    assert soil.classify_texture([sand], [silt], [clay])[0] == name
    # SoilGrids stores g/kg: the same soil, ten times the numbers
    assert soil.classify_texture([10 * sand], [10 * silt], [10 * clay])[0] == name


def test_a_soil_table_is_filled_from_the_texture_of_each_zone(tmp_path):
    path = tmp_path / "soil.csv"
    path.write_text("zone_id,texture,ks_mm_h,suction_m,theta_s,theta_i\n"
                    "1,loam,,,,0.2\n2,sandy loam,5,,,\n3,sealed,,,,\n")
    zones = soil.read_soil_table(path, initial_saturation=0.5)
    assert zones[1].ks_mm_h == pytest.approx(3.4)
    assert zones[1].a == pytest.approx(0.0889 * (0.463 - 0.2))
    assert zones[2].ks_mm_h == 5.0
    sandy = soil.texture("sandy loam")
    assert zones[2].theta_i == pytest.approx(sandy.theta_r + 0.5 * (0.453 - sandy.theta_r))
    assert zones[3].ks_mm_h == 0.0
    assert all(not z.problems() for z in zones.values())


def test_soil_values_in_the_wrong_unit_are_named():
    zone = soil.ZoneSoil(1, ks_mm_h=3.4, suction_m=8.89, theta_s=0.46, theta_i=0.5)
    problems = " ".join(zone.problems())
    assert "suction" in problems and "theta_i" in problems


def test_an_unknown_texture_is_refused():
    with pytest.raises(ValueError, match="unknown texture"):
        soil.zone_soil(1, texture_name="peat")


# --------------------------------------------------------------------------- #
# the case: a small synthetic V-catchment, built through the pipeline
# --------------------------------------------------------------------------- #

X0, Y0 = 700000.0, 5300000.0


def _catchment(root: Path, *, outlet: str = "full", extra: str = "") -> Path:
    """Two planes draining to a sloping channel; the outlet on the south edge."""
    import geopandas as gpd
    import rasterio
    from rasterio.transform import from_origin
    from shapely.geometry import LineString, Polygon

    src = root / "user-sources"
    src.mkdir(parents=True, exist_ok=True)
    width, length, channel = 60.0, 50.0, 6.0
    xs = np.arange(-5, width + 5, 1.0) + 0.5
    ys = np.arange(length + 5, -5, -1.0) - 0.5
    gx, gy = np.meshgrid(xs, ys)
    d = np.abs(gx - width / 2)
    z = 100.0 + 0.02 * gy + np.where(d < channel / 2, 0.0, 0.3 + 0.05 * (d - channel / 2))
    with rasterio.open(src / "dem.tif", "w", driver="GTiff", height=z.shape[0],
                       width=z.shape[1], count=1, dtype="float32", crs="EPSG:25832",
                       transform=from_origin(X0 - 5, Y0 + length + 5, 1, 1)) as dst:
        dst.write(z.astype("float32"), 1)
    poly = Polygon([(X0, Y0), (X0 + width, Y0), (X0 + width, Y0 + length),
                    (X0, Y0 + length)])
    gpd.GeoDataFrame(geometry=[poly], crs="EPSG:25832").to_file(src / "catchment.gpkg")
    if outlet == "full":
        line = LineString([(X0 - 1, Y0), (X0 + width + 1, Y0)])
    else:
        line = LineString([(X0 + width / 2 - channel, Y0),
                           (X0 + width / 2 + channel, Y0)])
    gpd.GeoDataFrame({"Type (inflow/outflow)": ["outflow"]}, geometry=[line],
                     crs="EPSG:25832").to_file(src / "liquid-boundaries.gpkg")
    (src / "rain.csv").write_text("time,rain\n0,60\n600,0\n")
    (src / "soil.csv").write_text("zone_id,texture,theta_i\n1,sandy loam,0.15\n")
    case = root / "catchment.axq-case"
    case.write_text(f"""project:
  name: catchment
  crs_epsg: 25832
  sim_dir: axqua-case
telemac:
  solver: telemac2d
  pysource: {Path(__file__).parent / "fakes" / "fake_pysource.sh"}
geodata:
  dem_initial: user-sources/dem.tif
  boundary: user-sources/catchment.gpkg
boundaries:
  liquid_boundaries: user-sources/liquid-boundaries.gpkg
mesh:
  default_size: 5.0
  min_size: 1.0
hydrology:
  enabled: true
  rainfall: user-sources/rain.csv
  rainfall_stamp: start
  soil_table: user-sources/soil.csv
  drain_time: 600
{extra}""")
    return case


def test_a_hydrology_case_builds_without_a_discharge_or_a_centerline(tmp_path):
    from axqua.config import load_config
    from axqua.core.capabilities import Capability
    from axqua.core.selafin import SelafinFile
    from axqua.solvers.telemac import pipeline, spec

    cfg = load_config(_catchment(tmp_path))
    assert cfg.outflow_condition == "normal_depth"
    assert cfg.boundaries.prescribed_flowrate is None
    art = pipeline.run(cfg, validate_env=False, log_to_file=False)
    model = Path(cfg.model_dir)
    cas = (model / "hydrology2d.cas").read_text()
    assert art.hydrology_cas == model / "hydrology2d.cas"
    assert not (model / "steady2d.cas").exists()        # a catchment has no steady state
    # DAMOCLES reads 72 columns (a comment may run on; nothing of it is read)
    assert max(len(line) for line in cas.splitlines() if not line.startswith("/")) <= 72
    for keyword in ("RAINFALL-RUNOFF MODEL : 3", "RAIN OR EVAPORATION : YES",
                    "FORTRAN FILE : 'user_fortran'", "FORMATTED DATA FILE 1 : forcing.txt",
                    "EQUATIONS : 'SAINT-VENANT FV'",
                    "OPTION OF THE HYDROSTATIC RECONSTRUCTION : 2",
                    "STAGE-DISCHARGE CURVES : 1", "TURBULENCE MODEL : 1",
                    "DURATION : 1200.0"):
        assert keyword in cas, keyword
    routine = (model / "user_fortran" / "runoff_greenampt.f").read_text()
    assert max(len(line) for line in routine.splitlines()) <= 72
    assert "SUBROUTINE RUNOFF_GREENAMPT" in routine and "HMP_KRN = 1" in routine
    # the outflow line prescribes its level from the Z(Q) curve, the rest is wall
    codes = [line.split()[:3] for line in (model / "boundaries.cli").read_text()
             .splitlines()]
    assert ["5", "4", "4"] in codes and ["2", "2", "2"] in codes
    assert ["4", "5", "5"] not in codes
    curves = (model / "outlet-rating.txt").read_text().splitlines()
    assert "Q(1) Z(1)" in curves
    geometry = SelafinFile(model / "geometry.slf")
    assert {"KS", "GA SUCTION A", "GA DELTA THETA", "INFILTRATED"} <= set(
        geometry.var_names)
    ks = geometry.frame(0)["KS"]
    assert np.allclose(ks, 10.9)                         # sandy loam, mm/h
    status = spec.SPEC.status(cfg)
    hydrology = status.capability(Capability.HYDROLOGY)
    assert hydrology.configured and hydrology.built and not hydrology.run
    # a catchment has no steady simulation and no steady mesh study
    assert not status.capability(Capability.STEADY2D).configured
    assert not status.capability(Capability.MESH_CONVERGENCE).configured
    # its inflow lines follow their hydrograph in the hydrology run, and HydroBayesCal has no steady run to perturb
    from types import SimpleNamespace

    cfg.boundaries.inflow = model / "hydrograph.csv"
    cfg.calibration = SimpleNamespace(parameters={"zone1": [0.1, 0.3]})
    cfg.ground_truth = SimpleNamespace(measurements="points.csv", sources=[], targets=None)
    (model / cfg.results_slf).write_bytes(b"")      # a steady result left from a river case
    status = spec.SPEC.status(cfg)
    for capability in (Capability.UNSTEADY2D, Capability.UNSTEADY3D, Capability.STEADY3D,
                       Capability.VERTICAL_CONVERGENCE, Capability.CALIBRATION):
        assert not status.capability(capability).configured, capability
    # and the backend refuses them, whatever files a river past left in the model folder
    from axqua.core.errors import ConfigError
    from axqua.solvers.telemac.backend import BACKEND

    for call, capability in ((BACKEND.build, Capability.STEADY3D),
                             (BACKEND.run, Capability.STEADY3D),
                             (BACKEND.run, Capability.STEADY2D),
                             (BACKEND.run, Capability.UNSTEADY2D),
                             (BACKEND.study, Capability.VERTICAL_CONVERGENCE),
                             (BACKEND.study, Capability.CALIBRATION)):
        with pytest.raises(ConfigError, match="hydrology mode"):
            call(cfg, capability)
    cfg.hydrology.enabled = False
    assert spec._calibration_configured(cfg) and spec._has_varying_inflow(cfg)
    assert spec._three_d_configured(cfg)


def test_a_hydrology_case_shows_water_from_one_millimeter(tmp_path):
    """Sheet flow is millimeters deep: the river's 1 cm would leave the map of a catchment nearly empty. A case that sets its own threshold keeps it."""
    from axqua.config import HYDROLOGY_WET_DEPTH, dump_config, load_config
    from axqua.core.capabilities import _display_settings

    case = _catchment(tmp_path)
    cfg = load_config(case)
    assert cfg.wet_depth == HYDROLOGY_WET_DEPTH == 0.001
    assert _display_settings(cfg)["wet_depth"] == 0.001
    # the default is resolved where it is read, not stored: after a dump and a load with the mode switched off, the case is a river case with the river's 1 cm
    cfg.hydrology.enabled = False
    assert cfg.wet_depth == 0.01
    dumped = tmp_path / "dumped.axq-case"
    dump_config(cfg, dumped)
    assert load_config(dumped).wet_depth == 0.01
    case.write_text(case.read_text() + "hydrodynamics:\n  wet_depth: 0.005\n")
    assert load_config(case).wet_depth == 0.005
    river = case.read_text().replace("  enabled: true\n", "  enabled: false\n")
    case.write_text(river.replace("hydrodynamics:\n  wet_depth: 0.005\n", ""))
    assert load_config(case).wet_depth == 0.01


def test_an_overfall_outlet_holds_no_water_at_its_lines(tmp_path):
    """The lower edge of a field: a level below every node, so TELEMAC prescribes a depth of zero along the line and its finite-volume kernel lets water out only."""
    from axqua.config import load_config
    from axqua.core.selafin import SelafinFile
    from axqua.solvers.telemac import pipeline

    cfg = load_config(_catchment(tmp_path, extra="  outlet_condition: overfall\n"))
    assert cfg.outflow_condition == "overfall"
    pipeline.run(cfg, validate_env=False, log_to_file=False)
    model = Path(cfg.model_dir)
    cas = (model / "hydrology2d.cas").read_text()
    bed = SelafinFile(model / "geometry.slf").frame(0)["BOTTOM"]
    level = next(float(line.split(":")[1]) for line in cas.splitlines()
                 if line.startswith("PRESCRIBED ELEVATIONS"))
    assert level == pytest.approx(float(bed.min()) - 1.0, abs=1e-3)
    assert "STAGE-DISCHARGE CURVES" not in cas
    codes = [line.split()[:3] for line in (model / "boundaries.cli").read_text()
             .splitlines()]
    assert ["5", "4", "4"] in codes and ["2", "2", "2"] in codes
    assert ["4", "4", "4"] not in codes


def test_a_missing_outlet_level_names_the_setting_of_the_mode(tmp_path):
    """In hydrology mode the outlet is hydrology.outlet_condition; boundaries.outflow_condition has no effect there, so the message must not send the user to it."""
    from axqua.config import load_config

    cfg = load_config(_catchment(tmp_path, extra="  outlet_condition: elevation\n"))
    with pytest.raises(ValueError, match=r"required by hydrology\.outlet_condition: elevation$"):
        cfg.validate()


@pytest.mark.parametrize("outlet", ["overfall", "normal_depth"])
def test_an_inflow_hydrograph_runs_in_the_hydrology_run(tmp_path, outlet):
    """A catchment upstream enters through an inflow line with its hydrograph. The liquid-boundaries file gives the inflow Q(t) and, for an outlet that holds one level, that level (overfall: below the mesh); a normal_depth outlet takes its level from TELEMAC's Z(Q) curve and gets no column."""
    import geopandas as gpd
    from shapely.geometry import LineString

    from axqua.config import load_config
    from axqua.core.selafin import SelafinFile
    from axqua.solvers.telemac import pipeline

    case = _catchment(tmp_path, outlet="channel", extra=f"  outlet_condition: {outlet}\n")
    src = tmp_path / "user-sources"
    lines = [LineString([(X0 + 24, Y0), (X0 + 36, Y0)]),
             LineString([(X0 + 24, Y0 + 50), (X0 + 36, Y0 + 50)])]
    gpd.GeoDataFrame({"Type (inflow/outflow)": ["outflow", "inflow"]}, geometry=lines,
                     crs="EPSG:25832").to_file(src / "liquid-boundaries.gpkg")
    (src / "inflow.csv").write_text("time,Q\n0,0.05\n300,0.2\n600,0.05\n")
    case.write_text(case.read_text().replace(
        "  liquid_boundaries: user-sources/liquid-boundaries.gpkg\n",
        "  liquid_boundaries: user-sources/liquid-boundaries.gpkg\n"
        "  inflow: user-sources/inflow.csv\n"))
    cfg = load_config(case)
    pipeline.run(cfg, validate_env=False, log_to_file=False)
    model = Path(cfg.model_dir)
    rows = (model / cfg.liquid_boundaries_file).read_text().splitlines()
    header = next(line.split() for line in rows if line.startswith("T"))
    assert any(name.startswith("Q(") for name in header)
    levels = [name for name in header if name.startswith("SL(")]
    if outlet == "normal_depth":
        assert not levels
        return
    assert len(levels) == 1
    column = header.index(levels[0])
    bed = SelafinFile(model / "geometry.slf").frame(0)["BOTTOM"]
    values = [float(line.split()[column]) for line in rows[3:] if line.strip()]
    assert values and values == pytest.approx([float(bed.min()) - 1.0] * len(values),
                                              abs=1e-3)


def test_the_hydrology_mesh_is_isotropic_and_honors_its_zones(tmp_path):
    import geopandas as gpd
    from shapely.geometry import Polygon

    from axqua.config import load_config
    from axqua.solvers.telemac import mesh

    case = _catchment(tmp_path)
    zone = Polygon([(X0 + 25, Y0 - 1), (X0 + 35, Y0 - 1), (X0 + 35, Y0 + 51),
                    (X0 + 25, Y0 + 51)])
    gpd.GeoDataFrame({"Zone Name": ["channel"], "Max Edge Length (m)": [1.5]},
                     geometry=[zone], crs="EPSG:25832").to_file(
        tmp_path / "user-sources" / "zones.gpkg")
    text = case.read_text().replace("  boundary: user-sources/catchment.gpkg",
                                    "  boundary: user-sources/catchment.gpkg\n"
                                    "  mesh_zones: user-sources/zones.gpkg")
    case.write_text(text)
    cfg = load_config(case)
    built = mesh.build_mesh(cfg)
    cx = built.x[built.triangles].mean(axis=1) - X0
    edges = np.hypot(np.diff(built.x[built.triangles][:, [0, 1, 2, 0]], axis=1),
                     np.diff(built.y[built.triangles][:, [0, 1, 2, 0]], axis=1))
    inside = (cx > 26) & (cx < 34)
    outside = (cx < 15) | (cx > 45)
    assert np.median(edges[inside]) < 0.5 * np.median(edges[outside])
    aspect = edges.max(axis=1) / edges.min(axis=1)
    assert np.percentile(aspect, 95) < 1.8               # near-equilateral everywhere
    estimate = mesh.estimate_hydrology_elements(cfg)
    assert 0.6 < built.nelem / estimate < 1.6


def test_the_checkup_finds_flow_across_a_wall_and_names_a_free_outlet(tmp_path):
    from axqua.config import load_config
    from axqua.hydrology.checks import run_checks

    cfg = load_config(_catchment(tmp_path, outlet="channel",
                                 extra="  outlet_condition: free\n"))
    codes = {f.code for f in run_checks(cfg, terrain=True)}
    assert "axqua.hydrology.flow_across_wall" in codes
    assert "axqua.hydrology.free_outlet" in codes
    cfg = load_config(_catchment(tmp_path / "full"))
    codes = {f.code for f in run_checks(cfg, terrain=True)}
    assert "axqua.hydrology.flow_across_wall" not in codes
    assert not [c for c in codes if c.endswith("unreadable") or c.endswith("invalid")]


def test_the_priority_flood_fills_a_pit_and_drains_to_the_lowest_exit():
    from axqua.hydrology import terrain

    z = np.add.outer(np.arange(10.0), np.zeros(10)) * 0.1     # falls to row 0
    z[5, 5] -= 1.0                                              # a pit
    grid = terrain.TerrainGrid(z=z, mask=np.ones_like(z, bool), x0=0.0, y0=10.0,
                               cell=1.0)
    flood = terrain.priority_flood(grid)
    assert flood.filled[5, 5] == pytest.approx(z[4, 5])   # filled to its spill point
    assert flood.area.sum() > 0
    exits = flood.area.ravel()[flood.exits]
    assert exits.sum() == pytest.approx(100.0)                 # all area leaves somewhere


def test_every_hydrology_code_is_explained_on_its_page():
    import re

    from axqua.hydrology.checks import CODES

    docs = Path(__file__).parents[1] / "docs" / "troubleshooting"
    for code, severity in CODES.items():
        page = "errors" if severity == "error" else "warnings"
        text = (docs / f"{page}.rst").read_text(encoding="utf-8")
        anchor = code.replace(".", "-").replace("_", "-")
        assert re.search(rf"^\.\. _{anchor}:", text, re.M), (code, page)


def test_the_report_reads_the_listing_of_a_run(tmp_path):
    from axqua.hydrology import report

    listing = tmp_path / "hydrology2d.cas_2026.sortie"
    listing.write_text("""
AXQUA HYDROLOGY   6.00000000E+01   4.20000000E+01   1.00000000E+01   0.00000000E+00
AXQUA HYDROLOGY   1.20000000E+02   8.40000000E+01   2.00000000E+01   1.00000000E+00
                   FINAL BALANCE OF WATER VOLUME

     RELATIVE ERROR CUMULATED ON VOLUME:   -0.6030636E-14

     INITIAL VOLUME              :     0.000000     M3
     FINAL VOLUME                :     13.00000     M3
     VOLUME THAT ENTERED THE DOMAIN:    -50.00000     M3  ( IF <0 EXIT )
     VOLUME ADDED BY SOURCE TERM   :     63.00000     M3
     TOTAL VOLUME LOST             :   -0.7563727E-11 M3
""")
    budget = report.read_budget(listing)
    assert list(budget["rain"]) == [42.0, 84.0]
    final = report.read_final_balance(listing)
    assert final["entered"] == -50.0 and final["final"] == 13.0

    class Sortie:
        time = np.array([60.0, 120.0])
        volume = np.array([30.0, 13.0])
        gross_out = np.array([0.2, 0.6])
        gross_in = np.array([0.0, 0.0])

    result = report.balance(Sortie(), budget, final)
    assert result.outflow_m3 == pytest.approx(50.0)
    assert result.storage_m3 == pytest.approx(13.0)
    assert result.error_m3 == pytest.approx(84.0 - 50.0 - 20.0 - 1.0 - 13.0)
    assert result.runoff_coefficient == pytest.approx(50.0 / 84.0)


def test_the_report_keeps_the_main_listing_and_deletes_the_processor_copies(tmp_path):
    """As after a steady run: the balance is in the main listing, the copy of each processor is clutter."""
    from axqua.config import load_config
    from axqua.hydrology import report

    cfg = load_config(_catchment(tmp_path))
    model = Path(cfg.model_dir)
    model.mkdir(parents=True, exist_ok=True)
    blocks = "".join(f"""
AXQUA HYDROLOGY   {t:.8E}   {0.1 * t:.8E}   {0.05 * t:.8E}   0.00000000E+00
                       BALANCE OF WATER VOLUME
     VOLUME IN THE DOMAIN :    {0.01 * t:.6f}     M3
     FLUX BOUNDARY    1:   {-0.0001 * t:.7E} M3/S  ( >0 : ENTERING  <0 : EXITING )
     RELATIVE ERROR IN VOLUME AT T =        {t:.1f}     S :    0.000000
""" for t in (60.0, 120.0, 180.0))
    main = model / "hydrology2d.cas_2026-10-25-14h07min42s.sortie"
    main.write_text(" NUMBER OF LIQUID BOUNDARIES:           1\n" + blocks)
    copies = [model / f"hydrology2d.cas_2026-10-25-14h07min42s_p0000{k}.sortie"
              for k in (1, 2)]
    for copy in copies:
        copy.write_text(main.read_text())
    result = report.analyze(cfg)
    assert result.rain_m3 == pytest.approx(18.0)
    assert main.exists() and not any(copy.exists() for copy in copies)
    assert (Path(cfg.postprocessing_dir) / "hydrology" / "water-balance.json").exists()


def test_the_cli_builds_a_design_storm_and_lists_portals(tmp_path, capsys):
    import json

    from axqua import cli

    case = _catchment(tmp_path)
    assert cli.main(["hydrology", "design", str(case), "--pairs",
                     "5:9.8,10:14.9,15:18.3,30:24.4,60:30.6", "--duration", "60",
                     "--step", "10", "--json"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["ok"] and answer["data"]["total_mm"] == pytest.approx(30.6)
    assert answer["data"]["settings"]["hydrology.rainfall"] == \
        "user-sources/hydrology/design-storm.csv"
    assert cli.main(["hydrology", "portals", "--country", "DE", "--json"]) == 0
    portals = json.loads(capsys.readouterr().out)["data"]
    assert portals["national"] and all(p["country"] == "DE" for p in portals["national"])
    assert "national meteorological agency" in portals["advice"]


def test_an_event_from_open_meteo_becomes_rain_and_weather(tmp_path, monkeypatch):
    from axqua.config import load_config
    from axqua.hydrology import fetch

    cfg = load_config(_catchment(tmp_path))
    hours = pd.date_range("2024-06-02 00:00", periods=48, freq="h")

    def fake(lon, lat, start, end, variables):
        frame = pd.DataFrame({"time": hours})
        for name in variables:
            frame[name] = 0.0
        frame.loc[13, "precipitation"] = 12.0          # 12:00 to 13:00
        frame.attrs["url"] = "https://archive-api.open-meteo.com/v1/archive?test"
        return frame

    monkeypatch.setattr(fetch, "_open_meteo", fake)
    event = fetch.fetch_event(cfg, "2024-06-02T10:00", "2024-06-02T16:00", tmp_path / "o")
    series = rainfall.read_series(event.rainfall)
    assert series.total_mm == pytest.approx(12.0)
    assert series.rate_at(2.5 * 3600) == pytest.approx(12.0)   # 12:00-13:00 from 10:00
    meta = Path(str(event.rainfall) + ".json").read_text()
    assert "CC BY 4.0" in meta and "Open-Meteo" in meta
    assert fetch.worldcover_tiles((12.1, 46.0, 12.5, 47.0)) == ["N45E012"]
    assert fetch.worldcover_tiles((11.5, 47.9, 12.2, 48.3)) == [
        "N45E009", "N45E012", "N48E009", "N48E012"]


# ------------------------------------------------- what the review of 2026-10-25 found


def test_a_dry_run_of_a_hydrology_case_launches_its_own_steering_file(tmp_path,
                                                                     monkeypatch):
    """The build of a catchment writes hydrology2d.cas and no steady2d.cas."""
    from types import SimpleNamespace

    from axqua import workflow
    from axqua.config import load_config
    from axqua.solvers.telemac import pipeline

    launched = {}

    def fake(runtime, cfg, **kwargs):
        launched.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(workflow, "run_solver_streaming", fake)
    cfg = load_config(_catchment(tmp_path))
    pipeline.run(cfg, validate_env=False, dry_run=True, log_to_file=False)
    assert launched["cas_file"] == "hydrology2d.cas"
    assert launched["duration"] == pytest.approx(1200.0)     # the rain + drain_time


@pytest.mark.parametrize("change, word", [
    (lambda cfg: setattr(cfg.boundaries, "internal_sources", True), "internal_sources"),
    (lambda cfg: setattr(cfg.gain_lose, "enabled", True), "gain-lose"),
])
def test_what_the_hydrology_build_does_not_write_is_refused(tmp_path, change, word):
    from axqua.config import load_config

    cfg = load_config(_catchment(tmp_path))
    cfg._check_hydrology()                                  # as it is: accepted
    change(cfg)
    with pytest.raises(ValueError, match=word):
        cfg._check_hydrology()


def test_an_hourly_file_of_the_dwd_is_read_with_its_dates_and_its_gap(tmp_path):
    """MESS_DATUM is YYYYMMDDHH as one integer, R1 the rain, -999 a missing value."""
    path = tmp_path / "produkt_rr_stunde_20240601_20240603_03379.txt"
    path.write_text(
        "STATIONS_ID;MESS_DATUM;QN_8;  R1;RS_IND;WRTR;eor\n"
        "       3379;2024060211;    3;   0.0;   0;-999;eor\n"
        "       3379;2024060212;    3;   2.1;   1;   6;eor\n"
        "       3379;2024060213;    3;-999;-999;-999;eor\n"
        "       3379;2024060214;    3;   4.0;   1;   6;eor\n")
    series = rainfall.read_series(path, units="mm", stamp="end")
    assert series.origin == pd.Timestamp("2024-06-02 10:00")
    assert list(series.end_s) == [3600.0, 7200.0, 10800.0, 14400.0]
    assert list(series.intensity_mm_h) == pytest.approx([0.0, 2.1, 0.0, 4.0])
    assert series.gaps == 1 and series.gap_hours == pytest.approx(1.0)
    assert series.total_mm == pytest.approx(6.1)


def test_seconds_are_not_read_as_dates():
    seconds = pd.Series([0, 3600, 7200, 10800])
    assert rainfall._integer_dates(seconds) is None
    assert rainfall._seconds(seconds)[1] is None


def test_a_gap_in_a_record_stays_a_gap_and_the_checkup_names_it(tmp_path):
    """The row after a gap used to be stretched over it, adding rain never measured."""
    from axqua.config import load_config
    from axqua.hydrology.checks import run_checks

    case = _catchment(tmp_path)
    (tmp_path / "user-sources" / "rain.csv").write_text(
        "time,rain\n2024-06-02 12:00,10\n2024-06-02 13:00,\n2024-06-02 14:00,10\n")
    series = rainfall.read_series(tmp_path / "user-sources" / "rain.csv")
    assert list(series.intensity_mm_h) == [10.0, 0.0, 10.0]
    assert series.total_mm == pytest.approx(20.0)
    codes = {f.code for f in run_checks(load_config(case))}
    assert "axqua.hydrology.rainfall_gaps" in codes


def test_a_design_storm_clears_the_time_zero_of_an_earlier_event(tmp_path, capsys):
    import json

    from axqua import cli
    from axqua.config import load_config
    from axqua.hydrology.checks import run_checks

    case = _catchment(tmp_path)
    assert cli.main(["hydrology", "design", str(case), "--pairs", "10:15,60:30",
                     "--duration", "60", "--step", "10", "--json"]) == 0
    settings = json.loads(capsys.readouterr().out)["data"]["settings"]
    assert "hydrology.rainfall_start" in settings
    assert settings["hydrology.rainfall_start"] is None      # removed from the case
    # the weather of an event under a rain without a date is named
    (tmp_path / "user-sources" / "weather.csv").write_text(
        "time,wind_speed_10m,wind_direction_10m\n2024-06-02 13:00,5,270\n"
        "2024-06-02 14:00,5,270\n2024-06-02 15:00,5,270\n")
    case = _catchment(tmp_path, extra="  weather: user-sources/weather.csv\n"
                                      "  wind: true\n")
    codes = {f.code for f in run_checks(load_config(case))}
    assert "axqua.hydrology.weather_alignment" in codes


@pytest.mark.parametrize("statement, closed", [
    ("", False),                                            # says nothing: an error
    (" THERE IS     1 LIQUID BOUNDARIES:\n\n THERE IS     1 SOLID BOUNDARIES:\n", False),
    (" THERE IS     1 SOLID BOUNDARIES:\n", True),          # serial, no liquid one
    (" NUMBER OF LIQUID BOUNDARIES:           0\n", True),   # parallel
    (" NUMBER OF LIQUID BOUNDARIES:           2\n", False)])
def test_a_listing_without_boundary_fluxes_is_accepted_when_it_says_its_domain_is_closed(
        tmp_path, statement, closed):
    """The listing of the run decides, not the case as it is built now (an outlet
    added and built after the run would otherwise make its listing unreadable)."""
    from axqua.solvers.telemac.sortie import read_sortie

    listing = tmp_path / "case.cas_2026.sortie"
    listing.write_text(statement + "".join(
        f"                            BALANCE OF WATER VOLUME\n"
        f"     VOLUME IN THE DOMAIN :   {v:.6E} M3\n"
        f"     RELATIVE ERROR IN VOLUME AT T =     {t:.4f} S :  0.000000E+00\n"
        for t, v in ((60.0, 10.0), (120.0, 20.0))))
    if not closed:
        with pytest.raises(ValueError, match="no boundary fluxes"):
            read_sortie(listing)
        return
    sortie = read_sortie(listing)
    assert list(sortie.time) == [60.0, 120.0] and sortie.fluxes.shape == (2, 0)
    assert sortie.liquid_boundaries == 0


def test_the_size_grid_is_computed_once_per_build_and_not_by_a_quick_checkup(
        tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import Polygon

    from axqua.config import load_config
    from axqua.hydrology.checks import run_checks
    from axqua.solvers.telemac import mesh

    case = _catchment(tmp_path)
    zone = Polygon([(X0 + 25, Y0 - 1), (X0 + 35, Y0 - 1), (X0 + 35, Y0 + 51),
                    (X0 + 25, Y0 + 51)])
    zones = tmp_path / "user-sources" / "zones.gpkg"
    gpd.GeoDataFrame({"Zone Name": ["channel"], "Max Edge Length (m)": [1.5]},
                     geometry=[zone], crs="EPSG:25832").to_file(zones)
    case.write_text(case.read_text().replace(
        "  boundary: user-sources/catchment.gpkg",
        "  boundary: user-sources/catchment.gpkg\n  mesh_zones: user-sources/zones.gpkg"))
    cfg = load_config(case)
    calls = []
    real = mesh._hydrology_size_grid
    monkeypatch.setattr(mesh, "_hydrology_size_grid",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    monkeypatch.setattr(mesh, "_SIZE_GRIDS", {})
    run_checks(cfg, terrain=False)                     # the case settings, a refresh
    assert calls == []
    run_checks(cfg, terrain=True)
    mesh.build_mesh(cfg)                               # its log line and its size field
    assert calls == [1]
    gpd.GeoDataFrame({"Zone Name": ["channel"], "Max Edge Length (m)": [2.0]},
                     geometry=[zone], crs="EPSG:25832").to_file(zones)
    mesh.estimate_hydrology_elements(cfg)              # a changed layer is read again
    assert calls == [1, 1]


@pytest.mark.parametrize("law, coefficient, ks", [
    (4, 0.03, (0.03 / 0.0474) ** 6), (3, 25.0, ((1 / 25.0) / 0.0474) ** 6),
    (5, 0.2, 0.2), (2, 50.0, None)])
def test_one_conversion_of_a_friction_law_to_a_nikuradse_ks(law, coefficient, ks):
    from axqua.rating import ks_from_friction

    if ks is None:
        assert ks_from_friction(law, coefficient) is None
    else:
        assert ks_from_friction(law, coefficient) == pytest.approx(ks)


# --------------------------------------------- what the second review of 2026-10-25 found


def test_daily_and_ten_minute_files_of_the_dwd_are_depths_whatever_the_settings(
        tmp_path):
    """RS is mm per precipitation day of the DWD, RWS_10 mm per 10 minutes before
    MESS_DATUM; the units and stamp settings (mm/h, end by default) do not apply."""
    daily = tmp_path / "produkt_nieder_tag_03379.txt"
    daily.write_text("STATIONS_ID;MESS_DATUM;QN_6;  RS;RSF;SH_TAG;NSH_TAG;eor\n"
                     "3379;20240601;3;20.0;6;0;0;eor\n3379;20240602;3;10.0;6;0;0;eor\n")
    series = rainfall.read_series(daily)                     # the defaults: mm/h, end
    assert series.total_mm == pytest.approx(30.0)
    assert series.origin == pd.Timestamp("2024-06-01 05:50")   # its morning observation
    ten = tmp_path / "produkt_zehn_min_rr_03379.txt"
    ten.write_text("STATIONS_ID;MESS_DATUM;QN;RWS_DAU_10;RWS_10;RWS_IND_10;eor\n"
                   "3379;202406021210;3;10;1.5;1;eor\n3379;202406021220;3;10;0.5;1;eor\n")
    series = rainfall.read_series(ten)
    assert list(series.intensity_mm_h) == pytest.approx([9.0, 3.0])
    assert series.origin == pd.Timestamp("2024-06-02 12:00")


def test_a_units_row_and_a_sum_are_not_data(tmp_path):
    path = tmp_path / "rain.csv"
    path.write_text("time,rain\ns,mm/h\n0,1\n3600,2\n7200,0\nSumme,3\n")
    series = rainfall.read_series(path)
    assert list(series.intensity_mm_h) == [1.0, 2.0, 0.0] and series.gaps == 0
    path.write_text("time,rain\n0,1\nkaputt,2\n7200,0\n")   # inside the data: an error
    with pytest.raises(ValueError, match="kaputt"):
        rainfall.read_series(path)


def test_a_broken_time_at_the_end_or_a_value_without_a_time_stops_the_import(tmp_path):
    """Only a row that cannot be data is left out; the last, perhaps the peak, is not."""
    path = tmp_path / "rain.csv"
    path.write_text("time,rain\n2024-06-02 12:00,1\n2024-06-02 13:00,2\n"
                    "2024-06-02 25:00,9\n")
    with pytest.raises(ValueError, match="25:00"):
        rainfall.read_series(path)
    path.write_text("time,rain\n2024-06-02 12:00,1\n,2\n2024-06-02 14:00,2\n")
    with pytest.raises(ValueError, match="after '2024-06-02 12:00' has a value"):
        rainfall.read_series(path)
    # two files joined: an empty row and the header of the second inside the data
    path.write_text("time;rain\n2024-06-02 12:00;1\n;\ntime;rain\n"
                    "2024-06-02 13:00;2\n2024-06-02 14:00;2\n")
    series = rainfall.read_series(path)
    assert list(series.intensity_mm_h) == [1.0, 2.0, 2.0]
    assert any("2 rows inside the data" in note for note in series.notes)


@pytest.mark.parametrize("text, origin", [
    ("stn;time;rre150h0\nSMA;202406021200;1\nSMA;202406021300;4\n"   # MeteoSwiss
     "SMA;202406021400;0.5\n", "2024-06-02 11:00"),
    ("Zeit;Regen\n2024060222;1\n2024060223;2\n2024060300;3\n",       # over midnight
     "2024-06-02 21:00"),
    ("time,rain\n2024060212,1\n2024060213,2\n", "2024-06-02 11:00")])
def test_integer_dates_are_read_as_dates_whatever_the_column_is_called(
        tmp_path, text, origin):
    path = tmp_path / "rain.csv"
    path.write_text(text)
    series = rainfall.read_series(path, units="mm")
    assert series.origin == pd.Timestamp(origin)
    assert np.allclose(np.diff(series.end_s), 3600.0)


@pytest.mark.parametrize("values, step", [
    ((1900010100, 1900013700, 1900017300), 3600.0),
    ((2030010100, 2030010700, 2030011300), 600.0),       # these parse as dates as well
    ((1901010107, 1901010703), 596.0)])                  # and a logger's jitter
def test_unix_seconds_are_never_read_as_dates(tmp_path, values, step):
    """From 2030 on, Unix seconds also parse as YYYYMMDDHH; their steps are whole minutes, or days when read as dates, which YYYYMMDDHH never are."""
    path = tmp_path / "logger.csv"
    path.write_text("timestamp,rain\n" + "".join(f"{v},5\n" for v in values))
    series = rainfall.read_series(path)
    assert series.origin is None
    assert list(series.end_s) == [step * (k + 1) for k in range(len(values))]


def test_the_date_cells_of_an_excel_table_are_dates(tmp_path):
    """pandas holds them as dates; read as numbers they were nanoseconds since 1970."""
    from axqua.hydrology import weather

    stamps = pd.to_datetime(["2024-06-01 01:00", "2024-06-01 02:00", "2024-06-01 03:00"])
    pd.DataFrame({"time": stamps, "rain": [1.0, 2.0, 3.0]}).to_excel(
        tmp_path / "rain.xlsx", index=False)
    series = rainfall.read_series(tmp_path / "rain.xlsx", units="mm")
    assert series.origin == pd.Timestamp("2024-06-01 00:00")
    assert list(series.end_s) == [3600.0, 7200.0, 10800.0]
    assert series.total_mm == pytest.approx(6.0)
    pd.DataFrame({"time": stamps, "temperature": [10.0, 11.0, 12.0]}).to_excel(
        tmp_path / "weather.xlsx", index=False)
    meteo = weather.read_weather(tmp_path / "weather.xlsx")
    assert meteo.origin == pd.Timestamp("2024-06-01 00:00")
    assert list(meteo.end_s) == [3600.0, 7200.0, 10800.0]


def test_a_header_repeated_inside_the_data_is_left_out_whatever_its_name(tmp_path):
    path = tmp_path / "joined.csv"
    path.write_text("Zeit (UTC+1),rain\n2024-06-02 12:00,1\nZeit (UTC+1),rain\n"
                    "2024-06-02 13:00,2\n")
    assert list(rainfall.read_series(path).intensity_mm_h) == [1.0, 2.0]


def test_a_value_of_the_dwd_covers_its_own_period_and_a_missing_row_is_a_gap(tmp_path):
    """An hour the file does not have is not spread over the next one; the day before the DWD changed its day in April 2001 ends at 05:30 UTC, the next begins at 05:50."""
    hourly = tmp_path / "produkt_rr_stunde_03379.txt"
    hourly.write_text("STATIONS_ID;MESS_DATUM;QN_8;  R1;RS_IND;WRTR;eor\n"
                      "3379;2024060211;3;1.0;1;6;eor\n3379;2024060213;3;2.0;1;6;eor\n")
    series = rainfall.read_series(hourly)
    assert list(series.intensity_mm_h) == [1.0, 0.0, 2.0]
    assert series.gaps == 1 and series.total_mm == pytest.approx(3.0)
    daily = tmp_path / "produkt_nieder_tag_03379.txt"
    daily.write_text("STATIONS_ID;MESS_DATUM;QN_6;  RS;RSF;SH_TAG;NSH_TAG;eor\n"
                     "3379;20010331;3;24.0;6;0;0;eor\n3379;20010401;3;24.0;6;0;0;eor\n")
    series = rainfall.read_series(daily)
    assert series.origin == pd.Timestamp("2001-03-31 05:30")    # 07:30 summer time
    assert list((series.end_s - series.start_s) / 3600.0) == pytest.approx(
        [24.0, 1 / 3, 24.0])
    assert list(series.intensity_mm_h) == [1.0, 0.0, 1.0] and series.gaps == 1


def test_a_missing_reading_of_the_weather_is_interpolated_and_named(tmp_path):
    """-999 used to reach the evaporation as a temperature and TELEMAC as wind."""
    from axqua.config import load_config
    from axqua.hydrology import weather
    from axqua.hydrology.checks import run_checks

    path = tmp_path / "user-sources" / "produkt_tu_stunde_03379.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("STATIONS_ID;MESS_DATUM;QN_9;TT_TU;RF_TU;eor\n"
                    "3379;2024060211;3;15.0;80.0;eor\n3379;2024060212;3;-999;-999;eor\n"
                    "3379;2024060213;3;17.0;70.0;eor\n")
    meteo = weather.read_weather(path)
    assert list(meteo.values["temperature"]) == [15.0, 16.0, 17.0]
    assert list(meteo.values["relative_humidity"]) == [80.0, 75.0, 70.0]
    assert meteo.gaps == {"temperature": 1, "relative_humidity": 1}
    wind = tmp_path / "user-sources" / "wind.csv"
    wind.write_text("time,wind_speed_10m,wind_direction_10m\nunit,m/s,deg\n"
                    "2024-06-02 13:00,5,350\n2024-06-02 14:00,,-999\n"
                    "2024-06-02 15:00,7,10\n")
    meteo = weather.read_weather(wind)                  # and a units row is no data
    assert list(meteo.values["wind_speed_10m"]) == [5.0, 6.0, 7.0]
    assert meteo.values["wind_direction_10m"][1] == pytest.approx(0.0)  # not 180
    case = _catchment(tmp_path, extra="  weather: user-sources/wind.csv\n"
                                      "  wind: true\n")
    codes = {f.code for f in run_checks(load_config(case))}
    assert "axqua.hydrology.weather_gaps" in codes


def test_the_duration_of_a_steering_file_is_read_as_telemac_reads_it(tmp_path):
    from axqua.solvers.telemac.steering import cas_duration

    cas = tmp_path / "hydrology2d.cas"
    cas.write_text("/ DURATION : 1\nTITLE = 'catchment'\n  DURATION = 7200\n")
    assert cas_duration(cas) == 7200.0
    cas.write_text("DURATION : 3.6D3\n")
    assert cas_duration(cas) == 3600.0
    assert cas_duration(tmp_path / "missing.cas") is None


def test_a_gap_survives_the_file_the_wizard_writes_and_is_named(tmp_path, capsys):
    import json

    from axqua import cli

    case = _catchment(tmp_path)
    record = tmp_path / "gauge.csv"
    record.write_text("time,rain\n2024-06-02 12:00,10\n2024-06-02 13:00,\n"
                      "2024-06-02 14:00,10\n")
    assert cli.main(["hydrology", "rainfall", str(case), "--file", str(record),
                     "--json"]) == 0
    answer = json.loads(capsys.readouterr().out)["data"]
    assert "1 intervals" in answer["caution"]
    written = rainfall.read_series(answer["rainfall"])      # the canonical file
    assert written.gaps == 1 and written.total_mm == pytest.approx(20.0)


# --------------------------------------------------------------------------- #
# a river through the catchment: outflow lines with outlets of their own
# --------------------------------------------------------------------------- #


def _river(root: Path, *, edges: str = "", river: str = "normal_depth",
           case_outlet: str = "overfall", flow: float = 0.2) -> Path:
    """The V-catchment with a river: it enters through an inflow line across the channel at the north edge (``Target flow``) and leaves through its own outflow line across the channel at the south edge; the rest of the south edge is the lower edge of a field."""
    import geopandas as gpd
    from shapely.geometry import LineString

    case = _catchment(root, extra=f"  outlet_condition: {case_outlet}\n")
    lines = [LineString([(X0 - 1, Y0), (X0 + 24, Y0)]),
             LineString([(X0 + 24, Y0), (X0 + 36, Y0)]),
             LineString([(X0 + 36, Y0), (X0 + 61, Y0)]),
             LineString([(X0 + 24, Y0 + 50), (X0 + 36, Y0 + 50)])]
    gpd.GeoDataFrame({"Type (inflow/outflow)": ["outflow", "outflow", "outflow", "inflow"],
                      "Target flow": [None, None, None, flow],
                      "Outlet": [edges, river, edges, ""]}, geometry=lines,
                     crs="EPSG:25832").to_file(root / "user-sources" / "liquid-boundaries.gpkg")
    return case


def _cas_values(cas: str, key: str) -> list[float]:
    text = cas.split(key, 1)[1].split(":", 1)[1].split("\n")[0]
    return [float(v) for v in text.split(";") if v.strip()]


def test_a_river_through_the_catchment_has_an_outlet_of_its_own(tmp_path):
    """The river leaves on its Z(Q) curve, the edges of the field as over a brink: one curve, its own level, and the two field edges below the mesh. A wall node keeps TELEMAC from joining the lines into one boundary."""
    import json

    from axqua.config import load_config
    from axqua.core.selafin import SelafinFile
    from axqua.solvers.telemac import pipeline

    cfg = load_config(_river(tmp_path))
    pipeline.run(cfg, validate_env=False, log_to_file=False)
    model = Path(cfg.model_dir)
    saved = json.loads((model / "liquid-boundaries.json").read_text())
    outflows = [b for b in saved if b["kind"] == "outflow"]
    assert len(outflows) == 3 and [b["condition"] for b in outflows].count("normal_depth") == 1
    assert [b["discharge"] for b in saved if b["kind"] == "inflow"] == [pytest.approx(0.2)]
    cas = (model / "hydrology2d.cas").read_text()
    flags = cas.split("STAGE-DISCHARGE CURVES :", 1)[1].split("\n")[0].strip().split(";")
    assert flags.count("1") == 1 and len(flags) == len(saved)
    curves = (model / cfg.outlet_rating_file).read_text()
    assert curves.count("Q(") == 1
    bed = float(SelafinFile(model / "geometry.slf").frame(0)["BOTTOM"].min())
    levels = dict(zip([b["index"] for b in sorted(saved, key=lambda b: b["index"])],
                      _cas_values(cas, "PRESCRIBED ELEVATIONS")))
    river = next(b["index"] for b in outflows if b["condition"] == "normal_depth")
    edges = [b["index"] for b in outflows if b["index"] != river]
    assert all(levels[i] == pytest.approx(bed - 1.0, abs=1e-3) for i in edges)
    assert levels[river] > bed - 0.5                    # the thalweg of its section
    flows = _cas_values(cas, "PRESCRIBED FLOWRATES")
    assert pytest.approx(0.2) in flows


def test_free_field_edges_beside_a_river_get_their_own_code(tmp_path):
    from axqua.config import load_config
    from axqua.solvers.telemac import pipeline

    cfg = load_config(_river(tmp_path, edges="free", case_outlet="normal_depth"))
    pipeline.run(cfg, validate_env=False, log_to_file=False)
    codes = {" ".join(line.split()[:3]) for line in
             (Path(cfg.model_dir) / "boundaries.cli").read_text().splitlines()}
    assert {"4 4 4", "5 4 4", "4 5 5", "2 2 2"} <= codes


def test_the_checkup_names_a_river_without_an_outlet_and_an_unknown_outlet(tmp_path):
    from axqua.config import load_config
    from axqua.hydrology.checks import run_checks

    def codes(case):
        return {f.code for f in run_checks(load_config(case))}

    assert "axqua.hydrology.river_without_outlet" not in codes(_river(tmp_path / "a"))
    over = _river(tmp_path / "b", river="")                   # all lines overfall
    assert "axqua.hydrology.river_without_outlet" in codes(over)
    dry = _river(tmp_path / "c", river="", flow=0.0)          # no discharge: no river
    assert "axqua.hydrology.river_without_outlet" not in codes(dry)
    wrong = _river(tmp_path / "d", river="weir")
    assert "axqua.hydrology.outlet_unknown" in codes(wrong)


def test_the_wizard_writes_a_copy_of_the_lines_with_the_river(tmp_path, capsys):
    import json

    import geopandas as gpd

    from axqua import cli

    case = _catchment(tmp_path)
    original = (tmp_path / "user-sources" / "liquid-boundaries.gpkg").read_bytes()
    assert cli.main(["hydrology", "lines", str(case), "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)["data"]
    assert [(line["row"], line["kind"], line["outlet"]) for line in listed["lines"]] == \
        [(0, "outflow", "")]
    assert listed["outlet_condition"] == "normal_depth"
    change = json.dumps([{"row": 0, "outlet": "Normal depth"}])
    assert cli.main(["hydrology", "lines", str(case), "--set", change, "--json"]) == 0
    answer = json.loads(capsys.readouterr().out)["data"]
    assert answer["settings"] == {"boundaries.liquid_boundaries":
                                  "user-sources/hydrology/liquid-boundaries.gpkg"}
    assert answer["lines"][0]["outlet"] == "normal_depth"
    copy = gpd.read_file(tmp_path / "user-sources" / "hydrology" / "liquid-boundaries.gpkg")
    assert list(copy["Outlet"]) == ["normal_depth"] and "Target flow" in copy.columns
    # the layer the user drew stays as it is
    assert (tmp_path / "user-sources" / "liquid-boundaries.gpkg").read_bytes() == original
    bad = json.dumps([{"row": 0, "outlet": "weir"}])
    assert cli.main(["hydrology", "lines", str(case), "--set", bad, "--json"]) != 0
    capsys.readouterr()


# --------------------------------------------------------------------------- #
# sediment transport: GAIA coupled into the hydrology run
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("options, model, bedload, suspension", [
    ({"soil": "sand"}, "sand", 7, 3),
    ({"soil": "texture", "texture": "loamy sand"}, "sand", 7, 3),
    ({"soil": "clay"}, "clay", None, None),
    ({"soil": "texture", "texture": "silty clay"}, "clay", None, None),
    ({"soil": "texture", "texture": "silt loam"}, "mixed", 7, 3),
    ({"soil": "shares", "shares": (40, 40, 20)}, "mixed", 7, 3),
    ({"soil": "grain", "d50": 0.005}, "gravel", 1, None),
    ({"soil": "grain", "d50": 0.00003}, "clay", None, None),
    ({"soil": "grain", "d50": 0.001}, "sand", 7, None),       # too coarse to suspend
])
def test_the_sediment_wizard_recommends_after_the_soil(options, model, bedload,
                                                       suspension):
    from axqua.hydrology import sediment

    found = sediment.recommend(**options)
    s = found.settings
    assert found.model == model and s["morphodynamics.enabled"] is True
    assert s["morphodynamics.layer_thickness"] == sediment.LAYER_THICKNESS
    assert (s["morphodynamics.bedload_formula"] if s["morphodynamics.bedload"]
            else None) == bedload
    assert s.get("morphodynamics.suspension_formula") == suspension
    types = [c["type"] for c in s["morphodynamics.sediment_classes"]]
    assert sum(c["fraction"] for c in s["morphodynamics.sediment_classes"]) == \
        pytest.approx(1.0)
    if model in ("clay", "mixed"):
        assert "CO" in types and s["morphodynamics.partheniades"] > 0
        assert not s["morphodynamics.equilibrium_inflow"]      # a river brings clear water
    if model == "mixed":
        assert types == ["CO", "NCO"]                           # GAIA: cohesive first
    assert found.reasons


def test_the_slope_switches_the_slope_effect_and_warns_on_steep_terrain():
    from axqua.hydrology import sediment

    flat = sediment.recommend(soil="sand", slope=0.01)
    hill = sediment.recommend(soil="sand", slope=0.08)
    cliff = sediment.recommend(soil="sand", slope=0.35)
    assert not flat.settings["morphodynamics.slope_effect"]
    assert hill.settings["morphodynamics.slope_effect"] and not hill.warnings
    assert any("outside their range" in w for w in cliff.warnings)
    with pytest.raises(ValueError, match="texture class"):
        sediment.recommend(soil="texture", texture="peat")
    with pytest.raises(ValueError, match="thickness"):
        sediment.recommend(soil="sand", layer_thickness=0.0)


def _with_sediment(case: Path, *extra: str) -> Path:
    """The case with the settings the sediment wizard writes for *extra* (CLI options)."""
    import json

    import yaml

    from axqua import cli

    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert cli.main(["hydrology", "sediment", str(case), *extra, "--json"]) == 0
    settings = json.loads(buffer.getvalue())["data"]["settings"]
    data = yaml.safe_load(case.read_text())
    for key, value in settings.items():
        block, name = key.split(".", 1)
        data.setdefault(block, {})
        if value is not None:
            data[block][name] = value
    case.write_text(yaml.safe_dump(data, sort_keys=False))
    return case


def test_gaia_is_coupled_into_the_hydrology_run(tmp_path):
    """What the first real runs needed: no COUPLING PERIOD FOR GAIA (TELEMAC v9.1.1 has none), clean rain for every suspended class, the bed in the start file, and tracer codes 5/4 at the inflow and outflow lines so suspended sediment can leave."""
    from axqua.config import load_config
    from axqua.core.selafin import read_slf
    from axqua.solvers.telemac import pipeline

    case = _with_sediment(_river(tmp_path), "--soil", "texture", "--texture", "silt loam")
    cfg = load_config(case)
    assert cfg.morphodynamics.enabled and cfg.morphodynamics.suspended_classes == 2
    pipeline.run(cfg, validate_env=False, log_to_file=False)
    model = Path(cfg.model_dir)
    cas = (model / "hydrology2d.cas").read_text()
    assert "COUPLING WITH : 'GAIA'" in cas and "GAIA STEERING FILE : gaia.cas" in cas
    assert "COUPLING PERIOD FOR GAIA" not in cas
    assert "VALUES OF TRACERS IN THE RAIN : 0.;0." in cas
    assert ",T*" in cas.split("VARIABLES FOR GRAPHIC PRINTOUTS", 1)[1][:200]
    gaia = (model / "gaia.cas").read_text()
    for line in ("CLASSES TYPE OF SEDIMENT : 'CO';'NCO'",
                 "CLASSES INITIAL FRACTION : 0.8;0.2",
                 "BED-LOAD TRANSPORT FORMULA FOR ALL SANDS : 7",
                 "SUSPENSION TRANSPORT FORMULA FOR ALL SANDS : 3",
                 "LAYERS INITIAL THICKNESS : 0.3",
                 "LAYERS PARTHENIADES CONSTANT : 0.001",
                 "LAYERS MUD CONCENTRATION : 1300"):
        assert line in gaia
    assert "EQUILIBRIUM INFLOW CONCENTRATION" not in gaia
    codes = {(r.split()[0], r.split()[7]) for r in
             (model / "boundaries.cli").read_text().splitlines()}
    assert ("4", "5") in codes and ("5", "4") in codes and ("2", "2") in codes
    start = read_slf(model / "initial-conditions.slf")
    assert "BOTTOM" in start["values"]


def test_without_suspension_the_files_stay_as_they_were(tmp_path):
    """No tracers, no new codes: a case without GAIA writes its .cli and start file as before."""
    from axqua.config import load_config
    from axqua.core.selafin import read_slf
    from axqua.solvers.telemac import pipeline

    cfg = load_config(_river(tmp_path))
    pipeline.run(cfg, validate_env=False, log_to_file=False)
    model = Path(cfg.model_dir)
    tracer = {r.split()[7] for r in (model / "boundaries.cli").read_text().splitlines()}
    assert tracer == {"2"}
    assert "BOTTOM" not in read_slf(model / "initial-conditions.slf")["values"]
    assert "VALUES OF TRACERS IN THE RAIN" not in (model / "hydrology2d.cas").read_text()


def test_the_checkup_asks_for_sediment_transport_until_the_case_decides(tmp_path):
    from axqua.config import load_config
    from axqua.hydrology.checks import run_checks

    case = _catchment(tmp_path)
    codes = {f.code for f in run_checks(load_config(case))}
    assert "axqua.hydrology.sediment_undecided" in codes
    case.write_text(case.read_text() + "morphodynamics:\n  enabled: false\n")
    codes = {f.code for f in run_checks(load_config(case))}
    assert "axqua.hydrology.sediment_undecided" not in codes
    coarse = case.read_text().replace(
        "morphodynamics:\n  enabled: false\n",
        "morphodynamics:\n  enabled: true\n  bedload_formula: 7\n  sediment_classes:\n"
        "  - {diameter: 0.01, density: 2650}\n")
    case.write_text(coarse)
    codes = {f.code for f in run_checks(load_config(case))}
    assert "axqua.hydrology.sediment_out_of_range" in codes


LISTING = """
                    GAIA MASS-BALANCE OF SEDIMENTS OVER ALL CLASSES:
     CUMULATED BED EVOLUTIONS                  =   -0.5213106E-02  ( KG )
     BOUNDARIES BEDLOAD FLUX                   =     0.000000      ( KG/S  >0 = ENTERING )
     BEDLOAD FLUX BOUNDARY    1                =   -0.010000      ( KG/S  >0 = ENTERING )
     BEDLOAD FLUX BOUNDARY    2                =    0.020000      ( KG/S  >0 = ENTERING )
     EROSION FLUX                              =    0.7020082E-01  ( KG/S )

     RELATIVE ERROR IN VOLUME AT T =        100.0     S :    0.1705163E-15

                     BALANCE OF TRACER COH SEDIMENT1   g/l

     QUANTITY OF TRACER:    3.000000     TRACER UNIT M3
     QUANTITY CREATED BY SOURCE TERM:      0.6574889E-02

     RELATIVE ERROR CUMULATED ON TRACER:     14.47239
     QUANTITY ENTERED THROUGH LIQ. BND.:   -2.000000      ( IF <0 EXIT )
     QUANTITY CREATED BY SOURCE TERM   :     50.00000
                   FINAL BALANCE OF TRACER  1

     INITIAL QUANTITY OF TRACER        :     0.000000
     FINAL QUANTITY                    :     3.000000
"""


def test_the_sediment_budget_is_read_from_the_listing(tmp_path):
    """The suspended load from TELEMAC's cumulated tracer balance (whose "lost" mass is the deposited mass), the bed load per boundary from GAIA's printouts."""
    from axqua.hydrology import report

    listing = tmp_path / "run.sortie"
    listing.write_text(LISTING)
    found = report.read_sediment(listing)
    budget = report.sediment_budget(found)
    item = budget["classes"]["COH SEDIMENT1"]
    assert item["eroded"] == pytest.approx(50.0)
    assert item["deposited"] == pytest.approx(45.0)        # 50 - 2 out - 3 left
    assert budget["suspended_out_kg"] == pytest.approx(2.0)
    assert budget["bedload_kg"] == pytest.approx([-0.5, 1.0])   # 0 at t = 0, trapezoid
    assert budget["out_kg"] == pytest.approx(2.0 - 0.5)
    plain = tmp_path / "plain.sortie"
    plain.write_text("     RELATIVE ERROR IN VOLUME AT T =  10.0 S :  0.0\n")
    assert report.read_sediment(plain) is None


def test_numbers_as_fortran_prints_them_are_read():
    """An exponent of three digits drops the E (found in the listing of the example)."""
    from axqua.hydrology.report import fortran_float

    assert fortran_float("0.6076331-116") == pytest.approx(6.076331e-117, rel=1e-12)
    assert fortran_float("1.5D-03") == pytest.approx(0.0015)
    assert fortran_float("-0.1E+03") == -100.0
    with pytest.raises(ValueError):
        fortran_float("NaNx")


def test_review_fixes_clay_fractions_and_an_empty_discharge(tmp_path):
    """A cohesive-only GAIA file has SKIN FRICTION CORRECTION 0 (GAIA stops otherwise), fractions sum to 1 exactly, and an empty Target flow cell is no discharge."""
    from axqua.config import load_config
    from axqua.core.boundaries import liquid_line_details
    from axqua.solvers.telemac import steering

    assert abs(sum(float(v) for v in steering._fractions(
        [{"fraction": 0.33335}, {"fraction": 0.66665}])) - 1.0) < 1e-8
    case = _with_sediment(_catchment(tmp_path), "--soil", "clay")
    cfg = load_config(case)
    Path(cfg.model_dir).mkdir(parents=True, exist_ok=True)
    gaia = steering.write_gaia_cas(cfg).read_text()
    assert "SKIN FRICTION CORRECTION : 0" in gaia
    river = _river(tmp_path / "r", flow=float("nan"))
    details = liquid_line_details(load_config(river))
    assert all(d["discharge"] is None for d in details if d["kind"] == "inflow")


def test_the_bed_load_of_a_single_class_is_read_from_its_own_block(tmp_path):
    """GAIA prints the block over all classes only for more than one class (mass_balance.f); a sand or gravel soil has one."""
    from axqua.hydrology import report

    listing = tmp_path / "one.sortie"
    listing.write_text("""
                    GAIA MASS-BALANCE OF SEDIMENTS PER CLASS:
     SEDIMENT CLASS NUMBER                     =        1
     BEDLOAD FLUX BOUNDARY    1                =   -0.020000      ( KG/S  >0 = ENTERING )

     RELATIVE ERROR IN VOLUME AT T =        100.0     S :    0.1E-15
""")
    found = report.read_sediment(listing)
    assert found is not None                       # a gravel run has no tracers
    assert report.sediment_budget(found)["bedload_kg"] == pytest.approx([-1.0])


def test_the_river_tab_reads_a_text_discharge_column(tmp_path, capsys):
    """A Target flow field of type text, as QGIS writes one: "1,6" is 1.6, "" and "-" are none."""
    import json

    import geopandas as gpd
    from shapely.geometry import LineString

    from axqua import cli

    case = _catchment(tmp_path)
    lines = [LineString([(X0 + 24, Y0), (X0 + 36, Y0)]),
             LineString([(X0 + 24, Y0 + 50), (X0 + 36, Y0 + 50)]),
             LineString([(X0, Y0 + 20), (X0, Y0 + 30)])]
    gpd.GeoDataFrame({"Type (inflow/outflow)": ["outflow", "inflow", "inflow"],
                      "Target flow": ["-", "1,6", ""]}, geometry=lines,
                     crs="EPSG:25832").to_file(tmp_path / "user-sources" / "liquid-boundaries.gpkg")
    change = json.dumps([{"row": 0, "outlet": "normal_depth"}])
    assert cli.main(["hydrology", "lines", str(case), "--set", change, "--json"]) == 0
    answer = json.loads(capsys.readouterr().out)["data"]
    assert [line["discharge"] for line in answer["lines"]] == [None, pytest.approx(1.6), None]


def test_a_sand_after_a_loam_leaves_no_cohesive_settings(tmp_path):
    from axqua.config import load_config

    case = _with_sediment(_catchment(tmp_path), "--soil", "texture", "--texture", "loam")
    assert load_config(case).morphodynamics.partheniades is not None
    import yaml

    from axqua.hydrology import sediment

    data = yaml.safe_load(case.read_text())
    for key, value in sediment.recommend(soil="sand").settings.items():
        name = key.split(".", 1)[1]
        if value is None:
            data["morphodynamics"].pop(name, None)       # as the wizard's Save does
        else:
            data["morphodynamics"][name] = value
    case.write_text(yaml.safe_dump(data, sort_keys=False))
    m = load_config(case).morphodynamics
    assert m.partheniades is None and m.mud_concentration is None
    assert m.mud_erosion_shear is None


def test_second_review_tracer_order_fractions_keyword_and_text_column(tmp_path):
    """Tracers in class order, each kind numbered on its own (lecdon_telemac2d_gaia.f); fractions that are no composition are refused; a case's own SKIN FRICTION CORRECTION is not written twice; a text discharge column is numbers for every reader."""
    import geopandas as gpd
    from shapely.geometry import LineString

    from axqua.config import Morphodynamics, load_config
    from axqua.core.geodata import dataset
    from axqua.solvers.telemac import steering

    m = Morphodynamics(enabled=True, suspended_load=True, sediment_classes=[
        {"type": "NCO"}, {"type": "CO"}, {"type": "NCO"}])
    assert m.tracer_names == ["NCOH SEDIMENT1", "COH SEDIMENT1", "NCOH SEDIMENT2"]
    m.suspended_load = False
    assert m.tracer_names == ["COH SEDIMENT1"]

    shares = steering._fractions([{"fraction": 0.4999995}, {"fraction": 0.5000005},
                                  {"fraction": 0.0}])
    assert all(float(v) >= 0 for v in shares)
    assert abs(sum(float(v) for v in shares) - 1.0) < 1e-8
    with pytest.raises(ValueError, match="fractions"):
        steering._fractions([{"fraction": 0}, {"fraction": 0}])

    case = _with_sediment(_catchment(tmp_path), "--soil", "clay")
    cfg = load_config(case)
    cfg.morphodynamics.extra_keywords = {"SKIN FRICTION CORRECTION": 0}
    Path(cfg.model_dir).mkdir(parents=True, exist_ok=True)
    assert steering.write_gaia_cas(cfg).read_text().count("SKIN FRICTION CORRECTION") == 1

    gpd.GeoDataFrame({"Type (inflow/outflow)": ["outflow", "inflow"],
                      "Target flow": ["", "0,065"]},
                     geometry=[LineString([(X0 + 24, Y0), (X0 + 36, Y0)]),
                               LineString([(X0 + 24, Y0 + 50), (X0 + 36, Y0 + 50)])],
                     crs="EPSG:25832").to_file(tmp_path / "user-sources" / "liquid-boundaries.gpkg")
    layer = dataset(load_config(case)).liquid_boundaries()
    assert layer["Target flow"].dtype == "float64"
    assert layer["Target flow"].iloc[1] == pytest.approx(0.065)


def test_third_review_gaia_lines_fit_damocles_and_a_case_keyword_wins(tmp_path):
    """DAMOCLES reads 72 columns: longer value lists go on lines of their own. A class of fraction 0 stays 0. Any keyword the case names in extra_keywords is written once, as the case gives it."""
    from axqua.config import load_config
    from axqua.solvers.telemac import steering

    assert steering._fractions([{"fraction": 0.1}, {"fraction": 0.2}, {"fraction": 0.0}])[-1] == "0"
    lines = steering._keyword("CLASSES INITIAL FRACTION",
                              steering._fractions([{"fraction": f} for f in (0.1, 0.2, 0.3, 0.3)]))
    assert len(lines) > 1 and all(len(line) <= 72 for line in lines)
    assert sum(float(v) for line in lines[1:] for v in line.strip(";").split(";")) \
        == pytest.approx(1.0, abs=1e-8)
    assert steering._keyword("KEY", ["1", "2"]) == ["KEY : 1;2"]          # fits: as before

    case = _with_sediment(_catchment(tmp_path), "--soil", "texture", "--texture", "loam")
    cfg = load_config(case)
    cfg.morphodynamics.extra_keywords = {"MORPHOLOGICAL FACTOR": 5,
                                         "CLASSES INITIAL FRACTION": "0.5;0.5"}
    Path(cfg.model_dir).mkdir(parents=True, exist_ok=True)
    gaia = steering.write_gaia_cas(cfg).read_text()
    assert gaia.count("MORPHOLOGICAL FACTOR") == 1 and "MORPHOLOGICAL FACTOR : 5" in gaia
    assert gaia.count("CLASSES INITIAL FRACTION") == 1 and "0.5;0.5" in gaia
    assert all(len(line) <= 72 for line in gaia.splitlines() if not line.startswith("/"))


def test_the_case_check_names_fractions_that_are_no_composition(tmp_path):
    from axqua.config import load_config
    from axqua.core.casecheck import check_case as check

    case = _catchment(tmp_path)
    case.write_text(case.read_text() + "morphodynamics:\n  enabled: true\n"
                    "  sediment_classes:\n  - {diameter: 0.0001, fraction: 0}\n"
                    "  - {diameter: 0.001, fraction: 0}\n")
    with pytest.raises(ValueError, match="fractions"):
        load_config(case).validate()
    found = check(case)
    assert any("fractions" in f.message and f.subject.startswith("morphodynamics")
               for f in found), [f.as_dict() for f in found]


def test_fourth_review_every_steering_line_fits_and_continued_keywords_are_read(tmp_path):
    """A continued line ends with ';' inside the 72 columns. Every multi-value keyword of GAIA (per layer, solid discharges, a case's own) and of TELEMAC is fitted; the readers and the patcher of aXqua take a keyword over all its lines."""
    from axqua import bayescal
    from axqua.config import load_config
    from axqua.solvers.telemac import steering

    for width in range(1, 30):
        values = ["9" * width] * 11
        lines = steering._keyword("CLASSES SEDIMENT DIAMETERS", values)
        assert all(len(line) <= 72 for line in lines), (width, lines)
        assert ";".join(line.split(":", 1)[-1].strip() for line in lines).replace(";;", ";") \
            .strip(";").split(";") == values
    lines = steering._keyword("CLASSES SEDIMENT DIAMETERS",
                              ["0.1234567891"] * 5 + ["0.00015", "0.002"])
    assert all(len(line) <= 72 for line in lines)

    case = _with_sediment(_catchment(tmp_path), "--soil", "clay")
    cfg = load_config(case)
    m = cfg.morphodynamics
    m.bed_layers, m.mud_erosion_shear = 4, 0.125
    m.prescribed_solid_discharges = [0.00125] * 6
    m.extra_keywords = {"CLASSES HIDING FACTOR": ";".join(["1.2345678"] * 10)}
    Path(cfg.model_dir).mkdir(parents=True, exist_ok=True)
    path = steering.write_gaia_cas(cfg)
    assert all(len(line) <= 72 for line in path.read_text().splitlines())
    read = steering.read_cas_values(path, "LAYERS CRITICAL EROSION SHEAR STRESS OF THE MUD",
                                    "PRESCRIBED SOLID DISCHARGES", "CLASSES HIDING FACTOR")
    assert read["LAYERS CRITICAL EROSION SHEAR STRESS OF THE MUD"].split(";") == ["0.125"] * 4
    assert read["PRESCRIBED SOLID DISCHARGES"].split(";") == ["0.00125"] * 6
    assert read["CLASSES HIDING FACTOR"].split(";") == ["1.2345678"] * 10
    assert {"PRESCRIBED SOLID DISCHARGES", "CLASSES HIDING FACTOR"} <= \
        steering.continued_keywords(path)

    # TELEMAC: a long PRESCRIBED ELEVATIONS, patched by the validation and calibration route
    text = "TITLE : 'x'\nPRESCRIBED ELEVATIONS :\n0.;0.;432.1234;432.1234;432.1234;432.1234;\n" \
           "432.1234;432.1234\nDURATION : 10\n&ETA\n"
    cas = tmp_path / "long.cas"
    cas.write_text(text)
    assert steering.read_cas_values(cas, "PRESCRIBED ELEVATIONS")["PRESCRIBED ELEVATIONS"] \
        .split(";") == ["0.", "0."] + ["432.1234"] * 6
    patched = bayescal._patch_cas(text, "PRESCRIBED ELEVATIONS", ";".join(["1.5"] * 3))
    assert "PRESCRIBED ELEVATIONS : 1.5;1.5;1.5\nDURATION : 10" in patched
    longer = bayescal._patch_cas(text, "PRESCRIBED ELEVATIONS", ";".join(["432.12345"] * 9))
    assert all(len(line) <= 72 for line in longer.splitlines())
    assert "432.1234;432.1234\n" not in longer                       # the old values are gone
    assert steering.fit_line("VARIABLES FOR GRAPHIC PRINTOUTS : '" + ",".join("UVSBHMQFKE" * 4)
                             + "'")[0] == "VARIABLES FOR GRAPHIC PRINTOUTS :"


def test_fourth_review_a_calibration_refuses_a_keyword_written_on_several_lines(tmp_path):
    """HydroBayesCal rewrites the first line of a keyword only, which would leave the old values on the next ones."""
    from axqua.calibration import _refuse_continued_keywords
    from axqua.config import load_config

    case = _with_sediment(_catchment(tmp_path), "--soil", "clay")
    cfg = load_config(case)
    Path(cfg.model_dir).mkdir(parents=True, exist_ok=True)
    cfg.model_path(cfg.gaia_cas).write_text(
        "CLASSES SHIELDS PARAMETERS :\n0.047;0.047;\n0.047\nMPM COEFFICIENT : 8\n")
    cfg.model_path(cfg.cas_file).write_text("TITLE : 'x'\n")
    _refuse_continued_keywords(cfg, ["zone1", "gaiaMPM COEFFICIENT"], cfg.cas_file)
    with pytest.raises(ValueError, match="CLASSES SHIELDS PARAMETERS 2"):
        _refuse_continued_keywords(cfg, ["gaiaCLASSES SHIELDS PARAMETERS 2"], cfg.cas_file)


def test_fourth_review_fractions_types_layer_cache_and_discharges(tmp_path):
    """The largest class takes the rounding remainder, so zero stays zero wherever it is. A wrong sediment_type is named also without classes. set_layer replaces what every reader of the boundary layer sees. 'nan' is no discharge. A layer of 'Target flow' alone has no type column."""
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import LineString

    from axqua.config import Morphodynamics, load_config
    from axqua.core.boundaries import _type_column, parse_discharge
    from axqua.core.geodata import dataset
    from axqua.hydrology import river
    from axqua.solvers.telemac import steering

    assert steering._fractions([{"fraction": 1}] * 3 + [{"fraction": 0}])[-1] == "0"
    with pytest.raises(ValueError, match="sediment_type"):
        Morphodynamics(enabled=True, sediment_type="SAND").validate()

    for text in ("nan", "NaN", np.float32("nan"), float("inf"), " ", "-"):
        assert parse_discharge(text) is None, text
    assert parse_discharge(np.float32(1.5)) == pytest.approx(1.5)

    case = _catchment(tmp_path)
    cfg = load_config(case)
    data = dataset(cfg)
    first = data.liquid_boundaries()
    edited = first.iloc[:1].copy()
    data.set_layer("liquid_boundaries", edited)
    assert len(data.liquid_boundaries()) == 1

    layer = gpd.GeoDataFrame({"Target flow": ["1,6"]},
                             geometry=[LineString([(X0 + 24, Y0), (X0 + 36, Y0)])],
                             crs="EPSG:25832")
    assert _type_column(layer) is None
    path = tmp_path / "user-sources" / "liquid-boundaries.gpkg"
    layer.to_file(path)
    cfg = load_config(case)
    assert river.lines(cfg)[0]["discharge"] == pytest.approx(1.6)
    written = gpd.read_file(river.write_lines(cfg, [{"row": 0, "discharge": 2.0}], tmp_path / "out"))
    assert written["Target flow"].iloc[0] == pytest.approx(2.0)
    assert written[river.TYPE_COLUMN].iloc[0] == "inflow"


def test_a_river_inflow_refuses_the_sediment_setups_telemac_cannot_run(tmp_path):
    """Measured with TELEMAC v9.1.1 on the test catchment with a river (2026-11-01): bed load without a suspended class crashes GAIA; suspended sand above 0.15 mm stops with a cohesive class (also at equilibrium) or with clear water; above 0.5 mm also at equilibrium. Without a river every soil runs, so nothing is refused."""
    import geopandas as gpd
    from shapely.geometry import LineString

    from axqua.config import Morphodynamics, load_config
    from axqua.hydrology import sediment
    from axqua.hydrology.checks import run_checks

    def m(classes, **kw):
        return Morphodynamics(**{"enabled": True, "sediment_classes": classes, **kw})

    co = {"diameter": 2e-5, "type": "CO", "fraction": 0.5}
    def nco(d):
        return {"diameter": d, "type": "NCO", "fraction": 0.5}
    runs = [m([co, nco(1.5e-4)], bedload=True, suspended_load=True),            # loam
            m([co], bedload=False, suspended_load=True),                         # clay
            m([nco(1e-4)], bedload=True, suspended_load=True),
            m([nco(2.5e-4)], bedload=True, suspended_load=True, equilibrium_inflow=True),
            m([nco(5e-4)], bedload=True, suspended_load=True, equilibrium_inflow=True),
            m([nco(2.5e-4)], enabled=False)]
    stops = [m([nco(5e-3)], bedload=True, suspended_load=False),                 # gravel
             m([nco(1e-4)], bedload=True, suspended_load=False),
             m([co, nco(4e-4)], bedload=True, suspended_load=True, equilibrium_inflow=True),
             m([nco(2.5e-4)], bedload=True, suspended_load=True),
             m([nco(1e-3)], bedload=True, suspended_load=True, equilibrium_inflow=True)]
    for setup in runs:
        assert sediment.river_conflicts(setup) == [], setup
    for setup in stops:
        assert sediment.river_conflicts(setup), setup

    case = _with_sediment(_catchment(tmp_path), "--soil", "grain", "--d50", "5")
    assert not any(f.code == "axqua.hydrology.sediment_with_river"
                   for f in run_checks(load_config(case)))                  # no river: runs
    gpd.GeoDataFrame({"Type (inflow/outflow)": ["outflow", "inflow"],
                      "Target flow": [None, 0.2]},
                     geometry=[LineString([(X0 + 24, Y0), (X0 + 36, Y0)]),
                               LineString([(X0 + 24, Y0 + 50), (X0 + 36, Y0 + 50)])],
                     crs="EPSG:25832").to_file(tmp_path / "user-sources" / "liquid-boundaries.gpkg")
    cfg = load_config(case)
    found = [f for f in run_checks(cfg) if f.code == "axqua.hydrology.sediment_with_river"]
    assert found and found[0].severity == "error" and "crash" in found[0].message
    answer = sediment.recommend_for_case(cfg, soil="grain", d50=0.005, slope=0.05)
    assert any("river inflow" in w for w in answer.warnings)
    loam = sediment.recommend_for_case(cfg, soil="texture", texture="loam", slope=0.05)
    assert not any("river inflow" in w for w in loam.warnings)
