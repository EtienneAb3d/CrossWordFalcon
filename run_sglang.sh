#!/usr/bin/env bash
# Alternative local LLM launcher, using SGLang instead of llama.cpp
# (run_llm.sh's own, long-standing default) — at the user's explicit
# request. Never invoked directly by run_Falcon.sh: run_llm.sh itself
# dispatches here when LLM_ENGINE=sglang (env.sh/env_default.sh), and
# otherwise keeps using its own llama.cpp path unchanged.
#
# Uses its own dedicated virtual environment (.venv-sglang, a separate
# Python 3.12 venv — SGLang's own MLX/Apple-Silicon support needs Python
# 3.12, not this project's main .venv's Python 3.14), at the user's
# explicit request ("Installer SGLang avec un venv spécifique"), and its
# own cloned source checkout (sglang-src/, an editable install — SGLang's
# Apple Silicon/MLX support is installed from a git clone with the
# platform-specific pyproject_other.toml swapped in as the active
# pyproject.toml, per SGLang's own official docs; neither this venv nor
# this clone are meant to be committed — see .gitignore).
#
# Two genuinely different hardware paths, both verified live before this
# script was written (see CLAUDE.md for the full trail):
#   - Apple Silicon (this project's own dev machine, a MacBook M1 Max):
#     SGLang's native MLX backend (SGLANG_USE_MLX=1). Only supports an
#     MLX-native pre-quantized model (an `mlx-community/<model>-Nbit` HF
#     repo) or on-the-fly `mlx_q4`/`mlx_q8` quantization of an fp16
#     safetensors model — NEVER a GGUF file (verified live: SGLang's own
#     GGUF quantization layer explicitly warns "Only CUDA, MUSA and NPU
#     support GGUF quantization currently", and MLX has no GGUF loader at
#     all).
#   - CUDA (a real NVIDIA GPU, untestable on this project's own dev
#     machine): SGLang's normal CUDA path, which does support GGUF
#     directly via --quantization gguf.
#
# One real, hard limitation found and verified live, not assumed: Qwen3.8
# specifically (any quantization/file format — GGUF or MLX-native, tried
# both) crashes outright on the MLX/Apple-Silicon path with `AssertionError:
# extra_buffer needs CUDA/MUSA/NPU/ROCm/XPU (FLA)` — this model's own
# hybrid Mamba-attention architecture unconditionally requires one of
# those platforms for its radix-cache "extra buffer", regardless of
# quantization format. This is why SGLANG_MODEL_PATH below defaults to a
# genuinely dense Qwen3.5 model (already vetted by this project's own
# llama.cpp history) for the MLX path, rather than Qwen3.8 — the GGUF/
# CUDA path is a separate concern, entirely unaffected by this (a real
# CUDA machine satisfies the assertion above, so Qwen3.8-27B-GGUF is
# expected to work there; this hasn't been directly tested — no CUDA
# hardware available on this project's own dev machine).
set -euo pipefail

cd "$(dirname "$0")"

LLM_HOST="${LLM_HOST:-127.0.0.1}"
LLM_PORT="${LLM_PORT:-3002}"
LOG_DIR="logs"
LLM_LOG="$LOG_DIR/sglang.log"
mkdir -p "$LOG_DIR"

if [ -f env.sh ]; then
    source env.sh
elif [ -f env_default.sh ]; then
    source env_default.sh
fi

SGLANG_MODEL_PATH="${SGLANG_MODEL_PATH:?SGLANG_MODEL_PATH not set — check env.sh (or env_default.sh)}"
# Empty by default (an MLX-community pre-quantized repo needs no explicit
# --quantization flag at all — see the header comment above); set to
# "gguf" for a GGUF repo on a real CUDA machine, or "mlx_q4"/"mlx_q8" to
# quantize an fp16 safetensors model on the fly on Apple Silicon.
SGLANG_QUANTIZATION="${SGLANG_QUANTIZATION:-}"
# Empty by default. SGLang's own equivalent of run_llm.sh's
# LLAMA_CHAT_TEMPLATE_KWARGS: a JSON object applied as the *default*
# chat_template_kwargs for every request that doesn't override it itself
# (--default-chat-template-kwargs, verified live to exist and work — see
# CLAUDE.md). Set to '{"enable_thinking":false}' (no space inside the
# JSON — see THINK_ARGS below, passed unquoted the same bash-3.2-safe way
# as QUANT_ARGS, so any internal whitespace would be word-split into a
# separate, broken argument) for a Qwen3/Qwen3.5 model to suppress its
# <think> reasoning block entirely, at the user's explicit request
# ("Essaye de configurer l'option reasoning à low, voire none") —
# verified live that "none" (enable_thinking=false) is the only real
# option for this model family: SGLang auto-detects this template's own
# reasoning_config as a plain on/off toggle (effort_kwarg=None, no
# graduated "low"/"medium"/"high" support at all for Qwen3's own chat
# template), so a request-level reasoning_effort of "low" would still
# fully enable thinking (`thinking = effort != "none"`) — only "none"
# actually disables it.
SGLANG_CHAT_TEMPLATE_KWARGS="${SGLANG_CHAT_TEMPLATE_KWARGS:-}"

