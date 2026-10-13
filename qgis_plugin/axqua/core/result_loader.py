"""Loading a job's results into QGIS, from the manifest the runner wrote.

The runner produces files and a small ``results/results.json`` describing them; the
plugin reads that and decides what to add. Neither side ever hands the other an array
(plan §26), and the runner never imports PyQGIS (plan §14).

Discovery is **manifest-first, glob-second**. A manifest says what a result *is* - which
variable to style, whether it is a mesh or a table - and a glob can only guess from a file
extension. The glob fallback exists for a job that predates the manifest or whose
postprocessing failed, so the user can still reach their data.

Layers go into a ``axqua/<job id>`` group and nothing else about the project is
touched. A plugin that quietly reordered layers or changed the CRS would be a plugin
people stop trusting with their work.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# PyQGIS is imported where it is used, not here. **Discovery is pure Python** - reading a
# manifest, walking a folder, classifying a suffix - and keeping it importable without
# QGIS means it can be tested in CI, where PyQGIS is not installable, and reused from a
# script. The loading half below needs QGIS and says so at each point of use.

#: Extensions QGIS can open, and how. Used only by the glob fallback.
MESH_SUFFIXES = {".slf", ".sslf", ".nc", ".2dm", ".msh", ".vtu", ".vtk", ".pvd"}
RASTER_SUFFIXES = {".tif", ".tiff", ".asc", ".vrt"}
VECTOR_SUFFIXES = {".gpkg", ".shp", ".geojson", ".json"}
TABLE_SUFFIXES = {".csv", ".xlsx"}

GROUP_NAME = "axqua"

#: The runner's own description of what it wrote. Never a result itself.
MANIFEST_NAME = "results.json"

#: Stop the glob fallback after this many entries. A TELEMAC job folder holds a handful
#: of files, but an OpenFOAM run directory holds a time directory per write per rank and
#: reaches hundreds of thousands of them - and ``rglob`` over that is minutes of stat
#: calls with nothing on screen to say why.
GLOB_LIMIT = 20000


@dataclass
class ResultItem:
    """One loadable thing, from the manifest or discovered."""

    name: str
    path: Path
    kind: str = "file"
    style: str = ""
    variable: str = ""
    description: str = ""

    @property
    def loadable(self) -> bool:
        """Whether QGIS can put this on the map at all.

        A CSV of fluxes and an xlsx convergence report are real results and are listed,
        but they open in a spreadsheet, not the canvas - so they are offered as files to
        open rather than silently skipped or wrongly added as an empty table layer.
        """
        return self.kind in {"mesh", "raster", "vector"}


@dataclass
class JobResults:
    job_id: str
    root: Path
    items: list[ResultItem] = field(default_factory=list)
    objective: float | None = None
    summary: dict = field(default_factory=dict)
    from_manifest: bool = True
    #: EPSG code of the case, from the manifest. ``None`` for a job that predates it.
    crs_epsg: int | None = None

    @property
    def layers(self) -> list[ResultItem]:
        return [i for i in self.items if i.loadable]

    @property
    def documents(self) -> list[ResultItem]:
        return [i for i in self.items if not i.loadable]


def discover(job_root: str | os.PathLike) -> JobResults:
    """What this job produced. Never raises - an unreadable job shows as empty."""
    root = Path(job_root)
    manifest = root / "results" / MANIFEST_NAME
    if manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            return _from_manifest(root, payload)
    return _by_globbing(root)


def _from_manifest(root: Path, payload: dict) -> JobResults:
    items = []
    for entry in payload.get("results") or []:
        raw = Path(str(entry.get("path", "")))
        # Manifest paths are relative to the job directory so a restored archive under a
        # different mount still opens.
        path = raw if raw.is_absolute() else (root / raw)
        if not path.exists():
            continue
        items.append(ResultItem(
            name=str(entry.get("name") or path.name),
            path=path,
            kind=str(entry.get("kind") or _kind_of(path)),
            style=str(entry.get("style") or ""),
            variable=str(entry.get("variable") or ""),
            description=str(entry.get("description") or ""),
        ))
    return JobResults(job_id=str(payload.get("job_id") or root.name), root=root,
                      items=items, objective=payload.get("objective"),
                      summary=dict(payload.get("summary") or {}),
                      crs_epsg=_epsg(payload.get("crs_epsg")))


def _epsg(value) -> int | None:
    """An EPSG code out of a manifest; ``None`` for anything that is not one."""
    try:
        code = int(value)
    except (TypeError, ValueError):
        return None
    return code if code > 0 else None


def _by_globbing(root: Path) -> JobResults:
    """Fallback for a job with no manifest, so the data is still reachable.

    Bounded, and over ``results/`` and ``input/`` only - ``results/qgis/`` used to be
    walked as well, which is a subtree of the first and so was walked twice.
    """
    items: list[ResultItem] = []
    seen: set[Path] = set()
    budget = GLOB_LIMIT
    for folder in (root / "results", root / "input"):
        if not folder.is_dir():
            continue
        for path in sorted(_walk(folder, budget)):
            budget -= 1
            if budget <= 0:
                break
            if not path.is_file() or path in seen:
                continue
            if path.name == MANIFEST_NAME:
                # ``.json`` is in the vector suffixes, so the manifest itself would
                # otherwise be offered as a layer - and it is the one file in the folder
                # that is certainly not a result.
                continue
            kind = _kind_of(path)
            if kind == "file":
                continue
            seen.add(path)
            items.append(ResultItem(name=path.stem, path=path, kind=kind,
                                    style=_guess_style(path)))
    return JobResults(job_id=root.name, root=root, items=items, from_manifest=False)


def _walk(folder: Path, budget: int) -> list[Path]:
    """At most *budget* entries under *folder*, breadth first.

    Breadth first because what is being looked for - a result file the postprocessing
    left behind - is near the top, while the enormous part of an OpenFOAM directory is
    deep inside per-rank time folders.
    """
    out: list[Path] = []
    queue = [folder]
    while queue and len(out) < budget:
        current = queue.pop(0)
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if len(out) >= budget:
                break
            if entry.is_dir():
                queue.append(entry)
            else:
                out.append(entry)
    return out


def _kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in MESH_SUFFIXES:
        return "mesh"
    if suffix in RASTER_SUFFIXES:
        return "raster"
    if suffix in VECTOR_SUFFIXES:
        return "vector"
    if suffix in TABLE_SUFFIXES:
        return "table"
    if suffix in {".png", ".jpg", ".pdf", ".svg"}:
        return "figure"
    return "file"


def _guess_style(path: Path) -> str:
    name = path.name.lower()
    if "depth" in name:
        return "water-depth"
    if "veloc" in name:
        return "velocity"
    return ""


# ------------------------------------------------------------------------- loading


class ResultLoader:
    """Puts a job's results on the map, grouped and styled."""

    def __init__(self, project=None, *, min_depth: float | None = None,
                 velocity_cap: float | None = None) -> None:
        from qgis.core import QgsProject

        from . import styles
        self.project = project or QgsProject.instance()
        self.min_depth = float(styles.DEFAULT_MIN_DEPTH if min_depth is None
                               else min_depth)
        self.velocity_cap = float(styles.DEFAULT_VELOCITY_MAX if velocity_cap is None
                                  else velocity_cap)
        self.warnings: list[str] = []
        #: Layers that were already in the group and so were not added again.
        self.reused: list = []
        self.at_last_step = 0

    @staticmethod
    def _georeference(layer, epsg: int | None) -> None:
        """Give *layer* the CRS of its case where the file itself names none.

        A SELAFIN mesh has no CRS. Without one QGIS draws the coordinates as they are,
        which only lands in the right place when the project uses the same system: over
        a web base map the reach sits thousands of kilometers away, and the user is
        left to find out which EPSG code the case was built in. The case knows it. A
        layer that already carries a CRS keeps it, and the project's own is never
        touched.
        """
        if not epsg or layer.crs().isValid():
            return
        from qgis.core import QgsCoordinateReferenceSystem

        crs = QgsCoordinateReferenceSystem.fromEpsgId(int(epsg))
        if crs.isValid():
            layer.setCrs(crs)

    def load(self, results: JobResults, *, items=None) -> list:
        """Add *results* to the layer tree. Returns the layers actually added.

        Loading the same job twice does not double its layers. Pressing *Load results*
        again is the natural thing to do when a run has advanced, and it used to leave
        the user deleting duplicates by hand.
        """
        self.warnings = []
        self.reused = []
        self.at_last_step = 0      # layers with several time steps, shown at the last
        targets = list(items if items is not None else results.layers)
        if not targets:
            return []
        group = self._group(results.job_id)
        present = _sources_in(group)
        added = []
        for item in targets:
            existing = present.get(str(item.path))
            if existing is not None:
                # Already on the map from an earlier load. Re-styling it is still worth
                # doing: the run has moved on, so the data range may have, and the file
                # may hold more time steps than when the layer was opened.
                if hasattr(existing, "reload"):
                    existing.reload()
                self._style(existing, item)
                self._last_step(existing, item)
                existing.triggerRepaint()
                self.reused.append(existing)
                continue
            layer = self._layer_for(item)
            if layer is None:
                self.warnings.append(f"{item.name}: QGIS could not open {item.path.name}")
                continue
            # Validity is checked before adding, or an invalid layer sits in the tree
            # looking like a result that failed rather than one that never opened.
            if not layer.isValid():
                self.warnings.append(
                    f"{item.name}: {item.path.name} did not load "
                    f"({layer.error().summary() if hasattr(layer, 'error') else 'invalid'})")
                continue
            self._georeference(layer, results.crs_epsg)
            self._style(layer, item)
            self._last_step(layer, item)
            self.project.addMapLayer(layer, False)
            group.addLayer(layer)
            added.append(layer)
        return added

    def _last_step(self, layer, item: ResultItem) -> None:
        """Show a result at its end, and say so where QGIS did not take it."""
        from qgis.core import QgsMeshDatasetIndex, QgsMeshLayer
        if not isinstance(layer, QgsMeshLayer):
            return
        try:
            if show_last_time_step(layer):
                self.at_last_step += 1
                return
            group = layer.rendererSettings().activeScalarDatasetGroup()
            several = group >= 0 and layer.datasetCount(QgsMeshDatasetIndex(group, 0)) > 1
        except Exception as exc:  # noqa: BLE001 - the layer is loaded either way
            several, group = True, f"{type(exc).__name__}: {exc}"
        if several:
            # Not silent: the map then shows the start of the run, not its result.
            self.warnings.append(
                f"{item.name}: QGIS shows the first time step of this layer, which is "
                "the initial state. Select the last one with the Temporal Controller.")

    def _group(self, job_id: str):
        root = self.project.layerTreeRoot()
        parent = root.findGroup(GROUP_NAME) or root.insertGroup(0, GROUP_NAME)
        return parent.findGroup(job_id) or parent.addGroup(job_id)

    def _layer_for(self, item: ResultItem):
        from qgis.core import QgsMeshLayer, QgsRasterLayer, QgsVectorLayer
        path = str(item.path)
        if item.kind == "mesh":
            # MDAL reads SELAFIN natively, which is exactly why the runner converts
            # nothing: a copy of a multi-hundred-megabyte result would buy nothing.
            return QgsMeshLayer(path, item.name, "mdal")
        if item.kind == "raster":
            return QgsRasterLayer(path, item.name)
        if item.kind == "vector":
            return QgsVectorLayer(path, item.name, "ogr")
        return None

    def _style(self, layer, item: ResultItem) -> None:
        from qgis.core import QgsMeshLayer, QgsVectorLayer
        if not item.style:
            return
        try:
            if isinstance(layer, QgsMeshLayer):
                self._style_mesh(layer, item)
            elif isinstance(layer, QgsVectorLayer) and item.style == STYLE_GRADUATED:
                if not color_by_attribute(layer, item.variable):
                    self.warnings.append(
                        f"{item.name}: the layer has no attribute '{item.variable}' "
                        "to color it by")
        except Exception as exc:  # noqa: BLE001
            # Styling is a nicety; an unstyled layer the user can restyle beats no layer.
            self.warnings.append(f"{item.name}: default styling failed ({exc})")

    def _style_mesh(self, layer, item: ResultItem) -> None:
        from . import styles
        index = _dataset_index(layer, item.variable)
        if index is None:
            self.warnings.append(
                f"{item.name}: no dataset called {item.variable!r} in this mesh")
            return
        renderer = layer.rendererSettings()
        if item.style == "water-depth":
            maximum, why = _dataset_maximum(layer, index)
            if maximum is None:
                # Not silent: 1 m on a 6 m river renders the whole channel one colour,
                # and a user who is not told will read that as a modelling result.
                self.warnings.append(
                    f"{item.name}: the depth range could not be read from the file "
                    f"({why}), so the colour scale is capped at 1 m. Adjust it in "
                    "Layer Properties > Symbology.")
                maximum = 1.0
            style = styles.DepthStyle(minimum=self.min_depth, maximum=maximum)
            renderer.setActiveScalarDatasetGroup(index)
            renderer.setScalarSettings(index, styles.depth_settings(style))
        elif item.style == "velocity":
            observed, why = _dataset_maximum(layer, index)
            if observed is None:
                self.warnings.append(
                    f"{item.name}: the velocity range could not be read from the file "
                    f"({why}), so the default scale is used.")
            style = styles.clamp_velocity(observed, self.velocity_cap)
            if style.warning:
                self.warnings.append(f"{item.name}: {style.warning}")
            renderer.setActiveScalarDatasetGroup(index)
            renderer.setScalarSettings(index, styles.velocity_scalar_settings(style))
            vector_index = _vector_index(layer, item.variable)
            if vector_index is None:
                self.warnings.append(
                    f"{item.name}: no vector dataset matches {item.variable!r}, so no "
                    "arrows were drawn. Choose one in Layer Properties > Symbology.")
            if vector_index is not None:
                renderer.setActiveVectorDatasetGroup(vector_index)
                renderer.setVectorSettings(vector_index,
                                           styles.velocity_vector_settings(style))
        else:
            return
        layer.setRendererSettings(renderer)
        layer.triggerRepaint()


