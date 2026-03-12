#!/usr/bin/env python3

import argparse
import os
import re
import socket
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock

import requests

RUN_TIME_MAX_MINUTES = 120
IDLE_TIME_MAX_MINUTES = 30
PROVISIONER = "releng-hardware"
DEFAULT_HOSTNAME_PREFIXES = ["t-linux64-ms-"]
REPEAT_TIME = 1800
ROOT_URL = "https://firefox-ci-tc.services.mozilla.com/api/queue"
SKIP_HOSTS_FILE = "skip_hosts.txt"
WORK_DIR = Path("/tmp/keep_moonshot_carts_up")

print_lock = Lock()


def get_hostname(slot: int, prefixes: list[str]) -> str | None:
    slot_str = f"{slot:03d}"
    for prefix in prefixes:
        vnet = "win" if prefix.startswith("t-w") else ""
        for dc in [1, 2]:
            fqdn = f"{prefix}{slot_str}.{vnet}test.releng.mdc{dc}.mozilla.com"
            try:
                socket.getaddrinfo(fqdn, None)
                return fqdn
            except socket.gaierror:
                pass
    return None


def find_hosts(prefixes: list[str]) -> tuple[dict, dict, dict]:
    carts: dict[str, str] = {}
    hosts: dict[str, str] = {}
    hostnames: dict = {}

    for x in range(14):
        for n in range(1, 46):
            i = x * 45 + n
            slot = f"{i:03d}"
            chassis_num = x + 1
            host = get_hostname(i, prefixes)
            if not host:
                continue
            worker_id = host.split(".")[0]
            dc = host.split(".")[3]
            tags = f",cart={n},chassis={chassis_num},host={host},id={slot},workerId={worker_id},group={dc} id={slot}i,"
            carts[slot] = tags
            hosts[host] = tags
            hostnames[i] = host
            hostnames[slot] = host

    return carts, hosts, hostnames


