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

# Dual-GPU support — CUDA only (a real NVIDIA machine can hold two
# independent SGLang server processes, one per card; a single Apple
# Silicon machine has exactly one integrated GPU, so this is a no-op
# there, see the CUDA-only guard further below). Same meaning/defaults as
# run_llm.sh's own identical variables — see that script's header for the
# full explanation: LLM_GPU_INDEX (default "0") is the PRIMARY/AUTOMATIC-
# generation instance's card; LLM_INTERACTIVE_GPU_INDEX (default unset)
# is the SECOND card, when configured, hosting a second, independent
# SGLang process dedicated to interactive/on-demand requests, listening
# on LLM_PORT_INTERACTIVE.
LLM_GPU_INDEX="${LLM_GPU_INDEX:-0}"
LLM_INTERACTIVE_GPU_INDEX="${LLM_INTERACTIVE_GPU_INDEX:-}"
LLM_PORT_INTERACTIVE="${LLM_PORT_INTERACTIVE:-3004}"
LLM_INTERACTIVE_LOG="$LOG_DIR/sglang_interactive.log"

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

# Empty by default. Passed to --json-model-override-args (a real SGLang
# flag, "override default model configurations") — needed for a GGUF repo
# that ships a full multimodal HF config.json as a sidecar next to the
# .gguf file (e.g. unsloth/Qwen3.8-27B-GGUF, which also ships mmproj-*.gguf
# for the vision tower): SGLang's own config loader always prefers that
# sidecar config.json over the GGUF's own text-only metadata ("a
# config.json next to the .gguf still wins" — see gguf_native.py), so the
# model is reported as multimodal even though only the base-LLM .gguf was
# named. That then crashes at startup: init_tokenizer_and_processor() only
# calls get_processor() when is_multimodal is true, and that call passes
# the raw local .gguf file path to AutoConfig.from_pretrained() (not the
# repo id/gguf_file kwarg), which fails immediately trying to parse the
# binary GGUF bytes as JSON (UnicodeDecodeError). Verified live, tracing
# ModelConfig.__init__: is_lm_only is read from hf_config.language_model_
# only BEFORE the --language-model-only CLI flag itself gets written back
# onto hf_config, so that flag alone arrives too late to prevent this —
# but a model_override_args value is applied inside get_config(), before
# ModelConfig ever reads is_lm_only, so setting language_model_only=true
# this way does land in time and correctly makes is_multimodal false,
# skipping the crashing get_processor() call entirely without disabling
# anything about the real text-generation model itself (this flag only
# ever skips vision-tower loading — see --language-model-only's own
# --help text). Set to '{"language_model_only":true}' (no space, same
# bash-3.2-safe unquoted-word convention as SGLANG_CHAT_TEMPLATE_KWARGS)
# for a repo shaped this way; leave empty for an ordinary text-only GGUF
# repo with no such sidecar config.json (nothing to work around there).
SGLANG_MODEL_OVERRIDE_ARGS="${SGLANG_MODEL_OVERRIDE_ARGS:-}"

# Empty by default. Passed to --tokenizer-path: needed on top of the
# language_model_only override above for a GGUF whose own general.
# architecture metadata (e.g. "qwen35" for the Qwen3.5/3.8 hybrid Mamba/
# linear-attention family) transformers' load_gguf_checkpoint() doesn't
# recognize yet — verified live: transformers 5.12.1's own GGUF_TO_
# TRANSFORMERS_MAPPING only covers qwen2/qwen2_moe/qwen3/qwen3_moe, so
# AutoTokenizer.from_pretrained() on the local .gguf path itself raises
# "GGUF model with architecture qwen35 is not supported yet." Pointing
# --tokenizer-path at the model's own real, non-GGUF HF repo (which has
# a normal tokenizer.json/tokenizer_config.json, e.g. "Qwen/Qwen3.8-27B"
# for unsloth/Qwen3.8-27B-GGUF) sidesteps the GGUF tokenizer reader
# entirely — the actual model weights still load from the local .gguf
# via --model-path, unaffected. Leave empty for a GGUF whose own
# architecture transformers' GGUF tokenizer converter already supports.
SGLANG_TOKENIZER_PATH="${SGLANG_TOKENIZER_PATH:-}"

