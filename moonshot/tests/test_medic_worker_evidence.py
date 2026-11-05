"""Automatic resets require exact override-pool evidence, even after SSH fails."""

import datetime
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import moonshot_medic as mm

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 5, 12, tzinfo=UTC)
STARTED = NOW - datetime.timedelta(seconds=5)
HOST = mm.worker_fqdn("ms017")
IDENTITY = {
    "provisioner": "releng-hardware",
    "worker_type": "gecko-t-linux-talos-2404-tc-v115",
    "worker_group": "mdc1",
    "worker_id": "t-linux64-ms-017",
}


def stamp(*, hours=0, seconds=0):
    return (NOW - datetime.timedelta(hours=hours, seconds=seconds)).isoformat()


@pytest.fixture
def evidence():
    provenance = {
        "identity": dict(IDENTITY), "observation_ts": stamp(hours=8),
        "source": "host_configuration", "override_present": True,
        "configured_worker_type": {
            "provisioner": IDENTITY["provisioner"], "worker_type": IDENTITY["worker_type"],
            "status": "resolved", "source": "override", "application_status": "applied",
            "override_sha256": "test_override_sha",
        },
    }
    return {
        "host": HOST, "ok": False, "ts": stamp(seconds=30),
        "worker_evidence_schema_version": 3,
        "configured_worker_type": {"status": "unresolved", "reason": "host_audit_failed"},
        "observed_worker_identity": {"status": "unresolved"},
        "historical_worker_identity": {
            **IDENTITY, "status": "present", "observation_ts": stamp(hours=8),
            "freshness": "stale", "collection_status": "ok", "source": "host_configuration",
            "contradiction_status": "none_known", "reasons": [], "provenance": provenance,
        },
        "taskcluster": {
            "identity": dict(IDENTITY), "observation_ts": stamp(seconds=4),
            "last_date_active": stamp(hours=5), "record_status": "stored_success",
            "latest_lookup_status": "success", "freshness": "fresh",
            "historical_identity_match": "match", "historical_baseline_match": "match",
            "historical_baseline": deepcopy(provenance),
            "record": {**IDENTITY, "task_state": "completed"},
            "latest_lookup": {
                "identity": dict(IDENTITY), "status": "success",
                "started_ts": STARTED.isoformat(), "ts": stamp(seconds=1),
                "resolution_source": "historical_worker_identity",
                "successful_observation_ts": stamp(seconds=4), "freshness": "fresh",
                "historical_identity_match": "match", "historical_baseline_match": "match",
                "historical_baseline": deepcopy(provenance),
            },
        },
    }


def problem(record, **kwargs):
    return mm.automatic_recovery_problem(
        record, now=NOW, lookup_started=STARTED,
        freshness_seconds=900, max_identity_age_hours=168, **kwargs,
    )


def test_unreachable_override_worker_with_confirmed_inactivity_is_eligible(evidence):
    assert problem(evidence) is None


@pytest.mark.parametrize("activity", [stamp(seconds=10), stamp(hours=4), stamp(hours=-1), None,
                                      "invalid", "2026-11-05T01:00:00"])
def test_active_or_unknown_override_worker_is_never_eligible(evidence, activity):
    evidence["taskcluster"]["last_date_active"] = activity
    assert problem(evidence)


@pytest.mark.parametrize("state", ["running", "pending"])
def test_active_task_blocks_reset_even_with_old_last_active(evidence, state):
    evidence["taskcluster"]["record"]["task_state"] = state
    assert "active Taskcluster task" in problem(evidence)


@pytest.mark.parametrize("status", ["identity_unresolved", "pool_query_failed", "worker_missing",
                                   "worker_ambiguous", "task_lookup_failed", "unknown"])
def test_failed_lookup_cannot_reuse_retained_success(evidence, status):
    evidence["taskcluster"]["latest_lookup"]["status"] = status
    assert problem(evidence)


@pytest.mark.parametrize("target", ["taskcluster", "latest_lookup"])
@pytest.mark.parametrize("field", list(IDENTITY))
def test_every_identity_component_must_match(evidence, target, field):
    obj = evidence["taskcluster"]
    if target == "latest_lookup":
        obj = obj[target]
    obj["identity"][field] = "old-default-pool-or-other-worker"
    assert "different identity" in problem(evidence)


