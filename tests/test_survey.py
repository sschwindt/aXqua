"""The survey planner, module by module, on a small channel that needs no file.

The channel is the one of ``survey_case``: 200 m by 60 m, flowing in the direction of x, 0.9 m deep in the middle, with a pool of 1.6 m between x = 140 m and x = 170 m that cannot be waded. Zone 1 is the upstream half, zone 2 the downstream half.
"""

from __future__ import annotations

import datetime
import json
import types

import numpy as np
import pytest
import survey_case

from axqua.survey import conditions, placement, schedule, walk
from axqua.survey.drone import footprint_size, plan_airborne
from axqua.survey.field import FlowField, SurveyError
from axqua.survey.ground import plan_ground
from axqua.survey.kayak import fast_patches, plan_kayak
from axqua.survey.settings import KINDS, GroundSettings, ParameterNeed, SurveySettings, TimeSettings, clock_minutes
from axqua.survey.temperature import CLASSES, _deal, class_patches, classify, plan_temperature

X0, Y0 = survey_case.X0, survey_case.Y0
ACCESS, LEAVE = survey_case.ACCESS, survey_case.LEAVE


def channel_flow() -> FlowField:
    x, y, ikle, depth, u, bed = survey_case.channel()
    nx = int(round(survey_case.WIDTH / survey_case.STEP)) + 1
    column = np.arange(x.size) % nx
    return FlowField(x=x, y=y, triangles=ikle - 1, depth=depth, u=u, v=np.zeros_like(u), bed=bed, hops=np.minimum(column, nx - 1 - column), zone=np.where(x < X0 + survey_case.WIDTH / 2, 1, 2), source="r2d.slf")


@pytest.fixture(scope="module")
def flow() -> FlowField:
    return channel_flow()


def zone_needs(points: int = 6) -> list[ParameterNeed]:
    return [ParameterNeed("zone1", "zone", 1, points), ParameterNeed("zone2", "zone", 2, points)]


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #


def test_the_defaults_are_the_numbers_the_user_gave():
    g = GroundSettings()
    assert g.max_depth == 1.2 and g.velocity_limits == [[0.6, 2.0], [0.9, 1.4], [1.2, 1.0]]
    assert (g.speed_land, g.speed_with_current) == (3.0, 1.5) and g.speed_against_current == [[0.3, 1.0], [0.5, 0.5], [0.8, 0.1]]
    assert (g.time_per_depth, g.time_probe_height, g.time_water_depth) == (60.0, 40.0, 60.0)
    assert g.pressure_interval_h == 4.0 and (g.surveyors, g.surveyors_per_dgps) == (1, 3)
    s = SurveySettings()
    assert s.kinds == ["ground"] and s.time.day_hours == 8.0 and s.time.start == ""
    assert (s.time.short_break_minutes, s.time.short_break_every_h, s.time.long_break_minutes, s.time.long_break_every_h) == (10.0, 2.0, 30.0, 4.0)
    assert (s.kayak.kayakers, s.kayak.log_interval, s.kayak.max_velocity, s.kayak.break_every_h, s.kayak.break_minutes) == (2, 5.0, 3.0, 2.0, 10.0)
    assert s.temperature.method == "loggers"


def test_a_vertical_takes_its_depths_the_probe_adjustments_and_the_water_depth():
    g = GroundSettings()
    assert [g.depths_at(d) for d in (0.1, 0.29, 0.3, 0.49, 0.5, 1.1)] == [1, 1, 2, 2, 3, 3]
    assert [g.vertical_seconds(n) for n in (1, 2, 3)] == [160.0, 260.0, 360.0]
    assert g.heights(1) == [0.6] and g.heights(2) == [0.2, 0.8] and g.heights(3) == [0.2, 0.6, 0.8]


@pytest.mark.parametrize("depth, velocity, ok", [
    (0.5, 1.9, True), (0.5, 2.1, False), (0.6, 2.0, True), (0.8, 1.4, True), (0.8, 1.5, False),
    (1.1, 1.0, True), (1.1, 1.1, False), (1.2, 0.5, True), (1.3, 0.1, False),
])
def test_what_can_be_waded(depth, velocity, ok):
    assert bool(GroundSettings().wadeable(depth, velocity)) is ok


def test_settings_survive_json_and_ignore_what_they_do_not_know():
    s = SurveySettings.from_dict({"kinds": ["ground", "kayak"], "ground": {"surveyors": "3", "max_depth": "deep", "velocity_limits": [[0.5, 1.5]]}, "time": {"start": "07:30", "use_daylight": "no"}, "of a later version": 1, "kayak": {"unknown": 2}})
    assert s.kinds == ["ground", "kayak"]
    assert s.ground.surveyors == 3                      # a number written as text
    assert s.ground.max_depth == 1.2                    # text that is no number keeps the default
    assert s.ground.velocity_limits == [[0.5, 1.5]]
    assert s.time.start == "07:30" and s.time.use_daylight is False
    again = SurveySettings.from_dict(json.loads(json.dumps(s.as_dict())))
    assert again.as_dict() == s.as_dict()
    assert SurveySettings.from_dict(None).as_dict() == SurveySettings().as_dict()


def test_settings_say_what_is_wrong_with_them():
    assert SurveySettings().problems() == []
    s = SurveySettings.from_dict({"kinds": ["ground", "boat"], "ground": {"surveyors": 0}, "time": {"date": "20.06.2026", "start": "25:00"}})
    text = "; ".join(s.problems())
    assert "unknown kind of survey: boat" in text
    assert "at least one surveyor" in text
    assert "'20.06.2026' is not of the form YYYY-MM-DD" in text
    assert "'25:00' is not of the form HH:MM" in text
    assert "no kind of survey is chosen" in SurveySettings.from_dict({"kinds": []}).problems()


def test_every_number_a_plan_divides_by_is_checked():
    wrong = SurveySettings.from_dict({"airborne": {"fps": 0, "width_px": 0, "height_px": -1, "cruise_speed": 0, "battery_minutes": 0, "hfov_deg": 180, "overlap": 1.0}, "ground": {"max_depth": 0}})
    text = "; ".join(wrong.problems())
    for part in ("the frame rate of the video", "the width of the video", "the height of the video", "the cruise speed of the drone", "the flight time of a battery", "the largest wading depth"):
        assert f"{part} must be larger than zero" in text
    assert "the field of view of the video lies between 0 and 180 degrees" in text and "the overlap of the video frames lies between 0 and 1" in text


@pytest.mark.parametrize("ground, said", [
    ({"speed_against_current": [0.3, 1.0]}, "the walking speeds against the current are pairs"),
    ({"speed_against_current": [[0.3, "slow"]]}, "the walking speeds against the current are pairs"),
    ({"speed_against_current": [[0.3, 0.0]]}, "the walking speeds against the current must be larger than zero"),
    ({"velocity_limits": [[0.6]]}, "the velocity limits of wading are pairs"),
    ({"velocity_limits": []}, "the velocity limits of wading are pairs"),
    ({"heights_two": [0.2, "x"]}, "the heights of the readings at two depths are numbers between 0 and 1"),
    ({"heights_one": [1.2]}, "the heights of the readings at one depth are numbers between 0 and 1"),
])
def test_a_list_of_the_wrong_shape_is_a_problem_and_no_crash(ground, said):
    assert any(said in text for text in SurveySettings.from_dict({"ground": ground}).problems())


