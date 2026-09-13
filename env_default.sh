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
export EMBED_PORT="${EMBED_PORT:-3003}"
# Port for the SECOND, interactive-dedicated LLM instance on a dual-GPU
# machine (run_llm.sh/run_sglang.sh) — see the "Dual-GPU LLM" section
# further below. Always defined, harmless when unused: nothing ever binds
# to it unless LLM_INTERACTIVE_GPU_INDEX is also set.
export LLM_PORT_INTERACTIVE="${LLM_PORT_INTERACTIVE:-3004}"

# Local, CPU-only multilingual text-embedding server (run_embed.sh),
# consumed by backend/embedder.py's Embedder class. Default model:
# BAAI/bge-m3 (Q4_K_M GGUF, ~418 MB, 1024-dim, CLS pooling, 100+
# languages including all 6 CrossWordFalcon languages) served by
# llama_cpp.server in --embedding mode. EMBED_BASE_URL is derived from
# EMBED_PORT below — repoint it at any OpenAI-compatible /v1 endpoint to
# use a different embedding provider, no code change needed.
export EMBED_MODEL="${EMBED_MODEL:-bge-m3}"
export EMBED_GGUF_REPO="${EMBED_GGUF_REPO:-gpustack/bge-m3-GGUF}"
export EMBED_GGUF_FILE="${EMBED_GGUF_FILE:-bge-m3-Q4_K_M.gguf}"
export EMBED_BASE_URL="${EMBED_BASE_URL:-http://127.0.0.1:${EMBED_PORT}/v1}"
export EMBED_API_KEY="${EMBED_API_KEY:-EMPTY}"
# 0 = CPU-only (default). A positive value offloads that many layers to
# the GPU (99 = all). GPU is ~5-15x faster per embedding on bge-m3 and,
# unlike CPU, gains a further ~3.5x from batched requests. bge-m3 needs
# only ~0.4 GB VRAM, so it can share the card with the LLM: to run both
# on a ~12 GB GPU, ALSO lower SGLang's static pool (set
# SGLANG_MEM_FRACTION_STATIC to ~0.60 — outside the Install.sh SGLANG
# AUTOCONFIG block) so it stops grabbing the whole card, or run the LLM
# on llama.cpp (run_llm.sh), which shares VRAM more gracefully.
export EMBED_N_GPU_LAYERS="${EMBED_N_GPU_LAYERS:-0}"

# Number of uvicorn worker processes for the MIDDLEWARE (front) server,
# read by run_Falcon.sh. Only the front end takes this — it's a stateless
# proxy + static-file server, so independent workers just add capacity.
# The back end deliberately stays single-process (its in-memory job
# store / queues / RSS scheduler cannot be shared across workers — see
# backend/app.py). Default 10; set to 1 to go back to a single front
# process.
export CROSSWORDFALCON_FRONTEND_WORKERS="${CROSSWORDFALCON_FRONTEND_WORKERS:-10}"

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

# Chat debug: when enabled, backend/app.py writes the COMPLETE prompt
# actually sent to the LLM (the full messages array — system prompt +
# whole conversation history + the current question) into this session's
# LOG_CHAT/*.md file, inside a collapsible <details> block above each
# reply, for analysis. Off by default; "1"/"true"/"yes"/"on" turn it on.
# export CHATBOT_DEBUG="1"

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

