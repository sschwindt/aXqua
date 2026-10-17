"""The OpenFOAM outlet with a stage-discharge relation (BAW's waterLevel_alpha_prgh).

The fixed outlet holds the level that the rating curve gives at the discharge of the
case. With ``openfoam.outflow_boundary: rating-curve`` the outlet holds the level that
the curve gives at the discharge that actually leaves through it, at every time step,
with the boundary condition of the BAW that the OpenFOAM installation of aXqua builds
(Thorenz 2024). No OpenFOAM here: what is checked is what is written.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from test_openfoam import _Cfg, _two_phase_mesh  # noqa: E402

TABLE = [(0.5, 100.30), (1.0, 100.42), (2.0, 100.58)]


def test_the_outlet_follows_the_rating_curve_with_the_bc_of_the_baw(tmp_path):
    from axqua.solvers.openfoam.fields import write_fields

    write_fields(_two_phase_mesh(), _Cfg(outflow_boundary="rating-curve"), tmp_path,
                 outflow_stage=100.42, discharges={"inlet-1": 1.0}, rating=TABLE)
    for name in ("p_rgh", "alpha.water"):
        text = (tmp_path / "0" / name).read_text()
        block = text[text.index("outlet-1"):]
        block = block[:block.index("}")]
        assert "type            waterLevel_alpha_prgh;" in block, name
        assert "mode            ratingCurveTable;" in block, name
        pairs = re.findall(r"\(([-\d.e]+) ([-\d.e]+)\)", block)
        assert [(float(q), float(h)) for q, h in pairs] == TABLE, name
    u = (tmp_path / "0" / "U").read_text()
    assert "pressureInletOutletVelocity" in u[u.index("outlet-1"):]
    # the level the build starts from is the one at the discharge of the case
    assert "100.420000" in (tmp_path / "constant" / "hRef").read_text()


def test_the_control_dict_loads_the_library_of_the_baw_only_when_asked(tmp_path):
    from axqua.solvers.openfoam import dicts

    rating = _Cfg(outflow_boundary="rating-curve")
    run = [s for s in dicts.stages(rating) if s.name == "run"][0]
    text = dicts.control_dict(rating, run, patches=["inlet-1", "outlet-1"])
    assert f'libs            ("{dicts.BAW_LIBRARY}");' in text
    plain = _Cfg()
    assert dicts.BAW_LIBRARY not in dicts.control_dict(plain, run,
                                                       patches=["outlet-1"])
    # the name is the one the installer builds
    installer = (Path(__file__).resolve().parents[1] / "src" / "axqua" / "install"
                 / "vendor" / "OpenFOAM-installer" / "sediment_installer"
                 / "installer.py").read_text(encoding="utf-8")
    assert f'BAW_LIBRARY = "{dicts.BAW_LIBRARY}"' in installer


def test_a_rating_curve_outlet_needs_a_free_surface_and_a_known_setting():
    from axqua.config import OpenFoam

    OpenFoam(outflow_boundary="rating-curve").validate()
    with pytest.raises(ValueError, match="rating-curve"):
        OpenFoam(outflow_boundary="rating-curve", mode="rigid-lid").validate()
    with pytest.raises(ValueError, match="outflow_boundary"):
        OpenFoam(outflow_boundary="weir").validate()
    with pytest.raises(ValueError, match="outlet_stage"):
        OpenFoam(outflow_boundary="rating-curve", outlet_stage=815.0).validate()


def test_the_rating_table_comes_from_the_case_curve_or_is_derived(tmp_path):
    from types import SimpleNamespace

    from axqua.solvers.openfoam.case import rating_table

    curve = tmp_path / "rating.csv"
    curve.write_text("Q,WSE\n0.5,100.30\n1.0,100.42\n2.0,100.58\n")
    cfg = SimpleNamespace(boundaries=SimpleNamespace(stage_discharge=curve))
    assert rating_table(cfg, 1.0, tmp_path) == TABLE
    # one pair is no curve: the outlet could only hold one level
    curve.write_text("Q,WSE\n1.0,100.42\n")
    made = []

    def derive(cfg, discharges, *, out):
        made.append(list(discharges))
        Path(out).write_text("Q,WSE\n" + "".join(f"{q},{100 + 0.1 * q}\n"
                                                  for q in discharges))
        return Path(out)

    table = rating_table(cfg, 1.0, tmp_path, derive=derive)
    assert made and len(table) >= 5
    qs = [q for q, _ in table]
    assert qs == sorted(qs) and qs[0] < 1.0 < qs[-1]
    np.testing.assert_allclose([h for _, h in table], [100 + 0.1 * q for q in qs])
