#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Install complete. Activate the venv with: source .venv/bin/activate"
echo "Start the back end:   uvicorn backend.app:app --port 8001"
echo "Start the middleware: uvicorn frontend.server:app --port 8000"
echo
echo "Optional: to run the default local LLM (vLLM) that generates crossword"
echo "clues, on a Linux machine run: pip install -r requirements-vllm.txt && ./run_vllm.sh"
