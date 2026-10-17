"""The QGIS-in-the-loop tests: widgets, layers, layouts, and the threading rules.

The rest of the suite tests the plugin's pure logic, which is most of it. What it cannot
see is the class of defect that only exists because this is a Qt application - a
subprocess run from a constructor, a callback delivered into a deleted widget, a print
layout whose items are not where the code says they are. Those are precisely the defects
this file exists for, and every one of them was a real bug here before it was a test.

PyQGIS is not a pip install, so CI runs without it and these skip. Locally::

    /usr/bin/python3 -m pytest qgis_plugin/tests -q

The QGIS build a developer has is rarely the one the plugin targets, so anything that
depends on a specific QGIS version asks the object rather than assuming, exactly as
``compat`` does.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("qgis.core", reason="PyQGIS is not importable in this interpreter")

from qgis.core import QgsApplication, QgsProject  # noqa: E402

from axqua_plugin.core import result_loader, runner_client  # noqa: E402


@pytest.fixture(scope="session")
def qgis_app():
    """One QGIS application for the session. Creating a second one aborts the process."""
    app = QgsApplication.instance()
    if app is None:
        app = QgsApplication([], False)
        QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
        app.initQgis()
    yield app


@pytest.fixture(autouse=True)
def _cases_not_in_the_settings_of_qgis(monkeypatch):
    """The panel keeps its list of cases in the settings of QGIS. In a test the list
    starts empty and what the panel would store is kept on the dock, so that no test
    reads or writes the settings file of the QGIS that runs the tests."""
    from axqua_plugin.core import project as project_io
    from axqua_plugin.gui import dock as dock_module

    monkeypatch.setattr(dock_module, "_stored_cases", lambda: "")
    monkeypatch.setattr(dock_module.AxquaDock, "_remember_cases",
                        lambda self: setattr(self, "remembered",
                                             project_io.as_settings(self.ctx.project)))


@pytest.fixture
def project(qgis_app):
    """A clean project per test - layers left behind would leak between them."""
    instance = QgsProject.instance()
    instance.clear()
    yield instance
    instance.clear()


class FakeMessageBar:
    def __init__(self):
        self.messages = []

    def pushMessage(self, *args, **kwargs):      # noqa: N802 - QGIS naming
        self.messages.append((args, kwargs))


class FakeIface:
    """Just enough of ``iface`` for the dock to be built and talked to."""

    def __init__(self):
        self._bar = FakeMessageBar()
        self._window = None

    def messageBar(self):                        # noqa: N802 - QGIS naming
        return self._bar

    def mainWindow(self):                        # noqa: N802 - QGIS naming
        return None

    def activeLayer(self):                       # noqa: N802 - QGIS naming
        return None                              # nothing is selected in the layer panel

    def mapCanvas(self):                         # noqa: N802 - QGIS naming
        from qgis.gui import QgsMapCanvas
        if self._window is None:
            self._window = QgsMapCanvas()
        return self._window


# --------------------------------------------------------------- the threading rule


def test_building_the_dock_runs_no_subprocess_on_the_gui_thread(qgis_app, monkeypatch):
    """The one defect this file was written for.

    ``initGui`` builds this dock, and QGIS builds plugins during startup with the splash
    screen up. The executable probe used to run right here, synchronously: seconds of
    frozen QGIS for every user on every launch, and a minute and a half when one of the
    candidate paths was on an unreachable mount. The probe still happens - it has to -
    but it must happen on a task thread.
    """
    calls = []

    def record(*args, **kwargs):
        calls.append(threading.current_thread() is threading.main_thread())
        raise AssertionError("no subprocess should be needed for this")

    monkeypatch.setattr(runner_client.subprocess, "run", record)

    from axqua_plugin.gui.dock import AxquaDock
    dock = AxquaDock(FakeIface())
    try:
        assert True not in calls, (
            "axqua was run on the GUI thread while the dock was being constructed")
    finally:
        dock.deleteLater()


def test_a_task_whose_owner_is_gone_does_not_call_back(qgis_app):
    """A dock closed while a call is in flight must not be called into afterwards.

    Without the owner check this raises ``RuntimeError: wrapped C/C++ object has been
    deleted`` from inside a Qt slot, where nothing can catch it and QGIS reports it as a
    plugin crash.
    """
    import sip
    from qgis.PyQt.QtWidgets import QWidget

    from axqua_plugin.core.tasks import RunnerTask

    owner = QWidget()
    delivered = []
    task = RunnerTask("test", lambda: 1, on_success=delivered.append, owner=owner)
    task.run()
    sip.delete(owner)
    task.finished(True)                       # would raise if the guard were absent
    assert delivered == []


def test_cancelling_everything_leaves_nothing_in_flight(qgis_app):
    from axqua_plugin.core import tasks

    task = tasks.run_async("test", lambda: 1)
    assert task in tasks._IN_FLIGHT
    tasks.cancel_all()
    assert not tasks._IN_FLIGHT


# ------------------------------------------------------------------------- layers


def _write_mesh(folder: Path) -> Path:
    """A two-triangle SMS 2DM mesh, which MDAL reads without any extra driver."""
    path = folder / "reach.2dm"
    path.write_text(
        "MESH2D\n"
        "E3T 1 1 2 3 1\n"
        "E3T 2 2 4 3 1\n"
        "ND 1 0.0 0.0 1.0\n"
        "ND 2 10.0 0.0 1.5\n"
        "ND 3 0.0 10.0 2.0\n"
        "ND 4 10.0 10.0 2.5\n",
        encoding="utf-8")
    return path


def test_a_mesh_result_loads_into_its_own_job_group(project, tmp_path):
    mesh = _write_mesh(tmp_path)
    results = result_loader.JobResults(
        job_id="JOB-1", root=tmp_path,
        items=[result_loader.ResultItem(name="reach", path=mesh, kind="mesh")])
    added = result_loader.ResultLoader(project).load(results)
    assert len(added) == 1
    group = project.layerTreeRoot().findGroup(result_loader.GROUP_NAME)
    assert group is not None and group.findGroup("JOB-1") is not None


def test_a_result_without_a_crs_gets_the_one_of_its_case(project, tmp_path):
    """A mesh file names no CRS. Without one the reach does not line up with a base map
    unless the project happens to use the same system."""
    mesh = _write_mesh(tmp_path)
    results = result_loader.JobResults(
        job_id="JOB-1", root=tmp_path, crs_epsg=25832,
        items=[result_loader.ResultItem(name="reach", path=mesh, kind="mesh")])
    (layer,) = result_loader.ResultLoader(project).load(results)
    assert layer.crs().authid() == "EPSG:25832"


def test_a_result_of_a_job_without_a_crs_loads_as_before(project, tmp_path):
    mesh = _write_mesh(tmp_path)
    results = result_loader.JobResults(
        job_id="JOB-1", root=tmp_path,
        items=[result_loader.ResultItem(name="reach", path=mesh, kind="mesh")])
    (layer,) = result_loader.ResultLoader(project).load(results)
    assert not layer.crs().isValid()


def test_loading_the_same_result_twice_does_not_duplicate_it(project, tmp_path):
    """Pressing *Load results* again is the natural thing to do when a run has moved on,
    and it used to leave the user deleting duplicate layers by hand."""
    mesh = _write_mesh(tmp_path)
    results = result_loader.JobResults(
        job_id="JOB-1", root=tmp_path,
        items=[result_loader.ResultItem(name="reach", path=mesh, kind="mesh")])
    loader = result_loader.ResultLoader(project)
    assert len(loader.load(results)) == 1
    assert loader.load(results) == []              # nothing added the second time
    assert len(loader.reused) == 1
    group = project.layerTreeRoot().findGroup(result_loader.GROUP_NAME).findGroup("JOB-1")
    assert len(group.findLayers()) == 1


def test_a_mesh_variable_really_is_styled(project, tmp_path):
    """The headline feature, and it never once worked.

    ``datasetGroupsIndexes()`` returns ints while ``datasetGroupMetadata()`` wants a
    ``QgsMeshDatasetIndex``, so every styling attempt raised TypeError, was caught by
    the guard that exists to keep an unstyled layer loadable, and was reported as
    "default styling failed" - a warning nobody reads on a layer that did appear.
    """
    mesh = _write_mesh(tmp_path)
    results = result_loader.JobResults(
        job_id="JOB-1", root=tmp_path,
        items=[result_loader.ResultItem(name="bed", path=mesh, kind="mesh",
                                        style="water-depth",
                                        variable="Bed Elevation")])
    loader = result_loader.ResultLoader(project)
    added = loader.load(results)
    assert loader.warnings == []
    assert added[0].rendererSettings().activeScalarDatasetGroup() == 0


def _result_layer(folder: Path, steps: int = 1):
    """The fixture mesh with a water depth and a velocity, as a result carries them,
    at *steps* time steps. The values grow from step to step, so that the picture of
    each step is another one."""
    from qgis.core import QgsMeshLayer

    folder.mkdir(parents=True, exist_ok=True)
    layer = QgsMeshLayer(str(_write_mesh(folder)), "steady result", "mdal")
    for name, rows in (("WATER DEPTH", ((0.2,), (0.6,), (0.9,), (1.4,))),
                       ("SCALAR VELOCITY", ((0.5,), (1.0,), (1.5,), (2.0,))),
                       ("VELOCITY", ((0.5, 0.0), (1.0, 0.0), (0.0, 1.5), (1.2, 1.6)))):
        kind = "VEC" if len(rows[0]) == 2 else "SCL"
        path = folder / f"{name.lower().replace(' ', '-')}.dat"
        frames = "".join(
            f"TS 0 {float(step)}\n" + "".join(
                " ".join(f"{value * (step + 1) / steps:.4f}" for value in row) + "\n"
                for row in rows)
            for step in range(steps))
        path.write_text("DATASET\nOBJTYPE \"mesh2d\"\nBEG" + kind + "\nND 4\nNC 2\n"
                        f"NAME \"{name}\"\n" + frames + "ENDDS\n", encoding="utf-8")
        assert layer.addDatasets(str(path)), name
    return layer


def _picture(layer, when=None) -> bytes:
    """What a map draws of *layer*: without a time, or for the time of one step."""
    from qgis.core import QgsMapRendererParallelJob, QgsMapSettings
    from qgis.PyQt.QtCore import QSize

    settings = QgsMapSettings()
    settings.setLayers([layer])
    settings.setExtent(layer.extent())
    settings.setOutputSize(QSize(160, 160))
    if when is not None:
        settings.setIsTemporal(True)
        settings.setTemporalRange(when)
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    image = job.renderedImage()
    return bytes(image.constBits().asarray(image.sizeInBytes()))


def test_a_loaded_result_is_shown_at_its_last_time_step(project, tmp_path, qgis_app):
    """QGIS draws the first time step of a mesh layer. Of a steady simulation that is
    the initial state, with standing water and no velocity: it looks like a result.
    Pictures are compared here, because the defect was invisible to every test that
    only asked the layer what it was set to."""
    from qgis.core import QgsMeshRendererVectorSettings as Vectors

    from axqua_plugin.compat import enum_value

    layer = _result_layer(tmp_path / "three", steps=3)
    result_loader.apply_map_style(layer, "depth-arrows")
    group = layer.rendererSettings().activeScalarDatasetGroup()
    # as a result file without a date in its header has it when a plugin adds it
    from qgis.PyQt.QtCore import QDateTime
    layer.temporalProperties().setReferenceTime(
        QDateTime(), layer.dataProvider().temporalCapabilities())
    assert not layer.temporalProperties().referenceTime().isValid()

    # a movie selects each frame by its time, also in a result that names no date
    frames = [_picture(layer, result_loader.time_range_of(layer, group, step))
              for step in range(3)]
    assert len(set(frames)) == 3
    assert layer.temporalProperties().referenceTime().isValid()

    assert _picture(layer) == frames[0]              # what QGIS shows by itself
    assert result_loader.show_last_time_step(layer) is True
    assert _picture(layer) == frames[2]              # the end of the run
    assert layer.staticScalarDatasetIndex().dataset() == 2
    assert layer.staticVectorDatasetIndex().dataset() == 2
    # ... with the style it had, and still selectable by time
    drawn = layer.rendererSettings()
    assert drawn.activeScalarDatasetGroup() == group
    assert drawn.vectorSettings(drawn.activeVectorDatasetGroup()).symbology() == \
        enum_value(Vectors, "Symbology.Arrows", "Arrows")
    assert _picture(layer, result_loader.time_range_of(layer, group, 1)) == frames[1]

    # a result with one time step has nothing to select
    assert result_loader.show_last_time_step(_result_layer(tmp_path / "one")) is False


def test_every_way_of_loading_a_result_shows_its_last_time_step(project, tmp_path,
                                                                monkeypatch):
    """The loader does it, so that the job list, the Processing algorithm and whatever
    loads results later cannot forget it. A layer that is loaded again is read anew
    first: the run may have written more time steps since."""
    mesh = _write_mesh(tmp_path)
    results = result_loader.JobResults(
        job_id="JOB-1", root=tmp_path,
        items=[result_loader.ResultItem(name="reach", path=mesh, kind="mesh")])
    asked = []
    monkeypatch.setattr(result_loader, "show_last_time_step",
                        lambda layer: asked.append(layer.name()) or True)
    loader = result_loader.ResultLoader(project)
    (layer,) = loader.load(results)
    assert asked == ["reach"] and loader.at_last_step == 1
    reloaded = []
    monkeypatch.setattr(type(layer), "reload", lambda self: reloaded.append(self.name()))
    assert loader.load(results) == [] and loader.reused == [layer]
    assert reloaded == ["reach"] and asked == ["reach", "reach"]

    # where QGIS does not take the step, the user is told what the map shows
    monkeypatch.setattr(result_loader, "show_last_time_step", lambda layer: False)
    several = _result_layer(tmp_path / "several", steps=2)
    result_loader.apply_map_style(several, "depth-arrows")
    loader._last_step(several, results.items[0])
    assert any("first time step" in warning for warning in loader.warnings)

    source = (Path(result_loader.__file__).resolve().parents[1] / "processing" /
              "algorithms" / "import_results.py").read_text(encoding="utf-8")
    assert "ResultLoader(" in source and "at_last_step" in source


def test_no_module_calls_a_function_the_mesh_layer_does_not_have():
    """``setStaticScalarDatasetIndex`` is in the C++ interface of QGIS and not in the
    Python one of 3.44. The movie export called it and stopped at its first frame; the
    tests did not see it, because none of them rendered a frame."""
    plugin = Path(result_loader.__file__).resolve().parents[1]
    calls = [str(path.relative_to(plugin)) for path in plugin.rglob("*.py")
             if ".setStatic" in path.read_text(encoding="utf-8")]
    assert calls == []


def test_a_map_style_draws_arrows_or_streamlines_over_the_water_depth(project, tmp_path):
    """The predefined map styles: the velocity as arrows or streamlines, colored by its
    magnitude, on top of the water depth or of the velocity magnitude."""
    from qgis.core import QgsMeshRendererVectorSettings as Vectors

    from axqua_plugin.compat import enum_value
    from axqua_plugin.core import styles

    layer = _result_layer(tmp_path)
    index = {result_loader.group_metadata(layer, i).name(): i
             for i in layer.datasetGroupsIndexes()}
    streamlines = enum_value(Vectors, "Symbology.Streamlines", "Streamlines")
    arrows = enum_value(Vectors, "Symbology.Arrows", "Arrows")
    assert list(styles.MAP_STYLES)[0] == "depth-streamlines"   # what the tab offers first

    assert result_loader.apply_map_style(layer, "depth-streamlines", min_depth=0.05) == []
    drawn = layer.rendererSettings()
    assert drawn.activeScalarDatasetGroup() == index["WATER DEPTH"]
    assert drawn.activeVectorDatasetGroup() == index["VELOCITY"]
    vectors = drawn.vectorSettings(index["VELOCITY"])
    assert vectors.symbology() == streamlines
    assert vectors.streamLinesSettings().seedingDensity() == styles.STREAMLINE_DENSITY
    # the lines are colored by the velocity magnitude, up to the fastest value. This
    # never happened before a real result was drawn: the member that switches it on
    # was looked for on the wrong class, and every vector was black.
    from qgis.core import QgsInterpolatedLineColor as LineColor
    by_ramp = enum_value(LineColor, "ColoringMethod.ColorRamp", "ColorRamp")
    assert vectors.coloringMethod() == by_ramp
    assert vectors.colorRampShader().maximumValue() == pytest.approx(2.0)
    # no vector where the water stands: a dry node would get an arrow otherwise
    assert vectors.filterMin() == styles.VECTOR_MINIMUM
    depth = drawn.scalarSettings(index["WATER DEPTH"]).colorRampShader()
    assert depth.shade(0.02)[4] == 0 and depth.shade(0.5)[4] == 255   # shallow: clear

    result_loader.apply_map_style(layer, "depth-arrows")
    vectors = layer.rendererSettings().vectorSettings(index["VELOCITY"])
    assert vectors.symbology() == arrows and vectors.coloringMethod() == by_ramp
    # on a grid of the map, so that the number of arrows does not follow the mesh
    assert vectors.isOnUserDefinedGrid()
    assert vectors.userGridCellWidth() == styles.ARROW_SPACING

    result_loader.apply_map_style(layer, "velocity-streamlines")
    drawn = layer.rendererSettings()
    assert drawn.activeScalarDatasetGroup() == index["SCALAR VELOCITY"]
    vectors = drawn.vectorSettings(index["VELOCITY"])
    assert vectors.symbology() == streamlines
    # over the velocity magnitude the lines are dark: the fill is the magnitude
    assert vectors.coloringMethod() != by_ramp
    assert vectors.color().name() == styles.VECTOR_COLOR

    # a velocity above the cap is drawn up to the cap, and the user is told
    (note,) = result_loader.apply_map_style(layer, "velocity-arrows", velocity_cap=1.0)
    assert "capped at 1.0 m/s" in note


def test_a_layer_that_is_no_result_gets_a_sentence_and_no_style(project, tmp_path):
    from qgis.core import QgsMeshLayer, QgsVectorLayer

    bare = QgsMeshLayer(str(_write_mesh(tmp_path)), "geometry", "mdal")
    with pytest.raises(ValueError, match="has no velocity"):
        result_loader.apply_map_style(bare, "depth-arrows")
    points = QgsVectorLayer("Point?crs=EPSG:25832", "points", "memory")
    for other in (points, None):
        with pytest.raises(ValueError, match="mesh layers"):
            result_loader.apply_map_style(other, "depth-arrows")
    with pytest.raises(ValueError, match="no map style"):
        result_loader.apply_map_style(bare, "rainbow")


def test_the_map_styles_are_one_choice_and_one_button_on_the_qgis_page(dock, tmp_path):
    page = dock.page("postprocessing", "qgis")
    choice = page.style_choice
    assert [choice.itemText(i) for i in range(choice.count())] == [
        "Water depth with streamlines", "Water depth with velocity arrows",
        "Velocity magnitude with streamlines", "Velocity magnitude with arrows"]
    page.apply_style()                              # no layer is selected in QGIS
    assert "Select a result layer" in page.style_status.text()

    layer = _result_layer(tmp_path)
    dock.ctx.iface.activeLayer = lambda: layer
    page.apply_style()
    assert page.style_status.text().startswith(
        "Water depth with streamlines: applied to the layer steady result.")
    assert layer.rendererSettings().activeVectorDatasetGroup() >= 0


def test_the_velocity_fill_is_transparent_where_nothing_moves(qgis_app):
    """Dry ground has a velocity of exactly zero. Filled, it painted the floodplain in
    the brightest color of the ramp."""
    from axqua_plugin.core import styles

    shader = styles.velocity_scalar_settings(styles.VelocityStyle(maximum=2.0)).colorRampShader()
    items = shader.colorRampItemList()
    assert items[0].value == styles.VELOCITY_TRANSPARENT_BELOW and items[0].color.alpha() == 0
    assert items[1].color.alpha() == 255 and abs(items[-1].value - 2.0) < 1e-9
    hit, _red, _green, _blue, alpha = shader.shade(0.0)
    assert hit and alpha == 0                       # dry
    assert shader.shade(0.5)[4] == 255              # flowing


def test_the_dataset_maximum_comes_from_the_file(project, tmp_path):
    """A silent ``or 1.0`` capped a 6 m river's color scale at 1 m."""
    from qgis.core import QgsMeshLayer
    layer = QgsMeshLayer(str(_write_mesh(tmp_path)), "reach", "mdal")
    maximum, why = result_loader._dataset_maximum(layer, 0)
    assert why == ""
    assert maximum == pytest.approx(2.5)       # the highest node in the fixture


