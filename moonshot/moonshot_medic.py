#!/usr/bin/env python3
"""Automated triage for hung t-linux64-ms-* Moonshot cartridges: resets via iLO, collects hang diagnostics, and tracks repeat offenders.

Usage:
  moonshot_medic.py [--auto] [--no-reset] [--no-freshness] [--ignore-recency] [HOST ...]

  --auto                    Fetch bad-host list from fleetroll instead of reading argv/stdin (requires --confirm).
  --no-reset                Skip iLO reboot (host already freshly rebooted).
  --freshness-requirement   Max acceptable age of fleetroll data in minutes (default: loop-interval).
  --ignore-recency          Process hosts even if collected within the last RECENCY_MINUTES minutes.
  HOST ...                  Short (ms025) or FQDN. If omitted and not --auto, reads stdin.
"""

import argparse
import datetime
import json
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

FLEETROLL_DIR = Path.home() / "git/fleetroll_mvp"
SCRIPT_DIR = Path(__file__).parent.resolve()
RESET_DIR = SCRIPT_DIR
RESULTS_BASE = SCRIPT_DIR / "moonshot_debugging_results"
HANG_SCRIPT = SCRIPT_DIR / "moonshot_hang_report.py"
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
MOONSHOT_HOST_RE = re.compile(r'^t-linux64-ms-\d+\.test\.releng\.mdc[12]\.mozilla\.com$', re.IGNORECASE)
MOONSHOT_OBSERVED_FILTER = "host~t-linux64-ms os=L sort:host:asc"

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


# --- SSH probe ---

def ssh_is_online(fqdn: str, timeout: int = 10) -> bool:
    try:
        sock = socket.create_connection((fqdn, 22), timeout=timeout)
        sock.close()
        return True
    except (socket.timeout, OSError):
        return False


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


def record_reset_success(state: dict, fqdn: str) -> None:
    h = _host_entry(state, fqdn)
    h["consecutive_reset_failures"] = 0
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    h["last_success"] = now_iso
    h["total_resets"] = h.get("total_resets", 0) + 1
    h["skip_until"] = None
    h.setdefault("reset_timestamps", []).append(now_iso)


def record_reset_failure(state: dict, fqdn: str) -> None:
    h = _host_entry(state, fqdn)
    h["consecutive_reset_failures"] = h.get("consecutive_reset_failures", 0) + 1
    h["last_failure"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    h["total_failures"] = h.get("total_failures", 0) + 1
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
        "uv", "run", "fleetroll", "host-monitor", str(host_list),
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


def _resets_since(hosts: dict, hours: int) -> int:
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)).isoformat()
    return sum(
        sum(1 for ts in h.get("reset_timestamps", []) if ts >= cutoff)
        for h in hosts.values()
    )


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
        lines += ["## Daily Activity", ""]
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
            "| Host | Total Resets | Failures | Consec Fails | Last Success | Last Failure | Skip Until |",
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
        parts.append("<h2>Daily Activity</h2>")
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
            "    <th>Host</th><th>Total Resets</th><th>Failures</th>"
            "<th>Consec Fails</th><th>Last Success</th><th>Last Failure</th><th>Skip Until</th>",
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


def say(msg: str) -> None:
    if not _voice_enabled:
        return
    now = datetime.datetime.now()
    if not _voice_all_hours and not (now.weekday() < 5 and VOICE_HOUR_START <= now.hour < VOICE_HOUR_END):
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
        section(f"{label}  host-audit")
        audit = capture(["uv", "run", "fleetroll", "host-audit", fqdn], cwd=FLEETROLL_DIR, check=False)
        with tmp_file.open("a") as f:
            f.write("\n---\n\n# Fleetroll Host Audit\n\n```\n")
            f.write(audit)
            f.write("\n```\n")
        tmp_file.rename(out_file)
        ok(f"[{label}] -> {out_file}")

    return host_ok


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
                        help=f"Speak outside working hours ({VOICE_HOUR_START}:00–before {VOICE_HOUR_END}:00).")
    parser.add_argument("-l", "--loop-interval", type=int, default=15, metavar="MINUTES",
                        help="Minutes to sleep between auto runs (default: 15).")
    parser.add_argument("--freshness-min-pct", type=int, default=FRESHNESS_MIN_PCT, metavar="PCT",
                        help=f"Minimum %% of hosts with fresh fleetroll data required (default: {FRESHNESS_MIN_PCT}).")
    parser.add_argument("--update-report", action="store_true",
                        help="Regenerate OVERVIEW.html and OVERVIEW.md from existing state, then exit.")
    return parser.parse_args()


