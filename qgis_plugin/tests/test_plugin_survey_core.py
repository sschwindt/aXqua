"""The Survey planner without its window: the form tables, the texts, and the calls to ``axqua survey``.

``survey_defaults.json`` beside this file is the settings document of the library with its defaults. The plugin never imports the library, so the tests of the library (``tests/test_survey_planner.py``) assert that the file is what the library answers, and the tests here that the form of the plugin edits every setting in it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axqua_plugin.core import runner_client, survey

DEFAULTS = json.loads((Path(__file__).with_name("survey_defaults.json")).read_text(encoding="utf-8"))
PLUGIN = Path(__file__).resolve().parents[1] / "axqua"


def test_the_form_edits_every_setting_of_the_library_once():
    keys = [item.key for item in survey.fields()]
    assert len(keys) == len(set(keys))
    in_forms = set(keys)
    document = {f"{block}.{name}" for block in ("ground", "airborne", "kayak", "temperature", "time") for name in DEFAULTS[block]}
    assert in_forms == document - {"time.date", "time.start"}                      # the day and its start are a date and a time field of the window
    assert set(survey.FORMS) == {"ground", "airborne", "kayak", "temperature", "time"} and tuple(DEFAULTS["kinds"]) == ("ground",)
    for item in survey.fields():
        value = survey.get(DEFAULTS, item.key)
        assert item.label and item.kind in ("float", "int", "bool", "choice", "pairs", "numbers"), item.key
        if item.kind in ("float", "int"):
            assert item.low <= value <= item.high, item.key
            assert round(float(value), item.decimals if item.kind == "float" else 0) == float(value), f"{item.key}: the field would round the default"
        elif item.kind == "choice":
            assert value in [choice for choice, _text in item.choices], item.key
        elif item.kind == "bool":
            assert isinstance(value, bool), item.key
        else:
            assert isinstance(value, list) and value, item.key
    assert [item.key for item in survey.fields("kayak")][0].startswith("kayak.")


def test_the_defaults_the_survey_was_ordered_with():
    ground = DEFAULTS["ground"]
    assert ground["max_depth"] == 1.2 and ground["velocity_limits"] == [[0.6, 2.0], [0.9, 1.4], [1.2, 1.0]]
    assert (ground["speed_land"], ground["speed_with_current"], ground["speed_against_current"]) == (3.0, 1.5, [[0.3, 1.0], [0.5, 0.5], [0.8, 0.1]])
    assert (ground["time_per_depth"], ground["time_probe_height"], ground["time_water_depth"], ground["pressure_interval_h"]) == (60.0, 40.0, 60.0, 4.0)
    assert (ground["depth_two_points"], ground["depth_three_points"], ground["surveyors"], ground["surveyors_per_dgps"]) == (0.3, 0.5, 1, 3)
    assert ground["heights_one"] == [0.6]                                          # 0.6 of the depth above the riverbed
    time = DEFAULTS["time"]
    assert (time["day_hours"], time["short_break_minutes"], time["short_break_every_h"], time["long_break_minutes"], time["long_break_every_h"]) == (8.0, 10.0, 2.0, 30.0, 4.0)
    kayak = DEFAULTS["kayak"]
    assert (kayak["kayakers"], kayak["log_interval"], kayak["max_velocity"], kayak["break_every_h"], kayak["break_minutes"]) == (2, 5.0, 3.0, 2.0, 10.0)


def _assigned(path: Path, name: str):
    """The node a module assigns to *name* at its top level, read from the source: the plugin never imports the library."""
    import ast

    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return node.value
    raise AssertionError(f"{path.name} assigns no {name}")


def test_the_names_of_the_plugin_are_the_names_of_the_library():
    import ast

    library = PLUGIN.parents[1] / "src" / "axqua" / "survey"
    assert survey.KINDS == ast.literal_eval(_assigned(library / "settings.py", "KINDS"))
    assert survey.KIND_TITLES == ast.literal_eval(_assigned(library / "settings.py", "KIND_TITLES"))
    assert set(survey.RULE_TITLES) == set(ast.literal_eval(_assigned(library / "settings.py", "RULES")))
    assert survey.ACCESS_LAYER == ast.literal_eval(_assigned(library / "output.py", "ACCESS_LAYER"))
    assert survey.WALKABLE_LAYER == ast.literal_eval(_assigned(library / "output.py", "WALKABLE_LAYER"))
    assert survey.ROLES == tuple(ast.literal_eval(_assigned(library / "output.py", "ROLES")))
    layers = [key.value for key in _assigned(library / "output.py", "LAYERS").keys]
    assert sorted(name for name, _kind, _title in survey.PLAN_LAYERS) == sorted(layers) and survey.ACCESS_LAYER not in layers and survey.WALKABLE_LAYER not in layers
    assert {kind for _name, kind, _title in survey.PLAN_LAYERS} <= set(survey.KINDS) | {""}


def test_a_dotted_key_reads_and_writes_the_document():
    document = {"ground": {"max_depth": 1.2}}
    assert survey.get(document, "ground.max_depth") == 1.2 and survey.get(document, "ground.missing", 7) == 7 and survey.get(document, "kayak.floats") is None
    survey.put(document, "kayak.floats", 2)
    survey.put(document, "ground.max_depth", 1.0)
    survey.put(document, "online", False)
    assert document == {"ground": {"max_depth": 1.0}, "kayak": {"floats": 2}, "online": False}


def test_pairs_and_numbers_are_written_as_a_person_types_them():
    assert survey.pairs_text([[0.6, 2.0], [0.9, 1.4], [1.2, 1.0]]) == "0.6: 2; 0.9: 1.4; 1.2: 1"
    assert survey.parse_pairs("0.9: 1.4; 0.6: 2 ;1,2: 1;") == [[0.6, 2.0], [0.9, 1.4], [1.2, 1.0]]            # sorted, decimal comma read
    assert survey.parse_pairs(survey.pairs_text(DEFAULTS["ground"]["speed_against_current"])) == DEFAULTS["ground"]["speed_against_current"]
    with pytest.raises(ValueError, match="lacks the colon"):
        survey.parse_pairs("0.6 2")
    with pytest.raises(ValueError, match="is not two numbers"):
        survey.parse_pairs("0.6: fast")
    with pytest.raises(ValueError, match="at least one pair"):
        survey.parse_pairs(" ; ")
    assert survey.numbers_text([0.2, 0.6, 0.8]) == "0.2; 0.6; 0.8" and survey.parse_numbers("0,2; 0.6;0.8") == [0.2, 0.6, 0.8]
    with pytest.raises(ValueError, match="'x' is not a number"):
        survey.parse_numbers("0.2; x")
    with pytest.raises(ValueError, match="at least one number"):
        survey.parse_numbers("")


def test_the_reading_heights_of_a_vertical_are_checked():
    assert survey.heights_problem("ground.heights_two", [0.2, 0.8]) == ""
    assert survey.heights_problem("ground.heights_two", [0.6]) == "2 heights needed, 1 given"
    assert survey.heights_problem("ground.heights_one", [0.2, 0.8]) == "1 height needed, 2 given"
    assert "between 0 and 1" in survey.heights_problem("ground.heights_three", [0.2, 0.6, 1.0])
    assert survey.heights_problem("airborne.altitude", [40.0]) == ""


def test_the_rows_of_the_parameter_table():
    info = {"parameters": [{"name": "zone1", "rule": "zone", "zone": 1, "points": 6, "use": True, "declared": True, "note": ""},
                           {"name": "VELOCITY DIFFUSIVITY", "rule": "shear", "zone": None, "points": 4, "use": False, "declared": False}]}
    rows = survey.parameter_rows(info)
    assert rows[0] == {"name": "zone1", "rule": "zone", "zone": 1, "points": 6, "use": True, "declared": True, "note": ""}
    assert rows[1]["declared"] is False and rows[1]["note"] == ""
    assert survey.parameter_setting(rows[0]) == {"name": "zone1", "rule": "zone", "points": 6, "use": True, "zone": 1}
    assert survey.parameter_setting(rows[1]) == {"name": "VELOCITY DIFFUSIVITY", "rule": "shear", "points": 4, "use": False}
    assert survey.parameter_setting({"name": "my ks", "rule": "everywhere", "zone": 3, "points": 2, "use": True}) == {"name": "my ks", "rule": "everywhere", "points": 2, "use": True}
    assert survey.parameter_rows({}) == []
    assert survey.rule_title("shear") == "in shear zones" and survey.rule_title("new", "where it acts") == "where it acts" and survey.rule_title("new") == "new"


def test_the_points_of_the_access_layer_in_lines():
    assert survey.access_lines({}) == ["No point is set yet."]
    lines = survey.access_lines({"access": [700060.0, 5340001.0], "leave": [700160.04, 5340001.0], "takeoff": None, "names": {"access": "bridge"}, "notes": ["a note"]})
    assert lines == ["access \"bridge\": 700060.0, 5340001.0", "leave: 700160.0, 5340001.0", "a note"]
    assert "role access is missing" in survey.access_lines({"leave": [1.0, 2.0]})[-1]


def test_the_areas_of_the_walkable_layer_in_lines():
    nothing = ["No area is drawn: on land the plan walks along the wetted edge only."]
    assert survey.walkable_lines({}) == nothing and survey.walkable_lines(None) == nothing and survey.walkable_lines({"count": 0, "area": 0.0, "notes": []}) == nothing
    assert survey.walkable_lines({"count": 1, "area": 412.4, "notes": []}) == ["1 area, 412 m\u00b2: on land the plan walks inside it and along the wetted edge."]
    lines = survey.walkable_lines({"count": 3, "area": 25300.0, "notes": ["1 feature of the layer walkable_areas is no polygon with an area: not used"]})
    assert lines == ["3 areas, 2.53 ha: on land the plan walks inside them and along the wetted edge.", "1 feature of the layer walkable_areas is no polygon with an area: not used"]
    field = next(field for group in survey.GROUND for field in group.fields if field.key == "ground.bank_strip")
    assert field.unit == "m" and field.low > 0.0 and "walkable areas" in field.tip


def test_daylight_weather_and_coverage_in_lines():
    answer = {"days": [{"date": "2026-06-20", "sunrise_text": "05:12", "sunset_text": "21:17", "weather": "light rain", "t_min": 11.4, "t_max": 19.6, "precipitation": 2.34, "gusts": 9.6},
                       {"date": "2026-06-21", "sunrise_text": "05:12", "sunset_text": "21:17"}, {"date": "2026-06-22"}, {"date": "2026-06-23"}],
              "timezone": "Europe/Berlin", "suggestion": {"start": "07:00", "end": "15:40", "reason": "within daylight"},
              "coverage": {"statement": "Surveyors should check the coverage."}, "notes": ["no forecast yet"], "sources": ["Weather: Open-Meteo"]}
    lines = survey.conditions_lines(answer)
    assert lines[0] == "2026-06-20: sunrise 05:12, sunset 21:17; light rain, 11 to 20 °C, 2.3 mm of rain, gusts up to 10 m/s"
    assert lines[1] == "2026-06-21: sunrise 05:12, sunset 21:17" and len([line for line in lines if line.startswith("2026-06-2")]) == 3
    assert lines[3:] == ["Times are local times, Europe/Berlin.", "Suggested working day: 07:00 to 15:40 (within daylight).", "Surveyors should check the coverage.", "Note: no forecast yet", "Weather: Open-Meteo"]
    assert survey.suggested_start(answer) == "07:00" and survey.suggested_start({}) == ""
    assert survey.conditions_lines({"suggestion": {"reason": "it rains all day"}}) == ["No start is suggested: it rains all day."]


PLAN = {
    "folder": "/case/survey-plan", "gpkg": "/case/survey-plan/survey-plan.gpkg",
    "plans": {
        "ground": {"summary": {"verticals": 16, "one_depth": 2, "two_depths": 5, "three_depths": 9, "surveyors": 2, "walk_length": 2340.0, "days": 2,
                               "per_day": [{"start": "08:00", "end": "15:50"}, {"start": "08:00", "end": "10:05"}]}, "warnings": ["zone 3 has no water that can be waded"]},
        "airborne": {"summary": {"stations": 4, "altitude": 40.0, "flight_minutes": 21.4, "batteries": 1, "start": "08:00", "end": "08:35"}},
        "kayak": {"summary": {"floats": 2, "length": 1840.0, "kayakers": 1, "fixes": 410, "start": "08:00", "end": "09:25"}},
        "temperature": {"summary": {"points": 8, "method": "loggers", "days": 1, "start": "08:00", "end": "10:10"}},
    },
    "skipped": {}, "notes": ["planned offline"],
    "layers": {"flowtracker_points": 16, "walk_paths": 26, "survey_events": 1, "drone_positions": 4, "drone_footprints": 4, "drone_route": 5, "kayak_tracks": 2, "kayak_avoid": 0, "temperature_points": 8, "temperature_patches": 4},
    "files": {"ground": {"map": "/p/survey-map-ground.png", "map_pdf": "/p/survey-map-ground.pdf", "factsheet": "/p/survey-factsheet-ground.odt"}, "kayak": {"map": "/p/survey-map-kayak.png", "factsheet": "/p/survey-factsheet-kayak.odt"}},
}


def test_a_plan_in_lines():
    assert survey.kind_line("ground", PLAN["plans"]["ground"]) == "16 verticals (2 at one depth, 5 at two, 9 at three), 2 surveyors with a probe, 2.3 km on foot, 2 days, 08:00 to 10:05 on day 2"
    assert survey.kind_line("airborne", PLAN["plans"]["airborne"]) == "4 hover positions at 40 m above the water, 21 min in the air with 1 battery, 08:00 to 08:35"
    assert survey.kind_line("kayak", PLAN["plans"]["kayak"]) == "2 drifts of 1.84 km by 1 kayaker, about 410 positions, 08:00 to 09:25"
    assert survey.kind_line("temperature", PLAN["plans"]["temperature"]) == "8 loggers, 1 day, 08:00 to 10:10"
    assert "readings of a mobile probe" in survey.kind_line("temperature", {"summary": {"points": 5, "method": "probe"}})
    lines = survey.plan_lines(PLAN)
    assert lines[0].startswith("Ground-based velocimetry (wading): 16 verticals") and lines[1] == "    Warning: zone 3 has no water that can be waded"
    assert [line.split(":")[0] for line in lines[2:5]] == ["Airborne velocimetry (drone video)", "Kayak drifter velocimetry", "Water temperature"]
    assert lines[-2:] == ["Note: planned offline", "Maps, factsheets and survey-plan.gpkg are in /case/survey-plan."]
    skipped = survey.plan_lines({"skipped": {"kayak": {"title": "Kayak drifter velocimetry", "reason": "it needs a leave point", "remedy": "Set one."}}})
    assert skipped == ["Not planned: Kayak drifter velocimetry: it needs a leave point. Set one."]


def test_the_files_and_layers_of_a_plan():
    assert survey.plan_files(PLAN) == [("ground", "/p/survey-map-ground.pdf", "/p/survey-factsheet-ground.odt"), ("kayak", "/p/survey-map-kayak.png", "/p/survey-factsheet-kayak.odt")]
    names = [name for name, _title in survey.layers_to_load(PLAN)]
    assert "kayak_avoid" not in names and len(names) == 9                         # an empty layer is not put on the map
    assert names[0] == "temperature_patches" and names[-1] == "flowtracker_points"   # areas below, the verticals on top
    order = [name for name, _kind, _title in survey.PLAN_LAYERS]
    assert all(order.index(area) < order.index(point) for area in ("temperature_patches", "kayak_avoid", "drone_footprints") for point in ("flowtracker_points", "temperature_points", "drone_positions", "walk_paths"))
    assert survey.layer_uri("/case/survey-plan.gpkg", "walk_paths") == "/case/survey-plan.gpkg|layername=walk_paths"
    assert survey.layers_to_load({}) == [] and survey.plan_files({}) == []


def test_the_calls_to_axqua_survey(monkeypatch):
    sent = []

    class Answer:
        data = {"ok": 1}

    def call(self, args, **kwargs):
        entry = {"args": list(args), "timeout": kwargs.get("timeout")}
        if "--settings" in args:
            path = Path(args[args.index("--settings") + 1])
            entry["settings"] = json.loads(path.read_text(encoding="utf-8"))
            entry["file"] = path
        sent.append(entry)
        return Answer()

    client = runner_client.RunnerClient.__new__(runner_client.RunnerClient)
    monkeypatch.setattr(runner_client.RunnerClient, "call", call)
    assert client.survey_info("reach.axq-case") == {"ok": 1} and client.survey_init("reach.axq-case") == {"ok": 1}
    client.survey_conditions("reach.axq-case", {"time": {"date": "2026-06-20"}}, offline=True)
    client.survey_plan("reach.axq-case", {"kinds": ["kayak"]})
    client.survey_plan("reach.axq-case")
    assert [entry["args"][:3] for entry in sent] == [["survey", "info", "reach.axq-case"], ["survey", "init", "reach.axq-case"], ["survey", "conditions", "reach.axq-case"], ["survey", "plan", "reach.axq-case"], ["survey", "plan", "reach.axq-case"]]
    assert sent[2]["args"][3] == "--offline" and sent[2]["settings"] == {"time": {"date": "2026-06-20"}}
    assert "--offline" not in sent[3]["args"] and sent[3]["settings"] == {"kinds": ["kayak"]} and sent[4]["args"] == ["survey", "plan", "reach.axq-case"]
    assert sent[3]["timeout"] >= 3600                                              # a long reach takes minutes, not the seconds of a question
    assert not sent[2]["file"].exists() and not sent[3]["file"].exists()           # the settings file is removed after the call


def test_an_axqua_from_before_the_survey_planner_is_told_to_upgrade(monkeypatch):
    """The plugin is updated in QGIS, the program with pip: aXqua 1.3.5 answers 'axqua survey' with "the arguments could not be understood"."""
    def call(self, args, **kwargs):
        raise runner_client.RunnerError("axqua: the arguments could not be understood", code="axqua.config", subject="arguments", remedy="Run 'axqua --help' to see the accepted flags; argparse's own message is on stderr.")

    client = runner_client.RunnerClient.__new__(runner_client.RunnerClient)
    monkeypatch.setattr(runner_client.RunnerClient, "call", call)
    monkeypatch.setattr(runner_client.RunnerClient, "_version", lambda self: "1.3.5")
    for ask in (lambda: client.survey_info("reach.axq-case"), lambda: client.survey_init("reach.axq-case"), lambda: client.survey_plan("reach.axq-case", {"kinds": ["ground"]}), lambda: client.survey_conditions("reach.axq-case")):
        with pytest.raises(runner_client.RunnerError) as caught:
            ask()
        assert "needs aXqua 1.4.0 or newer" in caught.value.message and "(1.3.5)" in caught.value.message
        assert "pip install --upgrade axqua" in caught.value.remedy and "--help" not in caught.value.remedy


def test_another_failure_of_the_survey_planner_keeps_its_own_words(monkeypatch):
    def call(self, args, **kwargs):
        raise runner_client.RunnerError("the case has no steady result", code="axqua.survey", subject="result", remedy="Run the steady simulation.")

    client = runner_client.RunnerClient.__new__(runner_client.RunnerClient)
    monkeypatch.setattr(runner_client.RunnerClient, "call", call)
    with pytest.raises(runner_client.RunnerError) as caught:
        client.survey_info("reach.axq-case")
    assert caught.value.message == "the case has no steady result" and caught.value.remedy == "Run the steady simulation."


def test_the_settings_file_is_removed_when_the_call_fails(monkeypatch):
    seen = []

    def call(self, args, **kwargs):
        seen.append(Path(args[args.index("--settings") + 1]))
        raise runner_client.RunnerError("the survey cannot be planned")

    client = runner_client.RunnerClient.__new__(runner_client.RunnerClient)
    monkeypatch.setattr(runner_client.RunnerClient, "call", call)
    with pytest.raises(runner_client.RunnerError):
        client.survey_plan("reach.axq-case", {"kinds": ["ground"]})
    assert seen and not seen[0].exists()


def test_the_survey_planner_never_imports_the_library_and_has_its_help():
    import re

    from axqua_plugin.gui import sections

    for name in ("core/survey.py", "gui/survey_layers.py", "gui/survey_wizard.py", "gui/survey_box.py"):
        text = (PLUGIN / name).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import axqua\b|from axqua\b)", text, re.M), name
        assert "PyQt5" not in text and "PyQt6" not in text and "exec_(" not in text, name
        assert "—" not in text, name
    assert sections.WINDOWS["survey-wizard"] == ("usage/calibration-validation", "help-survey-planner")
    page = (PLUGIN.parents[1] / "docs" / "usage" / "calibration-validation.rst").read_text(encoding="utf-8")
    assert ".. _help-survey-planner:" in page
    # the names the window shows are the ones the documentation uses
    for label in ("*Survey planner...*", "*Set points on the map*", "*Save the points*", "*Look up*", "*Plan the survey*", "*Add parameter...*"):
        assert label in page, label
