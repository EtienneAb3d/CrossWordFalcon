#!/usr/bin/env python3
"""
Best-effort local hardware detection for the info badge in the web UI
(frontend/static/script.js) — reports whether backend/clues.py's LLM call
is likely running on CPU or GPU, and if GPU, which one and how much VRAM.
This probes the machine directly (nvidia-smi / macOS's sysctl and
system_profiler); it does NOT query the separate LLM server process
(llama_cpp.server, see run_llm.sh) itself — that process exposes no such
endpoint. It reports what hardware is actually present and, per
run_llm.sh's own GPU_CMAKE_ARGS detection, would normally be used — on a
machine where run_llm.sh fell back to CPU despite a GPU being present (a
missing CUDA Toolkit or Xcode Command Line Tools, see run_llm.sh), this
can overstate GPU usage. A documented limitation, not a bug: there's no
cheaper way to know for certain without instrumenting the LLM server
process itself.
"""
import platform
import re
import shutil
import subprocess

_PROBE_TIMEOUT = 5


def _detect_nvidia_gpu():
    """First NVIDIA GPU reported by `nvidia-smi`, if any — total VRAM is a
    real, dedicated figure here (unlike Apple Silicon's unified memory
    below), so `unified_memory` is always False for this branch."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    if not out:
        return None
    # First GPU only — good enough for this project's local-dev use case;
    # a multi-GPU box would just report its first card.
    name, _, vram = out.splitlines()[0].partition(",")
    try:
        vram_mb = int(float(vram.strip()))
    except ValueError:
        vram_mb = None
    return {"gpu_name": name.strip(), "gpu_vram_mb": vram_mb, "unified_memory": False}


def _detect_apple_gpu():
    """Apple Silicon's on-die GPU, if this is a Mac. `system_profiler`
    doesn't report a VRAM figure for it (verified directly: no "VRAM"
    line at all in `system_profiler SPDisplaysDataType`'s output on
    Apple Silicon, unlike discrete GPUs) — Apple Silicon has no dedicated
    VRAM to report, it shares the machine's own RAM (`sysctl hw.memsize`)
    with the CPU, so that total is reported instead, flagged via
    `unified_memory: True` so callers don't present it as if it were
    dedicated VRAM the way an NVIDIA card's figure is."""
    if platform.system() != "Darwin":
        return None
    try:
        out = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=True,
        ).stdout
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    name_match = re.search(r"Chipset Model:\s*(.+)", out)
    if not name_match:
        return None
    vram_mb = None
    try:
        mem_bytes = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=True,
        ).stdout.strip()
        vram_mb = int(mem_bytes) // (1024 * 1024)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return {"gpu_name": name_match.group(1).strip(), "gpu_vram_mb": vram_mb, "unified_memory": True}


def get_system_info(llm_model):
    """Returns {llm_model, compute: "gpu"|"cpu", gpu_name, gpu_vram_mb,
    unified_memory} for the info badge. gpu_name/gpu_vram_mb are None
    when compute is "cpu", or when a present GPU's details couldn't be
    determined (a probe failing is treated as "no GPU found", not an
    error — this is a nice-to-have status display, never worth failing
    a request over)."""
    gpu = _detect_nvidia_gpu() or _detect_apple_gpu()
    return {
        "llm_model": llm_model,
        "compute": "gpu" if gpu else "cpu",
        "gpu_name": gpu["gpu_name"] if gpu else None,
        "gpu_vram_mb": gpu["gpu_vram_mb"] if gpu else None,
        "unified_memory": bool(gpu and gpu.get("unified_memory")),
    }
