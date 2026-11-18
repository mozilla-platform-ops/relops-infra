import datetime as dt
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import moonshot_medic_overview2 as overview

HOST = "t-linux64-ms-126.test.releng.mdc1.mozilla.com"


def test_report_combines_saved_actions_with_completed_scan_findings(tmp_path):
    day = tmp_path / "20261118"
    day.mkdir()
    audit = day / "worker-audit.md"
    audit.write_text("# Host audit")
    log = day / "run.log"
    log.write_text(
        "Scan started at 2026-11-18T12:00:00Z | confirmed auto\n"
        "  Check 2 — Fleetroll health: 1 host matched\n"
        f"2026-11-18T12:00:02Z | Check 1 | ms126 | Collect diagnostics | saved | Collected | Evidence: {audit}\n"
        "Scan ended at 2026-11-18T12:00:09Z\n"
        "Scan started at 2026-11-18T13:00:00Z | confirmed auto\n"
    )
    for hour in (12, 13):
        report = day / f"{hour}-worker-diagnosis.json"
        report.write_text(json.dumps({
            "checked_at": f"2026-11-18T{hour}:00:06Z",
            "results": [{"host": HOST, "selection": "deferred", "reason": "Latest host audit failed"}],
        }))

    host = {
        "total_resets": 1, "reset_timestamps": ["2026-11-17T15:00:00Z"],
        "last_failure": "2026-11-18T10:00:00Z",
        "consecutive_reset_failures": 3, "skip_until": "2026-11-18T16:00:00Z",
        "action_history": [{
            "timestamp": "2026-11-18T12:00:02.123456Z", "action": "diagnostics",
            "success": True, "report": str(audit),
        }],
    }
    now = dt.datetime(2026, 11, 18, 14, tzinfo=dt.UTC)
    result = overview.build_report(tmp_path, {"hosts": {HOST: host}}, {HOST}, now)
    row, = result["hosts"]
    assert (row["label"], row["resets30"], row["failureStreak"]) == ("ms126", 2, 3)
    assert result["stats"] == {"backoff": 1, "recentFailures": 1, "resets24": 2, "resets7": 2,
                               "remediations24": 0, "remediations7": 0}
    assert row["backoffReason"] == "3 consecutive reset failures"
    assert row["latest"]["outcome"] == "Deferred"
    assert row["lastAction"]["reason"] == "Collected"
    assert row["lastAction"]["links"] == [{"label": "Host audit", "href": "20261118/worker-audit.md"}]
    assert sum(event["action"] == "Collect diagnostics" for event in row["events"]) == 1
    assert sum(event["action"] == "Worker diagnosis" for event in row["events"]) == 1
    scan = result["latestScan"]
    assert scan["health"] == "1 host matched"
    finding, = scan["findings"]
    assert finding["reason"] == "Latest host audit failed"
    assert finding["links"][0]["href"] == "20261118/12-worker-diagnosis.json"
    assert result["trend"][-2]["actions"] == [
        {"host": HOST, "label": "ms126", "action": "iLO reset", "check": "", "trigger": "",
         "category": "ilo", "count": 1},
    ]


def test_report_distinguishes_ilo_resets_and_check_2_remediation(tmp_path):
    day = tmp_path / "20261118"
    day.mkdir()
    (day / "run.log").write_text(
        f'{{"event": "medic_action", "host": "{HOST}", "action": "reset", '
        '"timestamp": "2026-11-18T09:00:00Z", "success": false}\n'
        "Scan started at 2026-11-18T12:00:00Z | confirmed auto\n"
        "2026-11-18T12:01:02Z | Check 1 | ms126 | iLO reset | verified | SSH responded\n"
        "2026-11-18T12:04:00Z | Check 2 | ms126 | SSH reboot | recovered | workerReady observed\n"
        "2026-11-18T12:05:00Z | Check 2 | ms126 | GDM restart | deferred | worker became busy\n"
        "Scan ended at 2026-11-18T12:10:00Z\n"
        "Scan started at 2026-11-18T13:00:00Z | manual recovery\n"
        "Scan ended at 2026-11-18T13:10:00Z\n"
    )
    other_host = "t-linux64-ms-274.test.releng.mdc1.mozilla.com"
    (day / "20261118T120200Z-worker-diagnosis.json").write_text(json.dumps({
        "checked_at": "2026-11-18T12:02:00Z",
        "results": [
            {"host": HOST, "diagnosis": {"worker_state": "worker_oom"},
             "recovery": {"outcome": "recovered", "reason": "Fresh Taskcluster contact"}},
            {"host": other_host, "diagnosis": {"worker_state": "gdm_autologin_stall"},
             "recovery": {"outcome": "unverified", "reason": "Worker readiness could not be verified"}},
        ],
    }))
    state = {"hosts": {HOST: {
        "total_resets": 3,
        "reset_timestamps": ["2026-11-18T11:00:00Z", "2026-11-18T12:01:02.001000Z", "2026-11-18T13:05:00Z"],
        "action_history": [{"action": "ssh_reboot", "timestamp": "2026-11-18T12:04:00.123456Z",
                             "outcome": "recovered", "success": True}],
    }}}
    report = overview.build_report(tmp_path, state, {HOST, other_host},
                                   dt.datetime(2026, 11, 18, 14, tzinfo=dt.UTC))
    host, other = report["hosts"]
    assert (host["resets30"], host["remediations30"]) == (4, 1)
    assert [event["check"] for event in host["events"] if event["action"] == "iLO reset"] == [
        "Manual", "Check 1", "", "",
    ]
    last_remediation = host["lastRemediation"]
    assert last_remediation["check"] == "Check 2"
    assert last_remediation["action"] == "SSH reboot"
    assert last_remediation["trigger"] == "Worker OOM"
    assert last_remediation["time"] == "2026-11-18T12:04:00.123456+00:00"
    assert other["lastRemediation"]["action"] == "GDM restart"
    assert other["lastRemediation"]["tone"] == "attention"
    assert other["lastRemediation"]["time_note"] == "Recorded by scan end"
    assert report["stats"]["remediations24"] == 2
    assert report["trend"][-1]["total"] == 6
    assert {item["action"] for item in report["trend"][-1]["actions"]} == {"iLO reset", "SSH reboot", "GDM restart"}


def test_generator_writes_a_separate_html_snapshot(tmp_path):
    original = tmp_path / "OVERVIEW.html"
    original.write_text("original")
    path = overview.generate_report(tmp_path, {HOST})

    class SnapshotData(HTMLParser):
        inside_data = False
        payload = ""

        def handle_starttag(self, tag, attrs):
            self.inside_data = tag == "script" and dict(attrs).get("id") == "report-data"

        def handle_data(self, data):
            if self.inside_data:
                self.payload += data

    parser = SnapshotData()
    html = path.read_text()
    parser.feed(html)
    data = json.loads(parser.payload)
    assert path.name == "OVERVIEW2.html"
    assert '<link rel="icon" href="favicon.svg" type="image/svg+xml">' in html
    assert (tmp_path / "favicon.svg").read_text().startswith("<svg")
    assert original.read_text() == "original"
    assert data["hosts"][0]["host"] == HOST
    assert data["hosts"][0]["latest"] is None
