"""Temporary process diagnosis is read-only, bounded, and separate from recovery."""

import argparse
import datetime
import json
import signal
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

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
    result = mm.diagnose_worker_probe(HOST, startup_grace_seconds=900)
    assert result["worker_state"] == ("zombie" if outcome == "success" else "unverified")
    assert "sensitive" not in json.dumps(result)


@pytest.fixture
def fleetroll_report():
    return {"schema_version": 1, "host": HOST, "ok": True,
            "collection_status": "completed", "health": "unhealthy", "os_type": "Linux",
            "checked_at": NOW.isoformat(), "gwhc_ts": NOW.isoformat(),
            "contract_status": "legacy", "gwhc_exit_code": 1,
            "zombie": {"status": "unknown"},
            "checks": [{"name": "generic_worker_process", "status": "fail",
                        "data": {"zombie_pids": [100], "nested": {"preserved": True}}}],
            "stderr": "sensitive stderr", "error": "sensitive exception",
            "gwhc": {"secret": "sensitive raw report"}}


@pytest.mark.parametrize("status,state", [("confirmed", "zombie"), ("absent", "not_zombie")])
def test_fleetroll_definitive_result_precedes_probe(monkeypatch, fleetroll_report, status, state):
    fleetroll_report["zombie"]["status"] = status
    def run(command, **kwargs):
        assert command == ["uv", "run", "fleetroll", "host", "diagnose", HOST, "--json"]
        assert kwargs["timeout"] == 45 and kwargs["check"] is False
        assert kwargs["cwd"] == mm.FLEETROLL_DIR
        return mm.subprocess.CompletedProcess(command, 0, json.dumps(fleetroll_report), "sensitive stderr")
    monkeypatch.setattr(mm.subprocess, "run", run)
    monkeypatch.setattr(mm, "diagnose_worker_probe", lambda *a, **kw: pytest.fail("definitive result overridden"))
    result = mm.diagnose_worker(HOST, startup_grace_seconds=900)
    assert result["worker_state"] == state
    assert result["source"] == "fleetroll_host_diagnose"
    assert result["report"]["checks"] == fleetroll_report["checks"]
    assert result["report"]["gwhc_exit_code"] == 1
    assert "sensitive" not in json.dumps(result)


@pytest.mark.parametrize("failure", [
    "unknown", "new_status", "missing_status", "schema", "bool_schema", "host", "failed",
    "nonzero", "stale", "naive", "future", "unsupported", "malformed", "oversized",
    "timeout", "missing_command", "not_object",
])
def test_fleetroll_fallback_preserves_probe_and_evidence(monkeypatch, fleetroll_report, failure):
    fleetroll_report["zombie"]["status"] = "confirmed"
    changes = {
        "unknown": {"zombie": {"status": "unknown"}},
        "new_status": {"zombie": {"status": "new_value"}},
        "missing_status": {"zombie": None}, "schema": {"schema_version": 2},
        "bool_schema": {"schema_version": True}, "host": {"host": mm.worker_fqdn("ms026")},
        "failed": {"ok": False}, "unsupported": {"contract_status": "unsupported"},
        "stale": {"gwhc_ts": (NOW - datetime.timedelta(hours=2)).isoformat()},
        "naive": {"checked_at": "2026-11-05T10:00:00"},
        "future": {"checked_at": (NOW + datetime.timedelta(hours=2)).isoformat()},
    }
    fleetroll_report.update(changes.get(failure, {}))
    def run(command, **kwargs):
        if failure == "timeout":
            raise mm.subprocess.TimeoutExpired(command, 45, stderr="sensitive stderr")
        if failure == "missing_command":
            raise FileNotFoundError("sensitive error")
        output = {"malformed": "bad", "oversized": "x" * 262145, "not_object": "[]"}.get(
            failure, json.dumps(fleetroll_report))
        return mm.subprocess.CompletedProcess(command, int(failure == "nonzero"), output, "sensitive stderr")
    monkeypatch.setattr(mm.subprocess, "run", run)
    calls = []
    def probe(host, **kwargs):
        calls.append((host, kwargs))
        return {"source": "medic_proc_v1", "worker_state": "held", "operator_hold": True}
    monkeypatch.setattr(mm, "diagnose_worker_probe", probe)
    result = mm.diagnose_worker(HOST, startup_grace_seconds=123)
    assert calls == [(HOST, {"startup_grace_seconds": 123})]
    assert result["worker_state"] == "held" and result["operator_hold"] is True
    assert result["fleetroll"]["worker_state"] == "unverified"
    if failure not in ("malformed", "oversized", "timeout", "missing_command", "not_object"):
        assert result["fleetroll"]["report"]["checks"] == fleetroll_report["checks"]
    assert "sensitive" not in json.dumps(result)