def test_a_clock_time_is_read_in_both_spellings():
    assert clock_minutes("07:30") == 450.0 and clock_minutes("07h30m") == 450.0 and clock_minutes("0:00") == 0.0
    assert clock_minutes("24:00") is None and clock_minutes("half past seven") is None


# --------------------------------------------------------------------------- #
# schedule
# --------------------------------------------------------------------------- #


def test_times_are_written_as_clock_and_duration():
    assert schedule.fmt_clock(0) == "00:00" and schedule.fmt_clock(485.6) == "08:06"
    assert schedule.fmt_duration(35) == "35 min" and schedule.fmt_duration(125) == "2 h 05 min"


def test_a_day_without_a_start_begins_at_midnight_and_lasts_eight_hours():
    window = schedule.day_window(TimeSettings(), [{"date": "2026-06-20", "sunrise": 300.0, "sunset": 400.0}])
    assert (window.start, window.end, window.limit) == (0.0, 480.0, "working hours")


def test_the_sunset_ends_a_day_that_has_a_start():
    sun = [{"date": "2026-06-20", "sunrise": 300.0, "sunset": 1200.0}, {"date": "2026-06-21", "sunrise": 300.0, "sunset": 1100.0}]
    late = TimeSettings(start="14:00")
    window = schedule.day_window(late, sun)
    assert (window.start, window.end, window.limit, window.date) == (840.0, 1200.0, "sunset", "2026-06-20")
    assert schedule.day_window(late, sun, day=1).end == 1100.0
    assert schedule.day_window(late, sun, day=5).end == 1100.0                    # past the forecast: the last day known
    assert schedule.day_window(TimeSettings(start="14:00", use_daylight=False), sun).limit == "working hours"
    assert schedule.day_window(TimeSettings(start="08:00"), sun).end == 960.0     # eight hours end before the sunset


def test_breaks_come_every_two_and_every_four_hours():
    breaks = schedule.Breaks(TimeSettings(), 0.0)
    assert breaks.due(119.0) == (0.0, "")
    assert breaks.due(120.0) == (10.0, "break")
    breaks.taken(130.0, "break")
    assert breaks.due(240.0) == (30.0, "long break")
    breaks.taken(270.0, "long break")
    assert breaks.due(389.0) == (0.0, "") and breaks.due(390.0) == (10.0, "break")
    assert schedule.Breaks(TimeSettings(short_break_minutes=0, long_break_minutes=0), 0.0).due(1000.0) == (0.0, "")


# --------------------------------------------------------------------------- #
# daylight, weather, network coverage
# --------------------------------------------------------------------------- #


def no_internet(*args, **kwargs):
    raise AssertionError("a test reached for the internet")


def test_sunrise_and_sunset_of_munich():
    rise, down = conditions.sun_times(48.14, 11.58, datetime.date(2026, 6, 21), 2.0)
    assert abs(rise - (5 * 60 + 13)) < 5 and abs(down - (21 * 60 + 17)) < 5
    rise, down = conditions.sun_times(48.14, 11.58, datetime.date(2026, 12, 21), 1.0)
    assert abs(rise - (8 * 60 + 1)) < 5 and abs(down - (16 * 60 + 22)) < 5
    assert conditions.sun_times(78.0, 15.0, datetime.date(2026, 6, 21), 2.0) == (None, None)


def test_which_hours_are_no_hours_for_a_survey():
    assert conditions.bad_hour({"code": 3, "precipitation": 0.0, "gusts": 5.0}) == ""
    assert conditions.bad_hour({"code": 95}) == "thunderstorm"
    assert conditions.bad_hour({"code": 3, "gusts": 16.0}) == "storm gusts"
    assert conditions.bad_hour({"code": 61, "precipitation": 2.5}) == "rain"
    assert conditions.bad_hour({"code": 3, "gusts": 11.0}) == "" and conditions.bad_hour({"code": 3, "gusts": 11.0}, drone=True) == "gusts"
    assert conditions.bad_hour({"code": 51, "precipitation": 0.3}) == "" and conditions.bad_hour({"code": 51, "precipitation": 0.3}, drone=True) == "rain"
    assert conditions.bad_hour({}) == ""


def test_the_suggested_window_is_the_longest_run_of_daylight_without_bad_weather():
    day = {"sunrise": 350.0, "sunset": 1210.0, "hours": []}
    assert conditions.suggest_window(day, 8.0) == {"start": "06:00", "end": "14:00", "reason": "from sunrise, within the working hours"}
    day["hours"] = [{"minute": 60.0 * h, "code": 61 if h < 10 else 3, "precipitation": 3.0 if h < 10 else 0.0, "gusts": 4.0} for h in range(24)]
    found = conditions.suggest_window(day, 8.0)
    assert (found["start"], found["end"]) == ("10:00", "18:00") and "without rain" in found["reason"]
    assert conditions.suggest_window(day, 12.0)["end"] == "20:00"                 # the sunset, not the working hours
    day["hours"] = [dict(hour, code=96) for hour in day["hours"]]
    found = conditions.suggest_window(day, 8.0)
    assert found["start"] == "" and "choose another day" in found["reason"]
    assert conditions.suggest_window({"sunrise": None, "sunset": None}, 8.0) == {}


def test_the_twelve_countries_have_a_coverage_source_and_a_correction_service():
    assert set(conditions.COUNTRIES) == {"de", "ch", "fr", "at", "it", "nl", "cz", "dk", "se", "ca", "cn", "us"}
    for code, entry in conditions.COUNTRIES.items():
        assert entry["name"] and entry["mobile"][0] and entry["rtk"], code
        assert code in conditions.RTK_URLS


@pytest.mark.parametrize("lon, lat, code", [(11.58, 48.14, "de"), (7.45, 46.95, "ch"), (2.35, 48.86, "fr"), (16.37, 48.21, "at"), (12.5, 41.9, "it"), (4.9, 52.37, "nl"), (14.42, 50.09, "cz"), (12.57, 55.68, "dk"), (18.07, 59.33, "se"), (-75.7, 45.42, "ca"), (116.4, 39.9, "cn"), (-104.99, 39.74, "us"), (0.0, 0.0, "")])
def test_the_country_of_a_position_without_the_internet(lon, lat, code):
    assert conditions.country_offline(lon, lat) == code


def test_without_the_internet_the_surveyors_are_told_to_check_the_coverage(monkeypatch):
    monkeypatch.setattr(conditions, "_get", no_internet)
    found = conditions.coverage(11.58, 48.14, online=False)
    assert found["country"] == "DE" and found["checked"] is False and found["mast"] is None
    assert found["statement"].startswith("No coverage map could be queried for this position. The surveyors should check the mobile coverage on the river before the survey: Mobilfunk-Monitoring of the Bundesnetzagentur")
    assert "SAPOS" in found["statement"] and "gigabitgrundbuch.bund.de" in found["statement"]
    nowhere = conditions.coverage(0.0, 0.0, online=False)
    assert nowhere["country"] == "" and "The surveyors should check the mobile coverage" in nowhere["statement"]
    border = conditions.coverage(7.59, 47.56, online=False)                       # Basel, where Germany and France begin
    assert border["country"] == "CH" and any("close to a border (Germany, France would also be possible)" in note for note in border["notes"])
    assert not any("close to a border" in note for note in found["notes"])        # Munich


