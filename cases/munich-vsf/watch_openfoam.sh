#!/usr/bin/env bash
# Keep a record of the interFoam run while nobody is watching.
#
# The sibling of watch_run.sh, for the other solver. A VOF run of this case is days,
# and the things worth knowing all happen between sessions: the time step settling,
# the hand-off from the spin-up to the production stage, the discharge coming into
# balance - or the air phase collapsing the step, which is what a two-phase run does
# when it goes wrong and is invisible from the outside until the clock stops moving.
#
# Appends a timestamped line every INTERVAL seconds to openfoam-watch.log:
#
#   <state> | stage | t=<simulated> | dt | Co max | s/step | rate | ETA
#
#   nohup ./watch_openfoam.sh > /dev/null 2>&1 &
#   tail -f axqua-case/openfoam/openfoam-watch.log
#
# It never touches the run. Stop it with: pkill -f watch_openfoam.sh

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OF="$HERE/axqua-case/openfoam"
RUN="$OF/run-of.log"
LOG="$OF/openfoam-watch.log"
INTERVAL="${INTERVAL:-1800}"          # 30 minutes

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }

# The stage end times, so the ETA is against the stage actually running. A rigid-lid
# case has no interface to settle and therefore no spin-up: dicts.stages() emits one
# stage and only stage2-run/ is written, so SPINUP_END is empty and every t is past it.
SPINUP_END=$(grep -E "^endTime" "$OF/system/stage1-spinup/controlDict" 2>/dev/null \
             | awk '{print $2}' | tr -d ';')
RUN_END=$(grep -E "^endTime" "$OF/system/stage2-run/controlDict" 2>/dev/null \
          | awk '{print $2}' | tr -d ';')

if [ -n "${SPINUP_END:-}" ]; then
    log "watch started (interval ${INTERVAL}s, spin-up to ${SPINUP_END} s, run to ${RUN_END:-?} s)"
else
    log "watch started (interval ${INTERVAL}s, single stage to ${RUN_END:-?} s - no spin-up, so this is a rigid-lid case)"
fi
prev_t=""; prev_wall=""

while true; do
    if pgrep -x interFoam > /dev/null 2>&1; then
        state="running ($(pgrep -c -x interFoam) ranks)"
    else
        state="NOT RUNNING"
    fi

    t=$(grep -E "^Time = " "$RUN" 2>/dev/null | tail -1 | awk '{print $3}')
    dt=$(grep -E "^deltaT" "$RUN" 2>/dev/null | tail -1 | awk '{print $3}')
    co=$(grep -E "^Courant Number mean" "$RUN" 2>/dev/null | tail -1 | sed 's/.*max: //')
    wall=$(grep "ExecutionTime" "$RUN" 2>/dev/null | tail -1 | awk '{print $3}')

    # Rate and ETA from the interval since the last look, not from the run's whole
    # history: the startup transient is cheap and the settled march is not, so an
    # average over both flatters the estimate for as long as anyone is watching.
    rate=""; eta=""
    if [ -n "${t:-}" ] && [ -n "${prev_t:-}" ] && [ -n "${wall:-}" ] \
       && [ -n "${prev_wall:-}" ]; then
        rate=$(awk -v a="$t" -v b="$prev_t" -v c="$wall" -v d="$prev_wall" \
               'BEGIN{ if (c>d) printf "%.3e", (a-b)/(c-d) }')
        target=$(awk -v x="$t" -v s="${SPINUP_END:-0}" -v r="${RUN_END:-0}" \
                 'BEGIN{ print (x < s) ? s : r }')
        if [ -n "$rate" ]; then
            eta=$(awk -v x="$t" -v g="$target" -v r="$rate" \
                  'BEGIN{ if (r>0) printf "%.1f d to t=%g s", (g-x)/r/86400, g }')
        fi
    fi
    prev_t="$t"; prev_wall="$wall"

    log "$state | t=${t:-?} s | dt=${dt:-?} | Co=${co:-?} | wall=${wall:-?} s | rate=${rate:-?} s/s | ${eta:-eta pending}"

    if [ "$state" = "NOT RUNNING" ]; then
        # Finished or died - either way the next line is the last, and which of the
        # two it was is in run-of.log rather than guessed at here.
        if grep -qE "^End$|Finalising" "$RUN" 2>/dev/null; then
            log "solver reported End. watch stopping"
        else
            log "solver is gone without reporting End - see the tail of run-of.log"
        fi
        exit 0
    fi
    sleep "$INTERVAL"
done
