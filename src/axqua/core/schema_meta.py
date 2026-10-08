"""What a form needs to know about every setting of a case file.

``axqua schema --json`` prints this table, and the case editor of the QGIS plugin builds
its forms from it. The plugin cannot import aXqua, so it cannot ask the dataclasses in
:mod:`axqua.config` itself, and a second, hand-written list of settings in the plugin
would be out of date with the first new field.

The table is **derived**, so it cannot miss a setting: the blocks and keys are those of
a completely written default case (``config_to_dict``), the kind of each value comes
from the annotation of its dataclass field, and its explanation is the comment that
stands with the field in the source. On top of that, :data:`CURATED` gives the settings
a modeler meets first a plain label, a unit and, for a file, what kind of file it is.
Those are the **essential** settings, which a form shows at once. Every other setting is
complete but stays out of the way until it is asked for.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
import tempfile
import types
import typing
from pathlib import Path
from typing import Any

__all__ = ["BLOCKS", "CURATED", "KINDS", "block_classes", "build", "field_kinds"]

#: What a value can be, as far as a form is concerned.
KINDS = ("text", "int", "number", "bool", "choice", "file", "folder", "vector", "raster",
         "table", "yaml")

#: The blocks of a case file in the order of the workflow: ``(key, title, docs label)``.
#: The label is the section of the page *aXqua case setup* that explains the block.
BLOCKS: tuple[tuple[str, str, str], ...] = (
    ("project", "Project paths", "case-project"),
    ("geodata", "Geodata", "case-geodata"),
    ("structures", "Structures", "case-geodata"),
    ("surfaces", "Surfaces from CAD", "case-surfaces"),
    ("boundaries", "Boundaries", "case-boundaries"),
    ("gain_lose", "Exchange with a porous body", "case-boundaries"),
    ("initialization", "Initialization", "case-initialization"),
    ("drying", "Drying of thin films", "case-initialization"),
    ("mesh", "Mesh configuration", "case-mesh"),
    ("friction", "Wall roughness", "case-roughness"),
    ("hydrodynamics", "Hydraulic simulation", "case-hydraulics"),
    ("telemac", "TELEMAC", "case-hydraulics"),
    ("openfoam", "OpenFOAM", "case-hydraulics"),
    ("morphodynamics", "Morphodynamic simulation", "case-morphodynamics"),
    ("dem_of_difference", "DEM of difference", "case-morphodynamics"),
    ("ground_truth", "Ground truth data", "help-calibration"),
    ("calibration", "Calibration", "help-calibration"),
    ("postproc", "Figures", "help-postprocessing"),
)


def _c(label: str, kind: str = "", unit: str = "", choices: tuple = (),
       help: str = "") -> dict[str, Any]:              # noqa: A002 - the column's name
    return {"label": label, "kind": kind, "unit": unit, "choices": choices, "help": help}


#: The settings a form shows first, with what a modeler calls them.
CURATED: dict[str, dict[str, Any]] = {
    "project.name": _c("Name of the case", help="Used in titles and file headers."),
    "project.crs_epsg": _c("Coordinate reference system (EPSG code)", "int",
                           help="A projected system with coordinates in meters, for "
                                "example 25832 for ETRS89 / UTM zone 32N."),
    "project.sim_dir": _c("Folder for results", "folder",
                          help="Everything aXqua produces is written into this folder. "
                               "A path relative to the case file is recommended."),
    "geodata.dem_initial": _c("Terrain model", "raster",
                              help="Digital elevation model with the river bed."),
    "geodata.boundary": _c("Model outline", "vector",
                           help="One polygon around the area to be simulated."),
    "geodata.mesh_zones": _c("Mesh zones", "vector",
                             help="Polygons named channel, floodplain or refinement, "
                                  "each with its edge length."),
    "geodata.channel_centerline": _c("Channel centerline", "vector",
                                     help="Line along the channel. Mesh cells in the "
                                          "channel are stretched along it."),
    "geodata.roughness_zones": _c("Roughness zones", "vector",
                                  help="Polygons with a whole-number Zone ID."),
    "geodata.roughness_table": _c("Roughness table", "table",
                                  help="Table with the columns zone_id and ks."),
    "geodata.dem_target": _c("Second terrain model", "raster",
                             help="A later survey, for the bed change between the two."),
    "geodata.structures": _c("Structures", "vector",
                             help="Dams, weirs, walls and buildings with their crest "
                                  "level or height."),
    "geodata.control_sections": _c("Control sections", "vector",
                                   help="Lines across which the discharge is reported."),
    "boundaries.liquid_boundaries": _c("Inflow and outflow lines", "vector",
                                       help="Lines on the model outline with the type "
                                            "inflow or outflow."),
    "boundaries.prescribed_flowrate": _c("Discharge", "number", "m3/s",
                                         help="Total inflow of the steady simulation."),
    "boundaries.outflow_condition": _c(
        "Water level at the outflow", "choice",
        choices=("elevation", "stage_discharge", "free"),
        help="elevation: the fixed level entered below. stage_discharge: from a rating "
             "curve at the simulated discharge. free: nothing is prescribed."),
    "boundaries.prescribed_elevation": _c("Outflow water level", "number", "m a.s.l."),
    "boundaries.stage_discharge": _c("Rating curve", "table",
                                     help="Table of discharge and water level at the "
                                          "outflow. Computed from the terrain if empty."),
    "boundaries.rating_method": _c(
        "Rating curve from the terrain", "choice", choices=("trapezoid", "section"),
        help="section uses the real cross section at the outflow line and is the "
             "better choice for a natural channel."),
    "boundaries.inflow": _c("Hydrograph", "table",
                            help="Discharge over time. Only needed for an unsteady "
                                 "simulation."),
    "initialization.prewet_depth": _c(
        "Water depth at the start", "number", "m",
        help="Starts the simulation with water in the channel, which saves the time "
             "the flow needs to fill a dry bed. Leave empty for a dry start."),
    "mesh.channel_size": _c("Edge length in the channel", "number", "m"),
    "mesh.floodplain_size": _c("Edge length on the floodplain", "number", "m"),
    "mesh.refinement_size": _c("Edge length in refinement zones", "number", "m"),
    "mesh.size_scale": _c("Factor on all edge lengths", "number", "-",
                          help="2 doubles every edge length, which gives a mesh with "
                               "about a quarter of the cells. Useful for a first run."),
    "mesh.channel_anisotropy": _c("Length to width of channel cells", "number", "-"),
    "friction.roughness_law": _c(
        "Roughness law", "int",
        help="TELEMAC number of the law: 5 is Nikuradse (ks in meters), 3 Strickler, "
             "4 Manning."),
    "friction.boundary_law": _c("Roughness law at closed boundaries", "int"),
    "friction.boundary_coefficient": _c("Roughness at closed boundaries", "number"),
    "hydrodynamics.duration": _c("Simulated time", "number", "s"),
    "hydrodynamics.turbulence_model": _c(
        "Turbulence model", help="auto selects the model that suits the cell size."),
    "hydrodynamics.desired_courant": _c("Courant number", "number", "-"),
    "hydrodynamics.flux_tolerance": _c(
        "Tolerance of the discharge balance", "number", "-",
        help="The simulation is steady when inflow and outflow differ by less than "
             "this share."),
    "telemac.solver": _c("Solver", "choice", choices=("telemac2d", "telemac3d")),
    "telemac.n_processors": _c("Processor cores", "int"),
    "gain_lose.enabled": _c("Model the exchange", "bool"),
    "gain_lose.zone": _c("Porous body", "vector",
                         help="Polygon of the gravel bar the water passes through."),
    "gain_lose.faces": _c("Where the exchange happens", "choice",
                          choices=("water-table", "lines")),
    "morphodynamics.enabled": _c("Simulate sediment transport", "bool"),
    "dem_of_difference.enabled": _c("Compute the bed change", "bool"),
    "ground_truth.sources": _c("Measurement files", "yaml",
                               help="One entry per file: category, kind and positions."),
    "ground_truth.targets": _c("Calibration target workbook", "table"),
    "calibration.parameters": _c("Calibration parameters", "yaml",
                                 help="One entry per parameter: name, min and max."),
    "calibration.calibration_quantities": _c("Quantities to calibrate against", "yaml"),
    "calibration.init_runs": _c("Simulations before the surrogate model", "int"),
    "calibration.max_runs": _c("Simulations in total", "int"),
    "openfoam.mode": _c("Free surface", "choice", choices=("vof", "rigid-lid")),
    "openfoam.cell_size": _c("Cell size", "number", "m"),
}

#: Choices of settings that are not essential, where the code accepts a fixed set only.
CHOICES: dict[str, tuple[str, ...]] = {
    "initialization.prewet_mode": ("normal-depth", "constant"),
    "gain_lose.mode": ("off", "region", "fortran"),
    "gain_lose.losing_region": ("patch", "line"),
    "gain_lose.water_table": ("off", "phreatic"),
}

#: Keys of the ``project`` block, which are fields of :class:`~axqua.config.Config`
#: itself and not of a block class.
_PROJECT = {"name": "text", "crs_epsg": "int", "sim_dir": "folder",
            "preprocessing_dir": "folder", "model_dir": "folder",
            "postprocessing_dir": "folder", "calibration_dir": "folder"}

_RASTER = re.compile(r"(^|_)dem(_|$)|raster|dem_of_difference|^ortho")
_TABLE = re.compile(r"table|stage_discharge|^inflow$|measurements|targets|series|csv")
_FOLDER = re.compile(r"(_dir|_folder|_root)$|^case_dir$")


def _file_kind(name: str) -> str:
    if _FOLDER.search(name):
        return "folder"
    if _RASTER.search(name):
        return "raster"
    if _TABLE.search(name):
        return "table"
    return "vector"


def _kind(annotation: Any, default: Any, name: str) -> str:
    """The kind of a value, from the annotation of its field."""
    options = typing.get_args(annotation) if (
        typing.get_origin(annotation) in (typing.Union, types.UnionType)) else (annotation,)
    options = [option for option in options if option is not type(None)]
    if any(option is Path for option in options):
        return _file_kind(name)
    if len(options) == 1:
        only = options[0]
        if only is bool:
            return "bool"
        if only is int:
            return "int"
        if only is float:
            return "number"
        if only is str:
            return "text"
    if options and all(option in (int, float, str) for option in options):
        # "auto" or a number: whatever is typed is kept as it is written
        return "text" if str in options else "number"
    if isinstance(default, bool):
        return "bool"
    return "yaml"                                 # a list, a table or a nested block


def _comments(cls: type) -> dict[str, str]:
    """The comment that stands with each field in the source of a dataclass."""
    try:
        lines = inspect.getsource(cls).splitlines()
    except (OSError, TypeError):
        return {}
    out: dict[str, str] = {}
    pending: list[str] = []
    field_line = re.compile(r"^\s{4}([a-z_][a-z0-9_]*)\s*:\s*[^=#]+(?:=[^#]*)?(?:#\s*(.*))?$")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            pending.append(stripped.lstrip("#").strip())
            continue
        match = field_line.match(line)
        if match:
            parts = pending + ([match.group(2)] if match.group(2) else [])
            text = " ".join(part for part in parts if part)
            if text:
                out[match.group(1)] = re.sub(r"\s+", " ", text)
        pending = []
    return out


def _label(name: str) -> str:
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _json(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


def _default_case():
    """A case with nothing but what the loader insists on, and all of it written out."""
    from axqua.config import config_to_dict, load_config

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "default.axq-case"
        path.write_text("project: {name: case, crs_epsg: 25832}\n"
                        "geodata: {dem_initial: dem.tif, boundary: outline.gpkg}\n",
                        encoding="utf-8")
        cfg = load_config(path)
        cfg.declared_blocks = frozenset()          # so that every block is written
        return cfg, config_to_dict(cfg, base=Path(folder), portable=True)


def build() -> list[dict[str, Any]]:
    """The sections of a case file with their settings, as plain data."""
    cfg, written = _default_case()
    sections = []
    listed = {block for block, _title, _label_ in BLOCKS}
    order = list(BLOCKS) + [(block, _label(block), "help-case-setup")
                            for block in written if block not in listed]
    for block, title, label in order:
        values = written.get(block)
        if not isinstance(values, dict):
            continue
        owner = getattr(cfg, block, None)
        cls = type(owner) if dataclasses.is_dataclass(owner) else None
        hints = typing.get_type_hints(cls) if cls is not None else {}
        comments = _comments(cls) if cls is not None else {}
        names = list(values)
        if block == "project":
            names = ["name", "crs_epsg", "sim_dir"] + [n for n in values
                                                       if n not in ("name", "crs_epsg")]
        fields = []
        for name in names:
            key = f"{block}.{name}"
            curated = CURATED.get(key, {})
            default = None if name in ("dem_initial", "boundary") else values.get(name)
            if block == "project":
                kind = _PROJECT.get(name, "text")
                default = values.get(name) if name != "sim_dir" else "axqua-case"
            else:
                kind = _kind(hints.get(name), default, name)
            kind = curated.get("kind") or ("choice" if key in CHOICES else kind)
            fields.append({
                "key": key, "block": block, "name": name,
                "label": curated.get("label") or _label(name),
                "kind": kind,
                "unit": curated.get("unit", ""),
                "help": curated.get("help") or comments.get(name, ""),
                "choices": list(curated.get("choices") or CHOICES.get(key, ())),
                "essential": key in CURATED,
                "default": _json(default),
            })
        sections.append({"block": block, "title": title, "label": label,
                         "fields": fields})
    return sections


def block_classes() -> dict[str, str]:
    """``{class name in lower case: block}``. The messages of the loader name a class
    (``MeshConfig: unknown config keys``), and a user can act on a block."""
    cfg, written = _default_case()
    out = {}
    for block in written:
        owner = getattr(cfg, block, None)
        if dataclasses.is_dataclass(owner):
            out[type(owner).__name__.lower()] = block
    return out


def field_kinds() -> dict[str, str]:
    """``{dotted key: kind}`` for every setting, which is what a check needs."""
    return {item["key"]: item["kind"] for section in build() for item in section["fields"]}
