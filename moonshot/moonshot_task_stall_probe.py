"""Target-side companion to Medic's worker process inspection."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


SIGNATURE_KEYS = ("boot_id", "pid", "start_ticks", "task_id", "resolved_at_us", "reclaimed_at_us")
TASK_ID = r"[A-Za-z0-9_-]{22}"
LOG_DATE = r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} "
SEQUENCE = (
    re.compile(LOG_DATE + rf"Reclaiming task ({TASK_ID})\.\.\.$"),
    re.compile(LOG_DATE + rf"Resolving task ({TASK_ID}) \.\.\.$"),
    re.compile(LOG_DATE + rf"Successfully reclaimed task ({TASK_ID})$"),
)
MIN_QUIET_US = 300_000_000
SEQUENCE_WINDOW_US = 10_000_000
OPERATOR_HOLD = "/home/cltbld/operator_hold"
REBOOT_MARKER = "/run/moonshot-medic-task-stall-reboot-attempted"


def read_worker_journal(boot_id):
    result = subprocess.run(
        ["journalctl", "-b", "--quiet", "--no-pager", "-n", "128", "-o", "json",
         "--output-fields=MESSAGE,_PID,SYSLOG_IDENTIFIER,_BOOT_ID,__MONOTONIC_TIMESTAMP,__REALTIME_TIMESTAMP",
         "-t", "generic-worker", "-t", "run-start-worker"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    if len(result.stdout) > 262144:
        return []
    entries = [json.loads(line) for line in result.stdout.splitlines()]
    return sorted((entry for entry in entries if entry["_BOOT_ID"] == boot_id.replace("-", "")),
                  key=lambda entry: int(entry["__MONOTONIC_TIMESTAMP"]))


def match_sequence(entries, *, started_us, now_us):
    worker = [entry for entry in entries if entry.get("SYSLOG_IDENTIFIER") == "generic-worker"
              and any(pattern.fullmatch(entry["MESSAGE"]) for pattern in SEQUENCE)]
    if len(worker) < 3:
        return {}
    recent = worker[-3:]
    matches = [pattern.fullmatch(entry["MESSAGE"]) for pattern, entry in zip(SEQUENCE, recent)]
    if not all(matches) or len({match[1] for match in matches}) != 1:
        return {}
    logger_pids = {entry.get("_PID") for entry in recent}
    if None in logger_pids or len(logger_pids) != 1:
        return {}
    reclaim, resolve, success = [int(entry["__MONOTONIC_TIMESTAMP"]) for entry in recent]
    if (not started_us <= reclaim < resolve < success <= now_us - MIN_QUIET_US
            or success - reclaim > SEQUENCE_WINDOW_US):
        return {}
    if any(int(entry["__MONOTONIC_TIMESTAMP"]) > success or (
        int(entry["__MONOTONIC_TIMESTAMP"]) >= reclaim
        and re.search(r"taskFinish|workerReady|worker_exited", entry["MESSAGE"])
    ) for entry in entries):
        return {}
    return {"task_id": matches[0][1], "resolved_at_us": int(recent[1]["__REALTIME_TIMESTAMP"]),
            "reclaimed_at_us": int(recent[2]["__REALTIME_TIMESTAMP"])}


def worker_process(collect, grace_seconds):
    current = collect()
    if (current["operator_hold"] or os.path.lexists(OPERATOR_HOLD) or current["unverified_pids"]
            or current["uptime_seconds"] < grace_seconds or len(current["processes"]) != 1):
        return {}
    process, = current["processes"]
    if process["state"] != "S":
        return {}
    parent = Path(f"/proc/{process['ppid']}/comm")
    root = Path(f"/proc/{process['pid']}")
    if parent.read_text().strip() != "start-worker":
        return {}
    threads = list((root / "task").iterdir())
    if not threads or any((thread / "children").read_text().strip() for thread in threads):
        return {}
    stat = (root / "stat").read_text().rsplit(")", 1)[1].split()
    if stat[0] != "S" or int(stat[1]) != process["ppid"]:
        return {}
    return {"boot_id": current["boot_id"], "pid": process["pid"], "start_ticks": int(stat[19])}


def detect(collect, grace_seconds):
    os_release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                      if "=" in line and not line.startswith("#"))
    if (os_release.get("ID", "").strip('"'), os_release.get("VERSION_ID", "").strip('"')) != ("ubuntu", "24.04"):
        return {}
    worker = worker_process(collect, grace_seconds)
    if not worker:
        return {}
    sequence = match_sequence(
        read_worker_journal(worker["boot_id"]),
        started_us=worker["start_ticks"] * 1_000_000 // os.sysconf("SC_CLK_TCK"),
        now_us=time.monotonic_ns() // 1000,
    )
    return {**worker, **sequence} if sequence else {}


def main(collect):
    mode, grace, signature = sys.argv[1:]
    expected = json.loads(signature)
    try:
        evidence = detect(collect, int(grace))
        if mode == "reboot" and evidence == expected:
            # Guard against a process or operator hold changing during the journal read.
            if worker_process(collect, int(grace)) != {key: expected[key] for key in SIGNATURE_KEYS[:3]}:
                evidence = {}
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        evidence = {}
    if mode != "reboot":
        print(json.dumps(evidence))
        return 0
    if not evidence or evidence != expected:
        return 3
    try:
        os.close(os.open(REBOOT_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        return 3
    subprocess.run(["systemctl", "--no-block", "reboot"], check=True, timeout=5)
    return 0
