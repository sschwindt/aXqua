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

**Pages, frames and what is required** (the user's corrections of 2026-10-16). The
editor shows the settings on :data:`PAGES`: one page per subject, each with one or more
frames (a block, or the part of a block that belongs to the subject: the lattice of the
OpenFOAM model is set on the page *Mesh configuration*). :data:`REQUIRED` names what a
minimal case cannot do without, and :data:`REQUIRED_WHEN` what an option makes
necessary, as conditions the editor evaluates while the form is filled in: the editor
marks both in the purple of the aXqua icon. :data:`SWITCHES` are the pages whose block
exists only when the case asks for it (simulating with OpenFOAM, calibrating).
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

__all__ = ["BLOCKS", "CURATED", "KINDS", "PAGES", "REQUIRED", "REQUIRED_WHEN", "SWITCHES",
           "block_classes", "build", "field_kinds", "pages"]

#: What a value can be, as far as a form is concerned. ``multichoice`` is a list of
#: entries out of a fixed set, which a form shows as check boxes.
KINDS = ("text", "int", "number", "bool", "choice", "multichoice", "file", "folder",
         "vector", "raster", "table", "yaml")

#: The blocks of a case file in the order of the workflow: ``(key, title, docs label)``.
#: The label is the section of the page *aXqua case setup* that explains the block.
BLOCKS: tuple[tuple[str, str, str], ...] = (
    ("project", "Project settings", "case-project"),
    ("geodata", "Geodata", "case-geodata"),
    ("boundaries", "Liquid boundaries", "case-boundaries"),
    ("gain_lose", "Exchange with a porous body", "case-boundaries"),
    ("initialization", "Initial conditions", "case-initialization"),
    ("drying", "Drying of thin films", "case-initialization"),
    ("mesh", "Mesh configuration", "case-mesh"),
    ("friction", "Wall roughness", "case-roughness"),
    ("hydrodynamics", "Hydraulic simulation", "case-hydraulics"),
    ("morphodynamics", "Morphodynamic simulation", "case-morphodynamics"),
    ("fine_sediment", "Fine sediment over a fixed bed", "case-morphodynamics"),
    ("dem_of_difference", "DEM of difference", "case-morphodynamics"),
    ("telemac", "TELEMAC", "case-hydraulics"),
    ("openfoam", "OpenFOAM", "case-hydraulics"),
    ("structures", "Structures", "case-geodata"),
    ("surfaces", "Surfaces from CAD", "case-surfaces"),
    ("ground_truth", "Measurements", "help-calibration"),
    ("calibration", "Calibration and validation", "help-calibration"),
    ("postproc", "Post-processing", "help-postprocessing"),
)

#: Why the drying of thin films matters, shown as the information of its frame.
DRYING_INFO = (
    "On a coarse bed, water thinner than the grain roughness does not flow over the "
    "bed: it stands within the grains and in nature drains into the ground. A 2D "
    "model has no ground below the bed, so such a film stays where it is and makes the "
    "wetted area look much larger than it is. On the Isar case, water shallower than "
    "1 cm covered 13 % of the wetted area and held 0.16 % of the water. Removing thin "
    "films takes away water that is both shallower than the film depth and slower than "
    "the film velocity, and returns it where the porous body gives water back, so that "
    "no water is lost. It needs the exchange with a porous body (page Liquid "
    "boundaries).")

#: How the lattice of the OpenFOAM model follows the mesh of the case.
OPENFOAM_MESH_INFO = (
    "The OpenFOAM model is built on a lattice of columns. With a factor, its cell size "
    "follows the edge length in the channel above: 3 makes the lattice three times "
    "coarser than the TELEMAC mesh, so a change of the TELEMAC mesh carries over. "
    "Without a factor, the cell size is used as it is.")

#: The pages of the editor, in the order of the workflow:
#: ``(key, title, docs label, ((block or block/group, frame title, information), ...))``.
#: ``openfoam/mesh`` is the group *mesh* of the block ``openfoam``, which belongs on
#: the page of the mesh; the rest of the block stays on its own page.
PAGES: tuple[tuple[str, str, str, tuple[tuple[str, str, str], ...]], ...] = (
    ("project", "Project settings", "case-project", (("project", "", ""),)),
    ("geodata", "Geodata", "case-geodata", (("geodata", "", ""),)),
    ("boundaries", "Liquid boundaries", "case-boundaries", (
        ("boundaries", "", ""), ("gain_lose", "Exchange with a porous body", ""))),
    ("initialization", "Initial conditions", "case-initialization", (
        ("initialization", "", ""), ("drying", "Drying of thin films", DRYING_INFO))),
    ("mesh", "Mesh configuration", "case-mesh", (
        ("mesh", "TELEMAC mesh", ""),
        ("openfoam/mesh", "OpenFOAM lattice", OPENFOAM_MESH_INFO))),
    ("friction", "Wall roughness", "case-roughness", (("friction", "", ""),)),
    ("hydrodynamics", "Hydraulic simulation", "case-hydraulics", (
        ("hydrodynamics", "", ""),)),
    ("morphodynamics", "Morphodynamic simulation", "case-morphodynamics", (
        ("morphodynamics", "Sediment transport with TELEMAC and GAIA", ""),
        ("fine_sediment", "Fine sediment over a fixed bed with OpenFOAM", ""),
        ("dem_of_difference", "DEM of difference", ""))),
    ("telemac", "TELEMAC", "case-hydraulics", (("telemac", "", ""),)),
    ("openfoam", "OpenFOAM", "case-hydraulics", (
        ("openfoam", "", ""), ("structures", "Structures", ""),
        ("surfaces", "Surfaces from CAD", ""))),
    ("calibration", "Calibration and validation", "help-calibration", (
        ("ground_truth", "Measurements", ""),
        ("calibration", "Calibration and validation", ""))),
    ("postproc", "Post-processing", "help-postprocessing", (("postproc", "", ""),)),
)

#: Pages whose block exists only when the case asks for it: a switch at the top of the
#: page, and the entry written when it is switched on (an empty block is not written).
SWITCHES: dict[str, dict[str, Any]] = {
    "telemac": {"label": "Simulate with TELEMAC", "seed": {"solver": "telemac2d"}},
    "openfoam": {"label": "Simulate with OpenFOAM", "seed": {"mode": "vof"}},
    "calibration": {"label": "Calibrate and validate this case",
                    "seed": {"calibration_quantities": ["WATER DEPTH",
                                                        "SCALAR VELOCITY"]}},
}

#: The entries a minimal case cannot do without.
REQUIRED = frozenset({
    "project.name", "project.crs_epsg", "geodata.dem_initial", "geodata.boundary",
    "boundaries.liquid_boundaries", "boundaries.prescribed_flowrate",
})

#: Entries an option makes necessary: ``key -> conditions``, of which one suffices. A
#: condition is ``{"key": ..., "in": [values]}`` (an unset entry counts with its
#: default) or ``{"block": ...}`` (the block is switched on).
REQUIRED_WHEN: dict[str, list[dict[str, Any]]] = {
    "boundaries.prescribed_elevation": [{"key": "boundaries.outflow_condition",
                                         "in": ["elevation"]}],
    "boundaries.inflow": [{"key": "hydrodynamics.regime", "in": ["unsteady"]}],
    "gain_lose.zone": [{"key": "gain_lose.enabled", "in": [True]}],
    "geodata.dem_target": [{"key": "dem_of_difference.enabled", "in": [True]}],
    "morphodynamics.sediment_classes": [{"key": "morphodynamics.enabled", "in": [True]}],
    "fine_sediment.grain_size": [{"key": "fine_sediment.enabled", "in": [True]}],
    "fine_sediment.inflow_concentration": [{"key": "fine_sediment.enabled",
                                            "in": [True]}],
    "openfoam.mode": [{"block": "openfoam"}],
    "openfoam.end_time": [{"block": "openfoam"}],
    "ground_truth.sources": [{"block": "calibration"}],
    "calibration.parameters": [{"block": "calibration"}],
    "calibration.calibration_quantities": [{"block": "calibration"}],
}


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
    "hydrodynamics.wet_depth": _c(
        "Minimum water depth", "number", unit="m",
        help="Water shallower than this is not treated as surface flow: on a bed "
             "with a Nikuradse roughness of 0.05-0.5 m, water 5 mm deep stands "
             "inside the grain roughness rather than flowing over it. The reports "
             "of aXqua and the map styles of the plugin both use this value, so "
             "the picture and the report agree."),
    "postproc.velocity_scale_max": _c(
        "Velocity scale maximum", "number", unit="m/s",
        help="Upper end of the velocity color scale in every figure of this case. "
             "Above about 5 m/s a depth-averaged river result is usually a "
             "wetting/drying artifact in a nearly dry cell, and one such node sets "
             "the scale and flattens the whole map. A chute or a fish pass runs "
             "faster and wants a higher value."),
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
    "fine_sediment.enabled": _c("Simulate the fine sediment", "bool"),
    "fine_sediment.grain_size": _c("Grain size", "number", "m"),
    "fine_sediment.fall_velocity": _c(
        "Settling velocity", "number", "m/s",
        help="Leave empty to have it computed from the grain size."),
    "fine_sediment.inflow_concentration": _c(
        "Concentration at the inflow", "text", "kg/m3",
        help="One number for all inflow lines, or one per line, such as "
             "{inlet-1: 0.02, inlet-2: 0}. 20 mg/l are 0.02 kg/m3."),
    "fine_sediment.pick_up": _c(
        "Pick-up from the bed", "choice", choices=("suspended", "bedload", "off"),
        help="suspended and bedload assume a bed that consists of the fine sediment. "
             "off: the bed only takes sediment up."),
    "fine_sediment.iterations": _c("Iterations at most", "int"),
    "fine_sediment.period": _c(
        "Period", "number", "s",
        help="Time over which the computed rates act. Leave empty to get rates only."),
    "fine_sediment.layer_thickness": _c("Thickness of the bed layer", "number", "m"),
    "fine_sediment.porosity": _c("Pore share of the bed layer", "number", "-"),
    "fine_sediment.initial_fraction": _c("Share of fines at the start", "number", "-"),
    "ground_truth.sources": _c("Measurement files", "yaml",
                               help="One entry per file: category, kind and positions."),
    "ground_truth.targets": _c("Calibration target workbook", "table"),
    "calibration.parameters": _c("Calibration parameters", "yaml",
                                 help="One entry per parameter: name, min and max."),
    "calibration.calibration_quantities": _c("Quantities to calibrate against", "yaml"),
    "calibration.init_runs": _c("Simulations before the surrogate model", "int"),
    "calibration.max_runs": _c("Simulations in total", "int"),
    "openfoam.mode": _c("Free surface", "choice", choices=("vof", "rigid-lid")),
    "openfoam.outflow_boundary": _c(
        "Outflow boundary", "choice", choices=("level", "rating-curve"),
        help="level: one water level for the whole run. rating-curve: the level that "
             "the stage-discharge relation gives for the discharge that leaves the "
             "model, at every time step (boundary condition of the BAW)."),
    "openfoam.cell_size_factor": _c(
        "Cell size relative to the channel edge length", "number", "-",
        help="3 makes the lattice three times coarser than the TELEMAC mesh in the "
             "channel. Leave empty to use the cell size below."),
    "openfoam.cell_size": _c("Cell size", "number", "m"),
    "openfoam.n_layers": _c("Layers in the vertical", "int"),
    "openfoam.end_time": _c("Simulated time", "number", "s",
                            help="End of the OpenFOAM run, counted from its start."),
    "openfoam.n_processors": _c("Processor cores", "int"),
    "hydrodynamics.regime": _c(
        "Steady or unsteady", "choice", choices=("steady", "unsteady"),
        help="unsteady runs the hydrograph given on the page Liquid boundaries."),
    "drying.film_infiltration": _c("Remove thin films", "bool"),
    "morphodynamics.sediment_classes": _c(
        "Sediment classes", "yaml",
        help="One entry per grain size class, for example name, diameter (m) and "
             "share of the bed."),
    "postproc.telemac_maps": _c(
        "Maps of a TELEMAC result", "multichoice",
        help="The layers that Load results adds in QGIS."),
    "postproc.map_style": _c(
        "Map style of the water depth", "choice",
        help="Velocity arrows or streamlines that the plugin draws over the water "
             "depth or the velocity magnitude of a loaded TELEMAC result. Empty: plain "
             "depth and velocity maps."),
    "postproc.openfoam_outputs": _c(
        "OpenFOAM results for", "multichoice",
        help="paraview: the file case.foam. visit: the controlDict of the case, which "
             "VisIt opens. vtk: a conversion after every run, which QGIS can read too "
             "and which can double the size of the case."),
    "postproc.scenes": _c(
        "VisIt figures", "multichoice",
        help="The predefined figures of three-dimensional results that axqua postproc "
             "renders with VisIt."),
}