@pytest.mark.parametrize("path,value", [
    (("worker_evidence_schema_version",), 2),
    (("historical_worker_identity",), None),
    (("historical_worker_identity", "contradiction_status"), "uncertain"),
    (("historical_worker_identity", "contradiction_status"), "contradicted"),
    (("historical_worker_identity", "worker_group"), None),
    (("historical_worker_identity", "collection_status"), "invalid"),
    (("historical_worker_identity", "provenance"), None),
    (("historical_worker_identity", "reasons"), [{"code": "override_change_unverified"}]),
    (("taskcluster",), []),
    (("taskcluster", "record_status"), "missing"),
    (("taskcluster", "latest_lookup"), None),
    (("taskcluster", "historical_baseline_match"), "mismatch"),
    (("taskcluster", "historical_baseline", "observation_ts"), stamp(hours=9)),
    (("taskcluster", "latest_lookup", "historical_baseline_match"), "unknown"),
    (("taskcluster", "latest_lookup", "historical_baseline", "observation_ts"), stamp(hours=9)),
    (("taskcluster", "latest_lookup", "resolution_source"), "legacy_role"),
    (("taskcluster", "latest_lookup", "successful_observation_ts"), stamp(hours=1)),
    (("taskcluster", "latest_lookup", "started_ts"), stamp(hours=1)),
    (("taskcluster", "latest_lookup", "ts"), stamp(hours=-1)),
    (("taskcluster", "record"), None),
])
def test_incomplete_or_mismatched_evidence_fails_closed(evidence, path, value):
    obj = evidence
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = value
    assert problem(evidence)


@pytest.mark.parametrize("age,eligible", [(3, False), (8, True), (168, True), (169, False), (-1, False)])
def test_baseline_age_policy_does_not_require_fresh_ssh(evidence, age, eligible):
    baseline = evidence["historical_worker_identity"]
    baseline["observation_ts"] = stamp(hours=age)
    baseline["provenance"]["observation_ts"] = stamp(hours=age)
    for obj in (evidence["taskcluster"], evidence["taskcluster"]["latest_lookup"]):
        obj["historical_baseline"] = deepcopy(baseline["provenance"])
    assert (problem(evidence) is None) is eligible


def test_slow_query_does_not_make_recent_activity_look_old(evidence):
    # The lookup is fresh at the 15-minute default, but has crossed four hours
    # since last activity only while the query was in flight.
    tc = evidence["taskcluster"]
    tc["latest_lookup"]["started_ts"] = stamp(seconds=600)
    tc["observation_ts"] = stamp(seconds=600)
    tc["latest_lookup"]["successful_observation_ts"] = tc["observation_ts"]
    tc["last_date_active"] = stamp(hours=4, seconds=300)
    assert mm.automatic_recovery_problem(
        evidence, now=NOW, lookup_started=NOW - datetime.timedelta(seconds=600),
        freshness_seconds=900, max_identity_age_hours=168,
    )


