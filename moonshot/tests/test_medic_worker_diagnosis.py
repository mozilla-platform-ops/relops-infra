"""Temporary process diagnosis is read-only, bounded, and separate from recovery."""

import datetime
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import moonshot_medic as mm

HOST = mm.worker_fqdn("ms025")
NOW = datetime.datetime.now(datetime.UTC)


@pytest.fixture
def probe():
    return {"schema_version": 1, "source": "medic_proc_v1", "checked_at": NOW.isoformat(),
            "boot_id": "00000000-0000-0000-0000-000000000001", "uptime_seconds": 3600,
            "operator_hold": False, "processes": [{"pid": 100, "ppid": 50, "state": "Z"}],
            "unverified_pids": [], "status_file_mtime": NOW.timestamp() - 86400}


def classify(probe):
    return mm.classify_worker_probe(probe, started=NOW, now=NOW, startup_grace_seconds=900)


@pytest.mark.parametrize("processes,uptime,state", [
    ([{"pid": 100, "ppid": 50, "state": "Z"}], 3600, "zombie"),
    ([{"pid": 100, "ppid": 50, "state": "Z"}], 30, "zombie"),
    ([{"pid": 100, "ppid": 50, "state": "S"}], 3600, "live"),
    ([], 45, "startup"), ([], 900, "absent"),
    ([{"pid": 100, "ppid": 50, "state": "X"}], 3600, "absent"),
])
def test_classification_does_not_infer_health_from_old_status(probe, processes, uptime, state):
    probe.update(processes=processes, uptime_seconds=uptime)
    assert classify(probe)["worker_state"] == state


def test_hold_and_incomplete_checks_take_precedence(probe):
    probe["unverified_pids"] = [200]
    assert classify(probe)["worker_state"] == "unverified"
    probe["operator_hold"] = True
    result = classify(probe)
    assert result["worker_state"] == "held"
    assert result["processes"][0]["state"] == "Z"


@pytest.mark.parametrize("field,value", [
    ("schema_version", 2), ("source", "other"), ("operator_hold", None),
    ("uptime_seconds", -1), ("uptime_seconds", float("nan")), ("uptime_seconds", True),
    ("boot_id", "bad"), ("checked_at", "2026-01-01T00:00:00"),
    ("checked_at", (NOW - datetime.timedelta(hours=2)).isoformat()),
    ("processes", None), ("processes", [{"pid": 100, "ppid": 50, "state": "?"}]),
    ("processes", [{"pid": True, "ppid": 50, "state": "Z"}]),
    ("unverified_pids", [100]), ("status_file_mtime", "old"),
])
def test_invalid_evidence_is_unverified(probe, field, value):
    probe[field] = value
    assert classify(probe)["worker_state"] == "unverified"


def test_no_arbitrary_remote_fields_are_forwarded(probe):
    probe["credentials"] = "must not appear"
    probe["processes"][0]["args"] = "must not appear"
    assert "must not appear" not in json.dumps(classify(probe))


def test_real_probe_with_proc_fixture(tmp_path):
    namespace = {"__name__": "probe_test"}
    exec(mm.WORKER_PROCESS_PROBE, namespace)  # noqa: S102 - execute our literal probe against a proc fixture
    root = tmp_path / "proc"
    (root / "sys/kernel/random").mkdir(parents=True)
    (root / "sys/kernel/random/boot_id").write_text("00000000-0000-0000-0000-000000000001")
    (root / "uptime").write_text("3600.0 123.0")
    worker = root / "100"
    worker.mkdir()
    (worker / "comm").write_text("generic-worker\n")
    (worker / "status").write_text("Name:\tgeneric-worker\nState:\tZ (zombie)\nPPid:\t50\n")
    # No cmdline file: zombies need only comm/status, never argument inspection.
    hold = tmp_path / "hold"
    status = tmp_path / "status"
    status.write_text("sensitive task contents must not be read")
    collect = namespace["collect"]
    result = collect(root, hold, status)
    assert result["processes"] == [{"pid": 100, "ppid": 50, "state": "Z"}]
    assert result["operator_hold"] is False
    assert result["status_file_mtime"] == status.stat().st_mtime
    hold.touch()
    assert collect(root, hold, status)["operator_hold"] is True
    (worker / "status").unlink()
    result = collect(root, hold, status)
    assert result["unverified_pids"] == [100]
    assert "sensitive" not in json.dumps(result)


@pytest.mark.parametrize("outcome", ["timeout", "ssh", "malformed", "oversized", "success"])
def test_ssh_probe_failures_and_command(monkeypatch, probe, outcome):
    def run(command, **kwargs):
        assert command[0] == "ssh" and HOST in command and "BatchMode=yes" in command
        assert command[-1].startswith("sudo -n python3 -c ")
        assert kwargs["timeout"] == 45
        if outcome == "timeout":
            raise mm.subprocess.TimeoutExpired(command, 45)
        return mm.subprocess.CompletedProcess(
            command, 255 if outcome == "ssh" else 0,
            stdout={"malformed": "bad", "oversized": "x" * 16385}.get(outcome, json.dumps(probe)),
            stderr="sensitive remote error",
        )
    monkeypatch.setattr(mm.subprocess, "run", run)
    result = mm.diagnose_worker(HOST, startup_grace_seconds=900)
    assert result["worker_state"] == ("zombie" if outcome == "success" else "unverified")
    assert "sensitive" not in json.dumps(result)