def test_the_outlines_tell_bavaria_from_austria():
    # the Isar in Munich and the Inn at Muehldorf lie inside the bounding box of Austria
    assert conditions.country_offline(11.58, 48.14) == "de" and conditions.country_offline(12.52, 48.24) == "de"
    assert conditions.country_offline(13.04, 47.80) == "at" and conditions.countries_nearby(13.04, 47.80, "at") == ["de"]
    assert conditions.country_offline(4.35, 50.85) == "" and conditions.country_offline(-0.12, 51.5) == ""      # Brussels, London
    from axqua.survey.outlines import OUTLINES

    assert set(OUTLINES) == set(conditions.COUNTRIES)


def test_a_network_that_does_not_answer_is_a_note_and_no_error(monkeypatch):
    def down(*args, **kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr(conditions, "_get", down)
    found = conditions.coverage(11.58, 48.14, online=True)
    assert found["country"] == "DE" and found["checked"] is False
    assert any("the country could not be looked up (no route to host)" in note for note in found["notes"])
    assert any("OpenStreetMap could not be asked" in note for note in found["notes"])
    assert "The surveyors should check the mobile coverage" in found["statement"]


def test_the_swiss_coverage_model_is_quoted_with_its_source(monkeypatch):
    def answer(url, data=None, timeout=None):
        if url.startswith(conditions.NOMINATIM):
            return json.dumps({"address": {"country_code": "CH", "country": "Switzerland"}}).encode()
        if url.startswith(conditions.SWISS_WMS):
            return (b"name = '1'" if "mobilnetz-4g" in url else b"name = '3'")
        return json.dumps({"elements": [{"lat": 46.96, "lon": 7.45, "tags": {"operator": "Swisscom"}}]}).encode()

    monkeypatch.setattr(conditions, "_get", answer)
    found = conditions.coverage(7.45, 46.95, online=True)
    assert found["country"] == "CH" and found["checked"] is True and (found["operators_4g"], found["operators_5g"]) == (1, 3)
    assert "outdoor coverage by 1 operator with 4G and by 3 with 5G" in found["statement"]
    assert "OpenStreetMap shows 1 mobile mast within 5 km, the nearest 1.1 km from the middle of the reach (Swisscom)" in found["statement"]
    assert "should check" not in found["statement"] and "swipos" in found["statement"]
    assert any("OFCOM" in source for source in found["sources"]) and any("OpenStreetMap" in source for source in found["sources"])


def test_numbers_and_their_nouns_agree():
    assert conditions._many(1, "operator") == "1 operator" and conditions._many(0, "operator") == "0 operators" and conditions._many(3, "mobile mast") == "3 mobile masts"


def test_a_day_without_the_internet_has_its_sun_and_no_weather(monkeypatch, flow):
    monkeypatch.setattr(conditions, "_get", no_internet)
    cfg = types.SimpleNamespace(crs_epsg=25832)
    settings = SurveySettings.from_dict({"time": {"date": "2026-06-20"}})
    found = conditions.look_up(cfg, flow, settings, online=False)
    assert abs(found["lon"] - 11.69) < 0.05 and abs(found["lat"] - 48.18) < 0.05
    assert found["weather"] is False and len(found["days"]) == 7 and found["days"][0]["date"] == "2026-06-20"
    assert found["days"][0]["sunrise_text"] and found["days"][0]["sunset_text"] and found["suggestion"]["start"]
    assert any("switched off" in note for note in found["notes"])
    assert any("computed by aXqua" in source for source in found["sources"])
    assert [day["date"] for day in conditions.sun_of(found)][:2] == ["2026-06-20", "2026-06-21"]
    none = conditions.look_up(cfg, flow, SurveySettings(), online=False)
    assert none["days"] == [] and any("no survey day is set" in note for note in none["notes"]) and conditions.sun_of(none) == []


def test_a_forecast_is_read_and_one_that_fails_is_a_note(monkeypatch, flow):
    cfg = types.SimpleNamespace(crs_epsg=25832)
    day = datetime.date.today() + datetime.timedelta(days=3)
    settings = SurveySettings.from_dict({"kinds": ["airborne"], "time": {"date": day.isoformat()}})

    def down(*args, **kwargs):
        raise OSError("timed out")

    monkeypatch.setattr(conditions, "_get", down)
    found = conditions.look_up(cfg, flow, settings, online=True)
    assert found["weather"] is False and len(found["days"]) == 7
    assert any("the weather forecast could not be fetched (timed out)" in note for note in found["notes"])

    stamp = day.isoformat()
    forecast = {
        "timezone": "Europe/Berlin", "utc_offset_seconds": 7200,
        "daily": {"time": [stamp], "sunrise": [f"{stamp}T05:12"], "sunset": [f"{stamp}T21:15"], "weather_code": [61], "temperature_2m_max": [21.0], "temperature_2m_min": [11.0], "precipitation_sum": [4.0], "precipitation_probability_max": [80], "wind_speed_10m_max": [6.0], "wind_gusts_10m_max": [12.0]},
        "hourly": {"time": [f"{stamp}T{h:02d}:00" for h in range(24)], "temperature_2m": [15.0] * 24, "precipitation": [0.5 if h < 9 else 0.0 for h in range(24)], "precipitation_probability": [50] * 24, "weather_code": [3] * 24, "wind_speed_10m": [3.0] * 24, "wind_gusts_10m": [5.0] * 24},
    }

    def answer(url, data=None, timeout=None):
        if url.startswith(conditions.OPEN_METEO):
            return json.dumps(forecast).encode()
        raise OSError("timed out")

    monkeypatch.setattr(conditions, "_get", answer)
    found = conditions.look_up(cfg, flow, settings, online=True)
    assert found["weather"] is True and found["timezone"] == "Europe/Berlin (UTC+2 h)"
    first = found["days"][0]
    assert (first["sunrise"], first["sunset"], first["weather"], first["sunrise_text"]) == (312.0, 1275.0, "slight rain", "05:12")
    assert len(first["hours"]) == 24
    # a drone does not fly in the light rain of the morning
    assert found["suggestion"]["start"] == "09:00" and "without rain" in found["suggestion"]["reason"]
    assert any("Open-Meteo" in source for source in found["sources"])


# --------------------------------------------------------------------------- #
# where the measurements are placed
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name, rule, zone", [
    ("zone4", "zone", 4), ("ZONE12", "zone", 12), ("zone 4", "everywhere", None), ("VELOCITY DIFFUSIVITY", "shear", None), ("Cmu", "shear", None),
    ("ROUGHNESS COEFFICIENT OF BOUNDARIES", "banks", None), ("SECONDARY CURRENTS PRODUCTION COEFFICIENT", "bends", None),
    ("THRESHOLD VALUE OF DEPTH", "margins", None), ("LAW OF BOTTOM FRICTION", "everywhere", None),
])
def test_a_parameter_name_says_where_its_measurements_belong(name, rule, zone):
    assert placement.guess_rule(name)[:2] == (rule, zone)


