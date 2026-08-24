#!/usr/bin/env bash
# Launches the local LLM server used by default for crossword clue
# generation (backend/clues.py): llama.cpp's built-in OpenAI-compatible
# server (llama_cpp.server), serving a quantized GGUF model. Works the same
# way on Linux and macOS from a single package (Metal on Apple Silicon,
# CUDA on Linux with a GPU, CPU everywhere) — see requirements-llama.txt.
#
# To use a cloud API instead of a local model, see env.sh.
set -euo pipefail

cd "$(dirname "$0")"

LLM_HOST="${LLM_HOST:-127.0.0.1}"
LLM_PORT="${LLM_PORT:-8002}"
MODELS_DIR="models"
LOG_DIR="logs"
LLM_LOG="$LOG_DIR/llm.log"

mkdir -p "$MODELS_DIR" "$LOG_DIR"

if [ -f env.sh ]; then
    source env.sh
fi
# Default: Qwen3.5-9B (the closest official size to a "14B"-class model that
# still comfortably fits a 12GB GPU at this quantization, with headroom for
# the KV cache), Q4_K_M quantized by bartowski. Override in env.sh to use a
# different GGUF.
GGUF_REPO="${LLAMA_GGUF_REPO:-bartowski/Qwen_Qwen3.5-9B-GGUF}"
GGUF_FILE="${LLAMA_GGUF_FILE:-Qwen_Qwen3.5-9B-Q4_K_M.gguf}"
MODEL_PATH="$MODELS_DIR/$GGUF_FILE"

pids=$(lsof -ti tcp:"$LLM_PORT" 2>/dev/null || true)
if [ -n "$pids" ]; then
    echo "Stopping LLM server already running on port $LLM_PORT (pid: $pids)"
    kill $pids 2>/dev/null || true
    sleep 1
    pids=$(lsof -ti tcp:"$LLM_PORT" 2>/dev/null || true)
    [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
fi

source .venv/bin/activate

# `pip install llama-cpp-python` builds a CPU-only binary unless CMAKE_ARGS
# asks for a GPU backend at build time — `--n_gpu_layers -1` below silently
# does nothing if that backend was never compiled in, regardless of what
# hardware is actually present. Detect that mismatch and rebuild with the
# right flag rather than serving on CPU without saying why.
GPU_CMAKE_ARGS=""
if [ "$(uname -s)" = "Darwin" ]; then
    GPU_CMAKE_ARGS="-DGGML_METAL=on"
elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    GPU_CMAKE_ARGS="-DGGML_CUDA=on"
fi
if [ -n "$GPU_CMAKE_ARGS" ]; then
    HAS_GPU_SUPPORT=$(python3 -c "import llama_cpp; print(llama_cpp.llama_supports_gpu_offload())" 2>/dev/null || echo False)
    if [ "$HAS_GPU_SUPPORT" != "True" ]; then
        echo "GPU detected but the installed llama-cpp-python has no GPU support built in."
        echo "Rebuilding with CMAKE_ARGS=\"$GPU_CMAKE_ARGS\" (recompiles llama.cpp, a few minutes)..."
        CMAKE_ARGS="$GPU_CMAKE_ARGS" pip install --force-reinstall --no-cache-dir \
            "$(grep -o 'llama-cpp-python\[server\]==[0-9.]*' requirements-llama.txt)"
    fi
fi

if [ ! -f "$MODEL_PATH" ]; then
    echo "Downloading $GGUF_FILE from $GGUF_REPO (several GB, one-time)..."
    curl -L --fail -o "$MODEL_PATH.part" \
        "https://huggingface.co/${GGUF_REPO}/resolve/main/${GGUF_FILE}"
    mv "$MODEL_PATH.part" "$MODEL_PATH"
fi

echo "Starting LLM server: model=$MODEL_PATH, port=$LLM_PORT"
nohup python3 -m llama_cpp.server \
    --model "$MODEL_PATH" \
    --host "$LLM_HOST" --port "$LLM_PORT" \
    --n_ctx 4096 --n_gpu_layers -1 \
    --chat_template_kwargs '{"enable_thinking": false}' \
    > "$LLM_LOG" 2>&1 &
LLM_PID=$!

echo "LLM server started (pid $LLM_PID, log: $LLM_LOG)"
echo "Endpoint: http://$LLM_HOST:$LLM_PORT/v1/chat/completions"
