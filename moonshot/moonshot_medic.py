#!/usr/bin/env python3
"""Automated triage for hung t-linux64-ms-* Moonshot cartridges: resets via iLO, collects hang diagnostics, and tracks repeat offenders.

Usage:
  moonshot_medic.py [--auto] [--legacy] [--once] [--no-reset] [--ignore-recency] [HOST ...]

  --auto                    Fetch bad-host list from fleetroll instead of reading argv/stdin (requires --confirm).
  --legacy                  Force plain terminal output in auto mode.
  --once                    Run one auto-mode iteration, then exit.
  --max-fleet-reset-pct     Maximum auto-selected percentage of configured fleet (default: 20).
  --no-reset                Skip iLO resets and SSH reboots.
  --freshness-requirement   Max acceptable age of fleetroll data in minutes (default: loop-interval).
  --ignore-recency          Process hosts even if collected within the last RECENCY_MINUTES minutes.
  HOST ...                  Short (ms025) or FQDN. If omitted and not --auto, reads stdin.
"""

import argparse
import datetime
import json
import math
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from collections.abc import Callable
from contextlib import contextmanager
from html import escape
from http.client import HTTPException
from pathlib import Path
from urllib.request import urlopen

from moonshot_gdm_probe import SIGNATURE_KEYS
from moonshot_oom_probe import SIGNATURE_KEYS as OOM_SIGNATURE_KEYS
from moonshot_task_stall_probe import SIGNATURE_KEYS as TASK_STALL_SIGNATURE_KEYS, TASK_ID as TASK_STALL_TASK_ID

FLEETROLL_DIR = Path.home() / "git/fleetroll_mvp"
SCRIPT_DIR = Path(__file__).parent.resolve()
RESET_DIR = SCRIPT_DIR
RESULTS_BASE = SCRIPT_DIR / "moonshot_debugging_results"
HANG_SCRIPT = SCRIPT_DIR / "moonshot_hang_report.py"
CLOCK_FIX_SCRIPT = SCRIPT_DIR / "install_clock_fix.py"
CLOCK_SKEW_SECONDS = 5 * 60
RECENCY_MINUTES = 60
AUTO_BATCH_SIZE = 10
SSH_REBOOT_WAIT_SECONDS = 180
GDM_VERIFY_WAIT_SECONDS = 60
SCRIPT_VOICE_NAME = "Moonshot Medic"
MEDIC_LOGO = (
    " ▄▀▄▀▄ ▄▀▄ ▄▀▄ ▄▀█ █▀ █░░ ▄▀▄ ▄█▄    ▄▀▄▀▄ ██▀ ▄▄█ ▀ ▄▀▀",
    " █░▀░█ ▀▄▀ ▀▄▀ █░█ ▄█ █▀█ ▀▄▀ ░█▄    █░▀░█ █▄▄ █▄█ █ ▀▄▄",
)
STATE_FILE = RESULTS_BASE / "state.json"
OVERVIEW_FILE = RESULTS_BASE / "OVERVIEW.md"
OVERVIEW_HTML_FILE = RESULTS_BASE / "OVERVIEW.html"
REPORT_THEME_FILE = SCRIPT_DIR / "moonshot_medic_report.css"
FAVICON_FILE = RESULTS_BASE / "favicon.svg"
SKIP_THRESHOLD_CONSECUTIVE = 3
SKIP_DURATION_HOURS = 6
FRESHNESS_MIN_PCT = 65
DEFAULT_MAX_FLEET_RESET_PCT = 20.0
FLEETROLL_FRESHNESS_SCHEMA_VERSION = 2
FLEETROLL_FRESHNESS_REQUIRED_SOURCES = {"host", "tc"}
MOONSHOT_HOST_RE = re.compile(r'^t-linux64-ms-\d+\.test\.releng\.mdc[12]\.mozilla\.com$', re.IGNORECASE)
MOONSHOT_OBSERVED_FILTER = "host~t-linux64-ms os=L sort:host:asc"
RECOVERY_FILTER_FILE = "configs/filters/moonshots-hung.yaml"
UNHEALTHY_MOONSHOTS_FILTER = "host~t-linux64-ms healthy=n tc_act>1h note= sort:host:asc"
INACTIVE_MOONSHOTS_FILTER = "host~t-linux64-ms tc_act>4h note= sort:host:asc"
WORKER_EVIDENCE_SCHEMA_VERSION = 3
HUNG_SECONDS = 4 * 60 * 60
DEFAULT_MAX_IDENTITY_AGE_HOURS = 7 * 24
WORKER_IDENTITY_FIELDS = ("provisioner", "worker_type", "worker_group", "worker_id")

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=30",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=4",
]

# --- interrupt handling ---

_interrupt_count = 0
_rescan_requested: bool | None = None


def _sigint_handler(sig, frame):
    global _interrupt_count
    _interrupt_count += 1
    if _interrupt_count == 1:
        print("\n[Ctrl-C] Will exit when current work finishes. Press Ctrl-C again to exit immediately.",
              file=sys.stderr)
    else:
        print("\n[Ctrl-C] Exiting immediately.", file=sys.stderr)
        sys.exit(130)


def _rescan_signal_handler(sig, frame):
    global _rescan_requested
    if _rescan_requested is False:
        _rescan_requested = True


# --- color / logging ---

_use_color = sys.stdout.isatty()
_log_fh = None
_log_recovery_label = "Check 1"
_tui_status_path: Path | None = None
_tui_status: dict = {}


def _publish_tui(**changes) -> None:
    global _tui_status_path
    if _tui_status_path is None:
        return
    _tui_status.update(changes)
    try:
        pending = _tui_status_path.with_suffix(".tmp")
        pending.write_text(json.dumps(_tui_status), encoding="utf-8")
        pending.replace(_tui_status_path)
    except OSError as exc:
        _tui_status_path = None
        warn(f"Dashboard status updates unavailable: {exc}")


def _tui_phase(phase: str, detail: str = "", *, next_run_at: float | None = None) -> None:
    _publish_tui(phase=phase, detail=detail, next_run_at=next_run_at)


def wait_for_next_scan(loop_interval: int) -> None:
    global _rescan_requested
    _rescan_requested = False
    try:
        _tui_phase("Waiting for next scan", next_run_at=time.time() + loop_interval * 60)
        info(f"Next run in {loop_interval} minute{'s' if loop_interval != 1 else ''}. Press Ctrl-C to stop.")
        for _ in range(loop_interval * 60):
            if _interrupt_count or _rescan_requested:
                break
            time.sleep(1)
        if _rescan_requested and not _interrupt_count:
            _tui_phase("Starting requested scan")
            info("Rescan requested. Starting the next scan.")
    finally:
        _rescan_requested = None


def _tui_result(key: str, *, title: str = "", summary: str = "",
                hosts: dict[str, dict[str, str]] | None = None, alert: bool = False,
                query: str = "", action: str = "") -> None:
    if _tui_status_path is not None:
        _publish_tui(**{key: {
            "title": title,
            "summary": summary,
            "hosts": hosts or {},
            "alert": alert,
            "updated_at": datetime.datetime.now().strftime("%H:%M:%S"),
            "query": query,
            "action": action,
        }})


def _tui_activity(message: str, *, host: str = "", tone: str = "") -> None:
    if _tui_status_path is None:
        return
    message = " ".join(message.split()).replace(f"{SCRIPT_DIR}/", "")
    event = {"time": datetime.datetime.now().isoformat(sep=" ", timespec="seconds"), "host": host,
             "message": message, "tone": tone}
    _publish_tui(activity=[*_tui_status.get("activity", [])[-199:], event])


def _tui_host_outcome(check: str, host: str, state: str, reason: str) -> None:
    if _tui_status_path is None:
        return
    key = "recovery_outcomes" if check == "Check 1" else "diagnosis_outcomes"
    outcomes = _tui_status.get(key, {})
    _publish_tui(**{key: {**outcomes, short_label(host): {"state": state, "reason": reason}}})


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _use_color else text


def _emit(line: str, *, stderr: bool = False) -> None:
    print(line, file=sys.stderr if stderr else sys.stdout)
    _append_run_log(line)


def _append_run_log(line: str) -> None:
    if _log_fh:
        print(re.sub(r'\033\[[0-9;]*m', '', line), file=_log_fh)


@contextmanager
def scan_log(mode: str, *, recovery_label: str = "Check 1"):
    global _log_fh, _log_recovery_label
    started = datetime.datetime.now(datetime.UTC)
    run_dir = RESULTS_BASE / started.strftime("%Y%m%d")
    run_dir.mkdir(parents=True, exist_ok=True)
    _publish_tui(phase="Starting scan", detail="", next_run_at=None,
                 scan_started_at=started.astimezone().strftime("%H:%M"),
                 recovery=None, fleet_health=None, diagnosis=None,
                 recovery_outcomes={}, diagnosis_outcomes={})
    previous = _log_fh, _log_recovery_label
    with (run_dir / "run.log").open("a", encoding="utf-8", buffering=1) as log:
        _log_fh, _log_recovery_label = log, recovery_label
        try:
            _append_run_log(f"Scan started at {started.isoformat()} | {mode}")
            info(f"Log: {run_dir / 'run.log'}")
            yield run_dir
        finally:
            try:
                _append_run_log(f"Scan ended at {datetime.datetime.now(datetime.UTC).isoformat()}\n")
            finally:
                _log_fh, _log_recovery_label = previous


def log_intervention(check: str, host: str, action: str, outcome: str, reason: str,
                     evidence: str | Path = "") -> None:
    if _tui_status_path is not None:
        tone = {"failed": "error", "deferred": "attention", "unverified": "attention",
                "verified": "success", "saved": "success", "recovered": "success",
                "rebooted": "success"}.get(outcome, "")
        _tui_activity(f"{action} {outcome} · {reason}", host=short_label(host), tone=tone)
        if check == "Check 1":
            if action == "iLO reset" and outcome == "verified":
                _tui_host_outcome(check, host, "ssh_restored", reason)
            elif action == "Clock fix" and outcome == "verified":
                _tui_host_outcome(check, host, "clock_repaired", reason)
            elif action in ("iLO reset", "Clock fix", "Collect diagnostics") and outcome == "failed":
                _tui_host_outcome(check, host, outcome, reason)
            elif action == "Recovery" and outcome == "deferred":
                _tui_host_outcome(check, host, outcome, reason)
    if _log_fh is None:
        return
    timestamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    reason = " ".join(reason.split())
    _append_run_log(f"{timestamp} | {check} | {short_label(host)} | {action} | {outcome} | {reason}"
                    + (f" | Evidence: {evidence}" if evidence else ""))


def info(msg: str, *, dashboard: bool = True) -> None:
    if dashboard:
        _tui_activity(msg)
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    _emit(f"{_c('2', ts)} {_c('1;34', '==>')} {_c('1', msg)}")


def section(msg: str) -> None:
    _tui_activity(msg, tone="accent")
    _emit(f"{_c('1;36', f'--- {msg} ---')}")


def ok(msg: str) -> None:
    _tui_activity(msg, tone="success")
    _emit(f"{_c('1;32', '[OK]')} {msg}")


def warn(msg: str) -> None:
    _tui_activity(f"Warning · {msg}", tone="attention")
    _emit(f"{_c('1;33', '[WARN]')} {msg}")


def err(msg: str) -> None:
    _tui_activity(f"Error · {msg}", tone="error")
    _emit(f"{_c('1;31', '[ERROR]')} {msg}", stderr=True)


# --- hostname helpers ---

def worker_fqdn(hostname: str) -> str:
    """Convert any ms hostname form to its full FQDN."""
    if "." in hostname:
        return hostname
    matches = re.findall(r'\d+', hostname)
    if not matches:
        return hostname
    slot_str = matches[-1]
    i = int(slot_str.lstrip("0") or "0")
    prefix = hostname[: hostname.rfind(slot_str)]
    if prefix.lower().rstrip("-") in ("", "ms"):
        prefix = "t-linux64-ms-"
    if i > 630:
        c = ((i - 1) - 30) // 45 + 2
    elif i > 615:
        # Slots 616-630: 15-slot extension block that wraps back to chassis 1 (mdc1).
        # This reflects a specific DC layout exception; update if physical layout changes.
        c = ((i - 1) - 15) // 45 + 1 - 13
    elif i > 300:
        c = ((i - 1) + 15) // 45 + 1
    else:
        c = (i - 1) // 45 + 1
    dc = "mdc2" if c > 7 else "mdc1"
    return f"{prefix}{slot_str.zfill(3)}.test.releng.{dc}.mozilla.com"


def canonical_moonshot_fqdn(hostname: str) -> str | None:
    """Return a canonical Moonshot FQDN, or None for non-Moonshot input."""
    fqdn = worker_fqdn(hostname.strip())
    if MOONSHOT_HOST_RE.fullmatch(fqdn):
        return fqdn
    return None


def short_label(hostname: str) -> str:
    """Return a short ms label, e.g. 'ms025', from any hostname form."""
    base = hostname.split(".")[0]
    m = re.search(r'ms-?(\d+)$', base, re.IGNORECASE)
    if m:
        return f"ms{int(m.group(1)):03d}"
    return base


def is_moonshot_host_token(token: str) -> bool:
    """Return True if token is a host form Medic is allowed to reset."""
    return bool(re.fullmatch(
        r'(?:ms-?\d+|t-linux64-ms-\d+(?:\.test\.releng\.mdc[12]\.mozilla\.com)?)',
        token.strip(),
        re.IGNORECASE,
    ))


def parse_bad_hosts(raw: str) -> tuple[list[str], list[str]]:
    """Parse fleetroll's hostname-only output, returning valid tokens and ignored words."""
    hosts: list[str] = []
    ignored: list[str] = []
    for token in raw.split():
        if is_moonshot_host_token(token):
            hosts.append(token)
        else:
            ignored.append(token)
    return hosts, ignored


def check_fleet_reset_circuit_breaker(
    hosts: list[str], max_reset_pct: float,
) -> tuple[bool, list[str], str]:
    """Validate auto-selected hosts against configured inventory and blast radius."""
    configured_hosts = _configured_linux_moonshot_hosts()
    if not configured_hosts:
        return False, [], "Cannot load configured Moonshot inventory; refusing automatic resets"

    # Preserve Fleetroll's order while ensuring duplicates cannot inflate work or
    # cause the same host to be reset more than once in a run.
    candidates = list(dict.fromkeys(worker_fqdn(host) for host in hosts))
    unknown_hosts = set(candidates) - configured_hosts
    if unknown_hosts:
        labels = " ".join(sorted(short_label(host) for host in unknown_hosts))
        return False, candidates, f"Fleetroll returned host(s) outside configured inventory: {labels}"

    candidate_pct = 100.0 * len(candidates) / len(configured_hosts)
    summary = (
        f"Fleet reset candidates: {len(candidates)}/{len(configured_hosts)} "
        f"({candidate_pct:.1f}%; maximum {max_reset_pct:g}%)"
    )
    if candidate_pct > max_reset_pct:
        required_pct = math.ceil(candidate_pct)
        return False, candidates, (
            f"Circuit breaker tripped — {summary}. "
            "After reviewing the candidate list, run an attended one-time recovery with "
            f"--auto --once --confirm --max-fleet-reset-pct {required_pct}"
        )
    return True, candidates, summary


# --- SSH probe ---

def unreachable_recovery_hosts(hosts: list[str]) -> list[str]:
    """Keep Check 1 on hosts that do not answer on the SSH port."""
    unreachable = []
    for host in hosts:
        try:
            with socket.create_connection((host, 22), timeout=5):
                pass
        except TimeoutError:
            unreachable.append(host)
            continue
        except ConnectionRefusedError:
            # A refusal still establishes that the network endpoint responded.
            pass
        except OSError as exc:
            warn(f"Skipping {short_label(host)}: could not verify network reachability ({exc})")
            log_intervention("Check 1", host, "Recovery", "deferred", "could not verify network reachability")
            continue
        info(f"Skipping {short_label(host)} in Check 1: host responded on the network")
        log_intervention("Check 1", host, "Recovery", "deferred", "host responded on the network")
    return unreachable


