#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# librsvg (rsvg-convert) — a runtime dependency of the app itself, not just a
# build/dev tool: backend/svg_export.py's save_grid_png() calls it after
# every successfully generated grid to render the GRID_SAMPLES/ PNG (best-
# effort — a missing binary is logged as a warning, never fails the
# request, which is exactly the gap that let this go unnoticed until now).
# Also used to render frontend/static/logo.png from its source SVG (see the
# style-guide SKILL). Installed here so a fresh machine has it from the
# start instead of only discovering the gap from a runtime warning later.
if ! command -v rsvg-convert >/dev/null 2>&1; then
    echo "Installing librsvg (rsvg-convert)..."
    if [ "$(uname -s)" = "Darwin" ]; then
        if command -v brew >/dev/null 2>&1; then
            brew install librsvg || echo "Warning: 'brew install librsvg' failed — install it manually."
        else
            echo "Warning: Homebrew not found — install librsvg manually (brew install librsvg)."
        fi
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y librsvg2-bin \
            || echo "Warning: 'apt-get install librsvg2-bin' failed — install it manually."
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y librsvg2-tools \
            || echo "Warning: 'dnf install librsvg2-tools' failed — install it manually."
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm librsvg \
            || echo "Warning: 'pacman -S librsvg' failed — install it manually."
    else
        echo "Warning: no supported package manager found — install librsvg manually"
        echo "(provides rsvg-convert): https://gitlab.gnome.org/GNOME/librsvg"
    fi
fi

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# llama.cpp Python bindings — installed unconditionally, at the user's
# explicit request ("tout en installant l'option LlamaCpp"): llama.cpp is
# run_llm.sh's default engine and the portable fallback for any hardware
# (CPU included), so it must always be available regardless of which
# engine the interactive configuration step further below ends up
# selecting. A build failure here is a warning, not fatal — run_llm.sh's
# own GPU-aware rebuild step can still recover it, or it can be installed
# by hand later (pip install -r requirements-llama.txt).
pip install -r requirements-llama.txt \
    || echo "Warning: 'pip install -r requirements-llama.txt' a echoue — llama-cpp-python devra etre installe a la main ou sera reconstruit par run_llm.sh au premier lancement."

# data/reference_corpus_<lang>.tar.xz (optional, one archive per language) —
# a pre-built snapshot of the CAPPED reference corpus (data/reference_
# corpus/<lang>_sentences.txt, at most build_sentence_corpus.MAX_SENTENCES_
# PER_LANGUAGE sentences — see that script), so a fresh clone gets backend/
# example_sentences.py's own LLM-grounding lookups working without any OPUS
# download at all. Split per language (rather than one combined archive) so
# each file stays under GitHub's 100MB hard file-size limit. Each language
# is unpacked independently if its archive is present; a language with no
# archive just has no example-sentence grounding for that language until
# data_builder/build_sentence_corpus.py is run for it. NOT sufficient to regenerate a
# language's data/wordlist_<lang>_full.tsv from scratch, though — that
# needs the FULL, uncapped corpus (<lang>_sentences_full.txt), never
# published here (see build_sentence_corpus.py/build_wordlist_freq.py's own
# docstrings for why) — but data/wordlist_<lang>_full.tsv is itself already
# checked into the repo, so a fresh clone never needs to rebuild it just to
# use the app; only actually regenerating it (e.g. after a pipeline change)
# needs the full corpus, via data_builder/build_sentence_corpus.py from scratch.
found_corpus_archive=0
mkdir -p data/reference_corpus
for lang in fr en de es it; do
    archive="data/reference_corpus_${lang}.tar.xz"
    if [ -f "$archive" ]; then
        echo "Extracting $archive..."
        # compress_reference_corpus.py builds this archive with
        # `tar -C data/reference_corpus <lang>_sentences.txt` (see that
        # script), so the file is stored WITHOUT a reference_corpus/ prefix
        # inside the archive — extracting with `-C data` (a real bug, found
        # and fixed live: landed <lang>_sentences.txt straight in data/
        # instead of data/reference_corpus/) would silently miss the
        # directory this project's own code actually reads from
        # (backend/example_sentences.py's CORPUS_DIR). Must extract into
        # data/reference_corpus itself, matching how it was packed.
        tar -xJf "$archive" -C data/reference_corpus
        found_corpus_archive=1
    fi
