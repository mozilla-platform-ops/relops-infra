import argparse
import datetime
import json
import subprocess
import sys

import pytest

import moonshot_gdm_probe as gdm
import moonshot_medic as medic


HOST = medic.worker_fqdn("ms236")
SIGNATURE = {"boot_id": "a" * 36, "gdm_invocation": "b" * 32, "pid": 11, "start_ticks": 110}
IDENTITY = {"worker_id": "t-linux64-ms-236", "worker_type": "gecko-t-linux-talos-2404",
            "provisioner": "releng-hardware", "worker_group": "mdc1"}


@pytest.mark.parametrize("condition", ["stalled", "desktop", "worker", "wrong_parent", "recent", "hold", "puppet"])
def test_gdm_detector_requires_the_incident_signature(monkeypatch, tmp_path, condition):
    for path, content in {
        "etc/gdm3/custom.conf": "[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=cltbld\n",
        "proc/uptime": "1000 0\n",
        "proc/11/cmdline": "gdm-session-worker [pam/gdm-autologin]\0".encode(),
        "proc/11/wchan": "futex_wait_queue\n",
    }.items():
        file = tmp_path / path
        file.parent.mkdir(exist_ok=True, parents=True)
        if isinstance(content, bytes):
            file.write_bytes(content)
        else:
            file.write_text(content)
    monkeypatch.setattr(gdm, "Path", lambda path: tmp_path / path.lstrip("/"))
    monkeypatch.setattr(gdm, "boot_id", lambda: SIGNATURE["boot_id"])
    monkeypatch.setattr(gdm.os.path, "lexists", lambda path: condition == "hold")
    monkeypatch.setattr(gdm.os, "sysconf", lambda *_: 100)
    monkeypatch.setattr(gdm.time, "monotonic", lambda: 1000)
    monkeypatch.setattr(gdm.time, "monotonic_ns", lambda: 10**12)
    monkeypatch.setattr(gdm, "gdm_service", lambda: {
        "MainPID": "10", "ActiveState": "active", "InvocationID": SIGNATURE["gdm_invocation"],
    })

    def properties(command, *args):
        if command == "cat":
            return {"ID": "ubuntu", "VERSION_ID": '"24.04"'}
        return {"ActiveState": "active" if condition == "puppet" else "inactive"}

    monkeypatch.setattr(gdm, "properties", properties)
    monkeypatch.setattr(gdm, "read_command", lambda *a: "")
    monkeypatch.setattr(gdm, "journal", lambda *a: json.dumps({
        "__MONOTONIC_TIMESTAMP": 900_000_000 if condition == "recent" else 100_000_000,
    }) if a[0] == "-u" else "")
    table = {
        10: {"name": "gdm3", "state": "S", "parent": 1, "start_ticks": 100},
        11: {"name": "gdm-session-wor", "state": "S",
             "parent": 1 if condition == "wrong_parent" else 10, "start_ticks": 110},
    }
    if condition in ("desktop", "worker"):
        table[12] = {"name": "Xorg" if condition == "desktop" else "generic-worker"}
    monkeypatch.setattr(gdm, "process_table", lambda: table)

    detected = gdm.detect(900)
    if condition == "stalled":
        assert {key: detected[key] for key in gdm.SIGNATURE_KEYS} == SIGNATURE
        assert [process["pid"] for process in detected["gdm_process_tree"]] == [10, 11]
    else:
        assert not detected


@pytest.mark.parametrize("changed", [False, True])
def test_restart_rechecks_the_same_stalled_process(monkeypatch, tmp_path, changed):
    evidence = {**SIGNATURE, "start_ticks": 120} if changed else SIGNATURE
    monkeypatch.setattr(sys, "argv", ["probe", "restart", "900", json.dumps(SIGNATURE)])
    monkeypatch.setattr(gdm, "detect", lambda _: evidence)
    monkeypatch.setattr(gdm, "gdm_service", lambda: {"InvocationID": SIGNATURE["gdm_invocation"]})
    monkeypatch.setattr(gdm, "RESTART_MARKER", tmp_path / "attempted")
    monkeypatch.setattr(gdm.os.path, "lexists", lambda _: False)
    commands = []
    monkeypatch.setattr(gdm, "read_command", lambda *args: commands.append(args))
    assert gdm.main() == (3 if changed else 0)
    assert gdm.main() == 3
    assert commands == ([] if changed else [("systemctl", "--no-block", "restart", "gdm.service")])


