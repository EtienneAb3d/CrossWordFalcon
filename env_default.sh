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

# --- Optional HTTPS on the front end -----------------------------------
# When BOTH cert and key point at readable PEM files, run_Falcon.sh
# starts a SECOND uvicorn instance for the front end that terminates TLS
# itself, on CROSSWORDFALCON_FRONTEND_HTTPS_PORT — in addition to (never
# instead of) the plain-HTTP instance on CROSSWORDFALCON_FRONTEND_PORT,
# which keeps serving whatever already forwards to it. Nothing here
# touches Apache or any system-wide config; TLS is terminated by this
# project's own front-end process and nothing else.
#
# A high port (3443, not 443) on purpose — no root / setcap / privileged
# bind needed, and it stays out of the way of any standard :443 routing
# the host may already do. Put a 443 -> 3443 forward in front of it the
# same way :80 already reaches the plain front end, if you want the site
# on the default HTTPS port.
#
# The cert here is a real Let's Encrypt certificate for
# falcon.cubaix.com, obtained rootless with certbot's --webroot plugin
# pointed at frontend/static/ (the running front end already serves
# /.well-known/acme-challenge/... from there). certbot lives in .venv,
# its state lives under the project's own letsencrypt/ dir (gitignored),
# and renew-https.sh renews + restarts only this project's front end.
# export CROSSWORDFALCON_FRONTEND_HTTPS_PORT="3443"
# export CROSSWORDFALCON_TLS_CERTFILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/letsencrypt/config/live/falcon.cubaix.com/fullchain.pem"
# export CROSSWORDFALCON_TLS_KEYFILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/letsencrypt/config/live/falcon.cubaix.com/privkey.pem"

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
# How backend/chatbot.py's ChatBot.reply_stream() filters a <think>...
# </think> reasoning block out of David FALCON's own chat replies — see
# backend/chatbot.py's own CHATBOT_THINK_FILTER_CHOICES comment for the
# full explanation. Left unset here (defaults to "open_close" inside the
# module itself) — that default is the safe choice for any model whose
# own reasoning-tag behavior hasn't been specifically verified: it only
# ever holds text back once it has actually SEEN a real <think> tag, so a
# model that never reasons at all is completely unaffected either way,
# streaming normally from the first token. Only switch this to "none" (a
# model confirmed to never emit either tag — the cheapest, safest choice
# once verified) or "close_only" (a model/server confirmed to inject the
# opening tag into the prompt itself, never echoing it back — verified
# live for this project's own SGLang/Qwen3 setup) once you've actually
# checked which applies to your own chosen model below; getting this
# wrong in the "close_only" direction for a model that doesn't need it
# can silently discard an entire reply with no visible error at all.
#
# To SEE which value your current model+engine needs: start the LLM server
# (./run_llm.sh) and run ./test_llm.sh — it lists the exposed models and
# asks for one short random sentence TWICE (once with reasoning_effort
# "none", once with "low"), printing the full raw JSON each time. Read the
# "content" / "reasoning_content" fields:
#   - no <think>/</think> anywhere, reasoning_content null  -> "none"
#   - a lone </think> in content, never an opening <think>   -> "close_only"
#   - a full <think>...</think> block in content             -> "open_close"
# (The SGLang + Qwen3-4B option above needs "close_only" — verified this
# exact way: "none" still emits a leading </think>, "low" moves the whole
# answer into reasoning_content.)
# export CHATBOT_THINK_FILTER="open_close"

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

