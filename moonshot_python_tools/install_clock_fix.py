#!/usr/bin/env python3

import argparse
import os
import sys
import subprocess

def parse_args():
    parser = argparse.ArgumentParser(
        description="Install NTP sync one-shot systemd service on a remote host"
    )
    parser.add_argument("-H", "--host", required=True, help="Remote host to install the service on")
    parser.add_argument("-u", "--user", default=os.getlogin(), help="SSH user (default: $USER)")
    parser.add_argument("-f", "--force", action="store_true", help="Force the operation without confirmation")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show command output")
    return parser.parse_args()

def run_ssh_command(host, user, command, verbose=False):
    """Execute a command on the remote host via SSH"""
    ssh_cmd = ["ssh", f"{user}@{host}", command]
    
    if verbose:
        print(f"[VERBOSE] Running: {' '.join(ssh_cmd)}")
    
    try:
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            check=True
        )
        if verbose and result.stdout:
            print(f"[VERBOSE] Output: {result.stdout}")
        return result
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Command failed with exit code {e.returncode}")
        print(f"[ERROR] stderr: {e.stderr}")
        sys.exit(1)

def install_unit_file(host, user, verbose=False):
    """Install the ntp-sync-once.service unit file"""
    unit_content = """[Unit]
Description=One-shot NTP sync at boot
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/ntpd -gq

[Install]
WantedBy=multi-user.target
"""
    
    print(f"Installing ntp-sync-once.service on {host}...")
    
    # Create the unit file using echo to avoid file transfer
    escaped_content = unit_content.replace("$", "\\$").replace("`", "\\`").replace('"', '\\"')
    command = f'sudo bash -c "echo \\"{escaped_content}\\" > /etc/systemd/system/ntp-sync-once.service"'
    
    run_ssh_command(host, user, command, verbose)
    print("✓ Unit file installed")

def reload_systemd(host, user, verbose=False):
    """Reload systemd daemon"""
    print("Reloading systemd daemon...")
    run_ssh_command(host, user, "sudo systemctl daemon-reload", verbose)
    print("✓ Systemd daemon reloaded")

def enable_service(host, user, verbose=False):
    """Enable the service"""
    print("Enabling ntp-sync-once.service...")
    run_ssh_command(host, user, "sudo systemctl enable ntp-sync-once.service", verbose)
    print("✓ Service enabled")

def start_service(host, user, verbose=False):
    """Start the service"""
    print("Starting ntp-sync-once.service...")
    run_ssh_command(host, user, "sudo systemctl start ntp-sync-once.service", verbose)
    print("✓ Service started")

def verify_installation(host, user, verbose=False):
    """Verify the service installation"""
    print("\nVerifying installation...")
    try:
        result = run_ssh_command(host, user, "sudo systemctl status ntp-sync-once.service", verbose)
        print("✓ Service status verified")
        if verbose:
            print(result.stdout)
        return True
    except:
        print("⚠ Warning: Could not verify service status (this may be normal for one-shot services)")
        return False

def main():
    args = parse_args()

    if '.' not in args.host:
        old_host = args.host
        args.host = f"t-linux64-ms-{args.host}.test.releng.mdc1.mozilla.com"
        print(f"Expanding host {old_host} to full hostname {args.host}...")

    # Confirm with user unless --force is specified
    if not args.force:
        print(f"This will install and enable the ntp-sync-once.service on:")
        print(f"  Host: {args.host}")
        print(f"  User: {args.user}")
        confirm = input("\nAre you sure you want to proceed? (y/N) ")
        
        if confirm.lower() != "y":
            print("Operation cancelled.")
            sys.exit(0)

    print(f"\nProceeding with installation on {args.host}...\n")

    # Perform installation steps
    install_unit_file(args.host, args.user, args.verbose)
    reload_systemd(args.host, args.user, args.verbose)
    enable_service(args.host, args.user, args.verbose)
    start_service(args.host, args.user, args.verbose)
    
    # Verify installation
    verify_installation(args.host, args.user, args.verbose)

    # write to log file
    with open("install_clock_fix.log", "a") as log_file:
        # include datestamp and host
        log_file.write(f"{subprocess.getoutput('date')} - {args.host}\n")
        # log_file.write(f"Installed ntp-sync-once.service on {args.host} as {args.user}\n")

    print("\n\033[92mNTP sync service installed and started successfully.\033[0m")

if __name__ == "__main__":
    main()