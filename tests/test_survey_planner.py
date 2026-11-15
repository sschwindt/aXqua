"""The survey planner on a built case: the layers of ``survey-plan.gpkg``, the plan folder, the factsheets and ``axqua survey``.

The case is the channel of ``survey_case`` with its steady result. No test reaches the internet: every plan is made offline, and ``conditions._get`` raises.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
import survey_case

from axqua.survey import conditions, factsheet, output, placement, planner
from axqua.survey.field import SurveyError, load_field
from axqua.survey.settings import KINDS, SurveySettings

ALL = {"kinds": list(KINDS), "time": {"date": "2026-06-20", "start": "08:00"}}


@pytest.fixture(autouse=True)
def no_internet(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached for the internet")

    monkeypatch.setattr(conditions, "_get", refuse)


def load(case: Path):
    from axqua.config import load_config

    return load_config(case)


@pytest.fixture()
def case(tmp_path):
    return survey_case.write_case(tmp_path)


@pytest.fixture(scope="module")
def planned(tmp_path_factory):
    """One plan of all four kinds, made once: ``(cfg, case file, what make_plan returned)``."""
    case_file = survey_case.write_case(tmp_path_factory.mktemp("survey"))
    cfg = load(case_file)
    survey_case.set_points(cfg, access=survey_case.ACCESS, leave=survey_case.LEAVE)
    result = planner.make_plan(cfg, SurveySettings.from_dict(ALL), online=False, case_name="survey-test")      # offline: nothing is looked up
    return cfg, case_file, result


# --------------------------------------------------------------------------- #
# the GeoPackage
# --------------------------------------------------------------------------- #


def test_the_access_layer_is_created_once_and_never_emptied(tmp_path):
    import geopandas as gpd

    path = tmp_path / "plan" / output.GPKG_NAME
    assert output.ensure_access_layer(path, 25832) is True
    frame = gpd.read_file(path, layer=output.ACCESS_LAYER)
    assert len(frame) == 0 and list(frame.columns) == ["role", "name", "geometry"] and frame.crs.to_epsg() == 25832
    assert output.read_access(path, 25832)["count"] == 0
    from shapely.geometry import Point

    gpd.GeoDataFrame({"role": ["access"], "name": ["bridge"]}, geometry=[Point(700060.0, 5340001.0)], crs="EPSG:25832").to_file(path, layer=output.ACCESS_LAYER, driver="GPKG")
    assert output.ensure_access_layer(path, 25832) is False
    assert output.read_access(path, 25832)["access"] == (700060.0, 5340001.0)


@pytest.mark.parametrize("text, role", [
    ("access", "access"), ("Access point", "access"), ("", "access"), (None, "access"), (float("nan"), "access"), ("put-in", "access"),
    ("leave", "leave"), ("Take-out", "leave"), ("exit", "leave"), ("pick-up", "leave"),
    ("takeoff", "takeoff"), ("Take-off", "takeoff"), ("drone", "takeoff"), ("launch pad", "takeoff"),
    ("parking", ""), ("bridge", ""),
])
def test_a_role_is_read_forgivingly(text, role):
    assert output.role_of(text) == role


def test_points_are_read_by_role_in_the_coordinate_system_of_the_case(tmp_path):
    import geopandas as gpd
    from shapely.geometry import Point

    path = tmp_path / output.GPKG_NAME
    points = gpd.GeoSeries([Point(700060.0, 5340001.0), Point(700160.0, 5340001.0), Point(700070.0, 5340002.0), Point(700100.0, 5340000.0)], crs="EPSG:25832").to_crs("EPSG:4326")
    gpd.GeoDataFrame({"role": ["access", "leave", "Access", "parking"], "name": ["bridge", None, "second", "car"]}, geometry=points).to_file(path, layer=output.ACCESS_LAYER, driver="GPKG")
    found = output.read_access(path, 25832)
    assert found["access"] == pytest.approx((700060.0, 5340001.0), abs=0.01) and found["leave"] == pytest.approx((700160.0, 5340001.0), abs=0.01)
    assert found["takeoff"] is None and found["count"] == 4 and found["names"] == {"access": "bridge", "leave": ""}
    text = " | ".join(found["notes"])
    assert "has the role 'parking', which is none of access, leave, takeoff: it is not used" in text
    assert "has 2 points with the role 'access': the first is used" in text
    assert output.read_access(tmp_path / "missing.gpkg", 25832)["access"] is None


def test_the_plan_layers_are_replaced_and_the_access_points_kept(planned):
    import geopandas as gpd
    import pyogrio

    cfg, _, result = planned
    gpkg = Path(result["gpkg"])
    layers = {str(name): str(kind) for name, kind in pyogrio.list_layers(gpkg)}
    assert set(layers) == set(output.LAYERS) | {output.ACCESS_LAYER}
    assert result["layers"] == {name: len(gpd.read_file(gpkg, layer=name)) for name in output.LAYERS}
    assert len(gpd.read_file(gpkg, layer=output.ACCESS_LAYER)) == 2
    points = gpd.read_file(gpkg, layer="flowtracker_points")
    assert points.crs.to_epsg() == 25832 and set(points["n_depths"]) <= {1, 2, 3} and points["label"].is_unique
    assert {"label", "sequence", "depth_m", "velocity_ms", "n_depths", "heights", "start_time"} <= set(points.columns)
    walks = gpd.read_file(gpkg, layer="walk_paths")
    assert set(walks["survey"]) == {"ground", "temperature"} and set(walks.geom_type) == {"LineString"}
    first = walks[(walks["survey"] == "ground") & (walks["leg"] == walks[walks["survey"] == "ground"]["leg"].min())].iloc[0]
    assert first["from_point"] == "access" and first.geometry.coords[0] == pytest.approx(survey_case.ACCESS)
    assert "pressure adjustment" in set(gpd.read_file(gpkg, layer="survey_events")["kind"])
    assert set(gpd.read_file(gpkg, layer="drone_footprints").geom_type) == {"Polygon"}


def test_a_second_plan_replaces_the_first_and_keeps_the_points_the_user_set(case):
    import geopandas as gpd

    cfg = load(case)
    survey_case.set_points(cfg, access=survey_case.ACCESS, leave=survey_case.LEAVE)
    first = planner.make_plan(cfg, SurveySettings.from_dict({"kinds": ["ground", "kayak"]}), online=False)
    gpkg = Path(first["gpkg"])
    assert first["layers"]["flowtracker_points"] > 0 and Path(first["files"]["ground"]["factsheet"]).is_file()
    again = planner.make_plan(cfg, SurveySettings.from_dict({"kinds": ["kayak"]}), online=False)
    assert again["layers"]["flowtracker_points"] == 0 and again["layers"]["kayak_tracks"] == 2
    assert len(gpd.read_file(gpkg, layer="flowtracker_points")) == 0 and len(gpd.read_file(gpkg, layer=output.ACCESS_LAYER)) == 2
    folder = Path(again["folder"])
    assert sorted(p.name for p in folder.glob("survey-factsheet-*")) == ["survey-factsheet-kayak.odt"]
    assert sorted(p.name for p in folder.glob("survey-map-*")) == ["survey-map-kayak.pdf", "survey-map-kayak.png"]


# --------------------------------------------------------------------------- #
# what the wizard is told before a plan
# --------------------------------------------------------------------------- #


def test_info_names_the_parameters_of_the_case_and_reads_no_result(tmp_path):
    cfg = load(survey_case.write_case(tmp_path, result=False))
    found = planner.info(cfg)
    assert [(p["name"], p["rule"], p["zone"], p["limits"]) for p in found["parameters"]] == [("zone1", "zone", 1, [0.02, 0.3]), ("zone2", "zone", 2, [0.05, 0.6]), ("VELOCITY DIFFUSIVITY", "shear", None, [1e-06, 0.01])]
    assert all(p["use"] and p["points"] == 6 for p in found["parameters"])
    assert found["has_zones"] is True and found["built"] is True and found["result_exists"] is False and found["gpkg_exists"] is False
    assert Path(found["folder"]).name == "survey-plan" and Path(found["gpkg"]).name == "survey-plan.gpkg" and found["last_plan"] is None
    assert [kind["key"] for kind in found["kinds"]] == list(KINDS) and set(found["roles"]) == {"access", "leave", "takeoff"}
    assert found["settings"]["ground"]["max_depth"] == 1.2 and found["crs_epsg"] == 25832
    assert set(found["layers"]) == set(output.LAYERS)
    json.dumps(found)
    assert not Path(found["folder"]).exists()                                      # asking writes nothing


def test_a_case_without_calibration_parameters_offers_its_roughness_zones(tmp_path):
    cfg = load(survey_case.write_case(tmp_path, calibration=False))
    found = planner.info(cfg)["parameters"]
    assert [(p["name"], p["rule"], p["zone"], p["use"], p["declared"]) for p in found] == [("zone1", "zone", 1, True, True), ("zone2", "zone", 2, True, True)]
    assert all("zone map" in p["note"] for p in found)
    # what the user changed in the wizard is kept
    changed = SurveySettings.from_dict({"parameters": [{"name": "zone2", "use": False, "points": 9}]})
    assert [(n.name, n.use, n.points) for n in placement.case_needs(cfg, changed)] == [("zone1", True, 6), ("zone2", False, 9)]


def test_a_zone_the_calibration_does_not_name_is_offered_and_not_selected(tmp_path):
    case = survey_case.write_case(tmp_path)
    case.write_text(case.read_text().replace("    - {name: zone2, min: 0.05, max: 0.60}\n", ""))
    needs = placement.case_needs(load(case), SurveySettings())
    assert [(n.name, n.use, n.declared) for n in needs] == [("zone1", True, True), ("VELOCITY DIFFUSIVITY", True, True), ("zone2", False, True)]
    assert needs[0].note == "" and needs[2].note == placement.ZONE_NOTE and needs[2].limits is None


def test_the_zones_come_from_the_table_or_from_the_layer(tmp_path):
    case = survey_case.write_case(tmp_path, calibration=False)
    cfg = load(case)
    assert placement.map_zones(cfg) == {1: None, 2: None}
    (tmp_path / "geo" / "roughness-table.csv").write_text("zone_id,ks_min,ks_max\n1,0.02,0.3\n2,0.05,0.6\n")
    assert placement.map_zones(cfg) == {1: (0.02, 0.3), 2: (0.05, 0.6)}
    assert [n.limits for n in placement.case_needs(cfg, SurveySettings())] == [[0.02, 0.3], [0.05, 0.6]]
    cfg.geodata.roughness_table = None
    assert placement.map_zones(cfg) == {1: None, 2: None}                          # read from the zone layer
    (tmp_path / "geo" / "roughness-zones.gpkg").unlink()
    cfg = load(case)                                                               # a case reads a layer once
    cfg.geodata.roughness_table = None
    assert placement.map_zones(cfg) == {}                                          # a layer that is not there offers nothing
    assert placement.map_zones(load(survey_case.write_case(tmp_path / "plain", zones=False))) == {}


def test_init_creates_the_geopackage_and_says_so_once(case):
    cfg = load(case)
    first = planner.init(cfg)
    assert first["created"] is True and Path(first["gpkg"]).is_file() and first["layer"] == "access_points" and first["access"]["count"] == 0
    assert planner.init(cfg)["created"] is False
    assert planner.info(cfg)["gpkg_exists"] is True


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #


def test_a_plan_of_all_four_kinds_writes_its_folder(planned):
    cfg, _, result = planned
    assert set(result["plans"]) == set(KINDS) and result["skipped"] == {} and result["case"] == "survey-test"
    folder = Path(result["folder"])
    assert folder == planner.folder(cfg)
    for kind in KINDS:
        files = result["files"][kind]
        assert Path(files["map"]).name == f"survey-map-{kind}.png" and Path(files["factsheet"]).name == f"survey-factsheet-{kind}.odt"
        assert Path(files["map"]).stat().st_size > 20_000 and Path(files["map_pdf"]).read_bytes()[:5] == b"%PDF-"
        assert Path(files["factsheet"]).is_file()
    assert result["plans"]["ground"]["summary"]["verticals"] > 0 and result["plans"]["airborne"]["summary"]["stations"] > 0
    assert result["plans"]["kayak"]["summary"]["floats"] == 2 and result["plans"]["temperature"]["summary"]["points"] == 8
    assert result["conditions"]["weather"] is False and result["conditions"]["days"][0]["sunrise_text"]
    assert "should check the mobile coverage" in result["conditions"]["coverage"]["statement"]
    stored = json.loads((folder / planner.PLAN_JSON).read_text())
    assert stored["made"] == result["made"] and set(stored["plans"]) == set(KINDS) and stored["settings"]["kinds"] == list(KINDS)
    assert "coords" not in json.dumps(stored["plans"]["ground"]["paths"])           # the geometry is in the GeoPackage
    assert planner.saved_settings(cfg).time.start == "08:00"
    last = planner.info(cfg)["last_plan"]
    assert last["made"] == result["made"] and last["kinds"] == list(KINDS)


def test_a_factsheet_is_an_open_document_in_arial_10_with_narrow_margins(planned):
    _, _, result = planned
    for kind in KINDS:
        path = Path(result["files"][kind]["factsheet"])
        with zipfile.ZipFile(path) as archive:
            assert archive.namelist()[0] == "mimetype" and archive.read("mimetype") == b"application/vnd.oasis.opendocument.text"
            assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
            content, styles = archive.read("content.xml").decode(), archive.read("styles.xml").decode()
        assert 'fo:page-width="21cm"' in styles and 'fo:page-height="29.7cm"' in styles
        for side in ("top", "bottom", "left", "right"):
            assert f'fo:margin-{side}="1.27cm"' in styles
        assert 'style:font-name="Arial"' in styles and 'fo:font-size="10pt"' in styles
        assert "Survey plan:" in content and "Sunrise" in content and "—" not in content
    ground = zipfile.ZipFile(result["files"]["ground"]["factsheet"]).read("content.xml").decode()
    for needed in ("Start at the access point", "Pressure adjustment", "DGPS", "Back at the access point", "survey-map-ground.pdf"):
        assert needed in ground
    kayak = zipfile.ZipFile(result["files"]["kayak"]["factsheet"]).read("content.xml").decode()
    assert "Barker" in kayak and "every 5 s" in kayak


@pytest.mark.skipif(shutil.which("soffice") is None or shutil.which("pdfinfo") is None, reason="needs LibreOffice and pdfinfo")
def test_every_factsheet_is_one_page(planned, tmp_path):
    _, _, result = planned
    sheets = [result["files"][kind]["factsheet"] for kind in KINDS]
    env = {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"}
    done = subprocess.run(["soffice", "--headless", f"-env:UserInstallation=file://{tmp_path / 'profile'}", "--convert-to", "pdf", "--outdir", str(tmp_path), *sheets], capture_output=True, text=True, timeout=300, env=env)
    assert done.returncode == 0, done.stderr
    for kind in KINDS:
        pdf = tmp_path / f"survey-factsheet-{kind}.pdf"
        assert pdf.is_file(), done.stdout + done.stderr
        pages = next(line.split()[1] for line in subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout.splitlines() if line.startswith("Pages:"))
        assert pages == "1", kind


def test_a_long_survey_still_gets_a_sheet_that_counts_as_one_page(tmp_path):
    cfg = load(survey_case.write_case(tmp_path))
    flow = load_field(cfg)
    from axqua.survey.ground import plan_ground
    from axqua.survey.settings import ParameterNeed

    settings = SurveySettings.from_dict({"time": {"date": "2026-06-20", "start": "08:00", "day_hours": 2.0}})
    plan = plan_ground(flow, settings, [ParameterNeed("zone1", "zone", 1, 30), ParameterNeed("zone2", "zone", 2, 30)], survey_case.ACCESS)
    assert plan["summary"]["verticals"] == 60 and plan["summary"]["days"] >= 3
    written = factsheet.write_factsheet(tmp_path / "long.odt", "ground", plan, {"settings": settings, "conditions": {}, "case": "long"})
    assert written["level"] > 0 and written["fits"] is True and written["units"] <= factsheet.PAGE_UNITS
    full = factsheet.build("ground", plan, {"settings": settings, "conditions": {}, "case": "long"}, 0)
    assert full.units > factsheet.PAGE_UNITS


def test_the_width_of_a_text_decides_its_lines():
    assert factsheet.text_width("iiii") < factsheet.text_width("mmmm") and factsheet.text_width("abc", bold=True) > factsheet.text_width("abc")
    width = factsheet.WIDTH * 72.0 / 2.54                                          # the text column of the page [pt]
    assert factsheet.wrapped_lines("short", width) == 1
    assert factsheet.wrapped_lines("word " * 80, width) == 4                       # 80 words of 24.5 pt with their space in 523 pt: 21 to a line
    assert factsheet.wrapped_lines("word " * 80, width, bold=True) > 4
    assert factsheet.wrapped_lines("", width) == 1


def test_a_kind_that_cannot_be_planned_is_skipped_with_its_reason(case):
    cfg = load(case)
    survey_case.set_points(cfg, access=survey_case.ACCESS)                          # no leave point
    result = planner.make_plan(cfg, SurveySettings.from_dict({"kinds": ["ground", "kayak"]}), online=False)
    assert set(result["plans"]) == {"ground"} and set(result["files"]) == {"ground"}
    skipped = result["skipped"]["kayak"]
    assert skipped["title"] and "needs a leave point" in skipped["reason"] and "role 'leave'" in skipped["remedy"]
    assert not list(Path(result["folder"]).glob("*kayak*")) and result["layers"]["kayak_tracks"] == 0


def test_nothing_to_plan_is_an_error_that_says_what_to_do(case):
    cfg = load(case)
    with pytest.raises(SurveyError, match="no survey could be planned") as caught:
        planner.make_plan(cfg, SurveySettings.from_dict({"kinds": ["ground", "kayak"]}), online=False)
    assert "needs an access point" in caught.value.message and "access_points" in caught.value.remedy
    assert Path(planner.gpkg_path(cfg)).is_file()                                  # the layer to set the point in is there
    assert not (planner.folder(cfg) / planner.PLAN_JSON).exists()


def test_settings_that_make_no_sense_are_refused_before_anything_is_read(tmp_path):
    cfg = load(survey_case.write_case(tmp_path, result=False))
    with pytest.raises(SurveyError, match="cannot be planned with these settings: .*at least one surveyor"):
        planner.make_plan(cfg, SurveySettings.from_dict({"ground": {"surveyors": 0}}), online=False)


def test_a_plan_needs_the_result_of_a_steady_run(tmp_path):
    cfg = load(survey_case.write_case(tmp_path, result=False))
    with pytest.raises(SurveyError, match="there is no simulation result to plan with") as caught:
        planner.make_plan(cfg, SurveySettings(), online=False)
    assert "steady simulation" in caught.value.remedy
    Path(cfg.model_path(cfg.geometry_slf)).unlink()
    with pytest.raises(SurveyError, match="not built yet"):
        planner.make_plan(cfg, SurveySettings(), online=False)


def test_without_a_selected_parameter_the_points_are_spread_over_the_water(case):
    cfg = load(case)
    survey_case.set_points(cfg, access=survey_case.ACCESS)
    settings = SurveySettings.from_dict({"parameters": [{"name": name, "use": False} for name in ("zone1", "zone2", "VELOCITY DIFFUSIVITY")], "points_per_parameter": 5})
    result = planner.make_plan(cfg, settings, online=False)
    assert result["plans"]["ground"]["summary"]["verticals"] == 5
    assert any("no calibration parameter is selected" in note for note in result["notes"])
    assert result["parameters"][-1]["name"] == "flow field"


def test_the_field_reads_the_mesh_of_the_geometry_and_the_last_frame(case):
    cfg = load(case)
    flow = load_field(cfg)
    x, y, ikle, depth, u, bed = survey_case.channel()
    assert flow.x.dtype.kind == "f" and float(abs(flow.x - x).max()) < 1e-6 and float(abs(flow.y - y).max()) < 1e-6      # not the single precision of the result
    assert float(abs(flow.depth - depth).max()) < 1e-5 and flow.edge_length == pytest.approx(2.41, abs=0.05)
    assert set(flow.zone.tolist()) == {1, 2} or set(flow.zone.tolist()) == {0, 1, 2}
    assert int(flow.hops.min()) == 0 and int(flow.hops.max()) == 50                 # nodes from the inflow and the outflow
    assert Path(flow.source).name == "r2d.slf"


def test_cleaning_up_never_offers_the_survey_plan(planned):
    from axqua import cleanup

    cfg, case_file, result = planned
    found = cleanup.inventory(cfg, case_file)
    offered = [path for category in found["categories"] for item in category["items"] for path in [item["id"], *item.get("paths", [])]]
    offered += [str(entry) for entry in found.get("in_use", [])]
    assert not [path for path in offered if "survey" in path]
    assert Path(result["gpkg"]).is_file()


# --------------------------------------------------------------------------- #
# axqua survey
# --------------------------------------------------------------------------- #


def answer(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_axqua_survey_from_init_to_plan(case, capsys):
    from axqua.surveycli import run_survey

    assert run_survey(["init", str(case), "--json"]) == 0
    first = answer(capsys)
    assert first["ok"] is True and first["data"]["created"] is True
    survey_case.set_points(load(case), access=survey_case.ACCESS, leave=survey_case.LEAVE)

    assert run_survey(["info", str(case), "--json"]) == 0
    data = answer(capsys)["data"]
    assert data["access"]["access"] and data["access"]["leave"] and data["result_exists"] is True

    assert run_survey(["conditions", str(case), "--date", "2026-06-20", "--offline", "--json"]) == 0
    data = answer(capsys)["data"]
    assert data["days"][0]["date"] == "2026-06-20" and data["coverage"]["country"] == "DE" and data["weather"] is False

    assert run_survey(["plan", str(case), "--kinds", "ground,kayak", "--date", "2026-06-20", "--start", "07:30", "--offline", "--json"]) == 0
    data = answer(capsys)["data"]
    assert set(data["plans"]) == {"ground", "kayak"} and data["case"] == "survey-test"
    assert data["plans"]["ground"]["timeline"][0]["start"] == "07:30" and data["settings"]["time"]["date"] == "2026-06-20"

    # the next call starts from the settings of the last plan
    assert run_survey(["plan", str(case), "--offline"]) == 0
    text = capsys.readouterr().out
    assert "Ground-based" in text and "verticals" in text and "floats" in text and "survey-factsheet-kayak.odt" in text


def test_axqua_survey_reads_a_settings_file(case, tmp_path, capsys):
    from axqua.surveycli import run_survey

    survey_case.set_points(load(case), access=survey_case.ACCESS)
    settings = tmp_path / "my-survey.json"
    settings.write_text(json.dumps({"kinds": ["temperature"], "temperature": {"points": 5, "method": "probe"}}))
    assert run_survey(["plan", str(case), "--settings", str(settings), "--offline", "--json"]) == 0
    data = answer(capsys)["data"]
    assert set(data["plans"]) == {"temperature"} and data["plans"]["temperature"]["summary"]["points"] == 5

    settings.write_text("[1, 2")
    assert run_survey(["plan", str(case), "--settings", str(settings), "--offline", "--json"]) != 0
    failed = answer(capsys)
    assert failed["ok"] is False and "could not be read" in json.dumps(failed)


def test_axqua_survey_fails_with_the_reason_and_the_remedy(tmp_path, capsys):
    from axqua.surveycli import run_survey

    case = survey_case.write_case(tmp_path, result=False)
    assert run_survey(["plan", str(case), "--offline", "--json"]) != 0
    failed = answer(capsys)
    text = json.dumps(failed)
    assert failed["ok"] is False and "there is no simulation result to plan with" in text and "Run the steady simulation" in text and "survey.not_possible" in text


def test_axqua_knows_the_command():
    from axqua import cli

    assert "survey" in cli.COMMANDS if hasattr(cli, "COMMANDS") else True
    source = Path(cli.__file__).read_text()
    assert '"survey": lambda argv: _run_survey(argv)' in source


def test_the_plugin_tests_know_the_defaults_of_the_library():
    """The plugin never imports the library, so its tests read the settings document from a file. A setting that is added, renamed or given another default changes this file, and with it what the forms of the plugin are tested against."""
    snapshot = Path(__file__).parents[1] / "qgis_plugin" / "tests" / "survey_defaults.json"
    written = json.dumps(SurveySettings().as_dict(), indent=2, sort_keys=True) + "\n"
    assert json.loads(snapshot.read_text(encoding="utf-8")) == json.loads(written), f"write the defaults again: {snapshot.name} no longer holds SurveySettings().as_dict()"