def ssh_is_online(fqdn: str, timeout: int = 10) -> bool:
    """Check that SSH can authenticate and execute a command, as scp requires."""
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", f"ConnectTimeout={timeout}",
                "-o", "ConnectionAttempts=1",
                fqdn,
                "true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout + 5,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# --- Ubuntu 18.04 clock remediation ---

def probe_1804_clock(fqdn: str) -> tuple[float, str] | None:
    """Return clock skew and SSH username only for confirmed Ubuntu 18.04 hosts."""
    try:
        before = time.time()
        result = subprocess.run(
            ["ssh", *SSH_OPTS, "-o", "BatchMode=yes", fqdn,
             '. /etc/os-release && printf "%s\\n" "$ID" "$VERSION_ID" '
             '&& date -u +%s && id -un'],
            capture_output=True, text=True, check=False, timeout=45,
        )
        after = time.time()
        if result.returncode != 0:
            warn(f"[{short_label(fqdn)}] clock probe failed; using normal triage")
            return None
        fields = result.stdout.strip().splitlines()
        if len(fields) != 4 or fields[:2] != ["ubuntu", "18.04"]:
            return None
        if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", fields[3]):
            return None
        return int(fields[2]) - (before + after) / 2, fields[3]
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        warn(f"[{short_label(fqdn)}] clock probe unavailable: {exc}")
        return None


def remediate_clock_skew(fqdn: str, state: dict) -> bool | None:
    """True: verified repair; False: repair failed; None: no applicable skew."""
    probe = probe_1804_clock(fqdn)
    if probe is None or abs(probe[0]) <= CLOCK_SKEW_SECONDS:
        return None
    skew, user = probe
    label = short_label(fqdn)
    warn(f"[{label}] Ubuntu 18.04 clock skew {skew:.0f}s; running clock fix")
    started = time.monotonic()
    verified = None
    try:
        # Force is intentional: the service may already exist but need another sync.
        result = subprocess.run(
            [sys.executable, str(CLOCK_FIX_SCRIPT), "-H", fqdn, "-u", user, "-f", "-v"],
            cwd=SCRIPT_DIR, capture_output=True, text=True, check=False, timeout=1200,
        )
        for output in (result.stdout, result.stderr):
            if output:
                _emit(output.rstrip())
        verified = probe_1804_clock(fqdn) if result.returncode == 0 else None
        repaired = verified is not None and abs(verified[0]) <= CLOCK_SKEW_SECONDS
    except (OSError, subprocess.TimeoutExpired) as exc:
        err(f"[{label}] clock fix failed: {exc}")
        repaired = False
    entry = _host_entry(state, fqdn)
    event = record_action(state, fqdn, "clock_fix", repaired,
                          skew_seconds=round(skew),
                          verified_skew_seconds=round(verified[0]) if verified else None,
                          duration_seconds=round(time.monotonic() - started, 1))
    log_intervention(_log_recovery_label, fqdn, "Clock fix", "verified" if repaired else "failed",
                     f"Ubuntu 18.04 clock skew was {skew:.0f}s and correction "
                     f"{'was verified' if repaired else 'could not be verified'}", STATE_FILE)
    entry["last_clock_fix"] = event.copy()
    if repaired:
        entry["total_clock_fixes"] = entry.get("total_clock_fixes", 0) + 1
        ok(f"[{label}] clock correction verified")
    else:
        err(f"[{label}] clock correction could not be verified")
    return repaired


def reset_batch(batch: list[str], state: dict, *,
                authorize_reset: Callable[[list[str]], list[str]] | None = None,
                ) -> tuple[list[str], list[str]]:
    """Repair skew before rebooting; retain repaired hosts for diagnostics."""
    _tui_phase("Checking clocks", " ".join(short_label(host) for host in batch))
    repaired = {host for host in batch if remediate_clock_skew(worker_fqdn(host), state) is True}
    reset_hosts = [host for host in batch if host not in repaired]
    if authorize_reset and reset_hosts:
        # Clock repair may take minutes. Refresh TC evidence after that work.
        _tui_phase("Rechecking Taskcluster before reset", " ".join(short_label(host) for host in reset_hosts))
        reset_hosts = authorize_reset(reset_hosts)
        batch = [host for host in batch if host in repaired or host in reset_hosts]
    if reset_hosts:
        _tui_phase("Resetting via iLO and waiting for SSH", " ".join(short_label(host) for host in reset_hosts))
        info("Resetting hosts via iLO (waiting for them to come back online)...")
        run(["uv", "run", "./reset_moonshot.py", "--force"] + reset_hosts,
            cwd=RESET_DIR, check=False)
    online_hosts, failed_labels = [], []
    for host in batch:
        fqdn, label = worker_fqdn(host), short_label(host)
        if host in repaired:
            online_hosts.append(host)
        elif ssh_is_online(fqdn):
            online_hosts.append(host)
            record_reset_success(state, fqdn)
        else:
            err(f"[{label}] did not come back online — skipping collection")
            record_reset_failure(state, fqdn)
            failed_labels.append(label)
    return online_hosts, failed_labels


# --- persistent host state ---

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception as exc:
            warn(f"Could not parse {STATE_FILE}: {exc} — starting with empty state")
            return {"hosts": {}}
    return {"hosts": {}}


def save_state(state: dict) -> None:
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).isoformat()
    for h in state.get("hosts", {}).values():
        if "reset_timestamps" in h:
            h["reset_timestamps"] = [ts for ts in h["reset_timestamps"] if ts >= cutoff]
        if "action_history" in h:
            h["action_history"] = [e for e in h["action_history"] if e["timestamp"] >= cutoff]
    RESULTS_BASE.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(STATE_FILE)


def _host_entry(state: dict, fqdn: str) -> dict:
    return state["hosts"].setdefault(fqdn, {
        "consecutive_reset_failures": 0,
        "last_success": None,
        "last_failure": None,
        "total_resets": 0,
        "total_failures": 0,
        "skip_until": None,
        "reset_timestamps": [],
    })


def record_action(state: dict, fqdn: str, action: str, success: bool, **details) -> dict:
    """Record completed actions separately; a clock fix does not imply TC recovery."""
    entry = _host_entry(state, fqdn)
    event = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "action": action, "success": success, **details,
    }
    entry.setdefault("action_history", []).append(event)
    counts = entry.setdefault("action_counts", {}).setdefault(action, {"success": 0, "failure": 0})
    counts["success" if success else "failure"] += 1
    entry.setdefault("last_actions", {})[action] = event.copy()
    _emit(json.dumps({"event": "medic_action", "host": fqdn, **event}, sort_keys=True))
    return event


def record_reset_success(state: dict, fqdn: str) -> None:
    h = _host_entry(state, fqdn)
    h["consecutive_reset_failures"] = 0
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    h["last_success"] = now_iso
    h["total_resets"] = h.get("total_resets", 0) + 1
    h["skip_until"] = None
    h.setdefault("reset_timestamps", []).append(now_iso)
    record_action(state, fqdn, "reset", True)
    reason = "SSH responded after reset"
    if _log_recovery_label == "Check 1":
        reason = "Host unreachable with stale Taskcluster activity. " + reason
    log_intervention(_log_recovery_label, fqdn, "iLO reset", "verified", reason, STATE_FILE)


def record_reset_failure(state: dict, fqdn: str) -> None:
    h = _host_entry(state, fqdn)
    h["consecutive_reset_failures"] = h.get("consecutive_reset_failures", 0) + 1
    h["last_failure"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    h["total_failures"] = h.get("total_failures", 0) + 1
    record_action(state, fqdn, "reset", False)
    reason = "SSH did not return after reset"
    if _log_recovery_label == "Check 1":
        reason = "Host unreachable with stale Taskcluster activity. " + reason
    log_intervention(_log_recovery_label, fqdn, "iLO reset", "failed", reason, STATE_FILE)
    if h["consecutive_reset_failures"] >= SKIP_THRESHOLD_CONSECUTIVE:
        skip_until = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=SKIP_DURATION_HOURS)
        ).isoformat()
        h["skip_until"] = skip_until
        label = short_label(fqdn)
        err(f"[{label}] {h['consecutive_reset_failures']} consecutive reset failures — "
            f"skipping for {SKIP_DURATION_HOURS}h (until {skip_until[:16]}Z)")


def is_skipped(state: dict, fqdn: str) -> bool:
    h = state["hosts"].get(fqdn, {})
    skip_until = h.get("skip_until")
    if not skip_until:
        return False
    return datetime.datetime.fromisoformat(skip_until) > datetime.datetime.now(datetime.timezone.utc)


def _configured_linux_moonshot_hosts() -> set[str]:
    host_list = FLEETROLL_DIR / "configs/host-lists/linux/all_moonshots.list"
    if not host_list.exists():
        host_list = FLEETROLL_DIR / "configs/host-lists/linux/all.list"
    if not host_list.exists():
        return set()
    hosts = set()
    for line in host_list.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fqdn = canonical_moonshot_fqdn(line)
        if fqdn:
            hosts.add(fqdn)
    return hosts


def _ssh_observed_linux_moonshot_hosts() -> set[str] | None:
    host_list = FLEETROLL_DIR / "configs/host-lists/linux/all_moonshots.list"
    if not host_list.exists():
        return None
    cmd = [
        "uv", "run", "fleetroll", "host", "monitor", str(host_list),
        "--once", "--hostname-only", "--filter", MOONSHOT_OBSERVED_FILTER,
    ]
    result = subprocess.run(cmd, cwd=FLEETROLL_DIR, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        warn(f"Could not query SSH-observed Moonshot fleet from fleetroll CLI: {detail}")
        return None
    hosts = set()
    ignored = []
    for line in result.stdout.splitlines():
        fqdn = canonical_moonshot_fqdn(line)
        if fqdn:
            hosts.add(fqdn)
        elif line.strip():
            ignored.append(line.strip())
    if ignored:
        warn(f"Ignoring {len(ignored)} non-Moonshot line(s) from fleetroll CLI output")
    return hosts


def _report_fleet_hosts() -> tuple[set[str], str]:
    hosts = _ssh_observed_linux_moonshot_hosts()
    if hosts:
        return hosts, "SSH-observed Linux Moonshots"
    return _configured_linux_moonshot_hosts(), "configured Linux Moonshots fallback"


def _never_reset_summary(hosts: dict) -> tuple[int, int, int, str] | None:
    fleet_hosts, source = _report_fleet_hosts()
    if not fleet_hosts:
        return None
    reset_hosts = {
        fqdn for fqdn, h in hosts.items()
        if h.get("total_resets", 0) > 0 and canonical_moonshot_fqdn(fqdn)
    }
    never_reset = fleet_hosts - reset_hosts
    pct = round(100 * len(never_reset) / len(fleet_hosts))
    return len(never_reset), len(fleet_hosts), pct, source


def announce_overview_report() -> None:
    info(f"Overview report: {OVERVIEW_HTML_FILE}")


def _resets_since(hosts: dict, hours: int) -> int:
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)).isoformat()
    return sum(
        sum(1 for ts in h.get("reset_timestamps", []) if ts >= cutoff)
        for h in hosts.values()
    )


def action_report_rows(hosts: dict) -> list[list[str]]:
    """Report typed outcomes without inferring worker/Taskcluster recovery."""
    rows = []
    for fqdn, host in sorted(hosts.items()):
        counts = host.get("action_counts", {})
        for action in ("clock_fix", "diagnostics"):
            totals = counts.get(action, {})
            last = host.get("last_actions", {}).get(action)
            # Support state written by the initial clock-fix implementation.
            if action == "clock_fix" and not last:
                last = host.get("last_clock_fix")
                successes = host.get("total_clock_fixes", 0)
                failures = int(last is not None and not last["success"])
            else:
                successes, failures = totals.get("success", 0), totals.get("failure", 0)
            if not last:
                continue
            before, after = last.get("skew_seconds"), last.get("verified_skew_seconds")
            rows.append([
                short_label(fqdn), action, str(successes + failures),
                str(successes), str(failures), last["timestamp"],
                "success" if last["success"] else "failure",
                str(before) if before is not None else "",
                str(after) if after is not None else "",
            ])
    return rows


def last_observed_host_os(root: Path) -> dict[str, dict[str, str]]:
    """Find the newest saved OS release observation for each Moonshot label."""
    observations = {}
    reports = sorted(root.glob("20*/*-ms*.md"), key=lambda path: path.name, reverse=True)
    for report in reports:
        match = re.fullmatch(r"(\d{8}T\d{6}Z)-(ms\d+)\.md", report.name)
        if not match or match[2] in observations:
            continue
        try:
            observed_at = datetime.datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.UTC)
            with report.open(encoding="utf-8", errors="replace") as stream:
                if stream.readline().strip() != "# Moonshot Hang Report":
                    continue
                for line in stream:
                    if line.strip() == "## os-release":
                        break
                    if line.startswith("## uptime"):
                        break
                else:
                    continue
                if line.strip() != "## os-release":
                    continue
                release = {}
                for line in stream:
                    if line.startswith("## "):
                        break
                    key, _, value = line.strip().partition("=")
                    if key in {"NAME", "VERSION_ID", "PRETTY_NAME"}:
                        release[key] = value.strip("\"'")
        except (OSError, ValueError):
            continue
        name = release.get("NAME")
        display = " ".join(filter(None, (name, release.get("VERSION_ID")))) if name else release.get("PRETTY_NAME")
        if display:
            observations[match[2]] = {"label": display, "observed_at": observed_at.isoformat()}
    return observations


ACTION_REPORT_HEADERS = [
    "Host", "Action", "Recorded Attempts", "Successes", "Failures", "Last Attempt",
    "Last Outcome", "Skew Before (s)", "Verified Skew (s)",
]


