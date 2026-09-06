#!/usr/bin/env bash
# test_llm.sh — diagnostic du serveur LLM local (ou distant) actuellement
# configure. En trois temps :
#   1. liste les modeles exposes par le serveur (GET /v1/models) ;
#   2. envoie une PREMIERE requete de generation d'une courte phrase au
#      hasard avec reasoning_effort="none" ;
#   3. envoie une SECONDE requete identique avec reasoning_effort="low".
# Pour chaque requete, la reponse JSON BRUTE et complete est affichee, puis
# un resume (content / reasoning_content / reasoning_tokens / presence de
# <think>...</think>) — de maniere a voir directement le comportement
# "THINK" reellement produit par le moteur + la configuration en place
# (voir dans env_default.sh les notes sur CHATBOT_THINK_FILTER et
# SGLANG_CHAT_TEMPLATE_KWARGS / LLAMA_CHAT_TEMPLATE_KWARGS).
#
# Ne depend que de curl + python3 (deja requis par le projet).
set -euo pipefail
cd "$(dirname "$0")"

if [ -f env.sh ]; then
    # shellcheck disable=SC1091
    . ./env.sh
elif [ -f env_default.sh ]; then
    # shellcheck disable=SC1091
    . ./env_default.sh
fi

LLM_PORT="${LLM_PORT:-3002}"
BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:${LLM_PORT}/v1/chat/completions}"
API_KEY="${LLM_API_KEY:-EMPTY}"
# /v1/models vit a cote de /v1/chat/completions sur un serveur
# OpenAI-compatible (llama.cpp, SGLang, Mistral...).
MODELS_URL="${BASE_URL%/chat/completions}/models"

echo "==================================================================="
echo " test_llm.sh"
echo "==================================================================="
echo "Serveur       : $BASE_URL"
echo "Modele (env)  : ${LLM_MODEL:-<non defini>}"
echo
echo "--- Modeles exposes ($MODELS_URL) ---"
models_json="$(curl -sS -H "Authorization: Bearer $API_KEY" "$MODELS_URL" || true)"
if [ -z "$models_json" ]; then
    echo "  (aucune reponse — le serveur LLM est-il demarre ? ./run_llm.sh)" >&2
else
    printf '%s\n' "$models_json" | python3 -m json.tool 2>/dev/null || printf '%s\n' "$models_json"
fi

# Modele a utiliser : LLM_MODEL si defini, sinon le premier de la liste.
MODEL="${LLM_MODEL:-}"
if [ -z "$MODEL" ] && [ -n "$models_json" ]; then
    MODEL="$(printf '%s' "$models_json" \
        | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["data"][0]["id"])' 2>/dev/null || true)"
fi
if [ -z "$MODEL" ]; then
    echo >&2
    echo "Impossible de determiner un modele a tester (serveur injoignable ?)." >&2
    exit 1
fi

PROMPTS=(
    "Ecris une seule phrase courte a propos de la mer."
    "Donne une phrase courte contenant le mot 'horloge'."
    "Invente une phrase de moins de dix mots sur un renard."
    "Ecris une breve phrase a propos du silence."
    "Une phrase courte sur une tasse de cafe, s'il te plait."
    "Compose une courte phrase evoquant la pluie sur une fenetre."
)
PROMPT="${PROMPTS[$((RANDOM % ${#PROMPTS[@]}))]}"

echo
echo "Modele teste  : $MODEL"
echo "Prompt (hasard): $PROMPT"

run_one() {
    effort="$1"
    echo
    echo "==================================================================="
    echo " Requete avec reasoning_effort = \"$effort\""
    echo "==================================================================="
    body="$(python3 -c '
import json, sys
print(json.dumps({
    "model": sys.argv[1],
    "messages": [{"role": "user", "content": sys.argv[2]}],
    "max_tokens": 256,
    "stream": False,
    "reasoning_effort": sys.argv[3],
}))' "$MODEL" "$PROMPT" "$effort")"
    resp="$(curl -sS -H "Content-Type: application/json" \
        -H "Authorization: Bearer $API_KEY" \
        -d "$body" "$BASE_URL" || true)"
    echo "--- Reponse JSON brute ---"
    printf '%s\n' "$resp" | python3 -m json.tool 2>/dev/null || printf '%s\n' "$resp"
    echo "--- Resume ---"
    printf '%s' "$resp" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception as e:
    print("  (reponse non-JSON : %s)" % e)
    sys.exit(0)
ch = (d.get("choices") or [{}])[0]
msg = ch.get("message", {}) or {}
content = msg.get("content")
rc = msg.get("reasoning_content")
usage = d.get("usage", {}) or {}
print("  finish_reason         :", ch.get("finish_reason"))
print("  message.content        :", repr(content))
print("  message.reasoning_content:", repr(rc))
print("  usage.reasoning_tokens :", usage.get("reasoning_tokens"))
if isinstance(content, str):
    print("  <think> present dans content  :", "<think>" in content)
    print("  </think> present dans content :", "</think>" in content)
'
}

run_one none
run_one low

echo
echo "==================================================================="
echo " Comment lire ce resultat (choix de CHATBOT_THINK_FILTER dans env.sh)"
echo "==================================================================="
echo "  - 'content' sans <think> ni </think>, reasoning_content vide/absent"
echo "      -> CHATBOT_THINK_FILTER=\"none\" (le moins couteux, une fois verifie)"
echo "  - un </think> apparait DANS 'content' mais jamais de <think> ouvrant"
echo "      -> CHATBOT_THINK_FILTER=\"close_only\""
echo "  - un bloc <think>...</think> complet apparait dans 'content'"
echo "      -> CHATBOT_THINK_FILTER=\"open_close\""
echo "  Si 'none' et 'low' donnent le meme comportement, le modele ne gere"
echo "  qu'un simple on/off (cas des chat templates Qwen3) : garder \"none\"."