# ============================================================================
# Dual-GPU LLM — TWO independent server instances, one per card, at the
# user's explicit request ("Cette machine a maintenant 2 GPUs... Toutes
# les requêtes de génération automatique... sont affectée à la première
# carte. Toutes les requêtes interactives... sont affectés à la seconde
# carte."). Both instances run the exact SAME model/quant (whichever
# block above/below is active) — this section only ever adds a SECOND
# process bound to a second card, never a different model.
# ============================================================================
# What counts as "automatic generation" (stays on the PRIMARY instance,
# LLM_BASE_URL/LLM_MODEL/LLM_API_KEY above, unaffected by this section):
# Automation/Populate.py, the web UI's own "Générer la grille" form, and
# Interactive/Edition mode's own "Finir la grille" button (all of them
# funnel into backend/app.py's _run_generate_job) plus "Recalculer"
# (_run_recompute_job).
#
# What counts as "interactive" (moved to the SECOND instance below, once
# configured — see backend/app.py's own interactive_clue_generator/
# interactive_chatbot): the Interactive/Edition mode's own theme-glossary
# build and "Proposer un titre", the ChatBot, and the Dictionary panel's
# "Définir"/"Thématique" plus the Paraphraseur panel ("Synonymes" makes no
# LLM call at all — a pure Qdrant lookup — so it's unaffected either way).
#
# Applies to BOTH run_llm.sh (llama.cpp) and run_sglang.sh (SGLang/CUDA
# only — Apple Silicon has a single integrated GPU, so this is a no-op
# there): each script launches a second, independent server process bound
# to LLM_INTERACTIVE_GPU_INDEX (CUDA_VISIBLE_DEVICES), listening on
# LLM_PORT_INTERACTIVE, whenever that variable is set. Install.sh detects
# more than one NVIDIA GPU and can configure all of this for you
# interactively; to do it by hand instead, uncomment ALL of:
# export LLM_GPU_INDEX="0"
# export LLM_INTERACTIVE_GPU_INDEX="1"
# export LLM_BASE_URL_INTERACTIVE="http://127.0.0.1:${LLM_PORT_INTERACTIVE}/v1/chat/completions"
#
# LLM_MODEL_INTERACTIVE/LLM_API_KEY_INTERACTIVE (both optional, left unset
# above on purpose): backend/app.py falls back to the primary instance's
# own LLM_MODEL/LLM_API_KEY when these are unset — the two instances are
# always the same model/quant, only the endpoint genuinely differs, so
# there's normally no reason to set either of these two.
#
# On SGLang specifically, if the primary card also cohabits with the
# embed server (see "GPU cohabitation" further below) and its own lowered
# SGLANG_MEM_FRACTION_STATIC, the SECOND card (nothing else sharing it)
# can usually be left at SGLang's own auto-computed default — only set
# SGLANG_MEM_FRACTION_STATIC_INTERACTIVE if something else ever needs to
# share that second card too (see run_sglang.sh's own declaration of it).

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

# ============================================================================
# Qdrant vector database (optional) — Install_qdrant.sh / run_qdrant.sh
#   client class: backend/qdrant_store.py (QdrantStore)
#   populate:     python -m data_builder.qdrant_populate --all
# ============================================================================
# Local Qdrant running as a Docker container, with persistent storage in
# data/qdrant/ and one collection ("words") holding word embeddings for
# every language — one tenant per language (a "lang" payload field indexed
# with is_tenant=true). Install_qdrant.sh pulls the image; run_qdrant.sh
# starts the container and (via QdrantStore.ensure_collection) creates the
# collection + tenant index. Every value below has the same fallback baked
# into the scripts and the class, so this block is only needed to override
# one. The vector size is normally probed from the embed model
# (BAAI/bge-m3 -> 1024); set QDRANT_VECTOR_SIZE to skip that probe.
# export QDRANT_IMAGE="qdrant/qdrant:latest"
# export QDRANT_CONTAINER="crosswordfalcon-qdrant"
# export QDRANT_HOST="127.0.0.1"
# export QDRANT_PORT="6333"
# export QDRANT_GRPC_PORT="6334"
# export QDRANT_COLLECTION="words"
# export QDRANT_DISTANCE="Cosine"
# export QDRANT_VECTOR_SIZE="1024"          # bge-m3; unset -> probe the embed server
# export QDRANT_ON_DISK="1"                 # vectors on disk instead of RAM — SSD storage only (slow on HDD)
# export QDRANT_URL=""                      # full base URL; overrides HOST/PORT (e.g. Qdrant Cloud)
# export QDRANT_API_KEY=""                  # sent as the api-key header when non-empty