@pytest.fixture
def fleetroll(monkeypatch, evidence):
    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(mm.datetime, "datetime", Clock)
    # A real command's source timestamps are at or after the requested start.
    tc = evidence["taskcluster"]
    tc["observation_ts"] = NOW.isoformat()
    tc["latest_lookup"].update(started_ts=NOW.isoformat(), ts=NOW.isoformat(),
                               successful_observation_ts=NOW.isoformat())
    calls = []

    def command(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("gather", "tc"):
            assert args[3] == "--use-historical-identity"
            assert Path(args[2]).read_text().splitlines() == [HOST]
            return "gathered"
        assert args[:2] == ("host", "monitor")
        assert "--json" in args
        return json.dumps({HOST: evidence})

    monkeypatch.setattr(mm, "_fleetroll_command", command)
    return calls


@pytest.mark.parametrize("active", [False, True])
def test_batch_queries_exact_identity_then_checks_activity(evidence, fleetroll, active):
    if active:
        evidence["taskcluster"]["last_date_active"] = stamp(seconds=10)
    assert mm.eligible_auto_hosts(
        [HOST], freshness_seconds=900, max_identity_age_hours=168,
    ) == ([] if active else [HOST])
    assert [cmd[:2] for cmd in fleetroll] == [("host", "monitor"), ("gather", "tc"), ("host", "monitor")]


def test_contradicted_baseline_is_not_queried(evidence, fleetroll):
    evidence["historical_worker_identity"]["contradiction_status"] = "contradicted"
    assert mm.eligible_auto_hosts([HOST], freshness_seconds=900, max_identity_age_hours=168) == []
    assert len(fleetroll) == 1


@pytest.mark.parametrize("error", [OSError("unavailable"), ValueError("invalid JSON"),
                                   mm.subprocess.CalledProcessError(1, "fleetroll"),
                                   mm.subprocess.TimeoutExpired("fleetroll", 300)])
def test_cli_errors_never_authorize_recovery(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(mm, "_fleetroll_command", fail)
    assert mm.eligible_auto_hosts([HOST], freshness_seconds=900, max_identity_age_hours=168) == []


def test_override_changes_during_lookup_block_recovery(evidence, fleetroll, monkeypatch):
    original = mm._fleetroll_command

    def command(*args, **kwargs):
        output = original(*args, **kwargs)
        if args[:2] == ("gather", "tc"):
            evidence["historical_worker_identity"]["contradiction_status"] = "contradicted"
        return output

    monkeypatch.setattr(mm, "_fleetroll_command", command)
    assert mm.eligible_auto_hosts([HOST], freshness_seconds=900, max_identity_age_hours=168) == []


@pytest.mark.parametrize("payload", ["null", "[]", "bad", json.dumps({HOST: None}),
                                     json.dumps({HOST: {"host": HOST, "worker_evidence_schema_version": 2}}),
                                     json.dumps({"ms017": {}})])
def test_snapshot_rejects_invalid_contract(monkeypatch, payload):
    monkeypatch.setattr(mm, "_fleetroll_command", lambda *a, **kw: payload)
    with pytest.raises(ValueError):
        mm.fleetroll_worker_snapshot("hosts.list")


@pytest.mark.parametrize("preview,active", [(True, True), (False, True), (False, False)])
def test_auto_main_gates_actions_with_override_evidence(
    monkeypatch, tmp_path, evidence, fleetroll, preview, active,
):
    args = ["moonshot_medic.py", "--auto", "--once", "--no-voice", "--max-fleet-reset-pct", "100"]
    if not preview:
        args.append("--confirm")
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(mm, "RESULTS_BASE", tmp_path)
    monkeypatch.setattr(mm, "FLEETROLL_DIR", tmp_path)
    monkeypatch.setattr(mm, "load_state", lambda: {"hosts": {}})
    monkeypatch.setattr(mm, "_configured_linux_moonshot_hosts", lambda: {HOST})
    monkeypatch.setattr(mm, "check_fleetroll_data_freshness", lambda *a: True)
    monkeypatch.setattr(mm, "recently_processed", lambda *a: False)
    monkeypatch.setattr(mm.time, "sleep", lambda *a: None)
    for name in ("update_overview_html", "update_overview_md", "save_state"):
        monkeypatch.setattr(mm, name, lambda *a: None)
    actions = []
    def reset(batch, state, *, authorize_reset=None):
        assert authorize_reset is not None
        batch = authorize_reset(batch)
        actions.extend(batch)
        return batch, []

    monkeypatch.setattr(mm, "reset_batch", reset)
    monkeypatch.setattr(mm, "remediate_clock_skew", lambda *a: None)
    monkeypatch.setattr(mm, "collect_diagnostics", lambda *a: True)
    if active:
        evidence["taskcluster"]["last_date_active"] = stamp(seconds=10)
    with pytest.raises(SystemExit) as exc:
        mm.main()
    assert exc.value.code == (1 if preview else 0)
    assert actions == ([] if preview or active else [HOST])
    expected = [("host", "monitor"), ("host", "monitor"), ("gather", "tc"), ("host", "monitor")]
    if not preview and not active:
        expected += [("host", "monitor"), ("gather", "tc"), ("host", "monitor")]
    assert [cmd[:2] for cmd in fleetroll] == expected


def test_clock_repair_delay_cannot_authorize_an_active_worker(evidence, fleetroll, monkeypatch):
    def repair(host, state):
        # During clock triage the worker starts processing tasks in its override pool.
        evidence["taskcluster"]["last_date_active"] = stamp(seconds=10)
        return None

    monkeypatch.setattr(mm, "remediate_clock_skew", repair)
    monkeypatch.setattr(mm, "run", lambda *a, **kw: pytest.fail("unexpected iLO reset"))
    monkeypatch.setattr(mm, "ssh_is_online", lambda *a: pytest.fail("unexpected reset accounting"))
    state = {"hosts": {}}
    assert mm.reset_batch(
        [HOST], state, authorize_reset=lambda hosts: mm.eligible_auto_hosts(
            hosts, freshness_seconds=900, max_identity_age_hours=168,
        ),
    ) == ([], [])
    assert state == {"hosts": {}}

