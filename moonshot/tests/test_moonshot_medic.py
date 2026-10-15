import datetime
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import moonshot_medic as mm


class TestAnnouncements:
    @pytest.mark.parametrize("output,returncode,meeting", [
        ('{"meeting_assessment": "likely"}', 0, True),
        ('{"meeting_assessment": "unknown"}', 0, False),
        ('{"meeting_assessment": "likely"}', 1, False),
        ('invalid JSON', 0, False),
        ('{}', 0, False),
        ('null', 0, False),
        ('[]', 0, False),
    ])
    def test_slack_status(self, monkeypatch, output, returncode, meeting):
        def fake_run(cmd, **kwargs):
            assert cmd == ["slack-status"]
            assert kwargs == dict(capture_output=True, text=True, check=False, timeout=5)
            return mm.subprocess.CompletedProcess(cmd, returncode, stdout=output)

        monkeypatch.setattr(mm.subprocess, "run", fake_run)
        assert mm.in_slack_meeting() is meeting

    @pytest.mark.parametrize("error", [
        FileNotFoundError(), PermissionError(),
        mm.subprocess.TimeoutExpired("slack-status", 5),
    ])
    def test_unavailable_status(self, monkeypatch, error):
        def fake_run(*args, **kwargs):
            raise error

        monkeypatch.setattr(mm.subprocess, "run", fake_run)
        assert mm.in_slack_meeting() is False

    @pytest.mark.parametrize("day,hour,enabled,all_hours,meeting,speaks,checks_slack", [
        (14, 10, True, False, False, True, True),
        (14, 17, True, False, True, False, True),
        (14, 9, True, False, False, False, False),
        (14, 18, True, False, False, False, False),
        (19, 12, True, False, False, False, False),
        (20, 12, True, False, False, False, False),
        (14, 12, False, True, False, False, False),
        (19, 22, True, True, False, True, True),
        (19, 22, True, True, True, False, True),
    ])
    def test_speech_rules(self, monkeypatch, day, hour, enabled, all_hours,
                          meeting, speaks, checks_slack):
        class Clock(datetime.datetime):
            @classmethod
            def now(cls):
                return cls(2026, 9, day, hour)

        monkeypatch.setattr(mm.datetime, "datetime", Clock)
        monkeypatch.setattr(mm, "_voice_enabled", enabled)
        monkeypatch.setattr(mm, "_voice_all_hours", all_hours)
        checks = []
        calls = []

        def check_meeting():
            checks.append(True)
            return meeting

        monkeypatch.setattr(mm, "in_slack_meeting", check_meeting)
        monkeypatch.setattr(mm.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))
        mm.say("Hello")
        assert bool(checks) is checks_slack
        assert calls == ([((["say", "-v", "Rocko", "-r", "220", "Hello"],),
                          {"check": False})] if speaks else [])


def _state():
    return {"hosts": {}}


def _future(hours=24):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)).isoformat()


def _past(hours=24):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)).isoformat()


FQDN = "t-linux64-ms-025.test.releng.mdc1.mozilla.com"


def _freshness_report():
    return {
        "schema_version": 2,
        "status": "fresh",
        "host_source": "configs/host-lists/linux/all.list",
        "hosts_total": 280,
        "required_sources": ["host", "tc"],
        "sources": {
            "host": {"status": "fresh", "hosts_fresh": 270, "hosts_total": 280},
            "tc": {"status": "fresh", "hosts_fresh": 269, "hosts_total": 280},
        },
        "failures": [],
    }


