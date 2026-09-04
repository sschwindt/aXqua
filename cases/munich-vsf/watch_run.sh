#!/usr/bin/env bash
# Keep a record of a long steady run while nobody is watching.
#
# A fish-pass run at this resolution takes hours to weeks, so the interesting events -
# the reach finishing filling, the fluxes coming into balance, the fields going still,
# or the solver quietly dying - all happen between sessions. This appends a timestamped
# line every INTERVAL seconds to convergence-watch.log: how far the run has got, what
# the boundary discharges are doing, and (once the result file has been merged) the
# convergence verdict for depth, velocity, TKE and discharge.
#
#   nohup ./watch_run.sh > /dev/null 2>&1 &
#   tail -f axqua-case/simulation/convergence-watch.log
#
# It never touches the run. Stop it with: pkill -f watch_run.sh

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIM="$HERE/axqua-case/simulation"
LOG="$SIM/convergence-watch.log"
PYTHON="${AXQUA_PYTHON:-/home/IWS/schwindt/miniforge3/bin/python3}"
INTERVAL="${INTERVAL:-1800}"          # 30 minutes

export PYTHONPATH="/srv/private/axqua/src:${PYTHONPATH:-}"

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }

log "watch started (interval ${INTERVAL}s)"
while true; do
    if pgrep -f "telemac2d.py steady2d" > /dev/null 2>&1; then
        state="running"
    else
        state="NOT RUNNING"
    fi

    # how far the march has got, from the streamed listing
    progress=$(grep -E "TIME:" "$SIM/run-full.log" 2>/dev/null | tail -1 | tr -s ' ')
    # the last discharge printout per boundary
    fluxes=$(grep "FLUX BOUNDARY" "$SIM/run-full.log" 2>/dev/null | tail -2 \
             | sed 's/^ *//' | tr '\n' ' ')
    log "$state | ${progress:-no listing yet} | ${fluxes:-no flux printout yet}"

    # the fields can only be judged once TELEMAC has merged the parallel result
    if [ -f "$SIM/r2d.slf" ]; then
        verdict=$("$PYTHON" "$HERE/check_convergence.py" 2>&1 \
                  | grep -E "last change|VERDICT|imbalance|boundary [0-9]" \
                  | sed 's/^.*axqua | //' | tr '\n' ' | ')
        log "CHECK: $verdict"
        case "$verdict" in
            *"VERDICT: converged"*) log "converged - watch stopping"; exit 0 ;;
        esac
    fi

    if [ "$state" = "NOT RUNNING" ] && [ -f "$SIM/r2d.slf" ]; then
        log "solver has finished; final check above. watch stopping"
        exit 0
    fi
    sleep "$INTERVAL"
done
