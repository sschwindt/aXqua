"""Measurement points near a liquid boundary, and the horizontal mesh of the 3D model.

The user's rules of 2026-11-04: measurement points less than 15 mesh nodes from an inflow
or outflow are used neither by the mesh convergence study nor by the calibration, the user
sees a warning with the remedy "Increase ROI beyond the concerned liquid boundary.", and
the 3D model may have a horizontal mesh of its own (``mesh.size_scale_3d``).
"""

from __future__ import annotations

import logging
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from axqua import bayescal
from axqua.core import selafin


def _strip(folder: Path, n: int = 41) -> Path:
    """A channel of n x 3 nodes, 1 m apart, inflow at x = 0 and outflow at x = n - 1."""
    folder.mkdir(parents=True, exist_ok=True)
    xs, ys = np.arange(float(n)), np.arange(3.0)
    X, Y = np.meshgrid(xs, ys)
    x, y = X.ravel(), Y.ravel()
    ikle = []
    for j in range(2):
        for i in range(n - 1):
            a, b, c, d = j * n + i, j * n + i + 1, (j + 1) * n + i + 1, (j + 1) * n + i
            ikle += [[a + 1, b + 1, c + 1], [a + 1, c + 1, d + 1]]
    selafin.write_geometry(folder / "geometry.slf", x, y, np.array(ikle),
                           np.zeros(x.size, dtype=int), bottom=np.zeros(x.size))
    rows = []
    for j in range(3):
        rows.append(f"4 5 5  0 0 0 0  2  0 0 0  {j * n + 1}  {len(rows) + 1}  # inflow")
    for j in range(3):
        rows.append(f"5 4 4  0 0 0 0  2  0 0 0  {j * n + n}  {len(rows) + 1}  # outflow")
    for i in range(1, n - 1):
        rows.append(f"2 2 2  0 0 0 0  2  0 0 0  {i + 1}  {len(rows) + 1}  # wall")
    (folder / "boundaries.cli").write_text("\n".join(rows) + "\n")
    return folder


def _cfg(model: Path):
    return types.SimpleNamespace(model_dir=model, geometry_slf="geometry.slf",
                                 boundary_cli="boundaries.cli")


def test_nodes_are_counted_from_the_nearest_liquid_boundary(tmp_path):
    from axqua.solvers.telemac.boundary import liquid_boundary_hops

    model = _strip(tmp_path / "simulation")
    x, y, hops, kind = liquid_boundary_hops(model / "geometry.slf", model / "boundaries.cli")
    middle = (y == 1.0)
    assert hops[middle & (x == 0)][0] == 0 and kind[middle & (x == 0)][0] == "inflow"
    assert hops[middle & (x == 14)][0] == 14 and kind[middle & (x == 14)][0] == "inflow"
    assert hops[middle & (x == 26)][0] == 14 and kind[middle & (x == 26)][0] == "outflow"
    assert hops[middle & (x == 20)][0] == 20


def test_points_less_than_15_nodes_from_a_liquid_boundary_are_left_out(tmp_path, caplog):
    model = _strip(tmp_path / "simulation")
    points = pd.DataFrame({"id": [1, 2, 3, 4], "x": [3.0, 15.0, 20.0, 37.0],
                           "y": [1.0, 1.0, 1.0, 1.0], "SCALAR VELOCITY_DATA": 1.0})
    with caplog.at_level(logging.WARNING, logger="axqua.near_boundary"):
        kept = bayescal._away_from_liquid_boundaries(_cfg(model), points)
    assert kept["x"].tolist() == [15.0, 20.0]
    assert kept["id"].tolist() == [1, 2]                  # renumbered for HydroBayesCal
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "1 of 4 measurement points lie less than 15 nodes from the inflow" in text
    assert "from the outflow boundary" in text and "(4)" in text
    assert text.count("Increase ROI beyond the concerned liquid boundary.") == 2


def test_an_unbuilt_case_keeps_every_point(tmp_path):
    keep = bayescal.away_from_liquid_boundaries(_cfg(tmp_path / "none"), [1.0], [1.0])
    assert keep.tolist() == [True]


