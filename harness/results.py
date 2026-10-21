"""Write benchmark results with enough metadata to reproduce them."""

import datetime
import json
import os
import platform as pyplatform
import subprocess
from pathlib import Path

import pandas as pd
import psutil

from . import platform
from .memory import gpu_scope
from .server import LLAMA_SERVER, MODEL, ROOT



def results_dir() -> Path:
    """results/<platform>: runs from different machines never mix or count as done for each other."""
    return ROOT / "results" / platform.current()


def _cmd(*args) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def power_source() -> str:
    if platform.current() == "macos":
        out = _cmd("pmset", "-g", "batt")
        return "AC" if "AC Power" in out else ("battery" if "Battery Power" in out else "unknown")
    # linux: servers have no battery; on laptops /sys/class/power_supply has an AC or battery entry
    supplies = list(Path("/sys/class/power_supply").glob("*"))
    if not any((d / "type").exists() and (d / "type").read_text().strip() == "Battery" for d in supplies):
        return "AC"
    online = [d / "online" for d in supplies if (d / "type").exists() and (d / "type").read_text().strip() == "Mains"]
    return "AC" if any(f.exists() and f.read_text().strip() == "1" for f in online) else "battery"


def check_power(allow_battery: bool = False):
    """Refuse to benchmark on battery: laptops throttle the GPU and timings become 2-3x slower and noisy."""
    if power_source() == "battery" and not allow_battery:
        raise SystemExit("on battery power: plug in the charger (or pass --allow-battery for a smoke test)")


def _machine() -> dict:
    if platform.current() == "macos":
        return {"machine": _cmd("sysctl", "-n", "machdep.cpu.brand_string") or pyplatform.processor()}
    cpu = next((l.split(":", 1)[1].strip() for l in Path("/proc/cpuinfo").read_text().splitlines()
                if l.startswith("model name")), pyplatform.processor())
    gpus = _cmd("nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader")
    return {"machine": cpu, "gpus": gpus.splitlines(), "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "gpu_memory_scope": gpu_scope()}


def _os() -> str:
    if pyplatform.system() == "Darwin":
        return f"macOS {pyplatform.mac_ver()[0]}"
    try:
        return pyplatform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
    except OSError:
        return f"Linux {pyplatform.release()}"


def environment() -> dict:
    llama_dir = LLAMA_SERVER.parents[2]
    return {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "platform": platform.current(),
        **_machine(),
        "power": power_source(),
        "memory_gib": round(psutil.virtual_memory().total / 2**30, 1),
        "os": _os(),
        "llama_cpp_commit": _cmd("git", "-C", str(llama_dir), "rev-parse", "--short", "HEAD"),
        "model": MODEL.name,
        "model_bytes": MODEL.stat().st_size if MODEL.exists() else None,
    }


def save(group: str, name: str, rows: list, meta: dict) -> Path:
    out = results_dir() / group / name
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).drop(columns=["tokens"], errors="ignore").to_csv(out / "raw.csv", index=False)
    (out / "meta.json").write_text(json.dumps({"env": environment(), **meta}, indent=2, default=str))
    print(f"wrote {out}/raw.csv ({len(rows)} rows)")
    return out
