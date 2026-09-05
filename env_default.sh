#!/usr/bin/env bash
# Example environment file, checked into the repo — contains no real secret.
# Copy it to env.sh (which is gitignored) and edit as needed:
#   cp env_default.sh env.sh
#
# Ports used by this project's own processes — the single place to change
# any of them, at the user's explicit request: every script/file that needs
# one reads it from an environment variable (each with its own hardcoded
# fallback matching the value below, for a caller that runs without ever
# sourcing this file — run_Falcon.sh, run_llm.sh, frontend/server.py,
# backend/clues.py) rather than hardcoding a separate literal of its own —
# change a port once, here, and every script/URL derived from it follows.
# `${VAR:-default}` (not a bare `export VAR=default`) so a value already set
# in the calling shell's own environment before this file is sourced is
# preserved, not silently clobbered. Moved off an original 8000/8001/8002
# range to this 3000/3001/3002 range, at the user's explicit request, after
# diagnosing a real collision live: a VS Code helper process was also
# listening on 127.0.0.1:8000 (see the project-best-practices SKILL for the
# full diagnosis), shadowing the real, otherwise perfectly healthy server
# for the browser.
export CROSSWORDFALCON_FRONTEND_PORT="${CROSSWORDFALCON_FRONTEND_PORT:-3000}"
export CROSSWORDFALCON_BACKEND_PORT="${CROSSWORDFALCON_BACKEND_PORT:-3001}"
export LLM_PORT="${LLM_PORT:-3002}"

# Derived from CROSSWORDFALCON_BACKEND_PORT just above — change the port
# there, not here, and this follows automatically. Read by
# frontend/server.py to know where to proxy /api/* requests.
export CROSSWORDFALCON_BACKEND_URL="http://127.0.0.1:${CROSSWORDFALCON_BACKEND_PORT}"

# backend/crossword_gen.py's grid generator tries several independent
# black-square patterns in parallel (separate processes) at each black-cell
# ratio step, rather than one at a time — the machine is typically far from
# saturating its CPU with just one attempt in flight, so running more at
# once finds a fillable pattern in about the same wall-clock time as a
# single attempt. Left unset here on purpose, at the user's explicit
# request: crossword_gen.py's own PARALLEL_ATTEMPTS already defaults to
# this machine's own CPU count (`os.cpu_count()`) whenever this variable
# isn't set, which fits any deployment's actual hardware automatically
# rather than a single fixed number picked for one particular machine.
# Uncomment and set an explicit value only to override that per-machine
# default — e.g. to leave some cores free for something else, or to force
# a specific number regardless of core count:
# export CROSSWORDFALCON_PARALLEL_ATTEMPTS=10

# run_llm.sh uses a GPU by default when one is detected (Metal on Apple
# Silicon, CUDA on Linux with an NVIDIA card — see run_llm.sh's own
# detection/rebuild logic). To always run on CPU instead — e.g. to free up
# the GPU for another process, to sidestep a flaky/unsupported GPU build, or
# to test how the app behaves on CPU (the default model below, Qwen3.5-0.8B,
# is small enough to stay fast even without a GPU, which is exactly why it's
# a good pick for this) — uncomment this line (any non-empty value forces
# CPU; the GPU detection/rebuild step is skipped entirely, not just
# ignored):
# export LLAMA_FORCE_CPU=1

# LLM used by backend/clues.py to generate crossword clues. Any
# OpenAI-compatible chat-completions endpoint works — swap the three LLM_*
# variables below to change provider, no code change needed. No real API
# key needed for a local model — llama.cpp ignores the bearer token unless
# you configured it to require one. All four LLAMA_* / LLM_MODEL lines in
# each block below are the actual, sole source of truth for which GGUF
# run_llm.sh serves — it has no separate hardcoded default of its own, so
# these must always be kept in sync as a group (switch models by
# commenting/uncommenting a full four-line block, never just one line).
#
# Models below are ordered smallest to largest, each with a one-line
# hardware/quality summary — pick the one that fits your machine and how
# good you need the clues to be:

