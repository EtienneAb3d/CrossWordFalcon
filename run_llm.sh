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
LLM_PORT="${LLM_PORT:-3002}"
MODELS_DIR="models"
LOG_DIR="logs"
LLM_LOG="$LOG_DIR/llm.log"

# Dual-GPU support, at the user's explicit request ("Cette machine a
# maintenant 2 GPUs... Configurer les lanceurs run_llm.sh et run_sglang.sh
# pour lancer 2 instances du modele LLM, un par carte"). Two INDEPENDENT
# instances of the SAME model/quant, each pinned to its own card via
# CUDA_VISIBLE_DEVICES (a real NVIDIA-runtime env var, silently unused on
# a CPU-only or Metal machine, so it's harmless to always set) — never
# two different models. Routing which request goes to which instance is
# entirely backend/app.py's job (LLM_BASE_URL vs. LLM_BASE_URL_INTERACTIVE,
# see that file's own module-level singletons): this script only ever
# launches the server process(es), it never decides what traffic reaches
# them.
#
#   - LLM_GPU_INDEX (default "0"): which card the PRIMARY instance (port
#     LLM_PORT) binds to — this is the one every AUTOMATIC generation
#     request uses (Populate.py, and the web UI's "Generer la grille"
#     form/Interactive mode's "Finir la grille" button, both of which go
#     through the exact same _run_generate_job — see backend/app.py).
#     Harmless to leave at its default on a single-GPU machine (there is
#     only ever device 0 to select) or on a multi-GPU one that never
#     configures the second instance below (CUDA already defaults to
#     device 0 as the first visible one anyway).
#   - LLM_INTERACTIVE_GPU_INDEX (default unset/empty): when set to a
#     SECOND, DIFFERENT card index, this script ALSO launches a second,
#     independent server process bound to it, listening on
#     LLM_PORT_INTERACTIVE — every INTERACTIVE/on-demand request
#     (Interactive/Edition mode, the ChatBot, the Dictionary panel's
#     Definir/Thematique/Synonymes, the Paraphraseur) is then routed to it
#     instead (see LLM_BASE_URL_INTERACTIVE in env.sh/env_default.sh,
#     read by backend/app.py — never by this script). Left unset (the
#     default), this script behaves exactly as it always has: ONE
#     instance, ONE port, and backend/app.py's own interactive_clue_
#     generator/interactive_chatbot degrade to sharing the primary
#     instance with every automatic request.
# Install.sh can configure both of these for you when it detects more
# than one NVIDIA GPU; see env_default.sh's own "Dual-GPU LLM" section for
# how to set them by hand instead.
LLM_GPU_INDEX="${LLM_GPU_INDEX:-0}"
LLM_INTERACTIVE_GPU_INDEX="${LLM_INTERACTIVE_GPU_INDEX:-}"
LLM_PORT_INTERACTIVE="${LLM_PORT_INTERACTIVE:-3004}"
LLM_INTERACTIVE_LOG="$LOG_DIR/llm_interactive.log"

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

# Alternative engine, at the user's explicit request: SGLang instead of
# this script's own long-standing llama.cpp default — see run_sglang.sh
# for the full reasoning (dedicated venv/Python version, MLX vs. CUDA
# paths, a real model-architecture limitation found and verified live).
# Unset/"llama_cpp" (every existing env.sh/env_default.sh) keeps this
# script's own behavior completely unchanged below this point.
LLM_ENGINE="${LLM_ENGINE:-llama_cpp}"
if [ "$LLM_ENGINE" = "sglang" ]; then
    exec ./run_sglang.sh
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

stop_port() {
    local port="$1"
    local pids
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "Stopping LLM server already running on port $port (pid: $pids)"
        kill $pids 2>/dev/null || true
        sleep 1
        pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
        [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
    fi
}

# Always stop both ports, regardless of whether LLM_INTERACTIVE_GPU_INDEX
# is currently set — a machine that previously ran in dual-instance mode
# and has since been reconfigured back to a single instance would
# otherwise leave an orphaned second process listening on LLM_PORT_
# INTERACTIVE forever.
stop_port "$LLM_PORT"
stop_port "$LLM_PORT_INTERACTIVE"

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

# `nohup` + `disown` + stdin from /dev/null, same as run_Falcon.sh — so this
# keeps running after the launching shell/terminal closes, not just across a
# background `&` (nohup alone only ignores SIGHUP, it doesn't detach from
# the shell's job table).
# n_ctx bumped from 4096 (Qwen3.5, thinking off) to 8192 (DeepSeek-R1-Distill
# reasons through a `<think>` block before every answer, see
# backend/clues.py's REASONING_TOKEN_BUDGET/_strip_reasoning) then to 32768:
# 8192 turned out too small for backend/chatbot.py's own David FALCON chat
# feature, reproduced live — DOC_USER/EN/ReadMe.md alone is already ~6200
# tokens, and a real grid's word list on top of it can easily push the
# system prompt past 8192, at which point llama_cpp.server raises
# "Requested tokens (N) exceed context window of 8192" *inside* its own
# streaming generator, after already sending a 200 OK — the client sees a
# silently empty SSE stream, not an HTTP error (see backend/chatbot.py's
# own defensive fix for the same incident). The model itself
# (n_ctx_train, confirmed live in logs/llm.log) supports far more than
# either value, so this is purely a `--n_ctx` choice, not a model limit.
start_llama_instance() {
    local gpu_index="$1" port="$2" log_file="$3" label="$4"
    echo "Starting LLM server ($label): model=$MODEL_PATH, port=$port, GPU index=$gpu_index"
    CUDA_VISIBLE_DEVICES="$gpu_index" nohup python3 -m llama_cpp.server \
        --model "$MODEL_PATH" \
        --host "$LLM_HOST" --port "$port" \
        --n_ctx 32768 --n_gpu_layers "$N_GPU_LAYERS" \
        --chat_template_kwargs "$CHAT_TEMPLATE_KWARGS" \
        < /dev/null > "$log_file" 2>&1 &
    local pid=$!
    disown "$pid"
    echo "LLM server ($label) started (pid $pid, log: $log_file)"
    echo "Endpoint: http://$LLM_HOST:$port/v1/chat/completions"
}

start_llama_instance "$LLM_GPU_INDEX" "$LLM_PORT" "$LLM_LOG" "automatic generation"
if [ -n "$LLM_INTERACTIVE_GPU_INDEX" ]; then
    start_llama_instance "$LLM_INTERACTIVE_GPU_INDEX" "$LLM_PORT_INTERACTIVE" \
        "$LLM_INTERACTIVE_LOG" "interactive requests"
fi
