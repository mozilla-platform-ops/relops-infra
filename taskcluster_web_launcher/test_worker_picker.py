"""Behavior tests for the worker picker; no network or browser required."""

import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

spec = importlib.util.spec_from_file_location(
    "worker_picker", Path(__file__).with_name("tcwl"), loader=importlib.machinery.SourceFileLoader("worker_picker", str(Path(__file__).with_name("tcwl")))
)
picker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(picker)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("worker-a", ("releng-hardware", "worker-a")),
        ("other/worker-b", ("other", "worker-b")),
        (
            "https://firefox-ci-tc.services.mozilla.com/provisioners/other/worker-types/worker-b?sortBy=Last%20Active",
            ("other", "worker-b"),
        ),
    ],
)
def test_parse_target(value, expected):
    assert picker.parse_target(value) == expected
    assert picker.worker_url(*expected).endswith(
        f"/provisioners/{expected[0]}/worker-types/{expected[1]}"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://firefox-ci-tc.services.mozilla.com/provisioners/a/worker-types/b",
        "https://example.com/provisioners/a/worker-types/b",
        "a/../b",
        "https://firefox-ci-tc.services.mozilla.com/provisioners/a/worker-types/b%2Fc",
    ],
)
def test_reject_invalid_targets(value):
    with pytest.raises(ValueError):
        picker.parse_target(value)


def test_history_persists_and_ranks_by_count_then_recency(tmp_path, monkeypatch):
    times = iter([1, 2, 3, 4])
    monkeypatch.setattr(picker.time, "time_ns", lambda: next(times))
    path = tmp_path / "nested" / "history.sqlite"
    with picker.database(path) as db:
        for worker in ("first", "second", "first", "third"):
            picker.record(db, ("pool", worker))
    with picker.database(path) as db:
        assert picker.history(db) == [
            ("pool", "first", 2),
            ("pool", "third", 1),
            ("pool", "second", 1),
        ]


def test_discovery_follows_continuation_tokens(monkeypatch):
    pages = iter(
        [
            {"workerTypes": [{"workerType": "a"}], "continuationToken": "a+b"},
            {"workerTypes": [{"workerType": "b"}]},
        ]
    )
    requests = []

    def fetch(request, timeout):
        requests.append(request.full_url)
        assert timeout == 8
        return io.BytesIO(json.dumps(next(pages)).encode())

    monkeypatch.setattr(picker.urllib.request, "urlopen", fetch)
    assert picker.discover("pool") == [("pool", "a"), ("pool", "b")]
    assert parse_qs(urlsplit(requests[1]).query)["continuationToken"] == ["a+b"]


class Screen:
    def __init__(self, keys):
        self.keys = iter(keys)
        self.lines = []

    def getmaxyx(self):
        return 24, 120

    def addnstr(self, _y, _x, text, *_args):
        self.lines.append(text)

    def get_wch(self):
        return next(self.keys)

    def __getattr__(self, _name):
        return lambda *_args: None


class ImmediateThread:
    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setattr(picker.curses, "curs_set", lambda *_: None)
    monkeypatch.setattr(picker.curses, "has_colors", lambda: False)
    monkeypatch.setattr(picker.threading, "Thread", ImmediateThread)


@pytest.fixture
def saved_db(tmp_path):
    db = picker.database(tmp_path / "history.sqlite")
    for target in [
        ("releng-hardware", "gone"),
        ("releng-hardware", "present"),
        ("other", "unknown"),
    ]:
        picker.record(db, target)
    yield db
    db.close()


def test_marks_only_missing_saved_workers_in_checked_provisioner(
    terminal, saved_db, monkeypatch
):
    monkeypatch.setattr(picker, "discover", lambda _: [("releng-hardware", "present")])
    screen = Screen(["\x1b"])
    assert picker.run_picker(screen, saved_db, "releng-hardware") is None
    assert any("gone" in line and "missing" in line for line in screen.lines)
    assert not any(
        "missing" in line and ("present" in line or "unknown" in line)
        for line in screen.lines
    )


def test_offline_does_not_mark_workers_missing(terminal, saved_db, monkeypatch):
    def offline(_):
        raise OSError("offline")

    monkeypatch.setattr(picker, "discover", offline)
    screen = Screen(["\x1b"])
    picker.run_picker(screen, saved_db, "releng-hardware")
    assert any("Offline:" in line for line in screen.lines)
    assert not any("missing" in line for line in screen.lines)


def test_filter_and_print_selection(terminal, saved_db, monkeypatch):
    monkeypatch.setattr(picker, "discover", lambda _: [])
    screen = Screen([*"present", "\x10"])
    assert picker.run_picker(screen, saved_db, "releng-hardware") == (
        ("releng-hardware", "present"),
        True,
    )


def test_interrupt_exits_cleanly_without_recording(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(picker.sys, "argv", ["tcwl", "--db", str(tmp_path / "history")])
    monkeypatch.setattr(picker.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(picker.sys.stdout, "isatty", lambda: True)

    def interrupt(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(picker.curses, "wrapper", interrupt)
    assert picker.main() == 130
    assert capsys.readouterr().err == "\nCancelled.\n"
    with picker.database(tmp_path / "history") as db:
        assert picker.history(db) == []