done
if [ "$found_corpus_archive" -eq 0 ]; then
    echo "No data/reference_corpus_<lang>.tar.xz archives found — skipping"
    echo "(optional; run data_builder/build_sentence_corpus.py per language to build the"
    echo "reference corpus from scratch)."
fi

# Première initialisation du panneau "Actu Croisée" (flux RSS + grilles
# scrappées), à la demande explicite de l'utilisateur : "Lors de
# l'installation sur une nouvelle machine, il faudra automatiquement
# initialiser une première fois les RSS et SCRAPP si ils n'existent pas
# encore." Sans ça, une machine fraîchement installée montrerait un
# panneau vide (voir backend/app.py's GET /api/rss et /api/scrapp,
# tous deux dégradant gracieusement vers une liste vide si le fichier
# combined.json n'existe pas) jusqu'au premier passage du planificateur
# quotidien (8h, voir _rss_daily_scheduler dans backend/app.py) — ce qui
# peut représenter jusqu'à 24h d'attente selon l'heure de l'installation.
# Chaque fichier est vérifié indépendamment (jamais réécrit s'il existe
# déjà, y compris sur une réinstallation) et l'échec de l'un ne bloque
# jamais l'autre ni le reste de l'installation — un problème réseau
# ponctuel ici ne doit pas empêcher l'installation d'aboutir ; le
# planificateur quotidien réessaiera de toute façon le lendemain.
if [ ! -f RSS/combined.json ]; then
    echo "Initialisation du flux RSS (première fois)..."
    python3 -c "from scrapper import fetch_rss_feeds; fetch_rss_feeds.fetch_all()" \
        || echo "Warning: echec de l'initialisation du flux RSS — le planificateur quotidien reessaiera demain."
fi
if [ ! -f SCRAPP/combined.json ]; then
    echo "Initialisation des grilles scrappees (SCRAPP, premiere fois)..."
    python3 -c "from scrapper import fetch_grid_links; fetch_grid_links.fetch_all()" \
        || echo "Warning: echec de l'initialisation de SCRAPP — le planificateur quotidien reessaiera demain."
fi

echo

# ===========================================================================
# Configuration du moteur LLM local (definitions de mots croises + ChatBot)
# ===========================================================================
# Install.sh pose desormais les questions permettant de choisir le moteur
# et le modele, en expliquant les compromis (vitesse / qualite / VRAM /
# risques de configuration), a la demande explicite de l'utilisateur. Le
# choix est ecrit dans env.sh entre les marqueurs BEGIN/END LLM AUTOCONFIG,
# que ce script retire puis reecrit sans jamais toucher au reste du fichier
# (ports, cle API personnalisee, etc.). llama.cpp est installe quoi qu'il
# arrive (fait plus haut, requirements-llama.txt) — moteur par defaut et
# repli portable sur tout materiel ; SGLang n'est installe que si
# l'utilisateur choisit une option qui l'utilise.
#
# stdin non interactif (CI, `curl ... | bash`) : aucune question posee,
# configuration llama.cpp sure appliquee automatiquement (Qwen3.5-4B si un
# GPU est present, Qwen3.5-0.8B sinon) — relancer ./Install.sh dans un vrai
# terminal pour choisir SGLang (plus rapide) ou un autre modele.

LLM_MARKER_BEGIN="# BEGIN LLM AUTOCONFIG (gere par Install.sh — modifiez en dehors de ce bloc, jamais a l'interieur)"
LLM_MARKER_END="# END LLM AUTOCONFIG"
SGLANG_MARKER_BEGIN_LEGACY="# BEGIN SGLANG AUTOCONFIG (gere par Install.sh — modifiez en dehors de ce bloc, jamais a l'interieur)"
SGLANG_MARKER_END_LEGACY="# END SGLANG AUTOCONFIG"
SGLANG_VENV=".venv-sglang"
SGLANG_SRC="sglang-src"
SGLANG_REPO_URL="https://github.com/sgl-project/sglang.git"

sglang_already_working() {
    [ -x "$SGLANG_VENV/bin/python3" ] && "$SGLANG_VENV/bin/python3" -c "import sglang" >/dev/null 2>&1
}

IS_APPLE_SILICON=false
HAS_NVIDIA_GPU=false
GPU_NAME=""
GPU_VRAM_MB=0
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    IS_APPLE_SILICON=true
elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    HAS_NVIDIA_GPU=true
    GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)"
    GPU_VRAM_MB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -cd '0-9' || true)"
    if [ -z "$GPU_VRAM_MB" ]; then GPU_VRAM_MB=0; fi
