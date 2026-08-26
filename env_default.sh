#!/usr/bin/env bash
# Example environment file, checked into the repo — contains no real secret.
# Copy it to env.sh (which is gitignored) and edit as needed:
#   cp env_default.sh env.sh
#
# backend/crossword_gen.py's grid generator tries several independent
# black-square patterns in parallel (separate processes) at each black-cell
# ratio step, rather than one at a time — the machine is typically far from
# saturating its CPU with just one attempt in flight, so running more at
# once finds a fillable pattern in about the same wall-clock time as a
# single attempt. Raise this if you have a lot of idle CPU cores and want
# more (and better — the best of the batch is kept) attempts per step;
# lower it on a machine with few cores, where too many at once could start
# competing with each other instead of running truly in parallel.
export CROSSWORDFALCON_PARALLEL_ATTEMPTS=10

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
export LLM_BASE_URL="http://127.0.0.1:8002/v1/chat/completions"
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
