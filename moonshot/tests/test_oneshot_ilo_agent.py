"""Check agent preflight with fake commands; never contact or image a host."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT = 'SHA256:iPO+iaKxEP9QOyIm3zC8/os20+GHLerfndIFRlgh5FQ'


class IloAgentTests(unittest.TestCase):
    def run_preflight(self, identities='', status=0, skip=False, wrapper=None):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory)
            for name, body in {
                'ssh-add': 'echo called > "$AGENT_CALLED"\nprintf "%s\\n" "$IDENTITIES"\nexit "$AGENT_STATUS"',
                # Stop at the first operation after preflight, before imaging.
                'ps': 'echo PREFLIGHT_PASSED >&2\nexit 42',
            }.items():
                command = binary / name
                command.write_text('#!/bin/sh\n' + body + '\n')
                command.chmod(0o755)
            env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'],
                       IDENTITIES=identities, AGENT_STATUS=str(status),
                       AGENT_CALLED=str(binary / 'called'))
            env.pop('SKIP_REIMAGE', None)
            if skip:
                env['SKIP_REIMAGE'] = '1'
            args = [str(ROOT / 'oneshot_linux.sh'), '1', '2', '003', 'role', '2404']
            if wrapper:
                args = [str(wrapper), '003']
            result = subprocess.run(['bash', *args], env=env, input='',
                                    text=True, capture_output=True, timeout=5)
            return result, (binary / 'called').exists()

    def test_expected_key_passes_with_any_comment(self):
        result, called = self.run_preflight(
            f'256 SHA256:other unrelated (ED25519)\n4096 {FINGERPRINT} renamed-key (RSA)')
        self.assertTrue(called)
        self.assertIn('PREFLIGHT_PASSED', result.stderr)

    def test_missing_key_stops_before_work(self):
        for identities in ('', '4096 SHA256:other Relops common RSA key (RSA)',
                           f'4096 {FINGERPRINT}extra misleading (RSA)'):
            with self.subTest(identities=identities):
                result, called = self.run_preflight(identities)
                self.assertTrue(called)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('PREFLIGHT_PASSED', result.stderr)
                self.assertIn('HP iLO SSH key', result.stderr)
                self.assertIn('ssh-add ~/.ssh/id_rsa_relops_2020-05-07', result.stderr)

    def test_empty_or_unavailable_agent_stops_before_work(self):
        for status, message in ((1, 'The agent has no identities.'),
                                (2, 'Could not open a connection to your authentication agent.')):
            with self.subTest(status=status):
                result, called = self.run_preflight(message, status)
                self.assertTrue(called)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('PREFLIGHT_PASSED', result.stderr)
                self.assertIn(message, result.stderr)
                self.assertIn('ssh-add ~/.ssh/id_rsa_relops_2020-05-07', result.stderr)

    def test_skip_reimage_does_not_check_agent(self):
        result, called = self.run_preflight(status=2, skip=True)
        self.assertFalse(called)
        self.assertIn('PREFLIGHT_PASSED', result.stderr)

    def test_dry_run_wrappers_do_not_check_agent(self):
        for wrapper in ROOT.glob('oneshot_*.sh'):
            if wrapper.name == 'oneshot_linux.sh' or 'hardened' in wrapper.name:
                continue
            with self.subTest(wrapper=wrapper.name):
                result, called = self.run_preflight(status=2, wrapper=wrapper)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(called)


if __name__ == '__main__':
    unittest.main()