def test_a_variable_that_is_not_in_the_mesh_is_reported_not_guessed(project, tmp_path):
    mesh = _write_mesh(tmp_path)
    results = result_loader.JobResults(
        job_id="JOB-1", root=tmp_path,
        items=[result_loader.ResultItem(name="depth", path=mesh, kind="mesh",
                                        style="water-depth", variable="WATER DEPTH")])
    loader = result_loader.ResultLoader(project)
    loader.load(results)
    assert any("WATER DEPTH" in w for w in loader.warnings)


# ------------------------------------------------------------------------ widgets


class _Context:
    """The slice of PluginContext the widgets under test actually touch."""

    def __init__(self):
        self.messages = []
        self.client = None
        self.min_depth = 0.05
        self.velocity_cap = 5.0

    def info(self, text):
        self.messages.append(("info", text))

    def warn(self, text):
        self.messages.append(("warn", text))

    def error(self, text):
        self.messages.append(("error", text))

    def client_or_warn(self):
        return None


def test_an_untouched_option_form_sends_nothing(qgis_app):
    """A two-state check box has no way to say "leave it to the case config", so every
    submit carried ``--option reconstruct=true --option to_vtk=false`` and silently
    overrode case-config.yml with the widget's defaults."""
    from axqua_plugin.gui.capability_tab_widget import CapabilityTab
    from axqua_plugin.gui.capability_tabs import CapabilityView

    view = CapabilityView(name="free_surface_3d", solver="openfoam",
                          implemented="yes", configured=True, built=True, run=False)
    tab = CapabilityTab(view, _Context())
    assert tab.options() == {}                 # nothing touched, nothing sent

    from axqua_plugin.compat import CHECKED, UNCHECKED
    tab._fields["reconstruct"].setCheckState(UNCHECKED)
    tab._fields["to_vtk"].setCheckState(CHECKED)
    assert tab.options() == {"reconstruct": False, "to_vtk": True}
    tab.deleteLater()


def test_the_openfoam_box_has_the_warm_up_check_box_and_sends_it_with_the_build(
        qgis_app, monkeypatch):
    """The documentation outline prescribes a check box *Use TELEMAC warm-up* that is
    on unless the user turns it off. Only the options of the run were shown, so the
    option of the build was in the table and nowhere in the plugin."""
    from axqua_plugin.compat import PARTIALLY_CHECKED, UNCHECKED
    from axqua_plugin.gui import capability_tab_widget
    from axqua_plugin.gui.capability_tab_widget import CapabilityTab
    from axqua_plugin.gui.capability_tabs import CapabilityView

    sent = []

    class _Client:
        def submit(self, config, kind, **kwargs):
            sent.append((kind, kwargs["options"]))
            return {"job_id": "JOB"}

    class _Ctx(_Context):
        project = type("P", (), {"profile": "", "job_root": "", "launcher": "auto"})()

        def client_or_warn(self):
            return _Client()

        def active_case_or_warn(self):
            return "/c/reach.axq-case"

        def job_submitted(self, job_id):
            pass

    monkeypatch.setattr(capability_tab_widget, "run_async",
                        lambda title, call, on_success=None, on_error=None, owner=None:
                        on_success(call()))
    view = CapabilityView(name="free_surface_3d", solver="openfoam",
                          implemented="yes", configured=True, built=False, run=False)
    assert (view.build_kind, view.submit_kind) == ("openfoam-build", "openfoam-run")
    tab = CapabilityTab(view, _Ctx())
    labels = [tab.options_form.itemAt(row, tab.options_form.ItemRole.LabelRole)
              .widget().text() for row in range(tab.options_form.rowCount())]
    assert labels[0] == "Use TELEMAC warm-up"            # the build comes first
    box = tab._build_fields["pre_run"]
    # untouched, the case file decides, where the warm-up is on by default
    assert box.checkState() == PARTIALLY_CHECKED and "on" in box.toolTip()

    tab._submit(view.build_kind)
    box.setCheckState(UNCHECKED)                         # a build without the warm-up
    tab._submit(view.build_kind)
    tab._submit(view.submit_kind)                        # the run does not carry it
    assert sent == [("openfoam-build", {}), ("openfoam-build", {"pre_run": False}),
                    ("openfoam-run", {})]
    tab.deleteLater()


def test_the_fine_sediment_box_takes_more_iterations_than_a_computer_has_cores(
        qgis_app):
    """The number box of the options was made for processor cores and ends at 4096. A
    steady simulation of a fine mesh needs more iterations than that."""
    from axqua_plugin.gui.capability_tab_widget import CapabilityTab
    from axqua_plugin.gui.capability_tabs import CapabilityView

    view = CapabilityView(name="morphodynamics", solver="openfoam", implemented="yes",
                          configured=True, built=True, run=False)
    tab = CapabilityTab(view, _Context())
    assert list(tab._build_fields) == ["pre_run"] and list(tab._fields) == ["iterations"]
    box = tab._fields["iterations"]
    assert tab.options() == {}                             # untouched: the case decides
    assert box.specialValueText() == "as set for the case"
    box.setValue(20000)
    assert box.value() == 20000 and tab.options() == {"iterations": 20000}
    assert tab.options(build=True) == {}
    tab.deleteLater()


def test_a_polygon_result_is_colored_by_its_attribute(project, tmp_path, qgis_app):
    """The bed of the fine sediment simulation is a polygon layer whose result is an
    attribute. Loaded without a style, every polygon has the same color and the layer
    shows the outline of the model and nothing of the result."""
    from qgis.core import (QgsFeature, QgsField, QgsGeometry, QgsGraduatedSymbolRenderer,
                           QgsRectangle, QgsVectorFileWriter, QgsVectorLayer)
    from qgis.PyQt.QtCore import QVariant

    from axqua_plugin.core.result_loader import (JobResults, ResultItem, ResultLoader,
                                                 color_by_attribute)

    memory = QgsVectorLayer("Polygon?crs=EPSG:25832", "bed", "memory")
    memory.dataProvider().addAttributes([QgsField("net", QVariant.Double)])
    memory.updateFields()
    features = []
    for i in range(40):
        feature = QgsFeature(memory.fields())
        feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(i, 0, i + 1, 10)))
        feature.setAttribute("net", 10.0 ** (i / 8.0 - 3.0))   # five orders of magnitude
        features.append(feature)
    memory.dataProvider().addFeatures(features)
    path = tmp_path / "fine-sediment-bed.gpkg"
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    QgsVectorFileWriter.writeAsVectorFormatV3(memory, str(path),
                                              project.transformContext(), options)

    plain = QgsVectorLayer(str(path), "plain", "ogr")
    loader = ResultLoader(project)
    item = ResultItem(name="Fine sediment at the bed", path=path, kind="vector",
                      style="graduated", variable="net")
    layer = loader.load(JobResults(job_id="job", root=tmp_path, items=[item]))[0]
    renderer = layer.renderer()
    assert isinstance(renderer, QgsGraduatedSymbolRenderer)
    assert renderer.classAttribute() == "net" and len(renderer.ranges()) >= 5
    assert loader.warnings == []

    def colors(of) -> int:
        """Colors that cover more than 2 % of the picture each."""
        from collections import Counter

        picture = _picture(of)
        pixels = Counter(picture[i:i + 4] for i in range(0, len(picture), 4))
        return sum(1 for n in pixels.values() if n > 0.02 * len(picture) / 4)

    # without a style: the background, one fill, and the outlines with their shade
    assert colors(plain) <= 4
    # with it: the background and one fill per class, and no outlines
    assert colors(layer) == 1 + len(renderer.ranges()) > colors(plain)

    missing = ResultItem(name="bed", path=path, kind="vector", style="graduated",
                         variable="no_such_attribute")
    assert color_by_attribute(plain, "no_such_attribute") is False
    other = ResultLoader(project)
    other._style(plain, missing)
    assert "no attribute 'no_such_attribute'" in other.warnings[0]


def test_the_3d_variant_is_a_choice_and_the_default_is_not_sent(qgis_app):
    """The non-hydrostatic run could only be started in a terminal: the option table
    knew numbers and check boxes, and the variant is neither."""
    from axqua_plugin.gui.capability_tab_widget import CapabilityTab
    from axqua_plugin.gui.capability_tabs import CapabilityView

    view = CapabilityView(name="steady3d", solver="telemac", implemented="yes",
                          configured=True, built=True, run=False)
    tab = CapabilityTab(view, _Context())
    combo = tab._fields["variant"]
    assert [combo.itemData(i) for i in range(combo.count())] == ["", "hydrodyn"]
    assert tab.options() == {}                        # hydrostatic is the kind's default
    combo.setCurrentIndex(1)
    assert tab.options() == {"variant": "hydrodyn"}
    tab.deleteLater()


def test_the_unsteady_3d_tab_sends_the_3d_switch_with_its_run(qgis_app, monkeypatch):
    from axqua_plugin.gui import capability_tab_widget
    from axqua_plugin.gui.capability_tab_widget import CapabilityTab
    from axqua_plugin.gui.capability_tabs import CapabilityView

    sent = []

    class _Client:
        def submit(self, config, kind, **kwargs):
            sent.append((kind, kwargs["options"]))
            return {"job_id": "JOB"}

    class _Ctx(_Context):
        project = type("P", (), {"profile": "", "job_root": "", "launcher": "auto"})()

        def client_or_warn(self):
            return _Client()

        def active_case_or_warn(self):
            return "case.axq-case"

    # run the "background" call at once: the test is about what is sent
    monkeypatch.setattr(capability_tab_widget, "run_async",
                        lambda title, call, **kwargs: call())
    view = CapabilityView(name="unsteady3d", solver="telemac", implemented="yes",
                          configured=True, built=True)
    tab = CapabilityTab(view, _Ctx())
    assert "mode_3d" not in tab._fields              # decided by the tab, not by a box
    tab._submit(view.submit_kind)
    tab._submit(view.build_kind)
    assert sent == [("unsteady", {"mode_3d": True}), ("build-3d", {})]
    tab.deleteLater()


def test_the_dashboard_repaints_only_the_row_that_changed(qgis_app, tmp_path):
    """``poll()`` already returns exactly which jobs moved; the widget used to throw
    that away and rebuild every cell of every row on every tick."""
    import json as _json

    from axqua_plugin.gui.jobs_widget import JobsWidget

    root = tmp_path / "JOB-1"
    root.mkdir()
    widget = JobsWidget(_Context())
    widget._rows_arrived([
        {"job_id": "JOB-1", "root": str(root), "state": "RUNNING", "kind": "steady"},
        {"job_id": "JOB-2", "root": str(tmp_path / "JOB-2"), "state": "COMPLETED"},
    ])
    first = widget.table.item(0, 4)
    assert first.text() == "RUNNING"

    (root / "status.json").write_text(_json.dumps(
        {"state": "COMPLETED", "progress": {"kind": "solver_run",
                                            "duration": 100, "simulated_time": 100}}),
        encoding="utf-8")
    widget._tick()
    assert widget.table.item(0, 4).text() == "COMPLETED"
    # The same item object, not a replacement: reusing it is the point.
    assert widget.table.item(0, 4) is first
    widget.deleteLater()


