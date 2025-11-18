#!/usr/bin/env python3
"""Python rewrite of ms_javaws_runner bash script.

Features:
- Checks for existing running Java Web Start process matching a pattern (default 'jweblauncher').
- Launches `javaws` with a provided JNLP file.
- Sleeps a configurable amount to allow process startup.
- Displays a spinner while waiting for the process to exit.
- Removes the JNLP file on completion.
- Supports a dry-run mode for testing without side effects.
"""
from __future__ import annotations
import argparse
import os
import sys
import time
import subprocess
from typing import Sequence


def process_running(pattern: str) -> bool:
    """Return True if any process matches pattern using `pgrep -f`.
    Falls back to manual `ps` scan if pgrep unavailable.
    """
    try:
        proc = subprocess.run(["pgrep", "-f", pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.returncode == 0
    except FileNotFoundError:
        # Fallback: parse ps output
        try:
            ps = subprocess.check_output(["ps", "-axo", "pid,args"], text=True, errors="ignore")
        except Exception:
            return False
        pattern_lower = pattern.lower()
        for line in ps.splitlines():
            if pattern_lower in line.lower():
                return True
        return False


def launch_javaws(jnlp_path: str, dry_run: bool) -> int:
    """Launch javaws with the given JNLP file. Returns the exit code of the javaws process.
    In dry-run mode, just prints the command and returns 0.
    """
    cmd = ["javaws", jnlp_path]
    if dry_run:
        print(f"[dry-run] Would execute: {' '.join(cmd)}")
        return 0
    try:
        proc = subprocess.Popen(cmd)
    except FileNotFoundError:
        print("Error: 'javaws' command not found in PATH.", file=sys.stderr)
        return 127
    except Exception as e:
        print(f"Error launching javaws: {e}", file=sys.stderr)
        return 1
    return proc.pid  # Return PID to indicate success; we don't wait here.


def wait_for_process_exit(pattern: str, spinner_interval: float = 0.1) -> None:
    """Wait until no process matches the pattern, showing a spinner."""
    spinner = ["-", "\\", "|", "/"]
    idx = 0
    sys.stdout.write(f"[waiting] {spinner[0]}")
    sys.stdout.flush()
    while process_running(pattern):
        sys.stdout.write("\b" + spinner[idx])
        sys.stdout.flush()
        time.sleep(spinner_interval)
        idx = (idx + 1) % len(spinner)
    sys.stdout.write("\b ")  # clear spinner char
    sys.stdout.write("\n")
    sys.stdout.flush()


def remove_file(path: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] Would remove: {path}")
        return
    if not os.path.exists(path):
        print(f"Note: File already absent: {path}")
        return
    try:
        os.remove(path)
        print(f"Removed file: {path}")
    except Exception as e:
        print(f"Error removing file '{path}': {e}", file=sys.stderr)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a JNLP via javaws and wait for its process to exit.")
    parser.add_argument("--jnlp", default="/Users/aerickson/Downloads/moonshot-jirc.jnlp", help="Path to JNLP file to launch (default: %(default)s)")
    parser.add_argument("--pattern", default="jweblauncher", help="Process name/pattern to monitor (default: %(default)s)")
    parser.add_argument("--sleep", type=int, default=35, help="Seconds to sleep allowing process to start (default: %(default)s)")
    parser.add_argument("--spinner-interval", type=float, default=0.1, help="Seconds per spinner tick (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="Do not execute or remove anything; just print actions")
    parser.add_argument("--no-remove", action="store_true", help="Skip removing the JNLP file at end")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if JNLP file missing or javaws not found")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    # Pre-check existing process
    if process_running(args.pattern):
        print(f"Error: A process matching '{args.pattern}' is already running. Close it before running this script.")
        return 1

    # Validate JNLP file presence
    if not os.path.exists(args.jnlp):
        msg = f"Error: JNLP file not found: {args.jnlp}"
        if args.strict:
            print(msg, file=sys.stderr)
            return 2
        print(msg + " (continuing anyway)")

    # Launch javaws
    pid_or_code = launch_javaws(args.jnlp, args.dry_run)
    if isinstance(pid_or_code, int) and pid_or_code in (1, 127):
        # Error codes from launch function
        return pid_or_code

    print(f"Sleeping {args.sleep} seconds to allow '{args.pattern}' to start...")
    time.sleep(args.sleep)

    print(f"Waiting for process matching '{args.pattern}' to exit...")
    wait_for_process_exit(args.pattern, args.spinner_interval)

    print(f"Process '{args.pattern}' has exited.")

    if not args.no_remove:
        print("Removing JNLP file...")
        remove_file(args.jnlp, args.dry_run)
    else:
        print("Skipping JNLP file removal per --no-remove.")

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
