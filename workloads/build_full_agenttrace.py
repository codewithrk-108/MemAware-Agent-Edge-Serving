"""Build the fixed 500-problem full-run workload without editing trajectories."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/traces/agenttrace_all_tool_delay_150x_max10s.jsonl'
OUTPUT = ROOT / 'data/traces/agenttrace_full_500_seed2.jsonl'
# Preserve approximately the source step/class mix; choose long strata first
# so choosing a shorter variant cannot exhaust a rare long-problem stratum.
QUOTAS = {'6-11': {'mbpp': 8, 'nl2bash': 9},
          '4-5': {'mbpp': 11, 'nl2bash': 17},
          '3': {'mbpp': 33, 'nl2bash': 27},
          '2': {'mbpp': 133, 'nl2bash': 97},
          '1': {'mbpp': 165, 'nl2bash': 0}}


def _rel(path):
    """Repo-relative path for manifests (absolute only for files outside the repo)."""
    path = Path(path).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def problem_key(row):
    """Source-model variants of the same MBPP/NL2Bash problem share this key."""
    key, separator, _ = row['task_id'].rpartition('-qwen3-')
    if not separator:
        raise ValueError(f"Unrecognized AgentTrace task ID: {row['task_id']}")
    return key


def step_bucket(row):
    n = len(row['steps'])
    return str(n) if n < 4 else '4-5' if n < 6 else '6-11'


def select_tasks(rows, seed=2):
    if len({r['task_id'] for r in rows}) != len(rows):
        raise ValueError('Source task IDs must be unique')
    rng = np.random.default_rng(seed)
    selected, used = [], set()
    for bucket, classes in QUOTAS.items():
        for cls, quota in classes.items():
            candidates = [r for r in rows if r['cls'] == cls and step_bucket(r) == bucket]
            count = 0
            for index in rng.permutation(len(candidates)):
                if count == quota:
                    break
                row = candidates[index]
                key = problem_key(row)
                if key in used:
                    continue
                selected.append(row)
                used.add(key)
                count += 1
            if count != quota:
                raise ValueError(f'Not enough unused problems for {cls}/{bucket}: {count}/{quota}')
    return [selected[i] for i in rng.permutation(len(selected))]


def distribution(values, boundaries):
    edges = [0, *boundaries, None]
    result = []
    for lo, hi in zip(edges, edges[1:]):
        count = sum(v >= lo and (hi is None or v < hi) for v in values)
        result.append({'bucket': f'{lo}-{hi - 1}' if hi is not None else f'{lo}+',
                       'count': count, 'percent': count / len(values) * 100})
    return result


def summary(values):
    return {'min': min(values), 'median': float(np.median(values)),
            'p95': float(np.percentile(values, 95)), 'max': max(values),
            'mean': float(np.mean(values))}


def summary_text(values, digits=2):
    return ', '.join(f'{key}: {value:.{digits}f}' for key, value in values.items())


def build(source, output, seed=2, overwrite=False):
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ValueError('Output must not overwrite the source')
    if output.exists() and not overwrite:
        raise FileExistsError(f'{output} exists; use --overwrite only to regenerate deliberately')
    source_bytes = source.read_bytes()
    rows = [json.loads(line) for line in source_bytes.decode().splitlines() if line.strip()]
    selected = select_tasks(rows, seed)
    inputs, outputs, initial, delays, context_ends = [], [], [], [], []
    for task in selected:
        initial.append(len(task['steps'][0]['input']))
        history = 0
        for i, step in enumerate(task['steps']):
            if step['n_gen'] <= 0 or not step['input']:
                raise ValueError('Empty input or nonpositive output budget')
            history += len(step['input'])
            inputs.append(history)
            outputs.append(step['n_gen'])
            history += step['n_gen']
            delay = step['tool_delay_s']
            if i == len(task['steps']) - 1:
                if delay is not None:
                    raise ValueError('Final step must have a null tool delay')
            elif delay is None or not 0 <= delay <= 10:
                raise ValueError('Nonterminal steps must have saved tool delays within [0, 10] seconds')
            else:
                delays.append(delay)
        context_ends.append(history)
    if max(context_ends) > 16384:
        raise ValueError('Source task exceeds the converted 16K per-task budget')
    encoded = ''.join(json.dumps(row, separators=(',', ':')) + '\n' for row in selected).encode()
    source_manifest = source.with_name(source.name + '.manifest.json')
    transform = json.loads(source_manifest.read_text()).get('tool_delay_transform') if source_manifest.exists() else None
    manifest = {
        'source': _rel(source), 'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
        'dataset': _rel(output), 'dataset_sha256': hashlib.sha256(encoded).hexdigest(),
        'selection': {'method': 'Seeded random stratified selection, rare long strata first, then shuffled',
                      'seed': seed, 'numpy_version': np.__version__, 'quotas': QUOTAS,
                      'unique_original_problems': True,
                      'task_ids': [r['task_id'] for r in selected],
                      'problem_keys': [problem_key(r) for r in selected]},
        'preservation': 'Complete source rows retained: inputs, system prompt, output budgets, tool results, and tool delays unchanged',
        'tool_delay_transform_in_source': transform,
        'counts': {'source_tasks': len(rows), 'tasks': len(selected),
                   'original_problems': len({problem_key(r) for r in selected}),
                   'model_requests': len(inputs), 'tool_calls': len(delays),
                   'classes': dict(Counter(r['cls'] for r in selected)),
                   'steps': dict(sorted(Counter(len(r['steps']) for r in selected).items())),
                   'step_groups': dict(Counter(step_bucket(r) for r in selected)),
                   'requested_output_tokens': sum(outputs)},
        'input_tokens': {'meaning': 'Full logical prompt before each request, including all preceding generated budgets; not recomputed/cached token counts',
                         'summary': summary(inputs), 'buckets': distribution(inputs, [1024, 2048, 4096, 8192, 16384])},
        'first_input_tokens': {'summary': summary(initial), 'buckets': distribution(initial, [1024, 2048, 4096, 8192, 16384])},
        'output_tokens': {'summary': summary(outputs), 'buckets': distribution(outputs, [256, 512, 1024, 2048, 4096, 8192, 16384])},
        'tool_delay_seconds': summary(delays),
        'maximum_final_context_tokens': max(context_ends),
        'arrivals': 'Not encoded in the trace; fix the runner seed/rate separately for matched comparisons',
        'replay': 'Token-ID replay with fixed output budgets and recorded tool results; tools are not executed and semantic answer quality is not measured',
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded)
    output.with_name(output.name + '.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    lines = ['# Full-run AgentTrace dataset: 500 original problems', '',
             f'File: `{output.name}`. Seed: {seed}. SHA-256: `{manifest["dataset_sha256"]}`.', '',
             'Complete trajectories are unchanged. Each original problem occurs once, even when the source has multiple model variants.', '',
             '## Task mix', '', '| Steps | Tasks |', '|---|---:|']
    for bucket in ('1', '2', '3', '4-5', '6-11'):
        lines.append(f'| {bucket} | {manifest["counts"]["step_groups"][bucket]} |')
    lines += ['', f'Classes: {manifest["counts"]["classes"]}. Planned model requests: {len(inputs)}. Tool waits: {len(delays)}.', '']
    for key, label in [('input_tokens', 'Full logical input per model request'),
                       ('output_tokens', 'Requested output per model request')]:
        lines += [f'## {label}', '', '| Tokens | Requests | Share |', '|---|---:|---:|']
        for bucket in manifest[key]['buckets']:
            lines.append(f'| {bucket["bucket"]} | {bucket["count"]} | {bucket["percent"]:.2f}% |')
        lines += ['', f'Summary — {summary_text(manifest[key]["summary"])}.', '']
    lines += ['## Interpretation and execution', '',
              'First-step inputs remain mostly near 1.1K tokens; long inputs come from later context growth. No sub-1K prompt stratum exists in this source. Input/output percentages describe model requests, not tasks.', '',
              f'Tool delays are copied from the controlled source (150×, capped at 10s): {summary_text(manifest["tool_delay_seconds"], 3)} seconds. Keep runner delay scale at 1.', '',
              'Use this file with `phase1.runner` and explicitly set `--n-tasks 500 --seed 2`, plus the chosen arrival rate/concurrency/KV settings. Reuse the same file and arrival seed across policies. The default runner task count is smaller than 500.', '',
              'Set full-run timeouts explicitly rather than inheriting the short CLI defaults. The smoke configurations used `RunConfig(deadline_s=14400, request_timeout_s=900)` and `require_kv_metrics=True`; validate those settings against the intended full-run load before launch.', '',
              'Existing instrumentation writes `requests.csv` (TTFT, TPOT, request duration, budgets, computed/reused tokens, errors), `tasks.csv` (completion, accumulated queueing, tool time, steps, outcome), `timeline.csv`, `admission_checks.csv`, process/GPU memory CSVs, and `kv_memory.csv` when the instrumented runtime is used. Require KV metrics for full runs.', '',
              'This builder creates a workload only; it does not launch inference. Semantic task-quality evaluation remains separate.']
    output.with_name(output.name + '.stats.md').write_text('\n'.join(lines) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--out', type=Path, default=OUTPUT)
    parser.add_argument('--seed', type=int, default=2)
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    manifest = build(args.source, args.out, args.seed, args.overwrite)
    print(json.dumps({'dataset': str(args.out), 'counts': manifest['counts'],
                      'sha256': manifest['dataset_sha256']}, indent=2))


if __name__ == '__main__':
    main()