def test_a_sediment_parameter_is_named_as_one_velocities_say_little_about():
    rule, zone, note = placement.guess_rule("GAIA: SHIELDS PARAMETERS")
    assert rule == "everywhere" and note == placement.SEDIMENT_NOTE


def test_no_measurement_near_the_inflow_and_the_outflow(flow):
    from axqua.bayescal import MIN_BOUNDARY_NODES

    allowed = placement.reachable_mask(flow, 0.05)
    assert allowed.any() and flow.hops[allowed].min() == MIN_BOUNDARY_NODES
    assert flow.depth[allowed].min() >= 0.05
    assert placement.reachable_mask(flow, 0.05, 0).sum() > allowed.sum()


def test_the_rules_choose_their_water(flow):
    allowed = placement.reachable_mask(flow, 0.05)
    zone, _ = placement.rule_mask(flow, ParameterNeed("zone2", "zone", 2), allowed)
    assert zone.any() and set(flow.zone[zone].tolist()) == {2}
    margins, _ = placement.rule_mask(flow, ParameterNeed("p", "margins"), allowed)
    assert margins.any() and flow.depth[margins].max() <= placement.MARGIN_DEPTH
    banks, _ = placement.rule_mask(flow, ParameterNeed("p", "banks"), allowed)
    assert banks.any() and np.abs(flow.y[banks] - (Y0 + survey_case.HEIGHT / 2)).min() > 15.0      # away from the middle
    shear, _ = placement.rule_mask(flow, ParameterNeed("p", "shear"), allowed)
    assert 0 < shear.sum() <= 0.3 * allowed.sum()
    assert np.abs(flow.y[shear] - (Y0 + survey_case.HEIGHT / 2)).mean() > np.abs(flow.y[allowed] - (Y0 + survey_case.HEIGHT / 2)).mean()
    everywhere, _ = placement.rule_mask(flow, ParameterNeed("p", "everywhere"), allowed)
    assert (everywhere == allowed).all()
    straight, _ = placement.rule_mask(flow, ParameterNeed("p", "bends"), allowed & (flow.x < X0 + 120))
    assert not straight.any()                                                     # a straight channel has no bend


def test_a_wall_of_the_model_is_a_bank():
    """A channel that is wet from wall to wall has banks too: its walls, not its inflow and its outflow."""
    walled = channel_flow()
    walled.depth = np.full_like(walled.depth, 0.5)
    edge = walled.wet_edge_distance()
    wall = (np.abs(walled.y - Y0) < 0.1) | (np.abs(walled.y - (Y0 + survey_case.HEIGHT)) < 0.1)
    inside = walled.hops > 0
    assert (walled.outline_nodes() == (wall | ~inside)).all()
    assert (edge[wall & inside] == 0).all() and int(edge.max()) == 15            # 30 m to the middle on a mesh of 2 m
    middle = np.abs(walled.y - (Y0 + survey_case.HEIGHT / 2)) < 0.1
    assert (edge[middle & (walled.hops == 0)] == 15).all()                       # the inflow is no bank
    allowed = placement.reachable_mask(walled, 0.05)
    banks, _ = placement.rule_mask(walled, ParameterNeed("p", "banks"), allowed)
    assert banks.any() and np.abs(walled.y[banks] - (Y0 + survey_case.HEIGHT / 2)).min() > 20.0


def test_a_zone_shallower_than_its_largest_roughness_is_remarked(flow):
    allowed = placement.reachable_mask(flow, 0.05)
    deep, remark = placement.rule_mask(flow, ParameterNeed("zone1", "zone", 1, 6, limits=[0.02, 0.3]), allowed)
    assert remark == "" and flow.depth[deep].min() >= 0.3
    _, remark = placement.rule_mask(flow, ParameterNeed("zone1", "zone", 1, 6, limits=[0.02, 2.0]), allowed)
    assert "shallower than its largest ks (2 m)" in remark


def test_spread_points_keep_their_spacing():
    rng = np.random.default_rng(1)
    xy = rng.uniform(0.0, 100.0, size=(400, 2))
    chosen = placement.spread(xy, 12, xy=xy, spacing=10.0)
    assert len(chosen) == len(set(chosen)) == 12
    picked = xy[chosen]
    gaps = np.hypot(picked[:, None, 0] - picked[None, :, 0], picked[:, None, 1] - picked[None, :, 1])[np.triu_indices(12, 1)]
    assert gaps.min() >= 10.0
    assert len(placement.spread(xy, 500, xy=xy, spacing=30.0)) < 30                 # the room runs out before the wish
    assert placement.spread(xy, 0) == [] and placement.spread(np.empty((0, 2)), 3) == []
    far = placement.spread(xy, 1, taken=np.array([[0.0, 0.0]]))
    assert np.hypot(*xy[far[0]]) > 100.0                                          # the farthest from what is taken


def test_every_parameter_gets_its_points_and_a_zone_without_water_is_named(flow):
    allowed = placement.reachable_mask(flow, 0.05)
    needs = zone_needs() + [ParameterNeed("zone7", "zone", 7, 4), ParameterNeed("unused", "everywhere", None, 5, use=False)]
    points = placement.place(flow, needs, allowed)
    assert points.served == {"zone1": 6, "zone2": 6, "zone7": 0}
    assert len(points) == 12 and len(set(points.node)) == 12
    assert [flow.zone[node] for node, purposes in zip(points.node, points.purposes) if purposes == ["zone1"]] == [1] * 6
    x, y = flow.x[points.node], flow.y[points.node]
    gaps = np.hypot(x[:, None] - x[None, :], y[:, None] - y[None, :])[np.triu_indices(12, 1)]
    assert gaps.min() >= 2.0 * flow.edge_length - 1e-9
    assert any("zone7" in text and "the zone is not calibrated" in text for text in points.warnings)


def test_a_point_serves_every_parameter_whose_rule_accepts_it(flow):
    allowed = placement.reachable_mask(flow, 0.05)
    points = placement.place(flow, [ParameterNeed("zone1", "zone", 1, 6), ParameterNeed("all", "everywhere", None, 6)], allowed)
    assert points.served == {"zone1": 6, "all": 6}
    assert len(points) < 12 and ["zone1", "all"] in points.purposes


def test_a_dry_model_takes_no_measurement(flow):
    points = placement.place(flow, zone_needs(), np.zeros(len(flow.x), dtype=bool))
    assert len(points) == 0 and "no node of the model can take a measurement" in points.warnings[0]


# --------------------------------------------------------------------------- #
# walking
# --------------------------------------------------------------------------- #


def test_the_walking_speed_against_the_current_falls_with_the_depth():
    speeds = walk.against_speed(GroundSettings(), np.array([0.2, 0.4, 0.6, 0.9])) * 3.6
    assert speeds.tolist() == pytest.approx([1.5, 1.0, 0.5, 0.1])


