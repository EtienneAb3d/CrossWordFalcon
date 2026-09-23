#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

LOG_DIR="logs"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

mkdir -p "$LOG_DIR"

source .venv/bin/activate

if [ -f env.sh ]; then
    source env.sh
else
    echo "Warning: env.sh missing, LLM_* variables not set (clue generation will fail)."
fi

# Ports are configured in one place — env.sh (or env_default.sh) — see its
# own comment there; these fallbacks only matter if neither was sourced at
# all (env.sh missing and env_default.sh absent too, which shouldn't happen
# on a normal checkout).
BACKEND_PORT="${CROSSWORDFALCON_BACKEND_PORT:-3001}"
FRONTEND_PORT="${CROSSWORDFALCON_FRONTEND_PORT:-3000}"
# Interface the middleware (both HTTP and HTTPS instances) binds to:
# 0.0.0.0 makes the UI reachable from other machines, 127.0.0.1 keeps it
# local to this machine (e.g. a development checkout running next to the
# public one).
FRONTEND_HOST="${CROSSWORDFALCON_FRONTEND_HOST:-0.0.0.0}"

# Number of uvicorn worker processes for the MIDDLEWARE (front) server
# only. Safe there: frontend/server.py is a stateless proxy + static-file
# server (a fresh httpx.AsyncClient per request, no cross-request state,
# no startup scheduler), so N independent workers just add connection-
# accept capacity. The BACK end deliberately stays single-process (no
# --workers): its JOBS/CANCEL_EVENTS/GRID_QUEUE/CLUES_QUEUE state and its
# _rss_daily_scheduler live in one process's memory and cannot be shared
# across workers — see backend/app.py's own comment and CLAUDE.md.
FRONTEND_WORKERS="${CROSSWORDFALCON_FRONTEND_WORKERS:-10}"

# Optional HTTPS front end (see env.sh / env_default.sh). A SECOND uvicorn
# instance for the same frontend.server:app, terminating TLS itself on a
# high port (3443 by default — no privileged bind, no Apache), alongside
# the plain-HTTP instance above which is left exactly as-is. Enabled only
# when both PEM files are actually readable; otherwise this whole block is
# a no-op and run_Falcon.sh behaves exactly as before.
FRONTEND_HTTPS_PORT="${CROSSWORDFALCON_FRONTEND_HTTPS_PORT:-3443}"
TLS_CERTFILE="${CROSSWORDFALCON_TLS_CERTFILE:-}"
TLS_KEYFILE="${CROSSWORDFALCON_TLS_KEYFILE:-}"
HTTPS_ENABLED=0
if [ -n "$TLS_CERTFILE" ] && [ -n "$TLS_KEYFILE" ] \
   && [ -r "$TLS_CERTFILE" ] && [ -r "$TLS_KEYFILE" ]; then
    HTTPS_ENABLED=1
elif [ -n "$TLS_CERTFILE$TLS_KEYFILE" ]; then
    echo "Warning: TLS cert/key configured but not readable — HTTPS front end disabled."
    echo "         cert: ${TLS_CERTFILE:-<unset>}"
    echo "         key : ${TLS_KEYFILE:-<unset>}"
fi

# Kills a PID's entire process tree (its children first, recursively, then
# the PID itself) instead of just the PID alone. Needed because a backend
# process stopped mid-generation can have live `ProcessPoolExecutor`
# worker processes (backend/crossword_gen.py's `_pattern_attempt`/
# `_pattern_continue`, one OS process per PARALLEL_ATTEMPTS, plus a
# `resource_tracker` helper) still running as its own direct children —
# `kill $pid` alone only ever terminates the uvicorn process itself, since
# each `generate_grid()` call creates its own `ProcessPoolExecutor` deep
# inside the palier loop rather than holding one at the app level for a
# shutdown handler to reach; a killed process never gets to run its own
# `with ProcessPoolExecutor(...) as executor:` cleanup (`executor.
# shutdown()`), so those workers are silently orphaned — reparented to
# launchd (PPID 1) — and keep running forever, since nothing in their own
# CSP search loop (`Filler._backtrack`) checks "is my parent still
# alive", only `cancel_event`/`batch_abandoned_event`/`deadline_checks`,
# none of which are ever set once orphaned. Found live, reported by the
# user ("le process qui tourne encore et qui ne s'est pas correctement
# interrompu au redémarrage du Back"): 436 such orphaned processes had
# silently accumulated across this project's entire development history
# (going back several days), none ever reaped by any previous restart.
kill_tree() {
    local pid="$1"
    local sig="$2"
    local child
    for child in $(pgrep -P "$pid" 2>/dev/null || true); do
        kill_tree "$child" "$sig"
    done
    kill "-$sig" "$pid" 2>/dev/null || true
}

stop_port() {
    local port="$1"
    local pids pid
    # -sTCP:LISTEN so this only ever matches the SERVER listening on the
    # port, never a local CLIENT with an open connection to it. Without
    # it, `lsof -ti tcp:PORT` also returns any process mid-request to the
    # port (a browser, a curl, Automation/Populate.py's polling loop) and
    # stop_port() would SIGTERM it too — which is exactly what silently
    # killed a long Populate run once, mid-poll, on a routine restart.
    pids=$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "Stopping server already running on port $port (pid: $pids)"
        for pid in $pids; do
            kill_tree "$pid" TERM
        done
        sleep 1
        # kill -9 (the whole tree again, not just the PID) any survivors
        # -sTCP:LISTEN so this only ever matches the SERVER listening on the
    # port, never a local CLIENT with an open connection to it. Without
    # it, `lsof -ti tcp:PORT` also returns any process mid-request to the
    # port (a browser, a curl, Automation/Populate.py's polling loop) and
    # stop_port() would SIGTERM it too — which is exactly what silently
    # killed a long Populate run once, mid-poll, on a routine restart.
    pids=$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true)
        if [ -n "$pids" ]; then
            for pid in $pids; do
                kill_tree "$pid" KILL
            done
        fi
    fi
}

