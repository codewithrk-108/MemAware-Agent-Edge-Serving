"""Replicate the seven full 500-task T4 runs (R1-R7) with identical configurations and seeds.

Every replicate reuses the RunConfig objects of the `full500_t4_part_a` and `full500_t4_part_b`
groups in phase1/sweep.py unchanged (seed 2, same trace, N, arrivals, KV cells and deadlines).
Before each run the planned task order and arrival offsets are checked against the original
`planned_runs.json`. Only the output folder differs:

    results/t4_full500_repeats_v1/rep<NN>_part_<a|b>/<original run name>/

Replicate 1 is the original data in results/t4_full500_v1_part_{a,b}/. Two hosts alternate
halves, so every configuration is measured on both instances:

    host a: rep02 part a, rep03 part b, rep04 part a, ...
    host b: rep02 part b, rep03 part a, rep04 part b, ...

A run starts only if its expected duration (original span x 1.25 + 10 min) ends before
--deadline. After every run the kept files (the per-run file rule in results/README.md) are
committed and, with --push, the host's results branch is pushed. server.log and memory.csv stay
on the instance disk; $PHASE1_AFTER_RUN, if set, runs after every run (the AWS bootstrap uses it to
copy the full run folders, logs included, to S3).
"""

import argparse
import copy
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import time
import traceback

import numpy as np
import pandas as pd

from harness.results import RESULTS
from phase1.runner import ROOT, execute, load
from phase1.sweep import grid

OUT = "t4_full500_repeats_v1"
KEEP = ("meta.json", "tasks.csv", "requests.csv", "timeline.csv", "kv_memory.csv",
        "gpu_memory.csv", "admission_checks.csv")
# span_s of the original runs (hours), by configuration number c1..c7
ORIGINAL_SPAN_H = {1: 3.08, 2: 1.90, 3: 0.68, 4: 1.85, 5: 0.30, 6: 1.84, 7: 1.26}


def now():
    return dt.datetime.now(dt.timezone.utc)


def log(msg):
    print(f"{now():%Y-%m-%dT%H:%M:%SZ} {msg}", flush=True)


def config_number(name):
    return int(name.split("_c", 1)[1].split("_", 1)[0])


def expected_s(name):
    return ORIGINAL_SPAN_H[config_number(name)] * 3600 * 1.25 + 600


def part_for(host, rep):
    return host if rep % 2 == 0 else {"a": "b", "b": "a"}[host]


def check_plan(cfg, part):
    """Same RNG steps as phase1.runner.run; compare with the original plan."""
    planned = {p["configuration"]["name"]: p for p in
               json.loads((RESULTS / f"t4_full500_v1_part_{part}" / "planned_runs.json").read_text())}
    p = planned[cfg.name]
    rng = np.random.default_rng(cfg.seed)
    pool = load(ROOT / cfg.trace)
    idx = rng.choice(len(pool), size=cfg.n_tasks, replace=cfg.n_tasks > len(pool))
    ids = [pool[i].task_id for i in idx]
    gaps = rng.exponential(1.0 / cfg.arrival_rate, size=len(ids)) if cfg.arrival_rate > 0 else np.zeros(len(ids))
    if len(gaps):
        gaps[0] = 0.0
    return ids == p["task_order"] and np.allclose(np.cumsum(gaps), p["arrival_offsets_s"])


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=ROOT, check=check, capture_output=True, text=True)


def push(branch, attempts=3):
    for attempt in range(1, attempts + 1):
        refresh = os.environ.get("PHASE1_CREDENTIAL_REFRESH")
        if refresh:
            subprocess.run(refresh, shell=True, capture_output=True)
        r = git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", check=False)
        if r.returncode == 0:
            log(f"pushed {branch}")
            return True
        log(f"push failed ({attempt}/{attempts}): {r.stderr.strip()[-300:]}")
        time.sleep(30)
    return False


def publish(paths, message, branch, do_push):
    paths = [str(p.relative_to(ROOT)) for p in paths if p.exists()]
    if paths:
        git("add", "-f", "--", *paths)
    if git("diff", "--cached", "--quiet", check=False).returncode != 0:
        git("commit", "-q", "-m", message)
    return push(branch) if do_push else True


def main():
    code = run()
    publish_log(code)
    return code


