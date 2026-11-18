"""Run real Expect against a fake SSH chassis; never contact real hardware."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(os.environ.get('REIMAGE_2404_SCRIPT',
    str(Path(__file__).resolve().parents[1] / 'reimage_2404.exp')))
if not SCRIPT.exists():
    SCRIPT = Path(__file__).with_name('reimage_2404.exp')
FAKE_SSH = r'''
import json, os, sys, tty
from pathlib import Path
mode = os.environ['TEST_SCENARIO']
tty.setraw(sys.stdin.fileno())
def emit(text):
    print(text, flush=True)
def prompt():
    emit('hpiLO->')
if mode == 'connect_eof':
    sys.exit(0)
if mode == 'connect_timeout':
    import time
    time.sleep(10)
    sys.exit(0)
prompt()
state = 'On'
power_on_attempts = 0
console = False
buffer = b''
while True:
    chunk = os.read(0, 1)
    if not chunk:
        break
    buffer += chunk
    if console and buffer.endswith(b'\x1b('):
        console = False
        buffer = b''
        prompt()
        continue
    if chunk not in (b'\r', b'\n'):
        continue
    command = buffer.decode(errors='replace').strip()
    buffer = b''
    with open(os.environ['TEST_COMMANDS'], 'a') as log:
        log.write(json.dumps(command) + '\n')
    if console:
        continue
    if command.startswith('set node bootonce'):
        if mode == 'pxe_command_eof':
            sys.exit(0)
        if mode == 'pxe_command_timeout':
            continue
        if mode == 'pxe_unconfirmed':
            prompt()
            continue
        emit('bootonce: PXE')
    elif command.startswith('set node power off'):
        if mode != 'stuck_on':
            state = 'Off'
    elif command.startswith('set node power on'):
        power_on_attempts += 1
        if mode not in ('stuck_off',) and (mode != 'second_attempt' or power_on_attempts == 2):
            state = 'On'
        emit('Power on requested')
    elif command.startswith('show node power'):
        if mode == 'state_eof':
            sys.exit(0)
        if mode == 'state_timeout':
            continue
        if mode != 'missing_state':
            emit('Power State: ' + state)
    elif command.startswith('connect node vsp'):
        if mode == 'vsp_eof':
            sys.exit(0)
        if mode == 'vsp_timeout':
            continue
        if mode == 'vsp_off':
            emit('The server is not powered on. Virtual Serial Port is not available.')
            continue
        console = True
        emit('Virtual Serial Port Active: C2N1')
        if mode == 'menu_eof':
            sys.exit(0)
        if mode == 'menu_timeout':
            continue
        emit('netboot.xyz - v2.x')
        if mode == 'default_timeout':
            continue
        emit('booting default...')
        continue
    elif command == 'exit':
        if mode == 'exit_timeout':
            continue
        sys.exit(0)
    prompt()
'''


@unittest.skipUnless(shutil.which('expect'), 'Expect is required')
class ReimageTests(unittest.TestCase):
    def simulate(self, scenario):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ssh = root / 'ssh'
            ssh.write_text('#!' + sys.executable + '\n' + FAKE_SSH)
            ssh.chmod(0o755)
            source = SCRIPT.read_text()
            # Accelerate sleeps, character pacing, and bounded deadlines only.
            source = source.replace('set timeout 120', 'set timeout 1')
            source = source.replace('set timeout 300', 'set timeout 1')
            source = source.replace('[clock seconds] + 300', '[clock seconds] + 1')
            source = source.replace('set send_slow {1 .1}', 'set send_slow {10000 .000001}')
            script = root / 'test.exp'
            script.write_text('rename sleep real_sleep\nproc sleep {args} {}\n' + source)
            commands = root / 'commands'
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                       TEST_SCENARIO=scenario, TEST_COMMANDS=str(commands))
            result = subprocess.run(['expect', str(script), '--chassis', 'fixture-chassis',
                                     '--node', 'c2n1'], env=env, text=True,
                                    capture_output=True, timeout=12)
            calls = [json.loads(line) for line in commands.read_text().splitlines()] if commands.exists() else []
            return result, calls

    def test_success_requires_both_confirmed_states(self):
        result, calls = self.simulate('success')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Power is Off (confirmed)', result.stdout)
        self.assertIn('Power is On (confirmed)', result.stdout)
        self.assertIn('Finished fixture-chassis c2n1', result.stdout)
        self.assertIn('connect node vsp c2n1', calls)

    def test_second_power_attempt_can_succeed(self):
        result, calls = self.simulate('second_attempt')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls.count('set node power on c2n1'), 2)

    def test_exhausted_power_attempts_never_open_console(self):
        for scenario, wanted, observed in [('stuck_off', 'On', 'Off'), ('stuck_on', 'Off', 'On')]:
            with self.subTest(scenario=scenario):
                result, calls = self.simulate(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('last observed Power State: ' + observed, result.stderr)
                self.assertNotIn('Power is ' + wanted + ' (confirmed)', result.stdout)
                self.assertIn('Still ' + observed + '; waiting for ' + wanted, result.stdout)
                self.assertNotIn('hpiLO->.', result.stdout)
                self.assertNotIn('connect node vsp c2n1', calls)

    def test_stage_errors_exit_with_context(self):
        scenarios = ('connect_eof', 'connect_timeout', 'pxe_command_eof',
                     'pxe_command_timeout', 'pxe_unconfirmed', 'state_eof',
                     'state_timeout', 'missing_state', 'vsp_eof', 'vsp_timeout',
                     'vsp_off', 'menu_eof', 'menu_timeout', 'default_timeout', 'exit_timeout')
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                result, _ = self.simulate(scenario)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('ERROR: fixture-chassis c2n1:', result.stderr)
                self.assertNotIn('Finished fixture-chassis c2n1', result.stdout)


if __name__ == '__main__':
    unittest.main()
