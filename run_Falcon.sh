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

stop_port "$BACKEND_PORT"
stop_port "$FRONTEND_PORT"
if [ "$HTTPS_ENABLED" -eq 1 ]; then
    stop_port "$FRONTEND_HTTPS_PORT"
fi

echo "Starting back end on port $BACKEND_PORT..."
# `nohup` alone only ignores SIGHUP — it doesn't detach from the shell's job
# table, so some shells/terminals still signal it on exit. `disown` removes
# it from that table too, and stdin is redirected from /dev/null since a
# fully detached process has no legitimate terminal to read from — together
# this is what lets the server keep running after the launching shell/
# terminal closes, not just across a background `&`.
nohup uvicorn backend.app:app --port "$BACKEND_PORT" < /dev/null > "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
disown "$BACKEND_PID"

echo "Starting middleware on port $FRONTEND_PORT..."
# --host 0.0.0.0 so the UI is reachable from other machines on the network,
# not just from this one. The back end ($BACKEND_PORT) stays on 127.0.0.1
# only — it's an internal implementation detail, browsers only ever talk to
# the middleware (see CLAUDE.md).
nohup uvicorn frontend.server:app --host 0.0.0.0 --port "$FRONTEND_PORT" < /dev/null > "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
disown "$FRONTEND_PID"

FRONTEND_HTTPS_PID=""
if [ "$HTTPS_ENABLED" -eq 1 ]; then
    echo "Starting middleware (HTTPS) on port $FRONTEND_HTTPS_PORT..."
    # Same app, same host binding — just this instance terminates TLS with
    # the Let's Encrypt cert. --ssl-certfile is the full chain (leaf +
    # intermediates), --ssl-keyfile the private key.
    nohup uvicorn frontend.server:app --host 0.0.0.0 --port "$FRONTEND_HTTPS_PORT" \
        --ssl-certfile "$TLS_CERTFILE" --ssl-keyfile "$TLS_KEYFILE" \
        < /dev/null > "$LOG_DIR/frontend-https.log" 2>&1 &
    FRONTEND_HTTPS_PID=$!
    disown "$FRONTEND_HTTPS_PID"
fi

LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || (hostname -I 2>/dev/null | awk '{print $1}') || true)

echo "Back end started (pid $BACKEND_PID, log: $BACKEND_LOG)"
echo "Middleware started (pid $FRONTEND_PID, log: $FRONTEND_LOG)"
echo "UI available at http://127.0.0.1:$FRONTEND_PORT (this machine)"
if [ -n "$LAN_IP" ]; then
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
