"""aXqua - TELEMAC and OpenFOAM river modelling from QGIS.

QGIS calls :func:`classFactory` to load the plugin. Everything else is imported lazily
from inside it, so a broken import in a widget produces a readable error in the plugin
manager rather than a QGIS that will not start.

The one architectural rule this package lives by: **it never imports axqua.** QGIS
ships its own Python; axqua needs gmsh, rasterio, geopandas and a solver's
environment. The plugin talks to the ``axqua`` command-line tool and reads the files
it writes, so either side can be reinstalled without touching the other.
"""

from __future__ import annotations

from pathlib import Path


def _version() -> str:
    """The version in ``metadata.txt``, which is the one QGIS shows.

    Read rather than repeated. A second literal here drifted from the manifest across
    two releases without anything noticing, and the number a user reads in the plugin
    manager is always the manifest's - so that file is the source and this follows it.
    """
    try:
        for line in (Path(__file__).with_name("metadata.txt")
                     .read_text(encoding="utf-8").splitlines()):
            if line.startswith("version="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "0.0.0"


__version__ = _version()


def classFactory(iface):        # noqa: N802 - the name QGIS requires
    """Return the plugin instance for *iface*."""
    from .plugin import AxquaPlugin
    return AxquaPlugin(iface)
