"""Fine sediment over a bed that stays in place, with Olsen's ``sediDriftFoam2``.

What is computed
----------------
``sediDriftFoam2`` (Nils Reidar B. Olsen, NTNU) is ``simpleFoam`` with one more
equation: the concentration of one grain size, which is carried with the flow, settles
with its fall velocity and is exchanged with the bed. The flow is steady and has one
phase under a fixed water surface, so the case is built on the mesh of
``openfoam.mode: rigid-lid`` whatever mode the free-surface case of the reach uses.

The solver can also move the bed and the water surface. aXqua does not use that: the
number of iterations before the first bed change (``initialIterations``) is set beyond
the end of the run. Three rules in the source of the solver are the reason, and each of
them would change a natural reach in a way that has nothing to do with sediment. A bed
point is lowered until the water is ten bottom cells deep. The points of a vertical are
spread evenly after every bed change, which undoes the bottom layer described below.
And the inflow and the last meter before the largest x coordinate are treated as the
ends of a laboratory flume.

What comes out is therefore the sediment budget of a bed that stays where it is: the
concentration at the bed, the rate at which grains settle onto it, the rate at which
the flow picks them up, and the difference of the two, per bed area. Over a period that
is a mass of fine sediment the bed takes up or gives off, and for a bed layer of given
thickness a change of its share of fines (:func:`evaluate`).

What the solver requires of a mesh, and how it is checked
----------------------------------------------------------
The requirements are read from ``sediDriftFoam2.C`` and verified on the mesh that is
written (:func:`check_columns`), because the solver itself checks none of them:

* patches named ``bedWall`` and ``freeSurface`` whose face *k* belong to the same
  column of hexahedra;
* bed faces with one vertex order throughout, so that the solver finds the neighbours
  of a bed face by comparing vertex numbers;
* **a positive height of the first cell center above the bed face center**, in the
  solver's own definition ``mesh.C().z() - patch.Cf().z()``. It enters a logarithm and
  ends the run with a floating point exception where it is zero or negative. On a
  terrain-following mesh that happens wherever neighboring verticals have layers of
  different thickness on a sloping bed, since the cell center then lies towards the
  thick side, which is the low side. The layer at the bed is therefore given **one
  thickness everywhere** (:func:`pin_bottom_layer`). A steep and twisted bed face
  still shortens the distance, so it is computed for every face with OpenFOAM's own
  definitions of the two centers (:mod:`axqua.solvers.openfoam.quality`) and a mesh
  with a face at or below the roughness limit of the logarithm is refused.

How deposition and pick-up are booked
-------------------------------------
Read from the source as well, because the budget in :func:`evaluate` has to be the one
the solver solves. Grains leave the water through the convection term of the bed cell
(the ``bounded`` scheme turns the inflow of the fall velocity into a sink), which makes
the settling rate *fall velocity x concentration in the bed cell x plan area*. Pick-up
is a volume source in the same cell, *fall velocity x reference concentration / (2 x
height of the cell center) x cell volume*. Both are evaluated here per plan area of the
column, and the difference of what enters and what leaves the reach is compared with
their sum over the bed.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from axqua.core import errors

log = logging.getLogger("axqua")

SOLVER = "sediDriftFoam2"
BED_PATCH = "bedWall"
SURFACE_PATCH = "freeSurface"

#: Density of the grains [kg/m3]. Not a setting: the formulas of the solver carry the
#: submerged density 1650 kg/m3 and the density of water 1000 kg/m3 as numbers.
GRAIN_DENSITY = 2650.0
WATER_DENSITY = 1000.0
#: Kinematic viscosity of water [m2/s], which the formulas of the solver assume.
VISCOSITY = 1.0e-6
GRAVITY = 9.81
KAPPA = 0.4
#: ``(1.65 g / nu^2)^(1/3)`` [1/m] as the solver writes it: the grain size times this
#: number is van Rijn's particle parameter.
PARTICLE_PARAMETER = 25296.0

#: Tangent of the angle of repose in Dey's correction of the critical shear stress on
#: a sloping bed, the smallest factor of that correction, and the cosine used by the
#: sand slide. The values of Olsen's own example; the sand slide never runs here.
TAN_REPOSE = 0.7
COS_REPOSE = 0.839
MIN_DAMPING = 0.1
#: An ``int`` of the solver: no bed change before this iteration, so none at all.
NEVER = 2_000_000_000

#: The argument of the solver's logarithm, 30 x height / roughness. At 1 the shear
#: velocity is infinite; below e it exceeds 0.4 times the velocity it is derived from.
LOG_LIMIT = 1.0
LOG_WEAK = math.e

BUILD_RECORD = "axqua-sediment.json"
BED_FILE = "axqua-bed.npz"
BED_LAYER_NAME = "fine-sediment-bed.gpkg"
BUDGET_NAME = "fine-sediment-budget.json"
LOG_NAME = "log.sediDriftFoam2"
SECONDS_PER_DAY = 86400.0


# --------------------------------------------------------------------------- #
# the sediment
# --------------------------------------------------------------------------- #


def fall_velocity(grain_size: float) -> float:
    """Settling velocity of a natural grain in still water [m/s].

    After Ferguson and Church (2004), with their constants for natural sand of sieve
    diameter (18 and 1.0): one formula that is Stokes' law for silt and the square-root
    law for coarse sand, and rises with the grain size throughout. The three formulas
    of van Rijn (1984), which go with the transport formulas of the solver, were tried
    first and not kept: where Stokes' law hands over at 0.1 mm they step down by 13 %,
    so that a slightly coarser grain would settle more slowly. The two agree within
    10 % over the range, and both give the 0.2 m/s that Olsen's example states for
    2 mm.
    """
    d = float(grain_size)
    relative = GRAIN_DENSITY / WATER_DENSITY - 1.0
    return relative * GRAVITY * d * d / (
        18.0 * VISCOSITY + math.sqrt(0.75 * relative * GRAVITY * d ** 3))


def settling_velocity(cfg) -> float:
    """The fall velocity of the case [m/s], given or computed."""
    given = cfg.fine_sediment.fall_velocity
    return float(given) if given is not None else fall_velocity(cfg.fine_sediment.grain_size)


def inflow_concentrations(cfg, inlets: list[str]) -> dict[str, float]:
    """Concentration by mass [kg/m3] at every inlet patch.

    One number serves all of them. A mapping names the lines, as ``inlet-1`` or as the
    number alone, and has to name each of them: a line left out would get clear water
    without anybody having said so.
    """
    given = cfg.fine_sediment.inflow_concentration
    if not isinstance(given, dict):
        return {name: float(given) for name in inlets}
    by_name = {}
    for key, value in given.items():
        text = str(key).strip()
        by_name[text if not text.isdigit() else f"inlet-{text}"] = float(value)
    unknown = sorted(set(by_name) - set(inlets))
    missing = sorted(set(inlets) - set(by_name))
    if unknown or missing:
        parts = []
        if unknown:
            parts.append("no inflow line is called " + ", ".join(unknown))
        if missing:
            parts.append("no value for " + ", ".join(missing))
        raise errors.ConfigError(
            "fine_sediment.inflow_concentration does not fit the inflow lines of the "
            "model: " + "; ".join(parts),
            subject="fine_sediment.inflow_concentration",
            remedy="The inflow lines of this model are " + ", ".join(inlets)
                   + ". Give one number for all of them, or one for each.")
    return {name: by_name[name] for name in inlets}


# --------------------------------------------------------------------------- #
# the mesh
# --------------------------------------------------------------------------- #


def sediment_config(cfg):
    """A copy of *cfg* whose OpenFOAM mesh is the one this solver needs.

    Water only under a lid on the surface of the 2D result, whatever the free-surface
    case of the reach is set to, and judged by the rules of the rigid lid.
    """
    out = copy.deepcopy(cfg)
    out.openfoam.mode = "rigid-lid"
    out.openfoam.solver = "interFoam"      # not the free-surface solver that is exempt
    return out                             # from the lid test


def pin_bottom_layer(of_mesh, thickness: float) -> float:
    """Give the layer at the bed one thickness at every vertical; returns it.

    Changes the levels of the points and nothing else. The lattice, the faces and the
    numbering stay as they were built, which is why the solver's rules about them,
    verified for the mesher, still hold afterwards.
    """
    from axqua.solvers.openfoam.mesh import sigma_levels

    z = np.asarray(of_mesh.z, dtype=float)
    height = z[:, -1] - z[:, 0]
    limit = 0.5 * float(height.min())
    if thickness > limit + 1e-9:
        raise errors.ConfigError(
            f"a bottom layer of {thickness:g} m does not fit: the shallowest water "
            f"column of the model is {float(height.min()):.3f} m deep, and the layer "
            "can take half of it at most",
            subject="fine_sediment.bottom_layer",
            remedy=f"Set fine_sediment.bottom_layer to {limit:.3f} m or less, or leave "
                   "it empty.")
    levels = sigma_levels(height, of_mesh.n_layers, bed_layer=float(thickness))
    new = z[:, :1] + levels * height[:, None]
    new[:, -1] = z[:, -1]
    points = of_mesh.polymesh.points
    # point = vertex * (n_layers + 1) + level: the one rule of the mesher's numbering
    if points.shape[0] != new.size or not np.allclose(
            points[:, 2].reshape(new.shape), z, atol=1e-9):
        raise errors.MeshError(
            "the points of the mesh are not numbered along its verticals",
            subject="openfoam", remedy="This is a defect of aXqua; please report it.")
    points[:, 2] = new.ravel()
    of_mesh.z = new
    return float(thickness)


def rename_patches(polymesh) -> None:
    """``bed`` and ``lid`` under the names the solver looks up."""
    from axqua.solvers.openfoam.mesh import BED_PATCH as BED, LID_PATCH as LID

    bed, lid = polymesh.patch(BED), polymesh.patch(LID)
    bed.name = BED_PATCH
    lid.name = SURFACE_PATCH
    lid.type = "patch"                     # as in Olsen's cases: no wall functions on it
    lid.in_groups = ()


def bed_neighbours(bed_faces: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray,
                                                   np.ndarray, np.ndarray]:
    """``(east, west, north, south, count)`` of every bed face, as the solver finds them.

    ``sediDriftFoam2.C`` compares vertex numbers: the east neighbour of a face has its
    vertices 0 and 1 where the face has 3 and 2, the north neighbour its vertices 0
    and 3 where the face has 1 and 2. A face without a neighbour keeps 0 there, as the
    arrays of the solver do, and ``count`` says how many it has.
    """
    faces = np.asarray(bed_faces)
    n = faces.shape[0]
    east = np.zeros(n, dtype=np.int64)
    west = np.zeros(n, dtype=np.int64)
    north = np.zeros(n, dtype=np.int64)
    south = np.zeros(n, dtype=np.int64)
    count = np.zeros(n, dtype=np.int64)
    by_01 = {(int(a), int(b)): i for i, (a, b) in enumerate(faces[:, [0, 1]])}
    by_03 = {(int(a), int(b)): i for i, (a, b) in enumerate(faces[:, [0, 3]])}
    for i, face in enumerate(faces):
        j = by_01.get((int(face[3]), int(face[2])))
        if j is not None:
            east[i], west[j] = j, i
            count[i] += 1
            count[j] += 1
        j = by_03.get((int(face[1]), int(face[2])))
        if j is not None:
            north[i], south[j] = j, i
            count[i] += 1
            count[j] += 1
    return east, west, north, south, count


def bed_vectors(points: np.ndarray, bed_faces: np.ndarray, centres: np.ndarray,
                neighbours) -> np.ndarray:
    """Unit vector normal to the bed at every bed face, as the solver computes it.

    From the centers of the four neighboring faces where a face has all four
    (``computevx3.h``), from its own corners otherwise (``computevx4.h``).
    """
    east, west, north, south, count = neighbours
    a = centres[north] - centres[south]
    b = centres[west] - centres[east]
    from_neighbours = np.cross(a, b)
    corners = points[bed_faces]
    from_corners = np.cross(corners[:, 2] - corners[:, 0], corners[:, 1] - corners[:, 3])
    vector = np.where((count == 4)[:, None], from_neighbours, from_corners)
    length = np.linalg.norm(vector, axis=1)
    return vector / np.where(length > 0, length, 1.0)[:, None]


@dataclass
class BedGeometry:
    """What the solver sees of the bed, per bed face in the order of the patch."""

    cells: np.ndarray            # the cell on each bed face
    corners: np.ndarray          # (n, 4, 2) plan corners of the column
    centre: np.ndarray           # (n, 3) center of the bed face
    plan_area: np.ndarray        # horizontal area of the column [m2]
    height: np.ndarray           # cell center above bed face center, vertically [m]
    volume: np.ndarray           # volume of the bed cell [m3]
    vector: np.ndarray           # (n, 3) unit normal of the bed, pointing up
    depth: np.ndarray            # water depth of the column [m]
    roughness_zone: np.ndarray   # roughness of the zone the column lies in [m]
    outlets: dict[str, np.ndarray] = field(default_factory=dict)   # patch -> cells
    inlets: dict[str, float] = field(default_factory=dict)         # patch -> m3/s

    @property
    def n(self) -> int:
        return int(self.cells.size)

    def save(self, path: Path) -> Path:
        arrays = {name: getattr(self, name) for name in (
            "cells", "corners", "centre", "plan_area", "height", "volume", "vector",
            "depth", "roughness_zone")}
        for name, cells in self.outlets.items():
            arrays[f"outlet:{name}"] = cells
        arrays["inlet_names"] = np.array(list(self.inlets), dtype=str)
        arrays["inlet_discharge"] = np.array(list(self.inlets.values()), dtype=float)
        np.savez_compressed(path, **arrays)
        return path

    @classmethod
    def load(cls, path: Path) -> "BedGeometry":
        with np.load(path) as data:
            outlets = {key.split(":", 1)[1]: data[key] for key in data.files
                       if key.startswith("outlet:")}
            inlets = {str(name): float(q) for name, q in
                      zip(data["inlet_names"], data["inlet_discharge"])}
            return cls(**{name: data[name] for name in (
                "cells", "corners", "centre", "plan_area", "height", "volume", "vector",
                "depth", "roughness_zone")}, outlets=outlets, inlets=inlets)


def check_columns(of_mesh) -> BedGeometry:
    """Verify what the solver assumes about the mesh; return what it sees of the bed.

    Raises :class:`~axqua.core.errors.MeshError` for a mesh the solver would compute
    nonsense on without saying so. The height of the first cell center is left to
    :func:`check_heights`, which knows the roughness.
    """
    from axqua.solvers.openfoam import quality

    mesh = of_mesh.polymesh
    bed_ids = mesh.patch_face_ids(BED_PATCH)
    top_ids = mesh.patch_face_ids(SURFACE_PATCH)
    if bed_ids.size != top_ids.size:
        raise errors.MeshError(
            f"the bed has {bed_ids.size} faces and the water surface {top_ids.size}; "
            "the solver takes face k of both for one column",
            subject="openfoam", remedy="This is a defect of aXqua; please report it.")
    bed_faces = mesh.faces[bed_ids]
    top_faces = mesh.faces[top_ids]
    layers = int(of_mesh.n_layers)
    if not np.array_equal(np.sort(top_faces, axis=1), np.sort(bed_faces, axis=1) + layers):
        raise errors.MeshError(
            "bed face k and surface face k do not stand over each other",
            subject="openfoam", remedy="This is a defect of aXqua; please report it.")
    columns = mesh.points[:, 2].reshape(-1, layers + 1)
    if not (np.diff(columns, axis=1) > 0).all():
        raise errors.MeshError(
            "the points of a vertical do not rise from the bed to the water surface",
            subject="openfoam", remedy="This is a defect of aXqua; please report it.")

    neighbours = bed_neighbours(bed_faces)
    shared: dict[frozenset, int] = {}
    for face in bed_faces:
        for k in range(4):
            edge = frozenset((int(face[k]), int(face[(k + 1) % 4])))
            shared[edge] = shared.get(edge, 0) + 1
    edges = sum(1 for n in shared.values() if n == 2)
    found = int(neighbours[4].sum() // 2)
    if found != edges:
        raise errors.MeshError(
            f"the solver's search finds {found} neighbours among the bed faces where "
            f"{edges} pairs of them share an edge: their vertices are not in one order",
            subject="openfoam", remedy="This is a defect of aXqua; please report it.")

    face_centres, face_vectors = quality.face_geometry(mesh)
    cell_centres, volumes = quality.cell_geometry(mesh, face_centres, face_vectors)
    cells = mesh.owner[bed_ids]
    centre = face_centres[bed_ids]
    vector = bed_vectors(mesh.points, bed_faces, centre, neighbours)
    if (vector[:, 2] <= 0).any():
        raise errors.MeshError(
            "the bed faces are not ordered so that their normal points up",
            subject="openfoam", remedy="This is a defect of aXqua; please report it.")
    ks = of_mesh.bed_ks
    geometry = BedGeometry(
        cells=cells,
        corners=mesh.points[bed_faces][:, :, :2].copy(),
        centre=centre,
        plan_area=np.abs(face_vectors[bed_ids, 2]),
        height=cell_centres[cells, 2] - centre[:, 2],
        volume=volumes[cells],
        vector=vector,
        depth=face_centres[top_ids, 2] - centre[:, 2],
        roughness_zone=(np.asarray(ks, dtype=float) if ks is not None
                        else np.full(cells.size, np.nan)),
    )
    for name in of_mesh.outlet_patches:
        geometry.outlets[name] = mesh.owner[mesh.patch_face_ids(name)]
    return geometry


def required_bottom_layer(geometry: BedGeometry, roughness: float, bottom: float) -> float:
    """Thickness of the bottom layer [m] with which every bed face suits the solver.

    The height of the first cell center above a bed face is half the bottom layer plus
    an offset of that face, which does not depend on the layer: the cell is the bed
    face moved up, so its center lies over the middle of the face *in plan*, while the
    center of the face lies towards its steeper part. On a steep and twisted face the
    offset is negative (-0.026 m on the worst face of the Isar example). This returns
    the layer with which the worst face stands clear of the solver's logarithm by the
    factor e, at which the shear velocity it computes is 0.4 times the flow velocity.
    """
    offset = float((geometry.height - 0.5 * bottom).min())
    return 2.0 * (roughness * LOG_WEAK / 30.0 - offset)


def check_heights(geometry: BedGeometry, roughness: float, bottom: float, *,
                  largest: float | None = None) -> list[str]:
    """Refuse a mesh on which the solver's logarithm fails; return what to note.

    The solver computes the shear velocity as ``0.4 U / ln(30 h / ks)`` with ``h`` the
    height of the cell center above the bed face center. *largest* is the thickest
    bottom layer the mesh allows, which decides what the refusal advises.
    """
    argument = 30.0 * geometry.height / roughness
    failing = int((argument <= LOG_LIMIT).sum())
    if failing:
        worst = float(geometry.height.min())
        needed = required_bottom_layer(geometry, roughness, bottom)
        if largest is not None and needed <= largest:
            remedy = (f"Set fine_sediment.bottom_layer to {needed:.3f} m or more (up to "
                      f"{largest:.3f} m), or leave it empty and aXqua chooses it.")
        else:
            remedy = ("Use smaller cells (openfoam.cell_size or cell_size_factor), so "
                      "that the bed rises less within one cell"
                      + (f": a bottom layer of {needed:.3f} m would be needed here, and "
                         f"the shallowest water column allows {largest:.3f} m."
                         if largest is not None else "."))
        raise errors.MeshError(
            f"on {failing} of {geometry.n} bed faces the first cell center is "
            f"{'below' if worst <= 0 else 'too close to'} the bed for the solver "
            f"(lowest {worst:+.4f} m, where the roughness of {roughness:g} m needs "
            f"more than {roughness / 30.0:.4f} m). It computes the bed shear stress "
            "from a logarithm of that height and ends with a floating point "
            "exception, or with a shear stress without meaning",
            subject="fine_sediment.bottom_layer", remedy=remedy,
            failing_faces=failing, lowest_height=worst)
    notes = []
    # not the face a chosen bottom layer has just put ON that limit
    weak = int((argument < LOG_WEAK * (1.0 - 1e-6)).sum())
    if weak:
        notes.append(
            f"on {weak} of {geometry.n} bed faces ({100.0 * weak / geometry.n:.1f} %) "
            f"the first cell center stands less than {roughness * LOG_WEAK / 30.0:.3f} "
            f"m above the bed, where the bottom layer of {bottom:g} m gives "
            f"{0.5 * bottom:.3f} m on a flat face: these faces are steep within one "
            "cell, and the shear stress the solver computes on them is too high")
    return notes


# --------------------------------------------------------------------------- #
# the case
# --------------------------------------------------------------------------- #


@dataclass
class SedimentArtifacts:
    """What the build of the fine sediment case produced."""

    case_dir: Path
    mesh: object = None                   # openfoam.mesh.OpenFoamMesh
    geometry: BedGeometry | None = None
    record: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        r = self.record
        out = [f"fine sediment case built in {self.case_dir}"]
        out.append(f"  cells       : {r.get('n_cells')} in {r.get('n_layers')} layers, "
                   f"bottom layer {r.get('bottom_layer', 0):.3f} m")
        out.append(f"  sediment    : {1000.0 * r.get('grain_size', 0):g} mm, settling "
                   f"at {1000.0 * r.get('fall_velocity', 0):.2f} mm/s, pick-up "
                   f"{r.get('pick_up')}")
        inflow = ", ".join(f"{name} {q:g} m3/s with {c:g} kg/m3" for name, (q, c) in
                           (r.get("inflow") or {}).items())
        out.append(f"  inflow      : {inflow}")
        out.append(f"  roughness   : {r.get('roughness', 0):g} m for the shear stress "
                   "on the grains")
        for note in list(self.record.get("mesh_notes") or []) + self.notes:
            out.append(f"  note        : {note}")
        return out


def _field(cls: str, name: str, dimensions: str, internal: str,
           boundary: dict[str, dict[str, str]]) -> str:
    from axqua.solvers.openfoam.fields import render_field

    return render_field(cls, name, dimensions, internal, boundary)


def write_fields(of_mesh, cfg, case_dir: Path, *, state, discharges: dict[str, float],
                 concentrations: dict[str, float], roughness: float) -> list[Path]:
    """Write the nine fields of ``0/``.

    *concentrations* are by mass [kg/m3]; the solver carries the share of the volume.
    The flow starts from the velocity of the 2D result, the same in all layers, and the
    concentration from the mean of what enters.

    The bed has **one** roughness, *roughness*, in the wall function of the flow as in
    the shear stress on the grains. The free-surface case gives every bed face the
    roughness of its zone, and that does not work here: on the Isar the zones reach
    0.5 m, ten times the bottom layer, and with them the residuals of the pressure,
    the vertical velocity and the turbulence stay at 5e-3 from the fiftieth iteration
    on, where one roughness lets them fall below 1e-5 within 400 iterations.
    """
    from axqua.solvers.openfoam import fields as of_fields

    zero = Path(case_dir) / "0"
    zero.mkdir(parents=True, exist_ok=True)
    inlets, outlets = list(of_mesh.inlet_patches), list(of_mesh.outlet_patches)
    walls = [p.name for p in of_mesh.polymesh.patches if p.type == "wall"]
    k, epsilon, _omega = of_fields.turbulence_scales(state, cfg)
    velocity = of_fields.initial_velocity(of_mesh, np.ones(of_mesh.n_cells), state)
    total = sum(discharges.values()) or 1.0
    mean = sum(discharges[p] * concentrations[p] for p in inlets) / total / GRAIN_DENSITY

    def table(inlet, outlet, wall, surface, bed=None) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for patch in inlets:
            out[patch] = inlet(patch) if callable(inlet) else dict(inlet)
        for patch in outlets:
            out[patch] = dict(outlet)
        for patch in walls:
            entry = bed if (patch == BED_PATCH and bed is not None) else wall
            out[patch] = entry(patch) if callable(entry) else dict(entry)
        out[SURFACE_PATCH] = dict(surface)
        return out

    gradient = {"type": "zeroGradient"}
    still = {"type": "fixedValue", "value": "uniform (0 0 0)"}
    files = {
        "U": _field("volVectorField", "U", "[0 1 -1 0 0 0 0]",
                    of_fields.vector_list(velocity), table(
            lambda p: {"type": "flowRateInletVelocity",
                       "volumetricFlowRate": f"constant {discharges[p]:.8g}",
                       "value": "uniform (0 0 0)"},
            {"type": "inletOutlet", "inletValue": "uniform (0 0 0)",
             "value": "uniform (0 0 0)"},
            {"type": "noSlip"}, {"type": "slip"})),
        # The solver sets the fall velocity in every cell at every iteration; what it
        # reads here are the boundaries, and a bed that is no boundary for the fall
        # velocity is what makes the settling a sink of the bed cell.
        "Umod": _field("volVectorField", "Umod", "[0 1 -1 0 0 0 0]", "uniform (0 0 0)",
                       table(still, still, still, {"type": "slip"})),
        "p": _field("volScalarField", "p", "[0 2 -2 0 0 0 0]", "uniform 0", table(
            gradient, {"type": "fixedValue", "value": "uniform 0"}, gradient, gradient)),
        "k": _field("volScalarField", "k", "[0 2 -2 0 0 0 0]", f"uniform {k:.6g}", table(
            {"type": "fixedValue", "value": f"uniform {k:.6g}"},
            {"type": "inletOutlet", "inletValue": f"uniform {k:.6g}",
             "value": f"uniform {k:.6g}"},
            {"type": "kqRWallFunction", "value": f"uniform {k:.6g}"}, gradient)),
        "epsilon": _field("volScalarField", "epsilon", "[0 2 -3 0 0 0 0]",
                          f"uniform {epsilon:.6g}", table(
            {"type": "fixedValue", "value": f"uniform {epsilon:.6g}"},
            {"type": "inletOutlet", "inletValue": f"uniform {epsilon:.6g}",
             "value": f"uniform {epsilon:.6g}"},
            {"type": "epsilonWallFunction", "value": f"uniform {epsilon:.6g}"},
            gradient)),
        "nut": _field("volScalarField", "nut", "[0 2 -1 0 0 0 0]", "uniform 0", table(
            {"type": "calculated", "value": "uniform 0"},
            {"type": "calculated", "value": "uniform 0"},
            lambda p: of_fields._wall_entries(of_mesh, cfg, p),
            {"type": "calculated", "value": "uniform 0"},
            bed={"type": "nutkRoughWallFunction", "Ks": f"uniform {roughness:.6g}",
                 "Cs": f"uniform {cfg.openfoam.roughness_constant:g}",
                 "value": "uniform 0"})),
        "Conc": _field("volScalarField", "Conc", "[0 0 0 0 0 0 0]",
                       f"uniform {mean:.8g}", table(
            lambda p: {"type": "fixedValue",
                       "value": f"uniform {concentrations[p] / GRAIN_DENSITY:.8g}"},
            {"type": "inletOutlet", "inletValue": "uniform 0", "value": "uniform 0"},
            gradient, gradient)),
    }
    for name in ("S_explicit", "S_implicit"):
        files[name] = _field("volScalarField", name, "[0 0 -1 0 0 0 0]", "uniform 0",
                             table(gradient, gradient, gradient, gradient))
    written = []
    for name, text in files.items():
        path = zero / name
        path.write_text(text)
        written.append(path)
    return written


_SCHEMES = """ddtSchemes
{
    // No time derivative: the flow is steady, and so is the concentration that goes
    // with it. Olsen's example marches the concentration in time, which the moving
    // bed needs and a fixed bed does not.
    default         steadyState;
}