fi

if [ ! -f env.sh ]; then
    cp env_default.sh env.sh
fi

strip_env_block() {
    b="$1"; e="$2"
    if grep -qF "$b" env.sh 2>/dev/null; then
        awk -v begin="$b" -v end="$e" '
            $0 == begin { skip = 1; next }
            $0 == end { skip = 0; next }
            !skip { print }
        ' env.sh > env.sh.new && mv env.sh.new env.sh
    fi
}

append_llm_block() {
    strip_env_block "$LLM_MARKER_BEGIN" "$LLM_MARKER_END"
    strip_env_block "$SGLANG_MARKER_BEGIN_LEGACY" "$SGLANG_MARKER_END_LEGACY"
    {
        echo ""
        echo "$LLM_MARKER_BEGIN"
        cat
        echo "$LLM_MARKER_END"
    } >> env.sh
}

configure_llamacpp() {
    key="$1"; model=""; repo=""; file=""
    case "$key" in
        0.8b) model="Qwen/Qwen3.5-0.8B"; repo="bartowski/Qwen_Qwen3.5-0.8B-GGUF"; file="Qwen_Qwen3.5-0.8B-bf16.gguf" ;;
        2b)   model="Qwen/Qwen3.5-2B";   repo="bartowski/Qwen_Qwen3.5-2B-GGUF";   file="Qwen_Qwen3.5-2B-bf16.gguf" ;;
        4b)   model="Qwen/Qwen3.5-4B";   repo="bartowski/Qwen_Qwen3.5-4B-GGUF";   file="Qwen_Qwen3.5-4B-bf16.gguf" ;;
        9b)   model="Qwen/Qwen3.5-9B";   repo="bartowski/Qwen_Qwen3.5-9B-GGUF";   file="Qwen_Qwen3.5-9B-Q4_K_M.gguf" ;;
        27b)  model="Qwen/Qwen3.8-27B";  repo="unsloth/Qwen3.8-27B-GGUF";         file="Qwen3.8-27B-UD-Q2_K_XL.gguf" ;;
        *)    model="Qwen/Qwen3.5-4B";   repo="bartowski/Qwen_Qwen3.5-4B-GGUF";   file="Qwen_Qwen3.5-4B-bf16.gguf" ;;
    esac
    append_llm_block <<EOF
export LLM_ENGINE="llama_cpp"
export LLM_MODEL="$model"
export LLM_BASE_URL="http://127.0.0.1:\${LLM_PORT}/v1/chat/completions"
export LLM_API_KEY="EMPTY"
export LLAMA_GGUF_REPO="$repo"
export LLAMA_GGUF_FILE="$file"
export LLAMA_CHAT_TEMPLATE_KWARGS='{"enable_thinking": false}'
EOF
    echo "  env.sh configure : moteur=llama.cpp, modele=$model"
}

configure_sglang_cuda() {
    gcc_line=""
    # CUDA's nvcc rejects a host gcc newer than 12 (verified live on CUDA
    # 13.0) — pick the NEWEST already-installed gcc-N that nvcc still
    # accepts (12 first, then 11, then 10), rather than the oldest.
    old_gcc=""
    for g in gcc-12 gcc-11 gcc-10; do
        if [ -x "/usr/bin/$g" ]; then old_gcc="/usr/bin/$g"; break; fi
    done
    if [ -n "$old_gcc" ]; then
        gcc_line="export SGLANG_NVCC_CC=\"$old_gcc\""
    fi
    append_llm_block <<EOF
export LLM_ENGINE="sglang"
export SGLANG_MODEL_PATH="bartowski/Qwen_Qwen3-4B-GGUF/Qwen_Qwen3-4B-Q4_K_M.gguf"
export SGLANG_QUANTIZATION="gguf"
export LLM_MODEL="Qwen/Qwen3-4B"
export LLM_BASE_URL="http://127.0.0.1:\${LLM_PORT}/v1/chat/completions"
export LLM_API_KEY="EMPTY"
export SGLANG_CHAT_TEMPLATE_KWARGS='{"enable_thinking":false}'
export SGLANG_REASONING_PARSER="qwen3"
export SGLANG_MEM_FRACTION_STATIC="0.78"
$gcc_line
export CHATBOT_THINK_FILTER="close_only"
EOF
    echo "  env.sh configure : moteur=sglang (CUDA), modele=Qwen3-4B-Q4_K_M.gguf"
}

