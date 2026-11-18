import argparse
import datetime
import io
import json
import subprocess
import sys

import pytest

import moonshot_medic as medic
import moonshot_task_stall_probe as probe


TASK = "SO67bfAHTjOxdygsnV2IIg"
HOST = medic.worker_fqdn("ms274")
IDENTITY = {"provisioner": "releng-hardware", "worker_type": "gecko-t-linux-netperf-2404",
            "worker_group": "mdc1", "worker_id": "t-linux64-ms-274"}
SIGNATURE = {"boot_id": "7dcb9a1b-861a-4e9d-96ab-43ca0b7ea2d3", "pid": 4376, "start_ticks": 5354,
             "task_id": TASK, "resolved_at_us": 1794930466576535, "reclaimed_at_us": 1794930466661117}
TC = {"identity": IDENTITY, "task_id": TASK, "task_run_id": 0, "task_state": "exception",
      "last_date_active": "2026-11-17T15:47:46.599Z"}


@pytest.mark.parametrize("condition", ["stall", "wrong_task", "newer_log", "different_logger", "too_recent"])
def test_resolution_signature_matches_the_live_log_shape(condition):
    def log(message, timestamp, realtime=1794930466500000, pid="4370"):
        return {"SYSLOG_IDENTIFIER": "generic-worker", "MESSAGE": message, "_PID": pid,
                "__MONOTONIC_TIMESTAMP": str(timestamp), "__REALTIME_TIMESTAMP": str(realtime)}

    entries = [
        log(f"2026/11/17 15:47:46 Reclaiming task {TASK}...", 1074837756),
        log("HTTP/1.1 200 OK", 1074848426),
        log(f"2026/11/17 15:47:46 Resolving task {TASK} ...", 1074849622, SIGNATURE["resolved_at_us"]),
        log(f"2026/11/17 15:47:46 Reclaimed task {TASK} successfully.", 1074933928),
        log(f"2026/11/17 15:47:46 Successfully reclaimed task {TASK}", 1074934205, SIGNATURE["reclaimed_at_us"]),
    ]
    if condition == "wrong_task":
        entries[-1]["MESSAGE"] = entries[-1]["MESSAGE"].replace(TASK, "x" * 22)
    if condition == "newer_log":
        entries.append(log('WORKER_METRICS {"eventType":"workerReady"}', 1074934206))
    if condition == "different_logger":
        entries[-1]["_PID"] = "4371"
    now_us = 1200000000 if condition == "too_recent" else 1700000000
    result = probe.match_sequence(entries, started_us=53540000, now_us=now_us)
    expected = {key: SIGNATURE[key] for key in probe.SIGNATURE_KEYS[3:]}
    assert result == (expected if condition == "stall" else {})


@pytest.mark.parametrize("condition", ["expired", "wrong_worker", "other_reason", "fresh_contact", "new_task"])
def test_queue_check_uses_original_run_and_keeps_retry_history(monkeypatch, condition):
    original = {"runId": 0, "state": "exception", "reasonResolved": "claim-expired",
                "workerGroup": "mdc1", "workerId": "t-linux64-ms-274",
                "started": "2026-11-17T15:30:46.566Z", "takenUntil": "2026-11-17T16:07:46.594Z",
                "resolved": "2026-11-17T16:07:49.397Z"}
    retry = {"runId": 1, "state": "completed", "reasonResolved": "completed",
             "workerGroup": "mdc1", "workerId": "t-linux64-ms-226"}
    status = {"taskId": TASK, "taskQueueId": "releng-hardware/gecko-t-linux-netperf-2404",
              "state": "completed", "runs": [original, retry]}
    tc = dict(TC)
    if condition == "wrong_worker":
        original["workerId"] = "t-linux64-ms-226"
    if condition == "other_reason":
        original["reasonResolved"] = "worker-shutdown"
    if condition == "fresh_contact":
        tc["last_date_active"] = "2026-11-17T16:04:00.000Z"
    if condition == "new_task":
        tc["task_id"] = "x" * 22
    monkeypatch.setattr(medic, "urlopen", lambda url, timeout: io.BytesIO(json.dumps({"status": status}).encode()))
    actual = medic.confirm_task_stall_attempt(SIGNATURE, tc)
    if condition == "expired":
        assert actual["run_id"] == 0
        assert [run["workerId"] for run in actual["history"]] == ["t-linux64-ms-274", "t-linux64-ms-226"]
    else:
        assert actual == {}


@pytest.mark.parametrize("changed", [False, True])
def test_remote_reboot_checks_worker_process_again_and_runs_once(monkeypatch, tmp_path, changed):
    monkeypatch.setattr(sys, "argv", ["probe", "reboot", "900", json.dumps(SIGNATURE)])
    monkeypatch.setattr(probe, "detect", lambda *a: SIGNATURE)
    process = {key: SIGNATURE[key] for key in probe.SIGNATURE_KEYS[:3]}
    monkeypatch.setattr(probe, "worker_process", lambda *a: {} if changed else process)
    monkeypatch.setattr(probe, "REBOOT_MARKER", tmp_path / "attempted")
    calls = []
    monkeypatch.setattr(probe.subprocess, "run", lambda args, **kw: calls.append(args))
    assert probe.main(lambda: None) == (3 if changed else 0)
    assert probe.main(lambda: None) == 3
    assert calls == ([] if changed else [["systemctl", "--no-block", "reboot"]])