#: The names under which a result carries the water depth and the velocity. A
#: depth-averaged companion of a 3D run has the velocity vector and no scalar velocity.
DEPTH_NAMES = ("WATER DEPTH",)
VELOCITY_SCALARS = ("SCALAR VELOCITY",)
VELOCITY_VECTORS = ("VELOCITY",)


#: The style of a polygon layer whose result is one of its attributes.
STYLE_GRADUATED = "graduated"

#: Classes of that style. Each holds the same number of polygons, so that a result
#: whose values span several orders of magnitude, as rates of deposition do, still
#: shows a pattern and not one color with a few outliers.
GRADUATED_CLASSES = 7


def color_by_attribute(layer, attribute: str, *, classes: int = GRADUATED_CLASSES) -> bool:
    """Color the polygons of *layer* by a numeric attribute. False without it."""
    from qgis.core import (QgsClassificationQuantile, QgsFillSymbol,
                           QgsGraduatedSymbolRenderer, QgsStyle)
    if not attribute or layer.fields().indexOf(attribute) < 0:
        return False
    renderer = QgsGraduatedSymbolRenderer(attribute)
    # no outlines: at the scale of a reach they would cover the colors of the cells
    renderer.setSourceSymbol(QgsFillSymbol.createSimple({"outline_style": "no"}))
    renderer.setClassificationMethod(QgsClassificationQuantile())
    renderer.updateClasses(layer, classes)
    ramp = QgsStyle.defaultStyle().colorRamp("Viridis")
    if ramp is not None:
        renderer.updateColorRamp(ramp)
    layer.setRenderer(renderer)
    return True


