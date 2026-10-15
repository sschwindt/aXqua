"""TELEMAC results for ParaView and VisIt.

The files are read back here by a reader of a few lines, so the tests need neither
program. What the two programs themselves make of an export of a real result (time
steps, variables, volumes) was checked once with ParaView 6.1 and VisIt 3.5 and is
recorded with the module.
"""

from __future__ import annotations

import json
import os
import re
import zlib
from pathlib import Path

import numpy as np
import pytest

from axqua.core.selafin import SelafinFile, read_slf
from axqua.postproc.selafin_vtk import (export_selafin, select_frames, vector_groups)

I4 = np.dtype(">i4")


def _record(payload: bytes) -> bytes:
    marker = np.array(len(payload), dtype=I4).tobytes()
    return marker + payload + marker


def write_result(path: Path, x, y, ikle, variables, times, *, nplan=1, double=False):
    """A SERAFIN file as TELEMAC writes a result: several frames, 2D or 3D.

    *variables* is ``[(name, unit, array of shape (frames, npoin))]``; *ikle* is
    0-based with 3 nodes per cell (2D) or 6 (prisms).
    """
    real = np.dtype(">f8" if double else ">f4")
    ikle = np.asarray(ikle)
    npoin = len(x)
    iparam = np.zeros(10, dtype=I4)
    iparam[0] = 1
    iparam[6] = nplan if nplan > 1 else 0
    with open(path, "wb") as out:
        out.write(_record(f"{'test result':<72}".encode()
                          + (b"SERAFIND" if double else b"SERAFIN ")))
        out.write(_record(np.array([len(variables), 0], dtype=I4).tobytes()))
        for name, unit, _ in variables:
            out.write(_record(f"{name:<16}{unit:<16}".encode()))
        out.write(_record(iparam.tobytes()))
        out.write(_record(np.array([len(ikle), npoin, ikle.shape[1], 1],
                                   dtype=I4).tobytes()))
        out.write(_record((ikle + 1).astype(I4).tobytes()))
        out.write(_record(np.zeros(npoin, dtype=I4).tobytes()))
        out.write(_record(np.asarray(x).astype(real).tobytes()))
        out.write(_record(np.asarray(y).astype(real).tobytes()))
        for index, time in enumerate(times):
            out.write(_record(np.array([time], dtype=real).tobytes()))
            for _, _, values in variables:
                out.write(_record(np.asarray(values[index]).astype(real).tobytes()))
    return path


def read_vtu(path: Path) -> dict:
    """The arrays and the field data of a ``.vtu`` written by the export."""
    raw = path.read_bytes()
    marker = raw.index(b'<AppendedData encoding="raw">')
    start = raw.index(b"_", marker) + 1
    head = raw[:marker].decode("utf-8")
    compressed = 'compressor="vtkZLibDataCompressor"' in head
    arrays = {}
    pattern = (r'<DataArray type="(\w+)"(?: Name="([^"]*)")? NumberOfComponents="(\d+)" '
               r'format="appended" offset="(\d+)"/>')
    for kind, name, components, offset in re.findall(pattern, head):
        at = start + int(offset)
        if compressed:
            count, block, _last = np.frombuffer(raw, "<u8", 3, at)
            sizes = np.frombuffer(raw, "<u8", int(count), at + 24)
            at += 24 + 8 * int(count)
            data = b""
            for size in sizes:
                data += zlib.decompress(raw[at:at + int(size)])
                at += int(size)
            assert all(len(zlib.decompress(b"")) == 0 for _ in ()) and block == 1 << 15
        else:
            size = int(np.frombuffer(raw, "<u8", 1, at)[0])
            data = raw[at + 8:at + 8 + size]
        dtype = {"Float32": "<f4", "Float64": "<f8", "Int32": "<i4", "Int64": "<i8",
                 "UInt8": "u1"}[kind]
        values = np.frombuffer(data, dtype)
        arrays[name] = values.reshape(-1, int(components)) if int(components) > 1 \
            else values
    fields = {name: float(value) for name, value in re.findall(
        r'Name="(TimeValue|TIME|CYCLE)" NumberOfTuples="1" format="ascii">([^<]+)<', head)}
    return {"arrays": arrays, "fields": fields, "head": head}


# A strip of four triangles far from the origin, as a river reach in UTM is.
X = np.array([676927.125, 676928.375, 676929.625, 676927.125, 676928.375, 676929.625])
Y = np.array([5267833.5, 5267833.5, 5267833.5, 5267834.75, 5267834.75, 5267834.75])
IKLE = np.array([[0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4]])       # counterclockwise
TIMES = [0.0, 116.5, 3000.0]


