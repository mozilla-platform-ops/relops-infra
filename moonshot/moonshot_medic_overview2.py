"""Generate the standalone, local OVERVIEW2.html report from saved Medic data."""

import datetime as dt
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote

import moonshot_medic as medic

HISTORY_DAYS = 30
OUTPUT_NAME = "OVERVIEW2.html"
TEMPLATE = Path(__file__).with_suffix(".html")
ACTION_NAMES = {
    "reset": "iLO reset",
    "clock_fix": "Clock repair",
    "diagnostics": "Collect diagnostics",
    "ssh_reboot": "SSH reboot",
    "gdm_restart": "GDM restart",
}
DIAGNOSIS_NAMES = {
    "deferred": "Deferred",
    "absent": "Worker absent",
    "live": "Worker live",
    "not_zombie": "No zombie found",
    "zombie": "Confirmed zombie",
    "gdm_autologin_stall": "GDM autologin stall",
    "task_resolution_stall": "Task resolution stall",
    "worker_oom": "Worker OOM",
    "unverified": "Unverified",
}
OUTCOMES = {
    "failed": ("Failed", "failure"),
    "deferred": ("Deferred", "attention"),
    "unverified": ("Unverified", "attention"),
    "recovered": ("Worker recovery verified", "success"),
    "rebooted": ("Reboot verified", "success"),
    "saved": ("Diagnostics saved", "success"),
}
VERIFIED_ACTIONS = {"iLO reset": "SSH restored", "Clock repair": "Clock repair verified"}
CHECK_2_ACTIONS = {"SSH reboot", "GDM restart"}
SCAN_CHECKS = {"confirmed auto": "Check 1", "manual recovery": "Manual"}


@dataclass(frozen=True)
class Evidence:
    label: str
    href: str


@dataclass
class Event:
    time: str
    action: str
    outcome: str
    tone: str
    reason: str = ""
    links: list[Evidence] = field(default_factory=list)
    check: str = ""
    trigger: str = ""
    time_note: str = ""


@dataclass
class Scan:
    start: str
    mode: str
    end: str = ""
    health: str = ""
    findings: list[dict] = field(default_factory=list)


def utc_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        stamp = dt.datetime.fromisoformat(value)
    except ValueError:
        return ""
    return stamp.replace(tzinfo=stamp.tzinfo or dt.UTC).astimezone(dt.UTC).isoformat(timespec="microseconds")


def read_json(path: Path) -> dict:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return content if isinstance(content, dict) else {}


def evidence_link(root: Path, filename: str | Path | None) -> list[Evidence]:
    if not filename:
        return []
    path = Path(filename)
    # Saved state can contain an absolute path from an earlier checkout.
    if path.is_absolute():
        if path.is_relative_to(root):
            path = path.relative_to(root)
        elif root.name in path.parts:
            path = Path(*path.parts[path.parts.index(root.name) + 1:])
        else:
            return []
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return []
    label = {
        "run.log": "Scan log",
        "state.json": "Medic state",
    }.get(path.name, "Host audit" if path.suffix == ".md" else "Worker diagnosis")
    return [Evidence(label, quote(target.relative_to(root).as_posix()))]


def action_outcome(action: str, outcome: str) -> tuple[str, str]:
    if outcome == "verified":
        return VERIFIED_ACTIONS.get(action, "Verified"), "success"
    return OUTCOMES.get(outcome, (outcome.replace("_", " ").capitalize(), "neutral"))


def recorded_event(root: Path, item: dict) -> Event | None:
    time = utc_time(item.get("timestamp"))
    key = item.get("action")
    if not time or not key:
        return None
    action = ACTION_NAMES.get(key, key.replace("_", " ").capitalize())
    result = item.get("outcome") or (
        ("saved" if key == "diagnostics" else "verified") if item.get("success") else "failed"
    )
    label, tone = action_outcome(action, result)
    trigger = DIAGNOSIS_NAMES.get(item.get("trigger"), "")
    reason = ""
    if key == "clock_fix" and item.get("skew_seconds") is not None:
        reason = f"Initial skew {item['skew_seconds']}s"
        if item.get("verified_skew_seconds") is not None:
            reason += f" · verified skew {item['verified_skew_seconds']}s"
    check = "Check 2" if action in CHECK_2_ACTIONS else item.get("check", "")
    return Event(time, action, label, tone, reason, evidence_link(root, item.get("report")),
                 check=check, trigger=trigger)