# Qwen3.5-0.8B — ultra-fast, including with no GPU at all, but results are
# often poor quality. Good for a first try or for testing, not for real use.
export LLM_BASE_URL="http://127.0.0.1:${LLM_PORT}/v1/chat/completions"
export LLM_MODEL="Qwen/Qwen3.5-0.8B"
export LLM_API_KEY="EMPTY"
export LLAMA_GGUF_REPO="bartowski/Qwen_Qwen3.5-0.8B-GGUF"
export LLAMA_GGUF_FILE="Qwen_Qwen3.5-0.8B-bf16.gguf"
export LLAMA_CHAT_TEMPLATE_KWARGS='{"enable_thinking": false}'

# Qwen3.5-2B — fast (a GPU is recommended), results only passable. To use
# it, uncomment all four lines below instead of the ones above.
# export LLM_MODEL="Qwen/Qwen3.5-2B"
# export LLAMA_GGUF_REPO="bartowski/Qwen_Qwen3.5-2B-GGUF"
# export LLAMA_GGUF_FILE="Qwen_Qwen3.5-2B-bf16.gguf"
# export LLAMA_CHAT_TEMPLATE_KWARGS='{"enable_thinking": false}'

# Qwen3.5-4B — works well on a small graphics card, decent/respectable
# results. To use it, uncomment all four lines below instead of the ones
# above.
# export LLM_MODEL="Qwen/Qwen3.5-4B"
# export LLAMA_GGUF_REPO="bartowski/Qwen_Qwen3.5-4B-GGUF"
# export LLAMA_GGUF_FILE="Qwen_Qwen3.5-4B-bf16.gguf"
# export LLAMA_CHAT_TEMPLATE_KWARGS='{"enable_thinking": false}'

# Qwen3.5-9B — a small GPU, better results. This project's default for a
# long stretch of its history. To use it, uncomment all four lines below
# instead of the ones above.
# export LLM_MODEL="Qwen/Qwen3.5-9B"
# export LLAMA_GGUF_REPO="bartowski/Qwen_Qwen3.5-9B-GGUF"
# export LLAMA_GGUF_FILE="Qwen_Qwen3.5-9B-Q4_K_M.gguf"
# export LLAMA_CHAT_TEMPLATE_KWARGS='{"enable_thinking": false}'

# Qwen3.8-27B — needs a GPU with at least 12GB VRAM, and is slow
# (~20-40s/word), but has the best clue quality observed so far of every
# model tried. To use it, uncomment all four lines below instead of the
# ones above.
# export LLM_MODEL="Qwen/Qwen3.8-27B"
# export LLAMA_GGUF_REPO="unsloth/Qwen3.8-27B-GGUF"
# export LLAMA_GGUF_FILE="Qwen3.8-27B-UD-Q2_K_XL.gguf"
# export LLAMA_CHAT_TEMPLATE_KWARGS='{"enable_thinking": false}'

# Mistral cloud API — the best possible result, no local hardware needed,
# but requires a paid API key (console.mistral.ai). To use it, comment out
# the three LLM_* lines in whichever block above is active and uncomment
# these instead:
# export LLM_BASE_URL="https://api.mistral.ai/v1/chat/completions"
# export LLM_MODEL="mistral-small-latest"
# export LLM_API_KEY="your-mistral-api-key-here"

