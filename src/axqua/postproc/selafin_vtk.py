"""TELEMAC results for ParaView and VisIt.

Neither program reads SERAFIN, the result format of TELEMAC (checked with ParaView
6.1 and VisIt 3.5), and both read VTK. This module converts a result file into VTK
without TELEMAC and without the ``vtk`` package: the unstructured-grid format is a small
XML header followed by the arrays, which numpy writes directly.

One result becomes one folder:

``<name>.pvd``          what ParaView opens: the list of time steps with their times
``<name>.visit``        what VisIt opens: the same list in VisIt's own form
``<name>/<name>_0000.vtu`` ...   one file per printout

**A 2D result** becomes a surface of triangles in the plane, as the model has it. The
fields are the variables of the result under their TELEMAC names (``WATER DEPTH``,
``FREE SURFACE``). The nodes stand at elevation zero by default, and that is deliberate:
an area or a volume integrated over the surface is then the one of the model, and a
streamline stays in the plane the depth-averaged velocity lives in. With the nodes at
the bed elevation the same integral runs over the *sloping* bed, which on the Isar
example is 1.4 % more area and 1.7 % more water than the model holds. The relief is one
filter away where it is wanted for a picture (*Warp By Scalar* with ``BOTTOM`` in
ParaView, *Elevate* in VisIt), and ``zsource="bed"`` or ``"surface"`` writes it into the
files for those who prefer that.

**A 3D result** becomes a volume of prisms between the planes of the TELEMAC-3D mesh,
at the elevations of each printout (``ELEVATION Z``): the mesh moves with the water
surface from frame to frame, as it does in the simulation.

Components that belong together are also written as a vector (``VELOCITY`` from
``VELOCITY U``, ``V`` and, in 3D, ``W``), because arrows and streamlines need one.

**Coordinates come from the geometry file of the model, not from the result.** TELEMAC
writes its results in single precision, coordinates included, and a number near five
million has a resolution of half a meter there: in a result file of a reach in UTM
every northing is a multiple of 0.5 m. The simulation is not affected, since TELEMAC
computes with the double-precision geometry, but the mesh *stored in the result* is.
On the full-resolution Isar model (0.5 m channel cells) 14 % of its cells have no area
left and 70 % have changed their area by more than a quarter. The fields are stored per
node, in the numbering of the geometry, so they are simply put on the nodes of the
geometry file (``geometry=``; :func:`axqua.model_column.safe_triangulation` states the
same rule for sampling). Without a geometry file the coordinates of the result are
used, and :attr:`Export.notes` says how coarse they are. The files are written in
double precision either way.

Each file carries its time twice, as ``TimeValue`` (which ParaView reads) and as
``TIME`` and ``CYCLE`` (which VisIt reads), so that a single ``.vtu`` opened by itself
still knows when it is.
"""

from __future__ import annotations

import logging
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from axqua.core.selafin import SelafinFile

log = logging.getLogger("axqua.postproc")

__all__ = ["Export", "ZSOURCES", "coordinate_resolution", "export_selafin",
           "select_frames", "vector_groups"]

#: VTK cell types.
_TRIANGLE, _WEDGE = 5, 13

#: Where the nodes of a 2D result stand: in the plane, at the bed, at the water surface.
ZSOURCES = ("flat", "bed", "surface")

_BED = ("BOTTOM", "FOND")
_SURFACE = ("FREE SURFACE", "SURFACE LIBRE")
_ELEVATION = ("ELEVATION Z", "COTE Z")

#: Endings that mark the components of one vector, in order.
_COMPONENTS = ((" U", " V", " W"), (" ALONG X", " ALONG Y", " ALONG Z"),
               (" X", " Y", " Z"), (" SUIVANT X", " SUIVANT Y", " SUIVANT Z"))

_BLOCK = 1 << 15                    # bytes per compressed block, the size VTK writes


@dataclass
class Export:
    """What a conversion wrote."""

    source: Path
    folder: Path
    pvd: Path
    visit: Path
    files: list[Path] = field(default_factory=list)
    times: list[float] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    vectors: list[str] = field(default_factory=list)
    kind: str = "2d"
    n_points: int = 0
    n_cells: int = 0
    coordinates: str = "result"          # "geometry" when they are the model's own
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source), "folder": str(self.folder),
            "pvd": str(self.pvd), "visit": str(self.visit),
            "files": [str(path) for path in self.files], "times": list(self.times),
            "fields": list(self.fields), "vectors": list(self.vectors),
            "kind": self.kind, "n_points": self.n_points, "n_cells": self.n_cells,
            "coordinates": self.coordinates, "notes": list(self.notes),
            "megabytes": round(sum(path.stat().st_size for path in self.files
                                   if path.is_file()) / 1e6, 1),
        }


