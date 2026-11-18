import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

import pytest

import moonshot_medic as medic
import moonshot_oom_probe as oom


HOST = medic.worker_fqdn("ms236")
BOOT_ID = "ccd33f9e-4e52-4c03-b09e-a4830a0e170d"
SIGNATURE = {"boot_id": BOOT_ID, "kernel_oom_us": 50_000_000, "terminal_oom_us": 50_089_680}
IDENTITY = {"worker_id": "t-linux64-ms-236", "worker_type": "gecko-t-linux-talos-2404",
            "provisioner": "releng-hardware", "worker_group": "mdc1"}
OFFLINE = {"boot_id": BOOT_ID, "uptime_seconds": 2000, "processes": [],
           "unverified_pids": [], "operator_hold": False}


@pytest.mark.parametrize("condition", ["oom", "different_time", "ready", "live", "hold"])
def test_oom_detector_correlates_current_boot_events(monkeypatch, tmp_path, condition):
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    monkeypatch.setattr(oom, "Path", lambda _: Path(os_release))
    monkeypatch.setattr(oom.os.path, "lexists", lambda _: False)
    monkeypatch.setattr(oom.pwd, "getpwnam", lambda user: argparse.Namespace(pw_uid=1005))
    current = dict(OFFLINE)
    if condition == "live":
        current["processes"] = [{"state": "S"}]
    if condition == "hold":
        current["operator_hold"] = True

    def journal(boot_id, pattern, *filters):
        assert boot_id == BOOT_ID.replace("-", "")
        if "-k" in filters:
            assert "global_oom" in pattern
            return [SIGNATURE["kernel_oom_us"]]
        if f"USER_UNIT={oom.TERMINAL_UNIT}" in filters:
            assert "_UID=1005" in filters
            event = SIGNATURE["terminal_oom_us"]
            return [event + 11_000_000 if condition == "different_time" else event]
        return [51_000_000 if condition == "ready" else 49_000_000]

    monkeypatch.setattr(oom, "journal_times", journal)
    assert oom.detect(lambda: current, 900) == (SIGNATURE if condition == "oom" else {})


@pytest.mark.parametrize("changed", ["", "worker", "hold", "boot", "signature"])
def test_remote_oom_reboot_is_conditional_and_once_per_boot(monkeypatch, tmp_path, changed):
    current = dict(OFFLINE)
    if changed == "worker":
        current["processes"] = [{"state": "S"}]
    if changed == "hold":
        current["operator_hold"] = True
    if changed == "boot":
        current["boot_id"] = "different"
    observed = dict(SIGNATURE)
    if changed == "signature":
        observed["terminal_oom_us"] += 1
    calls = []
    monkeypatch.setattr(sys, "argv", ["probe", "reboot", "900", json.dumps(SIGNATURE)])
    monkeypatch.setattr(oom, "detect", lambda *_: observed)
    monkeypatch.setattr(oom.os.path, "lexists", lambda _: False)
    monkeypatch.setattr(oom, "REBOOT_MARKER", tmp_path / "attempted")
    monkeypatch.setattr(oom.subprocess, "run", lambda args, **kw: calls.append(args))

    assert oom.main(lambda: current) == (3 if changed else 0)
    assert oom.main(lambda: current) == 3
    assert calls == ([] if changed else [["systemctl", "--no-block", "reboot"]])