def test_a_queued_job_is_polled_while_the_dashboard_is_hidden(qgis_app, tmp_path):
    """Hidden, every rect test fails, and the old fallback then polled *every* job -
    the cheapest state doing the most work."""
    from axqua_plugin.gui.jobs_widget import JobsWidget

    widget = JobsWidget(_Context())
    widget._rows_arrived([
        {"job_id": "JOB-1", "root": str(tmp_path / "JOB-1"), "state": "QUEUED"},
        {"job_id": "JOB-2", "root": str(tmp_path / "JOB-2"), "state": "COMPLETED"},
    ])
    assert widget.isVisible() is False
    assert widget._poll_ids() == ["JOB-1"]
    widget.deleteLater()


def test_the_log_view_appends_rather_than_reloading(qgis_app, tmp_path):
    """Re-reading a quarter of a megabyte every two seconds also threw away the user's
    selection and scroll position on every tick."""
    from axqua_plugin.core.job_model import Job
    from axqua_plugin.gui.log_dialog import LogDialog

    root = tmp_path / "JOB-1"
    root.mkdir()
    log = root / "runner.log"
    log.write_text("first line\n", encoding="utf-8")

    job = Job(job_id="JOB-1", root=root, state="RUNNING")
    dialog = LogDialog(job, _Context())
    dialog._use_path(log)
    assert "first line" in dialog.view.toPlainText()

    offset = dialog._offset
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("second line\n")
    dialog._poll()
    assert dialog._offset > offset
    text = dialog.view.toPlainText()
    assert text.count("first line") == 1 and "second line" in text

    # A rotated log starts again rather than splicing two files together.
    log.write_text("brand new\n", encoding="utf-8")
    dialog._poll()
    assert dialog.view.toPlainText().strip() == "brand new"
    dialog.close()


# ------------------------------------------------------------------------- layouts


def test_the_print_layout_carries_the_items_the_docs_promise(project, qgis_app):
    from qgis.core import (QgsLayoutItemLabel, QgsLayoutItemLegend, QgsLayoutItemMap,
                           QgsLayoutItemPolyline, QgsLayoutItemScaleBar)

    from axqua_plugin.gui.layouts import add_default_layout

    name = add_default_layout(FakeIface())
    layout = project.layoutManager().layoutByName(name)
    assert layout is not None
    kinds = [type(item) for item in layout.items()]
    for wanted in (QgsLayoutItemMap, QgsLayoutItemScaleBar, QgsLayoutItemLegend,
                   QgsLayoutItemPolyline, QgsLayoutItemLabel):
        assert wanted in kinds, f"the layout has no {wanted.__name__}"


def test_the_flow_arrow_follows_a_centerline_rather_than_the_canvas(project):
    """Documented as derived from the reach. It used to be a bounding-box guess that
    could only point along an axis, while ``bearing()`` sat written and never called."""
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer

    from axqua_plugin.gui.layouts import reach_bearing

    assert reach_bearing(project) is None          # nothing to go on yet

    layer = QgsVectorLayer("LineString?crs=EPSG:25832", "reach centerline", "memory")
    feature = QgsFeature()
    # Due east, so a compass bearing of 90 degrees.
    feature.setGeometry(QgsGeometry.fromPolylineXY(
        [QgsPointXY(0.0, 0.0), QgsPointXY(100.0, 0.0)]))
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    project.addMapLayer(layer)

    assert reach_bearing(project) == pytest.approx(90.0, abs=1e-6)


# --------------------------------------------------------------------- the log tab


def test_messages_reach_the_qgis_log_tab(qgis_app):
    """The troubleshooting page tells users to copy the aXqua tab into a bug report, so something
    has to write to it. Nothing did."""
    from qgis.core import QgsApplication as _App

    from axqua_plugin.compat import log_message

    seen = []
    _App.messageLog().messageReceived.connect(
        lambda message, tag, level: seen.append((message, tag)))
    log_message("a diagnostic line")
    qgis_app.processEvents()
    assert any(tag == "aXqua" and "diagnostic" in message for message, tag in seen)


# ----------------------------------------------------------------------- the movie


def test_the_movie_dialog_offers_only_animatable_layers(project, tmp_path, qgis_app):
    """A static bed-level mesh is a mesh layer too, and offering to animate it is how a
    dialog wastes somebody's afternoon."""
    from axqua_plugin.gui.movie_dialog import animatable_layers

    from qgis.core import QgsMeshLayer
    layer = QgsMeshLayer(str(_write_mesh(tmp_path)), "reach", "mdal")
    if not layer.isValid():                        # pragma: no cover - no MDAL 2DM
        pytest.skip("this QGIS cannot read 2DM meshes")
    project.addMapLayer(layer)
    # Bed elevation is a single timestep, so there is nothing to animate.
    assert animatable_layers() == []


def test_the_encode_command_is_an_argument_list_with_the_output_last(tmp_path):
    from axqua_plugin.gui.movie_dialog import encode_command

    command = encode_command(tmp_path / "frames", tmp_path / "out.webm", 12)
    assert isinstance(command, list) and all(isinstance(a, str) for a in command)
    assert command[-1].endswith("out.webm")
    assert "12" in command
    # A shell metacharacter in a path stays one argument, because there is no shell.
    weird = encode_command(tmp_path, tmp_path / "a; rm -rf b.webm", 8)
    assert weird[-1].endswith("a; rm -rf b.webm")


# ----------------------------------------------------------------- the fixed tabs

EXAMPLE = {
    "case_dir": "/cases/example-isar",
    "solvers": [
        {"solver": "telemac", "enabled": True, "env_ok": True, "capabilities": [
            {"capability": name, "implemented": "yes", "configured": configured,
             "built": built, "run": run}
            for name, configured, built, run in (
                ("steady2d", True, True, True), ("unsteady2d", False, None, None),
                ("steady3d", True, False, False), ("unsteady3d", False, None, None),
                ("morphodynamics", False, None, None), ("gain_lose", True, True, True),
                ("mesh_convergence", True, False, False),
                ("vertical_convergence", True, False, False),
                ("calibration", True, True, False))]},
        {"solver": "openfoam", "enabled": False, "env_ok": None, "capabilities": [
            {"capability": "steady2d", "implemented": "n/a"},
            {"capability": "free_surface_3d", "implemented": "yes"}]},
    ],
}


@pytest.fixture
def dock(qgis_app, monkeypatch):
    """The panel, with every call to axqua answered as 'not installed'."""
    def no_axqua(*args, **kwargs):
        raise FileNotFoundError("axqua is not installed in this test")

    monkeypatch.setattr(runner_client.subprocess, "run", no_axqua)
    from axqua_plugin.gui.dock import AxquaDock
    panel = AxquaDock(FakeIface())
    yield panel
    panel.deleteLater()


def test_the_panel_has_the_prescribed_tabs_and_sub_tabs(dock):
    titles = [dock.tabs.tabText(i).replace("&&", "&") for i in range(dock.tabs.count())]
    assert titles == ["Configuration", "Case Setup", "Preprocessing", "Simulations",
                      "Calibration & validation", "Postprocessing", "Batch-processing"]
    inner = {key: [tabs.tabText(i) for i in range(tabs.count())]
             for key, tabs in dock.subtabs.items()}
    assert inner == {"hydraulics": ["Baseline (Telemac)", "OpenFOAM",
                                    "Mesh convergence", "Morphodynamics"],
                     "postprocessing": ["QGIS", "ParaView", "VisIt"]}
    # the job list is below the tabs and not one of them
    assert dock.jobs_tab.parent() is not None and dock.tabs.indexOf(dock.jobs_tab) == -1


def test_help_opens_the_section_of_the_tab_that_is_showing(dock, monkeypatch):
    from axqua_plugin.gui import help as help_pages

    opened = []
    monkeypatch.setattr(help_pages, "open_url", lambda url: opened.append(url) or True)
    dock.show_section("case")
    dock.open_help()
    dock.show_section("hydraulics", "openfoam")
    assert dock.current_section() == ("hydraulics", "openfoam")
    dock.open_help()
    dock.show_section("postprocessing", "paraview")
    dock.open_help()
    assert [url.split("/latest/")[1] for url in opened] == [
        "usage/case-setup.html#help-case-setup",
        "usage/hydraulic-simulations.html#help-hydraulics-openfoam",
        "usage/postprocessing.html#help-postprocessing-paraview"]


def test_the_capabilities_of_a_case_appear_on_the_tabs_of_their_sections(dock):
    from axqua_plugin.gui.capability_tabs import CaseView

    dock._apply_case_view(CaseView.from_payload(EXAMPLE))
    telemac = dock.page("hydraulics", "telemac")
    assert [key[1] for key in telemac._boxes] == [
        "steady2d", "steady3d", "unsteady2d", "unsteady3d", "gain_lose"]
    # the build of the 2D model has a tab of its own; the 3D build stays with its run
    assert telemac.tab("telemac", "steady2d").build_button.isHidden()
    assert not telemac.tab("telemac", "steady3d").build_button.isHidden()
    mesh = dock.page("hydraulics", "mesh")
    assert mesh.solver == "telemac"
    assert [key[1] for key in mesh._boxes] == ["mesh_convergence", "vertical_convergence"]
    mesh.choose("openfoam")                    # this case does not use OpenFOAM
    assert mesh._boxes == {} and "does not use OpenFOAM" in mesh.note.text()
    mesh.choose("telemac")
    assert list(dock.page("calibration")._boxes) == [("telemac", "calibration")]
    assert list(dock.page("hydraulics", "morphodynamics")._boxes) == [
        ("telemac", "morphodynamics")]
    # a case without an openfoam block: the sub-tab says so instead of being empty
    openfoam = dock.page("hydraulics", "openfoam")
    assert openfoam._boxes == {} and "does not use OpenFOAM" in openfoam.note.text()


def test_the_preprocessing_tab_builds_and_shows_what_is_built(dock):
    from axqua_plugin.gui.capability_tabs import CaseView

    page = dock.page("preprocessing")
    assert not page.build_button.isEnabled()                 # no case yet
    dock._apply_case_view(CaseView.from_payload(EXAMPLE))
    assert page.build_button.isEnabled()
    assert page.state.text() == "The model is built."
    rows = {page.table.item(r, 0).text(): [page.table.item(r, c).text()
                                           for c in range(1, 5)]
            for r in range(page.table.rowCount())}
    assert rows["Steady 2D"] == ["telemac", "yes", "yes", "yes"]
    assert rows["Mesh convergence"] == ["telemac", "yes", "no", "no"]
    assert rows["Unsteady 2D"] == ["telemac", "no", "-", "-"]

    sent = []
    dock.submit = lambda kinds, options=None: sent.append(list(kinds))
    page.build_button.click()
    assert sent == [["preprocessing"]]


def _step(page, kind):
    """The entry of a job kind in the list of the batch page."""
    from axqua_plugin.compat import enum_value
    from qgis.PyQt.QtCore import Qt

    role = enum_value(Qt, "ItemDataRole.UserRole", "UserRole")
    return next(page.steps.item(i) for i in range(page.steps.count())
                if page.steps.item(i).data(role) == kind)


def test_the_fine_sediment_steps_of_a_batch_need_openfoam_to_be_asked(dock):
    """TELEMAC has a capability called morphodynamics too. A case that couples GAIA
    asks for that one, and its batch must not offer the fine sediment simulation."""
    import copy

    from axqua_plugin.compat import enum_value
    from axqua_plugin.gui.capability_tabs import CaseView
    from qgis.PyQt.QtCore import Qt

    enabled = enum_value(Qt, "ItemFlag.ItemIsEnabled", "ItemIsEnabled")
    page = dock.page("batch")

    def offered(payload):
        dock._apply_case_view(CaseView.from_payload(payload))
        return bool(_step(page, "openfoam-sediment").flags() & enabled)

    gaia = copy.deepcopy(EXAMPLE)
    for capability in gaia["solvers"][0]["capabilities"]:
        if capability["capability"] == "morphodynamics":
            capability["configured"] = True
    assert offered(EXAMPLE) is False and offered(gaia) is False

    fine = copy.deepcopy(EXAMPLE)
    fine["solvers"][1] = {"solver": "openfoam", "enabled": True, "env_ok": True,
                          "capabilities": [{"capability": "morphodynamics",
                                            "implemented": "yes", "configured": True,
                                            "built": False, "run": False}]}
    assert offered(fine) is True
    morphodynamics = dock.page("hydraulics", "morphodynamics")
    morphodynamics.choose("openfoam")
    box = morphodynamics.tab("openfoam", "morphodynamics")
    assert box.build_button.isEnabled() and box.submit_button.isEnabled()
    assert not box.results_button.isEnabled()              # nothing has run yet


def test_the_mesh_study_of_the_openfoam_model_is_chosen_on_the_simulations_tab(dock):
    """The user's correction of 2026-10-16: Mesh convergence is a sub-tab of
    Simulations, where the study is run with TELEMAC or with OpenFOAM."""
    import copy

    from axqua_plugin.gui.capability_tabs import CaseView

    both = copy.deepcopy(EXAMPLE)
    both["solvers"][1] = {"solver": "openfoam", "enabled": True, "env_ok": True,
                          "capabilities": [{"capability": "mesh_convergence",
                                            "implemented": "yes", "configured": True,
                                            "built": False, "run": False}]}
    dock._apply_case_view(CaseView.from_payload(both))
    mesh = dock.page("hydraulics", "mesh")
    assert [key[0] for key in mesh._boxes] == ["telemac", "telemac"]
    mesh.choose("openfoam")
    assert list(mesh._boxes) == [("openfoam", "mesh_convergence")]
    box = mesh.tab("openfoam", "mesh_convergence")
    assert box.view.submit_kind == "openfoam-mesh-convergence"
    assert box.view.build_kind is None and box.submit_button.isEnabled()
    assert "pre_run" in box._fields


def test_every_step_of_a_batch_is_on_the_screen(dock):
    """The list was 150 pixels high, which seven steps filled. With two more, the last
    ones could only be reached by scrolling a list that does not look like it scrolls."""
    from axqua_plugin.core import batch

    from qgis.PyQt.QtCore import QCoreApplication

    dock.show()
    dock.show_section("batch")
    QCoreApplication.processEvents()                       # the layout settles
    steps = dock.page("batch").steps
    assert steps.count() == len(batch.STEPS)
    last = steps.visualItemRect(steps.item(steps.count() - 1))
    assert 0 < last.bottom() <= steps.viewport().height()
    assert steps.verticalScrollBar().maximum() == 0        # nothing left to scroll to


def test_a_batch_is_submitted_in_the_order_of_the_workflow(dock):
    from axqua_plugin.compat import CHECKED
    from axqua_plugin.gui.capability_tabs import CaseView

    dock._apply_case_view(CaseView.from_payload(EXAMPLE))
    page = dock.page("batch")
    assert page.ticked() == ["preprocessing", "steady"]      # what is ticked at first
    _step(page, "calibration").setCheckState(CHECKED)
    sent = []
    dock.submit = lambda kinds, options=None: sent.append(list(kinds))
    page.submit_button.click()
    assert sent == [["preprocessing", "steady", "calibration"]]


def test_the_batch_script_is_written_for_the_active_case(dock, tmp_path, monkeypatch):
    from qgis.PyQt.QtWidgets import QFileDialog

    case = tmp_path / "reach.axq-case"
    case.write_text("project: {name: reach}\n", encoding="utf-8")
    dock.ctx.project.add_case(case)
    target = tmp_path / "reach-batch.sh"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "")))
    dock.page("batch").script_button.click()
    text = target.read_text(encoding="utf-8")
    assert f"CASE={case}" in text.replace("'", "")
    assert "run_step preprocessing\nrun_step steady\n" in text