@pytest.mark.parametrize("claim_still_expired", [False, True])
def test_recovery_rechecks_claim_after_collecting_diagnostics(monkeypatch, tmp_path, claim_still_expired):
    args = argparse.Namespace(startup_grace_minutes=15, freshness_min_pct=65, max_identity_age_hours=168)
    entry = {"host": HOST, "candidate_filter": medic.UNHEALTHY_MOONSHOTS_FILTER,
             "taskcluster": TC, "diagnosis": {"task_stall": SIGNATURE, "task_attempt": {"run_id": 0}}}
    state = {"hosts": {}}
    steps = []
    monkeypatch.setattr(medic, "recently_processed", lambda *_: False)
    monkeypatch.setattr(medic, "check_fleetroll_data_freshness", lambda *_: True)
    monkeypatch.setattr(medic, "save_state", lambda *_: None)
    monkeypatch.setattr(medic, "fleetroll_worker_snapshot", lambda host, **kw: {
        host: {"host": host, "ok": True, "ts": datetime.datetime.now(datetime.UTC).isoformat()},
    })
    monkeypatch.setattr(medic, "task_stall_probe_evidence", lambda host, mode, *args: (
        SIGNATURE if mode == "detect" else {"boot_id": "new-boot", "worker_ready_us": 2000}
    ))

    def tc(hosts, *, decisions, activity_since=None, **kw):
        steps.append("tc-before" if activity_since is None else "tc-after")
        decisions[HOST] = {"taskcluster": TC}
        return hosts

    def collect(*args, boot):
        assert boot == 0
        steps.append("diagnostics")
        return True

    def confirm(signature, evidence):
        steps.append("queue")
        return {"run_id": 0} if claim_still_expired else {}

    def reboot(host, mode, grace, signature):
        assert (host, mode, signature) == (HOST, "reboot", SIGNATURE)
        steps.append("reboot")
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(medic, "eligible_auto_hosts", tc)
    monkeypatch.setattr(medic, "collect_diagnostics", collect)
    monkeypatch.setattr(medic, "confirm_task_stall_attempt", confirm)
    monkeypatch.setattr(medic, "run_task_stall_probe", reboot)
    result = medic.recover_task_resolution_stall(entry, args, 900, state, tmp_path)
    assert result["outcome"] == ("recovered" if claim_still_expired else "deferred")
    assert steps == ["diagnostics", "tc-before", "queue", *(["reboot", "tc-after"] if claim_still_expired else [])]


@pytest.mark.parametrize("confirm", [False, True])
def test_check_2_reports_and_dispatches_the_task_stall(monkeypatch, tmp_path, confirm):
    args = argparse.Namespace(confirm=confirm, no_reset=False, diagnose_workers=False,
                              startup_grace_minutes=15, diagnosis_limit=10,
                              max_identity_age_hours=168, max_fleet_reset_pct=20)
    audit = {"host": HOST, "ok": True, "ts": datetime.datetime.now(datetime.UTC).isoformat()}
    monkeypatch.setattr(medic, "RESULTS_BASE", tmp_path)
    monkeypatch.setattr(medic, "_fleetroll_command", lambda *a: json.dumps({HOST: audit}))
    monkeypatch.setattr(medic, "_configured_linux_moonshot_hosts", lambda: {HOST})
    monkeypatch.setattr(medic, "historical_identity_problem", lambda *a, **kw: None)
    monkeypatch.setattr(medic, "load_state", lambda: {"hosts": {}})
    monkeypatch.setattr(medic, "recently_processed", lambda *_: False)
    monkeypatch.setattr(medic, "diagnose_worker", lambda *a, **kw: {"worker_state": "live"})
    monkeypatch.setattr(medic, "task_stall_probe_evidence", lambda *a: SIGNATURE)
    monkeypatch.setattr(medic, "confirm_task_stall_attempt", lambda *a: {"run_id": 0})
    monkeypatch.setattr(medic, "check_fleet_reset_circuit_breaker", lambda *a: (True, [HOST], ""))
    calls = []

    def eligible(hosts, *, decisions, **kwargs):
        decisions[HOST] = {"taskcluster": TC}
        return hosts

    def recover(entry, args, freshness_seconds, state, run_dir):
        calls.append(entry["host"])
        report, = run_dir.glob("*-worker-diagnosis.json")
        assert json.loads(report.read_text())["results"][0]["diagnosis"]["task_attempt"] == {"run_id": 0}
        return {"outcome": "recovered", "reason": "verified"}

    monkeypatch.setattr(medic, "eligible_auto_hosts", eligible)
    monkeypatch.setattr(medic, "recover_task_resolution_stall", recover)
    assert medic.report_worker_diagnoses(args, 900)
    report, = tmp_path.rglob("*-worker-diagnosis.json")
    assert json.loads(report.read_text())["results"][0]["diagnosis"]["worker_state"] == "task_resolution_stall"
    assert calls == ([HOST] if confirm else [])
