#!/usr/bin/env bash
# Launches the local clue-generation LLM with vLLM (CUDA only), as an
# OpenAI-compatible server on LLM_PORT. Only ever invoked through
# run_llm.sh's dispatch (LLM_ENGINE=vllm).
#
# vLLM lives in its own venv, .venv-vllm/ (Python 3.12), since it pins
# its own torch/transformers versions:
#   /usr/bin/python3.12 -m venv .venv-vllm
#   .venv-vllm/bin/pip install "vllm[extra-quant]==0.30.0"
# The "extra-quant" extra pulls vllm-gguf-plugin, which vLLM needs to
# load a .gguf file at all.
#
# The model is either VLLM_MODEL, a regular (safetensors) Hugging Face
# repo id or local directory passed to `vllm serve` as-is, or — when
# VLLM_MODEL is empty — a GGUF named by VLLM_GGUF_REPO + VLLM_GGUF_FILE (downloaded
# into the Hugging Face cache on first use, then resolved to its local
# path — vLLM loads a GGUF from a local file). VLLM_TOKENIZER and
# VLLM_HF_CONFIG_PATH point at the model's own non-GGUF HF repo, whose
# tokenizer/chat template and config.json are the reference ones.
#
# Two layouts on a multi-GPU machine:
#   - VLLM_TP_SIZE > 1: ONE instance on LLM_PORT, tensor-parallel across
#     the cards of VLLM_TP_GPU_INDICES (default "LLM_GPU_INDEX,
#     LLM_INTERACTIVE_GPU_INDEX") — for a model too large for one card.
#     No interactive instance: point LLM_BASE_URL_INTERACTIVE at LLM_PORT.
#   - otherwise: one instance on LLM_GPU_INDEX, plus a second, independent
#     one on LLM_INTERACTIVE_GPU_INDEX / LLM_PORT_INTERACTIVE when that
#     variable is set (same dual-GPU routing as run_llm.sh/run_sglang.sh).
set -euo pipefail

cd "$(dirname "$0")"

if [ -f env.sh ]; then
    source env.sh
elif [ -f env_default.sh ]; then
    source env_default.sh
fi

LLM_HOST="${LLM_HOST:-127.0.0.1}"
LLM_PORT="${LLM_PORT:-3002}"
LLM_PORT_INTERACTIVE="${LLM_PORT_INTERACTIVE:-3004}"
LLM_GPU_INDEX="${LLM_GPU_INDEX:-0}"
LLM_INTERACTIVE_GPU_INDEX="${LLM_INTERACTIVE_GPU_INDEX:-}"
LOG_DIR="logs"
LLM_LOG="$LOG_DIR/vllm.log"
LLM_INTERACTIVE_LOG="$LOG_DIR/vllm_interactive.log"
mkdir -p "$LOG_DIR"

VLLM_MODEL="${VLLM_MODEL:-}"
VLLM_GGUF_REPO="${VLLM_GGUF_REPO:-}"
VLLM_GGUF_FILE="${VLLM_GGUF_FILE:-}"
if [ -z "$VLLM_MODEL" ] && { [ -z "$VLLM_GGUF_REPO" ] || [ -z "$VLLM_GGUF_FILE" ]; }; then
    echo "Error: set VLLM_MODEL, or VLLM_GGUF_REPO + VLLM_GGUF_FILE — check env.sh (or env_default.sh)."
    exit 1
fi
VLLM_TOKENIZER="${VLLM_TOKENIZER:-}"
VLLM_HF_CONFIG_PATH="${VLLM_HF_CONFIG_PATH:-}"
# The name clients send as "model" (backend/clues.py, backend/chatbot.py).
VLLM_SERVED_MODEL_NAME="${VLLM_SERVED_MODEL_NAME:-${LLM_MODEL:-}}"
VLLM_TP_SIZE="${VLLM_TP_SIZE:-1}"
VLLM_TP_GPU_INDICES="${VLLM_TP_GPU_INDICES:-$LLM_GPU_INDEX${LLM_INTERACTIVE_GPU_INDEX:+,$LLM_INTERACTIVE_GPU_INDEX}}"
# Share of each card's TOTAL memory vLLM may take (weights + KV cache +
# activations). Keep room for anything else on the card (the embed server).
VLLM_GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-0.85}"
# Context length; bounds the KV cache vLLM must be able to hold for one
# request. The ChatBot's longest prompt is ~25k tokens.
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-32768}"
# JSON, no internal whitespace (word-split unquoted, like run_sglang.sh's
# own *_ARGS strings): e.g. {"enable_thinking":false} for a Qwen3 model.
VLLM_CHAT_TEMPLATE_KWARGS="${VLLM_CHAT_TEMPLATE_KWARGS:-}"
# Splits a <think> block into reasoning_content, never mixed into the
# visible content backend/chatbot.py reads ("qwen3" for this family).
VLLM_REASONING_PARSER="${VLLM_REASONING_PARSER:-}"
# Any further `vllm serve` flags, space-separated.
VLLM_EXTRA_ARGS="${VLLM_EXTRA_ARGS:-}"

