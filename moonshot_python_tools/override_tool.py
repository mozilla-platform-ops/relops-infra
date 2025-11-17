#!/usr/bin/env python3

import argparse
import os
import sys
import datetime
import paramiko
import subprocess

# install a ronin puppet override file or disables them

# the override file lives at /etc/puppet/ronin_settings

# if enabling, copy local override file in local dir to the remote host
# if disabling, move the override file to FILE.disabled.DATETIME

# override file format:

# heredoc
example_doc = """
# if you place this file at `/etc/puppet/ronin_settings`
# the `run-puppet.sh` script will use the values here.

# puppet overrides
PUPPET_REPO='https://github.com/aerickson/ronin_puppet.git'
PUPPET_BRANCH='moonshot_linux_py311_and_tc_update_plus_1804_hg_upgrade'
PUPPET_MAIL='aerickson@gmail.com'

# taskcluster overrides
# WORKER_TYPE_OVERRIDE='gecko-t-linux-talos-1804-staging'
""".lstrip()

REMOTE_OVERRIDE_PATH = "/etc/puppet/ronin_settings"

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
CLEAR = "\033[0m"


def ssh_connect(host, user=None):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, username=user)
    except Exception as e:
        print(f"SSH connection failed: {e}")
        sys.exit(1)
    return ssh

def enable_override(host, local_override, user=None):
    if not os.path.isfile(local_override):
        print(f"Local override file '{local_override}' does not exist.")
        sys.exit(1)

    remote = f"{host}" if not user else f"{user}@{host}"

    try:
        # Use ssh with sudo tee to write the file contents
        with open(local_override, "rb") as f:
            file_data = f.read()
        import shlex

        ssh_cmd = [
            "ssh", remote,
            f"sudo tee {shlex.quote(REMOTE_OVERRIDE_PATH)} > /dev/null && "
            f"sudo chmod 644 {shlex.quote(REMOTE_OVERRIDE_PATH)} && "
            f"sudo chown root:root {shlex.quote(REMOTE_OVERRIDE_PATH)}"
        ]
        print(f"Running: {' '.join(ssh_cmd)} < {local_override}")
        proc = subprocess.Popen(ssh_cmd, stdin=subprocess.PIPE)
        proc.communicate(input=file_data)
        if proc.returncode != 0:
            print("Failed to copy override file via ssh.")
            sys.exit(1)
        else:
            print(f"Copied {local_override} to {host}:{REMOTE_OVERRIDE_PATH}")
    except Exception as e:
        print(f"Failed to move override file: {e}")
        sys.exit(1)

def show_remote_override(host, user=None):
    ssh = ssh_connect(host, user)
    sftp = ssh.open_sftp()
    try:
        sftp.stat(REMOTE_OVERRIDE_PATH)
        print(f"{RED}Remote override file already exists at {REMOTE_OVERRIDE_PATH} on {host}. Showing contents:{CLEAR}")
        print("")
        with sftp.open(REMOTE_OVERRIDE_PATH, 'r') as f:
            print(f.read().decode(errors="replace"))
        return True
    except FileNotFoundError:
        return False
    finally:
        sftp.close()
        ssh.close()

def disable_override(host, user=None):
    ssh = ssh_connect(host, user)
    dt = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    disabled_path = f"{REMOTE_OVERRIDE_PATH}.disabled.{dt}"
    cmd = f"if [ -f {REMOTE_OVERRIDE_PATH} ]; then sudo mv {REMOTE_OVERRIDE_PATH} {disabled_path}; echo 'Renamed to {disabled_path}'; else echo 'No override file to disable.'; fi"
    try:
        stdin, stdout, stderr = ssh.exec_command(cmd)
        print(stdout.read().decode())
        err = stderr.read().decode()
        if err:
            print(err, file=sys.stderr)
    except Exception as e:
        print(f"Failed to disable override: {e}")
        sys.exit(1)
    finally:
        ssh.close()

def write_log(host, action):
    log_entry = f"{datetime.datetime.now().isoformat()} - {action} override on {host}\n"
    with open("override_tool.log", "a") as log_file:
        log_file.write(log_entry)

def main():
    parser = argparse.ArgumentParser(
        description="Enable or disable ronin puppet override file on a remote host."
    )
    parser.add_argument("command", choices=["enable", "disable"], help="Action to perform")
    parser.add_argument("host", help="Remote host")
    parser.add_argument("--user", help="SSH username")
    parser.add_argument("--local-override", default="ronin_settings", help="Local override file (for enable)")
    args = parser.parse_args()

    if '.' not in args.host:
        previous_host = args.host
        args.host = f"t-linux64-ms-{args.host}.test.releng.mdc1.mozilla.com"
        print(f"Assuming short hostname '{previous_host}', converting to FQDN ({args.host}).")

    if args.command == "enable":
        if show_remote_override(args.host, args.user):
            print(f"{RED}Override file already exists on remote host. Aborting to avoid overwrite.{CLEAR}")
            sys.exit(0)
        enable_override(args.host, args.local_override, args.user)
        print("Override enabled. To apply changes, run the following:")
        print("")
        print(f"  ssh {args.host} sudo run-puppet.sh")
        print("")
        write_log(args.host, "Enabled")
    elif args.command == "disable":
        disable_override(args.host, args.user)
        write_log(args.host, "Disabled")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()