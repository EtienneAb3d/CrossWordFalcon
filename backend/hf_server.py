#!/usr/bin/env python3
"""
Fallback local LLM server for platforms without vLLM support (macOS ships no
vLLM wheels — see requirements-vllm.txt). Serves the same model via
transformers on an OpenAI-compatible /v1/chat/completions endpoint, so
backend/clues.py needs no code change to target it — only LLM_BASE_URL in
env.sh points here (port 8002) instead of vLLM. run_vllm.sh launches this
automatically when it detects macOS.

Runs on Apple Silicon (MPS) or CUDA if available, CPU otherwise. Unlike
vLLM, there's no constrained/guided JSON decoding here — clue generation
relies on the model following the "respond with JSON" instruction in the
prompt, with a lenient extraction fallback in backend/clues.py.

Usage:
    uvicorn backend.hf_server:app --port 8002
"""
import os

import torch
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_NAME = os.environ.get("LLM_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")


def _pick_device():
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


DEVICE = _pick_device()
print(f"[hf_server] loading {MODEL_NAME} on {DEVICE}...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    torch_dtype=torch.float32 if DEVICE == "cpu" else torch.float16,
).to(DEVICE)
print("[hf_server] model loaded, ready.")

app = FastAPI()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    temperature: float = 0.7
    response_format: dict | None = None
    max_tokens: int = 1024


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest):
    prompt = tokenizer.apply_chat_template(
        [m.model_dump() for m in req.messages],
        tokenize=False,
        add_generation_prompt=True,
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(DEVICE)
    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=req.max_tokens,
            do_sample=req.temperature > 0,
            temperature=max(req.temperature, 0.01),
        )
    generated = output[0][inputs["input_ids"].shape[1]:]
    text = tokenizer.decode(generated, skip_special_tokens=True)
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "device": DEVICE}