class TestSshReadiness:
    def test_requires_authenticated_remote_command(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return mm.subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(mm.subprocess, "run", fake_run)

        assert mm.ssh_is_online(FQDN, timeout=12) is True
        cmd, kwargs = calls[0]
        assert cmd[0] == "ssh"
        assert "BatchMode=yes" in cmd
        assert "ConnectTimeout=12" in cmd
        assert cmd[-2:] == [FQDN, "true"]
        assert kwargs["timeout"] == 17

    def test_returns_false_when_session_cannot_start(self, monkeypatch):
        monkeypatch.setattr(
            mm.subprocess,
            "run",
            lambda *args, **kwargs: mm.subprocess.CompletedProcess(args[0], 255),
        )

        assert mm.ssh_is_online(FQDN) is False


class TestFleetrollFreshness:
    def test_accepts_fresh_schema_v2_report(self):
        valid, messages = mm.validate_fleetroll_freshness(
            mm.json.dumps(_freshness_report()), 0,
        )

        assert valid is True
        assert messages == ["host: fresh (270/280 hosts)", "tc: fresh (269/280 hosts)"]

    def test_rejects_nonempty_failures_even_if_status_is_fresh(self):
        report = _freshness_report()
        report["failures"] = [{"source": "tc", "reason": "collector failed"}]

        valid, messages = mm.validate_fleetroll_freshness(mm.json.dumps(report), 0)

        assert valid is False
        assert "Fleetroll tc freshness failure: collector failed" in messages

    @pytest.mark.parametrize("schema", [None, 1, 3])
    def test_rejects_unknown_or_missing_schema(self, schema):
        report = _freshness_report()
        report["schema_version"] = schema

        valid, messages = mm.validate_fleetroll_freshness(mm.json.dumps(report), 0)

        assert valid is False
        assert any("Unsupported Fleetroll freshness schema" in message for message in messages)

    def test_rejects_missing_required_tc_source(self):
        report = _freshness_report()
        report["required_sources"] = ["host"]
        del report["sources"]["tc"]

        valid, messages = mm.validate_fleetroll_freshness(mm.json.dumps(report), 0)

        assert valid is False
        assert "Fleetroll freshness report is missing required source(s): tc" in messages

    def test_rejects_malformed_required_sources_without_crashing(self):
        report = _freshness_report()
        report["required_sources"] = ["host", {"source": "tc"}]

        valid, messages = mm.validate_fleetroll_freshness(mm.json.dumps(report), 0)

        assert valid is False
        assert "Fleetroll freshness required_sources contains non-string values" in messages
        assert "Fleetroll freshness report is missing required source(s): tc" in messages

    def test_rejects_stale_source_and_failure(self):
        report = _freshness_report()
        report["status"] = "stale"
        report["sources"]["tc"]["status"] = "stale"
        report["failures"] = [{"source": "tc", "reason": "coverage is 10%"}]

        valid, messages = mm.validate_fleetroll_freshness(mm.json.dumps(report), 1)

        assert valid is False
        assert "Fleetroll tc data is 'stale' (269/280 hosts fresh)" in messages
        assert "Fleetroll tc freshness failure: coverage is 10%" in messages
        assert "Fleetroll overall freshness status is 'stale'" in messages
        assert "Fleetroll freshness command exited with status 1" in messages

    def test_rejects_invalid_json(self):
        valid, messages = mm.validate_fleetroll_freshness("not-json", 1)

        assert valid is False
        assert messages[0].startswith("Fleetroll returned invalid freshness JSON:")

    def test_command_requests_json_and_requires_fresh(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mm, "FLEETROLL_DIR", tmp_path)

        def fake_run(cmd, *, cwd, capture_output, text, check):
            assert cwd == tmp_path
            assert "--json" in cmd
            assert "--require-fresh" in cmd
            assert cmd[cmd.index("--stale-threshold") + 1] == "900"
            assert cmd[cmd.index("--min-fresh-pct") + 1] == "65"
            return mm.subprocess.CompletedProcess(
                cmd, 0, stdout=mm.json.dumps(_freshness_report()), stderr="",
            )

        monkeypatch.setattr(mm.subprocess, "run", fake_run)

        assert mm.check_fleetroll_data_freshness(900, 65) is True


class TestShortLabel:
    def test_fqdn(self):
        assert mm.short_label(FQDN) == "ms025"

    def test_short_dash_form(self):
        assert mm.short_label("t-linux64-ms-001") == "ms001"

    def test_short_nodash_form(self):
        assert mm.short_label("ms007") == "ms007"

    def test_leading_zero_strip(self):
        assert mm.short_label("ms-003") == "ms003"

    def test_three_digit_preserved(self):
        assert mm.short_label("ms-123") == "ms123"

    def test_unknown_form_returns_base(self):
        assert mm.short_label("some-random-host.example.com") == "some-random-host"


class TestWorkerFqdn:
    def test_fqdn_passthrough(self):
        assert mm.worker_fqdn(FQDN) == FQDN

    def test_short_dash_form(self):
        assert mm.worker_fqdn("ms-001") == "t-linux64-ms-001.test.releng.mdc1.mozilla.com"

    def test_short_nodash_form(self):
        assert mm.worker_fqdn("ms001") == "t-linux64-ms-001.test.releng.mdc1.mozilla.com"

    def test_mdc1_for_low_slot(self):
        assert ".mdc1." in mm.worker_fqdn("ms-001")

    def test_mdc2_for_high_slot(self):
        # slot 346 -> chassis 8 -> mdc2
        assert ".mdc2." in mm.worker_fqdn("ms-346")


class TestParseBadHosts:
    def test_accepts_short_and_fqdn_forms(self):
        raw = "ms001 ms-002 t-linux64-ms-003 t-linux64-ms-004.test.releng.mdc1.mozilla.com"
        hosts, ignored = mm.parse_bad_hosts(raw)

        assert hosts == [
            "ms001",
            "ms-002",
            "t-linux64-ms-003",
            "t-linux64-ms-004.test.releng.mdc1.mozilla.com",
        ]
        assert ignored == []

    def test_rejects_uv_warning_words(self):
        raw = (
            "warning: `VIRTUAL_ENV=/Users/aerickson/git/relops-infra/moonshot/.venv` "
            "does not match the project environment path `.venv` and will be ignored; "
            "use `--active` to target the active environment instead"
        )
        hosts, ignored = mm.parse_bad_hosts(raw)

        assert hosts == []
        assert ignored == raw.split()

    def test_keeps_valid_hosts_when_noise_is_present(self):
        raw = "ms025 warning: t-linux64-ms-026.test.releng.mdc1.mozilla.com ignored;"
        hosts, ignored = mm.parse_bad_hosts(raw)

        assert hosts == [
            "ms025",
            "t-linux64-ms-026.test.releng.mdc1.mozilla.com",
        ]
        assert ignored == ["warning:", "ignored;"]


class TestFleetResetCircuitBreaker:
    def test_allows_candidates_at_limit(self, monkeypatch):
        fleet = {mm.worker_fqdn(f"ms{i:03d}") for i in range(1, 101)}
        monkeypatch.setattr(mm, "_configured_linux_moonshot_hosts", lambda: fleet)

        allowed, candidates, summary = mm.check_fleet_reset_circuit_breaker(
            [f"ms{i:03d}" for i in range(1, 11)], 10,
        )

        assert allowed is True
        assert len(candidates) == 10
        assert "10/100 (10.0%; maximum 10%)" in summary

    def test_trips_above_limit(self, monkeypatch):
        fleet = {mm.worker_fqdn(f"ms{i:03d}") for i in range(1, 101)}
        monkeypatch.setattr(mm, "_configured_linux_moonshot_hosts", lambda: fleet)

        allowed, candidates, summary = mm.check_fleet_reset_circuit_breaker(
            [f"ms{i:03d}" for i in range(1, 12)], 10,
        )

        assert allowed is False
        assert len(candidates) == 11
        assert summary.startswith("Circuit breaker tripped")
        assert "--auto --once --confirm --max-fleet-reset-pct 11" in summary

    def test_deduplicates_before_calculating_percentage(self, monkeypatch):
        fleet = {mm.worker_fqdn(f"ms{i:03d}") for i in range(1, 11)}
        monkeypatch.setattr(mm, "_configured_linux_moonshot_hosts", lambda: fleet)

        allowed, candidates, _summary = mm.check_fleet_reset_circuit_breaker(
            ["ms001", "ms001"], 10,
        )

        assert allowed is True
        assert candidates == [mm.worker_fqdn("ms001")]

    def test_rejects_host_outside_inventory(self, monkeypatch):
        monkeypatch.setattr(
            mm, "_configured_linux_moonshot_hosts", lambda: {mm.worker_fqdn("ms001")},
        )

        allowed, _candidates, summary = mm.check_fleet_reset_circuit_breaker(
            ["ms999"], 100,
        )

        assert allowed is False
        assert "outside configured inventory: ms999" in summary

    def test_fails_closed_without_inventory(self, monkeypatch):
        monkeypatch.setattr(mm, "_configured_linux_moonshot_hosts", lambda: set())

        allowed, candidates, summary = mm.check_fleet_reset_circuit_breaker(["ms001"], 10)

        assert allowed is False
        assert candidates == []
        assert "Cannot load configured Moonshot inventory" in summary


class TestCircuitBreakerArgs:
    def test_once_requires_auto(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["moonshot_medic.py", "--once"])

        with pytest.raises(SystemExit) as exc:
            mm.main()

        assert exc.value.code == 1

    def test_raised_limit_requires_once(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv",
            ["moonshot_medic.py", "--auto", "--confirm", "--max-fleet-reset-pct", "21"],
        )

        with pytest.raises(SystemExit) as exc:
            mm.main()

        assert exc.value.code == 1


class TestHostEntry:
    def test_creates_default_entry(self):
        state = _state()
        h = mm._host_entry(state, FQDN)
        assert h["total_resets"] == 0
        assert h["consecutive_reset_failures"] == 0
        assert h["reset_timestamps"] == []
        assert h["skip_until"] is None

    def test_returns_existing_entry(self):
        state = _state()
        h1 = mm._host_entry(state, FQDN)
        h1["total_resets"] = 5
        h2 = mm._host_entry(state, FQDN)
        assert h2["total_resets"] == 5


class TestRecordResetSuccess:
    def test_increments_total_resets(self):
        state = _state()
        mm.record_reset_success(state, FQDN)
        assert state["hosts"][FQDN]["total_resets"] == 1

    def test_appends_timestamp(self):
        state = _state()
        mm.record_reset_success(state, FQDN)
        assert len(state["hosts"][FQDN]["reset_timestamps"]) == 1

    def test_clears_consecutive_failures(self):
        state = _state()
        mm._host_entry(state, FQDN)["consecutive_reset_failures"] = 3
        mm.record_reset_success(state, FQDN)
        assert state["hosts"][FQDN]["consecutive_reset_failures"] == 0

    def test_clears_skip_until(self):
        state = _state()
        mm._host_entry(state, FQDN)["skip_until"] = _future()
        mm.record_reset_success(state, FQDN)
        assert state["hosts"][FQDN]["skip_until"] is None

    def test_multiple_resets_accumulate(self):
        state = _state()
        mm.record_reset_success(state, FQDN)
        mm.record_reset_success(state, FQDN)
        mm.record_reset_success(state, FQDN)
        assert state["hosts"][FQDN]["total_resets"] == 3
        assert len(state["hosts"][FQDN]["reset_timestamps"]) == 3


class TestRecordResetFailure:
    def test_increments_consecutive(self):
        state = _state()
        mm.record_reset_failure(state, FQDN)
        assert state["hosts"][FQDN]["consecutive_reset_failures"] == 1

    def test_increments_total_failures(self):
        state = _state()
        mm.record_reset_failure(state, FQDN)
        assert state["hosts"][FQDN]["total_failures"] == 1

    def test_sets_last_failure(self):
        state = _state()
        mm.record_reset_failure(state, FQDN)
        assert state["hosts"][FQDN]["last_failure"] is not None

    def test_skip_set_at_threshold(self):
        state = _state()
        for _ in range(mm.SKIP_THRESHOLD_CONSECUTIVE):
            mm.record_reset_failure(state, FQDN)
        assert state["hosts"][FQDN]["skip_until"] is not None

    def test_no_skip_below_threshold(self):
        state = _state()
        for _ in range(mm.SKIP_THRESHOLD_CONSECUTIVE - 1):
            mm.record_reset_failure(state, FQDN)
        assert state["hosts"][FQDN]["skip_until"] is None


class TestIsSkipped:
    def test_not_skipped_by_default(self):
        state = _state()
        mm._host_entry(state, FQDN)
        assert mm.is_skipped(state, FQDN) is False

    def test_skipped_with_future_skip_until(self):
        state = _state()
        mm._host_entry(state, FQDN)["skip_until"] = _future(hours=1)
        assert mm.is_skipped(state, FQDN) is True

    def test_not_skipped_with_expired_skip_until(self):
        state = _state()
        mm._host_entry(state, FQDN)["skip_until"] = _past(hours=1)
        assert mm.is_skipped(state, FQDN) is False

    def test_unknown_host_not_skipped(self):
        state = _state()
        assert mm.is_skipped(state, "unknown.host") is False


class TestResetsSince:
    def test_counts_recent_timestamps(self):
        hosts = {
            FQDN: {"reset_timestamps": [_past(hours=1), _past(hours=2)]}
        }
        assert mm._resets_since(hosts, hours=3) == 2

    def test_excludes_old_timestamps(self):
        hosts = {
            FQDN: {"reset_timestamps": ["2020-01-01T00:00:00+00:00"]}
        }
        assert mm._resets_since(hosts, hours=24) == 0

    def test_mixed_timestamps(self):
        hosts = {
            FQDN: {"reset_timestamps": [_past(hours=1), "2020-01-01T00:00:00+00:00"]}
        }
        assert mm._resets_since(hosts, hours=3) == 1

    def test_multiple_hosts(self):
        fqdn2 = "t-linux64-ms-026.test.releng.mdc1.mozilla.com"
        hosts = {
            FQDN:  {"reset_timestamps": [_past(hours=1)]},
            fqdn2: {"reset_timestamps": [_past(hours=1)]},
        }
        assert mm._resets_since(hosts, hours=3) == 2

    def test_empty_hosts(self):
        assert mm._resets_since({}, hours=24) == 0

    def test_missing_reset_timestamps_key(self):
        hosts = {FQDN: {}}
        assert mm._resets_since(hosts, hours=24) == 0


class TestFleetDenominator:
    def test_canonical_moonshot_fqdn_accepts_short_label(self):
        assert mm.canonical_moonshot_fqdn("ms025") == FQDN

    def test_canonical_moonshot_fqdn_rejects_non_moonshot_text(self):
        assert mm.canonical_moonshot_fqdn("warning:") is None

    def test_ssh_observed_hosts_uses_fleetroll_cli(self, monkeypatch, tmp_path):
        host_list = tmp_path / "configs/host-lists/linux/all_moonshots.list"
        host_list.parent.mkdir(parents=True)
        host_list.write_text(FQDN + "\n")
        monkeypatch.setattr(mm, "FLEETROLL_DIR", tmp_path)

        def fake_run(cmd, *, cwd, capture_output, text, check):
            assert cmd[:4] == ["uv", "run", "fleetroll", "host-monitor"]
            assert cwd == tmp_path
            assert "--hostname-only" in cmd
            assert mm.MOONSHOT_OBSERVED_FILTER in cmd
            return mm.subprocess.CompletedProcess(cmd, 0, stdout=f"{FQDN}\nnot-a-host\n", stderr="")

        monkeypatch.setattr(mm.subprocess, "run", fake_run)

        assert mm._ssh_observed_linux_moonshot_hosts() == {FQDN}

    def test_ssh_observed_hosts_returns_none_when_cli_fails(self, monkeypatch, tmp_path):
        host_list = tmp_path / "configs/host-lists/linux/all_moonshots.list"
        host_list.parent.mkdir(parents=True)
        host_list.write_text(FQDN + "\n")
        monkeypatch.setattr(mm, "FLEETROLL_DIR", tmp_path)

        def fake_run(cmd, *, cwd, capture_output, text, check):
            return mm.subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        monkeypatch.setattr(mm.subprocess, "run", fake_run)

        assert mm._ssh_observed_linux_moonshot_hosts() is None

    def test_configured_hosts_falls_back_to_all_list(self, monkeypatch, tmp_path):
        host_list = tmp_path / "configs/host-lists/linux/all.list"
        host_list.parent.mkdir(parents=True)
        host_list.write_text(f"# comment\n{FQDN}\nnot-a-host\n")
        monkeypatch.setattr(mm, "FLEETROLL_DIR", tmp_path)

        assert mm._configured_linux_moonshot_hosts() == {FQDN}

    def test_never_reset_summary_uses_set_difference(self, monkeypatch):
        fqdn2 = "t-linux64-ms-026.test.releng.mdc1.mozilla.com"
        fqdn3 = "t-linux64-ms-027.test.releng.mdc1.mozilla.com"
        state_hosts = {
            FQDN: {"total_resets": 2},
            fqdn3: {"total_resets": 1},
        }
        monkeypatch.setattr(
            mm,
            "_report_fleet_hosts",
            lambda: ({FQDN, fqdn2}, "SSH-observed Linux Moonshots"),
        )

        assert mm._never_reset_summary(state_hosts) == (
            1,
            2,
            50,
            "SSH-observed Linux Moonshots",
        )


class TestOverviewHtml:
    def test_writes_favicon_and_links_it(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mm, "RESULTS_BASE", tmp_path)
        monkeypatch.setattr(mm, "OVERVIEW_HTML_FILE", tmp_path / "OVERVIEW.html")
        monkeypatch.setattr(mm, "FAVICON_FILE", tmp_path / "favicon.svg")
        monkeypatch.setattr(mm, "_report_fleet_hosts", lambda: ({FQDN}, "SSH-observed Linux Moonshots"))

        mm.update_overview_html({"hosts": {FQDN: {"total_resets": 1, "reset_timestamps": []}}})

        html = (tmp_path / "OVERVIEW.html").read_text()
        favicon = (tmp_path / "favicon.svg").read_text()
        assert '<link rel="icon" href="favicon.svg" type="image/svg+xml">' in html
        assert "<svg" in favicon
        assert "Moonshot" not in favicon

    def test_announce_overview_report_prints_html_path(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(mm, "OVERVIEW_HTML_FILE", tmp_path / "OVERVIEW.html")

        mm.announce_overview_report()

        out = capsys.readouterr().out
        assert "Overview report:" in out
        assert str(tmp_path / "OVERVIEW.html") in out