def vector_groups(names: Sequence[str]) -> dict[str, list[str]]:
    """Variables that are the components of one vector: ``{vector: [components]}``.

    ``VELOCITY U`` and ``VELOCITY V`` (and ``VELOCITY W``) become ``VELOCITY``. A name
    is only grouped when its first two components both exist, so ``FRICTION VELOCITY``
    or a tracer called ``TRACER X`` alone stays what it is.
    """
    present = set(names)
    groups: dict[str, list[str]] = {}
    for name in names:
        for endings in _COMPONENTS:
            if not name.endswith(endings[0]):
                continue
            stem = name[:-len(endings[0])]
            parts = [stem + ending for ending in endings if stem + ending in present]
            if stem and len(parts) >= 2 and parts[:2] == [stem + endings[0],
                                                          stem + endings[1]]:
                groups.setdefault(stem.strip(), parts)
            break
    return groups


def select_frames(n_frames: int, frames: Any = "all") -> list[int]:
    """The indices a caller asked for: ``"all"``, ``"last"``, one number, or a list.

    A number counts from the end when negative. ``"every N"`` takes each N-th frame
    and always the last one, which is the state a steady run was run for.
    """
    if n_frames <= 0:
        return []
    if frames in (None, "", "all"):
        return list(range(n_frames))
    if frames == "last":
        return [n_frames - 1]
    if isinstance(frames, str) and frames.startswith("every"):
        step = max(1, int(frames.split()[-1]))
        chosen = list(range(0, n_frames, step))
        return chosen if chosen[-1] == n_frames - 1 else chosen + [n_frames - 1]
    if isinstance(frames, (int, np.integer)) or (
            isinstance(frames, str) and frames.lstrip("-").isdigit()):
        frames = [int(frames)]
    out = []
    for index in frames:
        index = int(index)
        if index < 0:
            index += n_frames
        if not 0 <= index < n_frames:
            raise IndexError(f"the result has {n_frames} frames, not one with "
                             f"index {index}")
        out.append(index)
    return out


# ----------------------------------------------------------------------- VTK writing


def _vtk_type(array: np.ndarray) -> str:
    kind = array.dtype
    if kind.kind == "f":
        return "Float64" if kind.itemsize == 8 else "Float32"
    if kind.kind == "u":
        return f"UInt{kind.itemsize * 8}"
    return f"Int{kind.itemsize * 8}"


def _payload(array: np.ndarray, compress: bool) -> bytes:
    """One array as VTK stores it in an appended block, little-endian."""
    raw = np.ascontiguousarray(array, dtype=array.dtype.newbyteorder("<")).tobytes()
    if not compress:
        return np.array([len(raw)], dtype="<u8").tobytes() + raw
    blocks = [zlib.compress(raw[start:start + _BLOCK], 3)
              for start in range(0, len(raw), _BLOCK)]
    header = np.array([len(blocks), _BLOCK, len(raw) % _BLOCK]
                      + [len(block) for block in blocks], dtype="<u8")
    return header.tobytes() + b"".join(blocks)