def update_overview_md(state: dict) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    hosts = state.get("hosts", {})

    def fmt(s: str | None) -> str:
        return s[:19].replace("T", " ") + " UTC" if s else ""

    lines = [
        "# Moonshot Medic: Overview",
        "",
        "> **This file is auto-generated by `moonshot_medic.py`. Do not edit — changes will be overwritten.**",
        "",
        f"_Generated: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}_",
        "",
    ]

    skipped = {
        fqdn: h for fqdn, h in hosts.items()
        if h.get("skip_until") and
        datetime.datetime.fromisoformat(h["skip_until"]) > now
    }
    total_resets = sum(h.get("total_resets", 0) for h in hosts.values())
    unique_hosts = sum(1 for h in hosts.values() if h.get("total_resets", 0) > 0)
    resets_24h = _resets_since(hosts, 24)
    resets_7d = _resets_since(hosts, 24 * 7)
    never_reset = _never_reset_summary(hosts)
    if hosts:
        never_reset_str = ""
        if never_reset is not None:
            never_count, fleet_size, never_pct, source = never_reset
            never_reset_str = f", {never_count} of {fleet_size} {source} never reset ({never_pct}%)"
        lines.append(f"_{total_resets} reset{'s' if total_resets != 1 else ''} across {unique_hosts} unique host{'s' if unique_hosts != 1 else ''} — {resets_24h} in last 24h, {resets_7d} in last 7 days{never_reset_str}._")
        lines.append("")

    counts = daily_counts()
    if counts:
        lines += ["## Diagnostics Collected", ""]
        for date_str, count in counts:
            lines.append(f"- {date_str}: {count} host{'s' if count != 1 else ''}")
        lines.append("")

    if skipped:
        lines += ["## Needs Human Attention (currently skipped)", ""]
        for fqdn, h in sorted(skipped.items()):
            label = short_label(fqdn)
            lines.append(
                f"- **{label}** (`{fqdn}`): {h['consecutive_reset_failures']} consecutive failures, "
                f"skip until {fmt(h.get('skip_until'))}, "
                f"last failure: {fmt(h.get('last_failure'))}"
            )
        lines.append("")

    if hosts:
        lines += [
            "## Host History",
            "",
            "| Host | Total Resets | Reset Failures | Consec Reset Fails | Last Reset Success | Last Reset Failure | Skip Until |",
            "|------|-------------|----------|--------------|-------------|--------------|------------|",
        ]
        for fqdn, h in sorted(hosts.items()):
            label = short_label(fqdn)
            lines.append(
                f"| {label} | {h.get('total_resets', 0)} | {h.get('total_failures', 0)} | "
                f"{h.get('consecutive_reset_failures', 0)} | {fmt(h.get('last_success'))} | "
                f"{fmt(h.get('last_failure'))} | {fmt(h.get('skip_until'))} |"
            )
        lines.append("")

    action_rows = action_report_rows(hosts)
    if action_rows:
        lines += ["## Clock Repairs and Diagnostics", "",
                  "Clock success means verified clock correction; it does not confirm Taskcluster recovery. "
                  "Counts cover recorded outcomes; historical reset data is unchanged.", "",
                  "| " + " | ".join(ACTION_REPORT_HEADERS) + " |",
                  "| " + " | ".join(["---"] * len(ACTION_REPORT_HEADERS)) + " |"]
        for row in action_rows:
            row[5] = fmt(row[5])
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    RESULTS_BASE.mkdir(parents=True, exist_ok=True)
    OVERVIEW_FILE.write_text("\n".join(lines) + "\n")


def update_favicon(path: Path | None = None) -> None:
    path = FAVICON_FILE if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="10" fill="#111"/>
  <path d="M12 44V18h8l12 15 12-15h8v26h-8V30L34 43h-4L20 30v14z" fill="#0ff"/>
  <circle cx="50" cy="14" r="6" fill="#f90"/>
  <path d="M10 52h44" stroke="#444" stroke-width="4" stroke-linecap="round"/>
</svg>
""")


def update_overview_html(state: dict) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    hosts = state.get("hosts", {})
    host_os = last_observed_host_os(RESULTS_BASE)

    def os_badge(label: str) -> str:
        if observation := host_os.get(label):
            title = f'Last observed in diagnostics at {observation["observed_at"]}'
            os_label = escape(observation["label"])
            return f' <span class="os-badge" data-os="{os_label}" title="{escape(title)}">{os_label}</span>'
        return ""

    def fmt(s: str | None) -> str:
        if not s:
            return ""
        try:
            stamp = datetime.datetime.fromisoformat(s)
        except ValueError:
            return escape(s)
        stamp = stamp.replace(tzinfo=stamp.tzinfo or datetime.UTC).astimezone(datetime.UTC)
        iso = stamp.isoformat()
        return f'<time datetime="{iso}" data-utc="{iso}">{stamp:%Y-%m-%d %H:%M:%S UTC}</time>'

    skipped = {
        fqdn: h for fqdn, h in hosts.items()
        if h.get("skip_until") and
        datetime.datetime.fromisoformat(h["skip_until"]) > now
    }
    total_resets = sum(h.get("total_resets", 0) for h in hosts.values())
    unique_hosts = sum(1 for h in hosts.values() if h.get("total_resets", 0) > 0)
    resets_24h = _resets_since(hosts, 24)
    resets_7d = _resets_since(hosts, 24 * 7)
    fleet_hosts, fleet_source = _report_fleet_hosts()
    never_reset_hosts = fleet_hosts - {
        fqdn for fqdn, h in hosts.items()
        if h.get("total_resets", 0) > 0 and canonical_moonshot_fqdn(fqdn)
    }
    never_reset_pct = round(100 * len(never_reset_hosts) / len(fleet_hosts)) if fleet_hosts else None

    from collections import defaultdict
    _buckets: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _fqdn, _h in hosts.items():
        _label = short_label(_fqdn)
        for _ts in _h.get("reset_timestamps", []):
            _buckets[_ts[:10]][_label] += 1
    _chart_days = [(now.date() - datetime.timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    chart_data = [
        {"date": d, "total": sum(_buckets[d].values()), "hosts": dict(_buckets[d])}
        for d in _chart_days
    ]

    _reset_counts: dict[int, list[str]] = defaultdict(list)
    for _fqdn, _h in hosts.items():
        _reset_counts[_h.get("total_resets", 0)].append(short_label(_fqdn))
    _known = {_fqdn for _fqdn in hosts}
    for _fqdn in fleet_hosts - _known:
        _reset_counts[0].append(short_label(_fqdn))
    _max_bucket = max(_reset_counts.keys(), default=0)
    hist_data = [
        {"resets": i, "count": len(_reset_counts[i]), "hosts": sorted(_reset_counts[i])}
        for i in range(_max_bucket + 1)
    ]

    metrics = "".join(
        f'<div class="metric"><strong class="number">{value}</strong><span class="muted">{label}</span></div>'
        for label, value in (
            ("Lifetime iLO resets", total_resets), ("Hosts reset", unique_hosts),
            ("Resets · 24h", resets_24h), ("Resets · 7d", resets_7d),
        )
    )
    never_reset = ""
    if never_reset_pct is not None:
        never_reset = (
            '<p class="secondary inventory">'
            f'{len(never_reset_hosts)} of {len(fleet_hosts)} {escape(fleet_source)} '
            f'never reset ({never_reset_pct}%)</p>'
        )

    backoff = '<p class="empty">No hosts currently in reset backoff.</p>'
    if skipped:
        backoff = '<ul class="backoff">' + "".join(
            f'<li><strong class="mono">{escape(short_label(fqdn))}</strong> '
            f'{os_badge(short_label(fqdn))} '
            f'<span class="secondary">({escape(fqdn)})</span> · '
            f'{entry.get("consecutive_reset_failures", 0)} consecutive failures · '
            f'Retry after {fmt(entry.get("skip_until"))} · '
            f'Last failure {fmt(entry.get("last_failure"))}</li>'
            for fqdn, entry in sorted(skipped.items())
        ) + '</ul>'

    def table(table_id: str, headings: list[str], rows: list[str], empty: str) -> str:
        if not rows:
            return f'<p class="empty">{empty}</p>'
        header = "".join(f'<th scope="col"><button type="button">{escape(heading)}</button></th>'
                         for heading in headings)
        return (
            f'<div class="table-scroll"><table id="{table_id}"><thead><tr>{header}</tr></thead>'
            '<tbody>' + "\n".join(rows) + '</tbody></table></div>'
        )

    host_rows = []
    for fqdn, entry in sorted(hosts.items()):
        streak = entry.get("consecutive_reset_failures", 0)
        tone = "bad" if streak >= SKIP_THRESHOLD_CONSECUTIVE else "warn" if streak else ""
        row_class = ' class="skip"' if fqdn in skipped else ""
        host_rows.append(
            f'<tr data-host="{escape(fqdn.lower())}"{row_class}>'
            f'<td class="mono">{escape(short_label(fqdn))}{os_badge(short_label(fqdn))}</td>'
            f'<td>{entry.get("total_resets", 0)}</td>'
            f'<td>{entry.get("total_failures", 0)}</td>'
            f'<td class="{tone}">{streak}</td>'
            f'<td>{fmt(entry.get("last_success"))}</td>'
            f'<td>{fmt(entry.get("last_failure"))}</td>'
            f'<td>{fmt(entry.get("skip_until"))}</td></tr>'
        )
    host_table = table("host-table", [
        "Host", "Total iLO Resets", "Reset Failures", "Failure Streak",
        "Last Reset Success", "Last Reset Failure", "Skip Until",
    ], host_rows, "No recorded host history.")

    action_rows = []
    for row in action_report_rows(hosts):
        cells = [fmt(value) if i == 5 else escape(value) for i, value in enumerate(row)]
        cells[0] += os_badge(row[0])
        action_rows.append("<tr>" + "".join(f"<td>{value}</td>" for value in cells) + "</tr>")
    action_table = table("action-table", ACTION_REPORT_HEADERS, action_rows, "No clock repairs or diagnostics recorded.")
    daily_table = table("daily-table", ["UTC date", "Hosts collected"], [
        f'<tr><td>{escape(date)}</td><td class="ok">{count}</td></tr>'
        for date, count in daily_counts()
    ], "No daily diagnostics counts recorded.")

    # Embed data as JSON and keep the report usable as a local HTML file.
    data = json.dumps({"days": chart_data, "distribution": hist_data, "hostOS": host_os}).replace("<", r"\u003c")
    html = (SCRIPT_DIR / "moonshot_medic_overview.html").read_text(encoding="utf-8")
    for name, value in {
        "REPORT THEME": REPORT_THEME_FILE.read_text(encoding="utf-8"),
        "MEDIC LOGO": escape("\n".join(MEDIC_LOGO)),
        "GENERATED": fmt(now.isoformat()),
        "METRICS": metrics, "NEVER RESET": never_reset, "BACKOFF": backoff,
        "HOST TABLE": host_table, "ACTION TABLE": action_table, "DAILY TABLE": daily_table,
        "REPORT DATA": data,
    }.items():
        html = html.replace(f"<!-- {name} -->", value)

    RESULTS_BASE.mkdir(parents=True, exist_ok=True)
    update_favicon()
    OVERVIEW_HTML_FILE.write_text(html, encoding="utf-8")


# --- daily activity ---

def daily_counts() -> list[tuple[str, int]]:
    """Return (date_str, count) pairs sorted newest-first by scanning results dirs."""
    if not RESULTS_BASE.exists():
        return []
    results = []
    for d in sorted(RESULTS_BASE.iterdir(), reverse=True):
        if d.is_dir() and re.fullmatch(r'\d{8}', d.name):
            count = sum(1 for f in d.iterdir() if f.suffix == '.md')
            if count:
                date_str = f"{d.name[:4]}-{d.name[4:6]}-{d.name[6:]}"
                results.append((date_str, count))
    return results


# --- recency filter ---

def recently_processed(label: str) -> bool:
    if not RESULTS_BASE.exists():
        return False
    cutoff = datetime.datetime.now().timestamp() - RECENCY_MINUTES * 60
    return any(f.stat().st_mtime > cutoff for f in RESULTS_BASE.rglob(f"????????T??????Z-{label}.md"))


# --- announcements ---

_voice_enabled = True
_voice_all_hours = False
VOICE_HOUR_START = 10
VOICE_HOUR_END = 18


def in_slack_meeting() -> bool:
    """Suppress speech only when the optional slack-status command reports a meeting."""
    try:
        result = subprocess.run(
            ["slack-status"], capture_output=True, text=True, check=False, timeout=5,
        )
        if result.returncode != 0:
            return False
        status = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    return isinstance(status, dict) and status.get("meeting_assessment") == "likely"


def say(msg: str) -> None:
    if not _voice_enabled:
        return
    now = datetime.datetime.now()
    if not _voice_all_hours and not (now.weekday() < 5 and VOICE_HOUR_START <= now.hour < VOICE_HOUR_END):
        return
    if in_slack_meeting():
        return
    subprocess.run(["say", "-v", "Rocko", "-r", "220", msg], check=False)


# --- subprocess helpers ---

def run(cmd: list, *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check)


def capture(cmd: list, *, cwd: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)
    return (result.stdout + result.stderr).strip()


def capture_stdout(cmd: list, *, cwd: Path | None = None, check: bool = True) -> tuple[str, str]:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)
    return result.stdout.strip(), result.stderr.strip()


def validate_fleetroll_freshness(raw: str, returncode: int) -> tuple[bool, list[str]]:
    """Validate Fleetroll's versioned freshness response, failing closed."""
    messages: list[str] = []
    try:
        report = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return False, [f"Fleetroll returned invalid freshness JSON: {exc}"]

    if not isinstance(report, dict):
        return False, ["Fleetroll freshness JSON is not an object"]

    if report.get("schema_version") != FLEETROLL_FRESHNESS_SCHEMA_VERSION:
        messages.append(
            "Unsupported Fleetroll freshness schema: "
            f"expected {FLEETROLL_FRESHNESS_SCHEMA_VERSION}, "
            f"got {report.get('schema_version')!r}"
        )

    required_sources = report.get("required_sources")
    if not isinstance(required_sources, list):
        messages.append("Fleetroll freshness report has no valid required_sources list")
        required_sources = []
    else:
        invalid_sources = [source for source in required_sources if not isinstance(source, str)]
        if invalid_sources:
            messages.append("Fleetroll freshness required_sources contains non-string values")
        required_sources = [source for source in required_sources if isinstance(source, str)]
        missing = FLEETROLL_FRESHNESS_REQUIRED_SOURCES - set(required_sources)
        if missing:
            messages.append(
                f"Fleetroll freshness report is missing required source(s): {', '.join(sorted(missing))}"
            )

    sources = report.get("sources")
    if not isinstance(sources, dict):
        messages.append("Fleetroll freshness report has no valid sources object")
        sources = {}

    for source_name in required_sources:
        source = sources.get(source_name)
        if not isinstance(source, dict):
            messages.append(f"Fleetroll freshness source {source_name!r} is missing")
            continue
        status = source.get("status")
        coverage = ""
        if "hosts_fresh" in source and "hosts_total" in source:
            coverage = f" ({source['hosts_fresh']}/{source['hosts_total']} hosts fresh)"
        if status != "fresh":
            messages.append(f"Fleetroll {source_name} data is {status!r}{coverage}")

    failures = report.get("failures")
    if not isinstance(failures, list):
        messages.append("Fleetroll freshness report has no valid failures list")
    elif failures:
        for failure in failures:
            if isinstance(failure, dict):
                source = failure.get("source", "unknown")
                reason = failure.get("reason", "unspecified failure")
                messages.append(f"Fleetroll {source} freshness failure: {reason}")
            else:
                messages.append(f"Fleetroll freshness failure: {failure}")

    if report.get("status") != "fresh":
        messages.append(f"Fleetroll overall freshness status is {report.get('status')!r}")
    if returncode != 0:
        messages.append(f"Fleetroll freshness command exited with status {returncode}")

    if messages:
        return False, messages

    summaries = []
    for source_name in required_sources:
        source = sources[source_name]
        summaries.append(
            f"{source_name}: fresh "
            f"({source.get('hosts_fresh', '?')}/{source.get('hosts_total', '?')} hosts)"
        )
    return True, summaries


def check_fleetroll_data_freshness(stale_threshold: int, min_fresh_pct: int) -> bool:
    """Run and strictly validate Fleetroll's host and Taskcluster freshness check."""
    _tui_phase("Checking Fleetroll freshness")
    cmd = [
        "uv", "run", "fleetroll", "data", "freshness",
        "configs/host-lists/linux/all.list",
        "--stale-threshold", str(stale_threshold),
        "--min-fresh-pct", str(min_fresh_pct),
        "--json", "--require-fresh",
    ]
    result = subprocess.run(
        cmd, cwd=FLEETROLL_DIR, capture_output=True, text=True, check=False,
    )
    valid, messages = validate_fleetroll_freshness(result.stdout, result.returncode)
    for message in messages:
        _tui_activity(message, tone="" if valid else "attention")
        _emit(f"  {_c('2', message)}")
    if result.stderr.strip():
        warn(f"Fleetroll freshness stderr: {result.stderr.strip()}")
    return valid


# --- automatic recovery evidence ---

def _evidence_timestamp(value: object) -> datetime.datetime:
    if not isinstance(value, str):
        raise ValueError("missing evidence timestamp")
    stamp = datetime.datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("evidence timestamp has no timezone")
    return stamp


