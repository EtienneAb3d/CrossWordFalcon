#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

BACKEND_PORT=8001
FRONTEND_PORT=8000
LOG_DIR="logs"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

mkdir -p "$LOG_DIR"

stop_port() {
    local port="$1"
    local pids
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "Stopping server already running on port $port (pid: $pids)"
        kill $pids 2>/dev/null || true
        sleep 1
        # kill -9 any survivors
        pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
        [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
    fi
}

stop_port "$BACKEND_PORT"
stop_port "$FRONTEND_PORT"

source .venv/bin/activate

if [ -f env.sh ]; then
    source env.sh
else
    echo "Warning: env.sh missing, LLM_* variables not set (clue generation will fail)."
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
# not just from this one. The back end (port 8001) stays on 127.0.0.1 only —
# it's an internal implementation detail, browsers only ever talk to the
# middleware (see CLAUDE.md).
nohup uvicorn frontend.server:app --host 0.0.0.0 --port "$FRONTEND_PORT" < /dev/null > "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
disown "$FRONTEND_PID"

LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || (hostname -I 2>/dev/null | awk '{print $1}') || true)

echo "Back end started (pid $BACKEND_PID, log: $BACKEND_LOG)"
echo "Middleware started (pid $FRONTEND_PID, log: $FRONTEND_LOG)"
echo "UI available at http://127.0.0.1:$FRONTEND_PORT (this machine)"
if [ -n "$LAN_IP" ]; then
    echo "               and http://$LAN_IP:$FRONTEND_PORT (from other machines on the network)"
fi
echo "To stop the servers, rerun this script or run: kill $BACKEND_PID $FRONTEND_PID"
echo
echo "Note: clue generation uses the local LLM server by default (see env.sh)."
echo "If it isn't running yet, start it with: ./run_llm.sh"
