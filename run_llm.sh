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

# Which GGUF to serve is entirely env.sh's (or, absent that, env_default.sh's)
# call — no separate hardcoded default lives here. That used to duplicate
# whichever model env_default.sh's own active block named, in a second place
# that had to be kept in sync by hand every time the default model changed
# (a real, easy-to-miss source of drift: LLM_MODEL and the actual served
# GGUF silently disagreeing if only one of the two copies got updated).
# env_default.sh is checked into the repo and always has a complete, valid
# block active, so falling back to it (rather than requiring env.sh to
# exist) still gives a correct, current default with zero duplication.
if [ -f env.sh ]; then
    source env.sh
elif [ -f env_default.sh ]; then
    source env_default.sh
fi
GGUF_REPO="${LLAMA_GGUF_REPO:?LLAMA_GGUF_REPO not set — check env.sh (or env_default.sh)}"
GGUF_FILE="${LLAMA_GGUF_FILE:?LLAMA_GGUF_FILE not set — check env.sh (or env_default.sh)}"
CHAT_TEMPLATE_KWARGS="${LLAMA_CHAT_TEMPLATE_KWARGS:?LLAMA_CHAT_TEMPLATE_KWARGS not set — check env.sh (or env_default.sh)}"
MODEL_PATH="$MODELS_DIR/$GGUF_FILE"
# Optional, unset by default (GPU used when present — see the detection/
# rebuild block below): set LLAMA_FORCE_CPU to any non-empty value in
# env.sh to always run on CPU regardless of what hardware is detected,
# skipping GPU detection/rebuild entirely — useful to free up a GPU for
# another process, or to sidestep a flaky/unsupported GPU build.
FORCE_CPU="${LLAMA_FORCE_CPU:-}"
# Qwen3 and Qwen3.5 are hybrid thinking/non-thinking models — their chat
# template reads an `enable_thinking` flag, and backend/clues.py's
# one-word-per-call design needs it off (see LLAMA_CHAT_TEMPLATE_KWARGS in
# env.sh/env_default.sh) or it burns the whole per-call token budget on a
# `<think>` block before ever answering. DeepSeek-R1-Distill has no such
# flag: it always reasons through a `<think>...</think>` block — that key is
# simply absent from its own chat template, not off by default the same way
# (see backend/clues.py's REASONING_TOKEN_BUDGET/_strip_reasoning, needed
# only for that model) — env_default.sh's DeepSeek block sets
# LLAMA_CHAT_TEMPLATE_KWARGS to `{}` accordingly.