def _fleetroll_command(*args: str, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            ["uv", "run", "fleetroll", *args], cwd=FLEETROLL_DIR,
            capture_output=True, text=True, check=True, timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        if exc.stderr and exc.stderr.strip():
            warn(f"Fleetroll stderr: {exc.stderr.strip()}")
        raise
    if result.stderr.strip():
        warn(f"Fleetroll stderr: {result.stderr.strip()}")
    return result.stdout


def fleetroll_worker_snapshot(host_source: str, *, candidates: bool = False,
                              filter_query: str | None = None) -> dict:
    args = ["host", "monitor", host_source, "--once", "--json"]
    if candidates:
        _tui_phase("Selecting automated recovery candidates")
        args.extend(["--filter-file", RECOVERY_FILTER_FILE])
    if filter_query:
        args.extend(["--filter", filter_query])
    report = json.loads(_fleetroll_command(*args))
    if not isinstance(report, dict):
        raise ValueError("Fleetroll worker snapshot is not a host-keyed object")
    for host, record in report.items():
        if (canonical_moonshot_fqdn(host) != host or not isinstance(record, dict)
                or record.get("host") != host
                or record.get("worker_evidence_schema_version") != WORKER_EVIDENCE_SCHEMA_VERSION):
            raise ValueError(f"Invalid Fleetroll v3 worker evidence for {host!r}")
    return report


def show_unnoted_unhealthy_moonshots() -> None:
    _tui_phase("Checking Fleetroll health")
    filter_query = UNHEALTHY_MOONSHOTS_FILTER
    filter_line = f"Fleetroll filter: {filter_query}"
    try:
        output = _fleetroll_command(
            "host", "monitor", "configs/host-lists/linux/all.list", "--once", "--hostname-only",
            "--filter", filter_query,
        )
        hosts = output.split()
        if any(canonical_moonshot_fqdn(host) != host for host in hosts):
            raise ValueError("Fleetroll returned an invalid Moonshot hostname")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        title = f"Check 2 — Fleetroll health: Unavailable ({exc})"
        warn(title)
        _emit(f"  {filter_line}")
        _tui_result("fleet_health", summary="Unavailable", alert=True, query=filter_query)
        return

    title = (f"Check 2 — Fleetroll health: {len(hosts)} unnoted Moonshots with HEALTHY=N "
             "and Taskcluster activity older than 1h")
    info(title)
    _emit(f"  {filter_line}")
    show_host_labels(hosts)
    labels = [short_label(host) for host in hosts]
    _tui_result("fleet_health", query=filter_query,
                hosts={label: {"state": "matched", "reason": "Fleetroll health matched at scan time"}
                       for label in labels},
                summary=f"{len(hosts)} {'host' if len(hosts) == 1 else 'hosts'} matched at scan time")


def recovery_candidate_filter() -> str:
    # Use Fleetroll's environment to read its YAML, keeping the filter file authoritative.
    try:
        result = subprocess.run(
            ["uv", "run", "--no-cache", "--no-sync", "python", "-c",
             "import sys, yaml; from pathlib import Path; "
             "print(yaml.safe_load(Path(sys.argv[1]).read_text())['query'])",
             RECOVERY_FILTER_FILE],
            cwd=FLEETROLL_DIR, capture_output=True, text=True, check=True, timeout=5,
        )
        return " ".join(result.stdout.split()) or f"--filter-file {RECOVERY_FILTER_FILE}"
    except (OSError, subprocess.SubprocessError):
        return f"--filter-file {RECOVERY_FILTER_FILE}"


def show_host_labels(hosts: list[str]) -> None:
    if hosts:
        _emit(textwrap.fill(" ".join(short_label(host) for host in hosts), width=88,
                            initial_indent="  ", subsequent_indent="  "))


def show_auto_checks(result: str, *, hosts: list[str] | None = None, action: str = "",
                     fleetroll_fresh: bool = True) -> None:
    title = f"Check 1 — Automated recovery: {result}"
    filter_query = recovery_candidate_filter()
    filter_line = f"Fleetroll candidate filter: {filter_query}"
    info(title)
    _emit(f"  {filter_line}")
    show_host_labels(hosts or [])
    if action:
        _emit(f"  Action: {action}")
    labels = [short_label(host) for host in hosts or ()]
    _tui_result("recovery", query=filter_query, action=action,
                summary=result, alert=result.startswith(("Unavailable", "Skipped")),
                hosts={label: {"state": "eligible", "reason": "Eligible for automated recovery at scan time"}
                       for label in labels})
    if fleetroll_fresh:
        show_unnoted_unhealthy_moonshots()
    else:
        title = "Check 2 — Fleetroll health: Skipped — Fleetroll freshness check failed"
        info(title)
        _tui_result("fleet_health", summary="Skipped · Fleetroll freshness check failed", alert=True)


def historical_identity_problem(record: dict, *, now: datetime.datetime,
                                max_identity_age_hours: int) -> str | None:
    """A failed latest SSH audit is allowed; a contradictory baseline is not."""
    if record.get("worker_evidence_schema_version") != WORKER_EVIDENCE_SCHEMA_VERSION:
        return "unsupported worker evidence schema"
    baseline = record.get("historical_worker_identity")
    if not isinstance(baseline, dict) or baseline.get("status") != "present":
        return "no historical worker identity"
    if (baseline.get("collection_status") != "ok"
            or baseline.get("source") != "host_configuration"
            or baseline.get("contradiction_status") != "none_known"
            or baseline.get("reasons") != []):
        return "historical identity is uncertain or contradicted"
    identity = {key: baseline.get(key) for key in WORKER_IDENTITY_FIELDS}
    if not all(isinstance(value, str) and value.strip() for value in identity.values()):
        return "historical identity is incomplete"
    provenance = baseline.get("provenance")
    if (not isinstance(provenance, dict) or provenance.get("identity") != identity
            or provenance.get("observation_ts") != baseline.get("observation_ts")
            or provenance.get("source") != "host_configuration"):
        return "historical identity provenance is incomplete"
    try:
        age = (now - _evidence_timestamp(baseline.get("observation_ts"))).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return "invalid historical identity timestamp"
    if not 0 <= age <= max_identity_age_hours * 3600:
        return "historical identity is too old or future-dated"
    return None


def automatic_recovery_problem(record: dict, *, now: datetime.datetime,
                               lookup_started: datetime.datetime, freshness_seconds: int,
                               max_identity_age_hours: int,
                               allow_recent_identity: bool = False,
                               allow_missing_worker: bool = False,
                               activity_since: datetime.datetime | None = None) -> str | None:
    """Require a fresh successful lookup pinned to this exact historical baseline."""
    problem = historical_identity_problem(
        record, now=now, max_identity_age_hours=max_identity_age_hours,
    )
    if problem:
        return problem
    baseline = record["historical_worker_identity"]
    if (not allow_recent_identity
            and (now - _evidence_timestamp(baseline["observation_ts"])).total_seconds() <= HUNG_SECONDS):
        return "host identity was successfully observed within the last four hours"
    tc = record.get("taskcluster")
    if (allow_missing_worker and activity_since is None and isinstance(tc, dict)
            and tc.get("latest_lookup_status") == "worker_missing"):
        attempt = tc.get("latest_lookup")
        identity = {key: baseline[key] for key in WORKER_IDENTITY_FIELDS}
        if (not isinstance(attempt, dict) or attempt.get("status") != "worker_missing"
                or attempt.get("resolution_source") != "historical_worker_identity"
                or attempt.get("identity") != identity or attempt.get("historical_identity_match") != "match"
                or attempt.get("historical_baseline") != baseline["provenance"]
                or attempt.get("historical_baseline_match") != "match"
                or attempt.get("successful_observation_ts") is not None):
            return "missing-worker lookup does not match the historical identity"
        try:
            started = _evidence_timestamp(attempt.get("started_ts"))
            completed = _evidence_timestamp(attempt.get("ts"))
            if (not lookup_started.replace(microsecond=0) <= started <= completed <= now
                    or (now - started).total_seconds() >= min(freshness_seconds, 3600)):
                return "missing-worker lookup is stale or does not belong to this check"
        except (ValueError, TypeError, OverflowError):
            return "invalid missing-worker lookup timestamp"
        retained = tc.get("record")
        if isinstance(retained, dict) and (retained.get("task_id") or retained.get("task_state")) and (
            retained.get("task_state") not in ("completed", "failed", "exception")
        ):
            return "retained Taskcluster task was not known to be finished"
        return None
    if not isinstance(tc, dict) or tc.get("record_status") != "stored_success":
        return "no successful Taskcluster observation"
    attempt = tc.get("latest_lookup")
    if (not isinstance(attempt, dict) or attempt.get("status") != "success"
            or tc.get("latest_lookup_status") != "success"
            or attempt.get("resolution_source") != "historical_worker_identity"):
        return "latest Taskcluster lookup did not succeed for the historical identity"
    identity = {key: baseline[key] for key in WORKER_IDENTITY_FIELDS}
    for evidence in (tc, attempt):
        if (evidence.get("identity") != identity
                or evidence.get("historical_identity_match") != "match"
                or evidence.get("historical_baseline_match") != "match"
                or evidence.get("historical_baseline") != baseline["provenance"]):
            return "Taskcluster evidence belongs to a different identity or baseline"
    if attempt.get("successful_observation_ts") != tc.get("observation_ts"):
        return "latest lookup is not linked to the retained Taskcluster observation"
    try:
        started = _evidence_timestamp(attempt.get("started_ts"))
        completed = _evidence_timestamp(attempt.get("ts"))
        observed = _evidence_timestamp(tc.get("observation_ts"))
        # Fleetroll's gather clock has second resolution. Do not accept an older run.
        if not lookup_started.replace(microsecond=0) <= started <= observed <= completed <= now:
            return "Taskcluster lookup does not belong to this recovery check"
        if (now - started).total_seconds() >= min(freshness_seconds, 3600):
            return "Taskcluster lookup is stale"
        active = _evidence_timestamp(tc.get("last_date_active"))
        if activity_since is not None:
            if not activity_since <= active <= completed:
                return "no fresh Taskcluster activity after recovery"
            return None if isinstance(tc.get("record"), dict) else "missing Taskcluster worker record"
        # Measure inactivity at query time, not after an unusually slow query.
        if not (started - active).total_seconds() > HUNG_SECONDS:
            return "worker is active or activity timestamp is future-dated"
    except (ValueError, TypeError, OverflowError):
        return "invalid or missing Taskcluster timestamp"
    raw = tc.get("record")
    if not isinstance(raw, dict):
        return "missing Taskcluster worker record"
    if raw.get("task_state") in ("running", "pending"):
        return "worker has an active Taskcluster task"
    return None


def eligible_auto_hosts(hosts: list[str], *, freshness_seconds: int,
                        max_identity_age_hours: int,
                        allow_recent_identity: bool = False,
                        allow_missing_worker: bool = False,
                        activity_since: datetime.datetime | None = None,
                        lookup_timeout: int = 300,
                        decisions: dict | None = None) -> list[str]:
    """Refresh exact identities; only validated per-host evidence authorizes actions."""
    if not hosts:
        return []
    try:
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".list") as host_file:
            host_file.write("\n".join(hosts) + "\n")
            host_file.flush()
            before = fleetroll_worker_snapshot(host_file.name)
            now = datetime.datetime.now(datetime.timezone.utc)
            eligible = []
            for host in hosts:
                problem = historical_identity_problem(
                    before.get(host, {}), now=now,
                    max_identity_age_hours=max_identity_age_hours,
                )
                if problem:
                    warn(f"Skipping {short_label(host)}: {problem}")
                    if decisions is not None:
                        decisions[host] = {"reason": problem}
                else:
                    eligible.append(host)
            if not eligible:
                return []
            host_file.seek(0)
            host_file.truncate()
            host_file.write("\n".join(eligible) + "\n")
            host_file.flush()
            started = datetime.datetime.now(datetime.timezone.utc)
            try:
                _fleetroll_command("gather", "tc", host_file.name, "--use-historical-identity", timeout=lookup_timeout)
            except subprocess.CalledProcessError as exc:
                if exc.returncode != 2:
                    raise
                # Fleetroll commits successful observations and failed lookup
                # attempts before reporting incomplete collection with exit 2.
                # Exit 2 can also signal other errors: it authorizes nothing.
                # The snapshot below must prove a successful lookup from this
                # run independently for every host, including exact provenance.
                warn("Fleetroll TC gather exited with status 2; checking fresh per-host evidence")
            after = fleetroll_worker_snapshot(host_file.name)
        now = datetime.datetime.now(datetime.timezone.utc)
        allowed = []
        for host in eligible:
            problem = automatic_recovery_problem(
                after.get(host, {}), now=now, lookup_started=started,
                freshness_seconds=freshness_seconds,
                max_identity_age_hours=max_identity_age_hours,
                allow_recent_identity=allow_recent_identity,
                allow_missing_worker=allow_missing_worker, activity_since=activity_since,
            )
            tc = after.get(host, {}).get("taskcluster", {})
            if not isinstance(tc, dict):
                tc = {}
            missing = tc.get("latest_lookup_status") == "worker_missing"
            if allow_recent_identity and not problem and not missing and activity_since is None:
                task = after[host]["taskcluster"]["record"]
                if task.get("task_id") and task.get("task_state") not in ("completed", "failed", "exception"):
                    problem = "latest Taskcluster task state is unknown"
            if decisions is not None:
                evidence = {key: tc.get(key) for key in (
                    "identity", "observation_ts", "last_date_active", "latest_lookup_status",
                )}
                if isinstance(task := tc.get("record"), dict):
                    evidence.update({key: task.get(key) for key in ("task_id", "task_run_id", "task_state")})
                if missing and not problem:
                    evidence["identity"] = tc["latest_lookup"]["identity"]
                reason = ("fresh exact-identity lookup confirms worker missing" if missing else
                          "fresh exact-identity lookup confirms post-restart activity" if activity_since else
                          "fresh exact-identity lookup confirms old Taskcluster activity")
                decisions[host] = {
                    "reason": problem or reason, "taskcluster": evidence,
                }
            if problem:
                warn(f"Skipping {short_label(host)}: {problem}")
            else:
                allowed.append(host)
        return allowed
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        warn(f"Skipping automatic recovery batch: Fleetroll evidence check failed: {exc}")
        if decisions is not None:
            decisions.update({host: {"reason": "Fleetroll evidence check failed"} for host in hosts})
        return []


# Fallback evidence source until Fleetroll supplies a supported zombie diagnosis.
# Never read cmdline or worker configuration: either may contain credentials.
WORKER_PROCESS_INSPECTION = r'''
import datetime
import json
import stat
from pathlib import Path

def collect(root=Path("/proc"), hold=Path("/home/cltbld/operator_hold"),
            status_file=Path("/tmp/worker-status.json")):
    result = {"schema_version": 1, "source": "medic_proc_v1",
              "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "boot_id": (root / "sys/kernel/random/boot_id").read_text().strip(),
              "uptime_seconds": float((root / "uptime").read_text().split()[0]),
              "processes": [], "unverified_pids": []}
    try:
        result["operator_hold"] = stat.S_ISREG(hold.stat().st_mode)
    except FileNotFoundError:
        result["operator_hold"] = False
    try:
        result["status_file_mtime"] = status_file.stat().st_mtime
    except FileNotFoundError:
        result["status_file_mtime"] = None
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            name = (entry / "comm").read_text().strip()
        except FileNotFoundError:
            continue
        if name != "generic-worker":
            continue
        if len(result["processes"]) + len(result["unverified_pids"]) >= 32:
            raise ValueError("too many matching processes")
        pid = int(entry.name)
        try:
            fields = dict(line.split(":", 1) for line in (entry / "status").read_text().splitlines()
                          if ":" in line)
            state = fields["State"].split()[0]
            ppid = int(fields["PPid"].strip())
            result["processes"].append({"pid": pid, "ppid": ppid, "state": state})
        except (OSError, KeyError, ValueError, IndexError):
            result["unverified_pids"].append(pid)
    return result
'''

