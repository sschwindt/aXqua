#!/usr/bin/env python3
"""Take the screenshots of the plugin that the documentation shows.

The figures ``docs/img/gui-*.png`` are not made by hand. This script starts QGIS
without a window, opens the aXqua panel with one case, brings each tab and each editor
to the front, and saves a picture of it in which the places a text refers to are framed
and numbered. A figure therefore shows the plugin as it is, and is made again with one
command when the plugin changes::

    python scripts/build_screenshots.py --case <folder>/<name>.axq-case

The case has to be built and to have a completed steady job, so that the tabs show
what a user sees while working: the job list with its jobs, the checkup table, the
results. The figures of the hydrology mode need a case in that mode and are taken on
their own: ``--only "hydrology editor,hydrology,hydrology wizard"``. A case with the blocks ``calibration``, ``openfoam`` and ``fine_sediment``
shows those boxes filled, and the figure of the fine sediment simulation needs that
block. The script fails, and says which figure, when a widget that a caption explains
is not on the screen: a figure whose numbers no longer match its caption is worse than
none. How many marks each figure has is recorded in ``gui-screenshots.json`` beside
the figures, which a test compares with the captions.

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


def draw_marks(pixmap, frames) -> None:
    """Frame each rectangle and number it, in the style every figure here uses.

    Module level rather than inside :func:`take_screenshots` because not every figure
    needs a case: the settings dialog is the plugin talking about itself, and
    ``scripts/build_settings_screenshot.py`` takes it without building anything. Two
    copies of this drawing would drift, and a reader would see it.
    """
    from qgis.PyQt.QtCore import QRect, Qt
    from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPen

    from axqua.compat import enum_value

    painter = QPainter(pixmap)
    painter.setRenderHint(enum_value(QPainter, "RenderHint.Antialiasing",
                                     "Antialiasing"))
    font = QFont(painter.font())
    font.setBold(True)
    font.setPixelSize(15)
    painter.setFont(font)
    for rect in frames:                               # all frames first ...
        painter.setPen(QPen(QColor(MARK), 3))
        painter.setBrush(QColor(0, 0, 0, 0))
        painter.drawRoundedRect(rect, 6, 6)
    for number, rect in enumerate(frames, start=1):   # ... so no number is covered
        badge = QRect(max(rect.left() - 12, 2), max(rect.top() - 12, 2), 24, 24)
        painter.setPen(QPen(QColor("white"), 1))
        painter.setBrush(QColor(MARK))
        painter.drawEllipse(badge)
        painter.drawText(badge, int(enum_value(Qt, "AlignmentFlag.AlignCenter",
                                               "AlignCenter")), str(number))
    painter.end()


def inside_qgis() -> bool:
    try:
        from qgis.utils import iface
    except ImportError:
        return False
    return iface is not None


# ============================================================ the part inside QGIS


def take_screenshots() -> None:
    # The painting imports moved with the painting: draw_marks owns them now.
    from qgis.PyQt.QtCore import QEventLoop, QPoint, QRect, QTimer
    from qgis.PyQt.QtWidgets import QGroupBox, QListWidget, QPushButton, QTableWidget
    from qgis.utils import iface, plugins

    out = Path(os.environ["AXQUA_SHOTS_OUT"])
    case = Path(os.environ["AXQUA_SHOTS_CASE"])
    #: the figures to take, by the names of the loop at the end; empty: all of a river
    only = {n.strip() for n in os.environ.get("AXQUA_SHOTS_ONLY", "").split(",")
            if n.strip()}
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"figures": {}, "failed": {}}

    def save_report() -> None:
        (out / ".gui-screenshots-run.json").write_text(json.dumps(report, indent=1),
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
        for child in root.findChildren(QLineEdit):       # the gray text of an empty row
            if said(child.placeholderText()) != child.placeholderText():
                child.setPlaceholderText(said(child.placeholderText()))
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
        for number, mark in enumerate(marks, start=1):
            for widget in (mark if isinstance(mark, (list, tuple)) else [mark]):
                if not isinstance(widget, QRect) and widget.visibleRegion().isEmpty():
                    # the caption would explain a number the picture does not have
                    raise LookupError(f"mark {number} of {name} is not on the screen")
        pixmap = root.grab()
        limits = pixmap.rect().adjusted(2, 2, -2, -2)
        draw_marks(pixmap, [box_of(root, target).adjusted(-4, -4, 4, 4)
                            .intersected(limits) for target in marks])
        pixmap.save(str(out / f"{name}.png"))
        report["figures"][name] = len(marks)
        save_report()

    def attempt(name: str, action) -> None:
        """One figure failing must not cost the others."""
        try:
            action()
        except Exception:                                # noqa: BLE001
            report["failed"][name] = traceback.format_exc(limit=3)
            save_report()

    def buttons(root, *texts):
        return [first(root, QPushButton, text) for text in texts]

    dock = plugins["axqua"].dock
    client = dock.ctx.client
    dock.setFloating(True)
    dock.resize(*PANEL)
    dock.show()
    tab = dock.configuration_tab
    until(lambda: "axqua " in tab.runner_label.text(), 90)
    until(lambda: tab.profile is not None, 60)
    until(lambda: bool(tab.overview.get("targets")), 90)
    dock.add_case(case)                    # as *Add case...* does: the list is filled
    dock.set_active_case(str(case))
    until(lambda: dock.ctx.case_view is not None, 90)
    until(lambda: "checking" not in dock.case_tab.capability_label.text(), 120)
    dock.jobs_tab.refresh()
    until(lambda: dock.jobs_tab.table.rowCount() > 0, 10 if only else 30)
    wait(1500)

    # ---- Configuration -----------------------------------------------------------
    def configuration() -> None:
        from qgis.PyQt.QtWidgets import QScrollArea, QSplitter

        dock.show_section("configuration")
        # The tab scrolls in a panel that is lower than its three boxes. The picture is
        # taken of a panel high enough to show them, with the added height given to
        # the tab and not to the job list below it.
        splitter = dock.findChild(QSplitter)
        before = splitter.sizes()
        need = tab.findChild(QScrollArea).widget().sizeHint().height() + 70
        dock.resize(PANEL[0], need + before[-1] + 40)
        wait(300)
        splitter.setSizes([need, before[-1]])
        wait(400)
        try:
            shot(dock, "gui-configuration", [
                first(tab, QGroupBox, "aXqua program"),
                first(tab, QGroupBox, "Profile of this computer"),
                first(tab, QGroupBox, "Simulation"),
                dock.help_button])
        finally:
            dock.resize(*PANEL)
            wait(200)
            splitter.setSizes(before)

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
        window.resize(792, 780)                # every hint whole, with "Install for"
        window.show()
        fields = window._widgets
        shot(window, "gui-install-wizard", [
            fields["scope"].parentWidget(),
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
            page.capability_label,
            buttons(page, "Open case settings", "Check")])

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
        page = editor.stack.currentWidget()
        browse = first(page, QPushButton, "...")
        adder = next(box for box in page.findChildren(QComboBox)
                     if box.isVisible() and box.itemText(0).startswith("Add setting"))
        shot(editor, "gui-case-editor", [
            editor.section_list,
            browse.parentWidget(),
            adder,
            [editor.save_button, editor.cancel_button, editor.exit_button]])
        try:
            # both are frames of the page Morphodynamic simulation
            from qgis.PyQt.QtWidgets import QScrollArea

            for frame, name in (("Sediment transport", "gui-case-editor-morphodynamics"),
                                ("Fine sediment", "gui-case-editor-fine-sediment")):
                select("morphodynamic")
                scroll = editor.stack.currentWidget()
                box = first(scroll, QGroupBox, frame)
                if isinstance(scroll, QScrollArea):
                    scroll.ensureWidgetVisible(box, 0, 0)
                    wait(200)
                shot(editor, name, [
                    editor.section_list.visualItemRect(editor.section_list.currentItem())
                    .translated(editor.section_list.viewport().mapTo(editor,
                                                                    QPoint(0, 0))),
                    box])
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

    def fine_sediment() -> None:
        dock.show_section("hydraulics", "morphodynamics")
        page = dock.page("hydraulics", "morphodynamics")
        page.choose("openfoam")
        wait(300)
        inner, box = capability_box(page, "openfoam", "morphodynamics")
        shot(dock, "gui-morphodynamics-openfoam", [
            page.solver_choice, inner.options_box, inner.build_button,
            inner.submit_button, inner.results_button])

    def mesh_convergence() -> None:
        dock.show_section("hydraulics", "mesh")
        page = dock.page("hydraulics", "mesh")
        page.choose("telemac")
        wait(300)
        inner, box = capability_box(page, "telemac", "mesh_convergence")
        shot(dock, "gui-mesh-convergence", [page.solver_choice, inner.options_box,
                                            inner.submit_button, inner.results_button])

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

    # ---- the hydrology mode (a case in hydrology mode: --only hydrology...) -------
    def hydrology_editor() -> None:
        from axqua.gui.case_editor import CaseEditor
        read = client.case_read(case)
        editor = CaseEditor(client, case, read.get("data") or {}, client.schema())
        editor.resize(1180, 760)                   # the label of the switch in one piece
        editor.show()
        wait(300)
        names = [editor.section_list.item(i).text()
                 for i in range(editor.section_list.count())]
        editor.section_list.setCurrentRow(names.index("Mesh configuration"))
        wait(300)
        try:
            # the texts first: a long path in the header wraps, and its neutral
            # replacement does not, which moves the list after it was measured
            neutralize(editor)
            wait(300)
            switch = editor._switch_boxes["hydrology"][0]     # the one on the mesh page
            page = editor.section_list.visualItemRect(
                editor.section_list.item(names.index("Hydrology"))).translated(
                editor.section_list.viewport().mapTo(editor, QPoint(0, 0)))
            shot(editor, "gui-case-editor-hydrology", [
                switch, first(editor.stack.currentWidget(), QGroupBox, "TELEMAC mesh"),
                page])
        finally:
            editor.reject()

    def hydrology() -> None:
        dock.show_section("hydraulics", "hydrology")
        page = dock.page("hydraulics", "hydrology")
        until(lambda: page.data.summary.text().startswith("Catchment"), 60)
        until(lambda: page.data.check_button.isEnabled(), 120)
        wait(500)
        inner, box = capability_box(page, "telemac", "hydrology")
        shot(dock, "gui-hydrology", [
            sub_tab_bar("hydraulics"), [inner.build_button, inner.submit_button],
            page.data.summary, page.data.finding_list,
            [page.data.wizard_button, page.data.check_button]])

    def hydrology_wizard() -> None:
        from axqua.gui.hydrology_wizard import HydrologyWizard
        page = dock.page("hydraulics", "hydrology")
        window = HydrologyWizard(dock.ctx, case, page.data.info)
        window.resize(860, 1500)
        window.show()
        until(lambda: "<a href" in window._portal_labels[0][0].text(), 60)
        wait(500)
        rain = window.tabs.widget(0).widget()
        shot(window, "gui-hydrology-wizard", [
            window.tabs.tabBar(), window._portal_labels[0][0],
            first(rain, QGroupBox, "A rain record"),
            first(rain, QGroupBox, "A design storm"),
            first(rain, QGroupBox, "A past event"), window.next_button])
        window.close()

    def batch() -> None:
        dock.show_section("batch")
        page = dock.page("batch")
        shot(dock, "gui-batch", [first(page, QListWidget), page.submit_button,
                                 page.script_button])

    # ---- the panel in QGIS, with a result on the map -----------------------------
    def window() -> None:
        from qgis.core import QgsMeshLayer, QgsProject

        def loaded() -> list:
            return [layer for layer in QgsProject.instance().mapLayers().values()
                    if isinstance(layer, QgsMeshLayer)]

        dock.load_latest_results("steady")        # what *Load results* does
        if not until(lambda: len(loaded()) >= 2, 120):
            raise LookupError("the steady result of the case did not load")
        layers = loaded()
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
        save_report()

    for name, action in (("configuration", configuration),
                         ("profile editor", profile_editor), ("wizard", wizard),
                         ("case setup", case_setup), ("case editor", case_editor),
                         ("preprocessing", preprocessing), ("hydraulics", hydraulics),
                         ("mesh convergence", mesh_convergence),
                         ("fine sediment", fine_sediment),
                         ("calibration", calibration), ("postprocessing", postprocessing),
                         ("batch", batch), ("window", window),
                         ("hydrology editor", hydrology_editor),
                         ("hydrology", hydrology),
                         ("hydrology wizard", hydrology_wizard)):
        if only and name not in only:
            continue
        if not only and name.startswith("hydrology"):
            continue                      # they need a case in hydrology mode
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
    parser.add_argument("--only", default="",
                        help="the figures to take, comma-separated names of the loop of "
                             "take_screenshots; 'hydrology editor,hydrology,hydrology "
                             "wizard' needs a case in hydrology mode")
    args = parser.parse_args(argv)
    if not args.case.is_file():
        print(f"no such case file: {args.case}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="axqua-shots-") as scratch:
        env = dict(os.environ, QGIS_CUSTOM_CONFIG_PATH=scratch, QGIS_PROFILE="shots",
                   QT_QPA_PLATFORM="offscreen", AXQUA_SHOTS_CASE=str(args.case.resolve()),
                   AXQUA_SHOTS_OUT=str(args.out.resolve()), AXQUA_SHOTS_ONLY=args.only)
        report = args.out / ".gui-screenshots-run.json"
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
    report.unlink()
    for name, why in done.get("failed", {}).items():
        print(f"FAILED {name}: {why.strip().splitlines()[-1]}", file=sys.stderr)
    reduce_colors(args.out, sorted(done.get("figures", {})))
    # the marks of each figure, for the test that compares them with the captions
    marks = args.out / "gui-screenshots.json"
    known = json.loads(marks.read_text(encoding="utf-8")) if marks.is_file() else {}
    known.update(done.get("figures", {}))
    marks.write_text(json.dumps(dict(sorted(known.items())), indent=1) + "\n",
                     encoding="utf-8")
    print(f"{len(done.get('figures', {}))} figures in {args.out}")
    return 0 if done.get("done") and not done.get("failed") else 1


if inside_qgis():
    try:
        take_screenshots()
    except Exception:                                    # noqa: BLE001 - never hang QGIS
        Path(os.environ.get("AXQUA_SHOTS_OUT", "."),
             ".gui-screenshots-run.json").write_text(
            json.dumps({"figures": {}, "failed": {"start": traceback.format_exc()}}),
            encoding="utf-8")
        os._exit(1)
elif __name__ == "__main__":
    raise SystemExit(main())
