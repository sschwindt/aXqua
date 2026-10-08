"""A sequence of jobs for one case, and the script that runs it without QGIS.

The steps are the job kinds of aXqua in the order of the workflow. Submitted from the
panel, they are handed to aXqua one after the other at once: jobs of one case wait for
each other, so the sequence runs by itself and survives closing QGIS. Such a sequence
does not stop when a step fails. The script does: it waits for each job and ends at the
first one that did not complete.

Pure Python, no Qt.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    kind: str                  # the job kind of ``axqua submit --kind``
    title: str
    needs: str = ""            # the capability that has to be asked for by the case
    default: bool = False      # ticked when the list is first shown


#: The steps a case can be taken through, in the order they depend on each other.
STEPS: tuple[Step, ...] = (
    Step("preprocessing", "Build the TELEMAC model", "steady2d", True),
    Step("steady", "Steady 2D simulation", "steady2d", True),
    Step("mesh-convergence", "Mesh convergence study", "mesh_convergence"),
    Step("build-3d", "Build the 3D model", "steady3d"),
    Step("steady-3d", "Steady 3D simulation (hydrostatic)", "steady3d"),
    Step("calibration", "Bayesian calibration", "calibration"),
)


def step(kind: str) -> Step:
    return next(item for item in STEPS if item.kind == kind)


def ordered(kinds) -> list[str]:
    """*kinds* in workflow order, each once, unknown ones dropped."""
    wanted = set(kinds)
    return [item.kind for item in STEPS if item.kind in wanted]


def script(case_file: str, kinds, *, axqua: str = "axqua") -> str:
    """A shell script that runs *kinds* for *case_file* and stops at a failed step."""
    program = shlex.quote(axqua)
    lines = [
        "#!/bin/bash",
        "# Runs a sequence of aXqua jobs for one case and stops when a step fails.",
        "# Written by the aXqua plugin for QGIS. Start it so that it survives the",
        "# terminal:  nohup bash this-script.sh > batch.log 2>&1 &",
        "",
        f"AXQUA={program}",
        f"CASE={shlex.quote(str(case_file))}",
        "",
        "run_step () {",
        '    job=$("$AXQUA" submit "$CASE" --kind "$1" --json | python3 -c '
        "\"import json, sys; print(json.load(sys.stdin)['data']['job_id'])\")",
        '    [ -n "$job" ] || { echo "$1: could not be submitted"; exit 1; }',
        '    "$AXQUA" status "$job" --watch 30 > /dev/null',
        '    state=$("$AXQUA" status "$job" --json | python3 -c '
        "\"import json, sys; print(json.load(sys.stdin)['data']['state'])\")",
        '    echo "$(date +%H:%M:%S) $1: $state ($job)"',
        '    [ "$state" = "COMPLETED" ] || exit 1',
        "}",
        "",
    ]
    lines += [f"run_step {shlex.quote(kind)}" for kind in ordered(kinds)]
    return "\n".join(lines) + "\n"