def test_a_finding_puts_a_triangle_on_its_row_and_on_the_tab(dock):
    from axqua_plugin.gui import findings as fnd

    tab = dock.configuration_tab
    tab.show_profile({"path": "/home/x/default.axq-profile", "exists": True},
                     {"name": "bench", "solvers": {"telemac": {"setup_script": "/t.sh"}}})
    assert tab.edit_button.text() == "Edit profile..." and tab.check_button.isEnabled()
    assert tab._summary["solvers.telemac.setup_script"].text() == "/t.sh"
    index = dock.index_of("configuration")
    assert dock.tabs.tabIcon(index).isNull()

    tab.show_findings([fnd.Finding("warning", "axqua.environment.ambient", "ambient",
                                   "solvers.openfoam.setup_script", "enter the script")],
                      checked=True)
    assert not tab._triangles["solvers.openfoam.setup_script"].isHidden()
    assert tab._triangles["solvers.telemac.setup_script"].isHidden()
    assert not dock.tabs.tabIcon(index).isNull()             # the tab carries it too
    tab.show_findings([], checked=True)
    assert dock.tabs.tabIcon(index).isNull()


def test_a_computer_without_a_profile_is_offered_one(dock):
    tab = dock.configuration_tab
    tab.show_profile({"path": "/home/x/default.axq-profile", "exists": False}, None)
    assert tab.edit_button.text() == "Create profile..."
    assert not tab.check_button.isEnabled()
    assert "no profile yet" in tab.profile_path.text()


# ------------------------------------------------------------ the profile editor

PROFILE = {
    "path": "/home/x/default.axq-profile", "schema_version": 1, "name": "bench",
    "python": {"executable": "/env/bin/python"},
    "solvers": {"telemac": {"setup_script": "/opt/telemac/pysource.sh",
                            "mpi_processes": 12,
                            # an entry the editor has no row for must survive a save
                            "overrides": {"USETELCFG": "debian"}}},
    "postprocessors": {"visit": "/opt/visit/bin/visit"},
}


class _ProfileClient:
    def __init__(self, findings=(), located=None, backup=""):
        self.written, self.findings = [], list(findings)
        self.located, self.asked, self.backup = dict(located or {}), [], backup

    def profile_write(self, data):
        self.written.append(data)
        return {"path": "/home/x/default.axq-profile", "backup": self.backup,
                "findings": []}

    def profile_locate(self, program, folder):
        self.asked.append((program, folder))
        return dict({"program": program, "folder": folder, "script": "",
                     "candidates": []}, **self.located.get(program, {}))

    def profile_check(self, *, probe=True):
        return {"path": "/home/x/default.axq-profile", "findings": self.findings}


def _run_now(monkeypatch):
    """Run a background call at once: these tests are about what is sent and shown."""
    from axqua_plugin.gui import profile_editor

    def now(title, call, on_success=None, on_error=None, owner=None):
        try:
            answer = call()
        except Exception as exc:                 # noqa: BLE001 - as the task would
            on_error(exc)
        else:
            on_success(answer)

    monkeypatch.setattr(profile_editor, "run_async", now)


def test_the_editor_writes_what_its_rows_say_and_keeps_the_rest(qgis_app, monkeypatch):
    from axqua_plugin.gui.profile_editor import ProfileEditor, get

    _run_now(monkeypatch)
    client = _ProfileClient()
    editor = ProfileEditor(client, PROFILE)
    assert not editor.dirty                                   # just opened
    editor._widgets["solvers.openfoam.setup_script"].setText("/usr/lib/of/bashrc")
    editor._widgets["postprocessors.visit"].setText("")       # removed by the user
    assert editor.dirty
    editor.save()
    (written,) = client.written
    assert get(written, "solvers.openfoam.setup_script") == "/usr/lib/of/bashrc"
    assert get(written, "solvers.telemac.overrides") == {"USETELCFG": "debian"}
    assert get(written, "solvers.telemac.mpi_processes") == 12
    assert "postprocessors" not in written                     # nothing left in it
    assert "path" not in written                               # where, not what
    assert editor.saved_once and not editor.dirty
    editor.deleteLater()


def test_selecting_the_folder_of_a_program_fills_the_row_below(qgis_app, monkeypatch):
    """The folder is what a person knows about an installation. Which file in it
    aXqua runs is found by aXqua."""
    from axqua_plugin.gui.profile_editor import ProfileEditor, get

    _run_now(monkeypatch)
    script = "/data/opt/telemac-mascaret/configs/pysource.debian12.sh"
    client = _ProfileClient(located={
        "telemac": {"folder": "/data/opt/telemac-mascaret", "script": script,
                    "candidates": [script]},
        "openfoam": {"candidates": ["/of/etc/bashrc", "/of/other/etc/bashrc"]}})
    editor = ProfileEditor(client, dict(PROFILE, installations={"visit": "/opt/visit"},
                                        programs={"visit": "/opt/visit/bin/visit"}))
    assert editor._widgets["installations.visit"].text() == "/opt/visit"
    assert not editor.dirty
    # PROFILE names the launcher of VisIt itself; a row left empty says what is run
    folders_only = ProfileEditor(client, {"installations": {"visit": "/opt/visit"},
                                          "programs": {"visit": "/opt/visit/bin/visit"}})
    row = folders_only._widgets["postprocessors.visit"]
    assert row.text() == "" and row.placeholderText() == \
        "found in the installation folder: /opt/visit/bin/visit"
    folders_only.deleteLater()

    # a row that was not changed asks nothing, so the row below keeps what it says
    editor.locate("installations.visit")
    assert client.asked == []

    editor._widgets["installations.telemac"].setText("/data/opt")   # the folder above
    editor.locate("installations.telemac")
    assert client.asked == [("telemac", "/data/opt")]
    assert editor._widgets["installations.telemac"].text() == "/data/opt/telemac-mascaret"
    assert editor._widgets["solvers.telemac.setup_script"].text() == script
    assert script in editor.status.text()
    editor.locate("installations.telemac")                          # asked once
    assert len(client.asked) == 1

    editor._widgets["installations.openfoam"].setText("/of")
    editor.locate("installations.openfoam")
    assert editor._widgets["solvers.openfoam.setup_script"].text() == ""
    assert "holds 2 files" in editor.status.text()
    editor._widgets["installations.paraview"].setText("/empty")
    editor.locate("installations.paraview")
    assert "No program of paraview was found in /empty" in editor.status.text()

    editor.save()
    (written,) = client.written
    assert get(written, "installations.telemac") == "/data/opt/telemac-mascaret"
    assert get(written, "solvers.telemac.setup_script") == script
    assert get(written, "installations.visit") == "/opt/visit"
    # what the command adds for display is not part of the file
    assert "programs" not in written and "path" not in written
    editor.deleteLater()


def test_the_editor_says_where_a_profile_it_could_not_read_was_kept(qgis_app,
                                                                    monkeypatch):
    from axqua_plugin.gui.profile_editor import ProfileEditor

    _run_now(monkeypatch)
    kept = "/home/x/default.axq-profile.20261011-101500.bak"
    editor = ProfileEditor(_ProfileClient(backup=kept), {"name": "new"})
    editor.save()
    assert kept in editor.status.text() and "could not be read" in editor.status.text()
    editor.deleteLater()


def test_the_summary_shows_the_program_that_is_run_and_its_folder(dock):
    """A profile may name the installation folder only. The file found in it is what
    aXqua runs, and the tab shows that file."""
    from axqua_plugin.gui.configuration_tab import describe_entry

    tab = dock.configuration_tab
    profile = {"name": "bench",
               "installations": {"telemac": "/opt/telemac-mascaret", "visit": "/opt/v"},
               "solvers": {"openfoam": {"setup_script": "/of/etc/bashrc"}},
               "programs": {"telemac": "/opt/telemac-mascaret/configs/pysource.sh",
                            "openfoam": "/of/etc/bashrc"}}
    tab.show_profile({"path": "/home/x/default.axq-profile", "exists": True}, profile)
    assert tab._summary["solvers.telemac.setup_script"].text() == \
        "/opt/telemac-mascaret/configs/pysource.sh\ninstalled in /opt/telemac-mascaret"
    assert tab._summary["solvers.openfoam.setup_script"].text() == "/of/etc/bashrc"
    assert tab._summary["postprocessors.paraview"].text() == "not set"
    assert describe_entry(profile, "postprocessors.visit") == \
        "no program found in the folder\ninstalled in /opt/v"


def test_an_entry_of_two_lines_is_shown_with_both(dock):
    """The folder a program is installed in stands in a second line. In a panel lower
    than the three boxes of the tab need, the rows were squeezed and that line was cut
    off at its middle, in the panel and in the picture of it in the documentation. The
    tab scrolls now, as the pages of the workflow do."""
    from qgis.PyQt.QtCore import QCoreApplication
    from qgis.PyQt.QtWidgets import QScrollArea

    tab = dock.configuration_tab
    profile = {"name": "bench",
               "installations": {name: f"/opt/{name}" for name in (
                   "telemac", "openfoam", "paraview", "visit")},
               "programs": {name: f"/opt/{name}/bin/{name}" for name in (
                   "telemac", "openfoam", "paraview", "visit")}}
    tab.show_profile({"path": "/home/x/default.axq-profile", "exists": True}, profile)
    dock.setFloating(True)
    dock.resize(800, 520)                                  # far too low for the tab
    dock.show()
    dock.show_section("configuration")
    for _ in range(3):
        QCoreApplication.processEvents()
    area = tab.findChild(QScrollArea)
    assert area is not None and area.verticalScrollBar().maximum() > 0
    for key in ("solvers.telemac.setup_script", "solvers.openfoam.setup_script",
                "postprocessors.paraview", "postprocessors.visit"):
        label = tab._summary[key]
        assert label.text().count("\n") == 1
        assert label.height() >= label.heightForWidth(label.width()) > \
            label.fontMetrics().lineSpacing()


def test_a_profile_that_cannot_be_read_is_not_silently_replaced(dock, monkeypatch):
    """*Create profile...* on such a computer opens the editor with what aXqua finds.
    The window says that the file is kept, before anything is saved."""
    from axqua_plugin.gui import configuration_tab

    tab = dock.configuration_tab
    opened = []
    monkeypatch.setattr(configuration_tab, "run_async",
                        lambda title, call, on_success=None, on_error=None, owner=None:
                        on_success({"name": "found"}))
    monkeypatch.setattr(tab, "_open_editor",
                        lambda profile, title: opened.append((profile, title)))
    tab.show_profile({}, None, error="this profile was written by a newer aXqua")
    assert "could not be read" in tab.profile_path.text()
    tab.edit_profile()
    (profile, title), = opened
    assert profile == {"name": "found"}
    assert "newer aXqua" in title and ".bak" in title and "not deleted" in title
    # a computer that simply has no profile gets the ordinary sentence
    tab.show_profile({"path": "/home/x/default.axq-profile", "exists": False}, None)
    tab.edit_profile()
    assert ".bak" not in opened[-1][1]


def test_saving_is_never_refused_because_of_a_finding(qgis_app, monkeypatch):
    """A profile is incomplete while it is filled in. One that cannot be saved until it
    is complete is lost work."""
    from axqua_plugin.gui.profile_editor import ProfileEditor

    _run_now(monkeypatch)
    client = _ProfileClient([{"severity": "error", "code": "axqua.environment.script_missing",
                              "subject": "solvers.telemac.setup_script",
                              "message": "the script does not exist",
                              "remedy": "select the script"}])
    editor = ProfileEditor(client, PROFILE)
    editor._widgets["name"].setText("bench-2")
    editor.save()
    assert len(client.written) == 1 and editor.saved_once
    assert "Saved" in editor.status.text()
    assert not editor._triangles["solvers.telemac.setup_script"].isHidden()
    assert editor._triangles["name"].isHidden()
    assert len(editor.finding_list.findings) == 1
    editor.deleteLater()


def test_cancel_discards_and_exit_asks_only_when_something_changed(qgis_app, monkeypatch):
    from axqua_plugin.gui import editor_base
    from axqua_plugin.gui.profile_editor import ProfileEditor

    _run_now(monkeypatch)
    asked = []
    monkeypatch.setattr(editor_base, "exec_dialog",
                        lambda box: asked.append(box.text()))
    client = _ProfileClient()

    untouched = ProfileEditor(client, PROFILE)
    untouched.exit()                                 # nothing changed: closes at once
    assert asked == [] and untouched.result() == 1

    changed = ProfileEditor(client, PROFILE)
    changed._widgets["name"].setText("other")
    changed.exit()                                   # asks, and no button was chosen
    assert asked == ["The profile has changes that are not saved."]
    changed.reject()                                 # Cancel
    assert client.written == []
    for editor in (untouched, changed):
        editor.deleteLater()


def test_a_value_is_read_and_written_by_its_dotted_key(qgis_app):
    from axqua_plugin.gui.profile_editor import get, put

    data = {"solvers": {"telemac": {"setup_script": "/a"}}}
    assert get(data, "solvers.telemac.setup_script") == "/a"
    assert get(data, "solvers.openfoam.setup_script", "none") == "none"
    put(data, "jobs.root", "/scratch")
    assert data["jobs"] == {"root": "/scratch"}
    put(data, "solvers.telemac.setup_script", "")    # emptied: removed with its parents
    assert "solvers" not in data


# --------------------------------------------------------------- the case editor


def _field(key, kind, label="", essential=False, **extra):
    block, name = key.split(".", 1)
    return {"key": key, "block": block, "name": name, "kind": kind,
            "label": label or name, "essential": essential, "unit": extra.get("unit", ""),
            "help": extra.get("help", ""), "choices": extra.get("choices", []),
            "default": extra.get("default")}


SCHEMA = {"sections": [
    {"block": "geodata", "title": "Geodata", "label": "case-geodata", "fields": [
        _field("geodata.dem_initial", "raster", "Terrain model", True),
        _field("geodata.boundary", "vector", "Model outline", True),
        _field("geodata.breaklines", "vector", "Breaklines")]},
    {"block": "boundaries", "title": "Boundaries", "label": "case-boundaries", "fields": [
        _field("boundaries.prescribed_flowrate", "number", "Discharge", True,
               unit="m3/s"),
        _field("boundaries.outflow_condition", "choice", "Outflow", True,
               choices=["elevation", "stage_discharge", "free"]),
        _field("boundaries.internal_sources", "bool", "Internal sources")]},
    {"block": "hydrodynamics", "title": "Hydraulic simulation", "label": "", "fields": [
        _field("hydrodynamics.turbulence_model", "text", "Turbulence model", True),
        _field("hydrodynamics.duration", "number", "Simulated time", True, unit="s"),
        _field("hydrodynamics.graphic_printout_period", "int", "Printout period")]},
    {"block": "calibration", "title": "Calibration", "label": "", "fields": [
        _field("calibration.parameters", "yaml", "Parameters", True),
        _field("calibration.init_runs", "int", "Initial runs", True)]},
]}

CASE = {
    "project": {"name": "reach", "crs_epsg": 25832},          # a block without a form
    "geodata": {"dem_initial": "data/dem.tif", "boundary": "data/outline.gpkg"},
    "boundaries": {"prescribed_flowrate": 2.4, "outflow_condition": "stage_discharge"},
    "hydrodynamics": {"turbulence_model": 3, "duration": 3000.0,
                      "graphic_printout_period": 500},
    "calibration": {"parameters": [{"name": "zone4", "min": 0.02, "max": 0.3}],
                    "init_runs": 8},
}


class _CaseClient:
    def __init__(self, findings=()):
        self.written, self.findings = [], list(findings)

    def case_write(self, path, data):
        self.written.append(data)
        return {"path": str(path), "backup": str(path) + ".bak",
                "findings": self.findings}


