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
# Qwen3.5-9B GGUF. No real API key needed — llama.cpp ignores the bearer
# token unless you configured it to require one.
export LLM_BASE_URL="http://127.0.0.1:8002/v1/chat/completions"
export LLM_MODEL="Qwen/Qwen3.5-9B"
export LLM_API_KEY="EMPTY"

# Which GGUF run_llm.sh downloads/serves — override to use a different
# quantization or model (see run_llm.sh for the defaults).
# export LLAMA_GGUF_REPO="bartowski/Qwen_Qwen3.5-9B-GGUF"
# export LLAMA_GGUF_FILE="Qwen_Qwen3.5-9B-Q4_K_M.gguf"

# To use the Mistral cloud API instead of a local server: comment out the
# three LLM_* lines above and uncomment these (key from console.mistral.ai):
# export LLM_BASE_URL="https://api.mistral.ai/v1/chat/completions"
# export LLM_MODEL="mistral-small-latest"
# export LLM_API_KEY="your-mistral-api-key-here"
