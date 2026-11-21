import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from analysis.reproduce_phase2 import validate_run
from phase1.runner import RunConfig
from phase2.runner import Phase2Config, verify_evaluation_config
from phase2.freeze import freeze_policy, verify_frozen_policy
from phase2.workloads import split_agenttrace
from workloads.trace import Step, Task, load, save


class Phase2ArtifactTests(unittest.TestCase):
    def test_agenttrace_split_is_disjoint_and_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jsonl"
            tasks = [Task(f"task-{index}", "test", "agenttrace",
                          [Step([index + 10], 1, None)]) for index in range(10)]
            save(tasks, source)
            first = split_agenttrace(source, root / "dev.jsonl", root / "eval.jsonl",
                                     root / "manifest.json", 4, seed=3)
            second = split_agenttrace(source, root / "dev2.jsonl", root / "eval2.jsonl",
                                      root / "manifest2.json", 4, seed=3)
            self.assertEqual(first["development"]["task_ids"],
                             second["development"]["task_ids"])
            self.assertEqual(len(load(root / "dev.jsonl")), 4)
            self.assertFalse(set(first["development"]["task_ids"])
                             & set(first["evaluation"]["task_ids"]))

    def test_frozen_policy_detects_modified_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            estimator = root / "estimator.json"
            manifest = root / "manifest.json"
            artifact = root / "frozen.json"
            estimator.write_text('{"model": 1}\n')
            manifest.write_text('{"split": 1}\n')
            freeze_policy({"aging_threshold_s": 30}, estimator, manifest, artifact)
            self.assertEqual(verify_frozen_policy(artifact)["policy"]["aging_threshold_s"], 30)
            estimator.write_text('{"model": 2}\n')
            with self.assertRaisesRegex(ValueError, "missing or modified"):
                verify_frozen_policy(artifact)

    def test_result_validation_checks_output_budgets(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "meta.json").write_text(json.dumps({
                "summary": {"name": "test", "throughput_tasks_min": 1,
                            "completion_p95_s": 2, "max_ready_queue_s": 3}
            }))
            pd.DataFrame([{"status": "ok"}]).to_csv(run / "tasks.csv", index=False)
            pd.DataFrame([{"error": None, "n_predict_req": 4, "predicted_n": 4}]).to_csv(
                run / "requests.csv", index=False)
            pd.DataFrame([{"reason": "fifo_head", "selected_task_index": 0}]).to_csv(
                run / "scheduler_decisions.csv", index=False)
            self.assertEqual(validate_run(run)["ok"], 1)
            pd.DataFrame([{"error": None, "n_predict_req": 4, "predicted_n": 3}]).to_csv(
                run / "requests.csv", index=False)
            with self.assertRaisesRegex(ValueError, "output budgets"):
                validate_run(run)

    def test_evaluation_config_must_match_frozen_policy(self):
        config = Phase2Config(RunConfig(name="test"))
        policy = {
            field: getattr(config, field)
            for field in (
                "aging_threshold_s", "backfill_guard_s", "kv_safety_margin_cells",
                "reserve_per_waiting_task", "max_tool_reserve_cells", "reserve_horizon_s",
                "estimated_tool_delay_s", "timing_mode",
            )
        }
        artifact = {
            "policy": policy,
            "runtime_estimator": {"path": "model.json"},
        }
        verify_evaluation_config(config, Path("model.json"), artifact)
        artifact["policy"]["aging_threshold_s"] = 99
        with self.assertRaisesRegex(ValueError, "differs from frozen"):
            verify_evaluation_config(config, Path("model.json"), artifact)


if __name__ == "__main__":
    unittest.main()

