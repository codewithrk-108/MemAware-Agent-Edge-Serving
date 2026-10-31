"""Plot measured physical KV peaks from the twelve split T4 load experiments."""

import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [(0.0, 'Burst', '#7b3294', 'o'),
            (0.2, 'Poisson: mean gap 5s', '#d55e00', 's'),
            (0.1, 'Poisson: mean gap 10s', '#0072b2', '^')]


def measurements(results):
    rows = []
    for meta in sorted(results.glob('t4_load_v1_part_*/*/meta.json')):
        summary = json.loads(meta.read_text())['summary']
        assert summary['kv_measurement'] == 'physical_cells', meta
        assert not summary['kv_sampling_error'], meta
        with (meta.parent / 'kv_memory.csv').open() as handle:
            samples = list(csv.DictReader(handle))
        peak = max(int(s['used_cells']) for s in samples)
        capacity = summary['kv_capacity_cells']
        assert peak == summary['kv_peak_used_cells'], meta
        assert capacity == 32768 and 0 <= peak <= capacity, meta
        rows.append(dict(arrival_rate=summary['arrival_rate'],
                         concurrency=summary['concurrency'], peak_used_cells=peak,
                         capacity_cells=capacity, peak_usage_percent=100 * peak / capacity,
                         successful_tasks=summary['ok'], failed_tasks=summary['failed'],
                         source_meta=str(meta.relative_to(ROOT))))
    expected = {(rate, n) for rate, *_ in PATTERNS for n in (2, 4, 8, 16)}
    assert len(rows) == 12 and {(r['arrival_rate'], r['concurrency']) for r in rows} == expected, \
        'Need all twelve distinct load points before drawing the comparison'
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=ROOT / 'results')
    parser.add_argument('--out', type=Path, default=ROOT / 'results/analysis/t4_load_v1')
    args = parser.parse_args()
    rows = measurements(args.results)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / 'peak_kv_by_arrival.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r['arrival_rate'], r['concurrency'])))

    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
                         'axes.spines.top': False, 'axes.spines.right': False})
    fig, axes = plt.subplots(1, 2, figsize=(11, 5), gridspec_kw={'width_ratios': [1.2, 1]})
    for rate, label, color, marker in PATTERNS:
        group = sorted((r for r in rows if r['arrival_rate'] == rate),
                       key=lambda r: r['concurrency'])
        for ax in axes:
            ax.plot([r['concurrency'] for r in group],
                    [r['peak_used_cells'] for r in group], label=label,
                    color=color, marker=marker, markersize=7, linewidth=1.8,
                    markerfacecolor='white', markeredgewidth=1.6)
    for ax in axes:
        ax.axhline(32768, color='#666666', linestyle='--', linewidth=1, zorder=0)
        ax.set_xscale('log', base=2)
        ax.set_xticks([2, 4, 8, 16], ['2', '4', '8', '16'])
        ax.set_xlim(1.8, 18)
        ax.set_xlabel('Concurrency N')
        ax.grid(axis='y', color='#dddddd', linewidth=0.7)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x:,.0f}'))
    axes[0].set_ylim(0, 34500)
    axes[0].set_ylabel('Peak occupied physical KV cells')
    axes[0].set_title('Full KV pool')
    axes[0].legend(loc='lower right', frameon=False)
    axes[1].set_ylim(31000, 32900)
    axes[1].set_xlim(3.5, 18)
    axes[1].set_xticks([4, 8, 16], ['4', '8', '16'])
    axes[1].set_title('Zoom near capacity')
    axes[1].text(4, 32768 + 65, '32,768-cell capacity', color='#555555', fontsize=9)
    fig.suptitle('Peak KV occupancy vs concurrency, by arrival pattern', fontsize=14, y=0.98)
    fig.text(0.5, 0.90, 'T4 · Qwen3-0.6B Q8_0 · f16 KV · same 50 tasks · saved tool delays',
             ha='center', color='#555555', fontsize=10)
    fig.text(0.06, 0.035,
             'Sampled peaks (~1s intervals), including retained idle cache and periods with failed requests.\n'
             'One run per point; occupied cells are distinct from allocated GPU memory.',
             fontsize=9, color='#555555')
    fig.subplots_adjust(top=0.79, bottom=0.23, left=0.09, right=0.98, wspace=0.27)
    for ext in ('png', 'svg', 'pdf'):
        target = args.out / f'peak_kv_by_arrival.{ext}'
        fig.savefig(target, dpi=200)
        print(target)
    plt.close(fig)
    for rate, label, *_ in PATTERNS:
        group = sorted((r for r in rows if r['arrival_rate'] == rate), key=lambda r: r['concurrency'])
        print(label, [(r['concurrency'], round(r['peak_usage_percent'], 3)) for r in group])


if __name__ == '__main__':
    main()
