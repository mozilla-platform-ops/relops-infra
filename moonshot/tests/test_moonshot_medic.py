import datetime
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import moonshot_medic as mm


def _state():
    return {"hosts": {}}


def _future(hours=24):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)).isoformat()


def _past(hours=24):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)).isoformat()


FQDN = "t-linux64-ms-025.test.releng.mdc1.mozilla.com"


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