WORKER_PROCESS_PROBE = WORKER_PROCESS_INSPECTION + r'''
if __name__ == "__main__":
    print(json.dumps(collect()))
'''

WORKER_REBOOT_IF_ZOMBIE = WORKER_PROCESS_INSPECTION + r'''
import subprocess
import sys

current = collect()
states = {process["state"] for process in current["processes"]}
if (current["boot_id"] != sys.argv[1] or current["operator_hold"]
        or current["unverified_pids"] or current["uptime_seconds"] < int(sys.argv[2])
        or "Z" not in states or states - {"Z", "X", "x"}):
    sys.exit(3)
subprocess.run(["systemctl", "--no-block", "reboot"], check=True)
'''


def classify_worker_probe(raw: object, *, started: datetime.datetime,
                          now: datetime.datetime, startup_grace_seconds: int) -> dict:
    """Normalize bounded local process evidence; this never authorizes recovery."""
    unknown = {"source": "medic_proc_v1", "worker_state": "unverified"}
    try:
        if (not isinstance(raw, dict) or type(raw.get("schema_version")) is not int
                or raw["schema_version"] != 1 or raw.get("source") != "medic_proc_v1"):
            raise ValueError("unsupported process evidence")
        stamp = _evidence_timestamp(raw.get("checked_at"))
        if not started - datetime.timedelta(seconds=60) <= stamp <= now + datetime.timedelta(seconds=60):
            raise ValueError("process evidence clock outside check window")
        if not isinstance(raw.get("boot_id"), str) or not re.fullmatch(
                r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", raw["boot_id"]):
            raise ValueError("invalid boot identity")
        uptime = raw.get("uptime_seconds")
        if type(uptime) not in (int, float) or not math.isfinite(uptime) or uptime < 0:
            raise ValueError("invalid uptime")
        if type(raw.get("operator_hold")) is not bool:
            raise ValueError("hold state unavailable")
        processes, unverified = raw.get("processes"), raw.get("unverified_pids")
        if not isinstance(processes, list) or not isinstance(unverified, list) or len(processes) + len(unverified) > 32:
            raise ValueError("invalid process evidence")
        clean = []
        for process in processes:
            if (not isinstance(process, dict) or type(process.get("pid")) is not int
                    or process["pid"] <= 0 or type(process.get("ppid")) is not int or process["ppid"] < 0
                    or process.get("state") not in tuple("RSDZTtXxKWIP")):
                raise ValueError("invalid process entry")
            clean.append({key: process[key] for key in ("pid", "ppid", "state")})
        if any(type(pid) is not int or pid <= 0 for pid in unverified):
            raise ValueError("invalid unverified process entry")
        pids = [p["pid"] for p in clean] + unverified
        if len(pids) != len(set(pids)):
            raise ValueError("duplicate process evidence")
        mtime = raw.get("status_file_mtime")
        if mtime is not None and (type(mtime) not in (int, float) or not math.isfinite(mtime) or mtime < 0):
            raise ValueError("invalid status-file timestamp")
        # Whitelist fields rather than forwarding arbitrary remote output.
        evidence = {"source": "medic_proc_v1", "checked_at": stamp.isoformat(),
                    "boot_id": raw["boot_id"], "uptime_seconds": uptime,
                    "operator_hold": raw["operator_hold"], "processes": clean,
                    "unverified_pids": unverified, "status_file_mtime": mtime}
        if raw["operator_hold"]:
            state, reason = "held", "operator hold active"
        elif unverified:
            state, reason = "unverified", "worker process inspection incomplete"
        elif any(p["state"] == "Z" for p in clean):
            state, reason = "zombie", "confirmed zombie generic-worker"
        elif any(p["state"] not in ("X", "x") for p in clean):
            state, reason = "live", "live worker; task health not established"
        elif uptime < startup_grace_seconds:
            state, reason = "startup", "no live worker during startup grace"
        else:
            state, reason = "absent", "no live worker; provisioning/task state not established"
        return {**evidence, "worker_state": state, "reason": reason}
    except (ValueError, TypeError, OverflowError) as exc:
        return {**unknown, "reason": str(exc)}


def fleetroll_worker_diagnosis(fqdn: str, *, started: datetime.datetime) -> dict:
    """Collect bounded diagnostic evidence without forwarding remote stderr."""
    evidence = {"source": "fleetroll_host_diagnose", "worker_state": "unverified"}
    try:
        result = subprocess.run(
            ["uv", "run", "fleetroll", "host", "diagnose", fqdn, "--json"],
            cwd=FLEETROLL_DIR, capture_output=True, text=True, check=False, timeout=45,
        )
        if len(result.stdout) > 262144:
            raise ValueError("oversized Fleetroll diagnosis")
        raw = json.loads(result.stdout)
        if not isinstance(raw, dict):
            raise ValueError("invalid Fleetroll diagnosis")
        # Retain structured checks, including nested data, but omit raw reports,
        # stderr and exception text which may contain sensitive remote output.
        evidence["report"] = {key: raw[key] for key in (
            "schema_version", "host", "ts", "checked_at", "os_type", "ok",
            "collection_status", "reason", "health", "gwhc_exit_code",
            "gwhc_schema_version", "contract_status", "gwhc_ts", "zombie", "checks",
        ) if key in raw}
        if (result.returncode != 0 or type(raw.get("schema_version")) is not int
                or raw["schema_version"] != 1 or raw.get("host") != fqdn
                or raw.get("ok") is not True or raw.get("collection_status") != "completed"
                or raw.get("os_type") != "Linux" or raw.get("health") not in ("healthy", "unhealthy")
                or raw.get("contract_status") in ("unknown", "unsupported")):
            raise ValueError("unsupported or unavailable Fleetroll diagnosis")
        now = datetime.datetime.now(datetime.UTC)
        for key in ("checked_at", "gwhc_ts"):
            stamp = _evidence_timestamp(raw.get(key))
            if not started - datetime.timedelta(seconds=60) <= stamp <= now + datetime.timedelta(seconds=60):
                raise ValueError("Fleetroll evidence clock outside check window")
        zombie = raw.get("zombie")
        status = zombie.get("status") if isinstance(zombie, dict) else None
        # Absence of zombies does not establish worker presence or task health.
        if status == "confirmed":
            return {**evidence, "worker_state": "zombie", "reason": "Fleetroll confirmed zombie"}
        if status == "absent":
            return {**evidence, "worker_state": "not_zombie", "reason": "Fleetroll confirmed no zombie"}
        raise ValueError("Fleetroll zombie diagnosis inconclusive")
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, OverflowError):
        return {**evidence, "reason": "Fleetroll diagnosis unavailable or inconclusive"}


def diagnose_worker(fqdn: str, *, startup_grace_seconds: int) -> dict:
    if canonical_moonshot_fqdn(fqdn) != fqdn:
        return {"source": "medic_proc_v1", "worker_state": "unverified",
                "reason": "invalid Moonshot target"}
    fleetroll = fleetroll_worker_diagnosis(fqdn, started=datetime.datetime.now(datetime.UTC))
    if fleetroll["worker_state"] != "unverified":
        return fleetroll
    return {**diagnose_worker_probe(fqdn, startup_grace_seconds=startup_grace_seconds),
            "fleetroll": fleetroll}


