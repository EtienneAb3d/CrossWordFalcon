#!/usr/bin/env python3
"""
Best-effort local hardware detection for the info badge in the web UI
(frontend/static/script.js): every detected GPU (this project's own dev
host has two — one for automatic-generation LLM calls, one for on-demand/
interactive ones, see run_llm.sh/run_sglang.sh's LLM_GPU_INDEX/LLM_
INTERACTIVE_GPU_INDEX), which model is assigned to each (the automatic-
generation LLM, the interactive/on-demand LLM when it's a genuinely
separate instance, and the embedding model from backend/embedder.py), plus
the machine's total RAM and CPU count. This probes the machine directly
(nvidia-smi / macOS's sysctl and system_profiler, /proc/meminfo,
os.cpu_count()); it does NOT query the LLM/embed server processes
themselves (llama_cpp.server / SGLang, see run_llm.sh/run_sglang.sh/
run_embed.sh) — none of them expose an endpoint for this. It reports what
hardware is actually present and, per each launcher's own GPU-index
env vars, would normally be used — on a machine where a launcher actually
fell back to CPU despite a GPU being present (a missing CUDA Toolkit or
Xcode Command Line Tools), this can overstate GPU usage. A documented
limitation, not a bug: there's no cheaper way to know for certain without
instrumenting the server processes themselves — *except* for
LLAMA_FORCE_CPU (see run_llm.sh), which is a deliberate, known-in-advance
choice rather than a hardware-capability guess, so get_system_info()
checks it directly and keeps both LLM roles (never the embedding model,
which run_embed.sh controls independently via its own EMBED_N_GPU_LAYERS)
off every GPU unconditionally when it's set — this works because
run_Falcon.sh (which starts this very process) sources the same env.sh
run_llm.sh/run_sglang.sh do, so the flag reaches every process alike. GPU
hardware detection itself never depends on this flag — the cards are
still physically there regardless of which role ends up assigned to one.
"""
import os
import platform
import re
import shutil
import subprocess
import threading

_PROBE_TIMEOUT = 5


def _detect_nvidia_gpus():
    """Every NVIDIA GPU reported by `nvidia-smi`, in device-index order —
    unlike a single-GPU probe, a machine with more than one card needs
    every one listed, not just the first, so each can be matched against
    LLM_GPU_INDEX/LLM_INTERACTIVE_GPU_INDEX below. Returns a list of
    {"index": int, "name": str, "vram_mb": int|None, "roles": []} (empty
    "roles", filled in by get_system_info() below) — empty list if
    nvidia-smi is missing or reports nothing."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return []
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            index = int(parts[0])
        except ValueError:
            continue
        try:
            vram_mb = int(float(parts[2]))
        except ValueError:
            vram_mb = None
        gpus.append({"index": index, "name": parts[1], "vram_mb": vram_mb, "roles": []})
    return gpus


def _detect_apple_gpu():
    """Apple Silicon's on-die GPU, if this is a Mac. `system_profiler`
    doesn't report a VRAM figure for it (verified directly: no "VRAM"
    line at all in `system_profiler SPDisplaysDataType`'s output on
    Apple Silicon, unlike discrete GPUs) — Apple Silicon has no dedicated
    VRAM to report, it shares the machine's own RAM (`sysctl hw.memsize`)
    with the CPU, so that total is reported instead, flagged via a
    top-level `unified_memory: True` (get_system_info() below) so callers
    don't present it as if it were dedicated VRAM the way an NVIDIA card's
    figure is. Returned in the same {"index", "name", "vram_mb", "roles"}
    shape as _detect_nvidia_gpus(), always as a single-entry list (index
    0) — a Mac never has more than one usable GPU for this purpose."""
    if platform.system() != "Darwin":
        return []
    try:
        out = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=True,
        ).stdout
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return []
    name_match = re.search(r"Chipset Model:\s*(.+)", out)
    if not name_match:
        return []
    vram_mb = None
    try:
        mem_bytes = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=True,
        ).stdout.strip()
        vram_mb = int(mem_bytes) // (1024 * 1024)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return [{"index": 0, "name": name_match.group(1).strip(), "vram_mb": vram_mb, "roles": []}]


def _detect_ram_mb():
    """Total system RAM in MB, best-effort — /proc/meminfo on Linux,
    `sysctl hw.memsize` on macOS (the same probe _detect_apple_gpu already
    uses for its own unified-memory figure). None if neither works, or on
    any other platform."""
    system = platform.system()
    if system == "Linux":
        try:
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) // 1024
        except (OSError, ValueError, IndexError):
            return None
        return None
    if system == "Darwin":
        try:
            mem_bytes = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=True,
            ).stdout.strip()
            return int(mem_bytes) // (1024 * 1024)
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired, ValueError):
            return None
    return None


_prev_cpu_times_lock = threading.Lock()
# (idle_ticks, total_ticks) from the previous /proc/stat sample, module-
# level so consecutive calls (see backend/app.py's periodic sampler below)
# can compute a real delta-based percentage without ever sleeping to force
# one themselves.
_prev_cpu_times = None


def _read_proc_stat_cpu_times():
    """Linux only: the aggregate "cpu  ..." line of /proc/stat, summed
    across every core, as (idle, total) USER_HZ ticks — or None if the
    file is unreadable/unparseable."""
    try:
        with open("/proc/stat", encoding="utf-8") as f:
            line = f.readline()
    except OSError:
        return None
    parts = line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    try:
        nums = [int(x) for x in parts[1:]]
    except ValueError:
        return None
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
    return idle, sum(nums)


def _cpu_percent_linux():
    """Delta-based CPU occupancy (0-100) across every core combined, from
    two /proc/stat samples — this project's own version of the same
    self-priming technique psutil's non-blocking cpu_percent() uses,
    without adding a new dependency: never sleeps to force a delta, it
    just compares the current reading against whatever the *previous*
    call (from anywhere in this process) last saw. Returns None on the
    very first sample after process start (no baseline yet) or if /proc/
    stat can't be read."""
    global _prev_cpu_times
    cur = _read_proc_stat_cpu_times()
    if cur is None:
        return None
    with _prev_cpu_times_lock:
        prev = _prev_cpu_times
        _prev_cpu_times = cur
    if prev is None:
        return None
    delta_idle = cur[0] - prev[0]
    delta_total = cur[1] - prev[1]
    if delta_total <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * (1 - delta_idle / delta_total)))