def test_gdm_recovery_rechecks_tc_and_records_only_one_attempt_per_boot(monkeypatch, tmp_path):
    args = argparse.Namespace(startup_grace_minutes=15, freshness_min_pct=65, max_identity_age_hours=168)
    entry = {"host": HOST, "candidate_filter": medic.STOPPED_WORKERS_FILTER,
             "taskcluster": {"identity": IDENTITY}, "diagnosis": {"gdm": SIGNATURE}}
    state = {"hosts": {}}
    steps = []
    monkeypatch.setattr(medic, "recently_processed", lambda *_: False)
    monkeypatch.setattr(medic, "check_fleetroll_data_freshness", lambda *_: True)

    def snapshot(host, *, filter_query):
        assert filter_query == medic.STOPPED_WORKERS_FILTER
        return {host: {"host": host, "ok": True, "ts": datetime.datetime.now(datetime.UTC).isoformat()}}

    def eligible(hosts, *, decisions, allow_missing_worker=False, activity_since=None, **kwargs):
        assert allow_missing_worker == (activity_since is None)
        steps.append("tc-before" if activity_since is None else "tc-after")
        decisions[HOST] = {"taskcluster": {"identity": IDENTITY}}
        return hosts

    def restart(host, mode, grace, signature):
        assert (host, mode, signature) == (HOST, "restart", SIGNATURE)
        assert state["hosts"][HOST]["gdm_restart_boot_id"] == SIGNATURE["boot_id"]
        steps.append("restart")
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(medic, "fleetroll_worker_snapshot", snapshot)
    monkeypatch.setattr(medic, "eligible_auto_hosts", eligible)
    monkeypatch.setattr(medic, "save_state", lambda *_: None)
    monkeypatch.setattr(medic, "run_gdm_probe", restart)
    monkeypatch.setattr(medic, "gdm_probe_evidence", lambda *_: {**SIGNATURE, "gdm_invocation": "c" * 32})
    report = tmp_path / "diagnosis.json"
    assert medic.recover_gdm_autologin(entry, args, 900, state, report)["outcome"] == "recovered"
    assert medic.recover_gdm_autologin(entry, args, 900, state, report)["outcome"] == "deferred"
    assert steps == ["tc-before", "restart", "tc-after"]


def test_stopped_worker_selector_detects_gdm_outside_the_health_filter(monkeypatch):
    audit = {"host": HOST, "ok": True, "ts": datetime.datetime.now(datetime.UTC).isoformat()}

    def monitor(*args):
        return json.dumps({HOST: audit}) if args[-1] == medic.STOPPED_WORKERS_FILTER else "{}"

    monkeypatch.setattr(medic, "_fleetroll_command", monitor)
    monkeypatch.setattr(medic, "_configured_linux_moonshot_hosts", lambda: {HOST})
    monkeypatch.setattr(medic, "historical_identity_problem", lambda *a, **kw: None)
    monkeypatch.setattr(medic, "load_state", lambda: {"hosts": {}})
    monkeypatch.setattr(medic, "recently_processed", lambda *_: False)
    monkeypatch.setattr(medic, "eligible_auto_hosts", lambda hosts, **kw: hosts if kw["allow_missing_worker"] else [])
    monkeypatch.setattr(medic, "diagnose_worker", lambda *a, **kw: {"worker_state": "absent"})
    monkeypatch.setattr(medic, "gdm_probe_evidence", lambda *a: SIGNATURE)

    report = medic.diagnose_reachable_workers(freshness_seconds=900, max_identity_age_hours=168,
                                              limit=10, startup_grace_seconds=900,
                                              candidate_filter=medic.UNHEALTHY_MOONSHOTS_FILTER)
    entry, = report["results"]
    assert entry["candidate_filter"] == medic.STOPPED_WORKERS_FILTER
    assert entry["diagnosis"]["worker_state"] == "gdm_autologin_stall"


@pytest.mark.parametrize("confirm,no_reset,diagnose_only,acts", [
    (False, False, False, False), (True, True, False, False),
    (False, False, True, False), (True, False, False, True),
])
def test_gdm_restarts_only_after_a_diagnosis_report_in_confirmed_mode(
    monkeypatch, tmp_path, confirm, no_reset, diagnose_only, acts,
):
    args = argparse.Namespace(confirm=confirm, no_reset=no_reset, diagnose_workers=diagnose_only,
                              startup_grace_minutes=15, diagnosis_limit=10,
                              max_identity_age_hours=168, max_fleet_reset_pct=20)
    diagnosis = {"worker_state": "gdm_autologin_stall", "reason": "stalled", "gdm": SIGNATURE}
    entry = {"host": HOST, "selection": "diagnosed", "diagnosis": diagnosis}
    calls = []

    def recover(entry, args, freshness_seconds, state, path):
        assert json.loads(path.read_text())["results"][0]["diagnosis"] == diagnosis
        calls.append(entry["host"])
        return {"outcome": "recovered", "reason": "fresh worker contact"}

    monkeypatch.setattr(medic, "RESULTS_BASE", tmp_path)
    monkeypatch.setattr(medic, "diagnose_reachable_workers", lambda **kw: {"results": [entry]})
    monkeypatch.setattr(medic, "check_fleet_reset_circuit_breaker", lambda *a: (True, [HOST], ""))
    monkeypatch.setattr(medic, "load_state", lambda: {"hosts": {}})
    monkeypatch.setattr(medic, "recover_gdm_autologin", recover)
    assert medic.report_worker_diagnoses(args, 900)
    assert calls == ([HOST] if acts else [])
