"""Exercise bundled helper lookup from outside the script directory."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ScriptPathTests(unittest.TestCase):
    def test_wrappers_invoke_adjacent_helper_with_caller_relative_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / 'bundle with spaces'
            caller = root / 'caller'
            bundle.mkdir()
            caller.mkdir()
            (caller / 'settings').write_text('fixture')
            helper = bundle / 'oneshot_linux.sh'
            helper.write_text('#!/bin/bash\nprintf "%s\\n" "$PWD" "$@"\ncat "$7"\n')
            helper.chmod(0o755)
            (caller / 'oneshot_linux.sh').write_text('#!/bin/bash\nexit 99\n')
            for name in ('oneshot_1804_x11_talos.sh', 'oneshot_2404_x11_talos.sh',
                         'oneshot_2404_wayland_talos.sh', 'oneshot_2404_netperf.sh'):
                shutil.copy(ROOT / name, bundle / name)
                result = subprocess.run([str(bundle / name), '229', '--ronin-settings', 'settings', '--confirm'],
                                        cwd=caller, text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(str(caller), result.stdout)
                self.assertIn('fixture', result.stdout)

    def test_oneshot_and_reimage_resolve_each_helper_in_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / 'bundle with spaces'
            caller = root / 'caller'
            binary = root / 'bin'
            for directory in (bundle, caller, binary):
                directory.mkdir()
            for name in ('oneshot_linux.sh', 'reimage_1804.sh', 'reimage_2404.sh'):
                shutil.copy(ROOT / name, bundle / name)
            for version in ('1804', '2404'):
                script = bundle / f'reimage_{version}.exp'
                script.write_text('#!/bin/bash\nprintf "REIMAGE_FIXTURE %s\\n" "$*"\nexit 42\n')
                script.chmod(0o755)
            for name, body in {'flock': 'exit 0', 'uv': 'exit 1', 'sleep': 'exit 0', 'ps': 'echo oneshot_wrapper.sh'}.items():
                script = binary / name
                script.write_text(f'#!/bin/bash\n{body}\n')
                script.chmod(0o755)
            (caller / 'settings').write_text('echo SETTINGS_FIXTURE\n')
            env = dict(os.environ, PATH=f"{binary}{os.pathsep}{os.environ['PATH']}")
            env.pop('SKIP_REIMAGE', None)
            env.pop('SKIP_CONVERGE', None)
            for version in ('1804', '2404'):
                result = subprocess.run([str(bundle / 'oneshot_linux.sh'), '1', '2', '003', 'role', version,
                                         '--ronin-settings', 'settings'], cwd=caller, env=env,
                                        input='y\n', text=True, capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('SETTINGS_FIXTURE', result.stdout)
                self.assertIn('REIMAGE_FIXTURE --chassis', result.stdout)
                self.assertIn('All 3 attempts failed', result.stdout)
                self.assertTrue(list((bundle / 'logs').glob(f'reimage_{version}.*.log')))


if __name__ == '__main__':
    unittest.main()
