import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class AutoStopLauncherTest(unittest.TestCase):
    def run_launcher(self, sweep_exit):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'phase1').mkdir()
            (root / '.venv/bin').mkdir(parents=True)
            (root / 'bin').mkdir()
            shutil.copy('phase1/run_agenttrace_and_stop.sh', root / 'phase1/launcher.sh')
            fake_python = root / '.venv/bin/python'
            fake_python.write_text(f'#!/bin/sh\nexit {sweep_exit}\n')
            fake_python.chmod(0o755)
            fake_sudo = root / 'bin/sudo'
            fake_sudo.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$TEST_SHUTDOWN_LOG"\n')
            fake_sudo.chmod(0o755)
            env = {**os.environ, 'PATH': str(root / 'bin') + ':' + os.environ['PATH'],
                   'TEST_SHUTDOWN_LOG': str(root / 'shutdown.log')}
            result = subprocess.run(['bash', str(root / 'phase1/launcher.sh')], env=env,
                                    capture_output=True, text=True, timeout=10)
            status = (root / 'results/phase1/agenttrace_autostop_status.txt').read_text()
            self.assertEqual(result.returncode, sweep_exit)
            self.assertIn(f'sweep_exit_code={sweep_exit}\n', status)
            self.assertEqual((root / 'shutdown.log').read_text().strip(), '-n shutdown -h now')

    def test_stops_after_success(self):
        self.run_launcher(0)

    def test_stops_after_sweep_error(self):
        self.run_launcher(7)


if __name__ == '__main__':
    unittest.main()