gradSchemes
{
    default         Gauss linear;
}

divSchemes
{
    default         none;
    div(phi,U)      bounded Gauss linearUpwind grad(U);
    div(phi,k)      bounded Gauss upwind;
    div(phi,epsilon) bounded Gauss upwind;
    // `bounded` is part of the model here and not a matter of taste: it is what turns
    // the fall velocity arriving in the bed cell into the settling onto the bed.
    div(phi_d,Conc) bounded Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}

laplacianSchemes
{
    // limited, as in the free-surface case: the mesh follows the terrain and is
    // non-orthogonal wherever the bed slopes
    default         Gauss linear limited corrected 0.33;
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         limited corrected 0.33;
}

wallDist
{
    method          meshWave;
}
"""

_SOLUTION = """solvers
{{
    p
    {{
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-07;
        relTol          0.1;
    }}

    "(U|k|epsilon)"
    {{
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-07;
        relTol          0.1;
    }}

    Conc
    {{
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-09;
        relTol          0.01;
    }}
}}

SIMPLE
{{
    nNonOrthogonalCorrectors 1;
    consistent      yes;

    // The run ends here, or at the last iteration of controlDict.
    residualControl
    {{
        p               {residual:g};
        U               {residual:g};
        "(k|epsilon|Conc)" {residual:g};
    }}
}}