configure_sglang_mlx() {
    append_llm_block <<EOF
export LLM_ENGINE="sglang"
export SGLANG_MODEL_PATH="mlx-community/Qwen3-4B-4bit"
export LLM_MODEL="mlx-community/Qwen3-4B-4bit"
export LLM_BASE_URL="http://127.0.0.1:\${LLM_PORT}/v1/chat/completions"
export LLM_API_KEY="EMPTY"
export SGLANG_CHAT_TEMPLATE_KWARGS='{"enable_thinking":false}'
export SGLANG_REASONING_PARSER="qwen3"
export CHATBOT_THINK_FILTER="close_only"
EOF
    echo "  env.sh configure : moteur=sglang (MLX/Apple Silicon), modele=Qwen3-4B-4bit"
}

configure_mistral() {
    key="$1"
    if [ -z "$key" ]; then key="COLLEZ-VOTRE-CLE-API-MISTRAL-ICI"; fi
    append_llm_block <<EOF
export LLM_ENGINE="llama_cpp"
export LLM_BASE_URL="https://api.mistral.ai/v1/chat/completions"
export LLM_MODEL="mistral-small-latest"
export LLM_API_KEY="$key"
EOF
    echo "  env.sh configure : API cloud Mistral (mistral-small-latest)"
    if [ "$key" = "COLLEZ-VOTRE-CLE-API-MISTRAL-ICI" ]; then
        echo "  ATTENTION : editez env.sh pour y coller votre vraie cle API Mistral avant de lancer l'app."
    fi
}

install_sglang() {
    if sglang_already_working; then
        echo "  SGLang deja installe et fonctionnel ($SGLANG_VENV) — pas de reinstallation."
        return 0
    fi
    echo "  Installation de SGLang (peut prendre plusieurs minutes)..."
    if [ "$IS_APPLE_SILICON" = true ]; then
        py312=""
        if command -v python3.12 >/dev/null 2>&1; then
            py312="python3.12"
        elif command -v brew >/dev/null 2>&1; then
            brew install python@3.12 || true
            if command -v python3.12 >/dev/null 2>&1; then
                py312="python3.12"
            fi
        fi
        if [ -z "$py312" ]; then
            echo "  Warning: Python 3.12 introuvable (Homebrew absent ou echec de l'installation) — SGLang non installe."
            return 1
        fi
        "$py312" -m venv "$SGLANG_VENV" || return 1
        if [ ! -d "$SGLANG_SRC" ]; then
            git clone --depth 1 "$SGLANG_REPO_URL" "$SGLANG_SRC" || return 1
        fi
        if (cd "$SGLANG_SRC/python" && SGLANG_BUILD_RUST_EXTS=none "$OLDPWD/$SGLANG_VENV/bin/pip" install -e ".[all_mps]"); then
            return 0
        fi
        echo "  Warning: installation de SGLang (MLX) echouee."
        return 1
    else
        python3 -m venv "$SGLANG_VENV" || return 1
        "$SGLANG_VENV/bin/pip" install --upgrade pip uv || return 1
        if "$SGLANG_VENV/bin/uv" pip install --python "$SGLANG_VENV/bin/python3" --prerelease=allow sglang; then
            return 0
        fi
        echo "  Warning: installation de SGLang (CUDA) echouee."
        return 1
    fi
}

stop_running_llm_server() {
    port="3002"
    if [ -f env.sh ]; then
        port="$(source env.sh >/dev/null 2>&1; echo "${LLM_PORT:-3002}")"
    fi
    pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
    if [ -n "$pids" ]; then
        echo "  Arret du serveur LLM en cours (port $port) — la reconfiguration necessite un redemarrage."
        kill $pids 2>/dev/null || true
        sleep 1
        pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
        [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
    fi
}

apply_choice() {
    # $1 = id du choix moteur ; $2 = cle modele llama.cpp ; $3 = cle API Mistral
    case "$1" in
        sglang_cuda)
            if install_sglang; then
                configure_sglang_cuda
            else
                echo "  -> repli sur llama.cpp (Qwen3.5-4B)."
                configure_llamacpp 4b
            fi
            ;;
        sglang_mlx)
            if install_sglang; then
                configure_sglang_mlx
            else
                echo "  -> repli sur llama.cpp (Qwen3.5-4B)."
                configure_llamacpp 4b
            fi
            ;;
        llamacpp) configure_llamacpp "${2:-4b}" ;;
        mistral)  configure_mistral "${3:-}" ;;
        keep)     echo "  env.sh laisse tel quel." ;;
    esac
}