def test_invalid_target_never_collects_diagnosis(monkeypatch):
    monkeypatch.setattr(mm.subprocess, "run", lambda *a, **kw: pytest.fail("invalid target queried"))
    assert mm.diagnose_worker("invalid", startup_grace_seconds=900)["worker_state"] == "unverified"


@pytest.fixture
def selection(monkeypatch):
    record = {"host": HOST, "ts": datetime.datetime.now(datetime.UTC).isoformat(), "ok": True}
    records = {HOST: record}
    commands = []
    def command(*args):
        commands.append(args)
        assert "note=" in args[-1] and "data>" not in args[-1]
        if "--hostname-only" in args:
            return ""
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
        return {"worker_state": "zombie", "source": "medic_proc_v1",
                "reason": "confirmed zombie generic-worker"}
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


@pytest.mark.parametrize("stdin_tty,stdout_tty,interface", [
    (False, True, []), (True, False, []), (True, True, ["--legacy"]),
])
def test_diagnosis_main_cannot_reach_host_actions(
    monkeypatch, tmp_path, selection, capsys, stdin_tty, stdout_tty, interface,
):
    args = ["--auto", "--diagnose-workers", "--no-voice", *interface]
    monkeypatch.setattr(sys, "argv", ["medic", *args])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin_tty)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: stdout_tty)
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
    assert '"event": "medic_worker_diagnosis"' in capsys.readouterr().out