pids=$(lsof -ti tcp:"$LLM_PORT" 2>/dev/null || true)
if [ -n "$pids" ]; then
    echo "Stopping LLM server already running on port $LLM_PORT (pid: $pids)"
    kill $pids 2>/dev/null || true
    sleep 1
    pids=$(lsof -ti tcp:"$LLM_PORT" 2>/dev/null || true)
    [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
fi

source .venv/bin/activate

# N_GPU_LAYERS feeds --n_gpu_layers below: -1 offloads every layer llama.cpp
# can (the normal, GPU-preferring default), 0 forces CPU-only inference —
# set once here so the detection/rebuild block below can be skipped
# entirely when LLAMA_FORCE_CPU is set, rather than running it and then
# discarding whatever it found.
N_GPU_LAYERS=-1

if [ -n "$FORCE_CPU" ]; then
    echo "LLAMA_FORCE_CPU is set — running on CPU regardless of detected hardware."
    N_GPU_LAYERS=0
else
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
            CAN_BUILD=true

            # nvidia-smi only proves the NVIDIA *driver* is installed — building
            # against CUDA also needs the CUDA Toolkit's compiler (nvcc), a
            # separate install. Don't attempt (and fail) a build we already know
            # can't work — tell the user exactly what to install instead.
            if [ "$GPU_CMAKE_ARGS" = "-DGGML_CUDA=on" ] \
                    && ! command -v nvcc >/dev/null 2>&1 && [ -z "${CUDACXX:-}" ]; then
                CAN_BUILD=false
                echo "NVIDIA GPU detected (driver present) but no CUDA compiler (nvcc) found —"
                echo "running on CPU for now. To use the GPU, install the CUDA Toolkit (this is"
                echo "separate from the driver you already have):"
                if command -v apt-get >/dev/null 2>&1; then
                    echo "  sudo apt-get install nvidia-cuda-toolkit"
                elif command -v dnf >/dev/null 2>&1; then
                    echo "  sudo dnf install cuda-toolkit"
                elif command -v pacman >/dev/null 2>&1; then
                    echo "  sudo pacman -S cuda"
                else
                    echo "  see https://developer.nvidia.com/cuda-downloads for your distro"
                fi
                echo "Then rerun this script (or set CUDACXX to your nvcc path if it's already"
                echo "installed somewhere not on PATH)."
            fi

            # Similarly, building with Metal needs a C/C++ compiler — Xcode's
            # Command Line Tools, which aren't installed on macOS by default.
            if [ "$GPU_CMAKE_ARGS" = "-DGGML_METAL=on" ] && ! xcode-select -p >/dev/null 2>&1; then
                CAN_BUILD=false
                echo "Metal build needs Xcode's Command Line Tools, which aren't installed —"
                echo "running on CPU for now. To use the GPU, install them with:"
                echo "  xcode-select --install"
                echo "Then rerun this script."
            fi

            if [ "$CAN_BUILD" = true ]; then
                echo "GPU detected but the installed llama-cpp-python has no GPU support built in."
                echo "Rebuilding with CMAKE_ARGS=\"$GPU_CMAKE_ARGS\" (recompiles llama.cpp, a few minutes)..."
                # Never let a failed rebuild abort the script (set -e) — worst
                # case we fall back to whatever's already installed and run on
                # CPU, which is strictly better than not starting at all.
                if ! CMAKE_ARGS="$GPU_CMAKE_ARGS" pip install --force-reinstall --no-cache-dir \
                        "$(grep -o 'llama-cpp-python\[server\]==[0-9.]*' requirements-llama.txt)"; then
                    echo "GPU rebuild failed — continuing on CPU. This usually means a missing"
                    echo "build tool: check that cmake and a C/C++ compiler are installed"
                    echo "(Debian/Ubuntu: sudo apt-get install build-essential cmake), then rerun"
                    echo "this script. See the error above for the actual cause."
                fi
            fi
        fi
    fi
fi

if [ ! -f "$MODEL_PATH" ]; then
    echo "Downloading $GGUF_FILE from $GGUF_REPO (several GB, one-time)..."
    curl -L --fail -o "$MODEL_PATH.part" \
        "https://huggingface.co/${GGUF_REPO}/resolve/main/${GGUF_FILE}"
    mv "$MODEL_PATH.part" "$MODEL_PATH"
fi

echo "Starting LLM server: model=$MODEL_PATH, port=$LLM_PORT"
# `nohup` + `disown` + stdin from /dev/null, same as run_Falcon.sh — so this
# keeps running after the launching shell/terminal closes, not just across a
# background `&` (nohup alone only ignores SIGHUP, it doesn't detach from
# the shell's job table).
# n_ctx bumped from 4096 (Qwen3.5, thinking off) to 8192: DeepSeek-R1-Distill
# reasons through a `<think>` block before every answer (see
# backend/clues.py's REASONING_TOKEN_BUDGET/_strip_reasoning), so the prompt
# plus that reasoning plus the actual answer needs more room to fit.
nohup python3 -m llama_cpp.server \
    --model "$MODEL_PATH" \
    --host "$LLM_HOST" --port "$LLM_PORT" \
    --n_ctx 8192 --n_gpu_layers "$N_GPU_LAYERS" \
    --chat_template_kwargs "$CHAT_TEMPLATE_KWARGS" \
    < /dev/null > "$LLM_LOG" 2>&1 &
LLM_PID=$!
disown "$LLM_PID"

echo "LLM server started (pid $LLM_PID, log: $LLM_LOG)"
echo "Endpoint: http://$LLM_HOST:$LLM_PORT/v1/chat/completions"