# Alternative engine: SGLang instead of llama.cpp (run_llm.sh's own
# default above), at the user's explicit request — see run_sglang.sh's
# own header for the full reasoning and CLAUDE.md for the live-verified
# trail. Needs a separate, one-time install (its own Python 3.12 venv,
# `.venv-sglang/`, plus an editable install from a cloned `sglang-src/`
# checkout — see run_sglang.sh) never done by Install.sh automatically
# for every machine, since SGLang's own hardware support is still young
# and platform-specific; Install.sh only detects what's *possible* on
# this machine and reports it, it never installs SGLang itself. Uncomment
# LLM_ENGINE below (and pick one of the two SGLANG_MODEL_PATH blocks that
# actually matches your own hardware) only once that one-time install has
# already been done.
#
# export LLM_ENGINE="sglang"
#
# CUDA (a real NVIDIA GPU): SGLang's normal path, which does support GGUF
# directly. Untested on this project's own dev machine (a Mac, no CUDA
# hardware available) — reported here as the user's own intended default
# for a fresh install elsewhere, not as something already verified live
# the way the Apple Silicon block below has been.
# export SGLANG_MODEL_PATH="unsloth/Qwen3.8-27B-GGUF"
# export SGLANG_QUANTIZATION="gguf"
#
# Apple Silicon (Metal, via SGLang's native MLX backend): verified live on
# this project's own dev machine (a MacBook M1 Max) — a real chat
# completion was served successfully end to end. Qwen3-4B, not Qwen3.5/
# Qwen3.8, deliberately: BOTH Qwen3.5-9B-4bit and Qwen3.8-27B-GGUF crash
# outright on this backend (any quantization format, GGUF or MLX-native,
# both tried) with `AssertionError: extra_buffer needs CUDA/MUSA/NPU/ROCm/
# XPU (FLA)` — their shared hybrid Mamba-attention architecture
# unconditionally requires a CUDA-family platform for this, regardless of
# quantization (confirmed by reading SGLang's own arg_groups/mamba_hook.py
# directly — see run_sglang.sh's own header). Every plain, non-hybrid
# Qwen3 model (no ".5"/".8" suffix) passes the same code path with no
# crash — Qwen3-14B was tried first (already vetted for clue quality in
# this project's own llama.cpp history) and confirmed working, but found
# too slow on this specific machine at the user's own explicit follow-up
# request ("sur cette machine le modèle est trop lent, trouver un modèle
# plus petit") — also confirmed live to strain this machine's own Metal/
# unified-memory ceiling under a large prompt (a real, reproduced `RuntimeError:
# [METAL] Command buffer execution failed: Insufficient Memory`, twice,
# once compounded by two SGLang/MLX servers loaded at once — never run
# two of them concurrently on one machine). Qwen3-4B measured at
# ~1.2s/word for a real, clue-shaped prompt (0.46-2.63s across 3 sampled
# words), against several seconds/word for Qwen3-14B, with no repeat of
# the OOM crash — a clear net win on this hardware, at some cost to clue
# quality (this project's own llama.cpp history already rates 4B "decent/
# respectable" vs. 14B's "larger... same non-reasoning behavior"). No
# SGLANG_QUANTIZATION line needed — this repo is already MLX-native
# 4-bit. Like every Qwen3/Qwen3.5 model this project has used, it's a
# hybrid thinking/non-thinking model that reasons via a <think> block by
# default — SGLang's own equivalent of run_llm.sh's LLAMA_CHAT_TEMPLATE_
# KWARGS is SGLANG_CHAT_TEMPLATE_KWARGS just below (verified live to
# actually suppress it), so uncomment that too rather than relying solely
# on backend/clues.py's own <think>-stripping (which still works
# regardless, as a safety net, but disabling reasoning outright is both
# faster and avoids burning tokens on it in the first place). A larger
# model in this same family (Qwen3-8B/14B/32B-4bit — swap the repo name
# below, same convention) is a reasonable alternative on a machine with
# more headroom than this one, or where clue quality matters more than
# raw speed.
# export SGLANG_MODEL_PATH="mlx-community/Qwen3-4B-4bit"
#
# Disables the <think> reasoning block entirely (verified live, a real
# request/response comparison with and without it — see run_sglang.sh's
# own header): "none" (enable_thinking=false) is the only real option for
# this model's chat template, a plain on/off toggle with no graduated
# "low"/"high" effort support at all (an earlier idea to configure a
# "low" reasoning effort instead was tested and found to still fully
# enable thinking for this exact model). Applies regardless of engine/
# hardware path above — no space inside the JSON value, see run_sglang.sh
# for why.
# export SGLANG_CHAT_TEMPLATE_KWARGS='{"enable_thinking":false}'
