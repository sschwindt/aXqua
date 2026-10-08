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
    """A silent ``or 1.0`` capped a 6 m river's colour scale at 1 m."""
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
    assert titles == ["Configuration", "Case Setup", "Preprocessing",
                      "Hydraulic simulation", "Mesh convergence",
                      "Morphodynamic simulation", "Calibration & validation",
                      "Postprocessing", "Batch-processing"]
    inner = {key: [tabs.tabText(i) for i in range(tabs.count())]
             for key, tabs in dock.subtabs.items()}
    assert inner == {"hydraulics": ["Telemac", "OpenFOAM"],
                     "morphodynamics": ["Telemac", "OpenFOAM"],
                     "postprocessing": ["QGIS", "ParaView", "VisIt"]}
    # the job list is below the tabs and not one of them
    assert dock.jobs_tab.parent() is not None and dock.tabs.indexOf(dock.jobs_tab) == -1


def test_help_opens_the_section_of_the_tab_that_is_showing(dock, monkeypatch):
    from axqua_plugin.gui import help as help_pages

    opened = []
    monkeypatch.setattr(help_pages, "open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr(help_pages, "LOCAL", Path("/no/built/documentation"))
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
    assert [key[1] for key in dock.page("mesh")._boxes] == [
        "mesh_convergence", "vertical_convergence"]
    assert list(dock.page("calibration")._boxes) == [("telemac", "calibration")]
    assert list(dock.page("morphodynamics", "telemac")._boxes) == [
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


def test_a_batch_is_submitted_in_the_order_of_the_workflow(dock):
    from axqua_plugin.compat import CHECKED
    from axqua_plugin.gui.capability_tabs import CaseView

    dock._apply_case_view(CaseView.from_payload(EXAMPLE))
    page = dock.page("batch")
    assert page.ticked() == ["preprocessing", "steady"]      # what is ticked at first
    page.steps.item(5).setCheckState(CHECKED)                # calibration, the last
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
    def __init__(self, findings=()):
        self.written, self.findings = [], list(findings)

    def profile_write(self, data):
        self.written.append(data)
        return {"path": "/home/x/default.axq-profile", "findings": []}

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
