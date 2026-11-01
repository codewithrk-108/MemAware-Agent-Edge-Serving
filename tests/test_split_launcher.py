import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class SplitLauncherTest(unittest.TestCase):
    def launch(self, code=0, approve=True, reject_commit=False, push=False, push_fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            (root / 'phase1').mkdir(parents=True)
            (root / '.venv/bin').mkdir(parents=True)
            (root / 'bin').mkdir()
            (root / 'data/traces').mkdir(parents=True)
            (root / 'runtime').mkdir()
            (root / 'runtime/llama-b11780-kv-occupancy.patch').write_text('patch')
            (root / 'data/traces/agenttrace_50_seed2.jsonl').write_text('{}\n')
            shutil.copy('phase1/run_split_sweep_and_stop.sh', root / 'phase1/run.sh')
            env = {**os.environ, 'PATH': str(root / 'bin') + ':' + os.environ['PATH'],
                   'MODEL': str(root / 'model'), 'LLAMA_SERVER': str(root / 'bin/server'),
                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                   'TEST_STOP_LOG': str(root / 'stop.log')}
            (root / 'model').write_text('model')
            for name, content in {
                '.venv/bin/python': f'#!/bin/sh\nif [ "$1" = --version ]; then echo Python; exit 0; fi\necho result > results/t4_load_v1_part_a/raw.csv\nexit {code}\n',
                'bin/server': '#!/bin/sh\necho runtime-version\n',
                'bin/nvidia-smi': '#!/bin/sh\necho T4\n',
                'bin/sudo': '#!/bin/sh\nif [ "$TEST_EXPECT_PUSH" = 1 ]; then test "$(git rev-parse HEAD)" = "$(git ls-remote origin refs/heads/results/test | cut -f1)" || exit 1; fi\ngit log -1 --format=%s > "$TEST_STOP_LOG"\n',
            }.items():
                path = root / name
                path.write_text(content)
                path.chmod(0o755)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], env=env,
                                               text=True, stderr=subprocess.DEVNULL).strip()
            git('init')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.com')
            git('add', 'phase1/run.sh')
            git('commit', '-m', 'setup')
            git('checkout', '-b', 'results/test')
            credential = Path(tmp) / 'credential.json'
            if push:
                remote = Path(tmp) / 'remote.git'
                if not push_fail:
                    git('init', '--bare', str(remote))
                git('remote', 'add', 'origin', str(remote))
                credential.write_text('temporary test credential')
                env.update(PUSH_RESULTS='1', PUSH_RETRY_DELAY='0', TEST_EXPECT_PUSH='1',
                           PHASE1_GITHUB_CREDENTIAL=str(credential))
            (root / 'unrelated').write_text('keep untracked')
            if approve:
                (root / '.shutdown-approved').touch()
            if reject_commit:
                hook = root / '.git/hooks/pre-commit'
                hook.write_text('#!/bin/sh\nexit 1\n')
                hook.chmod(0o755)
            result = subprocess.run(['bash', str(root / 'phase1/run.sh'), 'a'], cwd=root,
                                    env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(git('ls-files', 'unrelated'), '')
            if reject_commit or push_fail:
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / 'stop.log').exists())
                if push_fail:
                    self.assertTrue(credential.exists())
            else:
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(git('log', '-1', '--format=%s'), 'Record T4 load sweep part a results')
                self.assertIn('raw.csv', git('show', '--stat', '--oneline', 'HEAD'))
                self.assertIn(f'sweep_exit_code={code}', (root / 'results/t4_load_v1_part_a/status.txt').read_text())
                self.assertEqual((root / 'stop.log').exists(), approve)
                if approve:
                    self.assertEqual((root / 'stop.log').read_text().strip(),
                                     'Record T4 load sweep part a results')
                if push:
                    self.assertFalse(credential.exists())
                    self.assertEqual(git('ls-remote', 'origin', 'refs/heads/results/test').split()[0],
                                     git('rev-parse', 'HEAD'))

    def test_commits_results_before_stop(self):
        self.launch()

    def test_records_failed_sweep_before_stop(self):
        self.launch(code=7)

    def test_commit_failure_leaves_instance_running(self):
        self.launch(reject_commit=True)

    def test_unconfirmed_shutdown_leaves_instance_running(self):
        self.launch(approve=False)

    def test_pushes_results_and_removes_temporary_credentials_before_stop(self):
        self.launch(push=True)

    def test_push_failure_preserves_results_and_leaves_instance_running(self):
        self.launch(push=True, push_fail=True)