def test_walking_upstream_takes_longer_and_the_pool_is_walked_around(flow):
    ground = GroundSettings()
    middle = Y0 + survey_case.HEIGHT / 2
    sites = [ACCESS, (X0 + 60.0, middle), (X0 + 100.0, middle), (X0 + 120.0, Y0 + 10.0), (X0 + 190.0, Y0 + 10.0), (X0 + 155.0, middle)]
    grid = walk.build_grid(flow, ground, sites)
    assert grid.water.any() and not grid.passable[grid.water & (grid.depth > ground.max_depth)].any()
    router = walk.Router(grid, sites)
    assert router.seconds[1, 2] == pytest.approx(40.0 / (1.5 / 3.6), rel=0.15)    # 40 m with the current
    # against the current in 0.9 m of water the 40 m would take 24 min: the way back leaves the river and follows the bank
    assert 1.5 * router.seconds[1, 2] < router.seconds[2, 1] < 300.0
    back = router.line(2, 1)
    assert walk.length(back) > 80.0 and min(p[1] for p in back) < Y0 + 5.0
    assert router.reachable().tolist()[:5] == [True] * 5
    line = router.line(3, 4)                                                      # past the pool, in water a surveyor can stand in
    assert line[0] == sites[3] and line[-1] == sites[4] and walk.length(line) >= 70.0
    depth, u, v, inside = flow.sample(np.array([p[0] for p in line]), np.array([p[1] for p in line]))
    assert bool(ground.wadeable(depth[inside], np.hypot(u, v)[inside]).all())
    assert 0.0 <= router.wet_share(0, 1) <= 1.0 and router.wet_share(1, 2) == 1.0
    # the pool itself: the site is moved to the nearest water that can be waded, and the nodes of the pool are not offered
    reachable = walk.reachable_nodes(grid, flow, *ACCESS)
    pool = (flow.depth > ground.max_depth)
    assert reachable.any() and not (reachable & pool).any()


def test_land_is_walked_faster_than_water(flow):
    ground = GroundSettings()
    sites = [(X0 + 40.0, Y0 - 10.0), (X0 + 140.0, Y0 - 10.0)]                      # both on the bank
    router = walk.Router(walk.build_grid(flow, ground, sites), sites)
    assert router.seconds[0, 1] == pytest.approx(100.0 / (3.0 / 3.6), rel=0.1)
    assert router.wet_share(0, 1) == 0.0


def nearest_neighbor(seconds, sites, start=0):
    left, order, here = list(sites), [], start
    while left:
        nxt = min(left, key=lambda s: seconds[here, s])
        order.append(nxt)
        left.remove(nxt)
        here = nxt
    return order


@pytest.mark.parametrize("seed", range(12))
def test_the_order_of_a_tour_is_a_permutation_and_never_worse_than_the_nearest_neighbor(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(2, 14))
    xy = rng.uniform(0.0, 100.0, size=(n + 1, 2))
    distance = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
    seconds = distance * rng.uniform(1.0, 4.0, size=distance.shape)               # not symmetric, as walking in a current
    sites = list(range(1, n + 1))
    order = walk.order_sites(seconds, sites)
    assert sorted(order) == sites
    assert walk.tour_seconds(seconds, order) <= walk.tour_seconds(seconds, nearest_neighbor(seconds, sites)) + 1e-9


def test_a_tour_of_no_site_and_of_one():
    seconds = np.array([[0.0, 5.0], [7.0, 0.0]])
    assert walk.order_sites(seconds, []) == [] and walk.tour_seconds(seconds, []) == 0.0
    assert walk.order_sites(seconds, [1]) == [1] and walk.tour_seconds(seconds, [1]) == 12.0


def test_a_tour_is_cut_and_dealt_without_losing_a_site():
    rng = np.random.default_rng(3)
    seconds = rng.uniform(10.0, 60.0, size=(11, 11))
    service = np.full(11, 200.0)
    order = list(range(1, 11))
    for parts in (1, 2, 3, 10, 15):
        cut = walk.split_tour(seconds, order, service, parts)
        assert [site for part in cut for site in part] == order                   # the order of the tour is kept
        assert len(cut) == min(parts, 10)
        dealt = walk.deal_tour(seconds, order, service, parts)
        assert sorted(site for run in dealt for site in run) == order and len(dealt) == min(parts, 10)
    sizes = [len(part) for part in walk.split_tour(seconds, order, service, 2)]
    assert max(sizes) - min(sizes) <= 2


def test_a_stretch_of_the_tour_is_as_long_as_its_team_is_large():
    seconds = np.full((41, 41), 30.0)
    service = np.full(41, 200.0)
    order = list(range(1, 41))
    three, one = walk.split_tour(seconds, order, service, [3, 1])
    assert three + one == order and (len(three), len(one)) == (30, 10)
    assert [len(part) for part in walk.split_tour(seconds, order, service, [1, 1])] == [20, 20]
    assert [len(part) for part in walk.split_tour(seconds, order, service, [3, 3, 2])] == [15, 15, 10]
    assert walk.split_tour(seconds, order, service, [0, 0]) == walk.split_tour(seconds, order, service, 2)      # weights that say nothing
    assert walk.split_tour(seconds, [5], service, [3, 1]) == [[5]]


def test_a_grid_without_ground_to_walk_on_is_said(flow):
    grid = walk.build_grid(flow, GroundSettings(), [ACCESS])
    grid.passable[:] = False
    with pytest.raises(SurveyError, match="no ground of the reach can be walked on or waded") as caught:
        grid.nearest_passable(*ACCESS)
    assert "lower discharge" in caught.value.remedy


def test_the_length_of_a_line():
    assert walk.length([(0, 0), (3, 4), (3, 10)]) == 11.0 and walk.length([(1, 1)]) == 0.0


# --------------------------------------------------------------------------- #
# the ground-based survey
# --------------------------------------------------------------------------- #


def day_settings(**ground) -> SurveySettings:
    return SurveySettings.from_dict({"ground": ground, "time": {"date": "2026-06-20", "start": "08:00"}})


def test_a_wading_survey_starts_and_ends_at_the_access_point(flow):
    settings = day_settings()
    plan = plan_ground(flow, settings, zone_needs(), ACCESS)
    points, summary = plan["points"], plan["summary"]
    assert summary["verticals"] == len(points) == 12 and summary["surveyors"] == 1 and summary["days"] == 1
    assert [p["label"] for p in points] == [f"{i:02d}" for i in range(1, 13)] and [p["sequence"] for p in points] == list(range(1, 13))
    ground = settings.ground
    for p in points:
        assert bool(ground.wadeable(p["depth"], p["velocity"])) and p["depth"] >= ground.min_depth
        assert p["n_depths"] == ground.depths_at(p["depth"])
        assert p["minutes"] == pytest.approx(ground.vertical_seconds(p["n_depths"]) / 60.0, abs=0.06)
        assert p["serves"] == f"zone{p['zone']}"
    assert summary["one_depth"] + summary["two_depths"] + summary["three_depths"] == 12
    assert summary["readings"] == sum(p["n_depths"] for p in points)
    three = next(p for p in points if p["n_depths"] == 3)
    assert three["heights"] == "0.2 h, 0.6 h, 0.8 h" and three["heights_m"].split(", ")[1] == f"{0.6 * three['depth']:.2f}"
    walks = [p for p in plan["paths"] if p["who"] == "surveyor"]
    assert len(walks) == 13 and walks[0]["from"] == "access" and walks[0]["to"] == "01"
    assert walks[-1]["kind"] == "return" and walks[-1]["to"] == "access" and walks[-1]["coords"][-1] == ACCESS
    assert all(a["to"] == b["from"] for a, b in zip(walks, walks[1:]))             # one line, in the direction of the walk
    assert not [p for p in plan["paths"] if p["who"] == "DGPS"] and summary["dgps_rovers"] == 1
    timeline = plan["timeline"]
    assert timeline[0]["what"] == "Start at the access point" and timeline[0]["start"] == "08:00" and timeline[-1]["what"] == "Back at the access point"
    starts = [(row["day"], row["start"]) for row in timeline]
    assert starts == sorted(starts)
    assert summary["per_day"][0]["start"] == "08:00" and summary["per_day"][0]["end"] == timeline[-1]["end"]
    assert plan["warnings"] == []


