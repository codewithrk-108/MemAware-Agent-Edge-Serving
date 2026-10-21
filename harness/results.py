"""Write benchmark results with enough metadata to reproduce them."""

import datetime
import json
import platform
import subprocess
from pathlib import Path

import pandas as pd
import psutil

from .server import LLAMA_SERVER, MODEL, ROOT

RESULTS = ROOT / "results"


def _cmd(*args) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def power_source() -> str:
    out = _cmd("pmset", "-g", "batt")
    return "AC" if "AC Power" in out else ("battery" if "Battery Power" in out else "unknown")


def check_power(allow_battery: bool = False):
    """Refuse to benchmark on battery: macOS throttles the GPU and timings become 2-3x slower and noisy."""
    if power_source() == "battery" and not allow_battery:
        raise SystemExit("on battery power: plug in the charger (or pass --allow-battery for a smoke test)")


def environment() -> dict:
    llama_dir = LLAMA_SERVER.parents[2]
    return {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "machine": _cmd("sysctl", "-n", "machdep.cpu.brand_string") or platform.processor(),
        "power": power_source(),
        "memory_gib": round(psutil.virtual_memory().total / 2**30, 1),
        "os": f"{platform.system()} {platform.mac_ver()[0] or platform.release()}",
        "llama_cpp_commit": _cmd("git", "-C", str(llama_dir), "rev-parse", "--short", "HEAD"),
        "model": MODEL.name,
        "model_bytes": MODEL.stat().st_size if MODEL.exists() else None,
    }


def save(group: str, name: str, rows: list, meta: dict) -> Path:
    out = RESULTS / group / name
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).drop(columns=["tokens"], errors="ignore").to_csv(out / "raw.csv", index=False)
    (out / "meta.json").write_text(json.dumps({"env": environment(), **meta}, indent=2, default=str))
    print(f"wrote {out}/raw.csv ({len(rows)} rows)")
    return out
