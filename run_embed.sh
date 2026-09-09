#!/usr/bin/env bash
# Launches the local multilingual text-embedding server used by
# backend/embedder.py (the Embedder class): llama.cpp's built-in
# OpenAI-compatible server (llama_cpp.server) in --embedding mode, serving
# a small multilingual GGUF embedding model.
#
# Default model: BAAI/bge-m3 (Q4_K_M GGUF, ~418 MB, 1024-dim, 100+
# languages incl. all 6 CrossWordFalcon languages, CLS pooling). It is the
# smallest genuinely state-of-the-art multilingual embedder — small enough
# to run comfortably on CPU alone, no GPU required.
#
# CPU by default (--n_gpu_layers 0, CUDA hidden) so it never competes with
# the clue-generation LLM for VRAM. Set EMBED_N_GPU_LAYERS to a positive
# value (e.g. 99 = offload every layer) in env.sh to run it on the GPU
# instead — worthwhile only when the LLM server is NOT running, since the
# RTX-3060-class card this project targets can't hold both. Measured on
# bge-m3: ~8 ms/embedding (~120/s) single, ~2 ms (~465/s) batched on GPU,
# vs. ~40 ms p50 on CPU with no batched speed-up.
#
# Which GGUF to serve is entirely env.sh's (or env_default.sh's) call —
# EMBED_GGUF_REPO / EMBED_GGUF_FILE / EMBED_MODEL — no hardcoded default
# lives here (same no-duplication rule as run_llm.sh).
set -euo pipefail

cd "$(dirname "$0")"

if [ -f env.sh ]; then
    source env.sh
elif [ -f env_default.sh ]; then
    source env_default.sh
fi

EMBED_HOST="${EMBED_HOST:-127.0.0.1}"
EMBED_PORT="${EMBED_PORT:-3003}"
MODELS_DIR="models"
LOG_DIR="logs"
EMBED_LOG="$LOG_DIR/embed.log"

GGUF_REPO="${EMBED_GGUF_REPO:?EMBED_GGUF_REPO not set — check env.sh (or env_default.sh)}"
GGUF_FILE="${EMBED_GGUF_FILE:?EMBED_GGUF_FILE not set — check env.sh (or env_default.sh)}"
MODEL_ALIAS="${EMBED_MODEL:?EMBED_MODEL not set — check env.sh (or env_default.sh)}"
MODEL_PATH="$MODELS_DIR/$GGUF_FILE"

# Context length — bge-m3 supports up to 8192, but embedding short strings
# (crossword words, clues, short sentences) never needs that; 2048 keeps
# the per-request compute buffer small. Raise EMBED_N_CTX in env.sh if you
# embed long documents.
EMBED_N_CTX="${EMBED_N_CTX:-2048}"
# CPU threads. Empty -> llama.cpp's own default (physical core count).
EMBED_N_THREADS="${EMBED_N_THREADS:-}"
# 0 (default) -> CPU-only, GPU hidden. A positive value offloads that many
# layers to the GPU (99 = all). Only use it when the LLM server is stopped.
EMBED_N_GPU_LAYERS="${EMBED_N_GPU_LAYERS:-0}"

mkdir -p "$MODELS_DIR" "$LOG_DIR"

# Stop any server already listening on the embed port (LISTEN socket only,
# never a mere client connection — same rule as run_Falcon.sh's stop_port).
pids=$(lsof -ti tcp:"$EMBED_PORT" -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$pids" ]; then
    echo "Stopping embed server already running on port $EMBED_PORT (pid: $pids)"
    kill $pids 2>/dev/null || true
    sleep 1
    pids=$(lsof -ti tcp:"$EMBED_PORT" -sTCP:LISTEN 2>/dev/null || true)
    [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
fi

source .venv/bin/activate

# Download the GGUF on first run (no auth needed — public repo).
if [ ! -f "$MODEL_PATH" ]; then
    echo "Downloading $GGUF_REPO / $GGUF_FILE -> $MODEL_PATH ..."
    URL="https://huggingface.co/$GGUF_REPO/resolve/main/$GGUF_FILE"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 -o "$MODEL_PATH" "$URL"
    else
        wget -O "$MODEL_PATH" "$URL"
    fi
fi

THREAD_ARGS=""
[ -n "$EMBED_N_THREADS" ] && THREAD_ARGS="--n_threads $EMBED_N_THREADS"

if [ "$EMBED_N_GPU_LAYERS" -gt 0 ] 2>/dev/null; then
    GPU_ENV=()
    NGL="$EMBED_N_GPU_LAYERS"
    MODE="GPU ($NGL layers)"
else
    # CUDA_VISIBLE_DEVICES='' guarantees CPU even if llama-cpp-python was
    # built with CUDA support (on this project's machines it often is, for
    # run_llm.sh).
    GPU_ENV=(env CUDA_VISIBLE_DEVICES="")
    NGL=0
    MODE="CPU-only"
fi

echo "Starting embed server on $EMBED_HOST:$EMBED_PORT (model: $MODEL_ALIAS, $MODE)..."
nohup "${GPU_ENV[@]}" .venv/bin/python -m llama_cpp.server \
    --model "$MODEL_PATH" \
    --model_alias "$MODEL_ALIAS" \
    --embedding true \
    --n_gpu_layers "$NGL" \
    --n_ctx "$EMBED_N_CTX" \
    $THREAD_ARGS \
    --host "$EMBED_HOST" \
    --port "$EMBED_PORT" \
    > "$EMBED_LOG" 2>&1 < /dev/null &
disown

pid=$!
echo "Embed server started (pid $pid, log: $EMBED_LOG)"
echo "Endpoint: http://$EMBED_HOST:$EMBED_PORT/v1/embeddings"
echo "Quick check:  .venv/bin/python -m backend.embedder --text \"bonjour le monde\""
echo "Benchmark:     .venv/bin/python -m backend.embedder --benchmark 1000"