echo "==================================================================="
echo " Configuration du moteur LLM (generation des definitions + ChatBot)"
echo "==================================================================="
echo
echo "Materiel detecte :"
if [ "$IS_APPLE_SILICON" = true ]; then
    echo "  - Apple Silicon (backend Metal / MLX disponible pour SGLang)"
elif [ "$HAS_NVIDIA_GPU" = true ]; then
    echo "  - GPU NVIDIA : ${GPU_NAME:-inconnu} (${GPU_VRAM_MB} Mo VRAM)"
else
    echo "  - Aucun GPU detecte : CPU uniquement (llama.cpp est la seule option locale)"
fi
echo

if [ ! -t 0 ]; then
    echo "stdin non interactif — aucune question posee."
    if [ "$HAS_NVIDIA_GPU" = true ] || [ "$IS_APPLE_SILICON" = true ]; then
        echo "Configuration sure appliquee : llama.cpp + Qwen3.5-4B."
        echo "Relancez ./Install.sh dans un terminal pour choisir SGLang (plus rapide)."
        apply_choice llamacpp 4b
    else
        echo "Configuration sure appliquee : llama.cpp + Qwen3.5-0.8B (aucun GPU)."
        apply_choice llamacpp 0.8b
    fi
    stop_running_llm_server
else
    MENU_IDS=()
    n=0
    llamacpp_opt=0
    if [ "$HAS_NVIDIA_GPU" = true ]; then
        n=$((n + 1)); MENU_IDS[$n]="sglang_cuda"
        echo "  $n) SGLang + Qwen3-4B (GGUF Q4_K_M)   [RECOMMANDE sur carte 12 Go type RTX 3060]"
        echo "       Vitesse : ~1-2 s/mot, ~11 Go VRAM. Qualite des definitions : correcte (4B)."
        echo "       Le plus rapide des bons choix locaux. Necessite une installation SGLang"
        echo "       unique (venv Python 3.12 dedie, .venv-sglang/, plusieurs minutes) — faite ici."
        echo "       Risque : le support GGUF de SGLang est CUDA-only et sensible aux versions."
        echo "       CE modele/quant precis est verifie en direct. Les GGUF Qwen3.5 / Qwen3.8"
        echo "       NE fonctionnent PAS sur ce chemin (bugs d'architecture hybride non resolus"
        echo "       en amont - voir CLAUDE.md). N'utilisez ici que des modeles 'Qwen3-*'."
        if [ "$GPU_VRAM_MB" -gt 0 ] && [ "$GPU_VRAM_MB" -lt 6000 ]; then
            echo "       ATTENTION : ${GPU_VRAM_MB} Mo VRAM seulement - peut-etre insuffisant pour"
            echo "       ce modele ; une option llama.cpp avec un petit modele serait plus sure."
        fi
        echo
    fi
    if [ "$IS_APPLE_SILICON" = true ]; then
        n=$((n + 1)); MENU_IDS[$n]="sglang_mlx"
        echo "  $n) SGLang + Qwen3-4B-4bit (MLX)   [RECOMMANDE sur Apple Silicon]"
        echo "       Vitesse : ~1 s/mot. Qualite des definitions : correcte (4B)."
        echo "       Necessite une installation SGLang unique (venv Python 3.12 dedie) - faite ici."
        echo "       Risque : n'utiliser QUE des modeles 'Qwen3-*' (sans .5/.8) sur ce backend ;"
        echo "       Qwen3.5/Qwen3.8 crashent (architecture hybride). Ne jamais lancer deux"
        echo "       serveurs SGLang/MLX a la fois (OOM Metal observe)."
        echo
    fi
    n=$((n + 1)); MENU_IDS[$n]="llamacpp"; llamacpp_opt=$n
    echo "  $n) llama.cpp + un modele Qwen (portable : tout materiel, y compris CPU seul)"
    echo "       Support GGUF mature et stable. Plus lent que SGLang a modele egal."
    echo "       Le choix precis du modele (0.8B a 27B) est demande juste apres."
    echo
    n=$((n + 1)); MENU_IDS[$n]="mistral"
    echo "  $n) API cloud Mistral (mistral-small-latest)"
    echo "       Meilleure qualite, aucun materiel local requis, mais cle API payante"
    echo "       necessaire (console.mistral.ai)."
    echo
    n=$((n + 1)); MENU_IDS[$n]="keep"
    echo "  $n) Ne rien changer (garder la configuration actuelle d'env.sh)"
    echo

    default_n=$llamacpp_opt
    if [ "$HAS_NVIDIA_GPU" = true ] || [ "$IS_APPLE_SILICON" = true ]; then
        default_n=1
    fi

    ans=""
    read -rp "Votre choix [${default_n}] : " ans || ans=""
    if [ -z "$ans" ]; then ans="$default_n"; fi
    if ! printf '%s' "$ans" | grep -qE '^[0-9]+$' || [ "$ans" -lt 1 ] || [ "$ans" -gt "$n" ]; then
        echo "Choix invalide - application du defaut ($default_n)."
        ans="$default_n"
    fi
    choice="${MENU_IDS[$ans]}"

    model_key=""
    mistral_key=""
    if [ "$choice" = "llamacpp" ]; then
        echo
        echo "Modele llama.cpp :"
        echo "  a) Qwen3.5-0.8B  - ultra-rapide, tourne meme sans GPU, qualite faible (essais/tests)"
        echo "  b) Qwen3.5-2B    - rapide (GPU conseille), qualite passable"
        echo "  c) Qwen3.5-4B    - bon compromis sur petit GPU, qualite correcte"
        echo "  d) Qwen3.5-9B    - petit GPU, meilleure qualite (ancien defaut du projet)"
        echo "  e) Qwen3.8-27B   - GPU >= 12 Go, lent (~20-40 s/mot), meilleure qualite locale observee"
        llama_default="a"
        if [ "$HAS_NVIDIA_GPU" = true ] || [ "$IS_APPLE_SILICON" = true ]; then
            llama_default="c"
        fi
        mk=""
        read -rp "Votre choix [${llama_default}] : " mk || mk=""
        if [ -z "$mk" ]; then mk="$llama_default"; fi
        case "$mk" in
            a|A) model_key="0.8b" ;;
            b|B) model_key="2b" ;;
            c|C) model_key="4b" ;;
            d|D) model_key="9b" ;;
            e|E) model_key="27b" ;;
            *)   echo "Choix invalide - defaut ($llama_default)."
                 if [ "$llama_default" = "c" ]; then model_key="4b"; else model_key="0.8b"; fi ;;
        esac
    elif [ "$choice" = "mistral" ]; then
        echo
        read -rp "Cle API Mistral (laisser vide pour l'ajouter plus tard dans env.sh) : " mistral_key || mistral_key=""
    fi

    echo
    apply_choice "$choice" "$model_key" "$mistral_key"
    if [ "$choice" != "keep" ]; then
        stop_running_llm_server
    fi
