"""Short model-backed exporter and runner validation; does not replay an experiment dataset."""

import argparse
import csv
import json
import os
from pathlib import Path
import tempfile
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    args = parser.parse_args()
    os.environ['LLAMA_SERVER'] = str(args.server.resolve())
    os.environ['MODEL'] = str(args.model.resolve())
    from harness.kv_memory import KvMemSampler, kv_memory_sample
    from harness.server import Server, ServerConfig
    from phase1 import runner

    with tempfile.TemporaryDirectory(prefix='kv-validation-') as tmp:
        out = Path(tmp)
        config = ServerConfig(ctx=512, parallel=2, cache_ram=0, mmap=False, port=8092,
                              extra=['--no-warmup', '-t', '4', '-tb', '4', '--slot-save-path', tmp])
        with Server(config, out / 'server.log') as srv:
            initial = kv_memory_sample(srv.url)
            assert initial['used_cells'] == 0, initial
            with KvMemSampler(srv.url, interval=0.1) as sampler:
                body = json.dumps({'prompt': list(range(1000, 1064)), 'n_predict': 64,
                                   'ignore_eos': True, 'temperature': 0, 'id_slot': 0,
                                   'cache_prompt': True}).encode()
                request = urllib.request.Request(srv.url + '/completion', data=body,
                                                 headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=120) as response:
                    completion = json.load(response)
                assert completion['timings']['predicted_n'] == 64, completion
                retained = kv_memory_sample(srv.url)
                assert retained['used_cells'] > 0 and retained['requests_processing'] == 0, retained
                request = urllib.request.Request(srv.url + '/slots/0?action=erase', data=b'{}',
                                                 headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=30) as response:
                    json.load(response)
                empty = kv_memory_sample(srv.url)
                assert empty['used_cells'] == 0, empty

        trace = out / 'trace.jsonl'
        trace.write_text(json.dumps({'task_id': 'kv-validation', 'cls': 'validation',
                                     'source': 'validation', 'steps': [
                                         {'input': list(range(1000, 1064)), 'n_gen': 64, 'tool_delay_s': 0.02},
                                         {'input': [1020] * 8, 'n_gen': 64, 'tool_delay_s': None}]}) + '\n')
        runner.RESULTS = out
        config.port = 8093
        summary = runner.execute(runner.RunConfig(name='kv', trace=str(trace), n_tasks=1,
                                 arrival_rate=0, concurrency=1, require_kv_metrics=True,
                                 server=config), group='validation')
        rows = list(csv.DictReader((out / 'validation/kv/kv_memory.csv').open()))
        assert summary['ok'] == 1 and summary['kv_measurement'] == 'physical_cells', summary
        assert summary['kv_peak_used_cells'] > 0 and summary['kv_capacity_cells'] == 512, summary
        assert rows and all(0 <= float(row['usage_ratio']) <= 1 for row in rows), rows
        print(json.dumps({'initial': initial, 'retained_idle': retained, 'after_erase': empty,
                          'exporter_sampler': sampler.summary(), 'runner_samples': len(rows),
                          'runner_peak_used_cells': summary['kv_peak_used_cells']}, indent=2))


if __name__ == '__main__':
    main()