# Empty by default — see this variable's own fuller explanation further
# below, right where it's actually used (--reasoning-parser). Needed here
# too, like every other optional SGLANG_* variable above: `set -u` (see
# the top of this script) makes a bare `[ -n "$SGLANG_REASONING_PARSER" ]`
# crash with "unbound variable" the moment this variable isn't exported
# by env.sh/env_default.sh at all, rather than the intended "treat it as
# empty" — found live ("run_sglang.sh: line 202: SGLANG_REASONING_PARSER:
# unbound variable") when this declaration was missing.
SGLANG_REASONING_PARSER="${SGLANG_REASONING_PARSER:-}"

# Empty by default — see this variable's own fuller explanation further
# below, right where it's actually used (--mem-fraction-static). Same
# `set -u` reasoning as SGLANG_REASONING_PARSER just above — this
# declaration was missing for the same reason and would fail the same
# way once actually reached with the variable unset.
SGLANG_MEM_FRACTION_STATIC="${SGLANG_MEM_FRACTION_STATIC:-}"

# Empty by default — the SECOND (interactive) instance's own --mem-
# fraction-static, applied only to it, never to the primary instance
# above. Left unset on a dual-GPU machine where the interactive card
# hosts nothing else (unlike the primary card, which may also cohabit
# with the embed server — see env.sh's own "GPU cohabitation" section),
# so SGLang's own auto-computed fraction for that otherwise-empty card is
# already the right default; set it the same way as SGLANG_MEM_FRACTION_
# STATIC (read the minimum straight off a real OOM error message) only if
# something else ever needs to share that second card too.
SGLANG_MEM_FRACTION_STATIC_INTERACTIVE="${SGLANG_MEM_FRACTION_STATIC_INTERACTIVE:-}"

if [ ! -d .venv-sglang ]; then
    echo "Error: .venv-sglang not found — SGLang isn't installed. See CLAUDE.md's"
    echo "run_sglang.sh entry for the install steps (Python 3.12 venv + editable"
    echo "install from a cloned sglang-src/ checkout)."
    exit 1
fi

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

# Always stop both ports regardless of whether LLM_INTERACTIVE_GPU_INDEX
# is currently set — same reasoning as run_llm.sh's identical stop_port
# calls: never leave an orphaned second process behind after switching
# back to a single-instance configuration.
stop_port "$LLM_PORT"
stop_port "$LLM_PORT_INTERACTIVE"

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

# Same plain-string convention as QUANT_ARGS/THINK_ARGS above, and the
# same reasoning (no internal whitespace in the JSON value, so word-
# splitting it unquoted is safe and required on bash 3.2).
OVERRIDE_ARGS=""
if [ -n "$SGLANG_MODEL_OVERRIDE_ARGS" ]; then
    OVERRIDE_ARGS="--json-model-override-args $SGLANG_MODEL_OVERRIDE_ARGS"
fi

# A bare repo id (no internal whitespace either), same plain-string
# convention as the others.
TOKENIZER_ARGS=""
if [ -n "$SGLANG_TOKENIZER_PATH" ]; then
    TOKENIZER_ARGS="--tokenizer-path $SGLANG_TOKENIZER_PATH"
fi

# Empty by default. --reasoning-parser makes SGLang split a model's own
# <think>...</think> block into a separate `reasoning_content` field
# (never mixed into the visible `content` a client actually reads) —
# needed on top of SGLANG_CHAT_TEMPLATE_KWARGS'{"enable_thinking":false}"
# above, since that flag alone is a template-render hint, not a hard
# guarantee: verified live that a real Qwen3-14B response to a long,
# complex system prompt (this project's own ChatBot) still emitted full
# reasoning text even with enable_thinking:false set, with no opening
# <think> tag at all (only a lone closing </think> partway through) —
# a shape backend/chatbot.py's own streaming stripper (which watches for
# the opening tag first) can't detect either. A configured reasoning
# parser handles this server-side regardless of tag-order oddities, and
# backend/chatbot.py already only ever reads each delta's own `content`
# field (never `reasoning_content`), so this alone is expected to fix the
# leak with no application code change needed. See SGLang's own
# --help for the full list of supported parser names per model family;
# "qwen3" is this family's own.
REASONING_ARGS=""
if [ -n "$SGLANG_REASONING_PARSER" ]; then
    REASONING_ARGS="--reasoning-parser $SGLANG_REASONING_PARSER"