@pytest.mark.parametrize("interface", [[], ["--tui"]])
def test_interactive_auto_launches_dashboard_with_explicit_child_flag(monkeypatch, interface):
    import moonshot_medic_tui as tui

    args = ["--auto", "--once", "--confirm"]
    monkeypatch.delenv(tui.STATUS_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["medic", *interface, *args, "--"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def run_dashboard(command, *, startup_logo):
        assert command[:3] == [sys.executable, "-u", str(Path(mm.__file__).resolve())]
        assert command[3:] == ["--tui", *args, "--"]
        assert startup_logo == mm.MEDIC_LOGO
        return 17

    monkeypatch.setattr(tui, "run_dashboard", run_dashboard)
    with pytest.raises(SystemExit) as exc:
        mm.main()

    assert exc.value.code == 17


def test_tui_diagnosis_publishes_the_result_without_relaunching(monkeypatch, tmp_path, selection):
    import moonshot_medic_tui as tui

    status_path = tmp_path / "status.json"
    monkeypatch.setenv(tui.STATUS_ENV, str(status_path))
    monkeypatch.setattr(sys, "argv", ["medic", "--auto", "--tui", "--diagnose-workers", "--no-voice"])
    monkeypatch.setattr(mm, "RESULTS_BASE", tmp_path)
    monkeypatch.setattr(mm, "_tui_status", {})
    monkeypatch.setattr(mm, "_tui_status_path", None)
    monkeypatch.setattr(mm, "check_fleetroll_data_freshness", lambda *a: True)
    monkeypatch.setattr(tui, "run_dashboard", lambda *a: pytest.fail("dashboard relaunched itself"))

    mm.main()

    status = json.loads(status_path.read_text())
    assert status["mode"] == "Diagnosis only"
    assert status["logo"] == list(mm.MEDIC_LOGO)
    assert "Diagnosis only" in tui.status_header(status, 0, False)[0]
    assert ("    ms025  Confirmed zombie · confirmed zombie generic-worker", "attention") in (
        tui.dashboard_lines(status)
    )
    assert status["diagnosis"]["query"] == mm.INACTIVE_MOONSHOTS_FILTER
    report_path, = tmp_path.rglob("*-worker-diagnosis.json")
    assert report_path.name not in status_path.read_text()


def test_tui_header_animates_while_active_and_counts_down_while_waiting(monkeypatch):
    import moonshot_medic_tui as tui

    monkeypatch.setattr(tui.time, "monotonic", lambda: 0.4)
    active = {"phase": "Checking Fleetroll"}
    assert tui.status_header(active, None, False)[0] == "◴ Checking Fleetroll"
    monkeypatch.setattr(tui.time, "monotonic", lambda: 1.0)
    assert tui.status_header(active, None, False)[0] == "◷ Checking Fleetroll"

    idle = {"phase": "Waiting for next scan", "next_run_at": 160.0}
    monkeypatch.setattr(tui.time, "time", lambda: 99.1)
    assert tui.status_header(idle, None, False)[0] == "Next scan in 1:01"
    monkeypatch.setattr(tui.time, "time", lambda: 161.0)
    assert tui.status_header(idle, None, False)[0] == "Next scan in 0:00"
    assert tui.status_header(idle, None, True)[0] == "Stopping after current work finishes"
    assert tui.status_header(idle, 0, False)[0] == "Waiting for next scan · Exit status 0"


@pytest.mark.parametrize("height, logs_height", [(12, 4), (32, 8)])
def test_tui_keeps_panes_and_phase_row_stable_through_startup_and_scans(
    monkeypatch, tmp_path, height, logs_height,
):
    import moonshot_medic_tui as tui

    path = tmp_path / "status.json"
    width = 60
    status = {"logo": mm.MEDIC_LOGO, "mode": "Confirmed recovery", "confirm": True}
    updates = iter([
        {"phase": "Checking Fleetroll", "detail": "Fetching host data for the Moonshot fleet",
         "scan_started_at": "12:43"},
        {"phase": "Waiting for next scan", "next_run_at": 100, "scan_started_at": "12:43",
         "recovery": {"title": "Automated recovery", "summary": "0 hosts eligible", "updated_at": "",
                      "alert": False, "query": "", "hosts": {}, "action": ""}},
        {"phase": "Starting scan", "scan_started_at": "12:44", "recovery": None},
    ])
    screen = Mock()
    screen.getmaxyx.return_value = height, width
    status_pane = Mock()
    log_pane = Mock()
    screen.derwin.side_effect = [status_pane, log_pane] * 4
    frames = []

    def advance():
        frames.append([call.args for call in status_pane.addnstr.call_args_list])
        status_pane.addnstr.reset_mock()
        if update := next(updates, None):
            path.write_text(json.dumps({**status, **update}))
            return -1
        return ord("q")

    screen.getch.side_effect = advance
    process = Mock()
    process.poll.side_effect = [None, None, None, 0]
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_: None)
    monkeypatch.setattr(tui.time, "time", lambda: 40)

    assert tui.display(screen, process, path, Mock(), tui.StopRequest(), startup_logo=mm.MEDIC_LOGO) == 0

    main_height = height - 1 - logs_height
    assert [call.args for call in screen.derwin.call_args_list] == [
        (main_height, width, 0, 0), (logs_height, width, main_height, 0),
    ] * 4
    logo_visible = height == 32
    assert all(any(row[2] == mm.MEDIC_LOGO[0] for row in frame) == logo_visible for frame in frames)
    phase_rows = [next(row for row in frame if phase in row[2]) for frame, phase in zip(
        frames, ("Starting Medic", "Checking Fleetroll", "Next scan in 1:00", "Starting scan"), strict=True,
    )]
    assert {row[0] for row in phase_rows} == ({4} if logo_visible else {1})
    assert phase_rows[1][2].endswith("… · Recovery enabled")
    assert all(len(row[2]) <= width - 4 for row in phase_rows)


@pytest.mark.parametrize("waiting", [False, True])
@pytest.mark.parametrize("size", [(15, 84), (10, 84)])
def test_tui_rescan_key_only_signals_during_countdown(monkeypatch, tmp_path, waiting, size):
    import moonshot_medic_tui as tui

    status = tmp_path / "status.json"
    status.write_text(json.dumps({
        "phase": "Waiting for next scan" if waiting else "Checking Fleetroll",
        "next_run_at": 100 if waiting else None,
        "mode": "Confirmed recovery",
        "confirm": True,
        "looping": True,
        "logo": mm.MEDIC_LOGO,
    }))
    screen = Mock()
    screen.getmaxyx.return_value = size
    screen.getch.side_effect = [ord("r"), ord("q")]
    process = Mock()
    process.poll.side_effect = [None, 0]
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_: None)
    monkeypatch.setattr(tui.time, "time", lambda: 40)
    assert tui.display(screen, process, status, Mock(), tui.StopRequest()) == 0

    footer = next(call.args[2] for call in screen.addnstr.call_args_list if call.args[0] == size[0] - 1)
    status_pane = screen.derwin.return_value if size[0] >= 12 else screen
    rendered = [call.args[2] for call in status_pane.addnstr.call_args_list]
    assert any("Recovery enabled" in line for line in rendered)
    if size[0] >= 12:
        assert mm.MEDIC_LOGO[0] in rendered
    if waiting:
        assert "Next scan in 1:00 · Recovery enabled" in rendered
        assert footer.startswith("r: recheck now | ")
        process.send_signal.assert_called_once_with(signal.SIGUSR1)
    else:
        assert not any(line.startswith("Next scan in") for line in rendered)
        assert footer.startswith("r: recheck when idle | ")
        process.send_signal.assert_not_called()