#: Settings whose choices are a constant of :mod:`axqua.config` that the loader checks
#: against, so that the form and the loader cannot disagree.
CONFIG_CHOICES: dict[str, str] = {
    "postproc.telemac_maps": "TELEMAC_MAPS",
    "postproc.map_style": "MAP_STYLES",
    "postproc.openfoam_outputs": "OPENFOAM_OUTPUTS",
    "postproc.scenes": "VISIT_SCENES",
}

#: Fields that stand under a ``# ---- name ----`` heading of their dataclass and still
#: belong on the page of their block: the outlets of the OpenFOAM model are boundary
#: conditions, not settings of its lattice.
OUTSIDE_THEIR_GROUP = frozenset({
    "openfoam.outflow_boundary", "openfoam.outlet_stage", "openfoam.outlet_air_margin",
    "openfoam.liquid_boundaries",
})

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


def _groups(cls: type) -> dict[str, str]:
    """The ``# ---- name ----`` heading each field of a dataclass stands under."""
    try:
        lines = inspect.getsource(cls).splitlines()
    except (OSError, TypeError):
        return {}
    heading = re.compile(r"^\s*#\s*-{3,}\s*(.+?)\s*-{3,}\s*$")
    field_line = re.compile(r"^\s{4}([a-z_][a-z0-9_]*)\s*:")
    out: dict[str, str] = {}
    current = ""
    for line in lines:
        found = heading.match(line)
        if found:
            current = found.group(1).strip().lower()
            continue
        match = field_line.match(line)
        if match and current:
            out[match.group(1)] = current
    return out


