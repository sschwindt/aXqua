"""Tests for reading a physical-flume campaign as cross-section reference data.

The fixtures reproduce the layout of the published Munich vertical-slot-fishway
dataset (Zenodo 14440623, ``physical-flume/``): a depth summary written as one block
per discharge, and a velocity table whose discharge label appears only on the first row
of each block. Both are hand-made sheets with title rows, footnote markers and gaps,
which is what the reader has to survive.

What is pinned above all is the **scale**: the depth sheet is published in prototype
dimensions and the velocity sheet is not marked either way, so neither scale may be
assumed. Both are explicit arguments, and a comparison records which convention it
used.
"""
import math

import pytest

from axqua import labdata

pd = pytest.importorskip("pandas")
pytest.importorskip("openpyxl")


def write_depth_sheet(path, *, prototype=True):
    """The published water-depth layout: title, metadata, then a block per discharge."""
    rows = [
        ["SUMMARY IN PROTOTYPE (1:3) DIMENSIONS" if prototype else "MODEL SCALE",
         None, None, None, None],
        [None, None, None, None, None],
        ["Date:", "April 2022 -- July 2024", "2023*", "2023*", "2023*"],
        ["Experimenters:", "Gerhard Schmid, Federica Scolari", None, None, None],
        [None, None, None, None, None],
        ["Q30 - 60 L/s", "XS1 - US2", "XS2 - US4", "XS3 - US5", "XS4 - US7"],
        ["MEAN (mm)", 147.127321, 250, 250, 270],
        ["STD** (40s in mm)", 0.69, 1.11, 0.77, 0.66],
        [None, None, None, None, None],
        ["MQ - 135 L/s", "XS1 - US2", "XS2 - US4", "XS3 - US5", "XS4 - US7"],
        ["MEAN (mm)", 276.623182, 390, 390, 360],
        ["STD** (40s in mm)", 1.05, 1.31, 1.1, 0.5],
        [None, None, None, None, None],
        ["HQ100 - 1000 L/s", "XS1 - US2", "XS2 - US4", "XS3 - US5", "XS4 - US7"],
        ["MEAN (mm)", 458.395006, 630, 640, 840],
        ["STD** (40s in mm)", 0.8, 1.78, 1.17, 1.53],
        [None, None, None, None, None],
        ["*Data adapted from an unpublishable report", None, None, None, None],
    ]
    pd.DataFrame(rows).to_excel(path, index=False, header=False,
                                sheet_name="water-depth-mm")
    return path


def write_velocity_sheet(path):
    """The published velocity layout, including the gaps the campaign really has."""
    header = ["Discharge", "u time avg. max", "u std", "v time avg. max", "v std",
              "w time avg. max", "w std", "XS", "U time avg. max", "U std",
              "U3d time-avg. max", "U3d std", "u mean"]
    rows = [
        ["ADV measurements at XS 1 were post-processed with", *[None] * 12],
        [None, "XS-maximum", *[None] * 10, "XS-average**"],
        header,
        ["Q30", 0.333965, 0.184231, -0.098153, 0.129035, -0.02101, 0.078404,
         "XS2", 0.3481, 0.2249, 0.2971, 0.2382, 0.2087],
        [None, 0.191884, 0.12844, -0.026443, 0.094479, -0.014088, 0.04604,
         "XS3", 0.1937, 0.1594, 0.1842, 0.166, 0.2087],
        [None, None, None, None, None, None, None,
         "XS1", None, None, "*", None, 0.7691],
        [None, 0.07101, 0.03441, 0.004097, 0.017069, 0.000976, 0.009486,
         "XS4", 0.0711, 0.0384, 0.0688, 0.0396, 0.2149],
        ["MQ", 0.250239, 0.045177, 0.001167, 0.043061, -0.002436, 0.028482,
         "XS4", 0.2502, 0.0624, 0.1304, 0.0686, 0.268],
        [None, 0.526187, 0.094476, -0.009829, 0.062752, -0.00297, 0.038899,
         "XS1", 0.5263, 0.1134, 0.5263, 0.1199, 0.6124],
        ["* No ADV measurement possible (too shallow)", *[None] * 12],
    ]
    pd.DataFrame(rows).to_excel(path, index=False, header=False,
                                sheet_name="flow-velocity")
    return path


# --------------------------------------------------------------------------- #
# depths
# --------------------------------------------------------------------------- #
def test_every_discharge_block_becomes_rows_of_sections(tmp_path):
    rows = labdata.read_depths(write_depth_sheet(tmp_path / "d.xlsx"))

    assert len(rows) == 12                       # 3 discharges x 4 sections
    assert {r["discharge_label"] for r in rows} == {"Q30", "MQ", "HQ100"}
    assert {r["section"] for r in rows} == {"XS1", "XS2", "XS3", "XS4"}