def test_tui_second_ctrl_c_forces_exit_after_showing_shutdown_instructions(monkeypatch, tmp_path):
    import moonshot_medic_tui as tui

    screen = Mock()
    screen.getmaxyx.return_value = (10, 84)
    screen.getch.side_effect = [3, ord("q"), 3]
    process = Mock(pid=123)
    process.poll.return_value = None
    stop = tui.StopRequest()
    monkeypatch.setattr(tui.curses, "curs_set", lambda *_: None)

    def kill_process_group(pid, sig):
        assert (pid, sig) == (process.pid, signal.SIGKILL)
        process.send_signal.assert_called_once_with(signal.SIGINT)
        assert screen.getch.call_count == 3
        process.poll.return_value = -sig

    monkeypatch.setattr(tui.os, "killpg", kill_process_group)
    assert tui.display(screen, process, tmp_path / "status.json", Mock(), stop) == -signal.SIGKILL
    assert stop.forced
    rendered = [call.args[2] for call in screen.addnstr.call_args_list]
    assert "Stopping after current work finishes" in rendered
    assert any(line.startswith("Ctrl-C: exit immediately") for line in rendered)


def test_tui_shows_scan_details_and_dated_single_row_logs(monkeypatch, tmp_path):
    import moonshot_medic_tui as tui

    path = tmp_path / "status.json"
    monkeypatch.setattr(mm, "_tui_status_path", path)
    monkeypatch.setattr(mm, "_tui_status", {})
    mm._publish_tui(logo=mm.MEDIC_LOGO, scan_started_at="12:43")
    mm._tui_result("recovery", summary="0 hosts eligible", query="tc_act>4h")
    mm._tui_result("fleet_health", summary="3 hosts matched at scan time", query="healthy=n",
                   hosts={host: {"state": "matched", "reason": "Fleetroll health match"}
                          for host in ("ms126", "ms155", "ms274")})
    mm._tui_result("diagnosis", hosts={
        "ms126": {"state": "deferred", "reason": "latest host audit did not succeed"},
        "ms274": {"state": "task_resolution_stall", "reason": "claim-expired task"},
    })
    mm.log_check_2_intervention({
        "host": mm.worker_fqdn("ms274"),
        "diagnosis": {"worker_state": "task_resolution_stall", "reason": "claim-expired task"},
    }, "recovered", "new boot and worker ready", tmp_path / "report.json")
    mm.warn("Audit retry scheduled\n  for the next scan\t" + "界" * 80)
    status = json.loads(path.read_text())
    timestamp = status["fleet_health"]["updated_at"]
    check_2 = [
        (f"  Check 2 · Fleetroll health · 3 hosts matched at scan time · {timestamp}", ""),
        ("    Filter  healthy=n", "muted"),
        ("    ms126  Deferred · latest host audit did not succeed", "attention"),
        ("    ms155  Not diagnosed · Fleetroll health match", "attention"),
        ("    ms274  Recovered · Task resolution stall (claim-expired task) · new boot and worker ready",
         "success"),
    ]
    assert tui.dashboard_lines(status)[-len(check_2):] == check_2

    for event in status["activity"]:
        datetime.datetime.strptime(event["time"], "%Y-%m-%d %H:%M:%S")

    screen = Mock()
    screen.getmaxyx.return_value = (32, 100)
    frames = []

    def capture_frame():
        pane = screen.derwin.return_value
        frames.append([call.args[2] for call in pane.addnstr.call_args_list])
        pane.addnstr.reset_mock()

    screen.refresh.side_effect = capture_frame
    screen.getch.side_effect = [ord("d"), ord("q")]
    process = Mock()
    process.poll.return_value = 0
    assert tui.display(screen, process, path, Mock(), tui.StopRequest()) == 0
    assert frames[0] == frames[1]
    assert all(" | d:" not in call.args[2] for call in screen.addnstr.call_args_list)
    for frame in frames:
        assert mm.MEDIC_LOGO[0] in frame
        assert "Scan details · started 12:43" in frame
        assert "    Filter  tc_act>4h" in frame
        assert all(row in frame for row, _ in check_2)
        assert all("Matched hosts" not in row and "    Report " not in row for row in frame)
        assert " Logs " in frame
        entries = frame[frame.index(" Logs ") + 1:]
        assert len(entries) == 2
        assert entries[0].startswith(f"{status['activity'][0]['time']}  ms274  SSH reboot recovered")
        assert entries[1].startswith(f"{status['activity'][1]['time']}  Warning · "
                                     "Audit retry scheduled for the next scan")
        assert all("\n" not in entry for entry in entries)
        assert entries[1].endswith("…")
        assert len(entries[1]) + entries[1].count("界") <= 96


