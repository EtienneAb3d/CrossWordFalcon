#!/usr/bin/env bash
#
# run_Populate.sh — start / stop / restart the automatic library populator
# (Automation/Populate.py), which generates grids one at a time and lets the
# back end save each finished one into the library.
#
# Populate is a plain background Python process, not a port listener, so
# run_Falcon.sh's port-based stop logic never touches it. This script finds
# it by its own PID file (logs/populate.pid), falling back to a precise
# pgrep, and never signals anything else.
#
# Usage:
#   ./run_Populate.sh status              # is it running? (default action)
#   ./run_Populate.sh start [args...]     # launch detached; args go to Populate.py
#   ./run_Populate.sh stop                # graceful stop, then escalate if needed
#   ./run_Populate.sh restart [args...]   # stop, then start with the given args
#
# Examples:
#   ./run_Populate.sh start
#   ./run_Populate.sh start --count 200 --language fr --difficulty easy
#   ./run_Populate.sh restart --mode turbo
#
set -euo pipefail

cd "$(dirname "$0")"

LOG_DIR="logs"
LOG_FILE="$LOG_DIR/populate.log"
PID_FILE="$LOG_DIR/populate.pid"
PY=".venv/bin/python"
SCRIPT="Automation/Populate.py"

# How long to wait for a graceful (finish-current-grid) stop before asking
# Populate to exit immediately, then before a hard kill.
STOP_GRACE_SECONDS="${POPULATE_STOP_GRACE:-20}"
FORCE_GRACE_SECONDS=5

mkdir -p "$LOG_DIR"

# --- find the running Populate process -----------------------------------

# Echoes the PID of a live Populate process, or nothing. Prefers the PID
# file; falls back to a pgrep anchored on the python interpreter so it can
# also find an instance started by hand.
populate_pid() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid="$(cat "$PID_FILE" 2>/dev/null || true)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            # Confirm it is really our script and not a recycled PID.
            if tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "$SCRIPT"; then
                echo "$pid"
                return 0
            fi
        fi
        rm -f "$PID_FILE"
    fi
    pgrep -f "python[0-9.]* .*${SCRIPT}" 2>/dev/null | head -n1 || true
}

# --- actions ------------------------------------------------------------

do_status() {
    local pid
    pid="$(populate_pid)"
    if [ -n "$pid" ]; then
        echo "Populate is running (pid $pid)."
        echo "  command: $(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
        echo "  log:     $LOG_FILE"
        if [ -f "$LOG_FILE" ]; then
            echo "  --- last lines ---"
            tail -n 5 "$LOG_FILE" | sed 's/^/  /'
        fi
        return 0
    fi
    echo "Populate is not running."
    return 0
}

do_stop() {
    local pid
    pid="$(populate_pid)"
    if [ -z "$pid" ]; then
        echo "Populate is not running — nothing to stop."
        rm -f "$PID_FILE"
        return 0
    fi

    echo "Stopping Populate (pid $pid) — asking it to finish the current grid first..."
    kill -INT "$pid" 2>/dev/null || true

    local waited=0
    while [ "$waited" -lt "$STOP_GRACE_SECONDS" ]; do
        kill -0 "$pid" 2>/dev/null || { echo "Stopped."; rm -f "$PID_FILE"; return 0; }
        sleep 1
        waited=$((waited + 1))
    done

    echo "Still running after ${STOP_GRACE_SECONDS}s — asking it to exit immediately..."
    kill -INT "$pid" 2>/dev/null || true
    waited=0
    while [ "$waited" -lt "$FORCE_GRACE_SECONDS" ]; do
        kill -0 "$pid" 2>/dev/null || { echo "Stopped."; rm -f "$PID_FILE"; return 0; }
        sleep 1
        waited=$((waited + 1))
    done

    echo "Forcing (SIGKILL)..."
    kill -KILL "$pid" 2>/dev/null || true
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        echo "WARNING: pid $pid is still alive." >&2
        return 1
    fi
    echo "Stopped."
    rm -f "$PID_FILE"
    return 0
}

do_start() {
    local pid
    pid="$(populate_pid)"
    if [ -n "$pid" ]; then
        echo "Populate is already running (pid $pid). Use 'restart' to relaunch." >&2
        return 1
    fi

    if [ ! -x "$PY" ]; then
        echo "Error: $PY not found — run ./Install.sh first." >&2
        return 1
    fi

    source .venv/bin/activate
    if [ -f env.sh ]; then
        source env.sh
    elif [ -f env_default.sh ]; then
        source env_default.sh
    fi

    # Keep the previous run's log for inspection.
    [ -f "$LOG_FILE" ] && mv -f "$LOG_FILE" "$LOG_FILE.1"

    # nohup + </dev/null + disown so it survives this shell/terminal closing
    # (same idiom as run_Falcon.sh).
    nohup "$PY" "$SCRIPT" "$@" < /dev/null > "$LOG_FILE" 2>&1 &
    local new_pid=$!
    disown "$new_pid" 2>/dev/null || true
    echo "$new_pid" > "$PID_FILE"

    sleep 1
    if kill -0 "$new_pid" 2>/dev/null; then
        echo "Populate started (pid $new_pid, log: $LOG_FILE)."
        [ "$#" -gt 0 ] && echo "  args: $*"
    else
        echo "Populate exited immediately — check $LOG_FILE:" >&2
        tail -n 20 "$LOG_FILE" | sed 's/^/  /' >&2
        rm -f "$PID_FILE"
        return 1
    fi
}

# --- dispatch ---------------------------------------------------------------

action="${1:-status}"
[ "$#" -gt 0 ] && shift || true

case "$action" in
    status)  do_status ;;
    start)   do_start "$@" ;;
    stop)    do_stop ;;
    restart) do_stop && do_start "$@" ;;
    -h|--help|help)
        sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
        ;;
    *)
        echo "Unknown action: $action" >&2
        echo "Usage: $0 {status|start [args...]|stop|restart [args...]}" >&2
        exit 2
        ;;
esac
