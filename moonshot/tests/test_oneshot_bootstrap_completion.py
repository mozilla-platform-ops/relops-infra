"""Exercise bootstrap exit handling without connecting to a worker."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / 'oneshot_linux.sh').read_text()
FUNCTIONS = SOURCE[SOURCE.index('run_remote_script() {'):SOURCE.index('# main\n')]


class BootstrapCompletionTests(unittest.TestCase):
    def run_bootstrap(self, status, output, override=False, upload_failure=False):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, TMPDIR=directory, TEST_STATUS=str(status),
                       TEST_OUTPUT=output, TEST_UPLOAD_FAILURE=str(int(upload_failure)))
            env.pop('ONESHOT_PUPPET_REPO', None)
            env.pop('ONESHOT_PUPPET_BRANCH', None)
            if override:
                env.update(ONESHOT_PUPPET_REPO='https://example.com/puppet.git',
                           ONESHOT_PUPPET_BRANCH='test')
            harness = '''set -euo pipefail
trap 'echo UNEXPECTED_ERROR >&2; exit 1' ERR
SSH_USER=relops
HOSTNAME=test-worker
HOST_NUMBER=004
PYFIGLET_PRESENT=false
REMOTE_SCRIPT='echo bootstrap'
ssh() {
  if [[ "$*" == *'mktemp /tmp/remote_script.'* ]]; then
    if [[ "$TEST_UPLOAD_FAILURE" == 1 ]]; then return 23; fi
    echo /tmp/remote-script-test
    return 0
  fi
  printf '%s\\n' "$TEST_OUTPUT"
  return "$TEST_STATUS"
}
'''
            result = subprocess.run(['bash'], input=harness + FUNCTIONS + '\nfinish_bootstrap\n',
                                    text=True, capture_output=True, env=env, timeout=5)
            logs = list(Path(directory).glob('moonshot-bootstrap.*'))
            self.assertEqual(len(logs), 1)
            self.assertIn(output if not upload_failure else '', logs[0].read_text())
            return result

    def test_success_and_confirmed_reboot_show_banner(self):
        for override in (False, True):
            for status in (0, 255):
                with self.subTest(override=override, status=status):
                    result = self.run_bootstrap(status, 'Success. Rebooting...', override)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('=== SUCCESS: test-worker bootstrap complete ===', result.stdout)

    def test_unconfirmed_disconnect_and_bootstrap_error_fail(self):
        for override in (False, True):
            for status, output in ((255, 'Connection closed'), (1, 'Success. Rebooting...'),
                                   (255, '+ echo Success. Rebooting...')):
                with self.subTest(override=override, status=status, output=output):
                    result = self.run_bootstrap(status, output, override)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn('=== SUCCESS:', result.stdout)
                    self.assertIn('success is unconfirmed', result.stderr)

    def test_upload_error_is_not_masked_by_remote_script_helper(self):
        result = self.run_bootstrap(0, '', override=True, upload_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('=== SUCCESS:', result.stdout)
        self.assertIn('status 23', result.stderr)


if __name__ == '__main__':
    unittest.main()