if [ ! -d .venv-sglang ]; then
    echo "Error: .venv-sglang not found — SGLang isn't installed. See CLAUDE.md's"
    echo "run_sglang.sh entry for the install steps (Python 3.12 venv + editable"
    echo "install from a cloned sglang-src/ checkout)."
    exit 1
fi

pids=$(lsof -ti tcp:"$LLM_PORT" 2>/dev/null || true)
if [ -n "$pids" ]; then
    echo "Stopping LLM server already running on port $LLM_PORT (pid: $pids)"
    kill $pids 2>/dev/null || true
    sleep 1
    pids=$(lsof -ti tcp:"$LLM_PORT" 2>/dev/null || true)
    [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
fi

# Built as a single string, not a bash array, deliberately: macOS's own
# default /bin/bash is 3.2.57 (Apple has never shipped a newer one, for
# licensing reasons) — "${ARR[@]}" on a possibly-EMPTY array raises
# "unbound variable" under `set -u` on that version (fixed in bash 4.4+),
# a real bug found live on this exact machine while first testing this
# script. Safe here specifically because SGLANG_QUANTIZATION is a single
# plain token (gguf/mlx_q4/mlx_q8), never something needing its own
# quoting/word-splitting protection.
QUANT_ARGS=""
if [ -n "$SGLANG_QUANTIZATION" ]; then
    QUANT_ARGS="--quantization $SGLANG_QUANTIZATION"
fi

# Same plain-string convention as QUANT_ARGS above (a bash array here would
# hit the identical bash-3.2 "unbound variable" bug once empty) — safe to
# leave the JSON value unquoted at the call site below since it's passed as
# a single shell word to --default-chat-template-kwargs, never split further
# by SGLang itself (it parses the whole argument as one JSON string).
THINK_ARGS=""
if [ -n "$SGLANG_CHAT_TEMPLATE_KWARGS" ]; then
    THINK_ARGS="--default-chat-template-kwargs $SGLANG_CHAT_TEMPLATE_KWARGS"
fi

# SGLANG_USE_MLX=1 only on Apple Silicon — this same script's CUDA path
# (a real NVIDIA machine) never sets it, matching SGLang's own documented
# distinction between the two backends (see the header comment above).
IS_APPLE_SILICON=false
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    IS_APPLE_SILICON=true
fi

echo "Starting SGLang server: model=$SGLANG_MODEL_PATH, port=$LLM_PORT, apple_silicon=$IS_APPLE_SILICON"
if [ "$IS_APPLE_SILICON" = true ]; then
    SGLANG_USE_MLX=1 nohup .venv-sglang/bin/python3 -m sglang.launch_server \
        --model-path "$SGLANG_MODEL_PATH" \
        --host "$LLM_HOST" --port "$LLM_PORT" \
        --disable-cuda-graph \
        $QUANT_ARGS \
        $THINK_ARGS \
        < /dev/null > "$LLM_LOG" 2>&1 &
else
    nohup .venv-sglang/bin/python3 -m sglang.launch_server \
        --model-path "$SGLANG_MODEL_PATH" \
        --host "$LLM_HOST" --port "$LLM_PORT" \
        $QUANT_ARGS \
        $THINK_ARGS \
        < /dev/null > "$LLM_LOG" 2>&1 &
fi
LLM_PID=$!
disown "$LLM_PID"

echo "SGLang server started (pid $LLM_PID, log: $LLM_LOG)"
echo "Endpoint: http://$LLM_HOST:$LLM_PORT/v1/chat/completions"