@pytest.mark.parametrize("blocked", ["", "task_active", "identity_changed", "signature_changed"])
def test_oom_recovery_revalidates_and_verifies_worker_contact(monkeypatch, tmp_path, blocked):
    args = argparse.Namespace(startup_grace_minutes=15, freshness_min_pct=65, max_identity_age_hours=168)
    entry = {"host": HOST, "candidate_filter": medic.UNHEALTHY_MOONSHOTS_FILTER,
             "taskcluster": {"identity": IDENTITY}, "diagnosis": {"oom": SIGNATURE}}
    state = {"hosts": {}}
    steps = []
    monkeypatch.setattr(medic, "recently_processed", lambda *_: False)
    monkeypatch.setattr(medic, "check_fleetroll_data_freshness", lambda *_: True)
    monkeypatch.setattr(medic, "save_state", lambda *_: None)

    def snapshot(host, *, filter_query):
        assert filter_query == medic.UNHEALTHY_MOONSHOTS_FILTER
        return {host: {"host": host, "ok": True, "ts": datetime.datetime.now(datetime.UTC).isoformat()}}

    def probe(host, mode, grace, signature=None):
        steps.append(mode)
        if mode == "detect":
            return {} if blocked == "signature_changed" else SIGNATURE
        return {"boot_id": "new-boot", "worker_ready_us": 2_000_000}

    def collect(*args, boot):
        assert boot == 0
        steps.append("diagnostics")
        return True

    def eligible(hosts, *, decisions, activity_since=None, **kwargs):
        steps.append("tc-before" if activity_since is None else "tc-after")
        identity = {**IDENTITY, "worker_type": "different"} if blocked == "identity_changed" else IDENTITY
        decisions[HOST] = {"taskcluster": {"identity": identity}}
        return [] if blocked == "task_active" else hosts

    def reboot(host, mode, grace, signature):
        assert (host, mode, signature) == (HOST, "reboot", SIGNATURE)
        assert state["hosts"][HOST]["oom_reboot_boot_id"] == BOOT_ID
        steps.append("reboot")
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(medic, "oom_probe_evidence", probe)
    monkeypatch.setattr(medic, "collect_diagnostics", collect)
    monkeypatch.setattr(medic, "fleetroll_worker_snapshot", snapshot)
    monkeypatch.setattr(medic, "eligible_auto_hosts", eligible)
    monkeypatch.setattr(medic, "run_oom_probe", reboot)
    result = medic.recover_worker_oom(entry, args, 900, state, tmp_path)
    if blocked:
        assert result["outcome"] == "deferred"
        assert steps == (["detect"] if blocked == "signature_changed" else ["detect", "diagnostics", "tc-before"])
    else:
        assert result["outcome"] == "recovered"
        assert steps == ["detect", "diagnostics", "tc-before", "reboot", "verify", "tc-after"]
        assert medic.recover_worker_oom(entry, args, 900, state, tmp_path)["outcome"] == "deferred"


@pytest.mark.parametrize("confirm,no_reset,acts", [(False, False, False), (True, True, False), (True, False, True)])
def test_check_2_reports_oom_and_dispatches_only_in_confirmed_mode(monkeypatch, tmp_path, confirm, no_reset, acts):
    args = argparse.Namespace(confirm=confirm, no_reset=no_reset, diagnose_workers=False,
                              startup_grace_minutes=15, diagnosis_limit=10,
                              max_identity_age_hours=168, max_fleet_reset_pct=20)
    audit = {"host": HOST, "ok": True, "ts": datetime.datetime.now(datetime.UTC).isoformat()}
    monkeypatch.setattr(medic, "RESULTS_BASE", tmp_path)
    monkeypatch.setattr(medic, "_fleetroll_command", lambda *a: json.dumps({HOST: audit}))
    monkeypatch.setattr(medic, "_configured_linux_moonshot_hosts", lambda: {HOST})
    monkeypatch.setattr(medic, "historical_identity_problem", lambda *a, **kw: None)
    monkeypatch.setattr(medic, "load_state", lambda: {"hosts": {}})
    monkeypatch.setattr(medic, "recently_processed", lambda *_: False)
    monkeypatch.setattr(medic, "eligible_auto_hosts", lambda hosts, **kw: hosts)
    monkeypatch.setattr(medic, "diagnose_worker", lambda *a, **kw: {"worker_state": "absent"})
    monkeypatch.setattr(medic, "gdm_probe_evidence", lambda *a: {})
    monkeypatch.setattr(medic, "oom_probe_evidence", lambda *a: SIGNATURE)
    monkeypatch.setattr(medic, "check_fleet_reset_circuit_breaker", lambda *a: (True, [HOST], ""))
    calls = []

    def recover(entry, args, freshness_seconds, state, run_dir):
        calls.append(entry["host"])
        saved, = run_dir.glob("*-worker-diagnosis.json")
        assert json.loads(saved.read_text())["results"][0]["diagnosis"]["oom"] == SIGNATURE
        return {"outcome": "recovered", "reason": "verified"}

    monkeypatch.setattr(medic, "recover_worker_oom", recover)
    with medic.scan_log("test scan") as run_dir:
        medic.record_reset_success({"hosts": {}}, medic.worker_fqdn("ms025"))
        assert medic.report_worker_diagnoses(args, 900)
    saved, = tmp_path.rglob("*-worker-diagnosis.json")
    assert json.loads(saved.read_text())["results"][0]["diagnosis"]["worker_state"] == "worker_oom"
    assert calls == ([HOST] if acts else [])
    log = (run_dir / "run.log").read_text()
    assert "Check 1 | ms025 | iLO reset | verified | Host unreachable" in log
    if confirm:
        outcome, reason = ("recovered", "verified") if acts else ("deferred", "--no-reset requested")
        assert (f"Check 2 | ms236 | SSH reboot | {outcome} | "
                f"host OOM killed the worker's terminal service. {reason} | Evidence: {saved}") in log
    else:
        assert "Check 2 | ms236 | SSH reboot" not in log
    assert log.index("Worker diagnosis:") < log.index("Scan ended at")