def diagnose_worker_probe(fqdn: str, *, startup_grace_seconds: int) -> dict:
    started = datetime.datetime.now(datetime.UTC)
    try:
        if canonical_moonshot_fqdn(fqdn) != fqdn:
            raise ValueError("invalid Moonshot target")
        result = subprocess.run(
            ["ssh", *SSH_OPTS, "-o", "BatchMode=yes", fqdn,
             "sudo -n python3 -c " + shlex.quote(WORKER_PROCESS_PROBE)],
            capture_output=True, text=True, check=False, timeout=45,
        )
        if result.returncode != 0:
            raise ValueError("SSH/process probe failed")
        if len(result.stdout) > 16384:
            raise ValueError("oversized process evidence")
        return classify_worker_probe(
            json.loads(result.stdout), started=started,
            now=datetime.datetime.now(datetime.UTC),
            startup_grace_seconds=startup_grace_seconds,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        # Remote stderr can contain sensitive or unbounded output; never persist it.
        return {"source": "medic_proc_v1", "worker_state": "unverified",
                "reason": "probe timeout" if isinstance(exc, subprocess.TimeoutExpired) else "process probe unavailable"}


def worker_audit_problem(host: str, record: object, *, now: datetime.datetime,
                         freshness_seconds: int) -> str | None:
    if not isinstance(record, dict) or record.get("host") != host:
        return "invalid host record"
    if record.get("ok") is not True:
        return "latest host audit did not succeed"
    try:
        age = (now - _evidence_timestamp(record.get("ts"))).total_seconds()
        if not 0 <= age < freshness_seconds:
            return "successful host audit is stale or future-dated"
    except (ValueError, TypeError, OverflowError):
        return "invalid host audit timestamp"
    return None


def run_worker_recovery_probe(host: str, program: str, mode: str, grace_seconds: int,
                              signature: dict | None):
    if canonical_moonshot_fqdn(host) != host:
        raise ValueError("invalid Moonshot target")
    remote = shlex.join(["sudo", "-n", "python3", "-c", program, mode, str(grace_seconds),
                         json.dumps(signature or {}, separators=(",", ":"))])
    return subprocess.run(
        ["ssh", *SSH_OPTS, "-o", "BatchMode=yes", host, remote],
        capture_output=True, text=True, check=False, timeout=45,
    )


def run_gdm_probe(host: str, mode: str, grace_seconds: int, signature: dict | None = None):
    program = (SCRIPT_DIR / "moonshot_gdm_probe.py").read_text()
    return run_worker_recovery_probe(host, program, mode, grace_seconds, signature)


def run_oom_probe(host: str, mode: str, grace_seconds: int, signature: dict | None = None):
    program = (WORKER_PROCESS_INSPECTION + "\n" + (SCRIPT_DIR / "moonshot_oom_probe.py").read_text()
               + "\nif __name__ == '__main__':\n    sys.exit(main(collect))\n")
    return run_worker_recovery_probe(host, program, mode, grace_seconds, signature)


def run_task_stall_probe(host: str, mode: str, grace_seconds: int, signature: dict | None = None):
    program = (WORKER_PROCESS_INSPECTION + "\n" + (SCRIPT_DIR / "moonshot_task_stall_probe.py").read_text()
               + "\nif __name__ == '__main__':\n    sys.exit(main(collect))\n")
    return run_worker_recovery_probe(host, program, mode, grace_seconds, signature)


def task_stall_probe_evidence(host: str, mode: str, grace_seconds: int, signature: dict | None = None) -> dict:
    if mode == "verify":
        return oom_probe_evidence(host, mode, grace_seconds, signature)
    try:
        result = run_task_stall_probe(host, mode, grace_seconds, signature)
        if result.returncode != 0 or len(result.stdout) > 4096:
            return {}
        evidence = json.loads(result.stdout)
        if (not isinstance(evidence, dict)
                or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", str(evidence.get("boot_id", "")))
                or not re.fullmatch(TASK_STALL_TASK_ID, str(evidence.get("task_id", "")))
                or not all(type(evidence.get(key)) is int and evidence[key] > 0
                           for key in ("pid", "start_ticks", "resolved_at_us", "reclaimed_at_us"))):
            return {}
        return {key: evidence[key] for key in TASK_STALL_SIGNATURE_KEYS}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def confirm_task_stall_attempt(stall: dict, tc: dict) -> dict:
    """Verify the original Queue attempt, since an overall task status can describe a retry."""
    task_id, run_id = stall["task_id"], tc.get("task_run_id")
    identity = tc.get("identity")
    if (not re.fullmatch(TASK_STALL_TASK_ID, task_id) or task_id != tc.get("task_id")
            or type(run_id) is not int or not isinstance(identity, dict) or tc.get("task_state") != "exception"):
        return {}
    try:
        with urlopen(f"https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/{task_id}/status", timeout=8) as response:
            status = json.loads(response.read(262144))["status"]
        if (status["taskId"] != task_id
                or status["taskQueueId"] != f"{identity['provisioner']}/{identity['worker_type']}"):
            return {}
        runs = status["runs"]
        if not isinstance(runs, list) or not all(isinstance(run, dict) for run in runs):
            return {}
        if any(run.get("state") in ("running", "pending")
               and (run.get("workerGroup"), run.get("workerId")) == (identity["worker_group"], identity["worker_id"])
               for run in runs):
            return {}
        matches = [run for run in runs if type(run.get("runId")) is int and run["runId"] == run_id]
        if len(matches) != 1:
            return {}
        run, = matches
        if (run["state"] != "exception" or run["reasonResolved"] != "claim-expired"
                or (run["workerGroup"], run["workerId"]) != (identity["worker_group"], identity["worker_id"])):
            return {}
        resolving = datetime.datetime.fromtimestamp(stall["resolved_at_us"] / 1_000_000, datetime.UTC)
        reclaimed = datetime.datetime.fromtimestamp(stall["reclaimed_at_us"] / 1_000_000, datetime.UTC)
        contact = _evidence_timestamp(tc.get("last_date_active"))
        if (not _evidence_timestamp(run["started"]) <= resolving <= reclaimed <= _evidence_timestamp(run["takenUntil"])
                <= _evidence_timestamp(run["resolved"]) <= datetime.datetime.now(datetime.UTC)
                or contact > reclaimed + datetime.timedelta(seconds=60)):
            return {}
        fields = ("runId", "workerGroup", "workerId", "state", "reasonResolved", "started", "takenUntil", "resolved")
        return {"task_id": task_id, "run_id": run_id, "history": [{key: run.get(key) for key in fields} for run in runs]}
    except (OSError, HTTPException, KeyError, TypeError, ValueError, OverflowError):
        return {}


def oom_probe_evidence(host: str, mode: str, grace_seconds: int, signature: dict | None = None) -> dict:
    try:
        result = run_oom_probe(host, mode, grace_seconds, signature)
        if result.returncode != 0 or len(result.stdout) > 4096:
            return {}
        evidence = json.loads(result.stdout)
        timestamps = ("worker_ready_us",) if mode == "verify" else OOM_SIGNATURE_KEYS[1:]
        if (not isinstance(evidence, dict) or not re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", str(evidence.get("boot_id", ""))
        ) or not all(type(evidence.get(key)) is int and evidence[key] > 0 for key in timestamps)):
            return {}
        return {key: evidence[key] for key in ("boot_id", *timestamps)}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def gdm_probe_evidence(host: str, mode: str, grace_seconds: int, signature: dict | None = None) -> dict:
    try:
        result = run_gdm_probe(host, mode, grace_seconds, signature)
        if result.returncode != 0 or len(result.stdout) > 16384:
            return {}
        evidence = json.loads(result.stdout)
        if (not isinstance(evidence, dict) or not re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", str(evidence.get("boot_id", ""))
        ) or not re.fullmatch(r"[0-9a-f]{32}", str(evidence.get("gdm_invocation", "")))):
            return {}
        if mode == "detect" and not all(type(evidence.get(key)) is int and evidence[key] > 0
                                        for key in ("pid", "start_ticks")):
            return {}
        if mode == "verify" and (type(evidence.get("worker_ready_us")) is not int or evidence["worker_ready_us"] <= 0):
            return {}
        return evidence
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def diagnose_reachable_workers(*, freshness_seconds: int, max_identity_age_hours: int,
                               limit: int, startup_grace_seconds: int,
                               candidate_filter: str = INACTIVE_MOONSHOTS_FILTER) -> dict:
    """A separate, diagnosis-only path. No clock repair, reset, or recovery state writes."""
    _tui_phase("Selecting workers for diagnosis")
    raw = json.loads(_fleetroll_command(
        "host", "monitor", "configs/host-lists/linux/all.list", "--once", "--json", "--filter", candidate_filter,
    ))
    if not isinstance(raw, dict):
        raise TypeError("Fleetroll snapshot is not a host-keyed object")
    inventory = _configured_linux_moonshot_hosts()
    if not inventory or any(canonical_moonshot_fqdn(h) != h or h not in inventory for h in raw):
        raise ValueError("diagnosis candidates cannot be validated against inventory")
    now = datetime.datetime.now(datetime.UTC)
    state = load_state()
    results, candidates = [], []
    for host, record in raw.items():
        reason = worker_audit_problem(host, record, now=now, freshness_seconds=freshness_seconds)
        if not reason:
            reason = historical_identity_problem(record, now=now, max_identity_age_hours=max_identity_age_hours)
        if not reason and (recently_processed(short_label(host)) or is_skipped(state, host)):
            reason = "recent processing or failure backoff"
        if not reason and len(candidates) >= limit:
            reason = "diagnosis limit reached"
        if reason:
            results.append({"host": host, "selection": "deferred", "reason": reason})
        else:
            candidates.append(host)
    decisions: dict = {}
    if candidates:
        _tui_phase("Checking Taskcluster before worker diagnosis", f"{len(candidates)} candidate hosts")
    eligible = set(eligible_auto_hosts(
        candidates, freshness_seconds=freshness_seconds,
        max_identity_age_hours=max_identity_age_hours, allow_recent_identity=True,
        allow_missing_worker=True, decisions=decisions,
    ))
    for host in candidates:
        if host not in eligible:
            results.append({"host": host, "selection": "deferred",
                            **decisions.get(host, {"reason": "Taskcluster/identity evidence did not pass"})})
            continue
        _tui_phase("Diagnosing worker", short_label(host))
        diagnosis = diagnose_worker(host, startup_grace_seconds=startup_grace_seconds)
        if diagnosis["worker_state"] in ("absent", "not_zombie"):
            if gdm := gdm_probe_evidence(host, "detect", startup_grace_seconds):
                diagnosis = {**diagnosis, "worker_state": "gdm_autologin_stall",
                             "reason": "cltbld GDM autologin is stuck before desktop startup", "gdm": gdm}
            elif oom := oom_probe_evidence(host, "detect", startup_grace_seconds):
                diagnosis = {**diagnosis, "worker_state": "worker_oom",
                             "reason": "host OOM killed the worker's terminal service", "oom": oom}
        if diagnosis["worker_state"] in ("live", "not_zombie"):
            tc = decisions.get(host, {}).get("taskcluster", {})
            if (tc.get("task_state") == "exception"
                    and (stall := task_stall_probe_evidence(host, "detect", startup_grace_seconds))
                    and (attempt := confirm_task_stall_attempt(stall, tc))):
                diagnosis = {**diagnosis, "worker_state": "task_resolution_stall",
                             "reason": "live worker stalled resolving a claim-expired task",
                             "task_stall": stall, "task_attempt": attempt}
        results.append({"host": host, "selection": "diagnosed",
                        "candidate_filter": candidate_filter,
                        "host_audit_ts": raw[host]["ts"],
                        **decisions.get(host, {}), "diagnosis": diagnosis})
    return {"schema_version": 1, "mode": "diagnosis_only",
            "checked_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "results": results}


def report_worker_diagnoses(args: argparse.Namespace, freshness_seconds: int,
                            check_1_hosts: tuple[str, ...] = ()) -> bool:
    report = diagnose_reachable_workers(
        freshness_seconds=freshness_seconds, max_identity_age_hours=args.max_identity_age_hours,
        limit=args.diagnosis_limit, startup_grace_seconds=args.startup_grace_minutes * 60,
        candidate_filter=INACTIVE_MOONSHOTS_FILTER if args.diagnose_workers else UNHEALTHY_MOONSHOTS_FILTER,
    )
    results = report["results"]
    if not results and not args.diagnose_workers:
        info("Worker diagnosis: no candidates")
        _tui_result("diagnosis", summary="No candidates")
        return True

    now = datetime.datetime.now(datetime.UTC)
    run_dir = RESULTS_BASE / now.strftime("%Y%m%d")
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / f"{now:%Y%m%dT%H%M%S%fZ}-worker-diagnosis.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")

    diagnosed = sum(entry["selection"] == "diagnosed" for entry in results)
    zombies = sum(entry.get("diagnosis", {}).get("worker_state") == "zombie" for entry in results)
    gdm_stalls = sum(entry.get("diagnosis", {}).get("worker_state") == "gdm_autologin_stall" for entry in results)
    worker_ooms = sum(entry.get("diagnosis", {}).get("worker_state") == "worker_oom" for entry in results)
    task_stalls = sum(entry.get("diagnosis", {}).get("worker_state") == "task_resolution_stall" for entry in results)
    zombie_label = "zombie" if zombies == 1 else "zombies"
    title = (f"Worker diagnosis: {diagnosed} diagnosed, {zombies} confirmed {zombie_label}, "
             f"{len(results) - diagnosed} deferred")
    if gdm_stalls:
        title += f", {gdm_stalls} GDM autologin {'stall' if gdm_stalls == 1 else 'stalls'}"
    if worker_ooms:
        title += f", {worker_ooms} worker OOM"
    if task_stalls:
        title += f", {task_stalls} task resolution {'stall' if task_stalls == 1 else 'stalls'}"
    rows = []
    findings = {}
    for entry in sorted(results, key=lambda entry: entry["host"]):
        diagnosis = entry.get("diagnosis")
        state = diagnosis["worker_state"] if diagnosis else entry["selection"]
        reason = diagnosis["reason"] if diagnosis else entry["reason"]
        label = short_label(entry["host"])
        findings[label] = {"state": state, "reason": reason}
        rows.append(f"{label}  {state.replace('_', ' ')} — {reason}")
    _tui_result("diagnosis", hosts=findings,
                query=INACTIVE_MOONSHOTS_FILTER if args.diagnose_workers else "")
    if args.diagnose_workers:
        for entry in results:
            _emit(json.dumps({"event": "medic_worker_diagnosis", **entry}, sort_keys=True))
        info(f"Diagnosis-only report: {report_path}", dashboard=False)
        return True
    info(title)
    for row in rows:
        _emit(f"  {row}")
    info(f"Worker diagnosis report: {report_path}", dashboard=False)
    candidates = [
        entry for entry in results
        if entry.get("diagnosis", {}).get("worker_state") == "gdm_autologin_stall"
        or (entry.get("diagnosis", {}).get("worker_state") in ("zombie", "worker_oom", "task_resolution_stall")
            and entry.get("taskcluster", {}).get("latest_lookup_status") != "worker_missing")
    ]
    if args.no_reset or _interrupt_count:
        if args.confirm:
            reason = "interrupted" if _interrupt_count else "--no-reset requested"
            for entry in candidates:
                log_check_2_intervention(entry, "deferred", reason, report_path)
        return True
    if not candidates:
        return True
    hosts = [entry["host"] for entry in candidates]
    circuit_ok, _, summary = check_fleet_reset_circuit_breaker(
        [*check_1_hosts, *hosts], args.max_fleet_reset_pct,
    )
    if not circuit_ok:
        warn(f"Check 2 recovery skipped: {summary}")
        if args.confirm:
            for entry in candidates:
                log_check_2_intervention(entry, "deferred", "fleet reset circuit breaker tripped", report_path)
        return False
    if not args.confirm:
        for worker_state, action in (
            ("zombie", "collect diagnostics, then reboot over SSH if idle"),
            ("gdm_autologin_stall", "restart GDM and verify worker registration"),
            ("worker_oom", "collect diagnostics, then reboot over SSH and verify worker registration"),
            ("task_resolution_stall", "recheck the expired claim, then reboot over SSH and verify worker registration"),
        ):
            matching = [entry["host"] for entry in candidates if entry["diagnosis"]["worker_state"] == worker_state]
            if matching:
                info(f"Check 2 recovery preview: {action}", dashboard=False)
                _tui_activity(f"Recovery preview · {' '.join(short_label(host) for host in matching)} · {action}")
                show_host_labels(matching)
        return True

    report["mode"] = "check_2_recovery"
    state = load_state()
    for entry in candidates:
        if _interrupt_count:
            log_check_2_intervention(entry, "deferred", "interrupted", report_path)
            continue
        host = entry["host"]
        try:
            if entry["diagnosis"]["worker_state"] == "gdm_autologin_stall":
                recovery = recover_gdm_autologin(entry, args, freshness_seconds, state, report_path)
            elif entry["diagnosis"]["worker_state"] == "worker_oom":
                recovery = recover_worker_oom(entry, args, freshness_seconds, state, run_dir)
            elif entry["diagnosis"]["worker_state"] == "task_resolution_stall":
                recovery = recover_task_resolution_stall(entry, args, freshness_seconds, state, run_dir)
            else:
                recovery = recover_reachable_zombie(host, args, freshness_seconds, state, run_dir)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            warn(f"Check 2 recovery error for {short_label(host)}: {type(exc).__name__}")
            recovery = {"outcome": "failed", "reason": "could not complete recovery workflow"}
        entry["recovery"] = recovery
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        log_check_2_intervention(entry, recovery["outcome"], recovery["reason"], report_path)
        result = f"{short_label(host)}: {recovery['outcome']} — {recovery['reason']}"
        info(f"Check 2 recovery {result}", dashboard=False)
    return all(entry.get("recovery", {}).get("outcome") not in ("failed", "unverified") for entry in candidates)


def log_check_2_intervention(entry: dict, outcome: str, reason: str, report_path: Path) -> None:
    diagnosis = entry["diagnosis"]
    action = "GDM restart" if diagnosis["worker_state"] == "gdm_autologin_stall" else "SSH reboot"
    _tui_host_outcome("Check 2", entry["host"], outcome, reason)
    log_intervention("Check 2", entry["host"], action, outcome,
                     f"{diagnosis['reason']}. {reason}", report_path)


# --- per-host collection ---

def collect_host(fqdn: str, label: str, out_file: Path, *, boot: int = -1) -> bool:
    section(f"{label}  {fqdn}")
    host_ok = True
    tmp_file = out_file.with_suffix(".tmp")

    if run(["scp"] + SSH_OPTS + [str(HANG_SCRIPT), f"{fqdn}:/tmp/moonshot_hang_report.py"],
           check=False).returncode != 0:
        err(f"[{label}] scp upload failed")
        host_ok = False

    if host_ok:
        with tmp_file.open("w") as fh:
            result = subprocess.run(
                ["ssh"] + SSH_OPTS + [fqdn, f"sudo python3 /tmp/moonshot_hang_report.py --boot {boot}"],
                stdout=fh, check=False,
            )
        if result.returncode != 0:
            err(f"[{label}] remote script failed")
            tmp_file.unlink(missing_ok=True)
            host_ok = False

    # cleanup regardless of prior failures
    run(["ssh"] + SSH_OPTS + [fqdn, "rm -f /tmp/moonshot_hang_report.py"],
        check=False)

    if host_ok:
        section(f"{label}  gather host")
        audit = capture(["uv", "run", "fleetroll", "gather", "host", fqdn], cwd=FLEETROLL_DIR, check=False)
        with tmp_file.open("a") as f:
            f.write("\n---\n\n# Fleetroll Host Audit\n\n```\n")
            f.write(audit)
            f.write("\n```\n")
        tmp_file.rename(out_file)
        ok(f"[{label}] -> {out_file}")

    return host_ok


def collect_diagnostics(fqdn: str, label: str, out_file: Path, state: dict, *, boot: int = -1) -> bool:
    started = time.monotonic()
    collected = False
    try:
        collected = collect_host(fqdn, label, out_file, boot=boot)
        return collected
    finally:
        record_action(state, fqdn, "diagnostics", collected,
                      duration_seconds=round(time.monotonic() - started, 1),
                      report=str(out_file) if collected else None)
        if boot == -1:
            log_intervention(_log_recovery_label, fqdn, "Collect diagnostics", "saved" if collected else "failed",
                             "host diagnostics collected" if collected else "host diagnostics collection failed",
                             out_file if collected else "")
        else:
            _tui_activity("Diagnostics captured" if collected else "Diagnostics collection failed",
                          host=label, tone="success" if collected else "error")
        save_state(state)


def request_zombie_reboot(host: str, boot_id: str, startup_grace_seconds: int) -> str:
    remote = shlex.join(["sudo", "-n", "python3", "-c", WORKER_REBOOT_IF_ZOMBIE,
                         boot_id, str(startup_grace_seconds)])
    try:
        result = subprocess.run(
            ["ssh", *SSH_OPTS, "-o", "BatchMode=yes", host, remote],
            capture_output=True, check=False, timeout=45,
        )
    except subprocess.TimeoutExpired:
        return "unknown"
    except OSError:
        return "failed"
    return {0: "requested", 3: "refused", 255: "unknown"}.get(result.returncode, "failed")


def wait_for_zombie_reboot(host: str, prior_boot_id: str, startup_grace_seconds: int) -> dict:
    deadline = time.monotonic() + SSH_REBOOT_WAIT_SECONDS
    while not _interrupt_count and time.monotonic() < deadline:
        evidence = diagnose_worker_probe(host, startup_grace_seconds=startup_grace_seconds)
        boot_id = evidence.get("boot_id")
        if boot_id and boot_id != prior_boot_id:
            return evidence
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    return {}


def recover_reachable_zombie(host: str, args: argparse.Namespace, freshness_seconds: int,
                             state: dict, run_dir: Path) -> dict:
    label = short_label(host)
    grace_seconds = args.startup_grace_minutes * 60
    before = diagnose_worker_probe(host, startup_grace_seconds=grace_seconds)
    if (before["worker_state"] != "zombie" or before.get("uptime_seconds", 0) < grace_seconds
            or any(p["state"] not in ("Z", "X", "x") for p in before["processes"])):
        return {"outcome": "deferred", "reason": "live SSH check did not confirm a zombie-only worker past startup grace",
                "pre_reboot": before}
    if recently_processed(label) or is_skipped(state, host) or _interrupt_count:
        return {"outcome": "deferred", "reason": "recent processing, failure backoff, or interrupt"}

    _tui_phase("Collecting diagnostics before SSH reboot", label)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_file = run_dir / f"{stamp}-{label}.md"
    if not collect_diagnostics(host, label, out_file, state, boot=0):
        return {"outcome": "failed", "reason": "pre-reboot diagnostics failed", "pre_reboot": before}

    evidence = {"pre_reboot": before, "diagnostics": str(out_file)}
    if not check_fleetroll_data_freshness(freshness_seconds, args.freshness_min_pct):
        return {**evidence, "outcome": "deferred", "reason": "Fleetroll freshness check failed before reboot"}
    snapshot = fleetroll_worker_snapshot(host, filter_query=UNHEALTHY_MOONSHOTS_FILTER)
    if host not in snapshot:
        return {**evidence, "outcome": "deferred", "reason": "host no longer matches the Check 2 filter"}
    audit_problem = worker_audit_problem(host, snapshot[host], now=datetime.datetime.now(datetime.UTC),
                                         freshness_seconds=freshness_seconds)
    if audit_problem:
        return {**evidence, "outcome": "deferred", "reason": audit_problem}
    decisions: dict = {}
    eligible = eligible_auto_hosts(
        [host], freshness_seconds=freshness_seconds, max_identity_age_hours=args.max_identity_age_hours,
        allow_recent_identity=True, decisions=decisions,
    )
    evidence.update(decisions.get(host, {}))
    if not eligible or _interrupt_count:
        reason = "interrupted" if _interrupt_count else evidence.get("reason", "Taskcluster inactivity could not be verified")
        return {**evidence, "outcome": "deferred", "reason": reason}

    _tui_phase("Requesting SSH reboot and verifying boot ID", label)
    old_boot_id = before["boot_id"]
    request = request_zombie_reboot(host, old_boot_id, grace_seconds)
    evidence["reboot_request"] = request
    if request == "refused":
        return {**evidence, "outcome": "deferred", "reason": "remote zombie, hold, or boot check changed"}
    post_boot = wait_for_zombie_reboot(host, old_boot_id, grace_seconds) if request != "failed" else {}
    if post_boot:
        recovery = {**evidence, "outcome": "rebooted", "reason": "new boot ID confirmed over SSH",
                    "post_reboot": post_boot}
    elif request == "failed":
        recovery = {**evidence, "outcome": "failed", "reason": "SSH reboot command failed"}
    else:
        recovery = {**evidence, "outcome": "unverified", "reason": "new boot ID could not be confirmed"}
    record_action(state, host, "ssh_reboot", bool(post_boot),
                  request=request, outcome=recovery["outcome"], report=str(out_file))
    save_state(state)
    return recovery


