#!/usr/bin/env python3
"""Take the figure of the settings dialog that the installation page shows.

``docs/img/gui-settings.png`` shows *aXqua > Settings* with the path of the aXqua
program filled in, because that is the one place a user has to type a path and the
installation page cannot say "enter it here" without showing where here is.

Separate from ``build_screenshots.py``, which takes the eighteen figures of the panel,
because those need a built case with a finished job and this one needs nothing: the
settings dialog is the plugin talking about itself. Requiring a case to photograph it
would make the figure expensive to remake, and a figure that is expensive to remake
goes stale. The marking is shared, not copied - :func:`build_screenshots.draw_marks`
draws it - so the two look alike::

    python scripts/build_settings_screenshot.py

QGIS is used headless (``QT_QPA_PLATFORM=offscreen``) through the Python that has the
QGIS bindings, which on Debian is the system Python rather than the conda environment.
The script re-executes itself there, so it is started the ordinary way.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "img" / "gui-settings.png"

#: What the figure shows as the path. Not this machine's: a figure is read by people
#: who have none of these folders, and `/home/user` is what the other figures use.
SHOWN_PATH = "/home/user/miniforge3/envs/axqua-env/bin/axqua"
#: What *Test* answers with. Taken from the installed aXqua so the figure cannot claim
#: a version that never existed; the path above is what it is attributed to.
SHOWN_RESULT = "axqua {version}"

#: Where the QGIS bindings live when they are not importable from this interpreter.
SYSTEM_SITE = "/usr/lib/python3/dist-packages"


def _render() -> int:
    from qgis.core import QgsApplication

    QgsApplication.setPrefixPath("/usr", True)
    app = QgsApplication([], True)
    app.initQgis()
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        from build_screenshots import draw_marks, reduce_colors

        from axqua.gui.settings_dialog import SettingsDialog
        from qgis.PyQt.QtCore import QPoint, QRect

        dialog = SettingsDialog()
        # wide enough that the whole path is readable: the figure exists to show what
        # a correct entry looks like, and a field scrolled to its end shows the tail
        dialog.resize(820, 130)
        dialog.executable.setText(SHOWN_PATH)
        dialog.executable.setCursorPosition(0)
        dialog.result.setText(SHOWN_RESULT.format(version=_axqua_version()))
        dialog.show()
        app.processEvents()
        dialog.executable.setCursorPosition(0)           # again, after the layout

        def box(widget) -> QRect:
            return QRect(widget.mapTo(dialog, QPoint(0, 0)), widget.size())

        # two marks: the dialog holds one setting now. What used to be its other two
        # rows - the drawing thresholds - belong to the case, and the dialog says so.
        marks = [box(dialog.executable), box(dialog.result)]
        limits = dialog.rect().adjusted(2, 2, -2, -2)
        pixmap = dialog.grab()
        draw_marks(pixmap, [r.adjusted(-4, -4, 4, 4).intersected(limits)
                            for r in marks])
        OUT.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(OUT))
        reduce_colors(OUT.parent, [OUT.stem])

        # the count of marks goes in the same file build_screenshots.py keeps, and is
        # merged into it exactly as that script does, so whichever runs last the file
        # holds both. tests/test_docs_outline.py compares it with the captions.
        import json

        marks_file = OUT.parent / "gui-screenshots.json"
        known = json.loads(marks_file.read_text(encoding="utf-8")) \
            if marks_file.is_file() else {}
        known[OUT.stem] = len(marks)
        marks_file.write_text(json.dumps(dict(sorted(known.items())), indent=1) + "\n",
                              encoding="utf-8")
        print(f"wrote {OUT.relative_to(REPO)} with {len(marks)} marks")
        return 0
    finally:
        app.exitQgis()


def _axqua_version() -> str:
    """The version of the installed aXqua.

    Passed in by the parent process: the renderer runs in the Python that has the QGIS
    bindings, which is not the environment aXqua is installed in, so asking there
    returns nothing and the figure would claim a version that does not exist.
    """
    passed = os.environ.get("AXQUA_SHOT_VERSION", "")
    if passed:
        return passed
    try:
        from importlib.metadata import version

        return version("axqua")
    except Exception:                                   # noqa: BLE001 - not installed
        return "unknown"


def main() -> int:
    try:
        import qgis  # noqa: F401
    except ImportError:
        if os.environ.get("AXQUA_SHOT_REEXEC"):
            print("the QGIS Python bindings are not importable; install QGIS >= 3.44",
                  file=sys.stderr)
            return 1
        env = dict(os.environ, AXQUA_SHOT_REEXEC="1", QT_QPA_PLATFORM="offscreen",
                   AXQUA_SHOT_VERSION=_axqua_version())
        env["PYTHONPATH"] = os.pathsep.join(
            [SYSTEM_SITE, str(REPO / "qgis_plugin"), env.get("PYTHONPATH", "")])
        return subprocess.call(["/usr/bin/python3", __file__], env=env)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return _render()


if __name__ == "__main__":
    raise SystemExit(main())