def apply_map_style(layer, key: str, *, min_depth: float | None = None,
                    velocity_cap: float | None = None) -> list[str]:
    """Give a result layer one of the predefined map styles (``styles.MAP_STYLES``).

    The fill is the water depth or the velocity magnitude, and the velocity is drawn on
    top of it as arrows or as streamlines, colored by its magnitude. Returns what the
    user has to know about the result (a capped color scale). Raises ``ValueError``
    with a sentence for the user where the layer is no result that can be drawn so.
    """
    from qgis.core import QgsMeshLayer

    from . import styles

    if key not in styles.MAP_STYLES:
        raise ValueError(f"there is no map style {key!r}")
    if not isinstance(layer, QgsMeshLayer):
        raise ValueError("Select a result layer of a simulation in the layer panel. "
                         "The map styles apply to mesh layers.")
    _title, fill, drawn = styles.MAP_STYLES[key]
    depth = next((index for name in DEPTH_NAMES
                  if (index := _dataset_index(layer, name)) is not None), None)
    vector = next((index for name in VELOCITY_VECTORS
                   if (index := _vector_index(layer, name)) is not None), None)
    if vector is None:
        raise ValueError(f"The layer {layer.name()} has no velocity. Select a result "
                         "of a hydraulic simulation.")
    if fill == "depth" and depth is None:
        raise ValueError(f"The layer {layer.name()} has no water depth.")

    notes: list[str] = []
    scalar = next((index for name in VELOCITY_SCALARS
                   if (index := _dataset_index(layer, name)) is not None
                   and not group_metadata(layer, index).isVector()), vector)
    observed, _why = _dataset_maximum(layer, scalar)
    speed = styles.clamp_velocity(
        observed, styles.DEFAULT_VELOCITY_MAX if velocity_cap is None else velocity_cap)
    if speed.warning:
        notes.append(speed.warning)

    renderer = layer.rendererSettings()
    if fill == "depth":
        maximum, why = _dataset_maximum(layer, depth)
        if maximum is None:
            notes.append(f"The depth range could not be read from the file ({why}), so "
                         "the color scale ends at 1 m.")
            maximum = 1.0
        renderer.setActiveScalarDatasetGroup(depth)
        renderer.setScalarSettings(depth, styles.depth_settings(styles.DepthStyle(
            minimum=styles.DEFAULT_MIN_DEPTH if min_depth is None else min_depth,
            maximum=maximum)))
    else:
        renderer.setActiveScalarDatasetGroup(scalar)
        renderer.setScalarSettings(scalar, styles.velocity_scalar_settings(speed))
    renderer.setActiveVectorDatasetGroup(vector)
    renderer.setVectorSettings(vector, (
        styles.streamline_vector_settings if drawn == "streamlines"
        else styles.velocity_vector_settings)(speed, by_magnitude=fill == "depth"))
    layer.setRendererSettings(renderer)
    layer.triggerRepaint()
    return notes


