#!/usr/bin/env python3

import argparse
import datetime
import logging
import logging.handlers
import multiprocessing
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time

import humanize
import paramiko
import psutil
from scp import SCPClient

from virtualbox_gw_runner import banner, exceptions, net, utils, vbox

# virtualbox_gw_runnner.py general process
#   vbox start
#   scp scripts over
#   ssh in to start g-w
#   when g-w done, vbox stop
#   restore snapshot

# time to wait between cycles
CHILL_TIME = 10

# where tc binaries are located
TC_COMPONENT_DIR = "v49.1.1"

WR_CONFIG_TEMPLATE_FILE_NAME = "worker-runner-config.template"
WR_CONFIG_FILE_NAME = "worker-runner.config"
GW_CONFIG_FILE_NAME = "generic-worker.config"

# local paths (on the virtualbox host)
HOST_RUN_DIR = "/home/ubuntu/.virtualbox_gw_runner"

# remote paths (in the virtualbox guest)
TC_DEPLOY_DIR = "~/.taskcluster"  # in ~ubuntu, TODO: use full path
WR_CONFIG_PATH = f"{TC_DEPLOY_DIR}/{WR_CONFIG_FILE_NAME}"
GW_CONFIG_PATH = f"{TC_DEPLOY_DIR}/{GW_CONFIG_FILE_NAME}"

# vbox constants
VM_NAME = "Ubuntu-22-04-test"

# guest vm constants
# ssh -l ubuntu localhost -p 2222
SSH_PORT = 2222
SSH_USER = "ubuntu"
SSH_HOST = "localhost"
SSH_KEY = pathlib.Path("~/.ssh/id_ed25519").expanduser()


wr_logger = logging.getLogger("worker-runner")
wr_logger.setLevel(logging.INFO)

handler = logging.handlers.RotatingFileHandler(
    "/tmp/wr.log", maxBytes=50000, backupCount=5
)
wr_logger.addHandler(handler)

formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)


def _handle_sigterm(sig, frame, result_code=0):
    print(
        f"*** received signal '{signal.Signals(sig).name}'. exiting with exit code {result_code}..."
    )
    sys.exit(result_code)


def get_connection():
    return net.get_connection(SSH_HOST, SSH_USER, SSH_PORT)


def scp_put_dir(local_dir, remote_path, make_executable=True):
    ssh = get_connection()
    scp = SCPClient(ssh.get_transport())

    while True:
        try:
            scp.put(local_dir, remote_path=remote_path, recursive=True)
            break
        except EOFError:
            print("  scp_put_dir: received EOFError. retrying...")
        time.sleep(2)

    if make_executable:
        stdin, stdout, stderr = ssh.exec_command(f"chmod 755 {remote_path}/*")


def scp_put_file(local_path, remote_path):
    ssh = get_connection()
    scp = SCPClient(ssh.get_transport())

    scp.put(local_path, remote_path=remote_path)


def deploy_tc_components():
    # binaries
    for file in os.listdir(TC_COMPONENT_DIR):
        scp_put_file(f"{TC_COMPONENT_DIR}/{file}", f"{TC_DEPLOY_DIR}/{file}")

    # place configs
    # scp_put_file(WR_CONFIG_FILE_NAME, TC_DEPLOY_DIR)
    scp_put_file(GW_CONFIG_FILE_NAME, GW_CONFIG_PATH)


def wait_for_ssh_to_be_ready(timeout=3, retry_interval=1):
    retry_interval = float(retry_interval)
    timeout = int(timeout)
    timeout_start = time.time()

    while time.time() < timeout_start + timeout:
        time.sleep(retry_interval)
        try:
            # TODO: take connection as arg
            get_connection()
            break
        except ConnectionResetError:
            print("  wait_for_ssh_to_be_ready: Connection reset. Waiting...")
        except paramiko.ssh_exception.SSHException:
            print("  wait_for_ssh_to_be_ready: SSH exception. Waiting...")
        except paramiko.ssh_exception.NoValidConnectionsError:
            print("  wait_for_ssh_to_be_ready: SSH not ready yet. Waiting...")
        time.sleep(5)


# wait for gui processes to be present on guest vm
def wait_for_gui(debug=False):
    # `ps -ef | grep evolution-alarm`
    ssh = get_connection()
    while True:
        stdin, stdout, stderr = ssh.exec_command(
            "ps -ef | grep evolution-alarm | grep -v grep"
        )
        output = stdout.read().decode("UTF-8").strip()
        if debug:
            print(f"  wait_for_gui: stdout: '{output}'")

        length_of_output = len(output)
        if debug:
            print(f"  wait_for_gui: len: {length_of_output}")
        if length_of_output != 0:
            break
        time.sleep(3)