# Build the jar first (no-op when already up to date), before stopping
# anything: a build failure then leaves whatever is currently running
# untouched.
BACKEND_JAR="backend_java/target/crosswordfalcon-backend.jar"
backend_java/build.sh
JAVA_BIN="$(backend_java/build.sh --print-java)"

stop_port "$BACKEND_PORT"
stop_port "$FRONTEND_PORT"
if [ "$HTTPS_ENABLED" -eq 1 ]; then
    stop_port "$FRONTEND_HTTPS_PORT"
fi

# The Python back end (run_Falcon.sh) is the same server in another
# language: stop any instance of it started from THIS checkout, even on a
# port other than $BACKEND_PORT, so only one back end version runs at a
# time. Scoped to this checkout's own directory (the process's working
# directory), never to every matching process on the machine — a second
# checkout (e.g. the production one) keeps running untouched.
process_cwd() {
    readlink "/proc/$1/cwd" 2>/dev/null \
        || lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p'
}

# $1: command-line pattern; $2: regex the process's executable name must
# fully match — so a shell or an editor whose own command line merely
# contains the pattern text is never mistaken for the server.
stop_checkout_processes() {
    local pattern="$1" exe_regex="$2" here pid exe
    here="$(pwd -P)"
    for pid in $(pgrep -f "$pattern" 2>/dev/null || true); do
        [ "$pid" = "$$" ] && continue
        exe="$(ps -o comm= -p "$pid" 2>/dev/null || true)"
        exe="${exe##*/}"
        [[ "$exe" =~ ^($exe_regex)$ ]] || continue
        if [ "$(process_cwd "$pid")" = "$here" ]; then
            echo "Stopping other back end version (pid $pid: $pattern)"
            kill_tree "$pid" TERM
            sleep 1
            kill -0 "$pid" 2>/dev/null && kill_tree "$pid" KILL
        fi
    done
    return 0
}

stop_checkout_processes "uvicorn backend.app:app" "uvicorn|python[0-9.]*"

echo "Starting Java back end on port $BACKEND_PORT..."
# Same detached launch as the Python back end (nohup + disown + stdin from
# /dev/null). Single JVM process, like the Python back end: JOBS, the
# GRID/CLUES queues and the schedulers live in its memory. The parallel
# grid-search attempts run as threads inside it (CROSSWORDFALCON_PARALLEL_
# ATTEMPTS), so stopping this one process stops every attempt.
# CROSSWORDFALCON_JAVA_OPTS (env.sh) passes extra JVM options (e.g. -Xmx8g).
# shellcheck disable=SC2086
nohup "$JAVA_BIN" ${CROSSWORDFALCON_JAVA_OPTS:-} -jar "$BACKEND_JAR" --port "$BACKEND_PORT" \
    < /dev/null > "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
disown "$BACKEND_PID"

echo "Starting middleware on port $FRONTEND_PORT..."
# --host $FRONTEND_HOST (0.0.0.0 by default) so the UI is reachable from
# other machines on the network, not just from this one. The back end
# ($BACKEND_PORT) stays on 127.0.0.1 only — it's an internal implementation
# detail, browsers only ever talk to the middleware (see CLAUDE.md).
nohup uvicorn frontend.server:app --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" \
    --workers "$FRONTEND_WORKERS" < /dev/null > "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
disown "$FRONTEND_PID"

FRONTEND_HTTPS_PID=""
if [ "$HTTPS_ENABLED" -eq 1 ]; then
    echo "Starting middleware (HTTPS) on port $FRONTEND_HTTPS_PORT..."
    # Same app, same host binding — just this instance terminates TLS with
    # the Let's Encrypt cert. --ssl-certfile is the full chain (leaf +
    # intermediates), --ssl-keyfile the private key.
    nohup uvicorn frontend.server:app --host "$FRONTEND_HOST" --port "$FRONTEND_HTTPS_PORT" \
        --workers "$FRONTEND_WORKERS" \
        --ssl-certfile "$TLS_CERTFILE" --ssl-keyfile "$TLS_KEYFILE" \
        < /dev/null > "$LOG_DIR/frontend-https.log" 2>&1 &
    FRONTEND_HTTPS_PID=$!
    disown "$FRONTEND_HTTPS_PID"
fi

LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || (hostname -I 2>/dev/null | awk '{print $1}') || true)

echo "Java back end started (pid $BACKEND_PID, log: $BACKEND_LOG)"
echo "Middleware started (pid $FRONTEND_PID, log: $FRONTEND_LOG)"
echo "UI available at http://127.0.0.1:$FRONTEND_PORT (this machine)"
if [ -n "$LAN_IP" ] && [ "$FRONTEND_HOST" != "127.0.0.1" ]; then
    echo "               and http://$LAN_IP:$FRONTEND_PORT (from other machines on the network)"
fi
if [ -n "$FRONTEND_HTTPS_PID" ]; then
    echo "Middleware (HTTPS) started (pid $FRONTEND_HTTPS_PID, log: $LOG_DIR/frontend-https.log)"
    echo "               and https://127.0.0.1:$FRONTEND_HTTPS_PORT / https://falcon.cubaix.com:$FRONTEND_HTTPS_PORT"
fi
echo "To stop the servers, rerun this script or run: kill $BACKEND_PID $FRONTEND_PID${FRONTEND_HTTPS_PID:+ $FRONTEND_HTTPS_PID}"
echo
echo "Note: clue generation uses the local LLM server by default (see env.sh)."
echo "If it isn't running yet, start it with: ./run_llm.sh"
