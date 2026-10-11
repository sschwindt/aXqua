#!/usr/bin/env python3
"""Take the screenshots of the plugin that the documentation shows.

The figures ``docs/img/gui-*.png`` are not made by hand. This script starts QGIS
without a window, opens the aXqua panel with one case, brings each tab and each editor
to the front, and saves a picture of it in which the places a text refers to are framed
and numbered. A figure therefore shows the plugin as it is, and is made again with one
command when the plugin changes::

    python scripts/build_screenshots.py --case <folder>/<name>.axq-case

The case has to be built and to have a steady result, so that the tabs show what a
user sees while working: the job list with its jobs, the checkup table, the results.
A case with the blocks ``calibration`` and ``openfoam`` shows those boxes filled.

QGIS is started through ``scripts/qgis_dev.sh`` (the QGIS of the conda environment
``qgis-dev``) with a QGIS profile of its own in a temporary folder, so no profile of
the user is touched. The same file is what runs inside QGIS: started there, it takes
the screenshots; started with Python, it starts QGIS and then reduces the pictures to
a palette of colors, which keeps the repository small.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

#: The size of the panel in the figures, in pixels.
PANEL = (800, 980)
MARK = "#d7301f"


def inside_qgis() -> bool:
    try:
        from qgis.utils import iface
    except ImportError:
        return False
    return iface is not None


# ============================================================ the part inside QGIS


def take_screenshots() -> None:
    from qgis.PyQt.QtCore import QEventLoop, QPoint, QRect, QTimer
    from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPen
    from qgis.PyQt.QtWidgets import QGroupBox, QListWidget, QPushButton, QTableWidget
    from qgis.utils import iface, plugins

    from axqua.compat import enum_value                  # the plugin, as QGIS loaded it
    from qgis.PyQt.QtCore import Qt

    out = Path(os.environ["AXQUA_SHOTS_OUT"])
    case = Path(os.environ["AXQUA_SHOTS_CASE"])
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"figures": {}, "notes": []}

    def save_report() -> None:
        (out / "gui-screenshots.json").write_text(json.dumps(report, indent=1),
                                                  encoding="utf-8")

    def wait(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def until(condition, seconds: float = 60.0) -> bool:
        for _ in range(int(seconds * 5)):
            try:
                if condition():
                    return True
            except Exception:                            # noqa: BLE001 - not there yet
                pass
            wait(200)
        return False

    def first(root, kind, text: str = ""):
        """The first visible child of *kind* whose text or title starts with *text*."""
        for child in root.findChildren(kind):
            label = child.title() if isinstance(child, QGroupBox) else (
                child.text() if hasattr(child, "text") else "")
            if child.isVisible() and str(label).replace("&", "").startswith(text):
                return child
        raise LookupError(f"no visible {kind.__name__} '{text}'")

    def box_of(root, target) -> QRect:
        if isinstance(target, QRect):
            return target
        if isinstance(target, (list, tuple)):            # several widgets, one frame
            rect = box_of(root, target[0])
            for other in target[1:]:
                rect = rect.united(box_of(root, other))
            return rect
        return QRect(target.mapTo(root, QPoint(0, 0)), target.size())

    #: What a figure shows in place of the folders of the computer it was made on.
    neutral = sorted({
        os.environ.get("AXQUA_HOME", ""): "/home/user/.config/axqua",
        os.environ.get("AXQUA_JOB_ROOT", ""): "/home/user/.local/share/axqua/jobs",
        str(case.parent.parent): "/home/user/cases",
        str(Path.home()): "/home/user",
        "/home/modelling": "/opt",
    }.items(), key=lambda pair: -len(pair[0]))

    def said(text: str) -> str:
        for private, public in neutral:
            if private:
                text = text.replace(private, public)
        return text

    def neutralize(root) -> None:
        """Replace the folders of this computer in every text of *root*."""
        from qgis.PyQt.QtWidgets import (QLabel, QLineEdit, QPlainTextEdit,
                                         QTableWidget as Table)
        for child in root.findChildren(QLabel) + root.findChildren(QLineEdit):
            if said(child.text()) != child.text():
                child.setText(said(child.text()))
                if isinstance(child, QLineEdit):
                    child.setCursorPosition(0)
        for child in root.findChildren(QPlainTextEdit):
            if said(child.toPlainText()) != child.toPlainText():
                child.setPlainText(said(child.toPlainText()))
        for child in root.findChildren(QListWidget):
            for row in range(child.count()):
                child.item(row).setText(said(child.item(row).text()))
        for child in root.findChildren(Table):
            for row in range(child.rowCount()):
                for column in range(child.columnCount()):
                    item = child.item(row, column)
                    if item is not None and said(item.text()) != item.text():
                        item.setText(said(item.text()))

    def shot(root, name: str, marks: list) -> None:
        """Save a picture of *root* with the *marks* framed and numbered."""
        neutralize(root)
        wait(500)
        marks = [mark for mark in marks if isinstance(mark, (QRect, list, tuple))
                 or not mark.visibleRegion().isEmpty()]
        pixmap = root.grab()
        painter = QPainter(pixmap)
        painter.setRenderHint(enum_value(QPainter, "RenderHint.Antialiasing",
                                         "Antialiasing"))
        font = QFont(painter.font())
        font.setBold(True)
        font.setPixelSize(15)
        painter.setFont(font)
        limits = pixmap.rect().adjusted(2, 2, -2, -2)
        for number, target in enumerate(marks, start=1):
            rect = box_of(root, target).adjusted(-4, -4, 4, 4).intersected(limits)
            painter.setPen(QPen(QColor(MARK), 3))
            painter.setBrush(QColor(0, 0, 0, 0))
            painter.drawRoundedRect(rect, 6, 6)
            badge = QRect(max(rect.left() - 12, 2), max(rect.top() - 12, 2), 24, 24)
            painter.setPen(QPen(QColor("white"), 1))
            painter.setBrush(QColor(MARK))
            painter.drawEllipse(badge)
            painter.drawText(badge, int(enum_value(Qt, "AlignmentFlag.AlignCenter",
                                                   "AlignCenter")), str(number))
        painter.end()
        pixmap.save(str(out / f"{name}.png"))
        report["figures"][name] = len(marks)
        save_report()

    def attempt(name: str, action) -> None:
        """One figure failing must not cost the others."""
        try:
            action()
        except Exception:                                # noqa: BLE001
            report["notes"].append(f"{name}: {traceback.format_exc(limit=3)}")
            save_report()

    def buttons(root, *texts):
        return [first(root, QPushButton, text) for text in texts]

    dock = plugins["axqua"].dock
    client = dock.ctx.client
    dock.setFloating(True)
    dock.resize(*PANEL)
    dock.show()
    tab = dock.configuration_tab
    until(lambda: "axqua 0." in tab.runner_label.text(), 90)
    until(lambda: tab.profile is not None, 60)
    until(lambda: bool(tab.overview.get("targets")), 90)
    dock.ctx.project.add_case(case)
    dock.set_active_case(str(case))
    until(lambda: dock.ctx.case_view is not None, 90)
    dock.jobs_tab.refresh()
    until(lambda: dock.jobs_tab.table.rowCount() > 0, 30)
    wait(1500)

    # ---- Configuration -----------------------------------------------------------
    def configuration() -> None:
        dock.show_section("configuration")
        shot(dock, "gui-configuration", [
            first(tab, QGroupBox, "aXqua program"),
            first(tab, QGroupBox, "Profile of this computer"),
            first(tab, QGroupBox, "Simulation software"),
            dock.help_button])

    def profile_editor() -> None:
        from axqua.gui.profile_editor import ProfileEditor
        editor = ProfileEditor(client, tab.profile)
        editor.resize(760, 760)
        editor.show()
        rows = editor._widgets
        shot(editor, "gui-profile-editor", [
            rows["installations.telemac"].parentWidget(),
            rows["solvers.telemac.setup_script"].parentWidget(),
            rows["solvers.telemac.mpi_processes"].parentWidget(),
            [editor.save_button, editor.cancel_button, editor.exit_button]])
        editor.reject()

    def wizard() -> None:
        from axqua.gui.install_wizard import InstallWizard
        window = InstallWizard(client, "openfoam", tab.overview)
        window.show()
        fields = window._widgets
        shot(window, "gui-install-wizard", [
            fields["folder"].parentWidget(),
            fields["reuse_openfoam"].parentWidget(),
            fields["viewers"].parentWidget(),
            window.next_button])
        window.close()

    # ---- Case Setup --------------------------------------------------------------
    def case_setup() -> None:
        dock.show_section("case")
        page = dock.case_tab
        shot(dock, "gui-case-setup", [
            buttons(page, "Add case", "Example case"),
            page.case_list,
            buttons(page, "Edit case", "Check"),
            page.capability_label])

    def case_editor() -> None:
        from axqua.gui.case_editor import CaseEditor
        read = client.case_read(case)
        editor = CaseEditor(client, case, read.get("data") or {}, client.schema())
        editor.resize(900, 760)
        editor.show()
        wait(300)

        def select(block: str) -> None:
            names = [editor.section_list.item(i).text().lower()
                     for i in range(editor.section_list.count())]
            row = next(i for i, text in enumerate(names) if block in text)
            editor.section_list.setCurrentRow(row)
            wait(300)

        from qgis.PyQt.QtWidgets import QComboBox

        select("geodata")
        page = editor.pages.currentWidget()
        browse = first(page, QPushButton, "...")
        adder = next(box for box in page.findChildren(QComboBox)
                     if box.isVisible() and box.itemText(0).startswith("Add setting"))
        shot(editor, "gui-case-editor", [
            editor.section_list,
            browse.parentWidget(),
            adder,
            [editor.save_button, editor.cancel_button, editor.exit_button]])
        try:
            select("morphodynamic")
            shot(editor, "gui-case-editor-morphodynamics", [
                editor.section_list.visualItemRect(editor.section_list.currentItem())
                .translated(editor.section_list.viewport().mapTo(editor, QPoint(0, 0))),
                editor.pages.currentWidget()])
        finally:
            editor.reject()

    # ---- the workflow tabs -------------------------------------------------------
    def preprocessing() -> None:
        dock.show_section("preprocessing")
        page = dock.page("preprocessing")
        shot(dock, "gui-preprocessing", [
            page.build_button, first(page, QTableWidget), dock.jobs_tab.table])

    def capability_box(page, solver: str, capability: str):
        inner = page.tab(solver, capability)
        parent = inner.parentWidget()
        return inner, (parent if isinstance(parent, QGroupBox) else inner)

    def sub_tab_bar(key: str):
        return dock.subtabs[key].tabBar()

    def hydraulics() -> None:
        dock.show_section("hydraulics", "telemac")
        page = dock.page("hydraulics", "telemac")
        inner, box = capability_box(page, "telemac", "steady2d")
        shot(dock, "gui-hydraulics-telemac", [
            sub_tab_bar("hydraulics"), box, inner.submit_button, inner.results_button,
            dock.jobs_tab])
        dock.show_section("hydraulics", "openfoam")
        page = dock.page("hydraulics", "openfoam")
        inner, box = capability_box(page, "openfoam", "free_surface_3d")
        warm_up = inner._build_fields["pre_run"]
        shot(dock, "gui-hydraulics-openfoam", [
            [inner.options_form.labelForField(warm_up), warm_up],
            inner.build_button, inner.submit_button])

    def mesh_convergence() -> None:
        dock.show_section("mesh")
        inner, box = capability_box(dock.page("mesh"), "telemac", "mesh_convergence")
        shot(dock, "gui-mesh-convergence", [inner.options_box, inner.submit_button,
                                            inner.results_button])

    def calibration() -> None:
        dock.show_section("calibration")
        page = dock.page("calibration")
        box = page.validation
        until(lambda: bool(box.info), 60)
        wait(500)
        inner, _box = capability_box(page, "telemac", "calibration")
        dock.resize(PANEL[0], 1330)
        wait(400)
        try:
            shot(dock, "gui-calibration", [
                inner.options_box, inner.submit_button,
                box.layer.parentWidget() or box.layer,
                [box.save_button, box.run_button]])
        finally:
            dock.resize(*PANEL)

    def postprocessing() -> None:
        dock.show_section("postprocessing", "qgis")
        page = dock.page("postprocessing", "qgis")
        shot(dock, "gui-postprocessing-qgis", [
            first(page, QGroupBox, "Results on the map"),
            first(page, QGroupBox, "Predefined map styles"),
            first(page, QGroupBox, "Map for a report and movie")])
        dock.show_section("postprocessing", "paraview")
        page = dock.page("postprocessing", "paraview")
        until(lambda: page.table.rowCount() > 0, 60)
        page.table.selectRow(0)
        shot(dock, "gui-postprocessing-paraview", [
            sub_tab_bar("postprocessing"), page.table, page.export_button,
            page.open_button])

    def batch() -> None:
        dock.show_section("batch")
        page = dock.page("batch")
        shot(dock, "gui-batch", [first(page, QListWidget), page.submit_button,
                                 page.script_button])

    # ---- the panel in QGIS, with a result on the map -----------------------------
    def window() -> None:
        from axqua.core import result_loader

        rows = dock.jobs_tab.table
        root = None
        for folder in sorted(Path(os.environ["AXQUA_JOB_ROOT"]).iterdir()):
            if "steady" in folder.name:
                root = folder
        results = result_loader.discover(root)
        layers = result_loader.ResultLoader(min_depth=0.01, velocity_cap=5.0).load(results)
        depth = next(layer for layer in layers if "velocity" not in layer.name().lower()
                     and "geometry" not in layer.name().lower())
        for layer in layers:                              # the depth alone, with lines
            node = iface.layerTreeView().layerTreeModel().rootGroup().findLayer(layer.id())
            if node is not None:
                node.setItemVisibilityChecked(layer is depth)
        iface.setActiveLayer(depth)
        dock.show_section("postprocessing", "qgis")
        page = dock.page("postprocessing", "qgis")
        page.apply_style()
        # what "Load results" does in the plugin: the end of the run, not its start
        result_loader.show_last_time_step(iface.mapCanvas(), layers)
        iface.messageBar().clearWidgets()
        main = iface.mainWindow()
        dock.setFloating(False)
        main.resize(1500, 900)
        dock.setMinimumWidth(640)
        wait(800)
        canvas = iface.mapCanvas()
        extent = depth.extent()
        extent.scale(0.42)
        canvas.setExtent(extent)
        canvas.refreshAllLayers()
        wait(9000)
        shot(main, "gui-qgis-window", [dock, canvas, page.style_button])
        canvas.grab().save(str(out / "gui-map-streamlines.png"))
        report["figures"]["gui-map-streamlines"] = 0
        report["notes"].append(f"job rows: {rows.rowCount()}")
        save_report()

    for name, action in (("configuration", configuration),
                         ("profile editor", profile_editor), ("wizard", wizard),
                         ("case setup", case_setup), ("case editor", case_editor),
                         ("preprocessing", preprocessing), ("hydraulics", hydraulics),
                         ("mesh convergence", mesh_convergence),
                         ("calibration", calibration), ("postprocessing", postprocessing),
                         ("batch", batch), ("window", window)):
        attempt(name, action)
    report["done"] = True
    save_report()
    QTimer.singleShot(200, lambda: os._exit(0))


# ============================================================ the part that starts QGIS


def reduce_colors(folder: Path, names: list[str]) -> None:
    """Store the figures with a palette of colors: a window has few of them."""
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is not installed; the figures are kept as QGIS wrote them.")
        return
    for name in names:
        path = folder / f"{name}.png"
        if not path.is_file():
            continue
        before = path.stat().st_size
        image = Image.open(path).convert("RGB")
        colors = 255 if name.startswith("gui-map") or name == "gui-qgis-window" else 128
        image.quantize(colors=colors, dither=Image.Dither.NONE).save(path, optimize=True)
        print(f"  {name}.png  {before // 1024} -> {path.stat().st_size // 1024} KiB")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import subprocess
    import tempfile

    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--case", type=Path, required=True,
                        help="a case file whose model is built and has a steady result")
    parser.add_argument("--out", type=Path, default=repo / "docs" / "img")
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    if not args.case.is_file():
        print(f"no such case file: {args.case}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="axqua-shots-") as scratch:
        env = dict(os.environ, QGIS_CUSTOM_CONFIG_PATH=scratch, QGIS_PROFILE="shots",
                   QT_QPA_PLATFORM="offscreen", AXQUA_SHOTS_CASE=str(args.case.resolve()),
                   AXQUA_SHOTS_OUT=str(args.out.resolve()))
        report = args.out / "gui-screenshots.json"
        report.unlink(missing_ok=True)
        try:
            subprocess.run([str(repo / "scripts" / "qgis_dev.sh"), "--nologo",
                            "--noversioncheck", "--code", str(Path(__file__).resolve())],
                           env=env, timeout=args.timeout, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            print("QGIS did not finish in time.", file=sys.stderr)
    if not report.is_file():
        print("QGIS wrote no report; the plugin may not have loaded.", file=sys.stderr)
        return 1
    done = json.loads(report.read_text(encoding="utf-8"))
    report.unlink()                                     # a record of the run, not a figure
    for note in done.get("notes", []):
        print("note:", note.strip().splitlines()[-1] if "\n" in note else note)
    reduce_colors(args.out, sorted(done.get("figures", {})))
    print(f"{len(done.get('figures', {}))} figures in {args.out}")
    return 0 if done.get("done") else 1


if inside_qgis():
    try:
        take_screenshots()
    except Exception:                                    # noqa: BLE001 - never hang QGIS
        Path(os.environ.get("AXQUA_SHOTS_OUT", "."), "gui-screenshots.json").write_text(
            json.dumps({"figures": {}, "notes": [traceback.format_exc()]}),
            encoding="utf-8")
        os._exit(1)
elif __name__ == "__main__":
    raise SystemExit(main())