@pytest.fixture
def selection(monkeypatch):
    record = {"host": HOST, "ts": datetime.datetime.now(datetime.UTC).isoformat(), "ok": True}
    records = {HOST: record}
    commands = []
    def command(*args):
        commands.append(args)
        assert "note=" in args[-1] and "data>" not in args[-1]
        return json.dumps(records)
    monkeypatch.setattr(mm, "_fleetroll_command", command)
    monkeypatch.setattr(mm, "_configured_linux_moonshot_hosts", lambda: {HOST, mm.worker_fqdn("ms026")})
    monkeypatch.setattr(mm, "load_state", lambda: {"hosts": {}})
    monkeypatch.setattr(mm, "historical_identity_problem", lambda *a, **kw: None)
    monkeypatch.setattr(mm, "recently_processed", lambda *a: False)
    calls = []
    def eligible(hosts, **kwargs):
        assert kwargs["allow_recent_identity"] is True
        return hosts
    monkeypatch.setattr(mm, "eligible_auto_hosts", eligible)
    def diagnose(host, **kwargs):
        calls.append(host)
        return {"worker_state": "zombie", "source": "medic_proc_v1"}
    monkeypatch.setattr(mm, "diagnose_worker", diagnose)
    return records, calls


def select():
    return mm.diagnose_reachable_workers(freshness_seconds=900, max_identity_age_hours=168,
                                         limit=1, startup_grace_seconds=900)


def test_selection_accepts_fresh_host_and_bounds_probes(selection):
    records, calls = selection
    second = mm.worker_fqdn("ms026")
    records[second] = {**deepcopy(records[HOST]), "host": second}
    report = select()
    assert calls == [HOST]
    assert report["mode"] == "diagnosis_only"
    assert any(r.get("reason") == "diagnosis limit reached" for r in report["results"])


@pytest.mark.parametrize("block", ["failed_audit", "old_audit", "identity", "recency", "backoff", "tc"])
def test_selection_defers_without_probing(selection, monkeypatch, block):
    records, calls = selection
    if block == "failed_audit":
        records[HOST]["ok"] = False
    elif block == "old_audit":
        records[HOST]["ts"] = (NOW - datetime.timedelta(hours=2)).isoformat()
    elif block == "identity":
        monkeypatch.setattr(mm, "historical_identity_problem", lambda *a, **kw: "uncertain identity")
    elif block == "recency":
        monkeypatch.setattr(mm, "recently_processed", lambda *a: True)
    elif block == "backoff":
        monkeypatch.setattr(mm, "is_skipped", lambda *a: True)
    else:
        monkeypatch.setattr(mm, "eligible_auto_hosts", lambda *a, **kw: [])
    assert select()["results"][0]["selection"] == "deferred"
    assert calls == []


def test_unknown_inventory_blocks_diagnosis(selection, monkeypatch):
    monkeypatch.setattr(mm, "_configured_linux_moonshot_hosts", lambda: set())
    with pytest.raises(ValueError, match="inventory"):
        select()


def test_diagnosis_main_cannot_reach_host_actions(monkeypatch, tmp_path, selection):
    monkeypatch.setattr(sys, "argv", ["medic", "--auto", "--diagnose-workers", "--no-voice"])
    monkeypatch.setattr(mm, "RESULTS_BASE", tmp_path)
    monkeypatch.setattr(mm, "check_fleetroll_data_freshness", lambda *a: True)
    def forbidden(*args, **kwargs):
        pytest.fail("diagnosis reached a host action or recovery state write")
    for name in ("reset_batch", "remediate_clock_skew", "collect_diagnostics", "save_state"):
        monkeypatch.setattr(mm, name, forbidden)
    mm.main()
    reports = list(tmp_path.rglob("*-worker-diagnosis.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["results"][0]["diagnosis"]["worker_state"] == "zombie"


@pytest.mark.parametrize("args", [[], ["--auto", "--confirm"], ["--auto", "ms025"],
                                  ["--auto", "--update-report"], ["--auto", "--diagnosis-limit", "0"]])
def test_invalid_diagnosis_modes_exit_before_work(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["medic", "--diagnose-workers", *args])
    with pytest.raises(SystemExit) as exc:
        mm.main()
    assert exc.value.code == 1


def test_fleet_freshness_blocks_diagnosis(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["medic", "--auto", "--diagnose-workers", "--no-voice"])
    monkeypatch.setattr(mm, "check_fleetroll_data_freshness", lambda *a: False)
    monkeypatch.setattr(mm, "diagnose_reachable_workers", lambda **kw: pytest.fail("stale fleet diagnosed"))
    with pytest.raises(SystemExit) as exc:
        mm.main()
    assert exc.value.code == 1