def state_events(root: Path, host: dict) -> list[Event]:
    # Older states retain reset timestamps and last outcomes without typed history.
    events = {}
    recorded = [
        *host.get("action_history", []),
        *host.get("last_actions", {}).values(),
        *[{"timestamp": stamp, "action": "reset", "success": True}
          for stamp in host.get("reset_timestamps", [])],
        {"timestamp": host.get("last_success"), "action": "reset", "success": True},
        {"timestamp": host.get("last_failure"), "action": "reset", "success": False},
    ]
    if clock := host.get("last_clock_fix"):
        recorded.append({"action": "clock_fix", **clock})
    for item in recorded:
        if event := recorded_event(root, item):
            events.setdefault((event.time[:19], event.action, event.outcome), event)
    return list(events.values())


def scan_log_records(root: Path, log: Path, resolve_host: dict) -> tuple[list[Scan], Scan | None, list]:
    scans = []
    events = []
    pending = None
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return scans, pending, events
    for line in lines:
        if line.startswith("Scan started at "):
            stamp, _, mode = line.removeprefix("Scan started at ").partition(" | ")
            pending = Scan(utc_time(stamp), mode)
        elif pending and line.startswith("Scan ended at "):
            pending.end = utc_time(line.removeprefix("Scan ended at "))
            if pending.start and pending.end >= pending.start:
                scans.append(pending)
            pending = None
        elif pending and (marker := "Check 2 — Fleetroll health: ") in line:
            pending.health = line.partition(marker)[2]
        elif line.startswith("{"):
            try:
                action_record = json.loads(line)
            except ValueError:
                continue
            if action_record.get("event") != "medic_action":
                continue
            fqdn = medic.canonical_moonshot_fqdn(action_record.get("host", ""))
            if fqdn and (event := recorded_event(root, action_record)):
                event.links = event.links or evidence_link(root, log)
                events.append((fqdn, event))
        else:
            fields = line.split(" | ", 5)
            if len(fields) != 6 or fields[1] not in ("Check 1", "Check 2", "Manual"):
                continue
            stamp, check, short, action, outcome, detail = fields
            time = utc_time(stamp)
            if time and (fqdn := resolve_host.get(short) or medic.canonical_moonshot_fqdn(short)):
                reason, _, path = detail.partition(" | Evidence: ")
                action = {"Clock fix": "Clock repair"}.get(action, action)
                label, tone = action_outcome(action, outcome)
                links = evidence_link(root, path) or evidence_link(root, log)
                events.append((fqdn, Event(time, action, label, tone, reason, links, check=check)))
    return scans, pending, events


def diagnosis_events(root: Path, report: Path, data: dict, time: str, scan: Scan | None,
                     history: dict[str, list[Event]]) -> list[dict]:
    events = []
    links = evidence_link(root, report)
    for entry in data.get("results", []):
        fqdn = medic.canonical_moonshot_fqdn(entry.get("host", ""))
        if not fqdn:
            continue
        diagnosis = entry.get("diagnosis") or {}
        state = diagnosis.get("worker_state", entry.get("selection", "unverified"))
        reason = diagnosis.get("reason") or entry.get("reason", "")
        label = DIAGNOSIS_NAMES.get(state, state.replace("_", " ").capitalize())
        tone = "neutral" if state in ("live", "not_zombie") else "attention"
        check = "Check 2" if scan and scan.mode in ("confirmed auto", "recovery preview") else ""
        event = Event(time, "Worker diagnosis", label, tone, reason, links, check=check)
        history[fqdn].append(event)
        finding = asdict(event)
        if recovery := entry.get("recovery"):
            action = "GDM restart" if state == "gdm_autologin_stall" else "SSH reboot"
            outcome, outcome_tone = action_outcome(action, recovery["outcome"])
            fallback = Event(scan.end if scan else time, action, outcome, outcome_tone, recovery["reason"], links,
                             check="Check 2", trigger=label,
                             time_note="Recorded by scan end" if scan else "Diagnosis time · action time unrecorded")
            match = next((existing for existing in sorted(history[fqdn], key=lambda item: item.time)
                          if existing.action == action and existing.outcome == outcome
                          and time[:19] <= existing.time[:19]
                          and (existing.time <= scan.end if scan else existing.time[:10] == time[:10])), None)
            if match:
                match.trigger = label
                match.reason = fallback.reason
                match.links = list(dict.fromkeys([*match.links, *links]))
            else:
                history[fqdn].append(fallback)
                match = fallback
            finding = asdict(match)
        events.append({"host": fqdn, **finding})
    return events


