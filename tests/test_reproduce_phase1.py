"""Every table and inline number in the Phase 1 writeup must be reproducible from committed data.

The writeup (report/phase1_writeup.tex) prints three values that differ from the data only in
rounding or units. They are listed in WRITEUP_ROUNDING so this test fails if any *other* value
drifts. If the writeup is corrected, update analysis.reproduce_phase1.WRITEUP_* and this set.
"""

import unittest

from pathlib import Path

from analysis import reproduce_phase1 as R
from analysis import run_inventory

WRITEUP_ROUNDING = {
    # data 3.3748 tasks/min rounds to 3.37
    "S3 Burst, N=4: completed (tasks/min)",
    # 4,387 MiB peak is 4.3 GiB (4.4 if read as thousands of MiB)
    "T4 device memory with the 32K pool (flat)",
    # R6 ready-queue p95 is 6,160.46 s
    "N4 ready-queue p95 range (s)",
}


class ReproduceWriteupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checks = R.writeup_numbers()

    def test_every_number_reproduces(self):
        mismatched = {claim for _, claim, writeup, data in self.checks if writeup != data}
        self.assertEqual(mismatched, WRITEUP_ROUNDING)

    def test_full_run_table_covers_r1_to_r7(self):
        table = R.table3()
        self.assertEqual(list(table.run), [f"R{i}" for i in range(1, 8)])
        self.assertEqual(int(table.done.sum() + table.failed.sum()), 3500)

    def test_s3_grid_has_twelve_runs(self):
        table = R.table1()
        self.assertEqual(len(table), 12)
        self.assertEqual(int((table.n_tasks - table.completed).sum()), 138)


    def test_committed_run_inventory_is_current(self):
        built = run_inventory.build().to_csv(index=False)
        self.assertEqual(run_inventory.OUT.read_text(), built,
                         "regenerate with: uv run python -m analysis.run_inventory")
        for path in run_inventory.build().path:
            self.assertTrue((R.M.ROOT / path).is_dir(), path)


if __name__ == "__main__":
    unittest.main()
