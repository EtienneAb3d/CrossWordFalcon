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
nohup uvicorn backend.app:app --port "$BACKEND_PORT" > "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

echo "Starting middleware on port $FRONTEND_PORT..."
nohup uvicorn frontend.server:app --port "$FRONTEND_PORT" > "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!

echo "Back end started (pid $BACKEND_PID, log: $BACKEND_LOG)"
echo "Middleware started (pid $FRONTEND_PID, log: $FRONTEND_LOG)"
echo "UI available at http://127.0.0.1:$FRONTEND_PORT"
echo "To stop the servers, rerun this script or run: kill $BACKEND_PID $FRONTEND_PID"
echo
echo "Note: clue generation uses the local LLM server by default (see env.sh)."
echo "If it isn't running yet, start it with: ./run_llm.sh"