def ensure_reference_time(layer) -> None:
    """Give a mesh layer a reference time where its file names none.

    The times of the steps are counted from it. QGIS sets a default for a layer that is
    added through its own dialogs and not for one a plugin adds, and a result without a
    date in its header then has no moment for any of its steps: every frame of a movie
    was the first one.
    """
    from qgis.PyQt.QtCore import QDate, QDateTime, Qt, QTime

    from ..compat import enum_value

    properties = layer.temporalProperties()
    if properties.referenceTime().isValid():
        return
    start = QDateTime(QDate(2000, 1, 1), QTime(0, 0, 0),
                      enum_value(Qt, "TimeSpec.UTC", "UTC"))
    properties.setReferenceTime(start, layer.dataProvider().temporalCapabilities())


def time_range_of(layer, group: int, step: int):
    """The moment of one time step of a mesh layer, as the range a map is drawn for.

    This is how a map that has a time selects a step, and how the movie export selects
    its frames: the layer has no function for it in the Python interface of QGIS 3.44.
    """
    from qgis.core import QgsDateTimeRange, QgsMeshDatasetIndex

    ensure_reference_time(layer)
    when = layer.temporalProperties().referenceTime().addMSecs(
        int(layer.datasetRelativeTimeInMilliseconds(QgsMeshDatasetIndex(group, step))))
    return QgsDateTimeRange(when, when.addMSecs(1))


