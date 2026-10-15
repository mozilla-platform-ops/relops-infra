"""Use dry runs only to prevent zero-prefixed host numbers changing targets."""
import os
from pathlib import Path
import subprocess
import unittest

SCRIPT = Path(os.environ.get('ONESHOT_HOST_SCRIPT',
    str(Path(__file__).resolve().parents[1] / 'oneshot_2404_x11_talos.sh')))
if not SCRIPT.exists():
    SCRIPT = Path(__file__).with_name('oneshot_2404_x11_talos.sh')


class HostNumberTests(unittest.TestCase):
    def dry_run(self, *arguments):
        return subprocess.run(['bash', str(SCRIPT), *arguments],
                              text=True, capture_output=True, timeout=5)

    def test_all_zero_prefixed_host_ids_remain_decimal(self):
        for number in range(1, 100):
            host = f't-linux64-ms-{number:03d}.test.releng.mdc1.mozilla.com'
            with self.subTest(host=host):
                result = self.dry_run(host)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Hostname:     ' + host, result.stdout)
                self.assertIn(f'Host Number:  {number:03d}', result.stdout)
                chassis = (number - 1) // 45 + 1
                cartridge = (number - 1) % 45 + 1
                self.assertIn(f'./oneshot_linux.sh {chassis} {cartridge} {number:03d} ', result.stdout)

    def test_input_forms_and_explicit_arguments(self):
        for value in ('47', '047', 't-linux64-ms-047', 't-linux64-ms-047.test.releng.mdc1.mozilla.com'):
            with self.subTest(value=value):
                result = self.dry_run(value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('t-linux64-ms-047.test.releng.mdc1.mozilla.com', result.stdout)
                self.assertIn('./oneshot_linux.sh 2 2 047 ', result.stdout)
        explicit = self.dry_run('2', '2', '047')
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertIn('./oneshot_linux.sh 2 2 047 ', explicit.stdout)

    def test_zero_and_invalid_input_rejected(self):
        for value in ('000', '0', 'not-a-host'):
            with self.subTest(value=value):
                self.assertNotEqual(self.dry_run(value).returncode, 0)


if __name__ == '__main__':
    unittest.main()