def test_the_probe_is_adjusted_to_the_atmosphere_at_the_start_and_every_four_hours(flow):
    plan = plan_ground(flow, day_settings(), zone_needs(30), ACCESS)
    marks = [e for e in plan["events"] if e["kind"] == "pressure adjustment"]
    assert marks[0]["at"] == "access" and marks[0]["start"] == "08:00" and marks[0]["minutes"] == 5.0
    assert plan["summary"]["pressure_adjustments"] == len(marks) >= 2
    minutes = [clock_minutes(e["start"]) for e in marks]
    assert all(240.0 <= b - a <= 300.0 for a, b in zip(minutes, minutes[1:]))
    assert all(e["at"] != "access" for e in marks[1:])                            # in the river, at the vertical that follows
    kinds = {e["kind"] for e in plan["events"]}
    assert {"break", "long break"} <= kinds
    assert plan["summary"]["break_minutes"] == sum(e["minutes"] for e in plan["events"] if e["kind"] in ("break", "long break"))
    never = plan_ground(flow, day_settings(pressure_interval_h=0, pressure_minutes=0), zone_needs(), ACCESS)
    assert never["summary"]["pressure_adjustments"] == 0


def test_a_short_working_day_makes_a_survey_of_several_days(flow):
    settings = SurveySettings.from_dict({"time": {"date": "2026-06-20", "start": "08:00", "day_hours": 0.5}})
    plan = plan_ground(flow, settings, zone_needs(), ACCESS)
    summary = plan["summary"]
    assert summary["days"] > 1 and len(summary["per_day"]) == summary["days"] and summary["verticals"] == 12
    assert [day["date"] for day in summary["per_day"]][:2] == ["2026-06-20", ""]   # without the sun of the days, only the first has a date
    for day in summary["per_day"]:
        assert day["start"] == "08:00" and clock_minutes(day["end"]) <= clock_minutes(day["latest_end"]) == 510.0
    assert sum(day["verticals"] for day in summary["per_day"]) == 12
    returns = [p for p in plan["paths"] if p["kind"] == "return"]
    assert len(returns) == summary["days"] and {p["day"] for p in returns} == set(range(1, summary["days"] + 1))
    assert sorted({p["day"] for p in plan["points"]}) == list(range(1, summary["days"] + 1))


def test_the_sunset_ends_the_day_of_a_survey_that_has_a_start(flow):
    sun = [{"date": "2026-06-20", "sunrise": 300.0, "sunset": 520.0}, {"date": "2026-06-21", "sunrise": 300.0, "sunset": 520.0}, {"date": "2026-06-22", "sunrise": 300.0, "sunset": 520.0}]
    plan = plan_ground(flow, day_settings(), zone_needs(), ACCESS, sun=sun)
    first = plan["summary"]["per_day"][0]
    assert first["limit"] == "sunset" and first["latest_end"] == "08:40" and plan["summary"]["days"] > 1
    assert plan["summary"]["per_day"][1]["date"] == "2026-06-21"


def test_several_surveyors_share_the_verticals_and_the_rover_has_a_path_of_its_own(flow):
    one = plan_ground(flow, day_settings(), zone_needs(), ACCESS)
    plan = plan_ground(flow, day_settings(surveyors=2), zone_needs(), ACCESS)
    summary = plan["summary"]
    assert summary["surveyors"] == 2 and summary["dgps_rovers"] == 1 and summary["verticals"] == 12
    assert {p["surveyor"] for p in plan["points"]} == {1, 2}
    assert all(p["label"] == f"{p['surveyor']}.{p['order']:02d}" for p in plan["points"])
    rover = [p for p in plan["paths"] if p["who"] == "DGPS"]
    assert rover and rover[0]["from"] == "access" and rover[-1]["to"] == "access"
    assert {p["to"] for p in rover} - {"access"} == {p["label"] for p in plan["points"]}   # the rover records every vertical
    assert clock_minutes(summary["per_day"][0]["end"]) < clock_minutes(one["summary"]["per_day"][0]["end"])
    assert len([e for e in plan["events"] if e["kind"] == "pressure adjustment" and e["at"] == "access"]) == 2   # one probe each
    four = plan_ground(flow, day_settings(surveyors=4), zone_needs(), ACCESS)
    assert four["summary"]["dgps_rovers"] == 2                                    # at most three surveyors per rover
    assert {p["number"] for p in four["paths"] if p["who"] == "DGPS"} == {1, 2}
    # the team of three takes three quarters of the verticals, not half of them: the fourth surveyor would work alone for twice as long
    counts = [sum(1 for p in four["points"] if p["surveyor"] == who) for who in (1, 2, 3, 4)]
    assert sum(counts) == 12 and counts[3] <= 4 and sum(counts[:3]) >= 8 and max(counts) - min(counts) <= 2


def test_more_surveyors_than_verticals_is_said(flow):
    plan = plan_ground(flow, day_settings(surveyors=3), [ParameterNeed("zone1", "zone", 1, 2)], ACCESS)
    assert plan["summary"]["verticals"] == 2
    assert any("2 of the 3 surveyors have verticals to measure" in text for text in plan["warnings"])


def test_a_zone_that_cannot_be_waded_is_named_with_what_to_do(flow):
    pool = np.where((flow.x >= X0 + 140) & (flow.x <= X0 + 170) & (flow.depth > 1.2), 3, flow.zone)
    deep = FlowField(x=flow.x, y=flow.y, triangles=flow.triangles, depth=flow.depth, u=flow.u, v=flow.v, bed=flow.bed, hops=flow.hops, zone=pool)
    plan = plan_ground(deep, day_settings(), [ParameterNeed("zone1", "zone", 1, 3), ParameterNeed("zone3", "zone", 3, 3)], ACCESS)
    assert plan["summary"]["verticals"] == 3
    text = " | ".join(plan["warnings"])
    assert "zone3: no wading measurement can be placed for roughness zone 3" in text and "the zone is not calibrated" in text
    assert "roughness zone 3 is too deep or too fast to wade at this discharge (depth up to 1.6 m" in text and "airborne or a kayak survey" in text


