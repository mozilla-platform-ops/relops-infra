#!/usr/bin/env python3
"""Automated triage for hung t-linux64-ms-* Moonshot cartridges: resets via iLO, collects hang diagnostics, and tracks repeat offenders.

Usage:
  moonshot_medic.py [--auto] [--once] [--no-reset] [--ignore-recency] [HOST ...]

  --auto                    Fetch bad-host list from fleetroll instead of reading argv/stdin (requires --confirm).
  --once                    Run one auto-mode iteration, then exit.
  --max-fleet-reset-pct     Maximum auto-selected percentage of configured fleet (default: 20).
  --no-reset                Skip iLO reboot (host already freshly rebooted).
  --freshness-requirement   Max acceptable age of fleetroll data in minutes (default: loop-interval).
  --ignore-recency          Process hosts even if collected within the last RECENCY_MINUTES minutes.
  HOST ...                  Short (ms025) or FQDN. If omitted and not --auto, reads stdin.
"""

import argparse
import datetime
import json
import math
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

FLEETROLL_DIR = Path.home() / "git/fleetroll_mvp"
SCRIPT_DIR = Path(__file__).parent.resolve()
RESET_DIR = SCRIPT_DIR
RESULTS_BASE = SCRIPT_DIR / "moonshot_debugging_results"
HANG_SCRIPT = SCRIPT_DIR / "moonshot_hang_report.py"
CLOCK_FIX_SCRIPT = SCRIPT_DIR / "install_clock_fix.py"
CLOCK_SKEW_SECONDS = 5 * 60
RECENCY_MINUTES = 60
AUTO_BATCH_SIZE = 10
SCRIPT_VOICE_NAME = "Moonshot Medic"
STATE_FILE = RESULTS_BASE / "state.json"
OVERVIEW_FILE = RESULTS_BASE / "OVERVIEW.md"
OVERVIEW_HTML_FILE = RESULTS_BASE / "OVERVIEW.html"
FAVICON_FILE = RESULTS_BASE / "favicon.svg"
SKIP_THRESHOLD_CONSECUTIVE = 3
SKIP_DURATION_HOURS = 6
FRESHNESS_MIN_PCT = 65
DEFAULT_MAX_FLEET_RESET_PCT = 20.0
FLEETROLL_FRESHNESS_SCHEMA_VERSION = 2
FLEETROLL_FRESHNESS_REQUIRED_SOURCES = {"host", "tc"}
MOONSHOT_HOST_RE = re.compile(r'^t-linux64-ms-\d+\.test\.releng\.mdc[12]\.mozilla\.com$', re.IGNORECASE)
MOONSHOT_OBSERVED_FILTER = "host~t-linux64-ms os=L sort:host:asc"
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


def _sigint_handler(sig, frame):
    global _interrupt_count
    _interrupt_count += 1
    if _interrupt_count == 1:
        print("\n[Ctrl-C] Will stop after current batch finishes. Press again to exit immediately.",
              file=sys.stderr)
    else:
        print("\n[Ctrl-C] Exiting immediately.", file=sys.stderr)
        sys.exit(130)


# --- color / logging ---

_use_color = sys.stdout.isatty()
_log_fh = None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _use_color else text


def _emit(line: str, *, stderr: bool = False) -> None:
    print(line, file=sys.stderr if stderr else sys.stdout)
    if _log_fh:
        print(re.sub(r'\033\[[0-9;]*m', '', line), file=_log_fh)


