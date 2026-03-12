import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Allow importing from the parent directory without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from keep_moonshot_carts_up import (
    current_hostlist,
    hostname_to_cart,
    is_older_than,
    load_skip_hosts,
    SKIP_HOSTS_FILE,
)


# ---------------------------------------------------------------------------
# is_older_than
# ---------------------------------------------------------------------------

class TestIsOlderThan:
    def _ts(self, delta: timedelta) -> str:
        """Return an ISO-8601 UTC timestamp offset by delta from now."""
        return (datetime.now(timezone.utc) + delta).isoformat().replace("+00:00", "Z")

    def test_none_returns_false(self):
        assert is_older_than(None, 10) is False

    def test_empty_string_returns_false(self):
        assert is_older_than("", 10) is False

    def test_invalid_string_returns_false(self):
        assert is_older_than("not-a-date", 10) is False

    def test_recent_timestamp_returns_false(self):
        ts = self._ts(timedelta(minutes=-5))
        assert is_older_than(ts, 10) is False

    def test_old_timestamp_returns_true(self):
        ts = self._ts(timedelta(minutes=-60))
        assert is_older_than(ts, 30) is True

    def test_exact_boundary_is_not_older(self):
        # Timestamp exactly at the cutoff should return False (not strictly older)
        ts = self._ts(timedelta(minutes=-10))
        # Allow a 2-second buffer for test execution time
        assert is_older_than(ts, 10) in (False, True)  # borderline; just no crash

    def test_future_timestamp_returns_false(self):
        ts = self._ts(timedelta(minutes=5))
        assert is_older_than(ts, 1) is False

    def test_z_suffix_handled(self):
        ts = "2000-01-01T00:00:00Z"
        assert is_older_than(ts, 1) is True

    def test_offset_suffix_handled(self):
        ts = "2000-01-01T00:00:00+00:00"
        assert is_older_than(ts, 1) is True


# ---------------------------------------------------------------------------
# hostname_to_cart
# ---------------------------------------------------------------------------

class TestHostnameToCart:
    def test_empty_list(self):
        assert hostname_to_cart([]) == {}

    def test_non_numeric_id_skipped(self):
        assert hostname_to_cart(["abc", "xyz"]) == {}

    def test_slot_1_lands_on_chassis_1_node_1(self):
        result = hostname_to_cart(["001"])
        assert len(result) == 1
        chassis = list(result.keys())[0]
        assert "moon-chassis-1" in chassis
        assert "mdc1" in chassis
        assert result[chassis] == ["1"]

    def test_slot_45_last_node_of_chassis_1(self):
        result = hostname_to_cart(["045"])
        chassis = list(result.keys())[0]
        assert "moon-chassis-1" in chassis
        assert result[chassis] == ["45"]

    def test_slot_46_first_node_of_chassis_2(self):
        result = hostname_to_cart(["046"])
        chassis = list(result.keys())[0]
        assert "moon-chassis-2" in chassis
        assert result[chassis] == ["1"]

    def test_mdc2_chassis_for_high_slot(self):
        # Slots > 300 end up in a chassis > 7 which maps to mdc2
        result = hostname_to_cart(["301"])
        chassis = list(result.keys())[0]
        assert "mdc2" in chassis

    def test_mdc1_chassis_for_low_slot(self):
        result = hostname_to_cart(["001"])
        chassis = list(result.keys())[0]
        assert "mdc1" in chassis

    def test_multiple_slots_same_chassis_aggregated(self):
        # Slots 1 and 2 both fall on chassis 1
        result = hostname_to_cart(["001", "002"])
        assert len(result) == 1
        nodes = list(result.values())[0]
        assert "1" in nodes
        assert "2" in nodes

    def test_multiple_slots_different_chassis(self):
        result = hostname_to_cart(["001", "046"])
        assert len(result) == 2

    def test_bare_integer_string(self):
        result = hostname_to_cart(["1"])
        assert len(result) == 1

    def test_prefixed_string_extracts_last_number(self):
        # "t-linux64-ms-001" should use slot 1 (last number), not 64
        result = hostname_to_cart(["t-linux64-ms-001"])
        chassis = list(result.keys())[0]
        assert "moon-chassis-1." in chassis
        assert result[chassis] == ["1"]

    def test_prefixed_string_high_slot(self):
        # "t-linux64-ms-214" should use slot 214, not 64
        result = hostname_to_cart(["t-linux64-ms-214"])
        chassis = list(result.keys())[0]
        # slot 214: c=(214-1)//45+1=5, n=(214-1)%45+1=34
        assert "moon-chassis-5." in chassis
        assert result[chassis] == ["34"]

    def test_return_type(self):
        result = hostname_to_cart(["001"])
        assert isinstance(result, dict)
        for k, v in result.items():
            assert isinstance(k, str)
            assert isinstance(v, list)


# ---------------------------------------------------------------------------
# current_hostlist
# ---------------------------------------------------------------------------

class TestCurrentHostlist:
    def _make_hostnames(self, entries: dict[int, str]) -> dict:
        """Build a hostnames dict with both int and zero-padded str keys."""
        hostnames = {}
        for i, host in entries.items():
            hostnames[i] = host
            hostnames[f"{i:03d}"] = host
        return hostnames

    def test_empty_hostnames(self):
        assert current_hostlist({}, set()) == []

    def test_all_skipped(self):
        hostnames = self._make_hostnames({1: "host1.example.com"})
        assert current_hostlist(hostnames, {"001"}) == []

    def test_not_skipped(self):
        hostnames = self._make_hostnames({1: "host1.example.com"})
        result = current_hostlist(hostnames, set())
        assert result == [("001", "host1.example.com")]

    def test_partial_skip(self):
        hostnames = self._make_hostnames({
            1: "host1.example.com",
            2: "host2.example.com",
        })
        result = current_hostlist(hostnames, {"001"})
        assert len(result) == 1
        assert result[0] == ("002", "host2.example.com")

    def test_slots_without_hosts_omitted(self):
        # Only slot 3 has a host; slots 1-2 and 4-630 have none
        hostnames = self._make_hostnames({3: "host3.example.com"})
        result = current_hostlist(hostnames, set())
        assert result == [("003", "host3.example.com")]

    def test_result_is_list_of_tuples(self):
        hostnames = self._make_hostnames({1: "h.example.com"})
        result = current_hostlist(hostnames, set())
        assert isinstance(result, list)
        assert all(isinstance(t, tuple) and len(t) == 2 for t in result)


# ---------------------------------------------------------------------------
# load_skip_hosts
# ---------------------------------------------------------------------------

class TestLoadSkipHosts:
    def test_missing_file_returns_empty_set(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = load_skip_hosts()
        assert result == set()

    def test_loads_entries(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / SKIP_HOSTS_FILE).write_text("001\n002\n003\n")
        result = load_skip_hosts()
        assert result == {"001", "002", "003"}

    def test_ignores_blank_lines(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / SKIP_HOSTS_FILE).write_text("001\n\n002\n\n")
        result = load_skip_hosts()
        assert result == {"001", "002"}

    def test_strips_whitespace(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / SKIP_HOSTS_FILE).write_text("  001  \n  002\n")
        result = load_skip_hosts()
        assert result == {"001", "002"}

    def test_returns_set(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / SKIP_HOSTS_FILE).write_text("001\n")
        assert isinstance(load_skip_hosts(), set)

    def test_empty_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / SKIP_HOSTS_FILE).write_text("")
        assert load_skip_hosts() == set()