class _Appended:
    """The arrays of one file, and the place each one has in the appended block."""

    def __init__(self, compress: bool) -> None:
        self.compress = compress
        self.parts: list[bytes] = []
        self.size = 0

    def add(self, name: str, array: np.ndarray, components: int = 1) -> str:
        payload = _payload(array, self.compress)
        offset, self.size = self.size, self.size + len(payload)
        self.parts.append(payload)
        named = f' Name="{_escape(name)}"' if name else ""
        return (f'<DataArray type="{_vtk_type(array)}"{named} '
                f'NumberOfComponents="{components}" format="appended" '
                f'offset="{offset}"/>')


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _write_vtu(path: Path, points: np.ndarray, cells: np.ndarray, cell_type: int,
               scalars: dict[str, np.ndarray], vectors: dict[str, np.ndarray],
               time: float, cycle: int, compress: bool) -> None:
    n_cells, per_cell = cells.shape
    store = _Appended(compress)
    lines = ['<?xml version="1.0"?>',
             '<VTKFile type="UnstructuredGrid" version="1.0" '
             'byte_order="LittleEndian" header_type="UInt64"'
             + (' compressor="vtkZLibDataCompressor"' if compress else "") + ">",
             "<UnstructuredGrid>",
             "<FieldData>",
             f'<DataArray type="Float64" Name="TimeValue" NumberOfTuples="1" '
             f'format="ascii">{time!r}</DataArray>',
             f'<DataArray type="Float64" Name="TIME" NumberOfTuples="1" '
             f'format="ascii">{time!r}</DataArray>',
             f'<DataArray type="Int32" Name="CYCLE" NumberOfTuples="1" '
             f'format="ascii">{cycle}</DataArray>',
             "</FieldData>",
             f'<Piece NumberOfPoints="{len(points)}" NumberOfCells="{n_cells}">',
             "<Points>", store.add("Points", points, 3), "</Points>",
             "<Cells>",
             store.add("connectivity", cells.astype(np.int32).ravel()),
             store.add("offsets", np.arange(per_cell, per_cell * (n_cells + 1),
                                            per_cell, dtype=np.int64)),
             store.add("types", np.full(n_cells, cell_type, dtype=np.uint8)),
             "</Cells>"]
    first_vector = next(iter(vectors), "")
    first_scalar = next(iter(scalars), "")
    lines.append("<PointData"
                 + (f' Scalars="{_escape(first_scalar)}"' if first_scalar else "")
                 + (f' Vectors="{_escape(first_vector)}"' if first_vector else "")
                 + ">")
    for name, values in scalars.items():
        lines.append(store.add(name, values))
    for name, values in vectors.items():
        lines.append(store.add(name, values, 3))
    lines += ["</PointData>", "</Piece>", "</UnstructuredGrid>",
              '<AppendedData encoding="raw">']
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as out:
        out.write(("\n".join(lines) + "\n_").encode("utf-8"))
        for part in store.parts:
            out.write(part)
        out.write(b"\n</AppendedData>\n</VTKFile>\n")
    tmp.replace(path)


# ------------------------------------------------------------------------- geometry


def _first(values: dict[str, np.ndarray], names: Sequence[str]) -> np.ndarray | None:
    return next((values[name] for name in names if name in values), None)


def _wedges(ikle: np.ndarray) -> np.ndarray:
    """TELEMAC prisms in the node order of a VTK wedge.

    Both list the lower triangle and then the upper one. TELEMAC walks each triangle
    counterclockwise seen from above; a VTK wedge wants the first triangle to face
    away from the second, which is clockwise seen from above. Swapping two nodes in
    both triangles turns one into the other; without it every cell has a negative
    volume, which a picture hides and an integral does not.
    """
    return ikle[:, [0, 2, 1, 3, 5, 4]]


def coordinate_resolution(slf: SelafinFile) -> float:
    """The spacing [m] of the numbers a result stores its coordinates with.

    Zero for a double-precision file. For single precision it is the distance between
    two neighboring representable numbers at the largest coordinate.
    """
    if slf.double or not slf.x.size:
        return 0.0
    largest = max(float(np.abs(slf.x).max()), float(np.abs(slf.y).max()))
    return float(np.spacing(np.float32(largest)))


def _plan_coordinates(slf: SelafinFile, geometry: str | Path | None, export: Export
                      ) -> tuple[np.ndarray, np.ndarray]:
    """``x`` and ``y`` of the plan nodes: the model's own where they can be had."""
    x, y = slf.x[:slf.npoin2], slf.y[:slf.npoin2]
    if geometry is not None:
        model = SelafinFile(geometry)
        if model.npoin2 == slf.npoin2:
            export.coordinates = "geometry"
            return model.x[:model.npoin2], model.y[:model.npoin2]
        export.notes.append(
            f"{Path(geometry).name} has {model.npoin2} nodes and {slf.path.name} has "
            f"{slf.npoin2}: the result belongs to another mesh, so its own coordinates "
            "are used.")
    resolution = coordinate_resolution(slf)
    if resolution >= 0.01:
        export.notes.append(
            f"The coordinates stored in {slf.path.name} have a resolution of "
            f"{resolution:g} m, because TELEMAC writes results in single precision. "
            "Name the geometry file of the model to place the nodes exactly.")
    return x, y


def _points(slf: SelafinFile, values: dict[str, np.ndarray], zsource: str,
            x: np.ndarray, y: np.ndarray) -> np.ndarray:
    points = np.empty((slf.npoin, 3), dtype=np.float64)
    if slf.nplan > 1:
        points[:, 0] = np.tile(x, slf.nplan)
        points[:, 1] = np.tile(y, slf.nplan)
        z = _first(values, _ELEVATION)
        if z is None:
            raise ValueError(
                f"{slf.path.name} is a 3D result without ELEVATION Z, so the elevation "
                "of its nodes is not known")
        points[:, 2] = z
        return points
    points[:, 0], points[:, 1] = x, y
    chosen = None if zsource == "flat" else _first(
        values, _SURFACE if zsource == "surface" else _BED)
    points[:, 2] = 0.0 if chosen is None else chosen
    return points