def test_rescan_shortens_only_the_current_wait(monkeypatch):
    monkeypatch.setattr(mm, "_rescan_requested", None)
    monkeypatch.setattr(mm, "_interrupt_count", 0)
    mm._rescan_signal_handler(signal.SIGUSR1, None)
    assert mm._rescan_requested is None

    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        mm._rescan_signal_handler(signal.SIGUSR1, None)

    monkeypatch.setattr(mm.time, "sleep", sleep)
    mm.wait_for_next_scan(15)
    assert sleeps == [1]
    assert mm._rescan_requested is None


@pytest.mark.parametrize("confirm,exit_code", [([], 1), (["--confirm"], 0)])
@pytest.mark.parametrize("no_reset", [[], ["--no-reset"]])
@pytest.mark.parametrize("state,zombies", [
    ("absent", "0 confirmed zombies"),
    ("live", "0 confirmed zombies"),
    ("zombie", "1 confirmed zombie"),
])
def test_auto_prints_diagnoses_without_reset_candidates(
    monkeypatch, tmp_path, capsys, selection, confirm, exit_code, no_reset, state, zombies,
):
    results_base = tmp_path / "results"
    monkeypatch.setattr(sys, "argv", ["medic", "--auto", "--once", "--no-voice", *confirm, *no_reset])
    monkeypatch.setattr(mm, "RESULTS_BASE", results_base)
    monkeypatch.setattr(mm, "FLEETROLL_DIR", tmp_path)
    monkeypatch.setattr(mm, "recovery_candidate_filter", lambda: "test recovery filter")
    monkeypatch.setattr(mm, "announce_overview_report", lambda: None)
    monkeypatch.setattr(mm, "check_fleetroll_data_freshness", lambda *a: True)
    monkeypatch.setattr(mm, "fleetroll_worker_snapshot", lambda *a, **kw: {})
    monkeypatch.setattr(mm, "check_fleet_reset_circuit_breaker", lambda *a: (True, [], ""))
    eligible = mm.eligible_auto_hosts
    monkeypatch.setattr(mm, "eligible_auto_hosts", lambda hosts, **kw: eligible(hosts, **kw) if hosts else [])
    diagnose = mm.diagnose_worker
    monkeypatch.setattr(mm, "diagnose_worker", lambda *a, **kw: {
        **diagnose(*a, **kw), "worker_state": state, "reason": "test diagnosis",
    })
    monkeypatch.setattr(mm, "reset_batch", lambda *a, **kw: pytest.fail("diagnosis triggered reset"))
    monkeypatch.setattr(mm, "load_state", lambda: {"hosts": {}})
    recoveries = []

    def recover(host, *args):
        recoveries.append(host)
        return {"outcome": "rebooted", "reason": "test recovery"}

    monkeypatch.setattr(mm, "recover_reachable_zombie", recover)
    records, calls = selection
    second = mm.worker_fqdn("ms026")
    records[second] = {**records[HOST], "host": second, "ok": False}

    with pytest.raises(SystemExit) as exc:
        mm.main()

    assert exc.value.code == exit_code
    assert calls == [HOST]
    assert recoveries == ([HOST] if confirm and state == "zombie" and not no_reset else [])
    output = capsys.readouterr().out
    assert f"1 diagnosed, {zombies}, 1 deferred" in output
    assert f"ms025  {state} — test diagnosis" in output
    assert "ms026  deferred — latest host audit did not succeed" in output
    assert "Action:" not in output
    assert "medic_worker_diagnosis" not in output
    report_path, = results_base.rglob("*-worker-diagnosis.json")
    assert str(report_path) in output
    assert len(json.loads(report_path.read_text())["results"]) == 2