def recover_worker_oom(entry: dict, args: argparse.Namespace, freshness_seconds: int,
                       state: dict, run_dir: Path) -> dict:
    return recover_worker_by_reboot(
        entry, args, freshness_seconds, state, run_dir, diagnosis_key="oom", trigger="worker_oom",
        signature_keys=OOM_SIGNATURE_KEYS, probe=oom_probe_evidence, request_probe=run_oom_probe,
    )


def recover_task_resolution_stall(entry: dict, args: argparse.Namespace, freshness_seconds: int,
                                  state: dict, run_dir: Path) -> dict:
    return recover_worker_by_reboot(
        entry, args, freshness_seconds, state, run_dir, diagnosis_key="task_stall", trigger="task_resolution_stall",
        signature_keys=TASK_STALL_SIGNATURE_KEYS, probe=task_stall_probe_evidence, request_probe=run_task_stall_probe,
    )


def recover_worker_by_reboot(entry: dict, args: argparse.Namespace, freshness_seconds: int,
                             state: dict, run_dir: Path, *, diagnosis_key: str, trigger: str,
                             signature_keys: tuple[str, ...], probe: Callable, request_probe: Callable) -> dict:
    host = entry["host"]
    label = short_label(host)
    grace_seconds = args.startup_grace_minutes * 60
    signature = {key: entry["diagnosis"][diagnosis_key][key] for key in signature_keys}
    host_state = _host_entry(state, host)
    boot_key = f"{diagnosis_key}_reboot_boot_id"
    if (host_state.get(boot_key) == signature["boot_id"] or recently_processed(label)
            or is_skipped(state, host) or _interrupt_count):
        return {"outcome": "deferred", "reason": "already attempted this boot, recent processing, backoff, or interrupt"}
    if probe(host, "detect", grace_seconds) != signature:
        return {"outcome": "deferred", "reason": "live SSH check did not confirm the same worker failure"}

    _tui_phase("Collecting diagnostics before worker reboot", label)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_file = run_dir / f"{stamp}-{label}.md"
    if not collect_diagnostics(host, label, out_file, state, boot=0):
        return {"outcome": "failed", "reason": "pre-reboot diagnostics failed"}
    evidence = {"diagnostics": str(out_file)}
    if not check_fleetroll_data_freshness(freshness_seconds, args.freshness_min_pct):
        return {**evidence, "outcome": "deferred", "reason": "Fleetroll freshness check failed before reboot"}
    snapshot = fleetroll_worker_snapshot(host, filter_query=entry["candidate_filter"])
    problem = worker_audit_problem(host, snapshot.get(host), now=datetime.datetime.now(datetime.UTC),
                                   freshness_seconds=freshness_seconds)
    if problem:
        return {**evidence, "outcome": "deferred", "reason": problem}
    decisions: dict = {}
    eligible = eligible_auto_hosts(
        [host], freshness_seconds=freshness_seconds, max_identity_age_hours=args.max_identity_age_hours,
        allow_recent_identity=True, decisions=decisions,
    )
    before = decisions.get(host, {}).get("taskcluster", {})
    identity = before.get("identity")
    evidence["taskcluster_before"] = before
    if not eligible or not identity or identity != entry.get("taskcluster", {}).get("identity") or _interrupt_count:
        return {**evidence, "outcome": "deferred", "reason": "idle Taskcluster identity could not be reconfirmed or interrupted"}
    if diagnosis_key == "task_stall":
        attempt = confirm_task_stall_attempt(signature, before)
        if not attempt or attempt["run_id"] != entry["diagnosis"]["task_attempt"]["run_id"]:
            return {**evidence, "outcome": "deferred", "reason": "original expired claim could not be reconfirmed"}
        evidence["task_attempt_before_reboot"] = attempt

    _tui_phase("Requesting SSH reboot", label)
    host_state[boot_key] = signature["boot_id"]
    save_state(state)
    if _interrupt_count:
        return {**evidence, "outcome": "deferred", "reason": "interrupted before requesting reboot"}
    requested_at = datetime.datetime.now(datetime.UTC)
    try:
        result = request_probe(host, "reboot", grace_seconds, signature)
        request = {0: "requested", 3: "refused", 255: "unknown"}.get(result.returncode, "failed")
    except subprocess.TimeoutExpired:
        request = "unknown"
    except OSError:
        request = "failed"
    evidence["reboot_request"] = request
    if request == "refused":
        recovery = {"outcome": "deferred", "reason": "remote failure signature, worker, hold, or boot check changed"}
    elif request == "failed":
        recovery = {"outcome": "failed", "reason": "SSH reboot command failed"}
    else:
        recovery = {"outcome": "unverified", "reason": "new boot with a ready worker could not be verified"}
        _tui_phase("Waiting for a new boot and ready worker", label)
        deadline = time.monotonic() + SSH_REBOOT_WAIT_SECONDS
        while not _interrupt_count and time.monotonic() < deadline:
            ready = probe(host, "verify", grace_seconds, signature)
            if ready and ready["boot_id"] != signature["boot_id"]:
                evidence["post_reboot"] = ready
                _tui_phase("Verifying Taskcluster contact after reboot", label)
                post: dict = {}
                contacted = eligible_auto_hosts(
                    [host], freshness_seconds=freshness_seconds, max_identity_age_hours=args.max_identity_age_hours,
                    allow_recent_identity=True, activity_since=requested_at, decisions=post,
                    lookup_timeout=max(1, int(deadline - time.monotonic())),
                )
                after = post.get(host, {}).get("taskcluster", {})
                evidence["taskcluster_after"] = after
                if contacted and after.get("identity") == identity:
                    recovery = {"outcome": "recovered",
                                "reason": "new boot, workerReady, and fresh contact for the expected Taskcluster identity"}
                    break
                recovery["reason"] = "new boot and workerReady observed but fresh Taskcluster contact was not verified"
            time.sleep(min(5, max(0, deadline - time.monotonic())))
    record_action(state, host, "ssh_reboot", recovery["outcome"] == "recovered", trigger=trigger,
                  outcome=recovery["outcome"], request=request, report=str(out_file))
    save_state(state)
    return {**evidence, **recovery}


def recover_gdm_autologin(entry: dict, args: argparse.Namespace, freshness_seconds: int,
                          state: dict, report_path: Path) -> dict:
    host = entry["host"]
    label = short_label(host)
    grace_seconds = args.startup_grace_minutes * 60
    before = entry["diagnosis"]["gdm"]
    signature = {key: before[key] for key in SIGNATURE_KEYS}
    host_state = _host_entry(state, host)
    if (host_state.get("gdm_restart_boot_id") == signature["boot_id"]
            or recently_processed(label) or is_skipped(state, host) or _interrupt_count):
        return {"outcome": "deferred", "reason": "already attempted this boot, recent processing, backoff, or interrupt"}

    if not check_fleetroll_data_freshness(freshness_seconds, args.freshness_min_pct):
        return {"outcome": "deferred", "reason": "Fleetroll freshness check failed before GDM restart"}
    # Recheck Check 2 selection, including the absence of an operator note.
    query = entry["candidate_filter"]
    snapshot = fleetroll_worker_snapshot(host, filter_query=query)
    problem = worker_audit_problem(host, snapshot.get(host), now=datetime.datetime.now(datetime.UTC),
                                   freshness_seconds=freshness_seconds)
    if problem:
        return {"outcome": "deferred", "reason": problem}
    decisions: dict = {}
    eligible = eligible_auto_hosts(
        [host], freshness_seconds=freshness_seconds, max_identity_age_hours=args.max_identity_age_hours,
        allow_recent_identity=True, allow_missing_worker=True, decisions=decisions,
    )
    if not eligible or _interrupt_count:
        return {"outcome": "deferred", "reason": "interrupted or Taskcluster inactivity could not be verified",
                "decision": decisions.get(host)}
    expected_identity = decisions[host]["taskcluster"]["identity"]
    original_identity = entry.get("taskcluster", {}).get("identity")
    if not expected_identity or expected_identity != original_identity:
        return {"outcome": "deferred", "reason": "Taskcluster identity changed after diagnosis"}

    _tui_phase("Restarting GDM and verifying worker readiness", label)
    # Persist the attempt before SSH. A timeout may mean the request already ran.
    host_state["gdm_restart_boot_id"] = signature["boot_id"]
    save_state(state)
    if _interrupt_count:
        return {"outcome": "deferred", "reason": "interrupted before requesting the GDM restart"}
    requested_at = datetime.datetime.now(datetime.UTC)
    evidence = {"taskcluster_before": decisions[host]["taskcluster"]}
    try:
        result = run_gdm_probe(host, "restart", grace_seconds, signature)
        request = {0: "requested", 3: "refused", 255: "unknown"}.get(result.returncode, "failed")
    except subprocess.TimeoutExpired:
        request = "unknown"
    except OSError:
        request = "failed"
    evidence["restart_request"] = request
    if request == "refused":
        recovery = {"outcome": "deferred", "reason": "the remote GDM stall or safety checks changed"}
    elif request == "failed":
        recovery = {"outcome": "failed", "reason": "GDM restart command failed"}
    else:
        recovery = {"outcome": "unverified", "reason": "worker readiness after the GDM restart could not be verified"}
        deadline = time.monotonic() + GDM_VERIFY_WAIT_SECONDS
        while not _interrupt_count and time.monotonic() < deadline:
            ready = gdm_probe_evidence(host, "verify", grace_seconds, signature)
            if (ready and ready["boot_id"] == signature["boot_id"]
                    and ready["gdm_invocation"] != signature["gdm_invocation"]):
                evidence["worker_ready"] = ready
                post: dict = {}
                _tui_phase("Verifying Taskcluster contact after GDM restart", label)
                contacted = eligible_auto_hosts(
                    [host], freshness_seconds=freshness_seconds, max_identity_age_hours=args.max_identity_age_hours,
                    allow_recent_identity=True, activity_since=requested_at, decisions=post,
                    lookup_timeout=max(1, int(deadline - time.monotonic())),
                )
                post_tc = post.get(host, {}).get("taskcluster", {})
                evidence["taskcluster_after"] = post_tc
                if contacted and post_tc.get("identity") == expected_identity:
                    recovery = {"outcome": "recovered", "reason": "workerReady and fresh contact for the expected Taskcluster identity"}
                    break
                recovery["reason"] = "workerReady observed but fresh Taskcluster contact was not verified"
            time.sleep(min(5, max(0, deadline - time.monotonic())))
    record_action(state, host, "gdm_restart", recovery["outcome"] == "recovered",
                  outcome=recovery["outcome"], request=request, report=str(report_path))
    save_state(state)
    return {**evidence, **recovery}


# --- main ---

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automated triage for hung t-linux64-ms-* Moonshot cartridges: resets via iLO, collects hang diagnostics, and tracks repeat offenders.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("hostname", nargs="*", metavar="HOST",
                        help="Short (ms025) or FQDN. Reads stdin if omitted.")
    parser.add_argument("--auto", action="store_true",
                        help="Fetch bad-host list from fleetroll.")
    interface = parser.add_mutually_exclusive_group()
    interface.add_argument("--tui", action="store_true",
                           help="Require the dashboard (default for interactive auto runs).")
    interface.add_argument("--legacy", action="store_true",
                           help="Force plain terminal output in auto mode.")
    parser.add_argument("--diagnose-workers", action="store_true",
                        help="One-shot read-only diagnosis of reachable, TC-inactive workers; requires --auto.")
    parser.add_argument("--diagnosis-limit", type=int, default=10,
                        help="Maximum candidates queried for worker diagnosis (default: 10).")
    parser.add_argument("--startup-grace-minutes", type=int, default=15,
                        help="Minimum uptime for Check 2 recovery and for classifying an absent worker (default: 15).")
    parser.add_argument("-n", "--no-reset", action="store_true",
                        help="Skip iLO reset, SSH reboot, and GDM restart.")
    parser.add_argument("--freshness-requirement", type=int, default=None, metavar="MINUTES",
                        help="Max acceptable age of fleetroll data in minutes (default: same as --loop-interval). "
                             "Set a large value to effectively disable the check.")
    parser.add_argument("--ignore-recency", action="store_true",
                        help=f"Process hosts even if collected within last {RECENCY_MINUTES} min.")
    parser.add_argument("--confirm", action="store_true",
                        help="Required with --auto to confirm you want to proceed.")
    parser.add_argument("-q", "--no-voice", action="store_true",
                        help="Suppress spoken announcements.")
    parser.add_argument("--voice-all-hours", action="store_true",
                        help=f"Speak outside working hours ({VOICE_HOUR_START}:00–before {VOICE_HOUR_END}:00); "
                             "still suppress speech when slack-status reports a meeting.")
    parser.add_argument("-l", "--loop-interval", type=int, default=15, metavar="MINUTES",
                        help="Minutes to sleep between auto runs (default: 15).")
    parser.add_argument("--once", action="store_true",
                        help="Run one auto-mode iteration, then exit.")
    parser.add_argument("--max-fleet-reset-pct", type=float,
                        default=DEFAULT_MAX_FLEET_RESET_PCT, metavar="PCT",
                        help=f"Maximum percentage of the configured fleet that auto mode may reset "
                             f"(default: {DEFAULT_MAX_FLEET_RESET_PCT:g}).")
    parser.add_argument("--freshness-min-pct", type=int, default=FRESHNESS_MIN_PCT, metavar="PCT",
                        help=f"Minimum %% of hosts with fresh fleetroll data required (default: {FRESHNESS_MIN_PCT}).")
    parser.add_argument("--max-identity-age-hours", type=int,
                        default=DEFAULT_MAX_IDENTITY_AGE_HOURS, metavar="HOURS",
                        help="Maximum historical worker identity age for automatic recovery "
                             "(default: 168 hours).")
    parser.add_argument("--update-report", action="store_true",
                        help="Regenerate OVERVIEW.html and OVERVIEW.md from existing state, then exit.")
    return parser.parse_args()