if [ ! -x .venv-vllm/bin/vllm ]; then
    echo "Error: .venv-vllm not found — vLLM isn't installed. See this script's header."
    exit 1
fi

# Stops whatever listens on a port, with its whole process tree (vLLM
# spawns one worker process per GPU). -sTCP:LISTEN keeps clients of the
# port (the back end) out of the kill list.
kill_tree() {
    local pid="$1" child
    for child in $(pgrep -P "$pid" 2>/dev/null || true); do
        kill_tree "$child"
    done
    kill "$pid" 2>/dev/null || true
}
stop_port() {
    local port="$1" pids pid
    pids=$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true)
    [ -z "$pids" ] && return 0
    echo "Stopping LLM server already running on port $port (pid: $pids)"
    for pid in $pids; do kill_tree "$pid"; done
    for _ in $(seq 1 30); do
        pids=$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true)
        [ -z "$pids" ] && return 0
        sleep 1
    done
    kill -9 $pids 2>/dev/null || true
}
stop_port "$LLM_PORT"
stop_port "$LLM_PORT_INTERACTIVE"

# The venv's own bin/ first, so tools vLLM spawns by bare name (ninja for
# JIT kernel builds) are found; CUDA_HOME/CC as for run_sglang.sh.
export PATH="$PWD/.venv-vllm/bin:$PATH"
if [ -z "${CUDA_HOME:-}" ] && [ -d /usr/local/cuda ]; then
    export CUDA_HOME=/usr/local/cuda
fi
if [ -n "${SGLANG_NVCC_CC:-}" ]; then
    export CC="$SGLANG_NVCC_CC"
fi

if [ -n "$VLLM_MODEL" ]; then
    MODEL_FILE="$VLLM_MODEL"
    MODEL_LABEL="$VLLM_MODEL"
else
    echo "Resolving $VLLM_GGUF_REPO/$VLLM_GGUF_FILE (downloaded on first use)..."
    MODEL_FILE=$(.venv-vllm/bin/python3 -c \
        'import sys; from huggingface_hub import hf_hub_download; print(hf_hub_download(sys.argv[1], sys.argv[2]))' \
        "$VLLM_GGUF_REPO" "$VLLM_GGUF_FILE")
    MODEL_LABEL="$VLLM_GGUF_REPO/$VLLM_GGUF_FILE"
fi

COMMON_ARGS="--host $LLM_HOST --gpu-memory-utilization $VLLM_GPU_MEMORY_UTILIZATION --max-model-len $VLLM_MAX_MODEL_LEN --enable-prefix-caching"
[ -n "$VLLM_TOKENIZER" ] && COMMON_ARGS="$COMMON_ARGS --tokenizer $VLLM_TOKENIZER"
[ -n "$VLLM_HF_CONFIG_PATH" ] && COMMON_ARGS="$COMMON_ARGS --hf-config-path $VLLM_HF_CONFIG_PATH"
[ -n "$VLLM_SERVED_MODEL_NAME" ] && COMMON_ARGS="$COMMON_ARGS --served-model-name $VLLM_SERVED_MODEL_NAME"
[ -n "$VLLM_CHAT_TEMPLATE_KWARGS" ] && COMMON_ARGS="$COMMON_ARGS --default-chat-template-kwargs $VLLM_CHAT_TEMPLATE_KWARGS"
[ -n "$VLLM_REASONING_PARSER" ] && COMMON_ARGS="$COMMON_ARGS --reasoning-parser $VLLM_REASONING_PARSER"
[ -n "$VLLM_EXTRA_ARGS" ] && COMMON_ARGS="$COMMON_ARGS $VLLM_EXTRA_ARGS"

start_instance() {
    local gpus="$1" port="$2" log_file="$3" extra="$4" label="$5"
    echo "Starting vLLM server ($label): model=$MODEL_LABEL, port=$port, GPU index=$gpus"
    # shellcheck disable=SC2086
    CUDA_VISIBLE_DEVICES="$gpus" nohup .venv-vllm/bin/vllm serve "$MODEL_FILE" \
        --port "$port" $COMMON_ARGS $extra \
        < /dev/null > "$log_file" 2>&1 &
    local pid=$!
    disown "$pid"
    echo "vLLM server ($label) started (pid $pid, log: $log_file)"
    echo "Endpoint: http://$LLM_HOST:$port/v1/chat/completions"
}

if [ "$VLLM_TP_SIZE" -gt 1 ]; then
    start_instance "$VLLM_TP_GPU_INDICES" "$LLM_PORT" "$LLM_LOG" \
        "--tensor-parallel-size $VLLM_TP_SIZE" "all requests, tensor-parallel"
else
    start_instance "$LLM_GPU_INDEX" "$LLM_PORT" "$LLM_LOG" "" "automatic generation"
    if [ -n "$LLM_INTERACTIVE_GPU_INDEX" ]; then
        start_instance "$LLM_INTERACTIVE_GPU_INDEX" "$LLM_PORT_INTERACTIVE" \
            "$LLM_INTERACTIVE_LOG" "" "interactive requests"
    fi
fi
