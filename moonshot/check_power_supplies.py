#!/usr/bin/env python3
"""Report Moonshot chassis power-supply status through the iLO SSH CLI."""

from __future__ import annotations

import argparse
import re
import sys
import time

import paramiko

DEFAULT_CHASSIS = range(1, 8)
ILO_COMMAND = "show chassis powersupply"
ILO_PROMPT = "hpiLO->"
CHASSIS_NAME = re.compile(r"^moon-chassis-(\d+)$")


def chassis_host(value: str) -> str:
    """Expand a chassis number or short hostname to its mdc1 iLO FQDN."""
    if value.isdigit():
        number = int(value)
        if number not in DEFAULT_CHASSIS:
            raise argparse.ArgumentTypeError("chassis number must be between 1 and 7")
        return f"moon-chassis-{number}.inband.releng.mdc1.mozilla.com"

    match = CHASSIS_NAME.fullmatch(value)
    if match:
        return chassis_host(match.group(1))
    if "." in value:
        return value
    raise argparse.ArgumentTypeError(
        "host must be a chassis number, moon-chassis-N, or a chassis FQDN"
    )


def check_power_supply(host: str, user: str, timeout: int) -> str:
    """Connect with iLO-compatible SSH algorithms and return command output."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            host,
            username=user,
            timeout=10,
            banner_timeout=10,
            auth_timeout=timeout,
            allow_agent=True,
            look_for_keys=True,
            disabled_algorithms={"pubkeys": ["rsa-sha2-512", "rsa-sha2-256"]},
        )
        channel = client.invoke_shell()
        _read_until(channel, ILO_PROMPT, timeout)
        channel.send(f"{ILO_COMMAND}\n")
        return _read_until(channel, ILO_PROMPT, timeout).removesuffix(ILO_PROMPT).strip()
    except (OSError, paramiko.SSHException) as error:
        raise RuntimeError(str(error)) from error
    finally:
        client.close()


def _read_until(channel: paramiko.Channel, marker: str, timeout: int) -> str:
    """Read an interactive iLO channel until *marker* arrives or it times out."""
    deadline = time.monotonic() + timeout
    output = ""
    while marker not in output:
        if channel.recv_ready():
            output += channel.recv(65535).decode(errors="replace")
            continue
        if channel.closed:
            raise RuntimeError(output.strip() or "connection closed before iLO prompt")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"timed out waiting for {marker}")
        time.sleep(0.1)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        action="append",
        type=chassis_host,
        metavar="CHASSIS",
        help="check one chassis (number, moon-chassis-N, or FQDN); may be repeated",
    )
    parser.add_argument("--user", default="relops", help="iLO SSH user")
    parser.add_argument("--timeout", type=int, default=30, help="per-operation timeout in seconds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    hosts = args.host or [chassis_host(str(number)) for number in DEFAULT_CHASSIS]
    failures = 0

    for host in hosts:
        print(f"\n===== {host} =====")
        try:
            print(check_power_supply(host, args.user, args.timeout))
        except RuntimeError as error:
            print(f"error: {error}", file=sys.stderr)
            failures += 1

    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