def publish_log(code):
    """Final commit of the run log and driver output, also when stopping at the deadline."""
    host, branch, do_push = STATE.get("host"), STATE.get("branch"), STATE.get("push")
    if not host:
        return
    root = RESULTS / OUT
    files = [root / f"host_{host}_runs.csv"]
    log_file = os.environ.get("REPEATS_LOG")
    if log_file and os.path.exists(log_file):
        dest = root / f"host_{host}_driver.txt"
        dest.write_text(open(log_file).read())
        files.append(dest)
    log(f"driver exiting with code {code}")
    publish(files, f"Finish replicate runs on host {host} (exit {code})", branch, do_push)


STATE = {}


def run():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", choices=("a", "b"), required=True)
    p.add_argument("--deadline", required=True, help="UTC ISO time; no run starts unless expected to end by then")
    p.add_argument("--first-rep", type=int, default=2)
    p.add_argument("--last-rep", type=int, default=30)
    p.add_argument("--push", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    deadline = dt.datetime.fromisoformat(a.deadline.replace("Z", "+00:00"))
    branch = git("branch", "--show-current").stdout.strip()
    if a.push and not branch.startswith("results/"):
        sys.exit("refusing to push outside a results/* branch")
    if not a.dry_run:
        STATE.update(host=a.host, branch=branch, push=a.push)
    groups = grid()
    root = RESULTS / OUT
    root.mkdir(parents=True, exist_ok=True)
    status = root / f"host_{a.host}_runs.csv"
    fields = ["rep", "part", "run", "start_utc", "end_utc", "status", "ok", "failed",
              "throughput_tasks_min", "completion_p95_s", "detail"]
    if not status.exists():
        with status.open("w", newline="") as f:
            csv.writer(f).writerow(fields)
    shortest = min(expected_s(c.name) for g in ("a", "b") for c in groups[f"full500_t4_part_{g}"])
    consecutive_errors = 0

    for rep in range(a.first_rep, a.last_rep + 1):
        part = part_for(a.host, rep)
        group = f"{OUT}/rep{rep:02d}_part_{part}"
        for cfg in groups[f"full500_t4_part_{part}"]:
            out = RESULTS / group / cfg.name
            if (out / "meta.json").exists():
                continue
            remaining = (deadline - now()).total_seconds()
            if remaining < shortest:
                log(f"deadline {a.deadline} reached; stopping")
                return 0
            if remaining < expected_s(cfg.name):
                log(f"skip {group}/{cfg.name}: needs ~{expected_s(cfg.name) / 3600:.1f} h, "
                    f"{remaining / 3600:.1f} h left")
                continue
            if not check_plan(cfg, part):
                log(f"plan mismatch for {cfg.name}; stopping")
                return 3
            log(f"start {group}/{cfg.name}")
            if a.dry_run:
                continue
            start = now()
            row = {"rep": rep, "part": part, "run": cfg.name, "start_utc": f"{start:%FT%TZ}"}
            try:
                s = execute(copy.deepcopy(cfg), group=group)
                row.update(status="ok", ok=s["ok"], failed=s["failed"],
                           throughput_tasks_min=round(s["throughput_tasks_min"], 4),
                           completion_p95_s=round(s["completion_p95_s"], 1))
                consecutive_errors = 0
            except Exception as e:
                traceback.print_exc()
                out.mkdir(parents=True, exist_ok=True)
                (out / "error.txt").write_text(traceback.format_exc())
                row.update(status="error", detail=f"{type(e).__name__}: {e}"[:200])
                consecutive_errors += 1
            row["end_utc"] = f"{now():%FT%TZ}"
            with status.open("a", newline="") as f:
                csv.DictWriter(f, fieldnames=fields).writerow(row)
            metas = sorted((RESULTS / group).glob("*/meta.json"))
            if metas:
                pd.DataFrame([json.loads(m.read_text())["summary"] for m in metas]).to_csv(
                    RESULTS / group / "summary.csv", index=False)
            files = [out / k for k in KEEP] + [out / "error.txt", RESULTS / group / "summary.csv", status]
            log_file = os.environ.get("REPEATS_LOG")
            if log_file and os.path.exists(log_file):
                dest = root / f"host_{a.host}_driver.txt"
                dest.write_text(open(log_file).read())
                files.append(dest)
            label = f"R{config_number(cfg.name)}"
            publish(files, f"Record replicate {rep} of {label} ({cfg.name}) on host {a.host}: {row['status']}",
                    branch, a.push)
            after = os.environ.get("PHASE1_AFTER_RUN")  # e.g. copy raw logs to S3
            if after:
                subprocess.run(after, shell=True, cwd=ROOT)
            if consecutive_errors >= 2:
                log("two consecutive run errors; stopping")
                return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
