"""The gauge tool (scripts/gauge_data.py) keeps the fetched gauge across a rerun.

A download button of Streamlit reruns the whole script with the Fetch button released.
The tool used to forget the gauge then, so after the first download the page fell back
to its start and the other table needed a second fetch. Needs Streamlit (the extra
``gui``); skipped without it. No network: the gauge data are replaced by a stub.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

APP = """
import sys
sys.path.insert(0, {scripts!r})
import pandas as pd
import gauge_data as g


def stub(identifier, period_days=7, daily=False):
    return g.GaugeData(
        source="HND Bayern", label="Pegel Test", station_id="1", slug="test",
        discharge=pd.DataFrame({{"datetime": pd.to_datetime(["2026-10-14 01:00",
                                                           "2026-10-14 02:00"]),
                                "Q": [44.8, 44.9]}}),
        stage=pd.DataFrame({{"datetime": pd.to_datetime(["2026-10-14 01:00"]),
                            "stage_m": [0.52]}}),
        rating=pd.DataFrame({{"stage_m": [0.0, 0.1], "Q": [2.2, 6.38],
                             "stage_native": [0, 10]}}),
        rating_note="test")


g.load_gauge = stub
g.run_app()
"""


def test_the_fetched_gauge_survives_the_rerun_of_a_download(tmp_path):
    from streamlit.testing.v1 import AppTest

    app = tmp_path / "app.py"
    app.write_text(APP.format(scripts=str(SCRIPTS)), encoding="utf-8")
    at = AppTest.from_file(str(app), default_timeout=60)
    at.run()
    assert at.info, "the start page asks for a gauge"
    at.sidebar.selectbox[0].select_index(1).run()
    at.sidebar.button[0].click().run()
    assert not at.error, [e.value for e in at.error]
    assert "Pegel Test" in at.subheader[0].value
    at.run()                                  # what a download button does
    assert at.subheader and "Pegel Test" in at.subheader[0].value