def main() -> None:
    global _voice_enabled, _voice_all_hours, _tui_status_path

    args = parse_args()
    _voice_enabled = not args.no_voice
    _voice_all_hours = args.voice_all_hours

    if args.diagnose_workers and (not args.auto or args.hostname or args.confirm or args.update_report):
        err("--diagnose-workers requires --auto and cannot combine with hosts, --confirm, or --update-report.")
        sys.exit(1)
    if args.diagnosis_limit < 1 or args.startup_grace_minutes < 1:
        err("--diagnosis-limit and --startup-grace-minutes must be positive.")
        sys.exit(1)

    if args.loop_interval < 1:
        err("--loop-interval must be at least 1 minute.")
        sys.exit(1)
    if args.max_identity_age_hours < 1:
        err("--max-identity-age-hours must be at least 1 hour.")
        sys.exit(1)
    if args.freshness_requirement is not None and args.freshness_requirement < 1:
        err("--freshness-requirement must be at least 1 minute.")
        sys.exit(1)
    if args.once and not args.auto:
        err("--once requires --auto.")
        sys.exit(1)
    if args.tui and not args.auto:
        err("--tui requires --auto.")
        sys.exit(1)
    if not 0 < args.max_fleet_reset_pct <= 100:
        err("--max-fleet-reset-pct must be greater than 0 and at most 100.")
        sys.exit(1)
    if args.max_fleet_reset_pct > DEFAULT_MAX_FLEET_RESET_PCT and not args.once:
        err(f"Raising --max-fleet-reset-pct above {DEFAULT_MAX_FLEET_RESET_PCT:g}% "
            "requires --once.")
        sys.exit(1)

    use_tui = args.tui or (
        args.auto and not args.legacy and not args.update_report
        and sys.stdin.isatty() and sys.stdout.isatty()
    )
    if use_tui:
        from moonshot_medic_tui import STATUS_ENV, run_dashboard

        if status_file := os.environ.pop(STATUS_ENV, None):
            _tui_status_path = Path(status_file)
            mode = "Diagnosis only" if args.diagnose_workers else (
                "Confirmed recovery" if args.confirm else "Recovery preview"
            )
            _publish_tui(mode=mode, confirm=args.confirm, logo=MEDIC_LOGO,
                         looping=args.confirm and not args.once and not args.update_report)
            _tui_phase("Starting Medic")
        else:
            command = [sys.executable, "-u", str(Path(__file__).resolve())]
            # The dashboard child has redirected streams and needs an explicit flag.
            if not args.tui:
                command.append("--tui")
            command.extend(sys.argv[1:])
            sys.exit(run_dashboard(command, startup_logo=MEDIC_LOGO))

    if args.update_report:
        state = load_state()
        update_overview_html(state)
        update_overview_md(state)
        info(f"Report updated: {OVERVIEW_HTML_FILE}")
        return

    signal.signal(signal.SIGINT, _sigint_handler)
    if use_tui:
        signal.signal(signal.SIGUSR1, _rescan_signal_handler)

    if not use_tui:
        print()
        for line in MEDIC_LOGO:
            print(_c('1;36', line))
        print()
    say(SCRIPT_VOICE_NAME)

    freshness_mins = args.freshness_requirement if args.freshness_requirement is not None else args.loop_interval
    stale_threshold = freshness_mins * 60

    def show_worker_diagnoses(check_1_hosts: tuple[str, ...] = ()) -> bool:
        try:
            return report_worker_diagnoses(args, stale_threshold, check_1_hosts)
        except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
            warn(f"Worker diagnosis failed: {exc}")
            _tui_result("diagnosis", title="Worker diagnosis unavailable", alert=True)
            return False

    def check_1_eligible_hosts(hosts: list[str]) -> list[str]:
        decisions: dict = {}
        eligible = eligible_auto_hosts(
            hosts, freshness_seconds=stale_threshold,
            max_identity_age_hours=args.max_identity_age_hours,
            decisions=decisions,
        )
        eligible_set = set(eligible)
        for host in hosts:
            if host not in eligible_set:
                log_intervention("Check 1", host, "Recovery", "deferred",
                                 decisions.get(host, {}).get("reason", "Taskcluster eligibility could not be verified"))
        return unreachable_recovery_hosts(eligible)

    def diagnose_only() -> None:
        if not check_fleetroll_data_freshness(stale_threshold, args.freshness_min_pct):
            err("Fleetroll freshness validation failed; diagnosis skipped.")
            sys.exit(1)
        try:
            report_worker_diagnoses(args, stale_threshold)
            return
        except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
            err(f"Worker diagnosis failed: {exc}")
            sys.exit(1)

    def preview_recovery() -> None:
        if not check_fleetroll_data_freshness(stale_threshold, args.freshness_min_pct):
            show_auto_checks("Skipped — Fleetroll freshness check failed", fleetroll_fresh=False)
            sys.exit(1)
        try:
            preview_hosts = list(fleetroll_worker_snapshot(
                "configs/host-lists/linux/all.list", candidates=True,
            ))
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            err(f"Cannot read automatic recovery candidates: {exc}")
            show_auto_checks("Unavailable — could not read recovery candidates")
            sys.exit(1)
        circuit_ok, preview_hosts, circuit_summary = check_fleet_reset_circuit_breaker(
            preview_hosts, args.max_fleet_reset_pct,
        )
        if not circuit_ok:
            err(circuit_summary)
            show_auto_checks("Skipped — fleet reset circuit breaker tripped")
            say("Moonshot Medic circuit breaker tripped")
            sys.exit(1)
        _tui_phase("Checking Taskcluster eligibility", f"{len(preview_hosts)} candidate hosts")
        preview_hosts = check_1_eligible_hosts(preview_hosts)
        n = min(len(preview_hosts), AUTO_BATCH_SIZE)
        total = len(preview_hosts)
        result = f"{total} {'host' if total == 1 else 'hosts'} eligible"
        if n < total:
            result += f" (first {n} shown)"
        action = ""
        if n:
            if args.no_reset:
                action = "Would check clocks, repair skew if needed, and collect diagnostics."
            else:
                action = "Would repair clock skew or reset via iLO, then collect diagnostics."
        show_auto_checks(result, hosts=preview_hosts[:n], action=action)
        if not _interrupt_count:
            show_worker_diagnoses(tuple(preview_hosts))
        if n:
            print()
            warn("You must watch and monitor the run. Re-run with --confirm to proceed.")
        _tui_phase("Recovery preview complete")
        sys.exit(1)

    if args.diagnose_workers:
        with scan_log("diagnosis only"):
            diagnose_only()
        return
    if args.auto and not args.confirm:
        with scan_log("recovery preview"):
            preview_recovery()
        return

    # --- verify dependencies ---
    for d in (FLEETROLL_DIR, RESET_DIR):
        if not d.is_dir():
            err(f"Required directory not found: {d}")
            sys.exit(1)
    if not HANG_SCRIPT.is_file():
        err(f"Diagnostic script not found: {HANG_SCRIPT}")
        sys.exit(1)

    if RESULTS_BASE.exists():
        state = load_state()
        update_overview_md(state)
        update_overview_html(state)
    if args.auto:
        announce_overview_report()

    def run_scan(first_run: bool, run_dir: Path) -> tuple[bool, bool]:
        last_failed = False
        stale = False
        check_1_hosts: tuple[str, ...] = ()
        # --- freshness gate ---
        if args.auto:
            if not check_fleetroll_data_freshness(stale_threshold, args.freshness_min_pct):
                show_auto_checks("Skipped — Fleetroll freshness check failed", fleetroll_fresh=False)
                say("Stale Fleetroll data")
                stale = True
                last_failed = True

        # --- resolve host list ---
        if not stale:
            if args.auto:
                try:
                    host_tokens = list(fleetroll_worker_snapshot(
                        "configs/host-lists/linux/all.list", candidates=True,
                    ))
                except (OSError, subprocess.SubprocessError, ValueError) as exc:
                    err(f"Cannot read automatic recovery candidates: {exc}")
                    show_auto_checks("Unavailable — could not read recovery candidates")
                    return True, True
                circuit_ok, hosts, circuit_summary = check_fleet_reset_circuit_breaker(
                    host_tokens, args.max_fleet_reset_pct,
                )
                if not circuit_ok:
                    err(circuit_summary)
                    show_auto_checks("Skipped — fleet reset circuit breaker tripped")
                    say("Moonshot Medic circuit breaker tripped")
                    return True, True
            elif args.hostname:
                hosts = [worker_fqdn(h) for h in args.hostname]
            else:
                if sys.stdin.isatty():
                    print("Enter hostnames (one per line, Ctrl-D to finish):", file=sys.stderr)
                hosts = [worker_fqdn(line.strip()) for line in sys.stdin if line.strip()]

            if not hosts:
                if args.auto:
                    show_auto_checks("0 hosts eligible")
                else:
                    warn("No hosts specified. Nothing to do.")
                    sys.exit(0)
            else:
                state = load_state()

                # --- recency + skip filter ---
                if not args.ignore_recency:
                    skipped, filtered = [], []
                    for h in hosts:
                        fqdn = worker_fqdn(h)
                        label = short_label(h)
                        if recently_processed(label):
                            skipped.append(h)
                            log_intervention(_log_recovery_label, fqdn, "Recovery", "deferred", "recently processed")
                        elif is_skipped(state, fqdn):
                            skip_until = state["hosts"].get(fqdn, {}).get("skip_until", "")
                            warn(f"Skipping {label}: {SKIP_THRESHOLD_CONSECUTIVE}+ consecutive reset failures "
                                 f"(skip until {skip_until[:16]}Z — use --ignore-recency to override)")
                            skipped.append(h)
                            log_intervention(_log_recovery_label, fqdn, "Recovery", "deferred",
                                             f"reset failure backoff until {skip_until}")
                        else:
                            filtered.append(h)
                    if skipped:
                        warn(f"Skipping {len(skipped)} host(s): {' '.join(short_label(h) for h in skipped)}")
                    hosts = filtered

                # Resolve eligibility before announcing recovery work.
                # Keep the batch/reset checks below to catch changes during waits.
                if args.auto:
                    _tui_phase("Checking Taskcluster eligibility", f"{len(hosts)} candidate hosts")
                    hosts = check_1_eligible_hosts(hosts)
                    check_1_hosts = tuple(hosts)
                    total = len(hosts)
                    show_auto_checks(f"{total} {'host' if total == 1 else 'hosts'} eligible", hosts=hosts)

                if not hosts:
                    if not args.auto:
                        info("No eligible hosts to process. Nothing to do.")
                else:
                    batches = [hosts[i:i + AUTO_BATCH_SIZE] for i in range(0, len(hosts), AUTO_BATCH_SIZE)]
                    n_total = len(hosts)
                    info(f"Hosts to process: {n_total} host(s) across {len(batches)} batch(es) of up to {AUTO_BATCH_SIZE}")
                    say(f"Starting run. {n_total} host{'s' if n_total != 1 else ''} detected.")

                    if first_run:
                        _tui_phase("Waiting to start recovery", " ".join(short_label(host) for host in hosts))
                        warn("Starting in 15 seconds — press Ctrl-C to abort.")
                        for _ in range(15):
                            if _interrupt_count:
                                break
                            time.sleep(1)
                        if _interrupt_count:
                            warn("Aborted before first run.")
                            for host in hosts:
                                log_intervention(_log_recovery_label, host, "Recovery", "deferred",
                                                 "interrupted before recovery")
                            return last_failed, True

                    info(f"Results will be saved to: {run_dir}")
                    run_started = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    _emit(f"{_log_recovery_label} recovery started at {run_started}")
                    _emit(f"Hosts: {' '.join(hosts)}")
                    _emit("")

                    if args.no_reset:
                        warn("Skipping reset (--no-reset).")
                        for host in hosts:
                            log_intervention(_log_recovery_label, host, "iLO reset", "deferred", "--no-reset requested")

                    ok_hosts: list[str] = []
                    fail_hosts: list[str] = []

                    for batch_num, batch in enumerate(batches, 1):
                        if _interrupt_count:
                            warn("Stopping after interrupt.")
                            break

                        if len(batches) > 1:
                            section(f"Batch {batch_num}/{len(batches)}: {' '.join(short_label(h) for h in batch)}")

                        # --- reset batch ---
                        reset_fail_labels: list[str] = []
                        if args.auto:
                            _tui_phase("Rechecking recovery batch eligibility",
                                       " ".join(short_label(host) for host in batch))
                            batch = check_1_eligible_hosts(batch)
                            if not batch:
                                continue
                        if not args.no_reset:
                            authorize_reset = check_1_eligible_hosts if args.auto else None
                            batch, reset_fail_labels = reset_batch(
                                batch, state, authorize_reset=authorize_reset,
                            )
                            save_state(state)
                            update_overview_md(state)
                            update_overview_html(state)

                        fail_hosts.extend(reset_fail_labels)

                        # --- collect batch ---
                        for host in batch:
                            fqdn = worker_fqdn(host)
                            label = short_label(host)
                            ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                            out_file = run_dir / f"{ts}-{label}.md"

                            try:
                                _tui_phase("Checking clock and collecting diagnostics", label)
                                # Reboots can expose the bad RTC again; also run with --no-reset.
                                clock_result = remediate_clock_skew(fqdn, state)
                                save_state(state)
                                collected = collect_diagnostics(fqdn, label, out_file, state)
                                if collected and clock_result is not False:
                                    ok_hosts.append(label)
                                else:
                                    if not collected:
                                        err(f"[{label}] collection failed")
                                    fail_hosts.append(label)
                            except Exception as exc:
                                err(f"[{label}] unexpected error: {exc}")
                                fail_hosts.append(label)
                            update_overview_md(state)
                            update_overview_html(state)
                            print()

                    # --- summary ---
                    _emit(_c('1', "=" * 40))
                    _emit(f"{_c('1', f'{_log_recovery_label} recovery complete:')} {run_dir}")
                    for action in ("clock_fix", "reset", "diagnostics"):
                        events = [e for h in state.get("hosts", {}).values()
                                  for e in h.get("action_history", [])
                                  if e["timestamp"] >= run_started and e["action"] == action]
                        if events:
                            successes = sum(e["success"] for e in events)
                            _emit(f"{action}: {len(events)} attempt(s), {successes} succeeded, "
                                  f"{len(events) - successes} failed")
                    if ok_hosts:
                        _emit(f"{_c('1;32', 'OK:  ')} {' '.join(ok_hosts)}")
                    if fail_hosts:
                        _emit(f"{_c('1;31', 'FAIL:')} {' '.join(fail_hosts)}")
                    _emit(_c('1', "=" * 40))

                    ok_n, fail_n = len(ok_hosts), len(fail_hosts)
                    if fail_n:
                        say(f"{ok_n} succeeded, {fail_n} failed.")
                    else:
                        say(f"All {ok_n} host{'s' if ok_n != 1 else ''} succeeded.")

                    last_failed = bool(fail_hosts)

        if args.auto and not stale and not _interrupt_count:
            last_failed = not show_worker_diagnoses(check_1_hosts) or last_failed

        return last_failed, False

    last_failed = False
    first_run = True
    mode = "confirmed auto" if args.auto else "manual recovery"
    recovery_label = "Check 1" if args.auto else "Manual"
    while True:
        with scan_log(mode, recovery_label=recovery_label) as run_dir:
            last_failed, stop = run_scan(first_run, run_dir)

        if args.auto:
            _tui_phase("Refreshing overview reports")
            try:
                update_overview_html(load_state())
            except Exception as exc:
                warn(f"Could not refresh OVERVIEW.html: {exc}")
            try:
                from moonshot_medic_overview2 import generate_report

                generate_report(RESULTS_BASE, _configured_linux_moonshot_hosts())
            except Exception as exc:
                warn(f"Could not refresh OVERVIEW2.html: {exc}")

        if stop:
            break
        first_run = False

        if not args.auto or args.once or _interrupt_count:
            break

        wait_for_next_scan(args.loop_interval)

        if _interrupt_count:
            break

        print()

    _tui_phase("Run finished with errors" if last_failed else "Run complete")
    sys.exit(1 if last_failed else 0)


if __name__ == "__main__":
    main()
