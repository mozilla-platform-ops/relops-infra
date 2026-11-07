"""Test installer/final-host key transitions with fake SSH and fixture files."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(os.environ.get('SSH_READINESS_SCRIPT',
    str(Path(__file__).resolve().parents[1] / 'oneshot_linux.sh')))
if not SOURCE.exists():
    SOURCE = Path(__file__).with_name('oneshot_linux.sh')
source = SOURCE.read_text()
block = source[source.index('# During reimaging,'):source.index('echo "SSH command functionality')]
block = block.replace('$HOME/.ssh/known_hosts', '$TEST_KNOWN_HOSTS')


class SshReadinessTests(unittest.TestCase):
    def run_check(self, retry=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            known = root / 'known_hosts'
            known.write_text('existing-fixture-key\n')
            binary = root / 'bin'
            binary.mkdir()
            ssh = binary / 'ssh'
            ssh.write_text('#!' + sys.executable + '\n' + r'''
import os, sys
from pathlib import Path
counter = Path(os.environ['TEST_COUNTER'])
attempt = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(attempt))
known = Path(os.environ['TEST_KNOWN_HOSTS'])
assert known.read_text() == 'existing-fixture-key\n', 'failed login persisted a key'
if os.environ.get('SKIP_REIMAGE'):
    assert not any(a.startswith('UserKnownHostsFile=') for a in sys.argv)
    print('Host key verification failed.', file=sys.stderr)
    sys.exit(255)
candidate = next(a.split('=',1)[1] for a in sys.argv if a.startswith('UserKnownHostsFile='))
Path(candidate).write_text(('installer-key' if attempt == 1 else 'installed-os-key') + '\n')
if attempt == 1:
    print('Permission denied (publickey,password).', file=sys.stderr)
    sys.exit(255)
sys.exit(0)
''')
            ssh.chmod(0o755)
            env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'],
                       TMPDIR=str(root), TEST_KNOWN_HOSTS=str(known), TEST_COUNTER=str(root / 'counter'))
            env.pop('SKIP_REIMAGE', None)
            if retry:
                env['SKIP_REIMAGE'] = '1'
            prefix = 'set -e\nSSH_USER=relops\ncountdown() { :; }\n'
            result = subprocess.run(['bash'], input=prefix + block, env=env,
                                    text=True, capture_output=True, timeout=5)
            expected = 'existing-fixture-key\n' if retry else 'existing-fixture-key\ninstalled-os-key\n'
            self.assertEqual(known.read_text(), expected)
            self.assertFalse(list(root.glob('moonshot-host-key.*')))
            return result

    def test_failed_installer_auth_does_not_pin_installer_key(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_retry_mode_preserves_host_key_mismatch_failure(self):
        result = self.run_check(retry=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('fix known_hosts', result.stderr)


if __name__ == '__main__':
    unittest.main()