# --------------------------------------------------------------------------- export


def export_selafin(source: str | Path, out_dir: str | Path, *, name: str | None = None,
                   geometry: str | Path | None = None, frames: Any = "all",
                   zsource: str = "flat", compress: bool = True,
                   on_frame: Callable[[int, int, float], None] | None = None) -> Export:
    """Convert a TELEMAC result into VTK files for ParaView and VisIt.

    *geometry* is the geometry file of the model, whose coordinates are exact where
    those of a result are rounded (see the module text); *frames* selects the
    printouts (:func:`select_frames`); *zsource* is where the nodes of a 2D result
    stand (:data:`ZSOURCES`); *on_frame* is called with ``(done, total, time)`` after
    each file, for a progress display.

    The folder of a result is rewritten whole: files of an earlier export of the same
    result that the new one does not write are removed, so that the ``.pvd`` and the
    folder never disagree.
    """
    if zsource not in ZSOURCES:
        raise ValueError(f"zsource must be one of {', '.join(ZSOURCES)}, got {zsource!r}")
    source = Path(source)
    slf = SelafinFile(source)
    if slf.ndp not in (3, 6):
        raise ValueError(f"{source.name} has cells of {slf.ndp} nodes; triangles and "
                         "prisms are what TELEMAC-2D and TELEMAC-3D write")
    chosen = select_frames(len(slf), frames)
    if not chosen:
        raise ValueError(f"{source.name} holds no results")

    name = name or source.stem
    out_dir = Path(out_dir)
    folder = out_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    volume = slf.nplan > 1 and slf.ndp == 6
    cells = _wedges(slf.ikle) if volume else slf.ikle
    groups = vector_groups(slf.var_names)
    # In 3D the elevation is the geometry itself; as a field it is still useful for
    # coloring by height, so it stays.
    export = Export(source=source, folder=folder, pvd=out_dir / f"{name}.pvd",
                    visit=out_dir / f"{name}.visit", kind="3d" if volume else "2d",
                    fields=list(slf.var_names), vectors=list(groups),
                    n_points=slf.npoin, n_cells=slf.nelem)

    x, y = _plan_coordinates(slf, geometry, export)
    for done, index in enumerate(chosen, start=1):
        values = slf.frame(index)
        points = _points(slf, values, zsource, x, y)
        scalars = {field_name: values[field_name] for field_name in slf.var_names}
        vectors = {}
        for vector, parts in groups.items():
            packed = np.zeros((slf.npoin, 3), dtype=values[parts[0]].dtype)
            for column, part in enumerate(parts[:3]):
                packed[:, column] = values[part]
            vectors[vector] = packed
        path = folder / f"{name}_{done - 1:04d}.vtu"
        _write_vtu(path, points, cells, _WEDGE if volume else _TRIANGLE, scalars,
                   vectors, float(slf.times[index]), index, compress)
        export.files.append(path)
        export.times.append(float(slf.times[index]))
        if on_frame is not None:
            on_frame(done, len(chosen), float(slf.times[index]))

    keep = {path.name for path in export.files}
    for stale in folder.glob(f"{name}_*.vtu"):
        if stale.name not in keep:
            stale.unlink()
    _write_index(export, name)
    log.info("exported %s: %d time step(s), %d points, %d %s -> %s", source.name,
             len(export.files), export.n_points, export.n_cells,
             "prisms" if volume else "triangles", export.pvd)
    return export


def _write_index(export: Export, name: str) -> None:
    """The two files that turn a folder of ``.vtu`` into one time series."""
    rows = [f'    <DataSet timestep="{time!r}" group="" part="0" '
            f'file="{name}/{path.name}"/>'
            for time, path in zip(export.times, export.files)]
    export.pvd.write_text(
        '<?xml version="1.0"?>\n'
        '<VTKFile type="Collection" version="0.1" byte_order="LittleEndian">\n'
        "  <Collection>\n" + "\n".join(rows) + "\n  </Collection>\n</VTKFile>\n",
        encoding="utf-8")
    export.visit.write_text(
        "".join(f"{name}/{path.name}\n" for path in export.files), encoding="utf-8")
