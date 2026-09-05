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
    """``help.rst`` tells users to copy the aXqua tab into a bug report, so something
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