def _editor(tmp_path, monkeypatch, findings=()):
    from axqua_plugin.gui import case_editor

    monkeypatch.setattr(case_editor, "run_async",
                        lambda title, call, on_success=None, on_error=None, owner=None:
                        on_success(call()))
    client = _CaseClient(findings)
    return case_editor.CaseEditor(client, tmp_path / "reach.axq-case", CASE, SCHEMA), client


def test_the_form_shows_the_essential_settings_and_what_the_case_already_has(
        qgis_app, tmp_path, monkeypatch):
    editor, _client = _editor(tmp_path, monkeypatch)
    assert "geodata.breaklines" not in editor.rows             # not essential, not set
    assert "hydrodynamics.graphic_printout_period" in editor.rows    # set in the case
    assert editor._adders["geodata"].count() == 2              # "Add setting..." + one
    assert [editor.section_list.item(i).text() for i in range(4)] == [
        "Geodata", "Boundaries", "Hydraulic simulation", "Calibration"]
    editor.deleteLater()


def test_a_case_that_was_only_opened_is_written_back_as_it_was(qgis_app, tmp_path,
                                                                monkeypatch):
    """Opening and saving must not turn 3 into "3", 3000.0 into 3000 or reorder a
    list: only a row whose text changed is converted from text."""
    editor, client = _editor(tmp_path, monkeypatch)
    assert editor.values() == CASE and not editor.dirty
    editor.save()
    (written,) = client.written
    assert written == CASE
    assert type(written["hydrodynamics"]["turbulence_model"]) is int
    assert type(written["hydrodynamics"]["duration"]) is float
    assert written["project"] == CASE["project"]              # a block without a form
    editor.deleteLater()


def test_a_changed_row_is_read_by_its_kind_and_an_emptied_one_is_removed(
        qgis_app, tmp_path, monkeypatch):
    editor, _client = _editor(tmp_path, monkeypatch)
    editor.rows["boundaries.prescribed_flowrate"].widget.setText("5.3")
    editor.rows["hydrodynamics.turbulence_model"].widget.setText("auto")
    editor.rows["hydrodynamics.graphic_printout_period"].widget.setText("")
    editor.rows["calibration.init_runs"].widget.setText("12")
    outflow = editor.rows["boundaries.outflow_condition"].widget
    outflow.setCurrentIndex(outflow.findText("free"))
    data = editor.values()
    assert data["boundaries"] == {"prescribed_flowrate": 5.3, "outflow_condition": "free"}
    assert data["hydrodynamics"] == {"turbulence_model": "auto", "duration": 3000.0}
    assert data["calibration"]["init_runs"] == 12
    assert editor.dirty
    editor.deleteLater()


def test_a_setting_is_added_from_the_list_and_a_yes_no_setting_has_three_states(
        qgis_app, tmp_path, monkeypatch):
    editor, _client = _editor(tmp_path, monkeypatch)
    adder = editor._adders["boundaries"]
    adder.setCurrentIndex(1)                                   # "Internal sources"
    editor._add_chosen("boundaries")
    row = editor.rows["boundaries.internal_sources"]
    assert row.widget.currentText() == "(not set)"
    assert "internal_sources" not in editor.values()["boundaries"]     # still not set
    row.widget.setCurrentIndex(row.widget.findText("yes"))
    assert editor.values()["boundaries"]["internal_sources"] is True
    assert not adder.isVisible() or adder.count() == 1         # nothing left to add
    editor.deleteLater()


def test_a_list_that_cannot_be_read_keeps_its_previous_content_and_says_so(
        qgis_app, tmp_path, monkeypatch):
    """The rest of the case is still saved: a typing error in one entry must not cost
    the work in all the others."""
    editor, client = _editor(tmp_path, monkeypatch)
    editor.rows["calibration.parameters"].widget.setPlainText("- {name: zone4, min: [")
    editor.rows["boundaries.prescribed_flowrate"].widget.setText("5.3")
    editor.save()
    (written,) = client.written
    assert written["calibration"]["parameters"] == CASE["calibration"]["parameters"]
    assert written["boundaries"]["prescribed_flowrate"] == 5.3
    assert not editor.rows["calibration.parameters"].triangle.isHidden()
    assert "cannot be read" in editor.findings[0].message

    editor.rows["calibration.parameters"].widget.setPlainText(
        "- {name: zone6, min: 0.1, max: 0.8}")
    assert editor.values()["calibration"]["parameters"] == [
        {"name": "zone6", "min": 0.1, "max": 0.8}]
    editor.deleteLater()


def test_findings_appear_at_their_setting_at_their_block_and_in_the_list(
        qgis_app, tmp_path, monkeypatch):
    findings = [
        {"severity": "error", "code": "axqua.config.missing_file",
         "subject": "geodata.dem_initial", "message": "the file does not exist"},
        {"severity": "warning", "code": "axqua.config.incomplete",
         "subject": "geodata.breaklines", "message": "about a setting without a row"},
        {"severity": "error", "code": "axqua.config.invalid_value",
         "subject": "boundaries", "message": "about the block as a whole"}]
    editor, client = _editor(tmp_path, monkeypatch, findings)
    editor.save()                                    # saved in spite of two errors
    assert len(client.written) == 1 and "Saved" in editor.status.text()
    assert not editor.rows["geodata.dem_initial"].triangle.isHidden()
    assert editor.rows["geodata.boundary"].triangle.isHidden()
    assert not editor.rows["geodata.breaklines"].triangle.isHidden()   # row was added
    assert not editor._problems["boundaries"].isHidden()
    assert editor._problems["geodata"].isHidden()    # its findings have rows
    icons = [editor.section_list.item(i).icon().isNull() for i in range(4)]
    assert icons == [False, False, True, True]
    assert len(editor.finding_list.findings) == 3
    editor.deleteLater()


def _req(key, kind, label="", *, required=False, when=(), **extra):
    item = _field(key, kind, label, True, **extra)
    item.update(required=required, required_when=list(when))
    return item


#: What ``axqua schema --json`` sends since the corrections of 2026-10-16: pages with
#: frames, switches, and what is required.
PAGED_FIELDS = [
    _req("project.name", "text", "Name of the case", required=True),
    _req("boundaries.prescribed_flowrate", "number", "Discharge", required=True),
    _req("boundaries.outflow_condition", "choice", "Water level at the outflow",
         choices=["elevation", "stage_discharge", "free"], default="elevation"),
    _req("boundaries.prescribed_elevation", "number", "Outflow water level",
         when=[{"key": "boundaries.outflow_condition", "in": ["elevation"]}]),
    _req("drying.film_infiltration", "bool", "Remove thin films"),
    _req("openfoam.cell_size", "number", "Cell size"),
    _req("openfoam.mode", "choice", "Free surface", choices=["vof", "rigid-lid"],
         when=[{"block": "openfoam"}]),
    _req("openfoam.end_time", "number", "Simulated time", when=[{"block": "openfoam"}]),
    _req("ground_truth.sources", "yaml", "Measurement files",
         when=[{"block": "calibration"}]),
    _req("calibration.parameters", "yaml", "Parameters", when=[{"block": "calibration"}]),
    _req("calibration.validation", "yaml", "Validation"),
    _req("postproc.telemac_maps", "multichoice", "Maps",
         choices=["depth", "velocity", "free-surface"], default=["depth", "velocity"]),
]
PAGED_SCHEMA = {
    "sections": [{"block": block, "title": block, "label": "",
                  "fields": [f for f in PAGED_FIELDS if f["block"] == block]}
                 for block in dict.fromkeys(f["block"] for f in PAGED_FIELDS)],
    "pages": [
        {"key": "project", "title": "Project settings", "label": "case-project",
         "switch": {}, "frames": [{"title": "", "info": "", "keys": ["project.name"]}]},
        {"key": "boundaries", "title": "Liquid boundaries", "label": "case-boundaries",
         "switch": {}, "frames": [{"title": "", "info": "", "keys": [
             "boundaries.prescribed_flowrate", "boundaries.outflow_condition",
             "boundaries.prescribed_elevation"]}]},
        {"key": "initialization", "title": "Initial conditions",
         "label": "case-initialization", "switch": {}, "frames": [
             {"title": "Drying of thin films", "info": "On a coarse bed ...",
              "keys": ["drying.film_infiltration"]}]},
        {"key": "mesh", "title": "Mesh configuration", "label": "case-mesh",
         "switch": {}, "frames": [{"title": "OpenFOAM lattice", "info": "",
                                   "keys": ["openfoam.cell_size"]}]},
        {"key": "openfoam", "title": "OpenFOAM", "label": "case-hydraulics",
         "switch": {"label": "Simulate with OpenFOAM", "seed": {"mode": "vof"}},
         "frames": [{"title": "", "info": "", "keys": ["openfoam.mode",
                                                        "openfoam.end_time"]}]},
        {"key": "calibration", "title": "Calibration and validation",
         "label": "help-calibration",
         "switch": {"label": "Calibrate and validate this case", "seed": {}},
         "frames": [{"title": "Measurements", "info": "",
                     "keys": ["ground_truth.sources"]},
                    {"title": "Calibration and validation", "info": "",
                     "keys": ["calibration.parameters", "calibration.validation"]}]},
        {"key": "postproc", "title": "Post-processing", "label": "help-postprocessing",
         "switch": {}, "frames": [{"title": "", "info": "",
                                   "keys": ["postproc.telemac_maps"]}]},
    ]}

PAGED_CASE = {
    "project": {"name": "reach"},
    "boundaries": {"prescribed_flowrate": 2.4},
    "ground_truth": {"sources": [{"category": "hydraulics", "kind": "points",
                                  "values": "gt/nov.gpkg"}]},
    "calibration": {"parameters": [{"name": "zone4", "min": 0.02, "max": 0.3}],
                    "validation": [{"name": "High flow", "prescribed_flowrate": 5.3,
                                    "prescribed_elevation": 815.2,
                                    "sources": [{"category": "hydraulics",
                                                 "kind": "points",
                                                 "values": "gt/sep.gpkg"}]}]},
}


def _paged(tmp_path, monkeypatch, case=None):
    from axqua_plugin.gui import case_editor

    monkeypatch.setattr(case_editor, "run_async",
                        lambda title, call, on_success=None, on_error=None, owner=None:
                        on_success(call()))
    client = _CaseClient()
    editor = case_editor.CaseEditor(client, tmp_path / "reach.axq-case",
                                    case or PAGED_CASE, PAGED_SCHEMA)
    return editor, client


def _purple(label) -> bool:
    return "#2e014b" in label.styleSheet() and "bold" in label.styleSheet()


def test_the_case_settings_have_the_pages_the_user_named(qgis_app, tmp_path,
                                                         monkeypatch):
    from qgis.PyQt.QtWidgets import QGroupBox, QToolButton

    editor, client = _paged(tmp_path, monkeypatch)
    assert editor.windowTitle() == "Case settings: reach.axq-case"
    assert [editor.section_list.item(i).text() for i in range(7)] == [
        "Project settings", "Liquid boundaries", "Initial conditions",
        "Mesh configuration", "OpenFOAM", "Calibration and validation",
        "Post-processing"]
    frames = [box.title() for box in editor.findChildren(QGroupBox)]
    assert "Drying of thin films" in frames and "OpenFOAM lattice" in frames
    # the drying frame explains why it matters
    drying = next(b for b in editor.findChildren(QGroupBox)
                  if b.title() == "Drying of thin films")
    assert any(t.toolTip().startswith("On a coarse bed")
               for t in drying.findChildren(QToolButton))
    # nothing touched: written back as it was read
    assert editor.values() == PAGED_CASE and not editor.dirty
    editor.deleteLater()


def test_required_entries_are_purple_and_follow_the_options(qgis_app, tmp_path,
                                                           monkeypatch):
    editor, _client = _paged(tmp_path, monkeypatch)
    rows = editor.rows
    assert _purple(rows["project.name"].label_widget)
    # the outflow level is required while the outflow condition is a level (its default)
    assert _purple(rows["boundaries.prescribed_elevation"].label_widget)
    outflow = rows["boundaries.outflow_condition"].widget
    outflow.setCurrentIndex(outflow.findData("free"))
    assert not _purple(rows["boundaries.prescribed_elevation"].label_widget)
    outflow.setCurrentIndex(outflow.findData("elevation"))
    # the page that lacks it is marked as well
    item = editor.section_list.item(1)
    assert item.font().bold() and "required" in item.toolTip()
    rows["boundaries.prescribed_elevation"].widget.setText("815.3")
    assert not editor.section_list.item(1).font().bold()

    # OpenFOAM is off: its entries are grayed out, required nowhere, and not written
    switch = editor.switches["openfoam"]
    assert not switch.isChecked() and not rows["openfoam.cell_size"].container.isEnabled()
    assert not _purple(rows["openfoam.end_time"].label_widget)
    switch.setChecked(True)                                      # switched on
    assert rows["openfoam.mode"].widget.currentData() == "vof"   # the seed entry
    assert _purple(rows["openfoam.end_time"].label_widget)
    assert rows["openfoam.cell_size"].container.isEnabled()      # on the mesh page
    assert editor.values()["openfoam"] == {"mode": "vof"}
    rows["openfoam.cell_size"].widget.setText("1.5")
    switch.setChecked(False)
    assert "openfoam" not in editor.values()                     # off is off
    editor.deleteLater()


def test_measurement_files_are_used_for_calibration_or_validation(qgis_app, tmp_path,
                                                                 monkeypatch):
    editor, client = _paged(tmp_path, monkeypatch)
    table = editor.measurements
    assert table.table.rowCount() == 2
    roles = [table._rows[i]["use"].currentData() for i in range(2)]
    assert roles == ["calibration", "validation"]
    assert table._rows[1]["situation"].text() == "High flow"
    assert table._rows[1]["discharge"].text() == "5.3"
    assert not table._rows[0]["discharge"].isEnabled()           # the case's discharge

    table.add("gt/aug.gpkg")
    table.set_role(2, "validation", "Summer", "54.3")
    data = editor.values()
    assert [s["values"] for s in data["ground_truth"]["sources"]] == ["gt/nov.gpkg"]
    situations = {s["name"]: s for s in data["calibration"]["validation"]}
    assert situations["High flow"]["prescribed_elevation"] == 815.2    # kept
    assert situations["Summer"]["prescribed_flowrate"] == 54.3
    assert [s["values"] for s in situations["Summer"]["sources"]] == ["gt/aug.gpkg"]

    table.set_role(0, "validation", "Summer", "54.3")             # no calibration data
    assert "ground_truth" not in editor.values()
    assert _purple(editor.measurement_label)                       # calibration is on
    editor.save()
    assert any("Measurement files" in f.message for f in editor.findings)
    editor.deleteLater()


def test_the_measurement_table_keeps_the_kind_reads_edited_paths_and_shares_a_discharge(
        qgis_app, tmp_path, monkeypatch):
    case = {"project": {"name": "reach"},
            "ground_truth": {"sources": [{"category": "hydraulics",
                                          "values": "ft/nov.ft.sum.xlsx",
                                          "positions": "ft/nov.gpkg"}]},
            "calibration": {"validation": [{"name": "High flow",
                                            "prescribed_flowrate": 5.3,
                                            "sources": [{"category": "hydraulics",
                                                         "kind": "points",
                                                         "values": "gt/sep.gpkg"}]}]}}
    editor, _client = _paged(tmp_path, monkeypatch, case)
    table = editor.measurements
    # a source without a kind is a FlowTracker source, and is written back without one
    assert table._rows[0]["kind"].currentData() == "flowtracker"
    assert editor.values() == case and not editor.dirty
    # a corrected path in the table is what is saved
    table.table.item(0, 0).setText("ft/nov-corrected.xlsx")
    table.table.item(0, 1).setText("")
    source = editor.values()["ground_truth"]["sources"][0]
    assert source["values"] == "ft/nov-corrected.xlsx" and "positions" not in source
    assert "kind" not in source
    # a second file of the same situation, its discharge left empty, keeps the discharge
    table.add("gt/sep-b.gpkg")
    table.set_role(2, "validation", "High flow")
    situation = editor.values()["calibration"]["validation"][0]
    assert situation["prescribed_flowrate"] == 5.3 and len(situation["sources"]) == 2
    # the discharge is one of the situation: changed in one row, changed in all
    table._rows[1]["discharge"].setText("6.1")
    assert table._rows[2]["discharge"].text() == "6.1"
    assert editor.values()["calibration"]["validation"][0]["prescribed_flowrate"] == 6.1
    editor.deleteLater()


