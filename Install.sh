#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Install complete. Activate the venv with: source .venv/bin/activate"
echo "Start the app: ./run_Falcon.sh"
echo
echo "Optional: to run the default local LLM that generates crossword clues,"
echo "run: pip install -r requirements-llama.txt && ./run_llm.sh"