# use config generated in run_wr_locally_until_config_generated()
# to run g-w in the guest
def run_gw_in_guest(noop=False):
    if noop:
        noop_time = 20
        print(f"  run_gw_in_guest: noop mode, sleeping {noop_time}s...")
        time.sleep(noop_time)
        return
    ssh = get_connection()

    # wait for host to be ready
    while True:
        try:
            ssh.exec_command("ls")
            break
        except:
            time.sleep(1)

    # worker-runner's binary is start-worker
    # TODO: add --with-worker-runner? doesn't do anything other than relay logging for now...
    #   - https://github.com/taskcluster/taskcluster/blob/cdfacaf129dbfd3327ac81232d33d1f768f3cf2c/workers/generic-worker/main.go#L119  # noqa
    command = (
        f"{TC_DEPLOY_DIR}/generic-worker-simple run --config {GW_CONFIG_PATH} 2>&1"
    )
    print(f"  command is: '{command}'")
    _stdin, stdout, _stderr = ssh.exec_command(command)

    # async output
    while True:
        line = stdout.readline()
        if not line:
            break
        print(line, end="")

    # synchronous output
    # print(stdout.read().decode())

    print(f"  exit code: {stdout.channel.recv_exit_status()}")
    if stdout.channel.recv_exit_status() != 0:
        raise exceptions.VBGWRNonZeroSSHException

    ssh.close()