def test_a_file_for_the_validation_switches_the_calibration_on(qgis_app, tmp_path,
                                                               monkeypatch):
    case = {"project": {"name": "reach"}, "boundaries": {"prescribed_flowrate": 2.4},
            "ground_truth": {"sources": [{"category": "hydraulics", "kind": "points",
                                          "values": "gt/nov.gpkg"}]}}
    editor, _client = _paged(tmp_path, monkeypatch, case)
    switch = editor.switches["calibration"]
    assert not switch.isChecked()
    editor.measurements.add("gt/sep.gpkg")
    editor.measurements.set_role(1, "validation", "High flow", "5.3")
    assert switch.isChecked()
    assert editor.values()["calibration"]["validation"][0]["name"] == "High flow"
    editor.deleteLater()


def test_a_multiple_choice_is_check_boxes_and_its_default_is_not_written(
        qgis_app, tmp_path, monkeypatch):
    editor, _client = _paged(tmp_path, monkeypatch)
    row = editor.rows["postproc.telemac_maps"]
    assert [c for c, box in row.boxes.items() if box.isChecked()] == ["depth", "velocity"]
    assert "postproc" not in editor.values()                     # the default stays
    row.boxes["free-surface"].setChecked(True)
    assert editor.values()["postproc"]["telemac_maps"] == ["depth", "velocity",
                                                           "free-surface"]
    editor.deleteLater()


def test_a_selected_file_is_written_relative_to_the_case_where_it_is_near(tmp_path):
    from axqua_plugin.gui.case_editor import stored_path

    folder = tmp_path / "cases" / "reach"
    assert stored_path(str(folder / "data" / "dem.tif"), folder) == "data/dem.tif"
    assert stored_path(str(tmp_path / "cases" / "other" / "dem.tif"), folder) == \
        "../other/dem.tif"
    far = "/srv/geodata/survey/2025/dem.tif"
    assert stored_path(far, folder) == far           # somewhere else entirely


def test_text_is_converted_by_the_kind_of_its_setting():
    from axqua_plugin.gui.case_editor import parse

    assert parse("int", "12") == 12 and parse("int", "2.5") == 2.5
    assert parse("number", " 0.35 ") == 0.35
    assert parse("text", "auto") == "auto" and parse("text", "3") == 3
    assert parse("vector", "data/lines.gpkg") == "data/lines.gpkg"
    assert parse("number", "") is None
    assert parse("number", "fast") == "fast"         # kept; the check says what is wrong


def test_the_case_setup_tab_has_case_settings_and_no_project_file(dock, tmp_path,
                                                                   monkeypatch):
    """The user's correction of 2026-10-16: a project is a case. The list of cases is
    kept in the settings of QGIS, and the box Case settings opens the active case."""
    import json

    from qgis.PyQt.QtWidgets import QGroupBox, QPushButton

    from axqua_plugin.core import project as project_io

    tab = dock.case_tab
    titles = [box.title() for box in tab.findChildren(QGroupBox)]
    assert titles == ["Cases", "Case settings"]
    labels = [button.text() for button in tab.findChildren(QPushButton)]
    assert "Open case settings..." in labels
    assert not {"Open project...", "Save project", "Save as...", "Edit case..."} & set(labels)
    assert not tab.settings_button.isEnabled()                  # no case yet

    first, second = tmp_path / "a.axq-case", tmp_path / "b.axq-case"
    for case in (first, second):
        case.write_text("project: {name: x}\n", encoding="utf-8")
    dock.add_case(first)
    dock.add_case(second)
    assert tab.settings_button.isEnabled() and str(first) in tab.case_label.text()
    dock.set_active_case(str(second))
    assert str(second) in tab.case_label.text()
    stored = json.loads(dock.remembered)
    assert stored == {"cases": [str(first), str(second)], "active_case": str(second)}
    dock.remove_case(second)
    assert json.loads(dock.remembered)["cases"] == [str(first)]

    # a project file of an earlier version adds its cases
    old = project_io.AxquaProject(path=tmp_path / "old.axqua-prj")
    old.add_case(second)
    project_io.save(old)
    dock.import_project(tmp_path / "old.axqua-prj")
    assert json.loads(dock.remembered)["cases"] == [str(first), str(second)]


def test_the_case_tab_opens_the_editor_and_takes_over_what_it_found(dock, tmp_path,
                                                                    monkeypatch):
    from axqua_plugin.gui import case_editor, case_tab

    case = tmp_path / "reach.axq-case"
    case.write_text("project: {name: reach}\n", encoding="utf-8")
    dock.ctx.project.add_case(case)
    dock.ctx.set_runner_ok(True)

    class Client:
        def case_read(self, path):
            return {"path": str(path), "folder": str(tmp_path), "data": CASE}

        def schema(self):
            return SCHEMA

        def case_check(self, path):
            return {"findings": [{"severity": "warning", "code": "axqua.config.incomplete",
                                  "subject": "boundaries.prescribed_flowrate",
                                  "message": "no discharge is set"}]}

    dock.ctx.client = Client()
    now = lambda title, call, on_success=None, on_error=None, owner=None: on_success(call())  # noqa: E731
    monkeypatch.setattr(case_tab, "run_async", now)
    opened = []
    monkeypatch.setattr(case_tab, "exec_dialog", lambda editor: opened.append(editor))
    dock.case_tab.check_case()
    index = dock.index_of("case")
    assert not dock.tabs.tabIcon(index).isNull()               # the tab carries it
    dock.case_tab.edit_case()
    (editor,) = opened
    assert isinstance(editor, case_editor.CaseEditor)
    # the editor opens with what the check had found
    assert not editor.rows["boundaries.prescribed_flowrate"].triangle.isHidden()
    editor.deleteLater()


# ------------------------------------------------------------------ the example case


EXAMPLES = {"source": "https://example.org/cases", "examples": [
    {"name": "example-isar", "title": "Isar River, losing and gaining reach",
     "summary": "A braided gravel-bed reach.", "case": "example-isar.axq-case",
     "files": 13, "bytes": 6200000, "megabytes": 6.2}]}


def test_an_example_case_is_downloaded_and_added_to_the_cases(dock, tmp_path,
                                                              monkeypatch):
    """One button on the Case Setup tab: which examples there are and where they come
    from is the answer of aXqua, the window only asks for the folder."""
    from qgis.PyQt.QtWidgets import QPushButton

    from axqua_plugin.core.runner_client import RunnerError
    from axqua_plugin.gui import case_tab, example_dialog
    from axqua_plugin.gui import help as help_pages

    asked = []

    class Client:
        fail = False

        def example_list(self):
            return EXAMPLES

        def example_get(self, name, folder):
            asked.append((name, folder))
            if self.fail:
                raise RunnerError(f"{folder}/{name} is there already",
                                  code="axqua.config", remedy="Choose another folder.")
            case = Path(folder) / name / f"{name}.axq-case"
            case.parent.mkdir(parents=True)
            case.write_text("project: {name: example-isar}\n", encoding="utf-8")
            return {"name": name, "folder": str(case.parent), "case": str(case),
                    "guide": str(case.parent / "README.md"), "files": 13,
                    "megabytes": 6.2}

    dock.ctx.client = Client()
    dock.ctx.set_runner_ok(True)
    now = lambda title, call, on_success=None, on_error=None, owner=None: _now(  # noqa: E731
        call, on_success, on_error)
    monkeypatch.setattr(example_dialog, "run_async", now)
    opened = []
    monkeypatch.setattr(case_tab, "exec_dialog", lambda window: opened.append(window))

    labels = [button.text() for button in dock.case_tab.findChildren(QPushButton)]
    assert "Example case..." in labels
    dock.case_tab.get_example()
    (window,) = opened
    assert isinstance(window, example_dialog.ExampleDialog)
    assert window.choice.count() == 1
    assert window.choice.currentText() == "Isar River, losing and gaining reach"
    assert "6.2 MB in 13 files" in window.description.text()
    assert window.get_button.isEnabled() and not window.folder_button.isEnabled()

    window.folder.setText(str(tmp_path / "work"))
    window.get()
    case = tmp_path / "work" / "example-isar" / "example-isar.axq-case"
    assert asked == [("example-isar", str(tmp_path / "work"))]
    assert any(Path(item).name == case.name for item in dock.ctx.project.cases)
    assert "README.md" in window.status.text() and "Preprocessing" in window.status.text()
    assert window.folder_button.isEnabled()

    # a refusal is shown with its remedy, and the window stays usable
    Client.fail = True
    window.get()
    assert "is there already" in window.status.text()
    assert "Choose another folder" in window.status.text()
    assert window.get_button.isEnabled()

    # Help opens the paragraph on the example case
    urls = []
    monkeypatch.setattr(help_pages, "open_url", lambda url: urls.append(url) or True)
    help_pages.open_window_help(example_dialog.HELP_KEY)
    assert urls[-1].endswith("usage/case-setup.html#help-example-case")
    window.deleteLater()


def _now(call, on_success, on_error):
    try:
        answer = call()
    except Exception as exc:                                    # noqa: BLE001
        on_error(exc)
    else:
        on_success(answer)


def test_no_example_is_said_so(qgis_app, monkeypatch):
    from axqua_plugin.gui import example_dialog

    class Client:
        def example_list(self):
            return {"source": "", "examples": []}

    monkeypatch.setattr(example_dialog, "run_async",
                        lambda title, call, on_success=None, on_error=None, owner=None:
                        _now(call, on_success, on_error))
    window = example_dialog.ExampleDialog(Client())
    assert window.status.text() == "No example case was found."
    assert not window.get_button.isEnabled()
    window.deleteLater()


# ---------------------------------------------------------- the installation wizards


def _overview(running=None):
    return {
        "host": {"description": "Debian GNU/Linux 12 (bookworm), x86_64",
                 "supported": True, "base": "debian12"},
        "default_jobs": 8,
        "openfoam_found": "/usr/lib/openfoam/openfoam2406/etc/bashrc",
        "targets": [
            {"target": "telemac", "title": "TELEMAC", "installed": {},
             "running": running, "last": running, "default_folder": "/home/x/opt",
             "default_folders": {"user": "/home/x/opt", "system": "/opt/telemac"}},
            {"target": "openfoam", "title": "OpenFOAM v2406 with the sediment solvers",
             "installed": {"openfoam": "/usr/lib/openfoam/openfoam2406/etc/bashrc"},
             "running": None, "last": None,
             "default_folder": "/home/x/.local/openfoam-sediment-v2406"},
            {"target": "postprocessors", "title": "ParaView and VisIt",
             "installed": {}, "running": None, "last": {"state": "failed"},
             "default_folder": "/home/x/.local/axqua-postprocessors"},
        ],
    }


def _plan(*, missing=(), ready=True, findings=(), elevation="pkexec"):
    command = "sudo apt-get update && sudo apt-get install -y " + " ".join(missing)
    return {
        "target": "telemac", "title": "TELEMAC", "folder": "/home/x/opt", "ready": ready,
        "host": {"description": "Debian GNU/Linux 12 (bookworm), x86_64"},
        "notes": ["TELEMAC is installed in /home/x/opt/telemac-mascaret."],
        "estimate": "30 to 60 minutes",
        "steps": [{"name": "Download and build TELEMAC",
                   "command": "/bin/bash telemac_debian12_installer.sh --skip-apt"}],
        "packages": {"needed": ["git", "gfortran", "cmake"], "missing": list(missing),
                     "unavailable": [], "command": command if missing else "",
                     "elevation": elevation if missing else ""},
        "findings": list(findings),
    }


class _InstallClient:
    def __init__(self, plan=None, statuses=()):
        self.plan = plan or _plan()
        self.statuses = list(statuses)
        self.calls: list[tuple] = []

    def install_overview(self):
        return _overview()

    def install_plan(self, target, options):
        self.calls.append(("plan", target, dict(options)))
        return self.plan

    def install_packages(self, target, options, *, elevate=False):
        self.calls.append(("packages", target, elevate))
        self.plan = _plan()                        # they are installed now
        return {"installation": {"message": "the packages were installed"}}

    def install_start(self, target, options):
        self.calls.append(("start", target, dict(options)))
        return {"id": "20261008-120000-telemac", "state": "queued", "steps": ["Build"]}

    def install_status(self, ident, *, tail=0):
        self.calls.append(("status", ident, tail))
        return self.statuses.pop(0)

    def install_cancel(self, ident):
        self.calls.append(("cancel", ident))
        return {"id": ident, "state": "cancelled", "message": "cancelled"}


def _wizard_now(monkeypatch):
    from axqua_plugin.gui import install_wizard

    def now(title, call, on_success=None, on_error=None, owner=None):
        try:
            answer = call()
        except Exception as exc:                 # noqa: BLE001 - as the task would
            on_error(exc)
        else:
            on_success(answer)

    monkeypatch.setattr(install_wizard, "run_async", now)
    return install_wizard


def test_the_wizard_starts_from_what_aXqua_found_and_sends_what_was_chosen(
        qgis_app, monkeypatch):
    install_wizard = _wizard_now(monkeypatch)
    telemac = install_wizard.InstallWizard(_InstallClient(), "telemac", _overview())
    assert telemac.options() == {"scope": "user", "folder": "/home/x/opt", "tag": "",
                                 "salome": "",
                                 "telemac_examples": "inputs", "bind": True,
                                 "base": "", "installers": ""}
    # of the example cases, only what a run reads is downloaded unless asked otherwise
    examples = telemac._widgets["telemac_examples"]
    assert [examples.itemData(i) for i in range(examples.count())] == \
        ["inputs", "all", "none"]
    examples.setCurrentIndex(2)
    assert telemac.options()["telemac_examples"] == "none"
    # for me alone or for all users; the folder follows the choice until it is typed
    scope = telemac._widgets["scope"]
    assert [scope.itemData(i) for i in range(scope.count())] == ["user", "system"]
    scope.setCurrentIndex(1)
    assert telemac.options()["folder"] == "/opt/telemac"
    scope.setCurrentIndex(0)
    assert telemac.options()["folder"] == "/home/x/opt"
    telemac._widgets["folder"].setText("/data/telemac")
    scope.setCurrentIndex(1)
    assert telemac.options()["folder"] == "/data/telemac"
    scope.setCurrentIndex(0)
    assert telemac.pages.currentIndex() == 0 and telemac.next_button.text() == "Next"
    telemac.deleteLater()

    openfoam = install_wizard.InstallWizard(_InstallClient(), "openfoam", _overview())
    chosen = openfoam.options()
    # an OpenFOAM v2406 is on this computer, so the sediment solvers build on it
    assert chosen["reuse_openfoam"] == "/usr/lib/openfoam/openfoam2406/etc/bashrc"
    assert (chosen["jobs"], chosen["viewers"], chosen["examples"],
            chosen["smoke_test"]) == (8, "both", False, True)
    # VisIt is recommended and can be left out; the recommended answer comes first
    viewers = openfoam._widgets["viewers"]
    assert [viewers.itemData(i) for i in range(viewers.count())] == \
        ["both", "paraview", "none"]
    assert viewers.itemText(0) == "ParaView and VisIt (recommended)"
    viewers.setCurrentIndex(1)
    assert openfoam.options()["viewers"] == "paraview"
    openfoam._widgets["reuse_openfoam"].setCurrentIndex(1)       # compile it instead
    assert openfoam.options()["reuse_openfoam"] == "no"
    assert not openfoam._widgets["openfoam_path"].isEnabled()
    openfoam.deleteLater()

    nothing_found = dict(_overview(), openfoam_found="")
    fresh = install_wizard.InstallWizard(_InstallClient(), "openfoam", nothing_found)
    assert fresh.options()["reuse_openfoam"] == "no"             # nothing to build on
    fresh.deleteLater()

    # the installation of the postprocessors installs at least one of them
    programs = install_wizard.InstallWizard(_InstallClient(), "postprocessors",
                                            _overview())
    viewers = programs._widgets["viewers"]
    assert [viewers.itemData(i) for i in range(viewers.count())] == ["both", "paraview"]
    assert programs.options()["viewers"] == "both"
    programs.deleteLater()