def test_the_discharge_is_parsed_from_the_block_title(tmp_path):
    rows = labdata.read_depths(write_depth_sheet(tmp_path / "d.xlsx"))
    by_label = {r["discharge_label"]: r["discharge"] for r in rows}

    assert by_label == {"Q30": 0.060, "MQ": 0.135, "HQ100": 1.000}


def test_millimetres_become_metres_and_the_station_is_kept(tmp_path):
    rows = labdata.read_depths(write_depth_sheet(tmp_path / "d.xlsx"))
    q30 = {r["section"]: r for r in rows if r["discharge_label"] == "Q30"}

    assert q30["XS2"]["h_mean"] == pytest.approx(0.250)
    assert q30["XS1"]["h_mean"] == pytest.approx(0.147127, abs=1e-6)
    assert q30["XS2"]["h_std"] == pytest.approx(0.00111)
    assert q30["XS1"]["station"] == "US2"        # the pool the section sits in


def test_a_length_scale_converts_a_model_sheet_to_prototype(tmp_path):
    """A 1:3 flume publishes 83 mm where the prototype has 250."""
    path = write_depth_sheet(tmp_path / "model.xlsx", prototype=False)

    as_is = labdata.read_depths(path)
    scaled = labdata.read_depths(path, length_scale=3.0)

    assert scaled[1]["h_mean"] == pytest.approx(3.0 * as_is[1]["h_mean"])


def test_a_sheet_without_discharge_blocks_is_rejected(tmp_path):
    path = tmp_path / "wrong.xlsx"
    pd.DataFrame([["not", "a", "depth", "sheet"]]).to_excel(path, index=False,
                                                            header=False)
    with pytest.raises(ValueError, match="no '<label> - <Q> L/s' blocks"):
        labdata.read_depths(path)


# --------------------------------------------------------------------------- #
# velocities
# --------------------------------------------------------------------------- #
def test_the_discharge_label_carries_down_its_block(tmp_path):
    """It appears only on the first row of each block, as in the published sheet."""
    rows = labdata.read_velocities(write_velocity_sheet(tmp_path / "v.xlsx"))
    labels = [(r["discharge_label"], r["section"]) for r in rows]

    assert labels == [("Q30", "XS2"), ("Q30", "XS3"), ("Q30", "XS1"), ("Q30", "XS4"),
                      ("MQ", "XS4"), ("MQ", "XS1")]


def test_the_section_maxima_are_read_with_their_spread(tmp_path):
    rows = labdata.read_velocities(write_velocity_sheet(tmp_path / "v.xlsx"))
    first = rows[0]

    assert first["u_max"] == pytest.approx(0.3481)
    assert first["u_std"] == pytest.approx(0.2249)
    assert first["u_section_mean"] == pytest.approx(0.2087)


def test_the_resultant_speed_is_not_confused_with_the_u_component(tmp_path):
    """The sheet has both 'u time avg. max' (component) and 'U time avg. max'
    (resultant), differing only by the capital. A modelled speed compares with the
    resultant; taking the component instead is a fifth low where the flow turns."""
    rows = labdata.read_velocities(write_velocity_sheet(tmp_path / "v.xlsx"))
    first = rows[0]

    assert first["u_max"] == pytest.approx(0.3481)          # resultant
    assert first["u_component_max"] == pytest.approx(0.333965)
    assert first["v_component_max"] == pytest.approx(-0.098153)


def test_a_sheet_with_only_components_is_refused(tmp_path):
    header = ["Discharge", "u time avg. max", "u std", "XS"]
    path = tmp_path / "components-only.xlsx"
    pd.DataFrame([header, ["Q30", 0.3, 0.1, "XS1"]]).to_excel(
        path, index=False, header=False)

    with pytest.raises(ValueError, match="no resultant"):
        labdata.read_velocities(path)


def test_a_section_the_adv_could_not_measure_yields_no_value(tmp_path):
    """Too shallow to measure is not the same as measured zero."""
    rows = labdata.read_velocities(write_velocity_sheet(tmp_path / "v.xlsx"))
    unmeasured = next(r for r in rows if r["discharge_label"] == "Q30"
                      and r["section"] == "XS1")

    assert unmeasured["u_max"] is None
    assert unmeasured["u3d_max"] is None
    assert unmeasured["u_section_mean"] == pytest.approx(0.7691)


