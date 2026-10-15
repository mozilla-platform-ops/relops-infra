"""Exercise staging in a temporary filesystem; never image or contact a host."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

WRAPPER = Path(os.environ.get('ONESHOT_TEST_WRAPPER',
    str(Path(__file__).resolve().parents[1] / 'oneshot_hardened_2404_talos.sh')))
if not WRAPPER.exists():
    WRAPPER = Path(__file__).with_name('oneshot_hardened_2404_talos.sh')
SOURCE = WRAPPER.read_text()


class BootstrapWrapperTests(unittest.TestCase):
    def test_remote_staging_and_launcher(self):
        stage = re.search(r"<<'REMOTE_STAGE'\n(.*?)\nREMOTE_STAGE", SOURCE, re.S).group(1)
        launcher = re.search(r"<<'LAUNCHER'\n(.*?)\nLAUNCHER", stage, re.S).group(1)
        subprocess.run(['bash', '-n'], input=stage, text=True, check=True)
        subprocess.run(['bash', '-n'], input=launcher, text=True, check=True)
        for override in ('', 'gecko-t-linux-talos-2404-relops-aje'):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for child in ('root/.ssh', 'root/ronin-bootstrap', 'etc/puppet', 'bin', 'python'):
                    (root / child).mkdir(parents=True, exist_ok=True)
                slug = 'mozilla-platform-ops/ronin_puppet-private'
                key = 'fixture-key\nwith-newline\n'
                (root / 'root/vault.yaml').write_text(json.dumps({'github': {'deploy_keys': {slug: {'private_key': key}}}}))
                # JSON fixture loader stands in for the host's installed PyYAML.
                (root / 'python/yaml.py').write_text('import json\nsafe_load = json.loads\n')
                (root / 'root/ronin-bootstrap/bootstrap_linux.sh').write_text('#!/bin/bash\nexit 0\n')
                install = root / 'bin/install'
                install.write_text('#!' + sys.executable + '\nimport pathlib,sys\npathlib.Path(sys.argv[-1]).mkdir(parents=True,exist_ok=True)\n')
                install.chmod(0o755)
                for command in ('systemctl', 'systemd-run'):
                    stub = root / 'bin' / command
                    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_EVENTS"\n')
                    stub.chmod(0o755)
                env = dict(os.environ, PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'],
                           PYTHONPATH=str(root / 'python'), TEST_EVENTS=str(root / 'events'),
                           PUPPET_REPO='https://github.com/' + slug + '.git',
                           PUPPET_BRANCH='20260922-harden_puppet', WORKER_TYPE_OVERRIDE=override)
                mapped = stage.replace('/root/', str(root / 'root') + '/').replace('/etc/puppet', str(root / 'etc/puppet'))
                subprocess.run(['bash'], input=mapped, text=True, env=env, check=True, capture_output=True)
                settings = root / 'etc/puppet/ronin_settings'
                self.assertEqual(settings.stat().st_mode & 0o777, 0o600)
                values = subprocess.check_output(['bash', '-c', 'source "$1"; printf "%s\\n%s\\n%s" "$PUPPET_REPO" "$PUPPET_BRANCH" "${WORKER_TYPE_OVERRIDE:-}"', '-', str(settings)], text=True).splitlines()
                self.assertEqual(values[:2], [env['PUPPET_REPO'], env['PUPPET_BRANCH']])
                self.assertEqual(values[2] if len(values) > 2 else '', override)
                keyfile = root / 'root/.ssh/mozilla_platform_ops_ronin_puppet_private_deploy_key'
                self.assertEqual(keyfile.read_text(), key)
                self.assertEqual(keyfile.stat().st_mode & 0o777, 0o600)
                self.assertEqual((root / 'etc/puppet_role').read_text(), 'gecko_t_linux_2404_talos\n')
                self.assertIn('--unit=ronin-bootstrap /bin/bash', (root / 'events').read_text())
                self.assertIn('exec /bin/bash', (root / 'root/ronin-bootstrap/run.sh').read_text())
                self.assertNotIn('bootstrap_hardened_puppet.sh', SOURCE)

    def test_dry_run_and_full_wrapper_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / 'oneshot_hardened_2404_talos.sh'
            wrapper.write_text(SOURCE)
            image = root / 'oneshot_2404_x11_talos.sh'
            image.write_text('#!/bin/sh\necho "image:$SKIP_CONVERGE:$*" >> "$TEST_EVENTS"\n')
            image.chmod(0o755)
            repo = root / 'repo'
            for relative in ('provisioners/linux/bootstrap_linux.sh', 'modules/puppet/files/github_known_hosts', 'modules/linux_packages/files/yq-er.sh'):
                destination = repo / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text('fixture\n')
            binary = root / 'bin'
            binary.mkdir()
            ssh = binary / 'ssh'
            ssh.write_text('#!/bin/sh\necho "ssh:$*" >> "$TEST_EVENTS"\ncat >/dev/null\n')
            ssh.chmod(0o755)
            vault = root / 'vault.yaml'
            vault.write_text('fixture\n')
            env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'],
                       RONIN_PUPPET_DIR=str(repo), VAULT_SOURCE=str(vault),
                       PUPPET_REPO='https://github.com/mozilla-platform-ops/ronin_puppet-private.git',
                       PUPPET_BRANCH='20260922-harden_puppet', TEST_EVENTS=str(root / 'events'))
            host = 't-linux64-ms-178.test.releng.mdc1.mozilla.com'
            dry = subprocess.run(['bash', str(wrapper), host], env=env, capture_output=True, text=True)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertFalse((root / 'events').exists())
            run = subprocess.run(['bash', str(wrapper), host, '--confirm'], env=env, capture_output=True, text=True, input='')
            self.assertEqual(run.returncode, 0, run.stderr)
            events = (root / 'events').read_text()
            self.assertIn('image:1:', events)
            self.assertIn('WORKER_TYPE_OVERRIDE=', events)
            self.assertIn('Standard bootstrap started independently of SSH', run.stdout)
            (root / 'events').unlink()
            env['PUPPET_BRANCH'] = 'master'
            rejected = subprocess.run(['bash', str(wrapper), host, '--confirm'], env=env, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse((root / 'events').exists())


if __name__ == '__main__':
    unittest.main()