def test_missing_packages_are_shown_with_their_command_and_do_not_block(
        qgis_app, monkeypatch):
    """The plugin never asks for a password: it shows the command, and on a desktop
    lets the system ask."""
    from qgis.PyQt.QtWidgets import QApplication

    install_wizard = _wizard_now(monkeypatch)
    warning = {"severity": "warning", "code": "axqua.install.packages_missing",
               "subject": "install.telemac.packages", "message": "1 package is missing"}
    exists = {"severity": "warning", "code": "axqua.install.folder_exists",
              "subject": "install.telemac.folder", "message": "the folder exists"}
    client = _InstallClient(_plan(missing=["gfortran"], findings=[warning, exists]))
    wizard = install_wizard.InstallWizard(client, "telemac", _overview())
    wizard.go_next()
    assert wizard.pages.currentIndex() == 1 and wizard.install_button.text() == "Install"
    assert client.calls[0][:2] == ("plan", "telemac")
    assert "1 of 3 system packages" in wizard.package_label.text()
    assert wizard.package_command.toPlainText().endswith("install -y gfortran")
    # the required actions in a terminal, under their heading in aXqua purple
    assert not wizard.actions_heading.isHidden()
    assert wizard.actions_heading.text() == "REQUIRED USER ACTIONS in TERMINAL:"
    assert "#2e014b" in wizard.actions_heading.styleSheet()
    assert "#2e014b" in wizard.package_command.styleSheet()
    assert wizard.package_command.minimumHeight() >= 120
    assert not wizard.copy_button.isHidden() and wizard.fix_button.isEnabled()
    assert wizard.install_button.isEnabled()                     # a warning only
    assert len(wizard.finding_list.findings) == 2
    assert not wizard._triangles["folder"].isHidden()            # at the row concerned
    assert wizard._triangles["tag"].isHidden()

    wizard.copy_command()
    assert QApplication.clipboard().text().endswith("install -y gfortran")

    wizard.install_packages()                                    # the desktop asks
    assert ("packages", "telemac", True) in client.calls
    assert "All 3 system packages" in wizard.package_label.text()
    assert wizard.copy_button.isHidden() and wizard.actions_heading.isHidden()
    assert "The packages were installed" in wizard.plan_label.text()
    wizard.deleteLater()

    terminal = _InstallClient(_plan(missing=["gfortran"], elevation=""))
    plain = install_wizard.InstallWizard(terminal, "telemac", _overview())
    plain.go_next()
    assert "terminal" in plain.fix_button.toolTip()              # no desktop dialog
    plain.deleteLater()


def test_an_installation_that_cannot_work_cannot_be_started(qgis_app, monkeypatch):
    install_wizard = _wizard_now(monkeypatch)
    error = {"severity": "error", "code": "axqua.install.unsupported_system",
             "subject": "install.telemac", "message": "there is no installer"}
    client = _InstallClient(_plan(ready=False, findings=[error]))
    wizard = install_wizard.InstallWizard(client, "telemac", _overview())
    wizard.go_next()
    assert not wizard.install_button.isEnabled()
    assert "cannot be started" in wizard.install_button.toolTip()
    wizard.go_back()
    assert wizard.pages.currentIndex() == 0 and wizard.next_button.isEnabled()
    wizard.deleteLater()


def test_an_installation_is_followed_until_it_ends_and_reported_once(
        qgis_app, monkeypatch):
    install_wizard = _wizard_now(monkeypatch)
    running = {"id": "20261008-120000-telemac", "state": "running", "step": 1,
               "steps": ["Download and build TELEMAC"], "elapsed": 190.0,
               "step_name": "Download and build TELEMAC", "log": "/x/install.log",
               "log_tail": "[ 42%] Building Fortran object"}
    done = dict(running, state="succeeded", elapsed=2400.0, findings=[],
                bound="/home/x/.config/axqua/default.axq-profile",
                outputs={"solvers.telemac.setup_script": "/home/x/opt/pysource.sh"},
                log_tail="[*] Installation finished.")
    client = _InstallClient(statuses=[running, done, done])
    reported = []
    wizard = install_wizard.InstallWizard(client, "telemac", _overview(),
                                          on_done=reported.append)
    wizard.go_next()
    wizard.install_button.click()
    assert client.calls[-1][0] == "start"
    assert wizard.pages.currentIndex() == 2 and wizard.timer.isActive()
    assert wizard.next_button.isHidden() and not wizard.back_button.isHidden()
    assert not wizard.install_button.isEnabled()                 # one at a time

    wizard.poll()
    assert "step 1 of 1" in wizard.state_label.text()
    assert "3 min" in wizard.state_label.text()
    assert wizard.log_view.toPlainText() == "[ 42%] Building Fortran object"
    assert wizard.cancel_button.isEnabled() and not reported

    wizard.poll()
    assert "finished" in wizard.state_label.text()
    assert "default.axq-profile" in wizard.state_label.text()
    assert not wizard.timer.isActive()
    # the cancel button says that the work is done, in dark gray-green
    assert wizard.cancel_button.text() == "Installation complete"
    assert "#5b7a5e" in wizard.cancel_button.styleSheet()
    assert len(reported) == 1 and reported[0]["state"] == "succeeded"
    wizard.show_status(done)                                     # shown again
    assert len(reported) == 1
    wizard.deleteLater()


def test_a_running_installation_is_shown_when_the_wizard_is_opened_again(
        qgis_app, monkeypatch):
    """QGIS was closed in between: the installation went on by itself."""
    from qgis.PyQt.QtWidgets import QMessageBox

    install_wizard = _wizard_now(monkeypatch)
    running = {"id": "20261008-120000-telemac", "state": "running", "step": 2,
               "steps": ["Prepare Git", "Download and build TELEMAC"],
               "step_name": "Download and build TELEMAC", "elapsed": 600.0}
    client = _InstallClient()
    wizard = install_wizard.InstallWizard(client, "telemac", _overview(running))
    assert wizard.pages.currentIndex() == 2 and wizard.timer.isActive()
    assert wizard.install_id == "20261008-120000-telemac"
    assert "step 2 of 2" in wizard.state_label.text()

    yes = install_wizard.enum_value(QMessageBox, "StandardButton.Yes", "Yes")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: yes)
    wizard.cancel_installation()
    assert ("cancel", "20261008-120000-telemac") in client.calls
    assert "cancelled" in wizard.state_label.text() and not wizard.timer.isActive()
    wizard.deleteLater()


def test_the_help_of_a_wizard_opens_the_section_on_its_program(qgis_app, monkeypatch):
    from axqua_plugin.gui import help as help_pages

    install_wizard = _wizard_now(monkeypatch)
    opened = []
    monkeypatch.setattr(help_pages, "open_url", lambda url: opened.append(url) or True)
    for target in ("telemac", "openfoam", "postprocessors"):
        wizard = install_wizard.InstallWizard(_InstallClient(), target, _overview())
        wizard.open_help()
        wizard.deleteLater()
    assert [url.split("/latest/")[1] for url in opened] == [
        "installation/simulation-software.html#install-telemac",
        "installation/simulation-software.html#install-openfoam",
        "installation/postprocessors.html#help-postprocessors"]


def test_the_tabs_of_a_wizard_can_be_visited_in_any_order(qgis_app, monkeypatch):
    install_wizard = _wizard_now(monkeypatch)
    client = _InstallClient()
    wizard = install_wizard.InstallWizard(client, "telemac", _overview())
    # the installation folder is the one setting that is required: aXqua purple
    from qgis.PyQt.QtWidgets import QLabel
    folder = next(label for label in wizard.findChildren(QLabel)
                  if label.text() == "Installation folder")
    assert "#2e014b" in folder.styleSheet()
    assert [wizard.tabs.tabText(i) for i in range(3)] == \
        ["Settings", "Check", "Installation"]
    wizard.tabs.setCurrentIndex(2)                               # straight there
    assert "No installation has been started" in wizard.state_label.text()
    assert not wizard.cancel_button.isEnabled()
    wizard.tabs.setCurrentIndex(1)                               # checks on arrival
    assert [call[0] for call in client.calls] == ["plan"]
    wizard.tabs.setCurrentIndex(0)
    wizard.tabs.setCurrentIndex(1)                               # same settings
    assert [call[0] for call in client.calls] == ["plan"]
    wizard.tabs.setCurrentIndex(0)
    wizard._widgets["tag"].setText("v9.0.0")
    wizard.tabs.setCurrentIndex(1)                               # other settings
    assert [call[0] for call in client.calls] == ["plan", "plan"]
    assert client.calls[-1][2]["tag"] == "v9.0.0"
    wizard.deleteLater()


class _StubbornClient(_InstallClient):
    """Each round of fixes installs one package, until the last one will not go."""

    def __init__(self, missing):
        super().__init__(_plan(missing=missing))
        self.missing = list(missing)

    def install_plan(self, target, options):
        self.calls.append(("plan", target, dict(options)))
        return _plan(missing=self.missing)

    def install_packages(self, target, options, *, elevate=False):
        self.calls.append(("packages", target, elevate))
        if len(self.missing) > 1:
            self.missing.pop(0)
        return {"installation": {"message": "the command ended with code 100"}}


def test_run_fixes_repeats_while_it_helps_and_then_offers_a_report(qgis_app,
                                                                  monkeypatch):
    install_wizard = _wizard_now(monkeypatch)
    client = _StubbornClient(["gfortran", "cmake", "libmetis-dev"])
    wizard = install_wizard.InstallWizard(client, "telemac", _overview())
    wizard.go_next()
    assert wizard.report_button.isHidden()
    wizard.fix_button.click()
    # two rounds helped, the third found the same package missing as before
    assert [c[0] for c in client.calls].count("packages") == 3
    assert wizard.missing() == ["libmetis-dev"]
    assert not wizard.report_button.isHidden()
    assert "libmetis-dev" in wizard.package_label.text()
    assert "code 100" in wizard.package_label.text()

    shown = []
    monkeypatch.setattr(install_wizard, "exec_dialog",
                        lambda dialog: shown.append(dialog) or 0)
    wizard.report()
    text = shown[0].report_text.toPlainText()
    assert "libmetis-dev" in text and "3 round(s)" in text
    assert shown[0].report_title.text() == "Installation of telemac does not work out"
    wizard.deleteLater()

    helped = _StubbornClient(["gfortran"])
    helped.install_packages = lambda target, options, elevate=False: (
        helped.missing.clear() or {"installation": {"message": ""}})
    done = install_wizard.InstallWizard(helped, "telemac", _overview())
    done.go_next()
    done.fix_button.click()
    assert "The fixes worked" in done.package_label.text()
    assert done.report_button.isHidden() and done.install_button.isEnabled()
    done.deleteLater()


def test_a_report_is_shown_before_it_is_sent_and_names_no_home_folder(qgis_app):
    from axqua_plugin.gui import report_issue

    plan = _plan(missing=["gfortran"])
    plan["findings"] = [{"severity": "error", "code": "axqua.install.x",
                         "message": f"{Path.home()}/opt cannot be written"}]
    title, body = report_issue.installation_report("openfoam", plan, rounds=2)
    assert "~/opt cannot be written" in body and str(Path.home()) not in body
    assert "gfortran" in body and "2 round(s)" in body
    info = report_issue.metadata()
    assert info["tracker"].startswith("https://github.com/") and "@" in info["email"]
    url = report_issue.issue_url(title, body, info["tracker"])
    assert url.startswith(info["tracker"] + "/new?title=Installation")
    long = report_issue.mail_url(title, "x" * 5000, "a@b.c")
    assert long.startswith("mailto:a@b.c?subject=") and len(long) < 2500


def test_an_installed_program_is_installed_again_only_after_a_question(dock,
                                                                       monkeypatch):
    from qgis.PyQt.QtWidgets import QMessageBox

    from axqua_plugin.gui import configuration_tab

    tab = dock.configuration_tab
    tab.show_software(_overview())
    # found in dark green, not found in aXqua purple and bold
    assert "#1b5e20" in tab._program_labels["openfoam"].styleSheet()
    missing = tab._program_labels["postprocessors"]
    assert "#2e014b" in missing.styleSheet() and "bold" in missing.styleSheet()
    assert tab.findChildren(configuration_tab.QGroupBox)[-1].title() == \
        "Simulation && postprocessing software"

    opened = []
    monkeypatch.setattr(configuration_tab, "InstallWizard",
                        lambda *a, **k: opened.append(k) or object())
    monkeypatch.setattr(configuration_tab, "exec_dialog", lambda _w: 0)
    asked = []
    no = configuration_tab.enum_value(QMessageBox, "StandardButton.No", "No")
    yes = configuration_tab.enum_value(QMessageBox, "StandardButton.Yes", "Yes")
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or no)
    tab.open_wizard("openfoam")                                  # No: back to the tab
    assert asked == ["Software already installed. Continue with re-installation?"]
    assert opened == []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: yes)
    tab.open_wizard("openfoam")
    assert opened[-1]["reinstall"] is True
    asked.clear()
    tab.open_wizard("postprocessors")                            # nothing to ask
    assert asked == [] and opened[-1]["reinstall"] is False


def test_a_wizard_for_an_installed_program_says_what_happens_to_it(qgis_app,
                                                                   monkeypatch,
                                                                   tmp_path):
    install_wizard = _wizard_now(monkeypatch)
    overview = _overview()
    entry = next(t for t in overview["targets"] if t["target"] == "telemac")
    entry["existing"] = {"folder": str(tmp_path / "opt"), "removable": True,
                         "removes": str(tmp_path / "opt" / "telemac-mascaret")}
    wizard = install_wizard.InstallWizard(_InstallClient(), "telemac", overview,
                                          reinstall=True)
    choice = wizard._widgets["reinstall"]
    assert [choice.itemData(i) for i in range(choice.count())] == \
        ["replace", "continue", "new"]
    assert wizard.options()["reinstall"] == "replace"
    assert wizard.options()["folder"] == str(tmp_path / "opt")
    choice.setCurrentIndex(2)                                    # a new folder
    assert wizard.options()["reinstall"] == ""
    assert wizard.options()["folder"] == str(tmp_path / "opt-2")
    assert wizard.windowTitle().startswith("Install again")
    wizard.deleteLater()

    entry["existing"] = {"folder": "/usr/lib/openfoam/openfoam2406", "removable": False}
    foreign = install_wizard.InstallWizard(_InstallClient(), "telemac", overview,
                                           reinstall=True)
    choice = foreign._widgets["reinstall"]
    assert [choice.itemData(i) for i in range(choice.count())] == ["new"]
    assert "leaves it as it is" in foreign.reinstall_note.text()
    foreign.deleteLater()