fi

# Empty by default (SGLang auto-computes a value from free VRAM at
# startup). A plain float, no internal whitespace, same convention as the
# others. Needed on a tight-VRAM card (e.g. 12GB) where the auto-computed
# fraction leaves no room at all for the KV cache once weights are loaded
# — verified live: SGLang's own error message on a real OOM names the
# exact minimum viable fraction to use ("Raise --mem-fraction-static
# above X"), so this is meant to be set to a value read directly from
# that message rather than guessed at.
MEM_FRACTION_ARGS=""
if [ -n "$SGLANG_MEM_FRACTION_STATIC" ]; then
    MEM_FRACTION_ARGS="--mem-fraction-static $SGLANG_MEM_FRACTION_STATIC"
fi

# Same convention, for the SECOND (interactive) instance only — see
# SGLANG_MEM_FRACTION_STATIC_INTERACTIVE's own declaration above.
MEM_FRACTION_ARGS_INTERACTIVE=""
if [ -n "$SGLANG_MEM_FRACTION_STATIC_INTERACTIVE" ]; then
    MEM_FRACTION_ARGS_INTERACTIVE="--mem-fraction-static $SGLANG_MEM_FRACTION_STATIC_INTERACTIVE"
fi

# Prepend the venv's own bin/ to PATH — this script always invokes
# .venv-sglang/bin/python3 by its full path (never via `source .../
# activate`), so a subprocess SGLang itself spawns by bare name (e.g.
# `ninja`, used by flashinfer's own JIT kernel compilation at first CUDA
# graph capture — the venv's own `pip install ninja` already provides
# .venv-sglang/bin/ninja, but it was never found without this) would
# otherwise only ever see the *calling* shell's own PATH, not the venv's.
export PATH="$PWD/.venv-sglang/bin:$PATH"

# flashinfer's own JIT build (cpp_ext.py's get_cuda_path()) reads CUDA_HOME/
# CUDA_PATH first, falling back to `which nvcc`'s own dirname only if
# neither is set — which on this machine resolves to /usr/bin/nvcc (a
# secondary update-alternatives "slave" symlink). Verified live: nvcc
# invoked via that exact path cannot find its own sibling headers/tools at
# all ("cuda_runtime.h: Aucun fichier ou dossier de ce nom", then
# "cudafe++: not found" once the header path was forced) — its own
# internal self-location logic doesn't handle this specific symlink the
# same way it handles /usr/local/cuda itself (the main, update-alternatives-
# managed "cuda" link, confirmed live to work correctly). Setting CUDA_HOME
# here sidesteps the broken fallback entirely and always tracks whichever
# CUDA version is currently selected via `update-alternatives --config
# cuda` (never hardcoded to one specific version number).
if [ -z "${CUDA_HOME:-}" ] && [ -d /usr/local/cuda ]; then
    export CUDA_HOME=/usr/local/cuda
fi

# Empty by default. flashinfer's own JIT build (cpp_ext.py) reads the
# real, standard `CC` env var and, if set, passes it to nvcc as
# `-ccbin $CC` — needed whenever the system's default `gcc` is newer than
# whatever this machine's installed CUDA Toolkit's own nvcc supports as a
# host compiler (verified live: CUDA 13.0's nvcc rejects gcc > 12 outright
# — "gcc versions later than 12 are not supported!" — while this
# machine's default `gcc` resolves to 13.3.0). Set to an older, still-
# installed gcc-N binary's full path (e.g. "/usr/bin/gcc-12") rather than
# installing a new compiler — Ubuntu already ships several side-by-side
# gcc-N versions. Leave empty if the system default gcc is already
# CUDA-toolkit-compatible.
if [ -n "${SGLANG_NVCC_CC:-}" ]; then
    export CC="$SGLANG_NVCC_CC"