// The pressure is not relaxed: `consistent` selects SIMPLEC, which does not need it.
// The concentration is not relaxed either, since the solver solves its equation as
// it stands.
relaxationFactors
{{
    equations
    {{
        U               0.8;
        ".*"            0.8;
    }}
}}
"""

#: Initial residual of every equation below which the run counts as converged.
RESIDUAL = 1.0e-5


def sediment_properties(cfg, *, roughness: float, velocity: float) -> str:
    """``constant/sedimentProperties``.

    The first block is the sediment of the case. The second keeps the bed and the
    water surface where they are; the third is read by the solver and has no effect
    without a bed change.
    """
    from axqua.solvers.openfoam.dicts import _dict_file

    formula = {"suspended": 0, "bedload": 1, "off": 2}[cfg.fine_sediment.pick_up]
    body = f"""particleSize    {float(cfg.fine_sediment.grain_size):.8g};   // [m]
fallVelocity    {-abs(velocity):.8g};   // [m/s], downwards
roughness       {roughness:.8g};   // [m] for the shear stress on the grains
// 0: reference concentration of van Rijn, 1: his bed load formula. The solver knows
// these two, and with any other number it picks nothing up.
sedimentFormula {formula};
minDampingFactor {MIN_DAMPING:g};   // smallest factor on the critical shear stress
tanAngleRepose  {TAN_REPOSE:g};