def test_the_configuration_tab_says_what_is_installed_and_what_is_running(dock):
    from axqua_plugin.gui.configuration_tab import describe_program

    tab = dock.configuration_tab
    assert not tab._program_buttons["telemac"].isEnabled()      # nothing is known yet
    running = {"id": "20261008-120000-telemac", "state": "running", "step": 2,
               "steps": ["a", "b"], "elapsed": 720.0}
    tab.show_software(_overview(running))
    assert "Debian GNU/Linux 12" in tab.host_label.text()
    assert tab._program_labels["telemac"].text() == \
        "The installation is running, step 2 of 2 (12 min)."
    assert tab._program_buttons["telemac"].text() == "Show the installation..."
    assert tab._program_labels["openfoam"].text().endswith("openfoam2406/etc/bashrc")
    assert tab._program_buttons["openfoam"].text() == "Install OpenFOAM..."
    assert "failed" in tab._program_labels["postprocessors"].text()
    assert tab.install_timer.isActive()                          # it keeps looking
    tab.show_software(_overview())
    assert not tab.install_timer.isActive()
    assert tab._program_buttons["telemac"].text() == "Install TELEMAC..."
    assert describe_program({}) == "Not found on this computer."


# ------------------------------------------------- results for ParaView and VisIt


def _export_list(exported=False, newer=False):
    done = {"pvd": "/c/vtk/r2d.pvd", "visit": "/c/vtk/r2d.visit",
            "up_to_date": not newer} if exported else \
        {"pvd": "", "visit": "", "up_to_date": False}
    return {"folder": "/c/vtk", "results": [
        {"name": "r2d.slf", "label": "steady simulation, 2D", "frames": 26,
         "kind": "2d", "exported": done},
        {"name": "r3d.slf", "label": "3D simulation", "frames": 6, "kind": "3d",
         "exported": {"pvd": "", "visit": "", "up_to_date": False}}]}


class _ExportClient:
    def __init__(self):
        self.calls, self.exported = [], False

    def export_list(self, case):
        return _export_list(self.exported)

    def export_results(self, case, names, *, frames="all"):
        self.calls.append((list(names), frames))
        self.exported = True
        return {"folder": "/c/vtk", "exports": [
            {"files": ["a.vtu"] * 26, "megabytes": 31.0} for _ in names]}


@pytest.fixture
def program_pages(dock, tmp_path, monkeypatch):
    """The ParaView and the VisIt page of a panel with one case, answered at once."""
    from axqua_plugin.gui import section_pages

    case = tmp_path / "reach.axq-case"
    case.write_text("project: {name: reach}\n", encoding="utf-8")
    dock.ctx.project.add_case(case)
    dock.ctx.set_runner_ok(True)
    dock.ctx.client = _ExportClient()
    now = lambda title, call, on_success=None, on_error=None, owner=None: on_success(call())  # noqa: E731
    monkeypatch.setattr(section_pages, "run_async", now)
    started = []
    monkeypatch.setattr(section_pages, "launch",
                        lambda program, arguments: started.append((program, arguments))
                        or True)
    return dock, dock.page("postprocessing", "paraview"), \
        dock.page("postprocessing", "visit"), started


def test_the_results_of_a_case_are_listed_exported_and_opened(program_pages):
    dock, paraview, visit, started = program_pages
    paraview.refresh()
    assert paraview.table.rowCount() == 2
    assert [paraview.table.item(0, column).text() for column in range(4)] == \
        ["r2d.slf", "steady simulation, 2D", "26", "no"]
    assert paraview.export_button.isEnabled()
    assert not paraview.open_button.isEnabled()              # nothing exported yet
    assert "No ParaView program was found" in paraview.open_button.toolTip()

    paraview.table.selectRow(0)
    paraview.frames.setCurrentIndex(1)                       # only the last time step
    paraview.export()
    assert dock.ctx.client.calls == [(["r2d.slf"], "last")]
    assert "Exported 1 result(s) with 26 time step(s)" in paraview.status.text()
    assert paraview.table.item(0, 3).text() == "yes"

    # without a program there is nothing to start, and the page says so
    paraview.table.selectRow(0)
    assert not paraview.open_button.isEnabled()
    assert "No ParaView program was found" in paraview.program.text()

    # a program aXqua found by itself serves until a profile names one
    dock.configuration_tab.show_software({"targets": [
        {"target": "postprocessors", "installed": {"paraview": "/usr/bin/paraview"}}]})
    paraview.table.selectRow(0)
    assert paraview.open_button.isEnabled()
    assert "found on this computer" in paraview.program.text()
    assert not visit.program_path                            # no VisIt was found

    profile = {"postprocessors": {"paraview": "/usr/bin/paraview",
                                  "visit": "/opt/visit/bin/visit"}}
    dock.profile_changed(profile)
    paraview.table.selectRow(0)
    assert paraview.open_button.isEnabled()
    paraview.open_program()
    # one export serves both programs; each opens its own index file, in its own way
    visit.refresh()
    visit.table.selectRow(0)
    visit.open_program()
    assert started == [("/usr/bin/paraview", ["/c/vtk/r2d.pvd"]),
                       ("/opt/visit/bin/visit", ["-o", "/c/vtk/r2d.visit"])]
    assert "was started" in visit.status.text()

    # a profile that names the installation folder only: the launcher found in it
    dock.profile_changed({"installations": {"visit": "/data/visit"},
                          "programs": {"visit": "/data/visit/bin/visit"}})
    assert visit.program_path == "/data/visit/bin/visit"


def test_an_openfoam_case_is_opened_in_paraview_and_visit_without_export(program_pages):
    dock, paraview, visit, started = program_pages
    payload = dict(_export_list(), openfoam=[
        {"label": "OpenFOAM free surface", "folder": "/c/openfoam", "run": True,
         "paraview": "/c/openfoam/case.foam", "visit": "/c/openfoam/system/controlDict"}])
    dock.profile_changed({"postprocessors": {"paraview": "/usr/bin/paraview",
                                             "visit": "/opt/visit/bin/visit"}})
    for page in (paraview, visit):
        page.show_results(payload)
        assert page.foam_list.count() == 1 and page.foam_button.isEnabled()
        page.open_openfoam()
    assert started == [("/usr/bin/paraview", ["/c/openfoam/case.foam"]),
                       ("/opt/visit/bin/visit", ["-o", "/c/openfoam/system/controlDict"])]
    visit.show_results(_export_list())                       # a case without OpenFOAM
    assert visit.foam_list.count() == 0 and not visit.foam_button.isEnabled()


def test_a_map_style_the_case_asks_for_reaches_the_loader(tmp_path):
    import json

    from axqua_plugin.core import result_loader, styles

    mesh = _write_mesh(tmp_path)
    (tmp_path / "results").mkdir(exist_ok=True)
    (tmp_path / "results" / "results.json").write_text(json.dumps({
        "job_id": "J", "results": [{"name": "depth", "path": str(mesh), "kind": "mesh",
                                    "style": "water-depth", "variable": "WATER DEPTH",
                                    "extra": {"map_style": "depth-arrows"}}]}))
    results = result_loader.discover(tmp_path)
    assert results.items[0].map_style == "depth-arrows"
    # the case file names a style by the keys of this plugin: aXqua lists the same
    import ast
    import re

    source = (Path(__file__).resolve().parents[2] / "src" / "axqua" / "config.py"
              ).read_text(encoding="utf-8")
    listed = re.search(r"^MAP_STYLES = (\(.*?\))$", source, re.MULTILINE | re.DOTALL)
    assert ast.literal_eval(listed.group(1)) == tuple(styles.MAP_STYLES)


def test_everything_is_exported_when_no_result_is_selected(program_pages):
    dock, paraview, _visit, _started = program_pages
    paraview.refresh()
    paraview.table.clearSelection()
    paraview.export()
    assert dock.ctx.client.calls == [(["r2d.slf", "r3d.slf"], "all")]


def test_an_export_older_than_its_result_is_named(program_pages):
    _dock, paraview, _visit, _started = program_pages
    paraview.show_results(_export_list(exported=True, newer=True))
    assert "newer than the export" in paraview.table.item(0, 3).text()
    paraview.show_results({}, note="Add a case on the tab Case Setup first.")
    assert paraview.table.rowCount() == 0 and not paraview.export_button.isEnabled()
    assert "Add a case" in paraview.status.text()


# ------------------------------------------------------------------- validation


def _validation_info(*, built=True, calibrated=True, reports=True, situation=True,
                     condition="stage_discharge"):
    return {
        "situations": [{"name": "September 2025", "inflows": {"1": 0.2, "3": 5.1},
                        "prescribed_elevation": 815.5,
                        "sources": [{"category": "hydraulics", "kind": "points",
                                     "positions": "data/september.gpkg"}]}]
        if situation else [],
        "boundaries": [{"index": 1, "kind": "inflow", "discharge": 0.8},
                       {"index": 2, "kind": "outflow", "discharge": None},
                       {"index": 3, "kind": "inflow", "discharge": 1.6}]
        if built else [],
        "outflow_condition": condition,
        "calibrated": {"values": {"zone4": 0.04}} if calibrated else None,
        "reports": [{"name": "September 2025", "figure": "/c/validation.png",
                     "notes": ["3 of the 84 points on the mesh are dry in the model."],
                     "summary": {"SCALAR VELOCITY": {
                         "n": 84, "bias": -0.12, "relative_bias": -0.142, "rmse": 0.4,
                         "within_error": 0.18}}}] if reports else [],
    }


class _ValidationClient:
    def __init__(self, info):
        self.info, self.written = info, []

    def validation_info(self, case):
        return self.info

    def case_read(self, case):
        if self.written:                        # what the last save left in the file
            return {"data": json.loads(json.dumps(self.written[-1]))}
        return {"data": {"project": {"name": "reach"},
                         "calibration": {"init_runs": 8, "validation": [
                             {"name": "old"}, {"name": "flood", "prescribed_flowrate": 40}]}}}

    def case_write(self, case, data):
        self.written.append(data)
        return {"path": str(case), "findings": []}


@pytest.fixture
def validation_box(dock, tmp_path, monkeypatch):
    from axqua_plugin.gui import validation_box

    case = tmp_path / "reach.axq-case"
    case.write_text("project: {name: reach}\n", encoding="utf-8")
    dock.ctx.project.add_case(case)
    dock.ctx.set_runner_ok(True)
    now = lambda title, call, on_success=None, on_error=None, owner=None: on_success(call())  # noqa: E731
    monkeypatch.setattr(validation_box, "run_async", now)
    submitted = []
    monkeypatch.setattr(type(dock.ctx), "submit",
                        lambda self, kind, options=None: submitted.append((kind, options)))
    box = dock.page("calibration").validation

    def use(info):
        dock.ctx.client = _ValidationClient(info)
        box.refresh()
        return dock.ctx.client

    return box, use, submitted, case


def test_the_validation_form_asks_for_another_situation_and_offers_no_split(
        validation_box):
    """Every inflow of the model with the discharge of the calibrated case, so that
    it is clear which is which; and no control that holds calibration data back."""
    from qgis.PyQt.QtWidgets import QAbstractButton, QLabel

    box, use, _submitted, _case = validation_box
    use(_validation_info())
    assert sorted(box._inflows) == [1, 3]
    assert (box._inflows[1].value(), box._inflows[3].value()) == (0.2, 5.1)
    labels = [label.text() for label in box.findChildren(QLabel)]
    assert "Discharge of inflow 1 (0.8 m³/s in the calibrated case)" in labels
    assert box.total.isHidden()                       # the inflows say it all
    assert box.level.isHidden()                       # the case computes the level
    assert box.name.text() == "September 2025"
    assert box.layer.text() == "data/september.gpkg"
    texts = " ".join(labels + [b.text() for b in box.findChildren(QAbstractButton)])
    for word in ("split", "leave-one-out", "cross-validation", "fraction", "%  of"):
        assert word not in texts.lower()
    # the result of the last run, in words
    assert "Flow velocity at 84 points" in box.result.text()
    assert "-0.12 m/s on average (-14 %)" in box.result.text()
    assert "dry in the model" in box.result.text()
    assert box.figure_button.isEnabled() and box.run_button.isEnabled()


def test_the_form_follows_what_the_case_needs(validation_box):
    box, use, _submitted, _case = validation_box
    use(_validation_info(built=False, calibrated=False, reports=False, situation=False,
                         condition="elevation"))
    assert not box._inflows and not box.total.isHidden()   # one total discharge
    assert not box.level.isHidden()                        # the case prescribes a level
    assert "not built yet" in box.hint.text()
    assert "no finished calibration" in box.hint.text()
    assert not box.run_button.isEnabled() and box.save_button.isEnabled()
    assert not box.figure_button.isEnabled() and box.result.text() == ""


def test_saving_writes_the_situation_and_keeps_the_rest_of_the_case(validation_box):
    box, use, submitted, case = validation_box
    client = use(_validation_info())
    box.name.setText("September 2025")
    box.layer.setText(str(case.parent / "data" / "september.gpkg"))
    box._inflows[1].setValue(0.25)
    box.save()
    (written,) = client.written
    assert written["project"] == {"name": "reach"}             # untouched
    assert written["calibration"]["init_runs"] == 8
    first, *others = written["calibration"]["validation"]
    assert first == {"name": "September 2025", "inflows": {1: 0.25, 3: 5.1},
                     "prescribed_elevation": 815.5,      # no field here: kept
                     "sources": [{"category": "hydraulics", "kind": "points",
                                  "positions": "data/september.gpkg"}]}
    assert others == [{"name": "flood", "prescribed_flowrate": 40}]   # a second one stays
    assert "saved" in box.result.text() and not submitted

    box.validate()                                             # save, then the job
    assert len(client.written) == 2
    assert submitted == [("validation", {"situation": "September 2025"})]
    # saved again under the same name: it is still one situation, and the other stays
    names = [s["name"] for s in client.written[-1]["calibration"]["validation"]]
    assert names == ["September 2025", "flood"]


def test_saving_before_the_model_is_built_keeps_what_the_form_cannot_show(
        validation_box):
    """The example case names a discharge per inflow. Before the build the form does
    not know the inflows, so it cannot show these discharges; it must not drop them."""
    box, use, _submitted, _case = validation_box
    info = _validation_info(built=False, calibrated=False, reports=False)
    info["situations"][0]["duration"] = 4000.0
    client = use(info)
    assert not box._inflows and box.total.isHidden()
    assert "inflow 1: 0.2 m³/s, inflow 3: 5.1 m³/s" in box.hint.text()
    box.save()
    first = client.written[0]["calibration"]["validation"][0]
    assert first["inflows"] == {"1": 0.2, "3": 5.1}
    assert first["duration"] == 4000.0                   # no field for it either
    assert "prescribed_flowrate" not in first
    # the layer is named as the case file names it, not rewritten in passing
    assert first["sources"] == [{"category": "hydraulics", "kind": "points",
                                 "positions": "data/september.gpkg"}]
    assert first["prescribed_elevation"] == 815.5         # not shown, not touched


def test_validation_is_a_step_of_a_batch_after_the_calibration():
    from axqua_plugin.core import batch

    assert batch.ordered(["validation", "calibration", "steady"]) == \
        ["steady", "calibration", "validation"]


def test_every_processing_algorithm_loads(qgis_app):
    """QGIS loads the provider at start; one algorithm that raised there opened an
    error dialog at every start of QGIS (and hung a headless one). The import of
    results read two settings that had moved into the case."""
    from axqua_plugin.processing.provider import AxquaProvider

    provider = AxquaProvider()
    provider.loadAlgorithms()
    for algorithm in provider.algorithms():
        algorithm.initAlgorithm({})
        assert algorithm.shortHelpString()
    names = {algorithm.name() for algorithm in provider.algorithms()}
    assert {"importresults"} <= names and len(names) == 3
