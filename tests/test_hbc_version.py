"""aXqua refuses a HydroBayesCal below its floor also where pip did not check it.

pip checks ``axqua[calibration]`` only when it installs, so an environment that had
HydroBayesCal before keeps its old version; ``hbc.require_hbc`` therefore checks the
installed version as well. The floor is 1.12.2: 1.12.1 starts the Sobol design at the
first point of the sequence, 1.12.2 lets the scikit-learn emulator run with TELEMAC.
"""

from __future__ import annotations

import re
import sys
import tomllib
import types
from pathlib import Path

import pytest

from axqua import hbc


def test_the_floor_is_the_one_of_the_calibration_extra():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    (spec,) = project["project"]["optional-dependencies"]["calibration"]
    assert re.fullmatch(r"hydrobayescal>=([\d.]+)", spec).group(1) == hbc.HBC_MINIMUM


@pytest.mark.parametrize("installed, old", [
    ("1.11.1", True), ("1.12.0", True), ("1.12.1", False), ("1.12.1.dev3", False),
    ("1.13", False), ("2.0.0", False), ("1.12", True), ("unknown", False)])
def test_only_a_version_below_the_floor_is_too_old(installed, old):
    assert hbc.hbc_too_old(installed, "1.12.1") is old


def test_an_old_hydrobayescal_is_refused_with_the_upgrade(monkeypatch):
    fake = types.ModuleType("hydroBayesCal")
    fake.copy_driver = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "hydroBayesCal", fake)
    monkeypatch.setattr(hbc, "hbc_version", lambda: "1.11.1")
    with pytest.raises(SystemExit, match=r"(?s)1\.11\.1 is too old.*pip install -U hydrobayescal"):
        hbc.require_hbc()
    monkeypatch.setattr(hbc, "hbc_version", lambda: hbc.HBC_MINIMUM)
    assert hbc.require_hbc() is fake
