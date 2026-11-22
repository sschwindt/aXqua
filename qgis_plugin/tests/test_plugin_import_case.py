"""Importing a TELEMAC case and putting its results on the map without a job (the user's
order of 2026-11-20), under QGIS with fake clients."""

from __future__ import annotations


import pytest

pytest.importorskip("qgis.core", reason="PyQGIS is not importable in this interpreter")


def _now(module, monkeypatch):
    monkeypatch.setattr(module, "run_async",
                        lambda title, call, on_success=None, on_error=None, owner=None:
                        on_success(call()))


class _ImportClient:
    def __init__(self):
        self.calls = []

    def case_import(self, case, steering, *, name="", crs=25832):
        self.calls.append((str(case), str(steering), name, crs))
        return {"case": str(case), "solver": "telemac2d", "steering": str(steering),
                "results": [{"name": "r2d.slf", "path": "/m/r2d.slf", "exists": False}],
                "missing": ["/m/r2d.slf"]}


def test_the_dialog_proposes_the_case_file_and_imports(qgis_app, tmp_path, monkeypatch):
    from axqua_plugin.gui import import_dialog

    _now(import_dialog, monkeypatch)
    added = []
    client = _ImportClient()
    dialog = import_dialog.ImportDialog(client, on_done=added.append)
    assert "does not build or run" in import_dialog.INTRO
    dialog.run_import()
    assert "Select the steering file" in dialog.status.text() and not client.calls
    dialog.steering.setText(str(tmp_path / "run" / "t2d.cas"))
    # the case file goes beside the steering file, named after it, unless typed
    assert dialog.case_file.text() == str(tmp_path / "run" / "t2d.axq-case")
    assert dialog.name.text() == "t2d"
    dialog.crs.setText("31468")
    dialog.run_import()
    assert client.calls == [(str(tmp_path / "run" / "t2d.axq-case"),
                             str(tmp_path / "run" / "t2d.cas"), "t2d", 31468)]
    assert added == [tmp_path / "run" / "t2d.axq-case"]
    assert "Not there yet: r2d.slf" in dialog.status.text()
    dialog.crs.setText("UTM")
    dialog.run_import()
    assert "EPSG code" in dialog.status.text() and len(client.calls) == 1
    dialog.deleteLater()


class _Project:
    def __init__(self, case):
        self.case = case

    def active_case_path(self):
        return self.case


class _Ctx:
    min_depth, velocity_cap, iface = 0.005, 5.0, None

    def __init__(self, case, client):
        self.project, self.client = _Project(case), client
        self.messages = []

    def warn(self, text):
        self.messages.append(("warn", text))

    def info(self, text):
        self.messages.append(("info", text))

    def run_action(self, name):
        pass

    def open_settings(self):
        pass


LISTING = {"folder": "/m/vtk", "crs_epsg": 31468, "imported": True, "openfoam": [],
           "results": [
               {"name": "out/r2d.slf", "label": "steady simulation, 2D", "frames": 2,
                "path": "/m/out/r2d.slf", "layers": [
                    {"name": "TELEMAC result (steady)", "path": "/m/out/r2d.slf",
                     "kind": "mesh", "style": "water-depth", "variable": "WATER DEPTH",
                     "extra": {"map_style": "arrows-depth"}},
                    {"name": "TELEMAC result (steady) - velocity", "path": "/m/out/r2d.slf",
                     "kind": "mesh", "style": "velocity", "variable": "SCALAR VELOCITY",
                     "extra": {}}]},
               {"name": "r3d.slf", "label": "3D simulation", "frames": 2,
                "path": "/m/r3d.slf", "layers": []}]}


class _ListClient:
    def __init__(self, payload):
        self.payload = payload
        self.asked = 0

    def export_list(self, case):
        self.asked += 1
        return self.payload


def test_the_results_of_a_case_go_on_the_map_without_a_job(qgis_app, tmp_path, monkeypatch):
    from axqua_plugin.core import result_loader
    from axqua_plugin.gui import section_pages

    _now(section_pages, monkeypatch)
    case = tmp_path / "reach.axq-case"
    case.write_text("project: {name: reach}\n")
    client = _ListClient(LISTING)
    ctx = _Ctx(case, client)
    page = section_pages.QgisPage(ctx)
    page.apply(None)
    assert client.asked == 0                       # not shown: asked when it is
    page.refresh()
    # a 3D volume is not a map layer: only the 2D result is offered
    assert page.result_list.count() == 1 and "out/r2d.slf" in page.result_list.item(0).text()
    items = page.case_items(page.case_results[0])
    assert [(i.style, i.variable, i.map_style) for i in items] == [
        ("water-depth", "WATER DEPTH", "arrows-depth"), ("velocity", "SCALAR VELOCITY", "")]
    loaded = []

    class _Loader:
        warnings, at_last_step = [], True

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def load(self, results):
            loaded.append(results)
            return list(results.items)

    monkeypatch.setattr(result_loader, "ResultLoader", _Loader)
    page.load_case_result()
    (results,) = loaded
    assert results.job_id == "reach" and results.crs_epsg == 31468
    assert [i.name for i in results.items] == ["TELEMAC result (steady)",
                                               "TELEMAC result (steady) - velocity"]
    assert ctx.messages[-1][0] == "info" and "axqua/reach" in ctx.messages[-1][1]
    # an aXqua from before the layers lists results without them
    old = {**LISTING, "results": [{k: v for k, v in LISTING["results"][0].items()
                                   if k != "layers"}]}
    page.show_case_results(old)
    assert page.result_list.count() == 0 and "1.5.0" in page.case_status.text()
    assert not page.load_case_button.isEnabled()
    page.deleteLater()


def test_an_imported_case_needs_no_terrain_model_in_the_case_settings(qgis_app):
    from axqua_plugin.gui.case_editor import condition_met

    imported = {"key": "project.imported_from", "set": True}
    values = {"project.imported_from": "run/t2d.cas"}
    assert condition_met(imported, values.get, lambda block: False)
    assert not condition_met(imported, {}.get, lambda block: False)
    assert not condition_met(imported, {"project.imported_from": ""}.get,
                             lambda block: False)