def _frames() -> dict[str, tuple[str, str]]:
    """``{block or block/group: (page key, frame title)}``."""
    return {block: (key, title) for key, _t, _l, frames in PAGES
            for block, title, _info in frames}


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
    from axqua import config as config_module

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
        groups = _groups(cls) if cls is not None else {}
        frames = _frames()
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
            group = "" if key in OUTSIDE_THEIR_GROUP else groups.get(name, "")
            page, frame = frames.get(f"{block}/{group}", frames.get(block, (block, "")))
            required = key in REQUIRED
            when = [dict(condition) for condition in REQUIRED_WHEN.get(key, ())]
            fields.append({
                "key": key, "block": block, "name": name,
                "label": curated.get("label") or _label(name),
                "kind": kind,
                "unit": curated.get("unit", ""),
                "help": curated.get("help") or comments.get(name, ""),
                "choices": list(curated.get("choices")
                                or getattr(config_module, CONFIG_CHOICES.get(key, ""), ())
                                or CHOICES.get(key, ())),
                # a required entry is always on the form
                "essential": key in CURATED or required or bool(when),
                "default": _json(default),
                "required": required,
                "required_when": when,
                "page": page,
                "frame": frame,
            })
        sections.append({"block": block, "title": title, "label": label,
                         "fields": fields})
    return sections


def pages(sections: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The pages of the editor with their frames and, in each, the keys it shows.

    *sections* is what :func:`build` returned, for a caller that has it already.
    Every setting of :func:`build` is on exactly one frame; a block that no page names
    gets a page of its own at the end.
    """
    sections = build() if sections is None else sections
    fields = [item for section in sections for item in section["fields"]]
    out = []
    known = set()
    for key, title, label, frames in PAGES:
        page = {"key": key, "title": title, "label": label,
                "switch": dict(SWITCHES.get(key, {})), "frames": []}
        for _block, frame_title, info in frames:
            keys = [item["key"] for item in fields
                    if item["page"] == key and item["frame"] == frame_title]
            if keys:
                page["frames"].append({"title": frame_title, "info": info, "keys": keys})
                known.update(keys)
        out.append(page)
    for section in sections:
        rest = [item["key"] for item in section["fields"] if item["key"] not in known]
        if rest:
            out.append({"key": section["block"], "title": section["title"],
                        "label": section["label"], "switch": {},
                        "frames": [{"title": "", "info": "", "keys": rest}]})
    return out


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