def _cpu_percent_macos():
    """Approximation, disclosed as such: macOS has no equivalent of /proc/
    stat this project can read without a new dependency, so this falls
    back to the 1-minute load average (os.getloadavg()) normalized by core
    count — a real measure of demand, but not the same thing as the
    instantaneous occupancy a tool like Activity Monitor reports (it can
    read higher than true occupancy on a machine with many processes
    waiting on I/O rather than actually running)."""
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        return None
    count = os.cpu_count() or 1
    return max(0.0, min(100.0, 100.0 * load1 / count))


def _nvidia_gpu_utilization():
    """Every NVIDIA GPU's own instantaneous utilization.gpu percentage
    (0-100), in device-index order — a real instantaneous reading, unlike
    CPU above, so it needs no delta/priming. Returns a list of {"index",
    "percent"} — empty if nvidia-smi is missing or reports nothing. A
    separate, cheap query from _detect_nvidia_gpus() (name/VRAM, probed
    once per page load): this one runs on its own fast, repeating
    schedule (see backend/app.py), so it only ever asks for what it
    actually needs."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return []
    result = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            index = int(parts[0])
            percent = float(parts[1])
        except ValueError:
            continue
        result.append({"index": index, "percent": max(0.0, min(100.0, percent))})
    return result


def sample_resource_usage():
    """Best-effort instantaneous resource-occupancy sample for the info
    badge's small meters (see frontend/static/script.js), at the user's
    explicit request: "un petit vu-mètre indiquant le taux d'occupation
    de chaque ressource (GPUs / CPU). Un seul vu-mètre pour l'ensemble des
    CPUs." Deliberately NOT computed per-request — see backend/app.py's
    own periodic sampler, which caches the result and lets every
    POST /api/presence heartbeat just read the cached value: a real GPU
    query is a subprocess spawn, too costly to pay for every heartbeat of
    every connected tab.

    Returns {"cpu_percent": float|None, "gpu_percent": [{"index",
    "percent"}, ...]}. `cpu_percent` is a single figure for every CPU
    combined (never per-core) — None on the very first sample of a fresh
    process (no delta baseline yet) or on an unsupported platform.
    `gpu_percent` is empty on Apple Silicon (no equivalent quick per-GPU
    utilization query exists there the way nvidia-smi provides one) or a
    machine with no GPU at all."""
    system = platform.system()
    if system == "Linux":
        cpu_percent = _cpu_percent_linux()
    elif system == "Darwin":
        cpu_percent = _cpu_percent_macos()
    else:
        cpu_percent = None
    return {"cpu_percent": cpu_percent, "gpu_percent": _nvidia_gpu_utilization()}


def get_system_info(llm_model, interactive_llm_model=None, embed_model=None, embed_on_gpu=False):
    """Returns {gpus, cpu_roles, unified_memory, cpu_count, ram_total_mb,
    llm_model, interactive_llm_model, embed_model} for the info badge.

    `gpus` is a list of {"index", "name", "vram_mb", "roles"} — one entry
    per detected GPU (possibly empty, possibly more than one), each
    `roles` a list of {"kind": "llm_auto"|"llm_interactive"|"embedding",
    "model": str} for whichever of the three model slots below actually
    landed on that card. `cpu_roles` holds the same per-slot shape for
    whichever slot didn't land on any GPU (every slot, on a CPU-only
    machine). The frontend (frontend/static/script.js) localizes `kind`
    into a label via i18n.js — this module never returns pre-translated
    text.

    `interactive_llm_model` should be passed only when it is genuinely a
    separate instance from `llm_model` (see backend/app.py's own
    `interactive_clue_generator is clue_generator` check) — passing it
    when the two share one instance would double-report the same model.
    `embed_model`/`embed_on_gpu` describe backend/embedder.py's own
    server (embed_on_gpu mirrors EMBED_N_GPU_LAYERS > 0, see
    run_embed.sh) — `embed_model=None` omits it entirely (e.g. Qdrant/the
    embed server were never configured).

    LLAMA_FORCE_CPU forces both LLM roles onto `cpu_roles` unconditionally
    (never the embedding model, controlled independently by
    `embed_on_gpu`) — a real, deliberate choice made ahead of time (see
    run_llm.sh), not a guess about hardware capability the way the probes
    above are. GPU detection itself still runs regardless, so a genuinely
    GPU-backed embedding model is still correctly reported even on a
    machine that forces its LLM calls onto CPU."""
    cpu_count = os.cpu_count()
    ram_total_mb = _detect_ram_mb()
    force_cpu = bool(os.environ.get("LLAMA_FORCE_CPU"))

    unified_memory = False
    # Hardware detection itself never depends on LLAMA_FORCE_CPU — the
    # cards are still physically there, and run_embed.sh's own GPU choice
    # (EMBED_N_GPU_LAYERS) is a completely independent setting from it
    # (see run_embed.sh — it never even reads LLAMA_FORCE_CPU). Only the
    # two LLM roles below are ever kept off a GPU because of this flag.
    gpus = _detect_nvidia_gpus()
    if not gpus:
        gpus = _detect_apple_gpu()
        unified_memory = bool(gpus)

    def _gpu_by_index(index):
        for gpu in gpus:
            if gpu["index"] == index:
                return gpu
        # Fall back to the first detected card for an index that doesn't
        # match any real one (e.g. LLM_GPU_INDEX left at its "0" default
        # on a machine whose only card happens to enumerate differently)
        # — never silently drop the role.
        return gpus[0] if gpus else None

    cpu_roles = []

    def _assign(kind, model, index, allow_gpu=True):
        gpu = _gpu_by_index(index) if (gpus and allow_gpu) else None
        entry = {"kind": kind, "model": model}
        if gpu is not None:
            gpu["roles"].append(entry)
        else:
            cpu_roles.append(entry)

    try:
        primary_index = int(os.environ.get("LLM_GPU_INDEX", "0").strip() or "0")
    except ValueError:
        primary_index = 0
    _assign("llm_auto", llm_model, primary_index, allow_gpu=not force_cpu)

    if interactive_llm_model is not None:
        interactive_index_raw = os.environ.get("LLM_INTERACTIVE_GPU_INDEX", "").strip()
        if interactive_index_raw:
            try:
                interactive_index = int(interactive_index_raw)
            except ValueError:
                interactive_index = primary_index
        else:
            interactive_index = primary_index
        _assign("llm_interactive", interactive_llm_model, interactive_index, allow_gpu=not force_cpu)

    if embed_model is not None:
        # run_embed.sh never pins a specific card via CUDA_VISIBLE_DEVICES
        # when GPU mode is on, so it always lands on whichever card CUDA
        # treats as device 0 — this project's own real deployment keeps it
        # there deliberately (see env.sh's "GPU cohabitation" section).
        if embed_on_gpu and gpus:
            _assign("embedding", embed_model, gpus[0]["index"])
        else:
            cpu_roles.append({"kind": "embedding", "model": embed_model})

    return {
        "gpus": gpus,
        "cpu_roles": cpu_roles,
        "unified_memory": unified_memory,
        "cpu_count": cpu_count,
        "ram_total_mb": ram_total_mb,
        "llm_model": llm_model,
        "interactive_llm_model": interactive_llm_model,
        "embed_model": embed_model,
    }