@pytest.fixture
def result_2d(tmp_path) -> Path:
    bed = np.array([815.0, 815.2, 815.4, 816.0, 816.1, 816.3])
    depth = np.array([[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                      [0.5, 0.4, 0.0, 0.2, 0.1, 0.0],
                      [1.0, 0.8, 0.3, 0.6, 0.5, 0.2]])
    u = depth * 0.9
    v = -depth * 0.2
    return write_result(tmp_path / "r2d.slf", X, Y, IKLE, [
        ("VELOCITY U", "M/S", u), ("VELOCITY V", "M/S", v),
        ("WATER DEPTH", "M", depth), ("FREE SURFACE", "M", bed + depth),
        ("BOTTOM", "M", np.tile(bed, (3, 1))),
        ("FRICTION VELOCITY", "M/S", depth * 0.05)], TIMES)


@pytest.fixture
def geometry(tmp_path) -> Path:
    """The geometry file of the model: the same nodes, in double precision."""
    bed = np.array([[815.0, 815.2, 815.4, 816.0, 816.1, 816.3]])
    return write_result(tmp_path / "geometry.slf", X, Y, IKLE, [("BOTTOM", "M", bed)],
                        [0.0], double=True)


@pytest.fixture
def result_3d(tmp_path) -> Path:
    """Two planes over the same four triangles: the bed, and a surface 0.5 m above."""
    bed = np.array([815.0, 815.2, 815.4, 816.0, 816.1, 816.3])
    z = np.concatenate([bed, bed + 0.5])
    prisms = np.hstack([IKLE, IKLE + 6])
    frames = np.tile(z, (2, 1))
    frames[1, 6:] += 0.25                                   # the surface rises
    return write_result(tmp_path / "r3d.slf", np.tile(X, 2), np.tile(Y, 2), prisms, [
        ("ELEVATION Z", "M", frames), ("VELOCITY U", "M/S", frames * 0 + 1.0),
        ("VELOCITY V", "M/S", frames * 0 + 0.5), ("VELOCITY W", "M/S", frames * 0 + 0.1)],
        [0.0, 60.0], nplan=2)


# ------------------------------------------------------------------- reading frames


def test_a_frame_is_read_alone_and_equals_what_the_whole_file_gives(result_2d):
    slf = SelafinFile(result_2d)
    assert (len(slf), slf.nplan, slf.npoin, slf.nelem, slf.ndp) == (3, 1, 6, 4, 3)
    assert slf.times == pytest.approx(TIMES)
    assert slf.var_names[:3] == ["VELOCITY U", "VELOCITY V", "WATER DEPTH"]
    assert slf.var_units[2] == "M"
    whole = read_slf(result_2d, frame=1)
    alone = slf.frame(1)
    for name in slf.var_names:
        assert np.array_equal(alone[name].astype(float), whole["values"][name])
    assert np.array_equal(slf.frame(-1)["WATER DEPTH"], slf.frame(2)["WATER DEPTH"])
    assert list(slf.frame(2, names=["BOTTOM"])) == ["BOTTOM"]
    with pytest.raises(IndexError, match="3 frames"):
        slf.frame(3)


def test_a_double_precision_result_keeps_its_precision(tmp_path):
    values = np.array([[815.123456789012] * 6])
    path = write_result(tmp_path / "d.slf", X, Y, IKLE, [("BOTTOM", "M", values)],
                        [0.0], double=True)
    slf = SelafinFile(path)
    assert slf.double and slf.frame(0)["BOTTOM"][0] == 815.123456789012
    assert slf.x[0] == 676927.125


def test_a_file_that_is_not_a_result_is_refused(tmp_path):
    (tmp_path / "notes.slf").write_bytes(b"\x00\x00\x00\x05hello\x00\x00\x00\x05")
    with pytest.raises(ValueError, match="not a SERAFIN file"):
        SelafinFile(tmp_path / "notes.slf")


# -------------------------------------------------------------------------- choices


def test_the_time_steps_to_export_are_all_the_last_or_a_selection():
    assert select_frames(5) == [0, 1, 2, 3, 4]
    assert select_frames(5, "last") == [4]
    assert select_frames(5, "every 2") == [0, 2, 4]
    assert select_frames(6, "every 4") == [0, 4, 5]          # the final state is kept
    assert select_frames(5, "-2") == [3] and select_frames(5, 0) == [0]
    assert select_frames(5, [0, -1]) == [0, 4]
    assert select_frames(0) == []
    with pytest.raises(IndexError):
        select_frames(5, 7)


def test_components_become_a_vector_only_when_they_belong_together():
    groups = vector_groups(["VELOCITY U", "VELOCITY V", "VELOCITY W", "WATER DEPTH",
                            "FRICTION VELOCITY", "FLOWRATE ALONG X", "FLOWRATE ALONG Y",
                            "TRACER X", "WIND ALONG X"])
    assert groups == {"VELOCITY": ["VELOCITY U", "VELOCITY V", "VELOCITY W"],
                      "FLOWRATE": ["FLOWRATE ALONG X", "FLOWRATE ALONG Y"]}


# --------------------------------------------------------------------------- export


def test_a_2d_result_becomes_a_time_series_both_programs_open(result_2d, tmp_path):
    done = export_selafin(result_2d, tmp_path / "vtk")
    assert (done.kind, done.n_points, done.n_cells) == ("2d", 6, 4)
    assert [path.name for path in done.files] == ["r2d_0000.vtu", "r2d_0001.vtu",
                                                  "r2d_0002.vtu"]
    # ParaView: the collection, with the time of each step
    pvd = done.pvd.read_text()
    assert pvd.count("<DataSet ") == 3
    assert 'timestep="3000.0"' in pvd and 'file="r2d/r2d_0002.vtu"' in pvd
    # VisIt: the same files, one per line, relative to the index
    assert done.visit.read_text().split() == ["r2d/r2d_0000.vtu", "r2d/r2d_0001.vtu",
                                              "r2d/r2d_0002.vtu"]
    assert json.loads(json.dumps(done.as_dict()))["vectors"] == ["VELOCITY"]

    last = read_vtu(done.files[-1])
    # each file knows its own time, in the form each program looks for
    assert last["fields"] == {"TimeValue": 3000.0, "TIME": 3000.0, "CYCLE": 2.0}
    arrays = last["arrays"]
    source = SelafinFile(result_2d).frame(2)
    for name, values in source.items():
        assert np.array_equal(arrays[name], values), name     # nothing is rounded
    assert arrays["WATER DEPTH"].dtype == np.float32
    assert np.array_equal(arrays["connectivity"].reshape(-1, 3), IKLE)
    assert list(arrays["offsets"]) == [3, 6, 9, 12] and set(arrays["types"]) == {5}
    # the vector the arrows and streamlines need; the friction velocity is no part of it
    assert np.array_equal(arrays["VELOCITY"][:, 0], source["VELOCITY U"])
    assert np.array_equal(arrays["VELOCITY"][:, 1], source["VELOCITY V"])
    assert not arrays["VELOCITY"][:, 2].any()
    assert 'Vectors="VELOCITY"' in last["head"]


def test_the_nodes_are_placed_with_the_coordinates_of_the_geometry_file(
        result_2d, geometry, tmp_path):
    """TELEMAC stores the coordinates of a result in single precision. Near five
    million that is a resolution of half a meter: 5267834.75 becomes 5267835.0, and a
    strip of 1.25 m is 1.5 m wide in the result file. The geometry file has them
    exactly, and the fields are numbered alike."""
    from axqua.postproc.selafin_vtk import coordinate_resolution

    stored = SelafinFile(result_2d)
    assert coordinate_resolution(stored) == 0.5
    assert coordinate_resolution(SelafinFile(geometry)) == 0.0
    assert set(stored.y) == {5267833.5, 5267835.0}            # what the result holds

    rounded = export_selafin(result_2d, tmp_path / "a", frames="last")
    assert rounded.coordinates == "result"
    assert "resolution of 0.5 m" in rounded.notes[0]
    assert np.array_equal(read_vtu(rounded.files[0])["arrays"]["Points"][:, 1], stored.y)

    exact = export_selafin(result_2d, tmp_path / "b", frames="last", geometry=geometry)
    assert exact.coordinates == "geometry" and exact.notes == []
    points = read_vtu(exact.files[0])["arrays"]["Points"]
    assert points.dtype == np.float64
    assert np.array_equal(points[:, 0], X) and np.array_equal(points[:, 1], Y)
    # the fields are untouched by where the nodes stand
    assert np.array_equal(read_vtu(exact.files[0])["arrays"]["WATER DEPTH"],
                          read_vtu(rounded.files[0])["arrays"]["WATER DEPTH"])


def test_a_geometry_of_another_mesh_is_not_used(result_2d, tmp_path):
    other = write_result(tmp_path / "other.slf", X[:3], Y[:3], IKLE[:1] * 0 + [0, 1, 2],
                         [("BOTTOM", "M", np.zeros((1, 3)))], [0.0], double=True)
    done = export_selafin(result_2d, tmp_path / "vtk", frames="last", geometry=other)
    assert done.coordinates == "result"
    assert "another mesh" in done.notes[0]


def test_the_plan_mesh_is_the_default_and_the_relief_is_a_choice(result_2d, tmp_path):
    """In the plane, an integral over the surface is the one of the model. At the bed
    it runs over the sloping bed, which is more area than the model has."""
    def z(source):
        done = export_selafin(result_2d, tmp_path / source, frames="last",
                              zsource=source)
        return read_vtu(done.files[0])["arrays"]["Points"][:, 2]

    frame = SelafinFile(result_2d).frame(-1)
    assert not z("flat").any()
    assert np.array_equal(z("bed"), frame["BOTTOM"].astype(float))
    assert np.array_equal(z("surface"), frame["FREE SURFACE"].astype(float))
    with pytest.raises(ValueError, match="zsource"):
        export_selafin(result_2d, tmp_path / "x", zsource="sideways")


def test_a_3d_result_becomes_prisms_with_a_positive_volume(result_3d, geometry,
                                                           tmp_path):
    """TELEMAC and VTK walk the triangles of a prism in opposite directions. Unturned,
    every cell has a negative volume: a picture hides that, an integral does not."""
    done = export_selafin(result_3d, tmp_path / "vtk", geometry=geometry)
    assert done.coordinates == "geometry"        # one plan mesh serves every plane
    assert (done.kind, done.n_points, done.n_cells) == ("3d", 12, 4)
    assert done.vectors == ["VELOCITY"]
    volumes = []
    for path in done.files:
        arrays = read_vtu(path)["arrays"]
        points = arrays["Points"]
        cells = arrays["connectivity"].reshape(-1, 6)
        assert set(arrays["types"]) == {13} and list(arrays["offsets"]) == [6, 12, 18, 24]
        base = np.cross(points[cells[:, 1]] - points[cells[:, 0]],
                        points[cells[:, 2]] - points[cells[:, 0]])
        up = points[cells[:, 3]] - points[cells[:, 0]]
        # the first triangle of a VTK wedge faces away from the second
        assert (np.einsum("ij,ij->i", base, up) < 0).all()
        area = 0.5 * np.abs(base[:, 2])
        thickness = (points[cells[:, 3:], 2].mean(axis=1)
                     - points[cells[:, :3], 2].mean(axis=1))
        volumes.append(float((area * thickness).sum()))
        assert np.array_equal(arrays["VELOCITY"],
                              np.tile([1.0, 0.5, 0.1], (12, 1)).astype(np.float32))
    plan_area = 4 * 0.5 * 1.25 * 1.25
    # the mesh follows the water surface from one printout to the next
    assert volumes == pytest.approx([plan_area * 0.5, plan_area * 0.75])
    assert read_vtu(done.files[1])["fields"]["TimeValue"] == 60.0


def test_a_new_export_leaves_no_time_step_of_the_old_one_behind(result_2d, tmp_path):
    first = export_selafin(result_2d, tmp_path / "vtk")
    assert len(list(first.folder.glob("*.vtu"))) == 3
    again = export_selafin(result_2d, tmp_path / "vtk", frames="last")
    assert [path.name for path in again.folder.glob("*.vtu")] == ["r2d_0000.vtu"]
    assert again.pvd.read_text().count("<DataSet ") == 1
    assert again.times == [3000.0]
    assert not list(again.folder.glob("*.part"))


def test_an_export_without_compression_holds_the_same_arrays(result_2d, tmp_path):
    packed = export_selafin(result_2d, tmp_path / "a", frames="last")
    plain = export_selafin(result_2d, tmp_path / "b", frames="last", compress=False)
    one, two = read_vtu(packed.files[0]), read_vtu(plain.files[0])
    assert "compressor" in one["head"] and "compressor" not in two["head"]
    for name, values in one["arrays"].items():
        assert np.array_equal(values, two["arrays"][name]), name


def test_progress_is_reported_per_time_step(result_2d, tmp_path):
    seen = []
    export_selafin(result_2d, tmp_path / "vtk", frames="every 2",
                   on_frame=lambda done, total, time: seen.append((done, total, time)))
    assert seen == [(1, 2, 0.0), (2, 2, 3000.0)]


# ------------------------------------------------------------------------------ CLI


def test_the_result_files_of_a_case_follow_its_case_file(fake_case):
    from axqua.solvers.telemac import spec

    names = [name for name, _label, _path in spec.result_files(fake_case)]
    assert names[:2] == ["r2d.slf", "r2d-hotstart.slf"]
    assert "r3d.slf" in names and "r3d-2d-hydrostatic.slf" in names
    assert len(names) == len(set(names))
    fake_case.results_slf = "steady.slf"
    renamed = [name for name, _label, _path in spec.result_files(fake_case)]
    assert renamed[:2] == ["steady.slf", "steady-hotstart.slf"]


def test_the_command_lists_and_exports_the_results_of_a_case(fake_case, result_2d,
                                                             capsys):
    from axqua.exportcli import run_export

    case = Path(fake_case.model_dir).parent.parent / "case-config.yml"
    assert case.is_file()
    target = fake_case.model_path("r2d.slf")
    target.write_bytes(result_2d.read_bytes())

    assert run_export([str(case), "--list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)["data"]
    (row,) = listed["results"]
    assert (row["name"], row["kind"], row["frames"], row["t_last"]) == \
        ("r2d.slf", "2d", 3, 3000.0)
    assert row["exported"] == {"pvd": "", "visit": "", "up_to_date": False}
    assert listed["folder"] == str(Path(fake_case.postprocessing_dir) / "vtk")

    assert run_export([str(case), "--frames", "last", "--json"]) == 0
    done = json.loads(capsys.readouterr().out)["data"]
    (export,) = done["exports"]
    assert Path(export["pvd"]).is_file() and Path(export["visit"]).is_file()
    assert len(export["files"]) == 1
    # the case has no geometry file yet, and the answer says what that means
    assert export["coordinates"] == "result" and "0.5 m" in export["notes"][0]

    # with the geometry file of the model, the nodes are placed exactly
    write_result(fake_case.model_path(fake_case.geometry_slf), X, Y, IKLE,
                 [("BOTTOM", "M", np.zeros((1, 6)))], [0.0], double=True)
    assert run_export([str(case), "--frames", "last", "--json"]) == 0
    (export,) = json.loads(capsys.readouterr().out)["data"]["exports"]
    assert export["coordinates"] == "geometry" and export["notes"] == []
    assert np.array_equal(read_vtu(Path(export["files"][0]))["arrays"]["Points"][:, 1], Y)

    assert run_export([str(case), "--list", "--json"]) == 0
    row = json.loads(capsys.readouterr().out)["data"]["results"][0]
    assert row["exported"]["up_to_date"] and row["exported"]["pvd"] == export["pvd"]

    # the simulation is run again: what is exported is no longer what the result holds
    later = Path(export["pvd"]).stat().st_mtime + 10
    os.utime(target, (later, later))
    assert run_export([str(case), "--list", "--json"]) == 0
    row = json.loads(capsys.readouterr().out)["data"]["results"][0]
    assert row["exported"]["pvd"] and not row["exported"]["up_to_date"]

    assert run_export([str(case), "--result", "r9d.slf", "--json"]) != 0
    refused = json.loads(capsys.readouterr().out)
    assert "r2d.slf" in refused["error"]["remedy"]


def test_one_result_file_is_exported_without_a_case(result_3d, geometry, capsys):
    from axqua.exportcli import run_export

    assert run_export([str(result_3d), "--json"]) == 0
    done = json.loads(capsys.readouterr().out)["data"]
    assert done["folder"] == str(result_3d.parent / "vtk")
    assert done["exports"][0]["kind"] == "3d"
    assert (result_3d.parent / "vtk" / "r3d.visit").is_file()
    assert done["exports"][0]["coordinates"] == "result"

    assert run_export([str(result_3d), "--geometry", str(geometry), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["exports"][0]["coordinates"] \
        == "geometry"
    assert run_export([str(result_3d), "--geometry", "nowhere.slf", "--json"]) != 0


def test_a_case_without_results_says_what_to_do(fake_case, capsys):
    from axqua.exportcli import run_export

    case = Path(fake_case.model_dir).parent.parent / "case-config.yml"
    assert run_export([str(case), "--list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["results"] == []
    assert run_export([str(case), "--json"]) != 0
    assert "Run a simulation" in json.loads(capsys.readouterr().out)["error"]["remedy"]