def run_wr_locally(noop=False):
    # TODO: don't rely on assumed path, explicitly state
    # TODO: save output to file that gets rotated (use log-rotate?)
    command = f"{TC_COMPONENT_DIR}/start-worker {WR_CONFIG_FILE_NAME} 2>&1"
    print(command)
    process = subprocess.Popen(
        command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    while True:
        output = process.stdout.readline()
        if output == b"" and process.poll() is not None:
            break
        if output:
            wr_logger.info(output.decode().strip())


def run_wr_in_background_forever_and_wait_for_gw_config(noop=False, test_mode=False):
    thread = threading.Thread(target=run_wr_locally)
    # TODO: store this reference for shutdown later?
    thread.start()

    if test_mode:
        print("run_wr_in_background_forever_and_wait_for_gw_config: TEST MODE")
        print("  not waiting for w-r to create a config, just touching config...")
        # just touch the config file to make the process continue
        utils.touch(GW_CONFIG_FILE_NAME)
        return

    while True:
        if not thread.is_alive():
            print("wr thread has exited... not good... exiting.")
            print("  wr logs are at /tmp/wr.log")
            sys.exit(1)
        # TODO: use full path
        if os.path.isfile(GW_CONFIG_FILE_NAME):
            print("  found g-w config file, returning...")
            break
        time.sleep(1)


def print_elapsed_time(start_ts, end_ts, label="elapsed time"):
    print(
        f"  {label}: {(end_ts - start_ts).total_seconds()}s ({humanize.precisedelta(end_ts - start_ts)})"
    )


def main_loop():
    # description='Example of argparse usage'
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-t",
        "--test-mode",
        action="store_true",
        help="enable test mode, read source for details",
    )
    args = parser.parse_args()

    banner.banner()

    # TODO: sanity check
    # - ensure required files and directories are present

    # generate wr config
    # print("* generating wr config...")
    # for non-cloud g-w, do a find/replace
    # generate_wr_config()
    # for cloud-gw, just do a copy
    # copy_wr_config()

    # for w-r google w/ g-w, we set /etc/hosts once the vm has booted
    # - in order for start-worker to work need metadata requests to work
    #   - set 'metadata' host to what it resolves to outside of container
    # print("* resolving 'metadata' host...")
    # metadata_host_output = socket.gethostbyname("metadata")
    # print(f"  got {metadata_host_output}")

    # generate gw config via start-worker
    # print("* generating g-w config via start-worker...")
    # start_ts = datetime.datetime.now()
    # generated_gw_config_file = run_wr_locally_until_config_generated()
    # end_ts = datetime.datetime.now()
    # print_elapsed_time(start_ts, end_ts)

    # run wr and generate gw config
    print("* running wr in background forever, waiting for g-w config...")
    run_wr_in_background_forever_and_wait_for_gw_config(test_mode=args.test_mode)

    counter = 0
    while True:
        banner.cycle_start_banner()
        cycle_start_ts = datetime.datetime.now()
        counter += 1
        print(f"* cycle start: cycle {counter}")

        # vbox stop
        print("* stopping vm...")
        start_ts = datetime.datetime.now()
        vbox.poweroff(VM_NAME)
        # we're ignoring non-zero exit code as this can be flaky
        # so wait until this command is empty
        # TODO: potentially just reboot the host vm at this point? if it doesn't keep working...
        print("  waiting for vm to stop...")
        vbox.wait_for_no_vbox_vms_running()
        end_ts = datetime.datetime.now()
        print_elapsed_time(start_ts, end_ts)

        # restore snapshot
        print("* restoring vm snapshot...")
        start_ts = datetime.datetime.now()
        vbox.restore_to_latest_snapshot(VM_NAME)
        end_ts = datetime.datetime.now()
        print_elapsed_time(start_ts, end_ts)

        # optimize cpu and memory settings for this host
        #   - could have been built on a 2 core system with 4GB RAM, but now on
        #     a 12 core host with 32GB RAM.
        #
        # get cores
        print("* optimizing vm CPU and RAM...")
        start_ts = datetime.datetime.now()
        cpu_count = multiprocessing.cpu_count()
        # get memory of host, convert bytes to MB
        mem_mbs = psutil.virtual_memory().total / 1000000
        # ubuntu host takes up ~1.9 GB with used (~300GB) and cache
        res_mbs = 2 * 1000
        # subtract memory - reserve (like 1.5-2 GB?)
        final_vm_mem_mbs = round(mem_mbs - res_mbs)
        print(f"  mem: {mem_mbs}, res: {res_mbs}, final: {final_vm_mem_mbs}")
        vbox.modify_vm(VM_NAME, cpu_count, final_vm_mem_mbs)
        end_ts = datetime.datetime.now()
        print_elapsed_time(start_ts, end_ts)

        # vbox start
        print("* starting vm...")
        start_ts = datetime.datetime.now()
        vbox.start_vm(VM_NAME)
        # wait until a vm is running
        vbox.wait_for_vbox_vms_running()
        end_ts = datetime.datetime.now()
        print_elapsed_time(start_ts, end_ts)

        # wait for host
        print("* waiting for ssh...")
        start_ts = datetime.datetime.now()
        paramiko.util.log_to_file("/dev/null")
        wait_for_ssh_to_be_ready()
        paramiko.util.log_to_file(sys.stderr)
        end_ts = datetime.datetime.now()
        print_elapsed_time(start_ts, end_ts, label="elapsed wait time")

        # set /etc/hosts to have 'metadata' host that vm instances can resolve (used by start-worker?)
        # print("* injecting hosts into /etc/hosts...")
        # ssh = get_connection()
        # command = (
        #     f"sudo bash -c \"echo '{metadata_host_output} metadata' >> /etc/hosts\""
        # )
        # _stdin, stdout, _stderr = ssh.exec_command(command)
        # print(f"  exit code: {stdout.channel.recv_exit_status()}")

        # not making ~/tasks, ~/downloads, and ~/caches on client.
        #   - g-w does this

        # scp scripts over
        print("* deploying tc components...")
        start_ts = datetime.datetime.now()
        deploy_tc_components()
        end_ts = datetime.datetime.now()
        print_elapsed_time(start_ts, end_ts)

        # wait for gui processes
        print("* waiting for gui...")
        start_ts = datetime.datetime.now()
        wait_for_gui()
        end_ts = datetime.datetime.now()
        print_elapsed_time(start_ts, end_ts, label="elapsed wait time")

        # TODO: wait for system to stabilize? via load being less than # of cores.

        # ssh in to start w-r
        # print("* running worker runner...")
        # run_wr_in_guest(noop=False)
        #
        # ssh in to start g-w
        print("* running g-w...")
        try:
            run_gw_in_guest(noop=False)
        except exceptions.VBGWRNonZeroSSHException:
            print("g-w returned non-zero, continuing...")

        # TODO: reboot host after X cycles?

        print(f"* cycle complete: cycle {counter}")
        cycle_end_ts = datetime.datetime.now()
        print_elapsed_time(cycle_start_ts, cycle_end_ts, label="cycle elapsed time")

        # wait a bit before starting over again
        if CHILL_TIME:
            print(f"* chilling for a {CHILL_TIME}s, then starting again...")
            time.sleep(CHILL_TIME)


if __name__ == "__main__":
    # systemd sends USR2 to terminate, handle gracefully
    signal.signal(signal.SIGUSR2, _handle_sigterm)
    main_loop()
