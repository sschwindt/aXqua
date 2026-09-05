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
    # Matches the initial run and any continuation; [t] stops the pattern matching
    # this script's own pgrep, which reported a finished run as still going.
    if pgrep -f "[t]elemac2d.py .*\.cas" > /dev/null 2>&1; then
        state="running"
    else
        state="NOT RUNNING"
    fi

    # how far the march has got, from the streamed listing
    LIVE="$SIM/run-continue.log"
    [ -f "$LIVE" ] || LIVE="$SIM/run-full.log"
    progress=$(grep -E "TIME:" "$LIVE" 2>/dev/null | tail -1 | tr -s ' ')
    # the last discharge printout per boundary
    fluxes=$(grep "FLUX BOUNDARY" "$LIVE" 2>/dev/null | tail -2 \
             | sed 's/^ *//' | tr '\n' ' ')
    log "$state | ${progress:-no listing yet} | ${fluxes:-no flux printout yet}"

    # the fields can only be judged once TELEMAC has merged the parallel result
    RESULT="r2d-hotstart.slf"
    [ -f "$SIM/$RESULT" ] || RESULT="r2d.slf"
    # Only once the solver has stopped. In a parallel run TELEMAC merges the result
    # file at the very end, so while one is marching there is nothing new to read -
    # and re-reading every frame of a half-gigabyte file each tick cost two minutes
    # of CPU to re-report the *previous* run's verdict.
    if [ "$state" = "NOT RUNNING" ] && [ -f "$SIM/$RESULT" ]; then
        # "Qin" catches the mass-balance line, which the old pattern missed: it says
        # "|Qin| - |Qout| = ...", never the word "imbalance", so the one number that
        # explains a non-converged run was filtered out of every log entry.
        verdict=$("$PYTHON" "$HERE/check_convergence.py" --result "$RESULT" 2>&1 \
                  | grep -E "last change|window mean|fluctuates|VERDICT|Qin|boundary [0-9]" \
                  | sed 's/^.*axqua | //' | tr '\n' ' | ')
        log "CHECK: $verdict"
        case "$verdict" in
            *"VERDICT: converged"*) log "converged - watch stopping" ;;
            *) log "solver has finished but has not converged; see the check above" ;;
        esac
        exit 0
    fi

    sleep "$INTERVAL"
done