def show_last_time_step(layer) -> bool:
    """Make *layer* show its last time step wherever a map has no time. ``True`` when
    the layer has several steps and now stands at the last one.

    QGIS draws the **first** step of a mesh layer on a map without a time: of a steady
    simulation that is the initial state, with standing water and no velocity, which
    looks like a result and is none. Which step that is, is a property of the layer
    (``staticScalarDatasetIndex``). It can be read from Python and not set, so it is
    set where QGIS itself keeps it: in the description of the layer that a project
    file stores, written out, changed and read back.

    Being a property of the layer, it holds for the map, for a print layout and after
    the project is opened again, and it does not touch the Temporal Controller, whose
    range stays the whole run for whoever wants another step. An earlier version set
    the time of the map canvas instead, which did none of that.
    """
    from qgis.core import QgsMeshDatasetIndex, QgsMeshLayer, QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument

    if not isinstance(layer, QgsMeshLayer):
        return False
    settings = layer.rendererSettings()
    scalar, vector = settings.activeScalarDatasetGroup(), settings.activeVectorDatasetGroup()
    if scalar < 0:
        return False
    last_scalar = layer.datasetCount(QgsMeshDatasetIndex(scalar, 0)) - 1
    last_vector = (layer.datasetCount(QgsMeshDatasetIndex(vector, 0)) - 1
                   if vector >= 0 else last_scalar)
    if last_scalar < 1:
        return False                               # one step: nothing to select
    ensure_reference_time(layer)
    document = QDomDocument("axqua")
    element = document.createElement("maplayer")
    context = QgsReadWriteContext()
    if not layer.writeLayerXml(element, document, context):
        return False
    static = element.firstChildElement("static-active-dataset")
    if static.isNull():
        static = document.createElement("static-active-dataset")
        element.appendChild(static)
    static.setAttribute("scalar", str(last_scalar))
    static.setAttribute("vector", str(max(last_vector, 0)))
    if not layer.readLayerXml(element, context):
        return False
    layer.triggerRepaint()
    return layer.staticScalarDatasetIndex().dataset() == last_scalar