def load_skip_hosts() -> set[str]:
    skip: set[str] = set()
    try:
        with open(SKIP_HOSTS_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    skip.add(line)
    except FileNotFoundError:
        pass
    return skip


def ping_host(fqdn: str) -> bool:
    result = subprocess.run(
        ["ping", "-q", "-c1", "-W5", fqdn],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def find_no_ping(hostnames: dict, skip_hosts: set[str]) -> list[tuple[str, str]]:
    log_file = WORK_DIR / "skip_hosts.log"
    old_log_file = WORK_DIR / "skip_hosts.log.old"
    try:
        os.rename(log_file, old_log_file)
    except OSError:
        pass

    no_ping: list[tuple[str, str]] = []
    lock = Lock()
    skip_log: list[str] = []

    def check(slot: str, host: str):
        if not ping_host(host):
            with lock:
                no_ping.append((slot, host))

    futures = []
    with ThreadPoolExecutor(max_workers=16) as executor:
        for i in range(1, 631):
            slot = f"{i:03d}"
            host = hostnames.get(i) or hostnames.get(slot)
            if not host:
                continue
            if slot in skip_hosts:
                skip_log.append(slot)
                continue
            futures.append(executor.submit(check, slot, host))
        for f in futures:
            f.result()

    with open(log_file, "w") as f:
        for slot in skip_log:
            f.write(f"{slot}\n")

    return no_ping


def current_hostlist(hostnames: dict, skip_hosts: set[str]) -> list[tuple[str, str]]:
    result = []
    for i in range(1, 631):
        slot = f"{i:03d}"
        host = hostnames.get(i) or hostnames.get(slot)
        if not host:
            continue
        if slot in skip_hosts:
            continue
        result.append((slot, host))
    return result


def is_older_than(timestamp_str: str | None, minutes: int) -> bool:
    if not timestamp_str:
        return False
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        return ts < cutoff
    except (ValueError, TypeError):
        return False


def tc_get(path: str) -> dict | None:
    try:
        response = requests.get(f"{ROOT_URL}{path}", timeout=30)
        if response.ok:
            return response.json()
    except requests.RequestException:
        pass
    return None


def get_worker_types(provisioner: str) -> list[str]:
    data = tc_get(f"/v1/provisioners/{provisioner}/worker-types/")
    if not data:
        return []
    return [wt["workerType"] for wt in data.get("workerTypes", [])]


def check_queues(provisioner: str, worker_types: list[str]) -> dict[str, int]:
    queued: dict[str, int] = {}
    for wt in worker_types:
        data = tc_get(f"/v1/pending/{provisioner}/{wt}")
        count = data.get("pendingTasks", 0) if data else 0
        key = f"{provisioner}_{wt}"
        queued[key] = count
        print(f"{key} {count}")
    return queued


def find_task_id_for_worker(
    hostname: str, dc_name: str, worker_types: list[str]
) -> tuple[str | None, str | None]:
    workers_file = WORK_DIR / "workers" / hostname
    if workers_file.exists():
        parts = workers_file.read_text().strip().split()
        if len(parts) >= 2:
            worker_id, worker_type = parts[0], parts[1]
            data = tc_get(
                f"/v1/provisioners/{PROVISIONER}/worker-types/{worker_type}/workers/{dc_name}/{worker_id}"
            )
            if data:
                recent = data.get("recentTasks", [])
                if recent:
                    return recent[-1].get("taskId"), worker_type

    try_first = ["gecko-t-linux-talos", "gecko_t_linux_2404_talos",
                 #, "gecko-t-win10-64-hw"  # no more windows
                 ]
    ordered = try_first + [wt for wt in worker_types if wt not in try_first]

    for worker_type in ordered:
        for worker_id in [hostname.lower(), hostname.upper()]:
            data = tc_get(
                f"/v1/provisioners/{PROVISIONER}/worker-types/{worker_type}/workers/{dc_name}/{worker_id}"
            )
            if data:
                recent = data.get("recentTasks", [])
                if recent:
                    task_id = recent[-1].get("taskId")
                    if task_id:
                        workers_file.parent.mkdir(exist_ok=True)
                        workers_file.write_text(f"{worker_id} {worker_type}\n")
                        return task_id, worker_type

    return None, None


def check_last_task(
    fqdn: str, worker_types: list[str], queued_tasks: dict[str, int]
) -> tuple[bool, bool]:
    """Check last task for a host. Returns (ok, not_found).

    ok=True means no action needed.
    not_found=True means worker was not found in Taskcluster.
    """
    with print_lock:
        print(fqdn, end="", flush=True)

    hostname = fqdn.split(".")[0]
    dc_parts = [p for p in fqdn.split(".") if p.startswith("mdc")]
    dc_name = dc_parts[0] if dc_parts else "mdc1"

    task_id, worker_type = find_task_id_for_worker(hostname, dc_name, worker_types)
    if not task_id:
        with print_lock:
            print(f" not found {hostname} ")
        return False, True

    with print_lock:
        print(f' "{task_id}":', end="", flush=True)

    data = tc_get(f"/v1/task/{task_id}/status")
    queue_key = f"{PROVISIONER}_{worker_type}"

    if not data:
        with print_lock:
            print()
        return True, False

    runs = data.get("status", {}).get("runs", [])
    if not runs:
        with print_lock:
            pending = queued_tasks.get(queue_key, 0)
            print(f" no tasks", end="")
            if pending > 0:
                print(f" [queue {worker_type}]: {pending}]")
                print(" N")
                return False, False
            print()
        return True, False

    last_run = runs[-1]
    state = last_run.get("state", "")
    started = last_run.get("started")
    ended = last_run.get("ended")

    with print_lock:
        print(f" {state} {started}->{ended}", end="", flush=True)

    if ended:
        if is_older_than(ended, IDLE_TIME_MAX_MINUTES):
            task_data = tc_get(f"/v1/task/{task_id}")
            task_name = task_data.get("metadata", {}).get("name", "") if task_data else ""
            pending = queued_tasks.get(queue_key, 0)
            with print_lock:
                print(f" [end>{IDLE_TIME_MAX_MINUTES}min]", end="")
                print(f' "{task_name}"', end="")
                print(f" [queue {worker_type}]: {pending}]", end="")
                if pending > 0:
                    print(" Q")
                    return False, False
                if state == "exception":
                    print(" X")
                    return False, False
                print()
            return True, False
    elif started and is_older_than(started, RUN_TIME_MAX_MINUTES):
        task_data = tc_get(f"/v1/task/{task_id}")
        task_name = task_data.get("metadata", {}).get("name", "") if task_data else ""
        with print_lock:
            print(f" [start>{RUN_TIME_MAX_MINUTES}min]", end="")
            print(f' "{task_name}"', end="")
            print(" S")
        return False, False

    with print_lock:
        print()
    return True, False


def hostname_to_cart(ids: list[str]) -> dict[str, list[str]]:
    """Translate host slot IDs to chassis FQDN -> node number list."""
    chassis_map: dict[str, list[str]] = {}
    for id_str in ids:
        m = re.search(r'\d+', id_str)
        if not m:
            continue
        i = int(m.group().lstrip("0") or "0")

        if i > 630:
            c = ((i - 1) - 30) // 45 + 2
            n = ((i - 1) - 630) % 45 + 1
        elif i > 615:
            c = ((i - 1) - 15) // 45 + 1 - 13
            n = ((i - 1) - 615) % 45 + 1 + 30
        elif i > 300:
            c = ((i - 1) + 15) // 45 + 1
            n = ((i - 1) + 15) % 45 + 1
        else:
            c = (i - 1) // 45 + 1
            n = (i - 1) % 45 + 1

        dc = "mdc2" if c > 7 else "mdc1"
        chassis_fqdn = f"moon-chassis-{c}.inband.releng.{dc}.mozilla.com"
        chassis_map.setdefault(chassis_fqdn, []).append(str(n))

    return chassis_map




def check_chassis_power(chassis: str, password: str, ilo_user: str) -> list[str]:
    result = subprocess.run(
        ["./check_power.sh", f"{ilo_user}@{chassis}"],
        input=f"{password}\n",
        capture_output=True,
        text=True,
    )
    items = re.findall(r'c[0-9]+n[0-9]+|Power State: (?:On|Off)', result.stdout)
    off_carts = []
    i = 0
    while i < len(items) - 1:
        if items[i].startswith("c"):
            if items[i + 1] == "Power State: Off":
                m = re.match(r'c(\d+)', items[i])
                if m:
                    off_carts.append(m.group(1))
            i += 2
        else:
            i += 1
    return off_carts


def reboot_workers(missing: list[str], password: str, ilo_user: str):
    chassis_map = hostname_to_cart(missing)
    procs = []
    for chassis_fqdn, nodes in chassis_map.items():
        nodes_str = ",".join(nodes)
        log_path = WORK_DIR / f"reboot.{chassis_fqdn}.{datetime.now().strftime('%H')}.log"
        print(f": {chassis_fqdn} {nodes_str}")
        log_f = open(log_path, "w")
        proc = subprocess.Popen(
            [
                "./up_carts_on_chassis.exp",
                "--hostname", f"{ilo_user}@{chassis_fqdn}",
                "--nodes", nodes_str,
            ],
            stdin=subprocess.PIPE,
            stdout=log_f,
            stderr=subprocess.STDOUT,
        )
        proc.stdin.write(f"{password}\n".encode())
        proc.stdin.close()
        procs.append((proc, log_f))
    for proc, log_f in procs:
        proc.wait()
        log_f.close()


def load_ilo_credentials() -> tuple[str, str]:
    config_path = Path.home() / ".moonshot.toml"
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
        ilo = config.get("ilo", {})
        username = ilo.get("username", "Administrator")
        password = ilo["password"]
        return username, password
    except FileNotFoundError:
        print(f"[ERROR] Credentials file not found: {config_path}")
        print("Please create ~/.moonshot.toml with:\n  [ilo]\n  username = \"Administrator\"\n  password = \"your_password\"")
        sys.exit(1)
    except KeyError:
        print(f"[ERROR] Missing 'password' under [ilo] in {config_path}")
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor and auto-remediate Moonshot hardware workers"
    )
    parser.add_argument(
        "--prefixes",
        default=" ".join(DEFAULT_HOSTNAME_PREFIXES),
        help="Space-separated hostname prefixes (default: %(default)r)",
    )
    parser.add_argument(
        "--repeat-time",
        type=int,
        default=REPEAT_TIME,
        metavar="SECONDS",
        help="Seconds between iterations (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check and report without rebooting any workers",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    hostname_prefixes = args.prefixes.split()
    repeat_time = args.repeat_time

    ilo_user, password = load_ilo_credentials()

    if args.dry_run:
        print("[DRY RUN] No reboots will be performed.")

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    (WORK_DIR / "workers").mkdir(exist_ok=True)

    print("Discovering hosts...")
    carts, hosts, hostnames = find_hosts(hostname_prefixes)

    while True:
        print(datetime.now())
        next_time = time.time() + repeat_time

        skip_hosts = load_skip_hosts()
        alllist = current_hostlist(hostnames, skip_hosts)
        noping_log = WORK_DIR / "nopinglist.log"

        nopinglist = find_no_ping(hostnames, skip_hosts)
        with open(noping_log, "w") as f:
            for slot, host in nopinglist:
                f.write(f"{slot} {host}\n")

        noping_ids = [slot for slot, _ in nopinglist]
        print(f"no ping response [{len(nopinglist)}]: {' '.join(f'{s} {h}' for s, h in nopinglist)}")

        worker_types = get_worker_types(PROVISIONER)
        queued_tasks = check_queues(PROVISIONER, worker_types)

        for d in [WORK_DIR / "reboot_workers", WORK_DIR / "not_reboot_workers"]:
            d.mkdir(parents=True, exist_ok=True)
            for fname in d.iterdir():
                fname.unlink()

        noping_set = set(noping_ids)
        skip_set = set(skip_hosts)
        not_found_hosts: list[str] = []
        results_lock = Lock()

        def check_host(slot_host: tuple[str, str]):
            slot, fqdn = slot_host
            ok, not_found = check_last_task(fqdn, worker_types, queued_tasks)
            if not_found:
                with results_lock:
                    not_found_hosts.append(fqdn.split(".")[0])
            if not ok:
                if slot not in skip_set:
                    if slot in noping_set:
                        (WORK_DIR / "reboot_workers" / slot).touch()
                    else:
                        with print_lock:
                            print(f"Ping was okay from {slot} {fqdn}")
                else:
                    (WORK_DIR / "not_reboot_workers" / slot).touch()

        with ThreadPoolExecutor(max_workers=20) as executor:
            list(executor.map(check_host, alllist))

        print(f"did not find [{len(not_found_hosts)}]: {' '.join(not_found_hosts)}")

        missing = [p.name for p in (WORK_DIR / "reboot_workers").iterdir()]
        not_missing = [p.name for p in (WORK_DIR / "not_reboot_workers").iterdir()]

        print(f"now will reboot [{len(missing)}]: {' '.join(missing)}")

        print(f"will not reboot [{len(not_missing)}]: {' '.join(not_missing)}")

        if missing:
            if args.dry_run:
                print("[DRY RUN] Would reboot:", " ".join(missing))
            else:
                print("NOW rebooting ...")
                reboot_workers(missing, password, ilo_user)

        print("now check if down...")
        for i in range(1, 15):
            chassis = None
            for dc in [1, 2]:
                candidate = f"moon-chassis-{i}.inband.releng.mdc{dc}.mozilla.com"
                try:
                    socket.getaddrinfo(candidate, None)
                    chassis = candidate
                    break
                except socket.gaierror:
                    pass
            if chassis:
                check_chassis_power(chassis, password, ilo_user)
            break  # original script only checks first chassis found

        nopinglist2 = find_no_ping(hostnames, skip_hosts)
        print(f"no ping response: {' '.join(f'{s} {h}' for s, h in nopinglist2)}")

        print(datetime.now())

        while time.time() < next_time:
            print(".", end="", flush=True)
            time.sleep(30)
        print()


if __name__ == "__main__":
    requests.packages.urllib3.disable_warnings()
    main()
