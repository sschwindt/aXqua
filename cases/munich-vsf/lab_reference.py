"""Fetch the published flume campaign and build the section reference table.

The Munich vertical-slot-fishway experiments are published at
https://zenodo.org/records/14440623 (BSD-3, Schwindt et al.). Only
``physical-flume/`` matters here: a water-depth summary and a velocity summary, four
cross-sections per discharge at Q30 (60 L/s), MQ (135 L/s) and HQ100 (1000 L/s).

Run it once to place the two workbooks under ``user-sources/ground-truth/`` and write
the merged reference table next to them::

    python lab_reference.py                 # download if missing, then build the table
    python lab_reference.py --offline       # use the workbooks already downloaded

The result is **cross-section aggregates**, not point measurements, which is why they
are not fed through the calibration-points CSV. The model side comes from
:func:`axqua.solvers.telemac.sections.line_discharges` over the same section lines, and
:func:`axqua.labdata.compare` puts the two together - see ``compare_sections.py``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import urllib.request
from pathlib import Path

from axqua import labdata
from axqua.logsetup import setup_logging

HERE = Path(__file__).resolve().parent
GROUND_TRUTH = HERE / "user-sources" / "ground-truth"

#: the two files of the record that this case uses, and where they come from
SOURCES = {
    "physical-lab-water-depth.xlsx":
        "https://raw.githubusercontent.com/sschwindt/schwindt-etal-UE-flip-SI/"
        "main/physical-flume/physical-lab-water-depth.xlsx",
    "physical-lab-velocities.xlsx":
        "https://raw.githubusercontent.com/sschwindt/schwindt-etal-UE-flip-SI/"
        "main/physical-flume/physical-lab-velocities.xlsx",
}

# --------------------------------------------------------------------------- #
# Scale: BOTH sheets are already at prototype scale. The depth summary says so in its
# title ("SUMMARY IN PROTOTYPE (1:3) DIMENSIONS") and the velocity summary, which says
# nothing, follows the same convention throughout the campaign - confirmed by the
# dataset's author. So neither quantity is converted here: the numbers are compared
# directly against a prototype-scale model, and the Froude velocity factor sqrt(3) is
# NOT applied. Both scales stay explicit rather than implicit precisely because that
# is the sort of thing nobody can reconstruct from the files a year later.
# --------------------------------------------------------------------------- #
LENGTH_SCALE = 1.0            # depths are published in prototype metres
VELOCITY_SCALE = 1.0          # velocities likewise - do NOT apply sqrt(3)


def fetch(offline: bool = False) -> dict[str, Path]:
    GROUND_TRUTH.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, url in SOURCES.items():
        path = GROUND_TRUTH / name
        if not path.exists():
            if offline:
                raise SystemExit(f"{path} is missing and --offline was given")
            logging.getLogger("axqua").info("downloading %s", name)
            urllib.request.urlretrieve(url, path)
        out[name] = path
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true",
                        help="do not download; use what is already in ground-truth/")
    args = parser.parse_args(argv)
    setup_logging(level=logging.INFO)
    log = logging.getLogger("axqua")

    files = fetch(offline=args.offline)
    table = labdata.reference_table(
        labdata.read_depths(files["physical-lab-water-depth.xlsx"],
                            length_scale=LENGTH_SCALE),
        labdata.read_velocities(files["physical-lab-velocities.xlsx"],
                                velocity_scale=VELOCITY_SCALE))

    out = GROUND_TRUTH / "section-reference.csv"
    table.to_csv(out, index=False)
    log.info("wrote %s", out)
    log.info("\n%s", table.to_string(index=False))
    log.info("both quantities are at prototype scale as published; no Froude "
             "conversion applied (see the scale note at the top of this script)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