# ============================================================================
# SGLang + Qwen3-4B (Q4_K_M GGUF) — THE RECOMMENDED, HIGHEST-PERFORMANCE
# LOCAL OPTION ON A 12GB NVIDIA CARD (RTX 3060 and similar).
# ============================================================================
# Verified live end to end on an RTX 3060 (12GB): ~0.1s to first token,
# ~1-2s/word, ~11.2GB VRAM in use (weights + a 46k-token KV cache).
# Clue quality is the same "decent/respectable" tier as the Qwen3.5-4B
# llama.cpp option above — this block's win is purely throughput: SGLang's
# CUDA engine is markedly faster than llama.cpp for the same model size.
#
# Trade-offs / requirements, be aware before choosing this:
#  - Needs a one-time SGLang install (its own Python 3.12 venv,
#    .venv-sglang/ — several minutes). Install.sh sets this up for you and
#    can write this exact block into env.sh; you can also run it by hand
#    (see run_sglang.sh's header).
#  - CUDA only. SGLang's GGUF support does not exist on Apple Silicon (use
#    the MLX block further down) or CPU (use llama.cpp above).
#  - SGLang's GGUF path is version-sensitive. THIS exact model+quant
#    (plain, non-hybrid Qwen3-4B) is verified working. Qwen3.5-4B and
#    Qwen3.8-27B GGUFs are NOT — their hybrid Mamba/linear-attention
#    architecture hits real, unresolved upstream bugs on this path (see
#    CLAUDE.md's sglang.log trail). Stick to a plain "Qwen3-*" GGUF here,
#    never a "Qwen3.5"/"Qwen3.8" one.
#  - On CUDA 13, nvcc rejects gcc > 12 as its host compiler — set
#    SGLANG_NVCC_CC to an older gcc if your system default is newer
#    (Install.sh picks one automatically; see run_sglang.sh's header).
#
# To use it, comment out the active llama.cpp block above and uncomment
# ALL of the lines below (Install.sh does this for you when you pick this
# option). CHATBOT_THINK_FILTER MUST be "close_only" here — verified live,
# this model+engine emits only a lone closing </think> with no opening
# tag, so "open_close"/"none" would let it leak into the chat reply.
# export LLM_ENGINE="sglang"
# export SGLANG_MODEL_PATH="bartowski/Qwen_Qwen3-4B-GGUF/Qwen_Qwen3-4B-Q4_K_M.gguf"
# export SGLANG_QUANTIZATION="gguf"
# export LLM_MODEL="Qwen/Qwen3-4B"
# export LLM_BASE_URL="http://127.0.0.1:${LLM_PORT}/v1/chat/completions"
# export LLM_API_KEY="EMPTY"
# export SGLANG_CHAT_TEMPLATE_KWARGS='{"enable_thinking":false}'
# export SGLANG_REASONING_PARSER="qwen3"
# export SGLANG_MEM_FRACTION_STATIC="0.78"
# export SGLANG_NVCC_CC="/usr/bin/gcc-12"
# export CHATBOT_THINK_FILTER="close_only"

# Mistral cloud API — the best possible result, no local hardware needed,
# but requires a paid API key (console.mistral.ai). To use it, comment out
# the three LLM_* lines in whichever block above is active and uncomment
# these instead:
# export LLM_BASE_URL="https://api.mistral.ai/v1/chat/completions"
# export LLM_MODEL="mistral-small-latest"
# export LLM_API_KEY="your-mistral-api-key-here"

# Alternative engine: SGLang instead of llama.cpp (run_llm.sh's own
# default above). Faster than llama.cpp for the same model, but needs a
# separate one-time install (its own Python 3.12 venv, `.venv-sglang/`
# — see run_sglang.sh's header). Install.sh now offers this
# interactively: it detects your hardware, explains the trade-offs, and
# writes the right block below into env.sh for you (inside its own
# BEGIN/END LLM AUTOCONFIG markers, which override everything above).
# You only need the manual instructions here if you're not using
# Install.sh.
#
# --- CUDA (a real NVIDIA GPU) ---
# Use the dedicated, live-verified "SGLang + Qwen3-4B (Q4_K_M GGUF)"
# block much further up in this file (right before the Mistral section)
# — it has the full, correct set of variables and the risk notes. Do NOT
# point SGLANG_MODEL_PATH at a "Qwen3.5"/"Qwen3.8" GGUF on this path:
# their hybrid architecture hits unresolved upstream bugs (CLAUDE.md).
#
# --- Apple Silicon (Metal, via SGLang's native MLX backend) ---
# Verified live on a MacBook M1 Max — a real chat completion served end
# to end. Qwen3-4B, not Qwen3.5/Qwen3.8, deliberately: BOTH Qwen3.5-9B
# and Qwen3.8-27B crash outright on this backend (any quant format,
# GGUF or MLX-native) with `AssertionError: extra_buffer needs CUDA/
# MUSA/NPU/ROCm/XPU (FLA)` — their hybrid Mamba-attention architecture
# needs a CUDA-family platform for this (confirmed by reading SGLang's
# own arg_groups/mamba_hook.py — see run_sglang.sh's header). Every
# plain, non-hybrid Qwen3 model (no ".5"/".8" suffix) passes the same
# path fine. Qwen3-14B works but was too slow on this machine and
# strained its Metal/unified-memory ceiling under a large prompt (a
# real, reproduced `RuntimeError: [METAL] Command buffer execution
# failed: Insufficient Memory`, twice — never run two SGLang/MLX servers
# at once). Qwen3-4B: ~1.2s/word for a real clue-shaped prompt, no OOM,
# at some cost to clue quality (4B is "decent/respectable" vs. 14B's
# larger). No SGLANG_QUANTIZATION line needed — this repo is already
# MLX-native 4-bit. A larger model in this family (Qwen3-8B/14B/32B-4bit
# — swap the repo name, same convention) is fine on a machine with more
# headroom. Uncomment ALL of:
# export LLM_ENGINE="sglang"
# export SGLANG_MODEL_PATH="mlx-community/Qwen3-4B-4bit"
# export SGLANG_REASONING_PARSER="qwen3"
# export CHATBOT_THINK_FILTER="close_only"
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