// The bed and the water surface stay in place: no bed change before this iteration.
initialIterations {NEVER};
waterRelax      0;
waterSmoother   0;

// Read by the solver, without effect on a bed that does not move.
timeStepSediments 0;
conversionCoefficient 1;
cosAngleRepose  {COS_REPOSE:g};
uphillMultiplier 0;
flagDistBed     0;
loopSlideMax    0;
variableTimeStepNumber 3;
"""
    return _dict_file("sedimentProperties", body, location="constant")


def write_dicts(cfg, case_dir: Path, *, roughness: float, velocity: float,
                iterations: int) -> list[Path]:
    from axqua.solvers.openfoam.dicts import _dict_file

    case_dir = Path(case_dir)
    system, constant = case_dir / "system", case_dir / "constant"
    system.mkdir(parents=True, exist_ok=True)
    constant.mkdir(parents=True, exist_ok=True)
    interval = max(1, math.ceil(iterations / 4))
    control = f"""application     {SOLVER};

startFrom       startTime;
startTime       0;
stopAt          endTime;
// One unit of time is one iteration.
endTime         {int(iterations)};
deltaT          1;

writeControl    timeStep;
writeInterval   {interval};
purgeWrite      1;
writeFormat     ascii;
writePrecision  10;
writeCompression off;
timeFormat      general;
timePrecision   8;
runTimeModifiable no;
"""
    transport = f"""transportModel  Newtonian;