@pytest.mark.parametrize("blocked_at,outcome,expected_steps", [
    ("", "rebooted", ["diagnostics", "freshness", "audit", "taskcluster", "reboot", "verify"]),
    ("diagnostics", "failed", ["diagnostics"]),
    ("freshness", "deferred", ["diagnostics", "freshness"]),
    ("audit", "deferred", ["diagnostics", "freshness", "audit"]),
    ("taskcluster", "deferred", ["diagnostics", "freshness", "audit", "taskcluster"]),
    ("verify", "unverified", ["diagnostics", "freshness", "audit", "taskcluster", "reboot", "verify"]),
])
def test_recover_zombie_collects_and_revalidates_before_reboot(
    monkeypatch, tmp_path, blocked_at, outcome, expected_steps,
):
    args = argparse.Namespace(startup_grace_minutes=15, freshness_min_pct=65, max_identity_age_hours=168)
    steps = []

    def step(name, value):
        steps.append(name)
        return value if name != blocked_at else None

    before = {"worker_state": "zombie", "boot_id": "old", "uptime_seconds": 1200,
              "processes": [{"state": "Z"}]}
    record = {"host": HOST, "ok": True, "ts": datetime.datetime.now(datetime.UTC).isoformat()}
    monkeypatch.setattr(mm, "diagnose_worker_probe", lambda *a, **kw: before)
    monkeypatch.setattr(mm, "recently_processed", lambda *a: False)
    def collect(*args, boot):
        assert boot == 0
        return step("diagnostics", True)

    monkeypatch.setattr(mm, "collect_diagnostics", collect)
    monkeypatch.setattr(mm, "check_fleetroll_data_freshness", lambda *a: step("freshness", True))
    monkeypatch.setattr(mm, "fleetroll_worker_snapshot", lambda *a, **kw: step("audit", {HOST: record}) or {})
    monkeypatch.setattr(mm, "eligible_auto_hosts", lambda *a, **kw: step("taskcluster", [HOST]))
    monkeypatch.setattr(mm, "request_zombie_reboot", lambda *a: step("reboot", "requested"))
    monkeypatch.setattr(mm, "wait_for_zombie_reboot", lambda *a: step("verify", {"boot_id": "new"}))
    monkeypatch.setattr(mm, "save_state", lambda *a: None)

    state = {"hosts": {}}
    result = mm.recover_reachable_zombie(HOST, args, 900, state, tmp_path)
    assert result["outcome"] == outcome
    assert steps == expected_steps
    if "reboot" in steps:
        assert state["hosts"][HOST]["last_actions"]["ssh_reboot"]["success"] == (outcome == "rebooted")