fi

echo
echo "==================================================================="
echo " Installation terminee."
echo "==================================================================="
echo "  - Activer le venv : source .venv/bin/activate"
echo "  - Lancer l'app    : ./run_Falcon.sh"
echo "  - Lancer le LLM   : ./run_llm.sh   (moteur/modele choisi ci-dessus ; le modele"
echo "                      est telecharge au premier lancement)"
echo "  - Verifier le LLM : ./test_llm.sh  (liste les modeles + une generation de test,"
echo "                      utile pour voir le comportement <think> reel)"
echo "  - Embeddings      : ./run_embed.sh (serveur d'embeddings multilingue CPU,"
echo "                      BAAI/bge-m3 ; modele telecharge au premier lancement)."
echo "                      Classe cliente : backend/embedder.py (Embedder)."
echo "  - Base vectorielle (optionnel) : ./Install_qdrant.sh puis ./run_qdrant.sh"
echo "                      (Qdrant en Docker, collection 'words', 1 tenant par langue)."
echo "                      Alimenter : python -m data_builder.qdrant_populate --all"
echo "                      (necessite ./run_qdrant.sh + ./run_embed.sh lances)."
echo "  - Changer plus tard : relancez ./Install.sh, ou editez le bloc 'LLM AUTOCONFIG'"
echo "    dans env.sh."
