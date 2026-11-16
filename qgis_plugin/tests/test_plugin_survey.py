"""The Survey planner under QGIS: the layers of ``survey-plan.gpkg`` on the map, the window, and its box on the tab.

The library is not there in these tests. The GeoPackage is written with OGR in the layout the library writes (``tests/test_survey_planner.py`` pins that layout), and a fake client answers what ``axqua survey`` answers.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("qgis.core", reason="PyQGIS is not importable in this interpreter")

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsProject  # noqa: E402

DEFAULTS = json.loads((Path(__file__).with_name("survey_defaults.json")).read_text(encoding="utf-8"))
X0, Y0 = 700000.0, 5340000.0
ROLES = {"access": "where the surveyors reach the river", "leave": "where the kayaks are taken out of the river", "takeoff": "where the drone takes off and lands"}
RULES = {"zone": "inside one roughness zone", "everywhere": "spread over the wetted model", "shear": "where the velocity changes most across the flow", "bends": "where the streamlines curve most",
         "banks": "along the banks", "margins": "in the shallow margins"}

#: The layers a plan fills, with the fields the styles read and the rows of the test plan.
PLAN = {
    "flowtracker_points": ("Point", [("label", str), ("surveyor", int), ("n_depths", int)],
                           [((20, 20), ("1", 1, 1)), ((40, 25), ("2", 1, 2)), ((60, 30), ("3", 2, 3)), ((80, 30), ("4", 2, 3))]),
    "walk_paths": ("LineString", [("who", str), ("number", int)],
                   [([(60, 1), (20, 20)], ("surveyor", 1)), ([(20, 20), (40, 25)], ("surveyor", 1)), ([(60, 1), (60, 30)], ("surveyor", 2)), ([(60, 1), (30, 22), (70, 30)], ("dgps", 1))]),
    "survey_events": ("Point", [("kind", str), ("start_time", str)], [((60, 1), ("pressure adjustment", "08:00")), ((40, 25), ("break", "10:00"))]),
    "drone_positions": ("Point", [("station", int)], [((50, 30), (1,)), ((110, 30), (2,))]),
    "drone_footprints": ("Polygon", [("station", int)], [([(20, 5), (80, 5), (80, 55), (20, 55), (20, 5)], (1,))]),
    "drone_route": ("LineString", [("leg", int)], [([(60, 1), (50, 30), (110, 30)], (1,))]),
    "kayak_tracks": ("LineString", [("kayaker", int)], [([(60, 5), (160, 5)], (1,)), ([(60, 12), (160, 12)], (2,))]),
    "kayak_avoid": ("MultiPolygon", [("velocity_ms", float)], []),
    "temperature_points": ("Point", [("label", str), ("water", str)], [((30, 8), ("T1", "slow, shallow")), ((150, 30), ("T2", "fast, deep"))]),
    "temperature_patches": ("MultiPolygon", [("water", str)], [([(0, 0), (200, 0), (200, 10), (0, 10), (0, 0)], ("slow, shallow",)), ([(0, 10), (200, 10), (200, 50), (0, 50), (0, 10)], ("fast, deep",))]),
}


def _geometry(kind: str, coordinates):
    from osgeo import ogr

    def ring(points):
        return ", ".join(f"{X0 + x} {Y0 + y}" for x, y in points)

    if kind == "Point":
        return ogr.CreateGeometryFromWkt(f"POINT ({X0 + coordinates[0]} {Y0 + coordinates[1]})")
    if kind == "LineString":
        return ogr.CreateGeometryFromWkt(f"LINESTRING ({ring(coordinates)})")
    if kind == "Polygon":
        return ogr.CreateGeometryFromWkt(f"POLYGON (({ring(coordinates)}))")
    return ogr.CreateGeometryFromWkt(f"MULTIPOLYGON ((({ring(coordinates)})))")


def write_gpkg(path: Path, *, plan: bool = True, access=(), walkable=()) -> Path:
    """``survey-plan.gpkg`` as the library writes it: the layers of the access points and of the walkable areas and, with *plan*, the layers of a plan."""
    from osgeo import ogr, osr

    ogr.UseExceptions()
    types = {"Point": ogr.wkbPoint, "LineString": ogr.wkbLineString, "Polygon": ogr.wkbPolygon, "MultiPolygon": ogr.wkbMultiPolygon}
    kinds = {str: ogr.OFTString, int: ogr.OFTInteger, float: ogr.OFTReal}
    system = osr.SpatialReference()
    system.ImportFromEPSG(25832)
    layers = {"access_points": ("Point", [("role", str), ("name", str)], [(xy, (role, f"the {role}")) for role, xy in access]),
              "walkable_areas": ("Polygon", [("name", str)], [(ring, ("dry ground",)) for ring in walkable])}
    if plan:
        layers.update(PLAN)
    path.parent.mkdir(parents=True, exist_ok=True)
    source = ogr.GetDriverByName("GPKG").CreateDataSource(str(path))
    for name, (kind, fields, rows) in layers.items():
        layer = source.CreateLayer(name, system, types[kind])
        for field, python in fields:
            layer.CreateField(ogr.FieldDefn(field, kinds[python]))
        for coordinates, values in rows:
            feature = ogr.Feature(layer.GetLayerDefn())
            feature.SetGeometry(_geometry(kind, coordinates))
            for (field, _python), value in zip(fields, values):
                feature.SetField(field, value)
            layer.CreateFeature(feature)
    source = None                                   # closes the file
    return path


def answer_of(gpkg: Path, files: dict | None = None) -> dict:
    """What ``axqua survey plan`` answers for the plan of :func:`write_gpkg`."""
    return {"gpkg": str(gpkg), "folder": str(gpkg.parent), "layers": {name: len(rows) for name, (_kind, _fields, rows) in PLAN.items()}, "files": files or {}, "skipped": {}, "notes": [],
            "plans": {"ground": {"summary": {"verticals": 4, "one_depth": 1, "two_depths": 1, "three_depths": 2, "surveyors": 2, "walk_length": 320.0, "days": 1, "start": "08:00", "end": "10:40"}, "warnings": []},
                      "kayak": {"summary": {"floats": 2, "length": 100.0, "kayakers": 2, "fixes": 20, "start": "08:00", "end": "08:20"}, "warnings": []}},
            "access": {"access": [X0 + 60.0, Y0 + 1.0], "leave": [X0 + 160.0, Y0 + 1.0], "takeoff": None, "count": 2, "notes": [], "names": {}}, "walkable": {"count": 0, "area": 0.0, "notes": []}}


@pytest.fixture
def project(qgis_app):
    instance = QgsProject.instance()
    instance.clear()
    yield instance
    instance.clear()


def _tree_names(project) -> list[str]:
    group = project.layerTreeRoot().findGroup("Survey plan")
    return [node.name() for node in group.findLayers()] if group is not None else []


def _colors(layer, size: int = 200) -> set:
    """The colors a map draws of *layer*, coarsely: what a person would tell apart."""
    from qgis.core import QgsMapRendererParallelJob, QgsMapSettings
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor

    settings = QgsMapSettings()
    settings.setLayers([layer])
    extent = layer.extent()
    extent.scale(1.3)
    settings.setExtent(extent)
    settings.setDestinationCrs(layer.crs())
    settings.setOutputSize(QSize(size, size))
    settings.setBackgroundColor(QColor("#ffffff"))
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    image = job.renderedImage()
    found = set()
    for x in range(size):
        for y in range(size):
            color = image.pixelColor(x, y)
            found.add((color.red() // 64, color.green() // 64, color.blue() // 64))
    return found


# ---------------------------------------------------------------------------- the layers
def test_the_access_layer_is_put_on_the_map_once_and_edited(project, tmp_path):
    from axqua_plugin.gui import survey_layers

    gpkg = write_gpkg(tmp_path / "survey-plan.gpkg", plan=False)
    layer = survey_layers.show_access(gpkg, ROLES)
    assert layer is not None and layer.isValid() and layer.isEditable() and layer.name() == "Access points (survey plan)"
    assert _tree_names(project) == ["Access points (survey plan)"]
    setup = layer.editorWidgetSetup(layer.fields().indexOf("role"))
    assert setup.type() == "ValueMap" and [list(entry)[0] for entry in setup.config()["map"]] == ["access", "leave", "takeoff"]   # nobody has to spell a role
    assert layer.defaultValueDefinition(layer.fields().indexOf("role")).expression() == "'access'"
    assert survey_layers.show_access(gpkg, ROLES) is layer and len(project.mapLayers()) == 1
    assert list(survey_layers.open_layers(gpkg)) == ["access_points"]

    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(X0 + 60.0, Y0 + 1.0)))
    feature["role"], feature["name"] = "access", "bridge"
    assert layer.addFeature(feature) and layer.isModified()
    assert survey_layers.commit_access(gpkg) == "" and not layer.isEditable()
    from qgis.core import QgsVectorLayer

    stored = QgsVectorLayer(f"{gpkg}|layername=access_points", "as the library reads it", "ogr")
    assert [(feature["role"], feature["name"]) for feature in stored.getFeatures()] == [("access", "bridge")]
    # nothing to save: the layer leaves editing without a question
    survey_layers.show_access(gpkg, ROLES)
    assert layer.isEditable() and survey_layers.commit_access(gpkg) == "" and not layer.isEditable()
    assert survey_layers.show_access(gpkg, ROLES, edit=False) is layer and not layer.isEditable()


def test_the_walkable_areas_are_drawn_below_the_plan_and_saved(project, tmp_path):
    from qgis.core import QgsVectorLayer, QgsWkbTypes

    from axqua_plugin.gui import survey_layers

    gpkg = write_gpkg(tmp_path / "survey-plan.gpkg", access=(("access", (60, 1)),))
    layer = survey_layers.show_walkable(gpkg)
    assert layer is not None and layer.isValid() and layer.isEditable() and layer.name() == "Walkable areas (survey plan)"
    assert QgsWkbTypes.geometryType(layer.wkbType()) == QgsWkbTypes.PolygonGeometry
    assert int(layer.editFormConfig().suppress()) == 1, "a drawn area is stored without a question for its attributes"
    assert survey_layers.show_walkable(gpkg) is layer and len(project.mapLayers()) == 1

    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(X0 + 50, Y0 - 30), QgsPointXY(X0 + 70, Y0 - 30), QgsPointXY(X0 + 70, Y0 + 2), QgsPointXY(X0 + 50, Y0 + 2), QgsPointXY(X0 + 50, Y0 - 30)]]))
    assert layer.addFeature(feature) and survey_layers.commit_walkable(gpkg) == "" and not layer.isEditable()
    stored = QgsVectorLayer(f"{gpkg}|layername=walkable_areas", "as the library reads it", "ogr")
    assert [round(feature.geometry().area()) for feature in stored.getFeatures()] == [640]
    assert len(_colors(layer)) >= 2, "the areas show on the map"

    # the plan and the access points are drawn over the areas, and a new plan leaves them on the map
    survey_layers.show_access(gpkg, ROLES, edit=False)
    survey_layers.show_plan(answer_of(gpkg))
    names = _tree_names(project)
    assert names[0] == "Access points (survey plan)" and names[-1] == "Walkable areas (survey plan)" and len(names) == 11
    assert survey_layers.remove_plan(gpkg) == 9 and _tree_names(project) == ["Access points (survey plan)", "Walkable areas (survey plan)"]
    assert survey_layers.show_walkable(gpkg, edit=False) is layer and not layer.isEditable()


def test_a_file_without_the_layer_gives_no_layer(project, tmp_path):
    from axqua_plugin.gui import survey_layers

    assert survey_layers.show_access(tmp_path / "missing.gpkg", ROLES) is None and not project.mapLayers()
    assert survey_layers.show_walkable(tmp_path / "missing.gpkg") is None and survey_layers.commit_walkable(tmp_path / "missing.gpkg") == ""
    assert survey_layers.commit_access(tmp_path / "missing.gpkg") == "" and survey_layers.remove_plan(tmp_path / "missing.gpkg") == 0
    assert survey_layers.show_plan({}) == [] and survey_layers.show_plan({"gpkg": str(tmp_path / "missing.gpkg")}) == []


def test_the_tool_that_adds_a_point_is_started(project, tmp_path):
    from axqua_plugin.gui import survey_layers

    class Action:
        triggered = 0

        def trigger(self):
            Action.triggered += 1

    class Iface:
        active = None

        def setActiveLayer(self, layer):                # noqa: N802 - QGIS naming
            self.active = layer

        def actionAddFeature(self):                     # noqa: N802 - QGIS naming
            return Action()

    iface = Iface()
    gpkg = write_gpkg(tmp_path / "survey-plan.gpkg", plan=False)
    layer = survey_layers.show_access(gpkg, ROLES, iface=iface)
    assert iface.active is layer and Action.triggered == 1
    assert survey_layers.show_access(gpkg, ROLES, iface=object()) is layer      # a QGIS without that tool is no error


def test_the_plan_is_put_on_the_map_with_its_styles(project, tmp_path):
    from qgis.core import QgsCategorizedSymbolRenderer, QgsMarkerLineSymbolLayer

    from axqua_plugin.gui import survey_layers

    gpkg = write_gpkg(tmp_path / "survey-plan.gpkg", access=(("access", (60, 1)), ("leave", (160, 1))))
    survey_layers.show_access(gpkg, ROLES, edit=False)
    added = survey_layers.show_plan(answer_of(gpkg))
    by_name = {survey_layers._layer_name(layer.source()): layer for layer in added}
    assert len(added) == 9 and "kayak_avoid" not in by_name                    # an empty layer is not put on the map
    # the access points on top, then the verticals; the areas at the bottom
    names = _tree_names(project)
    assert names[:2] == ["Access points (survey plan)", "Velocity verticals"] and names[-1] == "Temperature: patches of water" and len(names) == 10

    points = by_name["flowtracker_points"]
    renderer = points.renderer()
    assert isinstance(renderer, QgsCategorizedSymbolRenderer) and points.labelsEnabled()
    shapes = {category.label(): category.symbol().symbolLayer(0).properties()["name"] for category in renderer.categories()}
    assert shapes == {"surveyor 1, one depth": "circle", "surveyor 1, two depths": "triangle", "surveyor 2, three depths": "square"}
    colors = {category.label().split(",")[0]: category.symbol().symbolLayer(0).strokeColor().name() for category in renderer.categories()}
    assert colors["surveyor 1"] != colors["surveyor 2"]

    walks = by_name["walk_paths"].renderer()
    labels = sorted(category.label() for category in walks.categories())
    assert labels == ["walk of DGPS surveyor 1", "walk of surveyor 1", "walk of surveyor 2"]
    for category in walks.categories():
        symbol = category.symbol()
        assert any(isinstance(symbol.symbolLayer(i), QgsMarkerLineSymbolLayer) for i in range(symbol.symbolLayerCount())), "a walk path shows its direction"
        dashed = symbol.symbolLayer(0).properties()["line_style"] == "dash"
        assert dashed == ("DGPS" in category.label())
    assert sorted(category.label() for category in by_name["survey_events"].renderer().categories()) == ["break", "pressure adjustment"]
    assert sorted(category.label() for category in by_name["temperature_patches"].renderer().categories()) == ["fast, deep water", "slow, shallow water"]
    assert [category.label() for category in by_name["kayak_tracks"].renderer().categories()] == ["drift of kayaker 1", "drift of kayaker 2"]

    # what the map shows: every category draws, in a color of its own
    assert len(_colors(points)) >= 3 and len(_colors(by_name["walk_paths"])) >= 4 and len(_colors(by_name["temperature_patches"])) >= 3

    # a second plan replaces the first on the map, and the access points stay
    again = survey_layers.show_plan(answer_of(gpkg))
    assert len(again) == 9 and len(project.mapLayers()) == 10 and _tree_names(project)[0] == "Access points (survey plan)"
    assert survey_layers.remove_plan(gpkg) == 9 and _tree_names(project) == ["Access points (survey plan)"]


def test_a_single_surveyor_is_not_named_in_the_legend(project, tmp_path):
    from axqua_plugin.gui import survey_layers

    gpkg = write_gpkg(tmp_path / "survey-plan.gpkg")
    answer = answer_of(gpkg)
    answer["layers"] = {"walk_paths": 4, "flowtracker_points": 4}
    added = survey_layers.show_plan(answer)
    assert [layer.name() for layer in added] == ["Walk paths", "Velocity verticals"]
    points = added[1]
    points.setSubsetString("surveyor = 1")
    survey_layers.style(points, "flowtracker_points")
    assert sorted(category.label() for category in points.renderer().categories()) == ["vertical at one depth", "vertical at two depths"]


def test_zoom_shows_the_layers_of_the_plan(project, tmp_path):
    from qgis.core import QgsCoordinateReferenceSystem
    from qgis.gui import QgsMapCanvas

    from axqua_plugin.gui import survey_layers

    canvas = QgsMapCanvas()
    canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:25832"))

    class Iface:
        def mapCanvas(self):                            # noqa: N802 - QGIS naming
            return canvas

    gpkg = write_gpkg(tmp_path / "survey-plan.gpkg")
    added = survey_layers.show_plan(answer_of(gpkg))
    survey_layers.zoom_to(Iface(), added)
    extent = canvas.extent()
    assert extent.contains(QgsPointXY(X0 + 20.0, Y0 + 20.0)) and extent.contains(QgsPointXY(X0 + 199.0, Y0 + 49.0)) and extent.width() < 2000.0
    survey_layers.zoom_to(None, added)
    survey_layers.zoom_to(Iface(), [])
    canvas.deleteLater()


# ---------------------------------------------------------------------------- the window
class _Client:
    """Answers what ``axqua survey`` answers, and keeps what it was asked."""

    def __init__(self, gpkg: Path, *, parameters=None, access=None, walkable=None, last_plan=None, fail=None):
        self.gpkg = Path(gpkg)
        self.calls: list[tuple] = []
        self.fail = fail or {}
        self.access = access or {"access": None, "leave": None, "takeoff": None, "count": 0, "notes": [], "names": {}}
        self.walkable = walkable or {"count": 0, "area": 0.0, "notes": []}
        self.parameters = parameters if parameters is not None else [
            {"name": "zone1", "rule": "zone", "zone": 1, "points": 6, "use": True, "declared": True, "note": "", "limits": [0.02, 0.3]},
            {"name": "zone2", "rule": "zone", "zone": 2, "points": 6, "use": True, "declared": True, "note": "", "limits": [0.05, 0.6]},
            {"name": "zone3", "rule": "zone", "zone": 3, "points": 6, "use": False, "declared": True, "note": "a roughness zone of the zone map that the calibration of the case does not name yet"},
            {"name": "VELOCITY DIFFUSIVITY", "rule": "shear", "zone": None, "points": 6, "use": True, "declared": True, "note": ""}]
        self.last_plan = last_plan
        self.plan_answer: dict = {}
        self.conditions = {"days": [{"date": "2026-06-20", "sunrise_text": "05:12", "sunset_text": "21:17"}], "timezone": "Europe/Berlin",
                           "suggestion": {"start": "07:30", "end": "16:10", "reason": "within daylight"}, "coverage": {"statement": "Surveyors should check the DGPS network coverage <on site>."}, "notes": [], "sources": []}

    def _answer(self, what: str, value):
        if what in self.fail:
            raise self.fail[what]
        return value

    def survey_info(self, case):
        self.calls.append(("info", str(case)))
        return self._answer("info", {"settings": copy.deepcopy(DEFAULTS), "parameters": copy.deepcopy(self.parameters), "rules": dict(RULES), "roles": dict(ROLES), "folder": str(self.gpkg.parent), "gpkg": str(self.gpkg),
                                     "gpkg_exists": self.gpkg.is_file(), "access_layer": "access_points", "access": copy.deepcopy(self.access), "walkable_layer": "walkable_areas", "walkable": copy.deepcopy(self.walkable), "crs_epsg": 25832, "has_zones": True, "built": True,
                                     "result": str(self.gpkg.parent / "r2d.slf"), "result_exists": True, "last_plan": self.last_plan})

    def survey_init(self, case):
        self.calls.append(("init", str(case)))
        return self._answer("init", {"gpkg": str(self.gpkg), "layer": "access_points", "created": True, "roles": dict(ROLES), "crs_epsg": 25832, "access": copy.deepcopy(self.access), "walkable_layer": "walkable_areas", "walkable": copy.deepcopy(self.walkable)})

    def survey_conditions(self, case, settings=None, *, offline=False):
        self.calls.append(("conditions", copy.deepcopy(settings), offline))
        return self._answer("conditions", copy.deepcopy(self.conditions))

    def survey_plan(self, case, settings=None, *, offline=False):
        self.calls.append(("plan", copy.deepcopy(settings), offline))
        return self._answer("plan", copy.deepcopy(self.plan_answer))


class _Ctx:
    def __init__(self, client, iface=None):
        self.client = client
        self.iface = iface
        self.messages: list[tuple] = []

    def warn(self, text):
        self.messages.append(("warn", text))


@pytest.fixture
def planner(project, tmp_path, monkeypatch):
    """``open_planner(**what the client answers)`` gives the window and its client; nothing runs in the background."""
    from axqua_plugin.gui import survey_wizard

    monkeypatch.setattr(survey_wizard, "run_async", lambda title, call, on_success=None, on_error=None, owner=None: _now(call, on_success, on_error))
    opened = []
    monkeypatch.setattr(survey_wizard, "open_file", lambda path: opened.append(path) or True)
    case = tmp_path / "reach.axq-case"
    case.write_text("project: {name: reach}\n", encoding="utf-8")
    windows = []

    def open_planner(*, iface=None, gpkg=None, **answers):
        client = _Client(gpkg or tmp_path / "survey-plan" / "survey-plan.gpkg", **answers)
        window = survey_wizard.SurveyWizard(_Ctx(client, iface), case)
        windows.append(window)
        return window, client

    open_planner.opened = opened
    open_planner.case = case
    yield open_planner
    for window in windows:
        window.deleteLater()


def _now(call, on_success, on_error):
    try:
        answer = call()
    except Exception as exc:  # noqa: BLE001 - what a background task hands to on_error
        if on_error is None:
            raise
        on_error(exc)
    else:
        on_success(answer)


def test_the_window_has_its_tabs_and_reads_the_case(planner):
    from axqua_plugin.gui import survey_wizard

    window, client = planner()
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == list(survey_wizard.TABS) == ["Survey", "Access", "Ground-based", "Airborne", "Kayak", "Temperature", "Time and weather", "Plan"]
    assert client.calls == [("info", str(planner.case))] and window.windowTitle() == "Survey planner: reach.axq-case" and window.status.text() == ""
    # only the kinds that are chosen have their settings open, and Next walks over the others
    assert [window.tabs.isTabEnabled(i) for i in range(8)] == [True, True, True, False, False, False, True, True]
    window.next_button.click()
    window.next_button.click()
    window.next_button.click()
    assert window.tabs.tabText(window.tabs.currentIndex()) == "Time and weather"
    window.back_button.click()
    assert window.tabs.tabText(window.tabs.currentIndex()) == "Ground-based"
    window.kind_boxes["kayak"].setChecked(True)
    assert window.tabs.isTabEnabled(survey_wizard.KIND_TABS["kayak"]) and not window.tabs.isTabEnabled(survey_wizard.KIND_TABS["airborne"])
    window.tabs.setCurrentIndex(0)
    assert not window.back_button.isEnabled() and window.next_button.isEnabled()
    window.tabs.setCurrentIndex(survey_wizard.PLAN_TAB)
    assert window.back_button.isEnabled() and not window.next_button.isEnabled()
    assert not window.model_note.text() and "Planning with r2d.slf" in window.result_note.text()


def test_the_forms_give_back_the_settings_they_were_filled_with(planner):
    window, _client = planner()
    settings, problems = window.read_settings()
    assert problems == []
    expected = copy.deepcopy(DEFAULTS)
    expected["parameters"] = [{"name": "zone1", "rule": "zone", "points": 6, "use": True, "zone": 1}, {"name": "zone2", "rule": "zone", "points": 6, "use": True, "zone": 2},
                              {"name": "zone3", "rule": "zone", "points": 6, "use": False, "zone": 3}, {"name": "VELOCITY DIFFUSIVITY", "rule": "shear", "points": 6, "use": True}]
    assert settings == expected

    # and what a person changes arrives in the document
    for kind in ("airborne", "kayak", "temperature"):
        window.kind_boxes[kind].setChecked(True)
    window.widgets["ground.max_depth"][1].setValue(1.0)
    window.widgets["ground.surveyors"][1].setValue(2)
    window.widgets["ground.velocity_limits"][1].setText("0.5: 1.8; 1.0: 1.2")
    window.widgets["ground.heights_two"][1].setText("0.2; 0.7")
    window.widgets["kayak.kayakers"][1].setValue(1)
    method = window.widgets["temperature.method"][1]
    method.setCurrentIndex(method.findData("probe"))
    window.widgets["time.use_daylight"][1].setChecked(False)
    window.has_date.setChecked(True)
    from qgis.PyQt.QtCore import QDate, QTime

    window.date.setDate(QDate(2026, 6, 20))
    window.has_start.setChecked(True)
    window.start.setTime(QTime(7, 30))
    window.online.setChecked(False)
    window.points_default.setValue(4)
    window.spacing.setValue(12.5)
    window.result_file.setText(" /data/flood.slf ")
    settings, problems = window.read_settings()
    assert problems == [] and settings["kinds"] == ["ground", "airborne", "kayak", "temperature"]
    assert (settings["ground"]["max_depth"], settings["ground"]["surveyors"], settings["ground"]["velocity_limits"], settings["ground"]["heights_two"]) == (1.0, 2, [[0.5, 1.8], [1.0, 1.2]], [0.2, 0.7])
    assert (settings["kayak"]["kayakers"], settings["temperature"]["method"], settings["time"]) == (1, "probe", {**DEFAULTS["time"], "use_daylight": False, "date": "2026-06-20", "start": "07:30"})
    assert (settings["online"], settings["points_per_parameter"], settings["min_spacing"], settings["result"]) == (False, 4, 12.5, "/data/flood.slf")
    assert settings["ground"]["speed_land"] == 3.0 and settings["airborne"] == DEFAULTS["airborne"]


def test_a_field_that_cannot_be_read_is_named_with_its_tab(planner):
    from axqua_plugin.gui import survey_wizard

    window, client = planner()
    window.widgets["ground.speed_against_current"][1].setText("0.3 1.0")
    window.widgets["ground.heights_three"][1].setText("0.2; 0.8")
    _settings, problems = window.read_settings()
    assert [tab for tab, _text in problems] == [survey_wizard.KIND_TABS["ground"]] * 2
    assert "lacks the colon" in problems[0][1] and "3 heights needed, 2 given" in problems[1][1]
    window.tabs.setCurrentIndex(survey_wizard.PLAN_TAB)
    window.plan()
    assert window.tabs.currentIndex() == survey_wizard.KIND_TABS["ground"] and "lacks the colon" in window.status.text()
    assert not [call for call in client.calls if call[0] == "plan"]             # nothing is planned with settings nobody typed

    window.widgets["ground.speed_against_current"][1].setText("0.3: 1.0")
    window.widgets["ground.heights_three"][1].setText("0.2; 0.6; 0.8")
    for box in window.kind_boxes.values():
        box.setChecked(False)
    assert window.read_settings()[1] == [(0, "Choose at least one kind of survey.")]
    window.ask_conditions()
    assert window.tabs.currentIndex() == 0 and not [call for call in client.calls if call[0] == "conditions"]


def test_the_parameter_table(planner):
    from axqua_plugin.compat import CHECKED, UNCHECKED

    window, _client = planner()
    table = window.table
    assert table.rowCount() == 4 and [table.item(row, 1).text() for row in range(4)] == ["zone1", "zone2", "zone3", "VELOCITY DIFFUSIVITY"]
    assert [table.item(row, 0).checkState() == CHECKED for row in range(4)] == [True, True, False, True]
    # the roughness of a zone acts in that zone: neither its rule nor its zone can be changed
    assert not table.cellWidget(0, 2).isEnabled() and not table.cellWidget(0, 3).isEnabled() and table.cellWidget(0, 3).value() == 1
    assert table.cellWidget(3, 2).isEnabled() and table.cellWidget(3, 2).currentData() == "shear" and not table.cellWidget(3, 3).isEnabled()
    assert table.item(0, 5).text() == "of the case" and table.item(2, 5).text().startswith("of the case; a roughness zone of the zone map")
    assert table.cellWidget(0, 5) is None                                      # a parameter of the case cannot be removed
    assert [table.cellWidget(3, 2).itemText(i) for i in range(table.cellWidget(3, 2).count())] == ["inside a roughness zone", "over the wetted model", "in shear zones", "in bends", "along the banks", "in shallow margins"]

    window.points_default.setValue(3)
    assert window.add_parameter_named("  LAW OF BOTTOM FRICTION  ") and table.rowCount() == 5
    assert not window.add_parameter_named("law of bottom friction") and "in the table already" in window.status.text() and table.rowCount() == 5
    assert not window.add_parameter_named("") and table.rowCount() == 5
    rule, zone = table.cellWidget(4, 2), table.cellWidget(4, 3)
    assert rule.currentData() == "everywhere" and not zone.isEnabled() and table.cellWidget(4, 4).value() == 3
    rule.setCurrentIndex(rule.findData("zone"))
    assert zone.isEnabled()
    zone.setValue(2)
    table.item(1, 0).setCheckState(UNCHECKED)
    table.cellWidget(3, 4).setValue(9)
    rows = window.parameter_rows()
    assert rows[4] == {"name": "LAW OF BOTTOM FRICTION", "rule": "zone", "zone": 2, "points": 3, "use": True, "declared": False}
    assert rows[1]["use"] is False and rows[3]["points"] == 9 and rows[3]["zone"] is None
    settings, _problems = window.read_settings()
    assert settings["parameters"][4] == {"name": "LAW OF BOTTOM FRICTION", "rule": "zone", "points": 3, "use": True, "zone": 2}
    table.cellWidget(4, 5).click()                                             # Remove
    assert table.rowCount() == 4 and [row["name"] for row in window.parameter_rows()][-1] == "VELOCITY DIFFUSIVITY"


def test_a_case_that_is_not_ready_says_what_to_do(planner):
    window, _client = planner()
    info = dict(window.info, built=False, result_exists=False, has_zones=False)
    window._show_info(info)
    assert not window.model_note.isHidden() and "not built yet" in window.model_note.text() and "no roughness zones" in window.model_note.text()
    assert "r2d.slf is missing" in window.result_note.text()
    window._show_info(dict(info, built=True))
    assert "no result of a steady run yet" in window.model_note.text()

    from axqua_plugin.core.runner_client import RunnerError

    broken, _client = planner(fail={"info": RunnerError("the case file cannot be read")})
    assert "the case file cannot be read" in broken.status.text() and broken.table.rowCount() == 0


def test_the_access_points_are_set_on_the_map_and_saved(planner, project, tmp_path):
    gpkg = write_gpkg(tmp_path / "survey-plan" / "survey-plan.gpkg", plan=False)
    window, client = planner(gpkg=gpkg)
    assert window.access_label.text() == "No point is set yet." and "<b>leave</b>" in window.roles_label.text()
    window.start_access()
    assert client.calls[-1][0] == "init" and window.set_points.isEnabled() and "Click on the map" in window.status.text()
    layer = project.mapLayersByName("Access points (survey plan)")[0]
    assert layer.isEditable()

    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(X0 + 60.0, Y0 + 1.0)))
    feature["role"] = "access"
    layer.addFeature(feature)
    client.access = {"access": [X0 + 60.0, Y0 + 1.0], "leave": None, "takeoff": None, "count": 1, "notes": [], "names": {}}
    window.save_access()
    assert not layer.isEditable() and layer.featureCount() == 1                # saved before the library reads the file
    assert window.status.text() == "The points are saved." and window.access_label.text() == "access: 700060.0, 5340001.0"

    client.access = {"access": None, "leave": None, "takeoff": None, "count": 0, "notes": [], "names": {}}
    window.save_access()
    assert window.status.text() == "The layer holds no point yet."


def test_the_walkable_areas_are_drawn_on_the_map_and_saved(planner, project, tmp_path):
    gpkg = write_gpkg(tmp_path / "survey-plan" / "survey-plan.gpkg")
    window, client = planner(gpkg=gpkg)
    assert window.walkable_label.text() == "No area is drawn: on land the plan walks along the wetted edge only."
    window.start_walkable()
    assert client.calls[-1][0] == "init" and window.draw_areas.isEnabled() and "Click the corners" in window.status.text()
    layer = project.mapLayersByName("Walkable areas (survey plan)")[0]
    assert layer.isEditable()

    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(X0 + 50, Y0 - 30), QgsPointXY(X0 + 70, Y0 - 30), QgsPointXY(X0 + 70, Y0 + 2), QgsPointXY(X0 + 50, Y0 - 30)]]))
    layer.addFeature(feature)
    client.walkable = {"count": 1, "area": 320.0, "notes": []}
    window.save_walkable()
    assert not layer.isEditable() and layer.featureCount() == 1                # saved before the library reads the file
    assert window.status.text() == "The areas are saved." and window.walkable_label.text().startswith("1 area, 320 m")

    client.walkable = {"count": 0, "area": 0.0, "notes": []}
    window.save_walkable()
    assert window.status.text() == "The layer holds no area yet." and window.walkable_label.text().startswith("No area is drawn")

    # an area that is still being drawn is saved before the plan is made, and stays below the plan on the map
    window.start_walkable()
    layer.addFeature(feature)
    client.plan_answer = dict(answer_of(gpkg), walkable={"count": 2, "area": 640.0, "notes": []})
    window.plan()
    assert not layer.isEditable() and layer.featureCount() == 2 and window.walkable_label.text().startswith("2 areas, 640 m")
    names = _tree_names(project)
    assert names[0] == "Access points (survey plan)" and names[-1] == "Walkable areas (survey plan)" and len(names) == 11


def test_a_layer_that_cannot_be_made_is_reported(planner, tmp_path):
    from axqua_plugin.core.runner_client import RunnerError

    window, _client = planner()                                                # the client names a file that does not exist
    window.start_access()
    assert window.status.text() == "The layer of the access points could not be opened in QGIS." and window.set_points.isEnabled()
    window.start_walkable()
    assert window.status.text() == "The layer of the walkable areas could not be opened in QGIS." and window.draw_areas.isEnabled()
    failing, _client = planner(fail={"init": RunnerError("the folder of the case cannot be written")})
    failing.start_access()
    assert "cannot be written" in failing.status.text() and failing.set_points.isEnabled()


def test_daylight_weather_and_coverage_are_looked_up(planner):
    from axqua_plugin.core.runner_client import RunnerError

    window, client = planner()
    assert not window.use_start.isEnabled() and window.online.isChecked()
    window.ask_conditions()
    call = client.calls[-1]
    assert call[0] == "conditions" and call[2] is False and call[1]["kinds"] == ["ground"]
    text = window.weather_label.text()
    assert "2026-06-20: sunrise 05:12, sunset 21:17" in text and "Suggested working day: 07:30 to 16:10" in text
    assert "&lt;on site&gt;" in text                                           # what a service answers is text, not markup
    assert window.use_start.isEnabled() and window.look_up.isEnabled() and not window.has_start.isChecked()
    window.use_suggested_start()
    assert window.has_start.isChecked() and window.start.time().toString("HH:mm") == "07:30" and window.read_settings()[0]["time"]["start"] == "07:30"

    window.online.setChecked(False)
    window.ask_conditions()
    assert client.calls[-1][2] is True and client.calls[-1][1]["online"] is False   # nothing is looked up on the internet

    client.conditions = {"suggestion": {"reason": "no survey day is set"}}
    window.ask_conditions()
    assert not window.use_start.isEnabled()
    client.fail["conditions"] = RunnerError("the weather service does not answer")
    window.ask_conditions()
    assert "does not answer" in window.status.text() and window.look_up.isEnabled()


def test_planning_sends_the_forms_and_shows_the_plan(planner, project, tmp_path):
    from axqua_plugin.gui import survey_wizard

    gpkg = write_gpkg(tmp_path / "survey-plan" / "survey-plan.gpkg", access=(("access", (60, 1)), ("leave", (160, 1))))
    folder = gpkg.parent
    for name in ("survey-map-ground.pdf", "survey-factsheet-ground.odt", "survey-map-kayak.png"):
        (folder / name).write_bytes(b"x")
    files = {"ground": {"map": str(folder / "survey-map-ground.png"), "map_pdf": str(folder / "survey-map-ground.pdf"), "factsheet": str(folder / "survey-factsheet-ground.odt")},
             "kayak": {"map": str(folder / "survey-map-kayak.png"), "factsheet": str(folder / "survey-factsheet-kayak.odt")}}
    window, client = planner(gpkg=gpkg)
    client.plan_answer = answer_of(gpkg, files)
    window.kind_boxes["kayak"].setChecked(True)
    window.widgets["ground.surveyors"][1].setValue(2)
    window.tabs.setCurrentIndex(0)
    window.plan_button.click()

    call = client.calls[-1]
    assert call[0] == "plan" and call[2] is False and call[1]["kinds"] == ["ground", "kayak"] and call[1]["ground"]["surveyors"] == 2
    assert window.tabs.currentIndex() == survey_wizard.PLAN_TAB and window.plan_button.isEnabled() and not window.planning
    text = window.plan_text.toPlainText()
    assert "Ground-based velocimetry (wading): 4 verticals (1 at one depth, 1 at two, 2 at three), 2 surveyors with a probe" in text and "Kayak drifter velocimetry: 2 drifts" in text
    assert window.status.text() == "Planned: Ground-based velocimetry (wading), Kayak drifter velocimetry."
    assert window.access_label.text().splitlines() == ["access: 700060.0, 5340001.0", "leave: 700160.0, 5340001.0"]

    # the maps and factsheets: a button opens the file, and a file that is not there has no button to press
    assert not window.files_box.isHidden() and set(window.file_buttons) == {("ground", "map"), ("ground", "factsheet"), ("kayak", "map"), ("kayak", "factsheet")}
    assert window.file_buttons[("ground", "map")].isEnabled() and not window.file_buttons[("kayak", "factsheet")].isEnabled()
    window.file_buttons[("ground", "map")].click()
    window.file_buttons[("ground", "factsheet")].click()
    assert planner.opened == [str(folder / "survey-map-ground.pdf"), str(folder / "survey-factsheet-ground.odt")]

    # the plan is on the map, below the access points, which are not left in editing
    names = _tree_names(project)
    assert names[0] == "Access points (survey plan)" and "Velocity verticals" in names and "Walk paths" in names and len(names) == 10
    assert not project.mapLayersByName("Access points (survey plan)")[0].isEditable()

    # planning again takes the layers of the old plan off the map before the library replaces them in the file
    on_the_map = []
    real = client.survey_plan

    def watching(case, settings=None, *, offline=False):
        on_the_map.append(len(project.mapLayers()))
        return real(case, settings, offline=offline)

    client.survey_plan = watching
    window.add_to_map.setChecked(False)
    window.online.setChecked(False)
    window.plan()
    assert on_the_map == [1] and client.calls[-1][2] is True and len(project.mapLayers()) == 1


def test_a_plan_that_fails_says_why(planner):
    from axqua_plugin.core.runner_client import RunnerError

    window, client = planner(fail={"plan": RunnerError("The survey cannot be planned: no water of the result can be waded.")})
    window.plan()
    assert "no water of the result can be waded" in window.plan_text.toPlainText() and window.status.text() == "The survey could not be planned."
    assert window.plan_button.isEnabled() and not window.planning and window.files_box.isHidden()
    client.fail.clear()
    client.plan_answer = {"plans": {}, "skipped": {"ground": {"title": "Ground-based velocimetry (wading)", "reason": "no access point is set", "remedy": "Set one on the tab Access."}}}
    window.plan()
    assert window.status.text() == "Nothing could be planned; the reasons are listed above."
    assert window.plan_text.toPlainText() == "Not planned: Ground-based velocimetry (wading): no access point is set. Set one on the tab Access."


def test_a_plan_that_fails_leaves_the_plan_before_on_the_map(planner, project, tmp_path):
    from axqua_plugin.core.runner_client import RunnerError
    from axqua_plugin.gui import survey_layers

    gpkg = write_gpkg(tmp_path / "survey-plan" / "survey-plan.gpkg", access=[("access", (X0 + 60.0, Y0 + 1.0))])
    window, client = planner(gpkg=gpkg)
    client.plan_answer = answer_of(gpkg)
    window.plan()
    before = sorted(survey_layers.shown_plan(gpkg))
    assert len(before) == 9
    client.fail["plan"] = RunnerError("The survey cannot be planned: the working day starts at 22:00, and the sun sets at 21:17.")
    window.plan()
    assert sorted(survey_layers.shown_plan(gpkg)) == before, "the layers of the plan before are back on the map"
    assert window.status.text() == "The survey could not be planned. The map shows the plan before." and "sun sets at 21:17" in window.plan_text.toPlainText()
    assert survey_layers.show_plan({"gpkg": str(gpkg)}, names=["flowtracker_points", "no_such_layer"])[0].name() == "Velocity verticals"
    # without a plan on the map nothing is put there
    survey_layers.remove_plan(gpkg)
    window.plan()
    assert survey_layers.shown_plan(gpkg) == [] and window.status.text() == "The survey could not be planned."


def test_the_last_plan_of_the_case_is_offered_again(planner, tmp_path):
    folder = tmp_path / "survey-plan"
    folder.mkdir()
    (folder / "survey-factsheet-ground.odt").write_bytes(b"x")
    last = {"made": "2026-11-14 16:02", "kinds": ["ground"], "files": {"ground": {"map": str(folder / "survey-map-ground.png"), "factsheet": str(folder / "survey-factsheet-ground.odt")}}}
    window, _client = planner(last_plan=last)
    assert window.plan_text.toPlainText() == "The last plan was made on 2026-11-14 16:02: Ground-based velocimetry (wading)."
    assert not window.files_box.isHidden() and window.file_buttons[("ground", "factsheet")].isEnabled() and not window.file_buttons[("ground", "map")].isEnabled()


def test_help_opens_the_section_of_the_survey_planner(planner, monkeypatch):
    from qgis.PyQt.QtWidgets import QPushButton

    from axqua_plugin.gui import help as help_pages

    asked = []
    monkeypatch.setattr(help_pages, "open_window_help", lambda key: asked.append(key))
    window, _client = planner()
    [button] = [button for button in window.findChildren(QPushButton) if button.text() == "Help"]
    button.click()
    assert asked == ["survey-wizard"]


# ---------------------------------------------------------------------------- the box on the tab
class _Project:
    def __init__(self, case=None):
        self.case = case

    def active_case_path(self):
        return self.case


def test_the_box_opens_one_window_per_case(planner, monkeypatch, tmp_path):
    from axqua_plugin.gui import survey_box

    client = _Client(tmp_path / "survey-plan.gpkg")
    ctx = _Ctx(client)
    ctx.project = _Project()
    box = survey_box.SurveyBox(ctx)
    assert box.title() == "Survey planner" and box.open_button.text() == "Survey planner..."
    assert box.open_wizard() is None and ctx.messages == [("warn", "Add a case on the tab Case Setup first.")]

    ctx.project.case = str(planner.case)
    first = box.open_wizard()
    assert first is not None and first.case == planner.case and first.isVisible() and not first.isModal()   # the points are set on the map while it is open
    assert box.open_wizard() is first and [call[0] for call in client.calls] == ["info"]
    other = tmp_path / "other.axq-case"
    other.write_text("project: {}\n", encoding="utf-8")
    ctx.project.case = str(other)
    second = box.open_wizard()
    assert second is not first and second.case == other and not first.isVisible()
    second.close()
    third = box.open_wizard()
    assert third is not second and third.isVisible()                           # a window that was closed reads the case again
    third.close()
    box.deleteLater()


def test_the_calibration_tab_starts_with_the_survey_planner(qgis_app, monkeypatch):
    import sys

    from qgis.PyQt.QtWidgets import QGroupBox

    from axqua_plugin.core import runner_client, tasks
    from axqua_plugin.gui import configuration_tab
    from axqua_plugin.gui import dock as dock_module
    from axqua_plugin.gui.survey_box import SurveyBox

    def no_axqua(*args, **kwargs):
        raise AssertionError("this test runs no axqua")

    class Iface:
        def messageBar(self):                           # noqa: N802 - QGIS naming
            class Bar:
                def pushMessage(self, *args, **kwargs):  # noqa: N802 - QGIS naming
                    pass
            return Bar()

        def mainWindow(self):                           # noqa: N802 - QGIS naming
            return None

    monkeypatch.setattr(runner_client.subprocess, "run", no_axqua)
    monkeypatch.setattr(runner_client.subprocess, "Popen", no_axqua)
    # the panel reads no list of cases from the settings of the QGIS that runs the tests, writes none, and starts nothing in the background
    monkeypatch.setattr(dock_module, "_stored_cases", lambda: "")
    monkeypatch.setattr(dock_module.AxquaDock, "_remember_cases", lambda self: None)
    started, before = [], set(tasks._IN_FLIGHT)
    real = tasks.run_async                              # kept: the task module is one of the modules below, and is replaced like them
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("axqua_plugin") and getattr(module, "run_async", None) is real:
            monkeypatch.setattr(module, "run_async", lambda title, work=None, **kwargs: started.append(title))
    monkeypatch.setattr(configuration_tab.help_pages, "ask", lambda client: None)
    panel = dock_module.AxquaDock(Iface())
    page = panel.page("calibration")
    assert isinstance(page.survey, SurveyBox) and page.survey.ctx is panel.ctx
    assert started and tasks._IN_FLIGHT <= before       # what the panel asks at its start stayed in this list
    boxes = [box for box in page.findChildren(QGroupBox) if box.title() in ("Survey planner", "Validation in another flow situation")]
    titles = [box.title() for box in sorted(boxes, key=lambda box: box.mapTo(page, box.rect().topLeft()).y())]
    assert titles[0] == "Survey planner"                                       # the order of the work: plan the survey, measure, calibrate, validate
    panel.deleteLater()