def test_a_velocity_scale_is_applied_to_every_velocity(tmp_path):
    """Froude similarity scales velocity as sqrt(length), so a 1:3 flume needs 1.732."""
    path = write_velocity_sheet(tmp_path / "v.xlsx")

    as_is = labdata.read_velocities(path)
    scaled = labdata.read_velocities(path, velocity_scale=labdata.froude_velocity_scale(3.0))

    assert labdata.froude_velocity_scale(3.0) == pytest.approx(math.sqrt(3.0))
    assert scaled[0]["u_max"] == pytest.approx(as_is[0]["u_max"] * math.sqrt(3.0))
    assert scaled[0]["u_std"] == pytest.approx(as_is[0]["u_std"] * math.sqrt(3.0))


# --------------------------------------------------------------------------- #
# the joined reference and the comparison
# --------------------------------------------------------------------------- #
def test_the_two_sheets_join_despite_spelling_the_same_discharge_differently(tmp_path):
    """The published depth summary says HQ100 where the velocity summary says HQ. Left
    alone the tables do not join and half the reference data vanishes into unmatched
    rows - the quiet kind of wrong."""
    depth = tmp_path / "d.xlsx"
    pd.DataFrame([
        ["HQ100 - 1000 L/s", "XS1 - US2", "XS2 - US4"],
        ["MEAN (mm)", 458.395006, 630],
        ["STD** (40s in mm)", 0.8, 1.78],
    ]).to_excel(depth, index=False, header=False)
    velocity = tmp_path / "v.xlsx"
    pd.DataFrame([
        ["Discharge", "u time avg. max", "XS", "U time avg. max", "U std"],
        ["HQ", 0.973998, "XS1", 0.9741, 0.305],
        [None, 0.375996, "XS2", 0.3997, 0.4726],
    ]).to_excel(velocity, index=False, header=False)

    table = labdata.reference_table(labdata.read_depths(depth),
                                    labdata.read_velocities(velocity))

    assert len(table) == 2                       # not four unmatched halves
    assert set(table.discharge_label) == {"HQ100"}
    assert table.h_mean.notna().all() and table.u_max.notna().all()


def test_depths_and_velocities_merge_into_one_row_per_section(tmp_path):
    reference = labdata.reference_table(
        labdata.read_depths(write_depth_sheet(tmp_path / "d.xlsx")),
        labdata.read_velocities(write_velocity_sheet(tmp_path / "v.xlsx")))

    q30_xs2 = reference[(reference.discharge_label == "Q30")
                        & (reference.section == "XS2")].iloc[0]
    assert q30_xs2.h_mean == pytest.approx(0.250)
    assert q30_xs2.u_max == pytest.approx(0.3481)
    # a discharge with no velocity rows still keeps its depths
    assert reference[reference.discharge_label == "HQ100"].h_mean.notna().all()


def test_a_comparison_puts_model_and_measurement_side_by_side(tmp_path):
    reference = labdata.reference_table(
        labdata.read_depths(write_depth_sheet(tmp_path / "d.xlsx")))
    reference = reference[reference.discharge_label == "Q30"]
    modelled = [
        {"name": "XS1", "mean_depth": 0.150, "max_velocity": 0.40},
        {"name": "XS2", "mean_depth": 0.240, "max_velocity": 0.35},
        {"name": "XS3", "mean_depth": 0.255, "max_velocity": 0.30},
        {"name": "XS4", "mean_depth": 0.280, "max_velocity": 0.10},
    ]

    table = labdata.compare(reference, modelled, length_scale=3.0,
                            velocity_scale=math.sqrt(3.0))

    xs2 = table[table.section == "XS2"].iloc[0]
    assert xs2.h_error == pytest.approx(-0.010)
    assert xs2.h_relative == pytest.approx(-0.04)
    assert table.attrs["length_scale"] == 3.0


def test_the_written_comparison_records_the_scale_convention(tmp_path):
    """A table read a year later must say which scale its measurements were in."""
    reference = labdata.reference_table(
        labdata.read_depths(write_depth_sheet(tmp_path / "d.xlsx")))
    table = labdata.compare(reference.head(2), [{"name": "XS1", "mean_depth": 0.15}],
                            length_scale=3.0, velocity_scale=math.sqrt(3.0))

    path = labdata.write_comparison(table, tmp_path / "comparison.csv")
    first_line = path.read_text().splitlines()[0]

    assert first_line.startswith("# length scale 3.0, velocity scale 1.7320")
    assert "section" in path.read_text().splitlines()[1]