def info(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    _emit(f"{_c('2', ts)} {_c('1;34', '==>')} {_c('1', msg)}")


def section(msg: str) -> None:
    _emit(f"{_c('1;36', f'--- {msg} ---')}")


def ok(msg: str) -> None:
    _emit(f"{_c('1;32', '[OK]')} {msg}")


def warn(msg: str) -> None:
    _emit(f"{_c('1;33', '[WARN]')} {msg}")


def err(msg: str) -> None:
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
    repaired = {host for host in batch if remediate_clock_skew(worker_fqdn(host), state) is True}
    reset_hosts = [host for host in batch if host not in repaired]
    if authorize_reset and reset_hosts:
        # Clock repair may take minutes. Refresh TC evidence after that work.
        reset_hosts = authorize_reset(reset_hosts)
        batch = [host for host in batch if host in repaired or host in reset_hosts]
    if reset_hosts:
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


def record_reset_failure(state: dict, fqdn: str) -> None:
    h = _host_entry(state, fqdn)
    h["consecutive_reset_failures"] = h.get("consecutive_reset_failures", 0) + 1
    h["last_failure"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    h["total_failures"] = h.get("total_failures", 0) + 1
    record_action(state, fqdn, "reset", False)
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


def update_favicon() -> None:
    RESULTS_BASE.mkdir(parents=True, exist_ok=True)
    FAVICON_FILE.write_text("""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="10" fill="#111"/>
  <path d="M12 44V18h8l12 15 12-15h8v26h-8V30L34 43h-4L20 30v14z" fill="#0ff"/>
  <circle cx="50" cy="14" r="6" fill="#f90"/>
  <path d="M10 52h44" stroke="#444" stroke-width="4" stroke-linecap="round"/>
</svg>
""")


def update_overview_html(state: dict) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    hosts = state.get("hosts", {})

    def fmt(s: str | None) -> str:
        if not s:
            return ""
        iso = s if ("+" in s or s.endswith("Z")) else s[:19] + "+00:00"
        display = s[:19].replace("T", " ") + " UTC"
        return f'<span class="utc-time" data-utc="{iso}">{display}</span>'

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

    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta http-equiv="refresh" content="60">',
        '<link rel="icon" href="favicon.svg" type="image/svg+xml">',
        "<title>Moonshot Medic: Overview</title>",
        "<style>",
        "  body { font-family: monospace; background: #111; color: #ccc; padding: 1.5rem; }",
        "  h1 { color: #fff; }",
        "  h2 { color: #f90; margin-top: 2rem; }",
        "  .generated { color: #666; font-size: .85em; margin-bottom: 1rem; }",
        "  .tz-toggle { margin-bottom: 1.5rem; font-size: .85em; color: #aaa; }",
        "  .tz-toggle label { margin-right: 1rem; cursor: pointer; }",
        "  table { border-collapse: collapse; width: 100%; }",
        "  th { background: #222; color: #aaa; text-align: left; padding: .4rem .8rem; border-bottom: 1px solid #444; cursor: pointer; user-select: none; }",
        "  th:hover { color: #fff; }",
        "  th[data-sort='asc']::after  { content: ' ▲'; color: #f90; }",
        "  th[data-sort='desc']::after { content: ' ▼'; color: #f90; }",
        "  td { padding: .35rem .8rem; border-bottom: 1px solid #2a2a2a; }",
        "  tr:hover td { background: #1a1a1a; }",
        "  .skip { background: #2a1a00; }",
        "  .skip td { color: #f90; }",
        "  .ok { color: #4c4; }",
        "  .bad { color: #f44; }",
        "  .warn { color: #f90; }",
        "  .chart-tip { position:absolute; background:#222; border:1px solid #444; padding:.4rem .6rem; font-size:.8rem; color:#ccc; pointer-events:none; display:none; z-index:10; max-width:320px; }",
        "  .chart-tip strong { color:#fff; }",
        "  .summary-box { background:#1a1a1a; border:1px solid #444; border-radius:3px; padding:.6rem 1rem; margin-bottom:1.2rem; display:inline-block; }",
        "  .summary-box .stat { color:#fff; font-size:.95rem; }",
        "  .summary-box .stat + .stat { margin-top:.2rem; }",
        "</style>",
        "</head>",
        "<body>",
        '<pre style="color:#0ff;line-height:1;margin:0 0 .5rem;font-size:1rem">',
        " ▄▀▄▀▄ ▄▀▄ ▄▀▄ ▄▀█ █▀ █░░ ▄▀▄ ▄█▄    ▄▀▄▀▄ ██▀ ▄▄█ ▀ ▄▀▀",
        " █░▀░█ ▀▄▀ ▀▄▀ █░█ ▄█ █▀█ ▀▄▀ ░█▄    █░▀░█ █▄▄ █▄█ █ ▀▄▄",
        '<span style="color:#888;font-size:.75rem;letter-spacing:.15em"> OVERVIEW</span>',
        "</pre>",
        f'<p class="generated">Generated: <span class="utc-time" data-utc="{now.isoformat()}">{now.strftime("%Y-%m-%d %H:%M:%S UTC")}</span></p>',
        '<div class="tz-toggle">',
        '  <label><input type="radio" name="tz" value="local" checked> Local time</label>',
        '  <label><input type="radio" name="tz" value="utc"> UTC</label>',
        '</div>',
        '<h2>Summary</h2>' if hosts else "",
        ('<div class="summary-box"><span class="stat">'
         + f'{total_resets} reset{"s" if total_resets != 1 else ""} &nbsp;·&nbsp; {unique_hosts} unique host{"s" if unique_hosts != 1 else ""} &nbsp;·&nbsp; {resets_24h} last 24h &nbsp;·&nbsp; {resets_7d} last 7d'
         + (f' &nbsp;·&nbsp; {len(never_reset_hosts)} of {len(fleet_hosts)} {fleet_source} never reset ({never_reset_pct}%)' if never_reset_pct is not None else "")
         + '</span></div>') if hosts else "",
        "<h2>Reset Frequency (last 30 days)</h2>",
        '<div id="reset-chart" style="position:relative;margin-bottom:1.5rem;max-width:860px"></div>',
        f'<script>const RESET_CHART_DATA = {json.dumps(chart_data)};</script>',
    ]

    counts = daily_counts()
    if counts:
        parts.append("<h2>Diagnostics Collected</h2>")
        parts.append("<table>")
        parts.append("  <thead><tr><th>Date</th><th>Hosts collected</th></tr></thead>")
        parts.append("  <tbody>")
        for date_str, count in counts:
            parts.append(f'  <tr><td>{date_str}</td><td class="ok">{count}</td></tr>')
        parts += ["  </tbody>", "</table>"]

    if skipped:
        parts.append('<h2>&#x26A0; Needs Human Attention (currently skipped)</h2>')
        parts.append("<ul>")
        for fqdn, h in sorted(skipped.items()):
            label = short_label(fqdn)
            parts.append(
                f'  <li class="bad"><strong>{label}</strong> ({fqdn}): '
                f'{h["consecutive_reset_failures"]} consecutive failures, '
                f'skip until {fmt(h.get("skip_until"))}, '
                f'last failure: {fmt(h.get("last_failure"))}</li>'
            )
        parts.append("</ul>")

    if hosts:
        parts += [
            "<h2>Host History</h2>",
            '<div id="hist-chart" style="position:relative;margin-bottom:1.5rem;max-width:860px"></div>',
            f'<script>const HIST_DATA = {json.dumps(hist_data)};</script>',
            "<table>",
            "  <thead><tr>",
            "    <th>Host</th><th>Total Resets</th><th>Reset Failures</th>"
            "<th>Consec Reset Fails</th><th>Last Reset Success</th><th>Last Reset Failure</th><th>Skip Until</th>",
            "  </tr></thead>",
            "  <tbody>",
        ]
        for fqdn, h in sorted(hosts.items()):
            label = short_label(fqdn)
            is_skip = fqdn in skipped
            consec = h.get("consecutive_reset_failures", 0)
            row_class = ' class="skip"' if is_skip else ""
            consec_class = ' class="bad"' if consec >= SKIP_THRESHOLD_CONSECUTIVE else (' class="warn"' if consec > 0 else ' class="ok"')
            parts.append(
                f'  <tr{row_class}>'
                f"<td>{label}</td>"
                f'<td class="ok">{h.get("total_resets", 0)}</td>'
                f'<td>{h.get("total_failures", 0)}</td>'
                f"<td{consec_class}>{consec}</td>"
                f"<td>{fmt(h.get('last_success'))}</td>"
                f"<td>{fmt(h.get('last_failure'))}</td>"
                f"<td>{fmt(h.get('skip_until'))}</td>"
                f"</tr>"
            )
        parts += ["  </tbody>", "</table>"]

    action_rows = action_report_rows(hosts)
    if action_rows:
        from html import escape
        parts += ["<h2>Clock Repairs and Diagnostics</h2>",
                  "<p>Clock success means verified clock correction; it does not confirm Taskcluster recovery. "
                  "Counts cover recorded outcomes; historical reset data is unchanged.</p>",
                  "<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in ACTION_REPORT_HEADERS)
                  + "</tr></thead><tbody>"]
        for row in action_rows:
            cells = [fmt(value) if i == 5 else escape(value) for i, value in enumerate(row)]
            parts.append("<tr>" + "".join(f"<td>{value}</td>" for value in cells) + "</tr>")
        parts.append("</tbody></table>")

    parts += [
        "<script>",
        "  function formatTime(isoStr, mode) {",
        "    const d = new Date(isoStr);",
        "    if (mode === 'utc') {",
        "      return isoStr.slice(0, 19).replace('T', ' ') + ' UTC';",
        "    }",
        "    return d.toLocaleString(undefined, {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});",
        "  }",
        "  function updateTimes() {",
        "    const mode = document.querySelector('input[name=\"tz\"]:checked').value;",
        "    document.querySelectorAll('.utc-time').forEach(el => {",
        "      el.textContent = formatTime(el.dataset.utc, mode);",
        "    });",
        "  }",
        "  document.querySelectorAll('input[name=\"tz\"]').forEach(r => r.addEventListener('change', updateTimes));",
        "  updateTimes();",
        "  function cellValue(tr, idx) {",
        "    const el = tr.children[idx];",
        "    const utc = el.querySelector('.utc-time');",
        "    return utc ? utc.dataset.utc : el.textContent.trim();",
        "  }",
        "  function sortTable(th) {",
        "    const table = th.closest('table');",
        "    const tbody = table.querySelector('tbody');",
        "    const idx = Array.from(th.parentElement.children).indexOf(th);",
        "    const asc = th.dataset.sort === 'desc';",
        "    table.querySelectorAll('th').forEach(h => delete h.dataset.sort);",
        "    th.dataset.sort = asc ? 'asc' : 'desc';",
        "    const rows = Array.from(tbody.querySelectorAll('tr'));",
        "    rows.sort((a, b) => {",
        "      const av = cellValue(a, idx), bv = cellValue(b, idx);",
        "      const an = Number(av), bn = Number(bv);",
        "      const cmp = (av !== '' && bv !== '' && !isNaN(an) && !isNaN(bn)) ? an - bn : av.localeCompare(bv);",
        "      return asc ? cmp : -cmp;",
        "    });",
        "    rows.forEach(r => tbody.appendChild(r));",
        "  }",
        "  document.querySelectorAll('th').forEach(th => th.addEventListener('click', () => sortTable(th)));",
        "  (function() {",
        "    const data = RESET_CHART_DATA;",
        "    const W = 600, H = 65, PAD_B = 16, BAR_GAP = 2;",
        "    const maxVal = Math.max(...data.map(d => d.total), 1);",
        "    const barW = (W - BAR_GAP * (data.length - 1)) / data.length;",
        "    const svgNS = 'http://www.w3.org/2000/svg';",
        "    const svg = document.createElementNS(svgNS, 'svg');",
        "    svg.setAttribute('viewBox', `0 0 ${W} ${H + PAD_B}`);",
        "    svg.setAttribute('width', '100%');",
        "    svg.setAttribute('preserveAspectRatio', 'none');",
        "    svg.style.display = 'block';",
        "    data.forEach((d, i) => {",
        "      const barH = d.total > 0 ? Math.max(2, (d.total / maxVal) * H) : 2;",
        "      const x = i * (barW + BAR_GAP);",
        "      const rect = document.createElementNS(svgNS, 'rect');",
        "      rect.setAttribute('x', x);",
        "      rect.setAttribute('y', H - barH);",
        "      rect.setAttribute('width', barW);",
        "      rect.setAttribute('height', barH);",
        "      const hue = d.total > 0 ? Math.round(60 * (1 - d.total / maxVal)) : 0;",
        "      rect.setAttribute('fill', d.total > 0 ? `hsl(${hue},75%,42%)` : '#333');",
        "      rect.dataset.date = d.date;",
        "      rect.dataset.hosts = JSON.stringify(d.hosts);",
        "      rect.dataset.total = d.total;",
        "      svg.appendChild(rect);",
        "      const hit = document.createElementNS(svgNS, 'rect');",
        "      hit.setAttribute('x', x); hit.setAttribute('y', 0);",
        "      hit.setAttribute('width', barW); hit.setAttribute('height', H);",
        "      hit.setAttribute('fill', 'transparent');",
        "      hit.dataset.date = d.date; hit.dataset.hosts = JSON.stringify(d.hosts); hit.dataset.total = d.total;",
        "      svg.appendChild(hit);",
        "      if (i % 5 === 0 || i === data.length - 1) {",
        "        const txt = document.createElementNS(svgNS, 'text');",
        "        txt.setAttribute('x', x + barW / 2);",
        "        txt.setAttribute('y', H + PAD_B - 2);",
        "        txt.setAttribute('text-anchor', 'middle');",
        "        txt.setAttribute('font-size', '9');",
        "        txt.setAttribute('fill', '#666');",
        "        txt.setAttribute('font-family', 'monospace');",
        "        txt.textContent = d.date.slice(5);",
        "        svg.appendChild(txt);",
        "      }",
        "    });",
        "    const tip = document.createElement('div');",
        "    tip.className = 'chart-tip';",
        "    const container = document.getElementById('reset-chart');",
        "    container.appendChild(svg);",
        "    container.appendChild(tip);",
        "    svg.addEventListener('mousemove', e => {",
        "      const rect = e.target.closest('rect');",
        "      if (!rect) { tip.style.display = 'none'; return; }",
        "      const hosts = JSON.parse(rect.dataset.hosts);",
        "      const total = parseInt(rect.dataset.total, 10);",
        "      const sorted = Object.entries(hosts).sort((a, b) => b[1] - a[1]);",
        "      const lines = sorted.map(([h, n]) => n > 1 ? `${h} ×${n}` : h).join(', ');",
        "      tip.innerHTML = `<strong>${rect.dataset.date} — ${total} reset${total !== 1 ? 's' : ''}</strong>${lines ? '<br>' + lines : ''}`;",
        "      const cr = container.getBoundingClientRect();",
        "      const x = e.clientX - cr.left + 8;",
        "      const y = e.clientY - cr.top - 10;",
        "      tip.style.left = (x + 150 > cr.width ? x - 160 : x) + 'px';",
        "      tip.style.top = Math.max(0, y) + 'px';",
        "      tip.style.display = 'block';",
        "    });",
        "    svg.addEventListener('mouseleave', () => { tip.style.display = 'none'; });",
        "  })();",
        "  (function() {",
        "    const data = HIST_DATA;",
        "    if (!data.length) return;",
        "    const W = 600, H = 65, PAD_B = 16, BAR_GAP = 2;",
        "    const maxCount = Math.max(...data.map(d => d.count), 1);",
        "    const maxResets = Math.max(...data.map(d => d.resets), 1);",
        "    const barW = Math.max(6, (W - BAR_GAP * (data.length - 1)) / data.length);",
        "    const svgW = barW * data.length + BAR_GAP * (data.length - 1);",
        "    const svgNS = 'http://www.w3.org/2000/svg';",
        "    const svg = document.createElementNS(svgNS, 'svg');",
        "    svg.setAttribute('viewBox', `0 0 ${svgW} ${H + PAD_B}`);",
        "    svg.setAttribute('width', '100%');",
        "    svg.setAttribute('preserveAspectRatio', 'none');",
        "    svg.style.display = 'block';",
        "    data.forEach((d, i) => {",
        "      const barH = d.count > 0 ? Math.max(2, (d.count / maxCount) * H) : 0;",
        "      const x = i * (barW + BAR_GAP);",
        "      const hue = Math.round(120 * (1 - d.resets / maxResets));",
        "      const fill = `hsl(${hue},65%,42%)`;",
        "      if (barH > 0) {",
        "        const rect = document.createElementNS(svgNS, 'rect');",
        "        rect.setAttribute('x', x);",
        "        rect.setAttribute('y', H - barH);",
        "        rect.setAttribute('width', barW);",
        "        rect.setAttribute('height', barH);",
        "        rect.setAttribute('fill', fill);",
        "        rect.dataset.resets = d.resets;",
        "        rect.dataset.count = d.count;",
        "        rect.dataset.hosts = JSON.stringify(d.hosts);",
        "        svg.appendChild(rect);",
        "      }",
        "      const hit = document.createElementNS(svgNS, 'rect');",
        "      hit.setAttribute('x', x); hit.setAttribute('y', 0);",
        "      hit.setAttribute('width', barW); hit.setAttribute('height', H);",
        "      hit.setAttribute('fill', 'transparent');",
        "      hit.dataset.resets = d.resets; hit.dataset.count = d.count; hit.dataset.hosts = JSON.stringify(d.hosts);",
        "      svg.appendChild(hit);",
        "      const txt = document.createElementNS(svgNS, 'text');",
        "      txt.setAttribute('x', x + barW / 2);",
        "      txt.setAttribute('y', H + PAD_B - 2);",
        "      txt.setAttribute('text-anchor', 'middle');",
        "      txt.setAttribute('font-size', '9');",
        "      txt.setAttribute('fill', '#666');",
        "      txt.setAttribute('font-family', 'monospace');",
        "      txt.textContent = d.resets;",
        "      svg.appendChild(txt);",
        "    });",
        "    const tip = document.createElement('div');",
        "    tip.className = 'chart-tip';",
        "    const container = document.getElementById('hist-chart');",
        "    container.appendChild(svg);",
        "    container.appendChild(tip);",
        "    svg.addEventListener('mousemove', e => {",
        "      const rect = e.target.closest('rect');",
        "      if (!rect) { tip.style.display = 'none'; return; }",
        "      const hosts = JSON.parse(rect.dataset.hosts);",
        "      const count = parseInt(rect.dataset.count, 10);",
        "      const resets = rect.dataset.resets;",
        "      tip.innerHTML = `<strong>${resets} reset${resets !== '1' ? 's' : ''} — ${count} host${count !== 1 ? 's' : ''}</strong><br>` + hosts.join(', ');",
        "      const cr = container.getBoundingClientRect();",
        "      const x = e.clientX - cr.left + 8;",
        "      const y = e.clientY - cr.top - 10;",
        "      tip.style.left = (x + 150 > cr.width ? x - 160 : x) + 'px';",
        "      tip.style.top = Math.max(0, y) + 'px';",
        "      tip.style.display = 'block';",
        "    });",
        "    svg.addEventListener('mouseleave', () => { tip.style.display = 'none'; });",
        "  })();",
        "</script>",
        "</body>", "</html>", "",
    ]

    RESULTS_BASE.mkdir(parents=True, exist_ok=True)
    update_favicon()
    OVERVIEW_HTML_FILE.write_text("\n".join(parts))


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
    result = subprocess.run(
        ["uv", "run", "fleetroll", *args], cwd=FLEETROLL_DIR,
        capture_output=True, text=True, check=True, timeout=timeout,
    )
    if result.stderr.strip():
        warn(f"Fleetroll stderr: {result.stderr.strip()}")
    return result.stdout


def fleetroll_worker_snapshot(host_source: str, *, candidates: bool = False) -> dict:
    args = ["host", "monitor", host_source, "--once", "--json"]
    if candidates:
        args.extend(["--filter-file", "configs/filters/moonshots-hung.yaml"])
    report = json.loads(_fleetroll_command(*args))
    if not isinstance(report, dict):
        raise ValueError("Fleetroll worker snapshot is not a host-keyed object")
    for host, record in report.items():
        if (canonical_moonshot_fqdn(host) != host or not isinstance(record, dict)
                or record.get("host") != host
                or record.get("worker_evidence_schema_version") != WORKER_EVIDENCE_SCHEMA_VERSION):
            raise ValueError(f"Invalid Fleetroll v3 worker evidence for {host!r}")
    return report


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
                               allow_recent_identity: bool = False) -> str | None:
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
                        decisions: dict | None = None) -> list[str]:
    """Refresh exact identities in one batch; errors authorize no host actions."""
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
            _fleetroll_command("gather", "tc", host_file.name, "--use-historical-identity", timeout=300)
            after = fleetroll_worker_snapshot(host_file.name)
        now = datetime.datetime.now(datetime.timezone.utc)
        allowed = []
        for host in eligible:
            problem = automatic_recovery_problem(
                after.get(host, {}), now=now, lookup_started=started,
                freshness_seconds=freshness_seconds,
                max_identity_age_hours=max_identity_age_hours,
                allow_recent_identity=allow_recent_identity,
            )
            if allow_recent_identity and not problem:
                task = after[host]["taskcluster"]["record"]
                if task.get("task_id") and task.get("task_state") not in ("completed", "failed", "exception"):
                    problem = "latest Taskcluster task state is unknown"
            if decisions is not None:
                tc = after.get(host, {}).get("taskcluster", {})
                if not isinstance(tc, dict):
                    tc = {}
                decisions[host] = {
                    "reason": problem or "fresh exact-identity lookup confirms old Taskcluster activity",
                    "taskcluster": {key: tc.get(key) for key in (
                        "identity", "observation_ts", "last_date_active", "latest_lookup_status",
                    )},
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
WORKER_PROCESS_PROBE = r'''
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

if __name__ == "__main__":
    print(json.dumps(collect()))
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


def diagnose_reachable_workers(*, freshness_seconds: int, max_identity_age_hours: int,
                               limit: int, startup_grace_seconds: int) -> dict:
    """A separate, diagnosis-only path. No clock repair, reset, or recovery state writes."""
    raw = json.loads(_fleetroll_command(
        "host", "monitor", "configs/host-lists/linux/all.list", "--once", "--json",
        "--filter", "host~t-linux64-ms tc_act>4h note= sort:host:asc",
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
        reason = None
        if not isinstance(record, dict) or record.get("host") != host:
            reason = "invalid host record"
        elif record.get("ok") is not True:
            reason = "latest host audit did not succeed"
        else:
            try:
                age = (now - _evidence_timestamp(record.get("ts"))).total_seconds()
                if not 0 <= age < freshness_seconds:
                    reason = "successful host audit is stale or future-dated"
            except (ValueError, TypeError, OverflowError):
                reason = "invalid host audit timestamp"
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
    eligible = set(eligible_auto_hosts(
        candidates, freshness_seconds=freshness_seconds,
        max_identity_age_hours=max_identity_age_hours, allow_recent_identity=True, decisions=decisions,
    ))
    for host in candidates:
        if host not in eligible:
            results.append({"host": host, "selection": "deferred",
                            **decisions.get(host, {"reason": "Taskcluster/identity evidence did not pass"})})
            continue
        diagnosis = diagnose_worker(host, startup_grace_seconds=startup_grace_seconds)
        results.append({"host": host, "selection": "diagnosed",
                        "host_audit_ts": raw[host]["ts"],
                        **decisions.get(host, {}), "diagnosis": diagnosis})
    return {"schema_version": 1, "mode": "diagnosis_only",
            "checked_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "results": results}


# --- per-host collection ---

def collect_host(fqdn: str, label: str, out_file: Path) -> bool:
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
                ["ssh"] + SSH_OPTS + [fqdn, "sudo python3 /tmp/moonshot_hang_report.py"],
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


def collect_diagnostics(fqdn: str, label: str, out_file: Path, state: dict) -> bool:
    started = time.monotonic()
    collected = False
    try:
        collected = collect_host(fqdn, label, out_file)
        return collected
    finally:
        record_action(state, fqdn, "diagnostics", collected,
                      duration_seconds=round(time.monotonic() - started, 1),
                      report=str(out_file) if collected else None)
        save_state(state)


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
    parser.add_argument("--diagnose-workers", action="store_true",
                        help="One-shot read-only diagnosis of reachable, TC-inactive workers; requires --auto.")
    parser.add_argument("--diagnosis-limit", type=int, default=10,
                        help="Maximum candidates queried for worker diagnosis (default: 10).")
    parser.add_argument("--startup-grace-minutes", type=int, default=15,
                        help="Minimum uptime before an absent worker is outside startup grace (default: 15).")
    parser.add_argument("-n", "--no-reset", action="store_true",
                        help="Skip iLO reboot.")
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
    global _log_fh, _voice_enabled, _voice_all_hours

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
    if not 0 < args.max_fleet_reset_pct <= 100:
        err("--max-fleet-reset-pct must be greater than 0 and at most 100.")
        sys.exit(1)
    if args.max_fleet_reset_pct > DEFAULT_MAX_FLEET_RESET_PCT and not args.once:
        err(f"Raising --max-fleet-reset-pct above {DEFAULT_MAX_FLEET_RESET_PCT:g}% "
            "requires --once.")
        sys.exit(1)

    if args.update_report:
        state = load_state()
        update_overview_html(state)
        update_overview_md(state)
        info(f"Report updated: {OVERVIEW_HTML_FILE}")
        return

    signal.signal(signal.SIGINT, _sigint_handler)

    print()
    print(_c('1;36', " ▄▀▄▀▄ ▄▀▄ ▄▀▄ ▄▀█ █▀ █░░ ▄▀▄ ▄█▄    ▄▀▄▀▄ ██▀ ▄▄█ ▀ ▄▀▀"))
    print(_c('1;36', " █░▀░█ ▀▄▀ ▀▄▀ █░█ ▄█ █▀█ ▀▄▀ ░█▄    █░▀░█ █▄▄ █▄█ █ ▀▄▄"))
    print()
    say(SCRIPT_VOICE_NAME)

    freshness_mins = args.freshness_requirement if args.freshness_requirement is not None else args.loop_interval
    freshness_label = (f"{freshness_mins}min"
                       if args.freshness_requirement is not None
                       else f"{freshness_mins}min (from loop interval)")

    if args.diagnose_workers:
        if not check_fleetroll_data_freshness(freshness_mins * 60, args.freshness_min_pct):
            err("Fleetroll freshness validation failed; diagnosis skipped.")
            sys.exit(1)
        try:
            report = diagnose_reachable_workers(
                freshness_seconds=freshness_mins * 60,
                max_identity_age_hours=args.max_identity_age_hours,
                limit=args.diagnosis_limit, startup_grace_seconds=args.startup_grace_minutes * 60,
            )
            run_dir = RESULTS_BASE / datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
            run_dir.mkdir(parents=True, exist_ok=True)
            report_path = run_dir / (datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-worker-diagnosis.json")
            report_path.write_text(json.dumps(report, indent=2) + "\n")
            for entry in report["results"]:
                _emit(json.dumps({"event": "medic_worker_diagnosis", **entry}, sort_keys=True))
            info(f"Diagnosis-only report: {report_path}")
            return
        except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
            err(f"Worker diagnosis failed: {exc}")
            sys.exit(1)

    if args.auto and not args.confirm:
        stale_threshold = freshness_mins * 60
        info(f"Checking fleetroll data freshness (max age: {freshness_label})...")
        if not check_fleetroll_data_freshness(stale_threshold, args.freshness_min_pct):
            err("Fleetroll host or Taskcluster data failed freshness validation. "
                "Refresh it before previewing.")
            sys.exit(1)
        info("Fetching bad-host list to preview run...")
        try:
            preview_hosts = list(fleetroll_worker_snapshot(
                "configs/host-lists/linux/all.list", candidates=True,
            ))
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            err(f"Cannot read automatic recovery candidates: {exc}")
            sys.exit(1)
        circuit_ok, preview_hosts, circuit_summary = check_fleet_reset_circuit_breaker(
            preview_hosts, args.max_fleet_reset_pct,
        )
        if not circuit_ok:
            err(circuit_summary)
            say("Moonshot Medic circuit breaker tripped")
            sys.exit(1)
        info(circuit_summary)
        preview_hosts = eligible_auto_hosts(
            preview_hosts, freshness_seconds=stale_threshold,
            max_identity_age_hours=args.max_identity_age_hours,
        )
        n = min(len(preview_hosts), AUTO_BATCH_SIZE)
        print()
        if n:
            warn(f"Auto mode found {len(preview_hosts)} bad host(s); would process {n}:")
            warn(' '.join(preview_hosts[:n]))
        else:
            warn("Auto mode found no bad hosts to process.")
        print()
        warn("This is an automated script that will reboot hosts and collect diagnostics.")
        warn("You must watch and monitor the run. Re-run with --confirm to proceed.")
        sys.exit(1)

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

    last_failed = False
    first_run = True

    while True:
        last_failed = False
        stale = False
        # --- freshness gate ---
        if args.auto:
            stale_threshold = freshness_mins * 60
            info(f"Checking fleetroll data freshness (max age: {freshness_label})...")
            if not check_fleetroll_data_freshness(stale_threshold, args.freshness_min_pct):
                print()
                warn("Fleetroll host or Taskcluster data failed freshness validation "
                     "— will retry next loop.")
                say("Stale Fleetroll data")
                stale = True
                last_failed = True

        # --- resolve host list ---
        if not stale:
            if args.auto:
                info("Fetching bad-host list from fleetroll...")
                try:
                    host_tokens = list(fleetroll_worker_snapshot(
                        "configs/host-lists/linux/all.list", candidates=True,
                    ))
                except (OSError, subprocess.SubprocessError, ValueError) as exc:
                    err(f"Cannot read automatic recovery candidates: {exc}")
                    last_failed = True
                    break
                circuit_ok, hosts, circuit_summary = check_fleet_reset_circuit_breaker(
                    host_tokens, args.max_fleet_reset_pct,
                )
                if not circuit_ok:
                    err(circuit_summary)
                    say("Moonshot Medic circuit breaker tripped")
                    last_failed = True
                    break
                info(circuit_summary)
            elif args.hostname:
                hosts = [worker_fqdn(h) for h in args.hostname]
            else:
                if sys.stdin.isatty():
                    print("Enter hostnames (one per line, Ctrl-D to finish):", file=sys.stderr)
                hosts = [worker_fqdn(line.strip()) for line in sys.stdin if line.strip()]

            if not hosts:
                warn("No bad hosts found." if args.auto else "No hosts specified. Nothing to do.")
                if not args.auto:
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
                        elif is_skipped(state, fqdn):
                            skip_until = state["hosts"].get(fqdn, {}).get("skip_until", "")
                            warn(f"Skipping {label}: {SKIP_THRESHOLD_CONSECUTIVE}+ consecutive reset failures "
                                 f"(skip until {skip_until[:16]}Z — use --ignore-recency to override)")
                            skipped.append(h)
                        else:
                            filtered.append(h)
                    if skipped:
                        warn(f"Skipping {len(skipped)} host(s): {' '.join(short_label(h) for h in skipped)}")
                    hosts = filtered

                if not hosts:
                    info("All requested hosts were recently processed. Nothing to do.")
                else:
                    batches = [hosts[i:i + AUTO_BATCH_SIZE] for i in range(0, len(hosts), AUTO_BATCH_SIZE)]
                    n_total = len(hosts)
                    info(f"Hosts to process: {n_total} host(s) across {len(batches)} batch(es) of up to {AUTO_BATCH_SIZE}")
                    say(f"Starting run. {n_total} host{'s' if n_total != 1 else ''} detected.")

                    if first_run:
                        warn("Starting in 15 seconds — press Ctrl-C to abort.")
                        for _ in range(15):
                            if _interrupt_count:
                                break
                            time.sleep(1)
                        if _interrupt_count:
                            warn("Aborted before first run.")
                            break

                    # --- create results dir and open log ---
                    run_dir = RESULTS_BASE / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
                    run_dir.mkdir(parents=True, exist_ok=True)
                    log_path = run_dir / "run.log"
                    info(f"Results will be saved to: {run_dir}")
                    info(f"Log: {log_path}")

                    _log_fh = log_path.open("a")
                    try:
                        run_started = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        _emit(f"Run started at {run_started}")
                        _emit(f"Hosts: {' '.join(hosts)}")
                        _emit("")

                        if args.no_reset:
                            warn("Skipping reset (--no-reset).")

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
                                batch = eligible_auto_hosts(
                                    batch, freshness_seconds=stale_threshold,
                                    max_identity_age_hours=args.max_identity_age_hours,
                                )
                                if not batch:
                                    continue
                            if not args.no_reset:
                                authorize_reset = None
                                if args.auto:
                                    authorize_reset = lambda pending: eligible_auto_hosts(
                                        pending, freshness_seconds=stale_threshold,
                                        max_identity_age_hours=args.max_identity_age_hours,
                                    )
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
                        _emit(f"{_c('1', 'Run complete:')} {run_dir}")
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
                    finally:
                        if _log_fh:
                            _log_fh.close()
                            _log_fh = None

        first_run = False

        if not args.auto or args.once or _interrupt_count:
            break

        info(f"Next run in {args.loop_interval} minute{'s' if args.loop_interval != 1 else ''}. "
             f"Press Ctrl-C to stop.")
        for _ in range(args.loop_interval * 60):
            if _interrupt_count:
                break
            time.sleep(1)

        if _interrupt_count:
            break

        print()

    sys.exit(1 if last_failed else 0)


if __name__ == "__main__":
    main()