def main() -> None:
    global _log_fh, _voice_enabled, _voice_all_hours

    args = parse_args()
    _voice_enabled = not args.no_voice
    _voice_all_hours = args.voice_all_hours

    if args.loop_interval < 1:
        err("--loop-interval must be at least 1 minute.")
        sys.exit(1)
    if args.freshness_requirement is not None and args.freshness_requirement < 1:
        err("--freshness-requirement must be at least 1 minute.")
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

    if args.auto and not args.confirm:
        stale_threshold = freshness_mins * 60
        info(f"Checking fleetroll data freshness (max age: {freshness_label})...")
        r = subprocess.run(["uv", "run", "fleetroll", "data-freshness", "configs/host-lists/linux/all.list", "--stale-threshold", str(stale_threshold), "--min-fresh-pct", str(args.freshness_min_pct)],
                           cwd=FLEETROLL_DIR, capture_output=True, text=True)
        freshness_out = (r.stdout + r.stderr).strip()
        for line in freshness_out.splitlines():
            _emit(f"  {_c('2', re.sub(r'^=+>\s*', '', line))}")
        if r.returncode != 0:
            err(f"Fleetroll data is stale (older than {stale_threshold}s). Refresh it before previewing.")
            sys.exit(1)
        info("Fetching bad-host list to preview run...")
        raw, raw_err = capture_stdout(["bash", "tools/list_bad_linux_hosts.sh"], cwd=FLEETROLL_DIR, check=False)
        if raw_err:
            warn(f"Ignoring stderr from fleetroll bad-host list: {raw_err}")
        preview_hosts, ignored_hosts = parse_bad_hosts(raw)
        if ignored_hosts:
            warn(f"Ignoring {len(ignored_hosts)} non-host token(s) from fleetroll stdout: {' '.join(ignored_hosts)}")
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

    last_failed = False
    first_run = True

    while True:
        last_failed = False
        stale = False
        # --- freshness gate ---
        if args.auto:
            stale_threshold = freshness_mins * 60
            info(f"Checking fleetroll data freshness (max age: {freshness_label})...")
            r = subprocess.run(["uv", "run", "fleetroll", "data-freshness", "configs/host-lists/linux/all.list", "--stale-threshold", str(stale_threshold), "--min-fresh-pct", str(args.freshness_min_pct)],
                               cwd=FLEETROLL_DIR, capture_output=True, text=True)
            freshness_out = (r.stdout + r.stderr).strip()
            for line in freshness_out.splitlines():
                _emit(f"  {_c('2', re.sub(r'^=+>\s*', '', line))}")
            if r.returncode != 0:
                print()
                warn(f"Fleetroll data is stale (older than {stale_threshold}s) — will retry next loop.")
                say("Stale Fleetroll data")
                stale = True
                last_failed = True

        # --- resolve host list ---
        if not stale:
            if args.auto:
                info("Fetching bad-host list from fleetroll...")
                raw, raw_err = capture_stdout(["bash", "tools/list_bad_linux_hosts.sh"], cwd=FLEETROLL_DIR, check=False)
                if raw_err:
                    warn(f"Ignoring stderr from fleetroll bad-host list: {raw_err}")
                host_tokens, ignored_hosts = parse_bad_hosts(raw)
                if ignored_hosts:
                    warn(f"Ignoring {len(ignored_hosts)} non-host token(s) from fleetroll stdout: {' '.join(ignored_hosts)}")
                hosts = [worker_fqdn(h) for h in host_tokens]
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
                        _emit(f"Run started at {datetime.datetime.now(datetime.timezone.utc).isoformat()}")
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
                            if not args.no_reset:
                                info("Resetting hosts via iLO (waiting for them to come back online)...")
                                run(["uv", "run", "./reset_moonshot.py", "--force"] + batch,
                                    cwd=RESET_DIR, check=False)
                                print()
                                online_hosts = []
                                for host in batch:
                                    fqdn = worker_fqdn(host)
                                    label = short_label(host)
                                    if ssh_is_online(fqdn):
                                        online_hosts.append(host)
                                        record_reset_success(state, fqdn)
                                    else:
                                        err(f"[{label}] did not come back online — skipping collection")
                                        record_reset_failure(state, fqdn)
                                        reset_fail_labels.append(label)
                                save_state(state)
                                update_overview_md(state)
                                update_overview_html(state)
                                batch = online_hosts

                            fail_hosts.extend(reset_fail_labels)

                            # --- collect batch ---
                            for host in batch:
                                fqdn = worker_fqdn(host)
                                label = short_label(host)
                                ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                out_file = run_dir / f"{ts}-{label}.md"

                                try:
                                    if collect_host(fqdn, label, out_file):
                                        ok_hosts.append(label)
                                    else:
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

        if not args.auto or _interrupt_count:
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