fi

# SGLANG_USE_MLX=1 only on Apple Silicon — this same script's CUDA path
# (a real NVIDIA machine) never sets it, matching SGLang's own documented
# distinction between the two backends (see the header comment above).
IS_APPLE_SILICON=false
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    IS_APPLE_SILICON=true
fi

if [ "$IS_APPLE_SILICON" = true ] && [ -n "$LLM_INTERACTIVE_GPU_INDEX" ]; then
    echo "Warning: LLM_INTERACTIVE_GPU_INDEX is set but this is Apple Silicon —"
    echo "there is only ever one integrated GPU here (and this project's own"
    echo "history found running two SGLang/MLX servers at once causes a real"
    echo "Metal OOM). Ignoring LLM_INTERACTIVE_GPU_INDEX, starting a single"
    echo "instance only."
fi

start_mlx_instance() {
    echo "Starting SGLang server (MLX): model=$SGLANG_MODEL_PATH, port=$LLM_PORT"
    SGLANG_USE_MLX=1 nohup .venv-sglang/bin/python3 -m sglang.launch_server \
        --model-path "$SGLANG_MODEL_PATH" \
        --host "$LLM_HOST" --port "$LLM_PORT" \
        --disable-cuda-graph \
        $QUANT_ARGS \
        $THINK_ARGS \
        $OVERRIDE_ARGS \
        $TOKENIZER_ARGS \
        $REASONING_ARGS \
        $MEM_FRACTION_ARGS \
        < /dev/null > "$LLM_LOG" 2>&1 &
    LLM_PID=$!
    disown "$LLM_PID"
    echo "SGLang server (MLX) started (pid $LLM_PID, log: $LLM_LOG)"
    echo "Endpoint: http://$LLM_HOST:$LLM_PORT/v1/chat/completions"
}

# CUDA path only — see run_llm.sh's header for the full dual-GPU
# reasoning, mirrored here: CUDA_VISIBLE_DEVICES pins each independent
# SGLang process to its own card, same model/quant on both, only the
# port/GPU index/mem-fraction differ.
start_cuda_instance() {
    local gpu_index="$1" port="$2" log_file="$3" mem_args="$4" label="$5"
    echo "Starting SGLang server ($label): model=$SGLANG_MODEL_PATH, port=$port, GPU index=$gpu_index"
    CUDA_VISIBLE_DEVICES="$gpu_index" nohup .venv-sglang/bin/python3 -m sglang.launch_server \
        --model-path "$SGLANG_MODEL_PATH" \
        --host "$LLM_HOST" --port "$port" \
        $QUANT_ARGS \
        $THINK_ARGS \
        $OVERRIDE_ARGS \
        $TOKENIZER_ARGS \
        $REASONING_ARGS \
        $mem_args \
        < /dev/null > "$log_file" 2>&1 &
    local pid=$!
    disown "$pid"
    echo "SGLang server ($label) started (pid $pid, log: $log_file)"
    echo "Endpoint: http://$LLM_HOST:$port/v1/chat/completions"
}

if [ "$IS_APPLE_SILICON" = true ]; then
    start_mlx_instance
else
    start_cuda_instance "$LLM_GPU_INDEX" "$LLM_PORT" "$LLM_LOG" "$MEM_FRACTION_ARGS" "automatic generation"
    if [ -n "$LLM_INTERACTIVE_GPU_INDEX" ]; then
        start_cuda_instance "$LLM_INTERACTIVE_GPU_INDEX" "$LLM_PORT_INTERACTIVE" \
            "$LLM_INTERACTIVE_LOG" "$MEM_FRACTION_ARGS_INTERACTIVE" "interactive requests"
    fi
fi