def group_metadata(layer, index: int):
    """Dataset-group metadata for group *index*, whichever spelling QGIS accepts.

    ``datasetGroupsIndexes()`` returns plain ints and ``datasetGroupMetadata()`` takes a
    ``QgsMeshDatasetIndex``, so the obvious composition of the two raises ``TypeError``.
    That is exactly what used to happen to every mesh this plugin styled: the exception
    was caught by the styling guard and reported as "default styling failed", so the
    depth and velocity ramps the documentation leads with were never once applied. The
    int form is tried second in case a future build adds the overload.
    """
    from qgis.core import QgsMeshDatasetIndex
    try:
        return layer.datasetGroupMetadata(QgsMeshDatasetIndex(int(index)))
    except TypeError:                          # pragma: no cover - depends on the build
        return layer.datasetGroupMetadata(int(index))


def _sources_in(group) -> dict:
    """The layers already in *group*, by data source.

    The source is the right identity here rather than the layer name: the user is free
    to rename a layer, and renaming it should not cause a second copy of the same file
    to appear underneath it on the next load.
    """
    out: dict = {}
    for child in getattr(group, "children", list)():
        layer = child.layer() if hasattr(child, "layer") else None
        if layer is not None:
            try:
                out[layer.source()] = layer
            except (RuntimeError, AttributeError):
                continue
    return out


def _dataset_index(layer, variable: str) -> int | None:
    """Find a dataset group by name, tolerantly.

    SELAFIN names are fixed-width and often arrive padded ('WATER DEPTH     '), and a
    3D result may prefix them. An exact match would fail on files that are perfectly
    fine, so the comparison is trimmed and case-insensitive.
    """
    if not variable:
        return None
    wanted = variable.strip().upper()
    for index in layer.datasetGroupsIndexes():
        meta = group_metadata(layer, index)
        name = (meta.name() or "").strip().upper()
        if name == wanted:
            return index
    for index in layer.datasetGroupsIndexes():
        name = (group_metadata(layer, index).name() or "").strip().upper()
        if wanted in name:
            return index
    return None


def _vector_index(layer, variable: str = "") -> int | None:
    """The vector dataset group the arrows should come from.

    Matched against the variable being styled, not simply the first vector group in the
    file. A morphodynamic result carries both a velocity and a bedload vector, and
    taking whichever came first drew bedload arrows over a velocity ramp - which looks
    authoritative and is wrong.

    With no match and more than one candidate the honest answer is none: ambiguous
    arrows are worse than no arrows, and the caller says so.
    """
    vectors = []
    wanted = (variable or "").strip().upper()
    for index in layer.datasetGroupsIndexes():
        meta = group_metadata(layer, index)
        if not meta.isVector():
            continue
        name = (meta.name() or "").strip().upper()
        if wanted and (name == wanted or wanted in name):
            return index
        vectors.append(index)
    return vectors[0] if len(vectors) == 1 else None


def _dataset_maximum(layer, index: int) -> tuple[float | None, str]:
    """The dataset's own maximum, so the scale fits the data rather than a guess.

    Returns the reason alongside, because the fallback that used to be applied here -
    ``or 1.0`` - is a visible, wrong answer on any river deeper than a metre, and the
    user has to be told which one they are looking at.
    """
    try:
        meta = group_metadata(layer, index)
        value = meta.maximum()
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    if value is None:
        return None, "the file reports no maximum for this dataset"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None, f"the reported maximum {value!r} is not a number"
    if value != value:
        return None, "the reported maximum is NaN"
    if value <= 0:
        return None, f"the reported maximum is {value:g}"
    return value, ""
