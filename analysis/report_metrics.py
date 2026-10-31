"""Recompute report tables from archived run metadata, timelines, and request CSVs."""

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/data'


def integrate(frame, column, duration):
    """Left-held samples, clipped to workload start/end; no startup/post-run idle."""
    times = frame.t.to_numpy()
    values = frame[column].to_numpy()
    area = 0.0
    for i, (time, value) in enumerate(zip(times, values)):
        left = max(0.0, time)
        right = min(duration, times[i + 1]) if i + 1 < len(times) else duration
        dt = max(0.0, right - left)
        area += dt * value
    # Timeline counters are zero before their first event. KV samples start
    # before workload time zero, so they cover the full clipped interval.
    return area / duration if duration > 0 else float('nan')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    paths = sorted((ROOT / 'results/phase1').rglob('meta.json'))
    paths += sorted((ROOT / 'results').glob('t4_load_v1_part_*/*/meta.json'))
    for meta in paths:
        data = json.loads(meta.read_text())
        s = data['summary']
        run = data['run']
        d = meta.parent
        row = dict(s, source=str(d.relative_to(ROOT)),
                   arrival_rate=run['arrival_rate'], delay_scale=run['delay_scale'],
                   deadline_s=run['deadline_s'], request_timeout_s=run['request_timeout_s'],
                   model=data['env']['model'], machine=data['env']['machine'])
        tasks = pd.read_csv(d / 'tasks.csv')
        req = pd.read_csv(d / 'requests.csv')
        good = req[req.error.isna()]
        row['successful_request_e2e_p50_s'] = good.e2e_s.median()
        row['executed_tool_mean_s'] = tasks.tool_s.mean()
        row['executed_tool_p50_s'] = tasks.tool_s.median()
        row['realized_arrival_span_s'] = tasks.arrival.max() - tasks.arrival.min()
        row['successful_request_generated_tokens'] = good.predicted_n.sum()
        row['successful_short_outputs'] = int((good.predicted_n < good.n_predict_req).sum())
        timeline = pd.read_csv(d / 'timeline.csv')
        for field in ('running', 'runnable', 'tool_waiting'):
            if field not in timeline:
                continue
            row[f'mean_{field}'] = integrate(timeline, field, s['span_s'])
        row['max_running'] = timeline.running.max()
        if 'runnable' in timeline:
            row['max_ready_queue'] = timeline.runnable.max()
            indicators = timeline.assign(
                full=(timeline.running == s['concurrency']).astype(int),
                spare_empty=((timeline.running < s['concurrency']) & (timeline.runnable == 0)).astype(int),
                spare_queued=((timeline.running < s['concurrency']) & (timeline.runnable > 0)).astype(int))
            for field in ('full', 'spare_empty', 'spare_queued'):
                row[f'{field}_time_pct'] = 100 * integrate(indicators, field, s['span_s'])
        kv = d / 'kv_memory.csv'
        if kv.exists():
            cells = pd.read_csv(kv)
            row['mean_kv_used_cells'] = integrate(cells, 'used_cells', s['span_s'])
            row['mean_kv_usage_pct'] = 100 * row['mean_kv_used_cells'] / s['kv_capacity_cells']
            row['peak_kv_usage_pct'] = 100 * s['kv_peak_usage_ratio']
            row['kv_max_sample_gap_s'] = cells.t.diff().max()
            assert cells.used_cells.max() == s['kv_peak_used_cells'], d
        admission = d / 'admission_checks.csv'
        if admission.exists():
            checks = pd.read_csv(admission)
            fits = checks.head_fits.astype(str).str.lower() == 'true'
            failed = checks.request_outcome == 'error'
            row['estimated_fit_then_error'] = int((fits & failed).sum())
            row['estimated_exceed_then_ok'] = int((~fits & (checks.request_outcome == 'ok')).sum())
        if row['source'].startswith('results/t4_load_v1'):
            sums = good.groupby('task_id').e2e_s.sum().rename('request_s')
            ok = tasks[tasks.status == 'ok'].join(sums, on='task_id')
            row['successful_task_request_sum_p50_s'] = ok.request_s.median()
            row['successful_task_ready_queue_p50_s'] = ok.ready_queue_s.median()
            row['successful_task_tool_p50_s'] = ok.tool_s.median()
        rows.append(row)
    inventory = pd.DataFrame(rows)
    inventory.to_csv(OUT / 'run_inventory.csv', index=False)
    latest = inventory[inventory.source.str.startswith('results/t4_load_v1')].copy()
    latest['pattern'] = latest.arrival_rate.map({0.0: 'Burst', 0.1: 'Poisson 10s', 0.2: 'Poisson 5s'})
    latest.sort_values(['arrival_rate', 'concurrency']).to_csv(OUT / 'latest_t4_metrics.csv', index=False)
    print(f'Wrote {len(inventory)} agent runs and {len(latest)} instrumented T4 runs to {OUT}')


if __name__ == '__main__':
    main()