def test_a_job_puts_the_warning_into_its_result_manifest():
    from axqua.jobs import executor, results

    manifest = results.ResultManifest(job_id="j", solver="telemac", kind="calibration")
    with executor._surfaced(manifest):
        bayescal.near_boundary_log.warning("2 points are not used. %s",
                                           bayescal.NEAR_BOUNDARY_REMEDY)
        bayescal.near_boundary_log.warning("2 points are not used. %s",
                                           bayescal.NEAR_BOUNDARY_REMEDY)
        logging.getLogger("axqua").warning("not for the user")
    assert manifest.summary["warnings"] == [
        "2 points are not used. Increase ROI beyond the concerned liquid boundary."]


def test_without_a_3d_size_scale_the_3d_runs_use_the_case(tmp_path):
    from axqua.solvers.telemac.threed import three_d_case

    cfg = types.SimpleNamespace(mesh=types.SimpleNamespace(size_scale=3.0,
                                                           size_scale_3d=None))
    assert three_d_case(cfg) is cfg
    cfg.mesh.size_scale_3d = 3.0
    assert three_d_case(cfg) is cfg


def test_a_3d_size_scale_gives_the_3d_runs_a_folder_of_their_own(tmp_path):
    from axqua.config import load_config
    from axqua.solvers.telemac.threed import THREE_D_DIR, three_d_case

    case = Path(__file__).resolve().parents[1] / "cases" / "case-template" / \
        "case-template.axq-case"
    cfg = load_config(case)
    cfg.mesh.size_scale_3d = 2.308
    c3 = three_d_case(cfg, prepare=False)
    assert c3 is not cfg and c3.mesh.size_scale == pytest.approx(2.308)
    assert Path(c3.model_dir) == Path(cfg.model_dir).parent / THREE_D_DIR
    assert Path(c3.calibration_dir).parent == Path(c3.model_dir)
    assert cfg.mesh.size_scale != pytest.approx(2.308)       # the case is untouched


def test_the_2d_result_is_interpolated_onto_the_3d_mesh(tmp_path):
    from axqua.solvers.telemac.threed import interpolate_result

    coarse = _strip(tmp_path / "a", n=11)
    fine = tmp_path / "b"
    fine.mkdir()
    xs, ys = np.arange(0.0, 10.01, 0.5), np.arange(0.0, 2.01, 0.5)
    X, Y = np.meshgrid(xs, ys)
    x, y = X.ravel(), Y.ravel()
    nx = xs.size
    ikle = []
    for j in range(ys.size - 1):
        for i in range(nx - 1):
            a, b, c, d = j * nx + i, j * nx + i + 1, (j + 1) * nx + i + 1, (j + 1) * nx + i
            ikle += [[a + 1, b + 1, c + 1], [a + 1, c + 1, d + 1]]
    selafin.write_geometry(fine / "geometry.slf", x, y, np.array(ikle),
                           np.zeros(x.size, dtype=int), bottom=np.full(x.size, 0.1))
    geo = selafin.read_slf(coarse / "geometry.slf", frame=0)
    depth = 1.0 + 0.1 * geo["x"]
    selafin._write_selafin(
        coarse / "r2d.slf", geo["x"], geo["y"], geo["ikle"] + 1, geo["ipobo"],
        [("VELOCITY U", "M/S", 0.5 + 0.0 * depth), ("WATER DEPTH", "M", depth),
         ("FREE SURFACE", "M", depth), ("BOTTOM", "M", 0.0 * depth)],
        title="r", date=(2026, 11, 4, 0, 0, 0), double_precision=False)
    out = interpolate_result(coarse / "r2d.slf", coarse / "geometry.slf",
                             fine / "geometry.slf", fine / "r2d.slf")
    r = selafin.read_slf(out)
    assert len(r["x"]) == x.size
    assert r["values"]["WATER DEPTH"] == pytest.approx(1.0 + 0.1 * r["x"], abs=1e-5)
    assert r["values"]["BOTTOM"] == pytest.approx(0.1, abs=1e-6)       # its own bed
    assert r["values"]["FREE SURFACE"] == pytest.approx(
        0.1 + r["values"]["WATER DEPTH"], abs=1e-5)