def test_a_wading_survey_needs_an_access_point_at_the_river(flow):
    with pytest.raises(SurveyError, match="needs an access point") as caught:
        plan_ground(flow, day_settings(), zone_needs(), None)
    assert "access_points" in caught.value.remedy and caught.value.code == "survey.not_possible"
    with pytest.raises(SurveyError, match="m from the water of the model"):
        plan_ground(flow, day_settings(), zone_needs(), (X0 + 9000.0, Y0))


# --------------------------------------------------------------------------- #
# the airborne survey
# --------------------------------------------------------------------------- #


def test_the_footprint_of_the_camera():
    width, height, gsd = footprint_size(40.0, 70.0, 3840, 2160)
    assert width == pytest.approx(56.0, abs=0.05) and height == pytest.approx(31.5, abs=0.05) and gsd == pytest.approx(0.0146, abs=0.0001)
    assert footprint_size(80.0, 70.0, 3840, 2160)[0] == pytest.approx(2.0 * width)


def test_the_drone_hovers_over_the_water_of_every_parameter(flow):
    plan = plan_airborne(flow, day_settings(), zone_needs(), ACCESS, takeoff_level=401.0)
    stations, summary = plan["stations"], plan["summary"]
    assert summary["stations"] == len(stations) >= 2 and summary["altitude"] == 40.0 and summary["batteries"] >= 1
    assert [s["station"] for s in stations] == list(range(1, len(stations) + 1))
    for s in stations:
        assert s["altitude"] == 40.0 and s["hover_s"] == 180.0 and len(s["polygon"]) >= 4
        assert s["water_level"] == pytest.approx(400.0, abs=0.01) and s["above_takeoff"] == pytest.approx(39.0, abs=0.01)
        assert s["heading"] % 180 == 90                                           # the long side of the frame across or along a flow toward the east
        assert s["px_per_frame"] == pytest.approx(s["velocity"] / 30.0 / (s["gsd_cm"] / 100.0), rel=0.05)
    assert set(summary["stations_of"]) == {"zone1", "zone2"} and min(summary["stations_of"].values()) >= 1
    assert plan["route"][0]["coords"][0] == ACCESS and plan["route"][-1]["coords"][-1] == ACCESS
    assert summary["flight_minutes"] >= len(stations) * 3.0 and summary["video_gb"] > 0
    assert plan["timeline"][0]["start"] == "08:00"
    assert 0.0 < summary["filmed_share"] <= 1.0


def test_an_altitude_above_the_limit_and_tracers_too_small_are_warned_of(flow):
    high = SurveySettings.from_dict({"airborne": {"altitude": 150.0}})
    plan = plan_airborne(flow, high, zone_needs(), ACCESS)
    assert plan["summary"]["altitude"] == 120.0
    text = " | ".join(plan["warnings"])
    assert "the altitude of 150 m is above the limit of 120 m" in text
    assert "too small to detect reliably" in text                                # 3.5 cm tracers on 4.4 cm pixels
    assert not any("too small" in w for w in plan_airborne(flow, SurveySettings(), zone_needs(), ACCESS)["warnings"])


def test_a_flight_without_an_access_point_is_planned_and_says_so(flow):
    plan = plan_airborne(flow, SurveySettings(), zone_needs(), None)
    assert plan["stations"] and any("no access point is set" in text for text in plan["warnings"])


def test_more_flight_time_than_a_battery_holds_needs_a_second_battery(flow):
    settings = SurveySettings.from_dict({"airborne": {"battery_minutes": 8.0, "stations_per_parameter": 4}})
    plan = plan_airborne(flow, settings, zone_needs(), ACCESS)
    assert plan["summary"]["batteries"] >= 2 and {s["battery"] for s in plan["stations"]} == set(range(1, plan["summary"]["batteries"] + 1))


def test_a_station_that_one_battery_cannot_reach_is_warned_of(flow):
    settings = SurveySettings.from_dict({"airborne": {"battery_minutes": 2.0}})          # the hover alone takes three minutes
    plan = plan_airborne(flow, settings, zone_needs(), ACCESS)
    assert len([text for text in plan["warnings"] if "more than one battery holds (2 min)" in text]) == len(plan["stations"])
    assert not any("more than one battery holds" in text for text in plan_airborne(flow, SurveySettings(), zone_needs(), ACCESS)["warnings"])


def test_the_drone_does_not_fly_after_the_end_of_the_day(flow):
    """Ten minutes of daylight hold two stations of about four minutes; the battery would last for all of them."""
    sun = [{"date": f"2026-06-{20 + day}", "sunrise": 300.0, "sunset": 490.0} for day in range(8)]
    settings = SurveySettings.from_dict({"airborne": {"stations_per_parameter": 3, "battery_minutes": 60.0}, "time": {"date": "2026-06-20", "start": "08:00"}})
    plan = plan_airborne(flow, settings, zone_needs(), ACCESS, sun=sun)
    stations = plan["stations"]
    assert len(stations) >= 4 and max(s["day"] for s in stations) >= 2
    assert all(s["end_min"] <= 490.0 + 1e-6 for s in stations)
    for day in {s["day"] for s in stations}:
        assert min(s["start_min"] for s in stations if s["day"] == day) >= 480.0
    days = max(s["day"] for s in stations)
    assert plan["summary"]["days"] == days and len([row for row in plan["timeline"] if row["what"] == "End of the day"]) == days - 1
    assert all(clock_minutes(row["end"]) <= 490.0 for row in plan["timeline"] if row["kind"] == "hover")
    whole = plan_airborne(flow, SurveySettings.from_dict({"airborne": {"stations_per_parameter": 3, "battery_minutes": 60.0}, "time": {"date": "2026-06-20", "start": "08:00"}}), zone_needs(), ACCESS)
    assert {s["day"] for s in whole["stations"]} == {1} and whole["summary"]["batteries"] == 1


# --------------------------------------------------------------------------- #
# the kayak survey
# --------------------------------------------------------------------------- #


def test_two_kayakers_float_once_from_the_access_to_the_leave_point(flow):
    plan = plan_kayak(flow, day_settings(), zone_needs(), ACCESS, LEAVE)
    tracks, summary = plan["tracks"], plan["summary"]
    assert (summary["kayakers"], summary["rounds"], summary["floats"]) == (2, 1, 2) and len(tracks) == 2
    for track in tracks:
        xs = [c[0] for c in track["coords"]]
        assert xs == sorted(xs) and xs[0] < X0 + 70 and xs[-1] > X0 + 140          # downstream, from the put-in toward the take-out
        assert abs(track["fixes"] - track["drift_minutes"] * 60.0 / 5.0) < 2.0        # one position every 5 s; the minutes are rounded
        assert track["spacing_max"] == pytest.approx(track["velocity_max"] * 5.0, abs=0.06)
        assert track["serves"] == "zone1, zone2"
    assert tracks[0]["start"] == "08:00" and tracks[1]["start"] == "08:02"         # staggered
    assert summary["log_interval"] == 5.0 and plan["avoid"] == [] and plan["warnings"] == []
    assert plan["timeline"][0]["kind"] == "start" and plan["timeline"][-1]["kind"] == "end"


