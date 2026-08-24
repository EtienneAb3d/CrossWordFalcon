#!/usr/bin/env bash
# Launches the local LLM server used by default for crossword clue
# generation (backend/clues.py), stopping any instance already running on
# the same port first.
#
# vLLM only ships Linux wheels (CUDA GPUs, or CPU on x86/ARM/PowerPC) — see
# requirements-vllm.txt. It runs on GPU automatically when one is available
# (e.g. a 12GB RTX 3060 comfortably fits the default model below) and falls
# back to CPU otherwise (slower, but works).
#
# On macOS, where vLLM has no wheels at all, this launches
# backend/hf_server.py instead — the same model served via transformers on
# Apple Silicon (MPS) or CPU, behind the same /v1/chat/completions shape
# (see requirements-hf.txt). Same port either way, so env.sh needs no change.
set -euo pipefail

cd "$(dirname "$0")"

VLLM_HOST="${VLLM_HOST:-127.0.0.1}"
VLLM_PORT="${VLLM_PORT:-8002}"
VLLM_MODEL="${LLM_MODEL:-Qwen/Qwen2.5-1.5B-Instruct}"
LOG_DIR="logs"
VLLM_LOG="$LOG_DIR/vllm.log"

mkdir -p "$LOG_DIR"

if [ -f env.sh ]; then
    source env.sh
    VLLM_MODEL="${LLM_MODEL:-$VLLM_MODEL}"
fi

pids=$(lsof -ti tcp:"$VLLM_PORT" 2>/dev/null || true)
if [ -n "$pids" ]; then
    echo "Stopping LLM server already running on port $VLLM_PORT (pid: $pids)"
    kill $pids 2>/dev/null || true
    sleep 1
    pids=$(lsof -ti tcp:"$VLLM_PORT" 2>/dev/null || true)
    [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
fi

source .venv/bin/activate
export LLM_MODEL="$VLLM_MODEL"

if [ "$(uname -s)" = "Darwin" ]; then
    echo "macOS detected — vLLM has no macOS wheels, using the transformers-based"
    echo "fallback server (backend/hf_server.py) instead. Needs: pip install -r requirements-hf.txt"
    echo "Starting LLM server: model=$VLLM_MODEL, port=$VLLM_PORT (Apple Silicon MPS or CPU)"
    nohup uvicorn backend.hf_server:app --host "$VLLM_HOST" --port "$VLLM_PORT" \
        > "$VLLM_LOG" 2>&1 &
    VLLM_PID=$!
else
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
        echo "NVIDIA GPU detected — vLLM will use it automatically."
    else
        echo "No NVIDIA GPU detected — vLLM will run on CPU (slower)."
    fi
    echo "Starting vLLM server: model=$VLLM_MODEL, port=$VLLM_PORT"
    nohup vllm serve "$VLLM_MODEL" --host "$VLLM_HOST" --port "$VLLM_PORT" \
        > "$VLLM_LOG" 2>&1 &
    VLLM_PID=$!
fi

echo "LLM server started (pid $VLLM_PID, log: $VLLM_LOG)"
echo "First start downloads the model from Hugging Face — this can take a while."
echo "Endpoint: http://$VLLM_HOST:$VLLM_PORT/v1/chat/completions"