nu              {VISCOSITY:g};
"""
    turbulence = """simulationType  RAS;

RAS
{
    RASModel        kEpsilon;
    turbulence      on;
    printCoeffs     off;
}
"""
    files = {
        system / "controlDict": _dict_file("controlDict", control, location="system"),
        system / "fvSchemes": _dict_file("fvSchemes", _SCHEMES, location="system"),
        system / "fvSolution": _dict_file(
            "fvSolution", _SOLUTION.format(residual=RESIDUAL), location="system"),
        constant / "transportProperties": _dict_file(
            "transportProperties", transport, location="constant"),
        constant / "turbulenceProperties": _dict_file(
            "turbulenceProperties", turbulence, location="constant"),
        constant / "sedimentProperties": sediment_properties(
            cfg, roughness=roughness, velocity=velocity),
    }
    for path, text in files.items():
        path.write_text(text)
    return list(files)


def build_case(cfg, *, state=None, hotstart: str | Path | None = None,
               case_dir: str | Path | None = None,
               discharge: float | None = None) -> SedimentArtifacts:
    """Build the fine sediment case of *cfg* from the 2D result of the reach.

    *state* is the 2D result as the OpenFOAM mesher reads it; without one the case's
    own steady result is read, and without that there is no water surface to put the
    lid on and the build says so.
    """
    from axqua.core.capabilities import refresh_markers
    from axqua.solvers.openfoam import mesh as ofmesh
    from axqua.solvers.openfoam.case import _inlet_discharges
    from axqua.solvers.openfoam.hotstart import load_hotstart
    from axqua.solvers.openfoam.polymesh import validate, write_polymesh
    from axqua.workflow import resolve_discharge

    fine = cfg.fine_sediment
    if not fine.enabled:
        raise errors.ConfigError(
            "this case does not ask for a fine sediment simulation",
            subject="fine_sediment.enabled",
            remedy="Set fine_sediment.enabled in the case file, with the grain size "
                   "and the concentration at the inflow.")
    fine.validate()
    work = sediment_config(cfg)
    work.openfoam.validate()
    case_dir = Path(case_dir) if case_dir else cfg.fine_sediment_case_dir
    if state is None:
        state = load_hotstart(work, hotstart)
    if state is None:
        raise errors.ConfigError(
            "the fine sediment simulation needs the steady 2D result of the reach: its "
            "water surface is the upper end of the model",
            subject="fine_sediment",
            remedy="Run the steady 2D simulation of the case first.")
    try:
        # The discharge alone: under a lid the outflow needs no water level, so nothing
        # of the steady boundary conditions of the 2D case has to be prepared here.
        q = float(resolve_discharge(work, discharge))
    except ValueError:
        q = 0.0
    if q <= 0:
        raise errors.ConfigError(
            "no discharge for the fine sediment simulation",
            subject="boundaries.prescribed_flowrate",
            remedy="Set boundaries.prescribed_flowrate in the case file.")

    try:
        of_mesh = ofmesh.build_mesh(work, state=state)
    except ValueError as exc:
        if "RIGID LID DOES NOT APPLY" not in str(exc):
            raise
        # The refusal of the mesher, with what it means for this simulation: the
        # solver has no other upper boundary than a lid.
        raise errors.MeshError(
            str(exc).split("\n")[0],
            subject="openfoam.allow_stepped_lid",
            remedy="sediDriftFoam2 computes one phase under a fixed water surface, so "
                   "there is no other mode to change to. Where the water surface of "
                   "the 2D result steps that much, the velocities under the lid are "
                   "too high at the steps. Set openfoam.allow_stepped_lid to build "
                   "the case all the same; the result then says how far the lid was "
                   "from applying.") from exc
    notes: list[str] = []          # what a reader of the RESULT has to know
    floor = float(work.openfoam.min_water_depth)
    largest = 0.5 * float((of_mesh.z[:, -1] - of_mesh.z[:, 0]).min())
    even = floor / of_mesh.n_layers           # the layers of the shallowest column
    bottom = float(fine.bottom_layer) if fine.bottom_layer is not None \
        else min(even, largest)
    pin_bottom_layer(of_mesh, bottom)
    rename_patches(of_mesh.polymesh)
    problems = validate(of_mesh.polymesh)
    if problems:
        raise errors.MeshError(
            "the mesh of the fine sediment case is not valid: " + "; ".join(problems),
            subject="openfoam", remedy="This is a defect of aXqua; please report it.")

    geometry = check_columns(of_mesh)
    zone = geometry.roughness_zone
    if fine.roughness is not None:
        roughness = float(fine.roughness)
    elif np.isfinite(zone).any():
        roughness = float(np.nanmedian(zone))
    else:
        roughness = float(work.openfoam.friction_ks)
    if fine.bottom_layer is None:
        # Where the case leaves the layer open, it is made as thick as the steepest
        # bed face needs. A case that names a thickness gets that thickness, or the
        # refusal that says which one would do.
        needed = min(required_bottom_layer(geometry, roughness, bottom), largest)
        if needed > bottom + 1e-6:
            notes.append(
                f"the bottom layer is {needed:.3f} m thick and not {bottom:.3f} m, "
                "which the layers of the shallowest water column would give: on the "
                "steepest bed face the first cell center of a thinner layer is too "
                "close to the bed for the solver")
            bottom = pin_bottom_layer(of_mesh, needed)
            geometry = check_columns(of_mesh)
    notes += check_heights(geometry, roughness, bottom, largest=largest)
    if (geometry.centre[:, 0] < 0.5).any():
        # sediDriftFoam2.C: `if(bedCenter[bed].x() < 0.5)` raises the critical shear
        # stress, a rule for the inflow of the flume the solver was written for
        notes.append(
            "part of the bed has an x coordinate below 0.5 m: the solver raises the "
            "critical shear stress there, a rule made for the inflow of a flume. A "
            "projected coordinate system has no such coordinates")

    inlets = _inlet_discharges(of_mesh, work, q)
    geometry.inlets = dict(inlets)
    if not of_mesh.outlet_patches:
        raise errors.ConfigError(
            "the model has no outflow: no outflow line of boundaries.liquid_boundaries "
            "reaches the edge of the wetted area",
            subject="boundaries.liquid_boundaries",
            remedy="Check that the outflow line lies where the 2D result has water.")
    concentrations = inflow_concentrations(cfg, list(inlets))
    velocity = settling_velocity(cfg)

    if case_dir.exists():
        _clear_case(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    write_polymesh(of_mesh.polymesh, case_dir)
    write_fields(of_mesh, work, case_dir, state=state, discharges=inlets,
                 concentrations=concentrations, roughness=roughness)
    write_dicts(cfg, case_dir, roughness=roughness, velocity=velocity,
                iterations=int(fine.iterations))
    geometry.save(case_dir / "constant" / BED_FILE)
    (case_dir / "case.foam").write_text("")

    record = {
        "solver": SOLVER,
        "n_cells": int(of_mesh.n_cells),
        "n_layers": int(of_mesh.n_layers),
        "bed_faces": geometry.n,
        "bottom_layer": bottom,
        "lowest_height": float(geometry.height.min()),
        "grain_size": float(fine.grain_size),
        "fall_velocity": velocity,
        "pick_up": fine.pick_up,
        "roughness": roughness,
        "iterations": int(fine.iterations),
        "discharge": q,
        "inflow": {name: [float(inlets[name]), float(concentrations[name])]
                   for name in inlets},
        "velocity_2d_p95": float(state.velocity_scale()),
        "lid_step_median": _maybe(of_mesh.lid_step_median),
        "lid_step_p99": _maybe(of_mesh.lid_step_p99),
        "crs_epsg": int(cfg.crs_epsg),
        "notes": notes,
        "mesh_notes": list(of_mesh.notes),
    }
    (case_dir / BUILD_RECORD).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    art = SedimentArtifacts(case_dir=case_dir, mesh=of_mesh, geometry=geometry,
                            record=record, notes=notes)
    refresh_markers(cfg)
    for line in art.lines():
        log.info("%s", line)
    return art


def _maybe(value) -> float | None:
    return None if value is None else float(value)


def _clear_case(case_dir: Path) -> None:
    """Remove what an earlier build and run left in the case folder.

    Only what a case of this module consists of: the three folders of an OpenFOAM case,
    its time folders and the files written here. Anything else in the folder stays.
    """
    import shutil

    for entry in case_dir.iterdir():
        numbered = entry.is_dir() and _is_time(entry.name)
        if entry.is_dir() and (numbered or entry.name in ("constant", "system")):
            shutil.rmtree(entry)
        elif entry.is_file() and entry.name in (
                BUILD_RECORD, BED_LAYER_NAME, BUDGET_NAME, LOG_NAME, "case.foam",
                "scour.txt"):
            entry.unlink()


def _is_time(name: str) -> bool:
    try:
        float(name)
    except ValueError:
        return False
    return True


def is_built(case_dir: Path) -> bool:
    case_dir = Path(case_dir)
    return ((case_dir / "constant" / "polyMesh" / "faces").is_file()
            and (case_dir / "constant" / "sedimentProperties").is_file()
            and (case_dir / "constant" / BED_FILE).is_file())


def latest_time(case_dir: Path) -> str | None:
    """Name of the last time folder a run wrote, ``None`` before the first."""
    best: tuple[float, str] | None = None
    for entry in Path(case_dir).iterdir():
        if entry.is_dir() and _is_time(entry.name) and float(entry.name) > 0:
            if (entry / "Conc").is_file() and (best is None or float(entry.name) > best[0]):
                best = (float(entry.name), entry.name)
    return best[1] if best else None


# --------------------------------------------------------------------------- #
# reading a result
# --------------------------------------------------------------------------- #

_INTERNAL = re.compile(r"internalField\s+(uniform|nonuniform)\s+")
_LIST = re.compile(r"List<(\w+)>\s*(\d+)\s*\(")


def _values(text: str, start: int, n: int) -> tuple[np.ndarray, int]:
    """The field value that begins at *start*: ``uniform x`` or ``nonuniform List``.

    Returns ``(values, position behind them)``; a uniform value is repeated *n* times.
    """
    if text.startswith("uniform", start):
        end = text.index(";", start)
        numbers = np.array(text[start + 7:end].replace("(", " ").replace(")", " ").split(),
                           dtype=float)
        value = numbers if numbers.size > 1 else float(numbers[0])
        return (np.tile(value, (n, 1)) if numbers.size > 1 else np.full(n, value)), end
    match = _LIST.search(text, start)
    if match is None:
        raise ValueError("neither a uniform value nor a list")
    kind, count = match.group(1), int(match.group(2))
    if kind == "scalar":
        end = text.index(")", match.end())
        values = np.array(text[match.end():end].split(), dtype=float)
    else:
        end = text.index("\n)", match.end())
        values = np.array(text[match.end():end].replace("(", " ").replace(")", " ")
                          .split(), dtype=float).reshape(-1, 3)
    if values.shape[0] != count:
        raise ValueError(f"{values.shape[0]} values where the list announces {count}")
    return values, end


def read_internal(path: Path, n_cells: int) -> np.ndarray:
    """The cell values of an ASCII field file, scalars or vectors."""
    text = Path(path).read_text()
    match = _INTERNAL.search(text)
    if match is None:
        raise errors.SolverError(f"{path} holds no internalField", subject=str(path))
    try:
        values, _ = _values(text, match.start(1), n_cells)
    except ValueError as exc:
        raise errors.SolverError(f"{path} cannot be read: {exc}", subject=str(path),
                                 remedy="The run may have been stopped while it wrote. "
                                        "Run the simulation again.") from exc
    if values.shape[0] != n_cells:
        raise errors.SolverError(
            f"{path} holds {values.shape[0]} values for a mesh of {n_cells} cells",
            subject=str(path), remedy="Build the case again and run it.")
    return values


def read_patch(path: Path, patch: str, n_faces: int) -> np.ndarray:
    """The ``value`` entry of one patch in an ASCII field file."""
    text = Path(path).read_text()
    start = text.index("boundaryField")
    match = re.search(r"\n\s*" + re.escape(patch) + r"\s*\{", text[start:])
    if match is None:
        raise errors.SolverError(f"{path} has no patch {patch}", subject=str(path))
    begin = start + match.end()
    value = re.search(r"\bvalue\s+", text[begin:])
    if value is None:
        raise errors.SolverError(f"{path}: patch {patch} holds no value", subject=str(path))
    position = begin + value.end()
    position += len(text[position:]) - len(text[position:].lstrip())
    if text.startswith("nonuniform", position):
        position += len("nonuniform")
        position += len(text[position:]) - len(text[position:].lstrip())
    values, _ = _values(text, position, n_faces)
    return values


_RESIDUAL = re.compile(r"Solving for (\w+), Initial residual = ([-+0-9.eE]+)")


def read_log(path: Path) -> dict[str, Any]:
    """Iterations done, the initial residuals of the last one and how the run ended."""
    out: dict[str, Any] = {"iterations": 0, "residuals": {}, "converged": False,
                           "ended": False}
    if not Path(path).is_file():
        return out
    current: dict[str, float] = {}
    with open(path, errors="replace") as handle:
        for line in handle:
            if line.startswith("Time = "):
                out["iterations"] += 1
                current = {}
                continue
            match = _RESIDUAL.search(line)
            if match:
                current.setdefault(match.group(1), float(match.group(2)))
                out["residuals"] = current
            elif "SIMPLE solution converged" in line:
                out["converged"] = True
            elif line.startswith("End"):
                out["ended"] = True
    return out


# --------------------------------------------------------------------------- #
# the bed, from a result
# --------------------------------------------------------------------------- #


def shields(reynolds: np.ndarray) -> np.ndarray:
    """Critical Shields parameter as the solver fits the curve to the particle Reynolds
    number: 0.6 below 0.2, 0.06 from 500 on, a polynomial in the logarithm between."""
    reynolds = np.asarray(reynolds, dtype=float)
    a = np.log10(np.clip(reynolds, 0.2, 500.0))
    curve = 10.0 ** (-0.99863612 - 0.9253539586 * a + 0.54283631 * a * a
                     - 0.084544063 * a ** 3)
    return np.where(reynolds < 0.2, 0.6, np.where(reynolds < 500.0, curve, 0.06))


def bed_exchange(geometry: BedGeometry, velocity: np.ndarray, *, grain_size: float,
                 fall: float, roughness: float, pick_up: str) -> dict[str, np.ndarray]:
    """Shear stress and pick-up at every bed face, by the formulas of the solver.

    *velocity* is the velocity vector in the bed cells. Follows ``sediDriftFoam2.C``
    line by line, including what is particular to it: the shear velocity from the
    logarithmic law at the height of the cell center, Dey's factor on the critical
    shear stress of a sloping bed, van Rijn's transport parameter, and the reference
    concentration limited to 0.1.

    Returns the shear stress and its critical value [N/m2], the transport parameter
    ``T`` [-], the reference concentration [-] and the volume source of the bed cell
    ``S_explicit`` [1/s].
    """
    u = np.asarray(velocity, dtype=float)
    speed = np.linalg.norm(u, axis=1)
    height = geometry.height
    shear_velocity = KAPPA * speed / np.log(30.0 * height / roughness)
    stress = WATER_DENSITY * shear_velocity ** 2
    critical = (shields(shear_velocity * grain_size / VISCOSITY)
                * (GRAIN_DENSITY - WATER_DENSITY) * GRAVITY * grain_size)

    horizontal = np.hypot(u[:, 0], u[:, 1]) + 1e-20
    ux, uy = u[:, 0] / horizontal, u[:, 1] / horizontal
    b = geometry.vector
    plan = np.hypot(b[:, 0], b[:, 1]) + 1e-20
    bx, by = b[:, 0] / plan, b[:, 1] / plan
    along = bx * ux + by * uy                   # negative where the flow runs uphill
    across = np.abs(bx * uy - by * ux)
    slope = np.arccos(np.clip(b[:, 2], -1.0, 1.0))
    repose = math.atan(TAN_REPOSE)
    theta = np.clip(slope * along / repose, -0.99, 0.99)
    alpha = np.minimum(slope * across / repose, 0.99)
    damping = np.clip(0.945 * (1.0 - theta) ** 0.745 * (1.0 - alpha) ** 0.372,
                      MIN_DAMPING, 3.0)
    # The rule for the inflow of a flume, at an absolute coordinate (see build_case).
    x = geometry.centre[:, 0]
    near = x < 0.5
    damping = np.where(near, np.maximum(damping, 1.0) + 10.0 * (0.5 - x), damping)
    critical = critical * damping

    transport = np.maximum((stress - critical) / critical, 0.0)
    particle = grain_size * PARTICLE_PARAMETER
    if pick_up == "suspended":
        reference = 0.015 * grain_size / height * transport ** 1.5 / particle ** 0.3
    elif pick_up == "bedload":
        with np.errstate(divide="ignore", invalid="ignore"):
            reference = (0.053 * transport ** 2.1 / particle ** 0.3 * grain_size ** 1.5
                         * 4.02 / (2.0 * height * speed))
        reference = np.where(speed > 0, reference, 0.0)
    else:
        reference = np.zeros_like(stress)
    reference = np.minimum(reference, 0.1)
    return {"stress": stress, "critical": critical, "transport": transport,
            "reference": reference, "source": fall / (2.0 * height) * reference}


@dataclass
class BedResult:
    """The fine sediment at the bed, per bed face, and the budget of the reach."""

    table: dict[str, np.ndarray]
    budget: dict[str, Any]
    layer: Path | None = None
    files: list[Path] = field(default_factory=list)

    def lines(self) -> list[str]:
        b = self.budget
        out = [f"fine sediment after {b['iterations']} iterations"
               + (" (converged)" if b["converged"] else " (the last iteration allowed)")]
        out.append(f"  enters the reach   : {b['load_in']:.4g} kg/s")
        out.append(f"  leaves the reach   : {b['load_out']:.4g} kg/s")
        out.append(f"  settles on the bed : {b['deposition']:.4g} kg/s")
        out.append(f"  picked up          : {b['pick_up']:.4g} kg/s")
        out.append(f"  not accounted for  : {b['residual']:+.3g} kg/s "
                   f"({100.0 * b['closure']:.2f} % of what moves)")
        for note in b.get("notes", []):
            out.append(f"  note               : {note}")
        return out


def evaluate(cfg, case_dir: str | Path | None = None, *, write: bool = True) -> BedResult:
    """Read the last result of the case and book the sediment at the bed.

    Per bed face, in the plan of the column: the concentration in the cell at the bed,
    the settling and the pick-up per bed area and their difference; the shear stress
    with its critical value, as the solver computed them; and, where the case names a
    period, the mass the bed takes up or gives off in it and the share of fines of the
    bed layer at its end. The budget compares what enters and leaves the reach with
    what the bed exchanges.
    """
    case_dir = Path(case_dir) if case_dir else cfg.fine_sediment_case_dir
    if not is_built(case_dir):
        raise errors.ConfigError(
            f"no fine sediment case at {case_dir}", subject="fine_sediment",
            remedy="Build the fine sediment case first.")
    time = latest_time(case_dir)
    if time is None:
        raise errors.SolverError(
            f"the fine sediment case at {case_dir} holds no result",
            subject=str(case_dir), remedy="Run the fine sediment simulation.",
            component=SOLVER)
    record = json.loads((case_dir / BUILD_RECORD).read_text())
    geometry = BedGeometry.load(case_dir / "constant" / BED_FILE)
    n_cells = int(record["n_cells"])
    velocity = read_internal(case_dir / time / "U", n_cells)
    conc = read_internal(case_dir / time / "Conc", n_cells)
    fall = float(record["fall_velocity"])
    fine = cfg.fine_sediment

    cells = geometry.cells
    exchange = bed_exchange(geometry, velocity[cells],
                            grain_size=float(record["grain_size"]), fall=fall,
                            roughness=float(record["roughness"]),
                            pick_up=str(record["pick_up"]))
    near_bed = conc[cells]                                       # share of the volume
    settling = GRAIN_DENSITY * fall * near_bed                   # kg/m2/s, plan area
    # the source the solver adds to the bed cell, as mass per plan area of the column
    picked = GRAIN_DENSITY * exchange["source"] * geometry.volume / geometry.plan_area
    net = settling - picked

    table: dict[str, np.ndarray] = {
        "depth": geometry.depth,
        "velocity": np.linalg.norm(velocity[cells], axis=1),
        "shear": exchange["stress"],
        "shear_crit": exchange["critical"],
        "transport": exchange["transport"],
        "conc_bed": GRAIN_DENSITY * near_bed,                    # kg/m3
        "settling": settling * SECONDS_PER_DAY,                  # kg/m2/day
        "pick_up": picked * SECONDS_PER_DAY,
        "net": net * SECONDS_PER_DAY,
    }
    notes: list[str] = list(record.get("notes") or [])
    if fine.period is not None:
        table.update(bed_layer(net, period=float(fine.period),
                               thickness=float(fine.layer_thickness),
                               porosity=float(fine.porosity),
                               initial=float(fine.initial_fraction)))

    area = geometry.plan_area
    load_in = GRAIN_DENSITY * sum(
        q * c / GRAIN_DENSITY for q, c in (record.get("inflow") or {}).values())
    load_out = 0.0
    phi_path = case_dir / time / "phi"
    for name, owner in geometry.outlets.items():
        flux = read_patch(phi_path, name, owner.size)
        # Water that enters through an outflow face brings no sediment (inletOutlet).
        load_out += GRAIN_DENSITY * float((np.maximum(flux, 0.0) * conc[owner]).sum())
    deposition = float((settling * area).sum())
    erosion = float((picked * area).sum())
    residual = load_in + erosion - load_out - deposition
    moving = max(load_in + erosion, load_out + deposition, 1e-30)
    run = read_log(case_dir / LOG_NAME)
    fastest = float(np.linalg.norm(velocity, axis=1).max())
    budget = {
        "time": time,
        "iterations": int(run["iterations"]) or int(float(time)),
        "converged": bool(run["converged"]),
        "residuals": run["residuals"],
        "load_in": load_in, "load_out": load_out,
        "deposition": deposition, "pick_up": erosion,
        "residual": residual, "closure": abs(residual) / moving,
        "bed_area": float(area.sum()),
        "fastest_velocity": fastest,
        "velocity_2d_p95": record.get("velocity_2d_p95"),
        "lid_step_p99": record.get("lid_step_p99"),
        "period": fine.period,
        "notes": notes,
    }
    notes += _judgements(budget)

    result = BedResult(table=table, budget=budget)
    if write:
        result.layer = write_bed_layer(case_dir / BED_LAYER_NAME, geometry, table,
                                       int(record.get("crs_epsg") or cfg.crs_epsg))
        path = case_dir / BUDGET_NAME
        path.write_text(json.dumps(budget, indent=2, sort_keys=True) + "\n")
        result.files = [result.layer, path]
    return result


#: Share of the moving sediment the budget may leave open before the result says so.
BUDGET_TOLERANCE = 0.02


def _judgements(budget: dict[str, Any]) -> list[str]:
    """What a reader of the result has to know before using it."""
    from axqua.solvers.openfoam.report import LID_STEP_SEVERE, LID_STEP_WARN

    out = []
    if not budget["converged"]:
        worst = max(budget["residuals"].items(), key=lambda item: item[1], default=None)
        out.append(
            "the run used all its iterations without reaching the residual of "
            f"{RESIDUAL:g}"
            + (f" (largest at the end: {worst[0]} {worst[1]:.1e})" if worst else "")
            + "; raise fine_sediment.iterations if the budget below does not close")
    if budget["closure"] > BUDGET_TOLERANCE:
        out.append(
            f"the sediment budget is open by {100.0 * budget['closure']:.1f} % of what "
            "moves: the concentration has not reached its steady state")
    step = budget.get("lid_step_p99")
    if step is not None and step > LID_STEP_WARN:
        reference = budget.get("velocity_2d_p95")
        out.append(
            f"the water surface of the 2D result steps by {100.0 * step:.0f} % of the "
            "local depth within two cells (99th percentile), which is "
            + ("more than a fixed water surface can represent"
               if step > LID_STEP_SEVERE else "much for a fixed water surface")
            + f". The fastest cell of this result has {budget['fastest_velocity']:.2f} "
              "m/s"
            + (f", where 95 % of the 2D result stay below {reference:.2f} m/s"
               if reference else "")
            + ": read the bed near such steps with care")
    return out


def bed_layer(net: np.ndarray, *, period: float, thickness: float, porosity: float,
              initial: float) -> dict[str, np.ndarray]:
    """What a net rate does to a bed layer whose surface stays where it is.

    *net* is the mass the bed takes up per area and time [kg/m2/s], negative where it
    gives off. The layer consists of coarse grains that stay,
    ``grain density x thickness x (1 - porosity)`` per area, and of fines in the share
    *initial* of its dry mass. All fines the bed takes up are counted to the layer.

    Two limits are marked in ``limit`` and only the first changes a number: the bed
    gives off no more fines than the layer holds (-1), and where the fines of the layer
    no longer fit into the pores of the coarse grains (+1) the surplus lies on the bed.
    The pores take fines that pack as the coarse grains do.
    """
    coarse = GRAIN_DENSITY * thickness * (1.0 - porosity)
    held = coarse * initial / (1.0 - initial)
    capacity = GRAIN_DENSITY * thickness * porosity * (1.0 - porosity)
    asked = np.asarray(net, dtype=float) * period
    mass = np.maximum(asked, -held)
    fraction = (held + mass) / (coarse + held + mass)
    limit = np.where(asked < -held, -1, np.where(held + mass > capacity, 1, 0))
    return {"mass": mass, "fraction": fraction, "change": fraction - initial,
            "limit": limit.astype(np.int64)}


def write_bed_layer(path: Path, geometry: BedGeometry, table: dict[str, np.ndarray],
                    epsg: int) -> Path:
    """The bed as polygons in the plan, one per column, with the table as attributes."""
    import geopandas as gpd
    import shapely

    rings = np.concatenate([geometry.corners, geometry.corners[:, :1]], axis=1)
    frame = gpd.GeoDataFrame(
        {name: np.asarray(values) for name, values in table.items()},
        geometry=shapely.polygons(rings), crs=f"EPSG:{int(epsg)}")
    path = Path(path)
    if path.exists():
        path.unlink()
    frame.to_file(path, layer="bed", driver="GPKG")
    return path


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #


def set_iterations(case_dir: Path, iterations: int) -> None:
    """Change the largest number of iterations of a built case."""
    path = Path(case_dir) / "system" / "controlDict"
    text = path.read_text()
    interval = max(1, math.ceil(int(iterations) / 4))
    text, n = re.subn(r"(?m)^endTime\s+\S+;", f"endTime         {int(iterations)};", text)
    text, m = re.subn(r"(?m)^writeInterval\s+\S+;", f"writeInterval   {interval};", text)
    if n != 1 or m != 1:
        raise errors.SolverError(
            f"{path} is not the controlDict aXqua wrote", subject=str(path),
            remedy="Build the fine sediment case again.")
    path.write_text(text)


def run_case(cfg, *, case_dir: str | Path | None = None, runtime=None,
             iterations: int | None = None, check_mesh: bool = True, sink=None,
             show_progress: bool = True, should_stop=None):
    """Run ``sediDriftFoam2`` on the built case, from its first iteration.

    One process: Olsen's solvers walk through the points of the mesh in the order of
    their numbers, which a mesh divided among processes does not keep, and the
    launchers of the installation refuse a parallel run.

    Returns the finished process. A run that ends with an error raises
    :class:`~axqua.core.errors.SolverError` with what the solver said last.
    """
    from axqua.solvers.openfoam.runtime import OpenFoamProgress, OpenFoamRuntime

    case_dir = Path(case_dir) if case_dir else cfg.fine_sediment_case_dir
    if not is_built(case_dir):
        raise errors.ConfigError(
            f"no fine sediment case at {case_dir}", subject="fine_sediment",
            remedy="Build the fine sediment case first.")
    runtime = runtime or OpenFoamRuntime(cfg.openfoam)
    found = runtime.run(f"command -v {SOLVER}", check=False)
    if found.returncode != 0 or not found.stdout.strip():
        raise errors.EnvironmentError(
            f"the OpenFOAM installation of this computer has no {SOLVER}",
            subject="solvers.openfoam",
            remedy="The solver is not part of OpenFOAM. Install the OpenFOAM programs "
                   "with aXqua (axqua install openfoam), which builds the solvers of "
                   "Nils Reidar B. Olsen, and enter the folder of that installation "
                   "in the profile of this computer.")

    if check_mesh:
        usable, failed, _ = runtime.check_mesh(case_dir)
        if not usable:
            raise errors.MeshError(
                "checkMesh found problems that prevent a run",
                subject=str(case_dir),
                remedy="Build the case with larger cells (openfoam.cell_size or "
                       "cell_size_factor).",
                failed_checks=list(failed))
        if failed:
            log.info("checkMesh: runnable, with marginal faces: %s", "; ".join(failed))

    import shutil

    for entry in case_dir.iterdir():           # a run starts from the first iteration
        if entry.is_dir() and _is_time(entry.name) and float(entry.name) > 0:
            shutil.rmtree(entry)
    for name in (BED_LAYER_NAME, BUDGET_NAME):
        (case_dir / name).unlink(missing_ok=True)
    if iterations is not None:
        set_iterations(case_dir, int(iterations))
    end = _end_time(case_dir)

    progress = (OpenFoamProgress(end, sink=sink, echo=show_progress)
                if (show_progress or sink is not None) else None)
    log.info("running %s for %d iterations at most", SOLVER, int(end))
    with open(case_dir / LOG_NAME, "w") as listing:
        def on_line(line: str) -> None:
            listing.write(line + "\n")
            if progress is not None:
                progress.feed(line)
        try:
            proc = runtime.run(SOLVER, cwd=case_dir, check=False, on_line=on_line,
                               should_stop=should_stop)
        finally:
            if progress is not None:
                progress.close()
    if should_stop is not None and should_stop():
        return proc
    if proc.returncode != 0:
        raise _run_error(proc, case_dir)
    return proc


def _end_time(case_dir: Path) -> float:
    match = re.search(r"(?m)^endTime\s+(\S+);",
                      (Path(case_dir) / "system" / "controlDict").read_text())
    return float(match.group(1)) if match else 0.0


def _run_error(proc, case_dir: Path) -> "errors.SolverError":
    """Why a run ended, said in terms of what can be changed."""
    output = proc.stdout or ""
    done = len(re.findall(r"(?m)^Time = ", output))
    if "Floating point exception" in output or "sigFpe" in output:
        message = (f"{SOLVER} stopped with a floating point exception after {done} "
                   "iterations")
        remedy = ("The flow has become unstable. Use larger cells (openfoam.cell_size "
                  "or cell_size_factor) or a smaller roughness "
                  f"(fine_sediment.roughness), and read {case_dir / LOG_NAME} for the "
                  "last residuals.")
    else:
        fatal = re.search(r"FOAM FATAL (?:IO )?ERROR[^\n]*\n(.*?)(?:\n\s*\n|\Z)", output,
                          re.S)
        said = " ".join(fatal.group(1).split())[:300] if fatal else ""
        message = (f"{SOLVER} ended with code {proc.returncode} after {done} iterations"
                   + (f": {said}" if said else ""))
        remedy = f"Read {case_dir / LOG_NAME} for the first error."
    return errors.SolverError(message, subject=str(case_dir), remedy=remedy,
                              component=SOLVER, return_code=proc.returncode,
                              iterations=done)