def merge_action(events: list[Event], new: Event) -> None:
    # Intervention lines record the same actions as state, with a reason and rounded time.
    for index in range(len(events) - 1, -1, -1):
        old = events[index]
        if old.action == new.action and old.outcome == new.outcome and old.time[:19] == new.time[:19]:
            new.time = max(old.time, new.time)
            new.links = list(dict.fromkeys([*old.links, *new.links]))
            new.trigger = new.trigger or old.trigger
            new.check = new.check or old.check
            new.reason = new.reason or old.reason
            events[index] = new
            return
    events.append(new)


def recovery_category(event: Event) -> str:
    if event.outcome != "Deferred":
        if event.action == "iLO reset":
            return "ilo"
        if event.check == "Check 2" and event.action in CHECK_2_ACTIONS:
            return "remediation"
    return ""


def build_report(root: Path, state: dict, inventory: set[str], now: dt.datetime) -> dict:
    hosts = state.get("hosts", {})
    history = defaultdict(list, {host: state_events(root, value) for host, value in hosts.items()})
    resolve_host = {medic.short_label(host): host for host in sorted(hosts.keys() | inventory)}
    first_day = now.date() - dt.timedelta(days=HISTORY_DAYS - 1)
    earliest = now - dt.timedelta(days=HISTORY_DAYS)
    cutoff = utc_time(earliest.isoformat())
    current_time = utc_time(now.isoformat())
    latest_scan = None
    all_scans = []
    reports = []
    daily = []
    for day in sorted(root.iterdir()):
        if not (day.is_dir() and len(day.name) == 8 and day.name.isdecimal()):
            continue
        count = sum(1 for _ in day.glob("*.md"))
        if count:
            daily.append({"date": f"{day.name[:4]}-{day.name[4:6]}-{day.name[6:]}", "count": count})
        if day.name < earliest.strftime("%Y%m%d"):
            continue
        scans, pending, interventions = scan_log_records(root, day / "run.log", resolve_host)
        for fqdn, event in interventions:
            merge_action(history[fqdn], event)
        for report in sorted(day.glob("*-worker-diagnosis.json")):
            data = read_json(report)
            time = utc_time(data.get("checked_at"))
            if not time or (pending and pending.start and time >= pending.start):
                continue
            reports.append((report, data, time))
        all_scans.extend(scans)
        for scan in scans:
            if scan.mode == "confirmed auto" and (latest_scan is None or scan.end > latest_scan.end):
                latest_scan = scan
    for report, data, time in reports:
        scan = next((scan for scan in reversed(all_scans) if scan.start <= time <= scan.end), None)
        findings = diagnosis_events(root, report, data, time, scan, history)
        if scan:
            scan.findings.extend(findings)

    days = [(first_day + dt.timedelta(days=offset)).isoformat() for offset in range(HISTORY_DAYS)]
    trend = {day: Counter() for day in days}
    distribution = Counter()
    rows = []
    rolling = {hours: utc_time((now - dt.timedelta(hours=hours)).isoformat()) for hours in (24, 168)}
    older_than_7d = rolling[168]
    counts = {24: Counter(), 168: Counter()}
    for fqdn in sorted(hosts.keys() | history.keys() | inventory):
        host = hosts.get(fqdn, {})
        label = medic.short_label(fqdn)
        distribution[host.get("total_resets", 0)] += 1

        events = sorted(history[fqdn], key=lambda event: event.time, reverse=True)
        recent_counts = Counter()
        last_recovery = {}
        for event in events:
            if not event.check and event.action in ("iLO reset", "Clock repair"):
                sources = {SCAN_CHECKS[scan.mode] for scan in all_scans
                           if scan.mode in SCAN_CHECKS and scan.start <= event.time <= scan.end}
                if len(sources) == 1:
                    event.check = sources.pop()
            if not (category := recovery_category(event)):
                continue
            last_recovery.setdefault(category, event)
            if event.time < cutoff:
                continue
            recent_counts[category] += 1
            for hours, since in rolling.items():
                if event.time >= since:
                    counts[hours][category] += 1
            if event.time[:10] in trend:
                trend[event.time[:10]][fqdn, label, event.action, event.check, event.trigger, category] += 1

        last_action = next((event for event in events if event.action != "Worker diagnosis"), None)
        failure = next((event for event in events if event.tone == "failure" and event.time >= older_than_7d), None)
        until = utc_time(host.get("skip_until"))
        streak = host.get("consecutive_reset_failures", 0)
        backoff = until if until > current_time else ""
        backoff_reason = ""
        if backoff:
            last_reset_failure = next((e for e in events if e.action == "iLO reset" and e.tone == "failure"), None)
            backoff_reason = f"{streak} consecutive reset failure{'s' if streak != 1 else ''}"
            if last_reset_failure and last_reset_failure.reason:
                backoff_reason += f" · {last_reset_failure.reason}"
        rows.append({
            "host": fqdn, "label": label, "backoff": backoff, "backoffReason": backoff_reason,
            "latest": asdict(events[0]) if events else None,
            "lastAction": asdict(last_action) if last_action else None,
            "recentFailure": bool(failure), "failureStreak": streak,
            "resets30": recent_counts["ilo"], "remediations30": recent_counts["remediation"],
            "lastReset": asdict(last_recovery["ilo"]) if "ilo" in last_recovery else None,
            "lastRemediation": asdict(last_recovery["remediation"]) if "remediation" in last_recovery else None,
            "events": [asdict(event) for event in events if event.time >= cutoff],
        })
    return {
        "generated": utc_time(now.isoformat()),
        "hosts": rows,
        "stats": {"backoff": sum(bool(row["backoff"]) for row in rows),
                  "resets24": counts[24]["ilo"], "resets7": counts[168]["ilo"],
                  "remediations24": counts[24]["remediation"], "remediations7": counts[168]["remediation"],
                  "recentFailures": sum(row["recentFailure"] for row in rows)},
        "latestScan": asdict(latest_scan) if latest_scan else None,
        "trend": [
            {"date": day, "total": trend[day].total(), "actions": [
                {"host": fqdn, "label": label, "action": action, "check": check,
                 "trigger": trigger, "category": category, "count": count}
                for (fqdn, label, action, check, trigger, category), count in sorted(trend[day].items())
            ]}
            for day in days
        ],
        "distribution": sorted(distribution.items()),
        "daily": daily[::-1],
    }


def generate_report(root: Path, inventory: set[str]) -> Path:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    data = build_report(root, read_json(root / "state.json"), inventory, dt.datetime.now(dt.UTC))
    # JSON lives inside a raw-text element. In particular, saved messages cannot close it.
    payload = json.dumps(data, ensure_ascii=False).replace("<", r"\u003c")
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("<!-- MEDIC LOGO -->", escape("\n".join(medic.MEDIC_LOGO)))
    html = html.replace("<!-- REPORT DATA -->", payload)
    path = root / OUTPUT_NAME
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete_on_close=False) as draft:
        draft.write(html)
        draft.close()
        Path(draft.name).replace(path)
    return path


if __name__ == "__main__":
    print(generate_report(medic.RESULTS_BASE, medic._configured_linux_moonshot_hosts()))