def test_one_kayaker_floats_the_reach_twice(flow):
    plan = plan_kayak(flow, SurveySettings.from_dict({"kayak": {"kayakers": 1}}), zone_needs(), ACCESS, LEAVE)
    summary = plan["summary"]
    assert summary["rounds"] == 2 and summary["floats"] == 2 and [t["round"] for t in plan["tracks"]] == [1, 2]
    assert any(row["kind"] == "shuttle" for row in plan["timeline"])
    shuttle = next(row for row in plan["timeline"] if row["kind"] == "shuttle")
    assert clock_minutes(shuttle["end"]) - clock_minutes(shuttle["start"]) == 30.0
    assert clock_minutes(plan["tracks"][1]["start"]) >= clock_minutes(shuttle["end"])


def test_water_faster_than_the_limit_is_marked_to_avoid(flow):
    assert fast_patches(flow, 3.0) == []
    patches = fast_patches(flow, 1.5)
    assert patches and all(p["velocity_max"] > 1.5 for p in patches)
    box = patches[0]["geometry"].bounds
    assert box[0] >= X0 + 130 and box[2] <= X0 + 180                              # the pool
    plan = plan_kayak(flow, SurveySettings.from_dict({"kayak": {"max_velocity": 1.5}}), zone_needs(), ACCESS, LEAVE)
    assert plan["avoid"] and plan["summary"]["avoid_patches"] == len(plan["avoid"]) and plan["summary"]["avoid_area"] > 0


def test_a_kayak_survey_needs_both_points(flow):
    with pytest.raises(SurveyError, match="needs a leave point") as caught:
        plan_kayak(flow, SurveySettings(), zone_needs(), ACCESS, None)
    assert "role 'leave'" in caught.value.remedy
    with pytest.raises(SurveyError, match="needs an access point"):
        plan_kayak(flow, SurveySettings(), zone_needs(), None, LEAVE)


def test_a_long_float_has_its_breaks(flow):
    settings = SurveySettings.from_dict({"kayak": {"break_every_h": 0.01, "break_minutes": 10}})
    plan = plan_kayak(flow, settings, zone_needs(), ACCESS, LEAVE)
    marks = [e for e in plan["events"] if e["kind"] == "break"]
    assert marks and all(e["minutes"] == 10.0 and e["survey"] == "kayak" for e in marks)
    assert plan["tracks"][0]["minutes"] >= plan["tracks"][0]["drift_minutes"] + 10.0


# --------------------------------------------------------------------------- #
# the temperature survey
# --------------------------------------------------------------------------- #


def test_water_is_slow_or_fast_and_shallow_or_deep():
    cls = classify(np.array([0.1, 0.5, 0.1, 0.5]), np.array([0.1, 0.1, 0.5, 0.5]), 0.2, 0.3)
    assert [CLASSES[c] for c in cls] == ["slow, shallow", "slow, deep", "fast, shallow", "fast, deep"]
    assert CLASSES[int(classify(0.3, 0.2, 0.2, 0.3))] == "fast, deep"              # the limits belong to the upper class


def test_points_are_dealt_by_area_with_one_for_every_class():
    dealt = _deal(np.array([10.0, 80.0, 10.0]), 8)
    assert sum(dealt) == 8 and min(dealt) >= 1 and dealt[1] == max(dealt)
    assert _deal(np.array([10.0, 80.0, 30.0]), 2) == [0, 1, 1]
    assert sum(_deal(np.array([1.0, 1.0, 1.0, 1.0]), 4)) == 4


def test_loggers_stand_in_every_class_of_water(flow):
    settings = SurveySettings.from_dict({"temperature": {"slow_velocity": 0.6, "shallow_depth": 0.4}, "time": {"start": "08:00"}})
    plan = plan_temperature(flow, settings, ACCESS)
    points, summary = plan["points"], plan["summary"]
    assert summary["points"] == len(points) == 8 and summary["method"] == "loggers"
    assert [p["label"] for p in points] == [f"T{i:02d}" for i in range(1, 9)]
    assert sum(summary["by_class"].values()) == 8
    present = {name for name, share in summary["area_share"].items() if share > 0}
    assert present >= {"slow, shallow", "fast, deep"} and all(summary["by_class"][name] >= 1 for name in present)
    for p in points:
        assert p["water"] == CLASSES[int(classify(p["depth"], p["velocity"], 0.6, 0.4))] and p["method"] == "logger"
    waded = [p for p in points if p["reach"] == "wading"]
    assert waded and all(p["start"] and clock_minutes(p["end"]) - clock_minutes(p["start"]) == 10.0 for p in waded)
    assert plan["paths"][0]["from"] == "access" and plan["paths"][-1]["to"] == "access" and plan["paths"][0]["who"] == "temperature"
    assert {patch["water"] for patch in plan["patches"]} <= set(CLASSES) and sum(patch["area"] for patch in plan["patches"]) > 5000.0


def test_the_patches_are_classified_with_the_limits_of_the_settings(flow):
    patches = class_patches(flow, 0.6, 0.4)
    area = {name: sum(p["area"] for p in patches if p["water"] == name) for name in CLASSES}
    assert area["fast, deep"] > area["slow, shallow"] > 0
    assert sum(area.values()) == pytest.approx(200.0 * 54.0, rel=0.1)             # the wetted channel
    assert not [p for p in class_patches(flow, 0.0, 0.0) if p["water"] != "fast, deep"]


def test_a_mobile_probe_reads_more_points_in_less_time(flow):
    settings = SurveySettings.from_dict({"temperature": {"method": "probe", "points": 12}, "time": {"start": "08:00"}})
    plan = plan_temperature(flow, settings, ACCESS)
    assert plan["summary"]["points"] == 12 and {p["method"] for p in plan["points"]} == {"probe"}
    assert plan["summary"]["working_minutes"] == pytest.approx(12.0, abs=0.5)


def test_water_too_deep_to_wade_gets_its_logger_from_a_boat(flow):
    settings = SurveySettings.from_dict({"temperature": {"slow_velocity": 0.2, "shallow_depth": 1.3}})
    plan = plan_temperature(flow, settings, ACCESS)                               # "deep" is the pool alone
    deep = [p for p in plan["points"] if p["water"] == "fast, deep"]
    assert deep and all(p["reach"] == "boat, or a line from the bank" and p["start"] == "" for p in deep)
    assert plan["summary"]["not_on_foot"] == len(deep)
    assert not [path for path in plan["paths"] if path["to"] in {p["label"] for p in deep}]      # nobody walks to it
    assert [p["label"] for p in plan["points"]][-len(deep):] == [p["label"] for p in deep]        # the last of the list
    assert any("'fast, deep' water cannot be waded from the access point" in text for text in plan["warnings"])


def test_temperature_points_without_an_access_point_have_no_walk(flow):
    plan = plan_temperature(flow, SurveySettings(), None)
    assert plan["points"] and plan["paths"] == [] and plan["timeline"] == []
    assert any("no access point is set" in text for text in plan["warnings"])


def test_the_kinds_of_survey_are_the_four_the_user_named():
    assert KINDS == ("ground", "airborne", "kayak", "temperature")