@pytest.mark.parametrize("changes", [
    {}, {"operator_hold": True}, {"boot_id": "new"}, {"uptime_seconds": 30},
    {"processes": [{"state": "Z"}, {"state": "S"}]}, {"unverified_pids": [25]},
])
def test_remote_reboot_requires_same_boot_and_zombie_only(monkeypatch, changes):
    evidence = {"boot_id": "old", "operator_hold": False, "unverified_pids": [],
                "uptime_seconds": 1200, "processes": [{"state": "Z"}], **changes}
    reboot_calls = []
    monkeypatch.setattr(sys, "argv", ["script", "old", "900"])
    monkeypatch.setattr(mm.subprocess, "run", lambda command, **kwargs: reboot_calls.append(command))
    program = mm.WORKER_REBOOT_IF_ZOMBIE.removeprefix(mm.WORKER_PROCESS_INSPECTION)
    if changes:
        with pytest.raises(SystemExit) as refused:
            exec(program, {"collect": lambda: evidence})
        assert refused.value.code == 3
        assert not reboot_calls
    else:
        exec(program, {"collect": lambda: evidence})
        assert reboot_calls == [["systemctl", "--no-block", "reboot"]]


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


@pytest.mark.parametrize("confirm", [[], ["--confirm"]])
def test_fleet_freshness_blocks_check_2(monkeypatch, capsys, tmp_path, confirm):
    monkeypatch.setattr(sys, "argv", ["medic", "--auto", "--once", "--no-voice", *confirm])
    monkeypatch.setattr(mm, "FLEETROLL_DIR", tmp_path)
    monkeypatch.setattr(mm, "announce_overview_report", lambda: None)
    monkeypatch.setattr(mm, "check_fleetroll_data_freshness", lambda *a: False)
    monkeypatch.setattr(mm, "show_unnoted_unhealthy_moonshots", lambda: pytest.fail("stale health queried"))
    monkeypatch.setattr(mm, "diagnose_reachable_workers", lambda **kw: pytest.fail("stale fleet diagnosed"))
    with pytest.raises(SystemExit) as exc:
        mm.main()
    assert exc.value.code == 1
    assert "Check 2 — Fleetroll health: Skipped — Fleetroll freshness check failed" in capsys.readouterr().out
