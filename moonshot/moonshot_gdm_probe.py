"""Ubuntu Moonshot GDM checks executed locally on the target over SSH."""

import configparser
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


SERVICE = "gdm.service"
WORKER_NAMES = {"generic-worker", "start-worker", "run-start-worke"}
DESKTOP_NAMES = {"Xorg", "Xwayland", "gnome-shell", "gnome-session", "gnome-session-b"}
PAM_OPENED = r"pam_unix\(gdm-autologin:session\): session opened for user cltbld([ (]|$)"
WORKER_READY = r'"eventType"[[:space:]]*:[[:space:]]*"workerReady"'
MIN_STALL_SECONDS = 300
SIGNATURE_KEYS = ("boot_id", "gdm_invocation", "pid", "start_ticks")
RESTART_MARKER = "/run/moonshot-medic-gdm-restart-attempted"


def read_command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=5).stdout


def properties(*args):
    return dict(line.split("=", 1) for line in read_command(*args).splitlines() if "=" in line)


def process_table(root=Path("/proc")):
    processes = {}
    for entry in root.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            _, stat = (entry / "stat").read_text().rsplit(")", 1)
            fields = stat.split()
            processes[int(entry.name)] = {
                "name": (entry / "comm").read_text().strip(),
                "state": fields[0], "parent": int(fields[1]), "start_ticks": int(fields[19]),
            }
        except (FileNotFoundError, ProcessLookupError):
            continue
    return processes


def descends_from(pid, ancestor, processes):
    seen = set()
    while pid in processes and pid not in seen:
        if pid == ancestor:
            return True
        seen.add(pid)
        pid = processes[pid]["parent"]
    return False


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def gdm_service():
    return properties("systemctl", "show", SERVICE, "-p", "MainPID", "-p", "InvocationID", "-p", "ActiveState")


def journal(*args):
    return read_command("journalctl", "-b", "--quiet", "--no-pager", "-n", "1", "-o", "json", *args).strip()


def detect(grace_seconds):
    os_release = properties("cat", "/etc/os-release")
    if (os_release.get("ID", "").strip('"'), os_release.get("VERSION_ID", "").strip('"')) != ("ubuntu", "24.04"):
        return {}
    if float(Path("/proc/uptime").read_text().split()[0]) < grace_seconds:
        return {}
    if os.path.lexists("/home/cltbld/operator_hold"):
        return {}
    config = configparser.ConfigParser(interpolation=None, strict=False)
    with Path("/etc/gdm3/custom.conf").open() as source:
        config.read_file(source)
    if (not config.getboolean("daemon", "AutomaticLoginEnable", fallback=False)
            or config.get("daemon", "AutomaticLogin", fallback="") != "cltbld"):
        return {}
    if properties("systemctl", "show", "run-puppet.service", "-p", "ActiveState").get("ActiveState") != "inactive":
        return {}
    read_command("/usr/local/bin/puppet-run-marker", "check")
    gdm = gdm_service()
    main_pid = int(gdm["MainPID"])
    invocation = gdm["InvocationID"]
    if main_pid <= 1 or gdm["ActiveState"] != "active" or not re.fullmatch("[0-9a-f]{32}", invocation):
        return {}
    for line in read_command("loginctl", "list-sessions", "--no-legend", "--no-pager").splitlines():
        if not line.strip():
            continue
        session = properties("loginctl", "show-session", line.split()[0], "-p", "Name", "-p", "Type", "-p", "Service")
        if session["Type"] in ("x11", "wayland") and (
            session["Name"], session["Service"], session["Type"]
        ) != ("cltbld", "gdm-autologin", "x11"):
            return {}
    if journal("-t", "run-start-worker", "-t", "generic-worker"):
        return {}
    login = journal("-u", SERVICE, f"--grep={PAM_OPENED}")
    if not login:
        return {}
    login_us = int(json.loads(login)["__MONOTONIC_TIMESTAMP"])
    login_age = (time.monotonic_ns() // 1000 - login_us) / 1_000_000
    ticks_per_second = os.sysconf("SC_CLK_TCK")
    processes = process_table()
    if (main_pid not in processes or login_us * ticks_per_second < processes[main_pid]["start_ticks"] * 1_000_000
            or login_age < MIN_STALL_SECONDS
            or any(p["name"] in WORKER_NAMES | DESKTOP_NAMES for p in processes.values())):
        return {}
    cutoff = (time.monotonic() - MIN_STALL_SECONDS) * ticks_per_second
    for pid, process in sorted(processes.items(), reverse=True):
        if (process["name"] != "gdm-session-wor" or process["state"] != "S"
                or process["start_ticks"] > cutoff or not descends_from(pid, main_pid, processes)):
            continue
        try:
            with Path(f"/proc/{pid}/cmdline").open("rb") as source:
                title = source.read(128).split(b"\0", 1)[0]
            wchan = Path(f"/proc/{pid}/wchan").read_text().strip()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if title != b"gdm-session-worker [pam/gdm-autologin]" or wchan != "futex_wait_queue":
            continue
        tree = [{"pid": p, **entry} for p, entry in processes.items()
                if descends_from(p, main_pid, processes)]
        return {"boot_id": boot_id(), "gdm_invocation": invocation, "pid": pid,
                "start_ticks": process["start_ticks"], "wait_channel": wchan,
                "pam_opened_cltbld_monotonic_us": login_us, "gdm_process_tree": tree[:32]}
    return {}


def verify(expected):
    gdm = gdm_service()
    if (boot_id() != expected["boot_id"] or gdm.get("ActiveState") != "active"
            or not re.fullmatch("[0-9a-f]{32}", gdm.get("InvocationID", ""))
            or gdm["InvocationID"] == expected["gdm_invocation"]):
        return {}
    ready = journal("-t", "generic-worker", f"--grep={WORKER_READY}")
    if not ready or not any(
        p["name"] == "generic-worker" and p["state"] not in {"Z", "X", "x"} for p in process_table().values()
    ):
        return {}
    return {"boot_id": boot_id(), "gdm_invocation": gdm["InvocationID"],
            "worker_ready_us": int(json.loads(ready)["__REALTIME_TIMESTAMP"])}


def main():
    mode, grace, signature = sys.argv[1:]
    expected = json.loads(signature)
    try:
        evidence = verify(expected) if mode == "verify" else detect(int(grace))
    except (OSError, ValueError, KeyError, IndexError, configparser.Error, subprocess.SubprocessError):
        evidence = {}
    if mode == "restart":
        if (not evidence or any(evidence[key] != expected[key] for key in SIGNATURE_KEYS)
                or gdm_service().get("InvocationID") != expected["gdm_invocation"]
                or os.path.lexists("/home/cltbld/operator_hold")):
            return 3
        # /run is boot-scoped. Reserve the attempt atomically across Medic processes.
        try:
            os.close(os.open(RESTART_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            return 3
        read_command("systemctl", "--no-block", "restart", SERVICE)
    else:
        print(json.dumps(evidence))
    return 0


if __name__ == "__main__":
    sys.exit(main())
