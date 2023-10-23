import subprocess
import time

from virtualbox_gw_runner import exceptions, utils

VBOX_COMMAND = "VBoxManage"


# set mem and cpu
#   VBoxManage modifyvm Ubuntu-22-04-test --cpus 2 --memory 6144
def modify_vm(vm_name, cpu_count, memory_mb):
    command = (
        f"{VBOX_COMMAND} modifyvm {vm_name} --cpus {cpu_count} --memory {memory_mb}"
    )
    _output, _return_code = utils.run_command(
        command, raise_on_nonzero=True, test_mode=False
    )


def restore_to_snapshot(vm_name, snapshot_name):
    while True:
        try:
            command = f"{VBOX_COMMAND} snapshot {vm_name} restore {snapshot_name}"
            _output, _return_code = utils.run_command(command, test_mode=False)
            break
        except exceptions.VBGWRNonZeroException:
            print("  encountered an issue restoring snapshot. retrying...")
            time.sleep(2)


# current aka latest
def restore_to_latest_snapshot(vm_name):
    while True:
        try:
            command = f"{VBOX_COMMAND} snapshot {vm_name} restorecurrent"
            _output, _return_code = utils.run_command(command, test_mode=False)
            break
        except exceptions.VBGWRNonZeroException:
            print("  encountered an issue restoring snapshot. retrying...")
            time.sleep(2)


def poweroff(vm_name):
    command = f"{VBOX_COMMAND} controlvm {vm_name} poweroff"
    _output, _return_code = utils.run_command(
        command, raise_on_nonzero=False, test_mode=False
    )


def start_vm(vm_name):
    while True:
        try:
            command = f"{VBOX_COMMAND} startvm {vm_name} --type headless"
            _output, _return_code = utils.run_command(
                command, test_mode=False, raise_on_nonzero=False
            )
            break
        except exceptions.VBGWRNonZeroException:
            print("  encountered an starting vm. retrying...")
            time.sleep(2)


# TODO: just invert result of wait_for_vbox_vms_running()?
def wait_for_no_vbox_vms_running():
    while True:
        command = f"{VBOX_COMMAND} list runningvms"
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True
        )
        length_of_output = len(result.stdout.strip())
        if length_of_output == 0:
            break
        time.sleep(2)


def wait_for_vbox_vms_running():
    while True:
        command = f"{VBOX_COMMAND} list runningvms"
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True
        )
        length_of_output = len(result.stdout.strip())
        if length_of_output != 0:
            break
        time.sleep(2)
