#!/usr/bin/env bash
# Example environment file, checked into the repo — contains no real secret.
# Copy it to env.sh (which is gitignored) and edit as needed:
#   cp env_default.sh env.sh
#
# LLM used by backend/clues.py to generate crossword clues. Any
# OpenAI-compatible chat-completions endpoint works — swap the three
# variables below to change provider, no code change needed.
#
# Default: the local llama.cpp server (see run_llm.sh) serving a quantized
# Qwen3.5-9B GGUF (thinking disabled), ~2s/word — this project's very
# first default, restored again after trying Qwen3-14B (~8-9s/word): the
# user found Qwen3.5-9B's clue quality better in practice, despite
# Qwen3-14B's larger size. No real API key needed — llama.cpp ignores the
# bearer token unless you configured it to require one. All four LLAMA_* /
# LLM_MODEL lines below are the actual, sole source of truth for which
# GGUF run_llm.sh serves — it has no separate hardcoded default of its
# own, so these must always be kept in sync as a group (switch models by
# commenting/uncommenting a full four-line block, never just one line).
export LLM_BASE_URL="http://127.0.0.1:8002/v1/chat/completions"
export LLM_MODEL="Qwen/Qwen3.5-9B"
export LLM_API_KEY="EMPTY"
export LLAMA_GGUF_REPO="bartowski/Qwen_Qwen3.5-9B-GGUF"
export LLAMA_GGUF_FILE="Qwen_Qwen3.5-9B-Q4_K_M.gguf"
export LLAMA_CHAT_TEMPLATE_KWARGS='{"enable_thinking": false}'

# To use Qwen3.8-27B instead (Unsloth Dynamic 2-bit quant — the strongest
# observed clue-agreement quality of every model tried so far, at the cost
# of being much slower, ~20-40s/word; a good choice if you have a GPU with
# at least 12GB VRAM, see README.md and the project-best-practices SKILL):
# uncomment all four lines below instead of the ones above.
# export LLM_MODEL="Qwen/Qwen3.8-27B"
# export LLAMA_GGUF_REPO="unsloth/Qwen3.8-27B-GGUF"
# export LLAMA_GGUF_FILE="Qwen3.8-27B-UD-Q2_K_XL.gguf"
# export LLAMA_CHAT_TEMPLATE_KWARGS='{"enable_thinking": false}'

# To use Qwen3.5-4B unquantized instead (bf16, full precision — the
# fastest option this project has tried, ~3s/word, but with the weakest
# observed semantic grounding; see the project-best-practices SKILL):
# uncomment all four lines below instead of the ones above.
# export LLM_MODEL="Qwen/Qwen3.5-4B"
# export LLAMA_GGUF_REPO="bartowski/Qwen_Qwen3.5-4B-GGUF"
# export LLAMA_GGUF_FILE="Qwen_Qwen3.5-4B-bf16.gguf"
# export LLAMA_CHAT_TEMPLATE_KWARGS='{"enable_thinking": false}'

# To use Qwen3-14B instead (~8-9s/word — tried as the default for a while;
# see the project-best-practices SKILL): uncomment all four lines below
# instead of the ones above.
# export LLM_MODEL="Qwen/Qwen3-14B"
# export LLAMA_GGUF_REPO="bartowski/Qwen_Qwen3-14B-GGUF"
# export LLAMA_GGUF_FILE="Qwen_Qwen3-14B-Q4_K_M.gguf"
# export LLAMA_CHAT_TEMPLATE_KWARGS='{"enable_thinking": false}'

# To use DeepSeek-R1-Distill-Qwen-14B instead (a reasoning model — much
# slower per word, 20-70s vs. ~2-40s, and not clearly better on this
# project's hard grammatical-agreement clue cases either; see the
# project-best-practices SKILL before switching to this one): uncomment all
# four lines below instead of the ones above.
# export LLM_MODEL="DeepSeek-R1-Distill-Qwen-14B"
# export LLAMA_GGUF_REPO="bartowski/DeepSeek-R1-Distill-Qwen-14B-GGUF"
# export LLAMA_GGUF_FILE="DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf"
# export LLAMA_CHAT_TEMPLATE_KWARGS='{}'

# To use the Mistral cloud API instead of a local server: comment out the
# three LLM_* lines above and uncomment these (key from console.mistral.ai):
# export LLM_BASE_URL="https://api.mistral.ai/v1/chat/completions"
# export LLM_MODEL="mistral-small-latest"
# export LLM_API_KEY="your-mistral-api-key-here"

# run_llm.sh uses a GPU by default when one is detected (Metal on Apple
# Silicon, CUDA on Linux with an NVIDIA card — see run_llm.sh's own
# detection/rebuild logic). To always run on CPU instead — e.g. to free
# up the GPU for another process, or to sidestep a flaky/unsupported GPU
# build — uncomment this line (any non-empty value forces CPU; the GPU
# detection/rebuild step is skipped entirely, not just ignored):
# export LLAMA_FORCE_CPU=1
