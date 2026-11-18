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
    def __init__(self, keys, height=24, width=120):
        self.keys = iter(keys)
        self.lines = []
        self.height = height
        self.width = width
        self.canvas = {}
        self.frames = []
        self.segments = []

    def getmaxyx(self):
        return self.height, self.width

    def addnstr(self, y, x, text, limit, attr=0):
        self.lines.append(text)
        self.segments.append((y, x, text[:limit], attr))
        row = self.canvas.setdefault(y, [" "] * self.width)
        for offset, char in enumerate(text[:limit]):
            assert 0 <= y < self.height and 0 <= x + offset < self.width
            row[x + offset] = char

    def erase(self):
        self.canvas = {}

    def refresh(self):
        self.frames.append("\n".join(
            "".join(self.canvas.get(y, [])).rstrip() for y in range(self.height)
        ))

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
    monkeypatch.setattr(picker, "discover_provisioners", lambda: [])


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
    screen = Screen(["\x1b", "\x03"])
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
    screen = Screen(["\x1b", "\x03"])
    picker.run_picker(screen, saved_db, "releng-hardware")
    assert any("Refresh failed" in line for line in screen.lines)
    assert not any("missing" in line for line in screen.lines)


def test_filter_and_print_selection(terminal, saved_db, monkeypatch):
    monkeypatch.setattr(picker, "discover", lambda _: [])
    screen = Screen([*"present", "\x10"])
    assert picker.run_picker(screen, saved_db, "releng-hardware") == (
        ("releng-hardware", "present"),
        True,
    )
    assert any("releng-hardware/present" in line for line in screen.lines)


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


def test_provisioner_discovery_follows_pagination(monkeypatch):
    pages = iter([
        {"provisioners": [{"provisionerId": "gecko-t"}], "continuationToken": "next"},
        {"provisioners": [{"provisionerId": "proj-autophone"}]},
    ])
    urls = []

    def fetch(request, timeout):
        urls.append(request.full_url)
        return io.BytesIO(json.dumps(next(pages)).encode())

    monkeypatch.setattr(picker.urllib.request, "urlopen", fetch)
    assert picker.discover_provisioners() == ["gecko-t", "proj-autophone"]
    assert urlsplit(urls[0]).path == "/api/queue/v1/provisioners"
    assert parse_qs(urlsplit(urls[1]).query)["continuationToken"] == ["next"]


def test_defaults_preload_two_and_other_provisioners_load_lazily(
    terminal, saved_db, monkeypatch
):
    calls = []
    monkeypatch.setattr(picker, "discover_provisioners", lambda: ["gecko-t", "other"])

    def discover(name):
        calls.append(name)
        return [(name, "worker")]

    monkeypatch.setattr(picker, "discover", discover)
    catalog = picker.Catalog(saved_db)
    catalog.poll()
    assert calls == ["releng-hardware", "proj-autophone"]
    assert "gecko-t" in catalog.names
    catalog.activate("gkt")
    assert "gecko-t" not in calls
    catalog.activate("gecko-t/worker")
    catalog.poll()
    assert calls == ["releng-hardware", "proj-autophone", "gecko-t"]
    assert catalog.known["gecko-t"] == [("gecko-t", "worker")]
    catalog.activate("gecko-t")
    assert len(calls) == 3


def test_fuzzy_provisioner_selection_loads_workers(terminal, saved_db, monkeypatch):
    calls = []
    monkeypatch.setattr(picker, "discover_provisioners", lambda: ["gecko-t"])

    def discover(name):
        calls.append(name)
        return [(name, "worker")]

    monkeypatch.setattr(picker, "discover", discover)
    screen = Screen([*"gkt", "\n", "\x10"])
    assert picker.run_picker(screen, saved_db) == (("gecko-t", "worker"), True)
    assert calls == ["releng-hardware", "proj-autophone", "gecko-t"]


def test_cache_persists_across_sessions_and_expires(terminal, tmp_path, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(picker.time, "time", lambda: clock[0])
    calls = []
    monkeypatch.setattr(picker, "discover_provisioners", lambda: ["gecko-t"])

    def discover(name):
        calls.append(name)
        return [(name, "worker")]

    monkeypatch.setattr(picker, "discover", discover)
    path = tmp_path / "cache.sqlite"
    with picker.database(path) as db:
        first = picker.Catalog(db)
        first.poll()
        first.activate("gecko-t")
        first.poll()
    calls.clear()
    with picker.database(path) as db:
        second = picker.Catalog(db)
        second.activate("gecko-t")
        second.poll()
        assert calls == []
        assert second.known["gecko-t"] == [("gecko-t", "worker")]
        assert not second.verified
        assert not second.pending
        second.refresh()
        second.poll()
        assert set(calls) == {"releng-hardware", "proj-autophone", "gecko-t"}
    clock[0] += picker.CACHE_TTL + 1
    calls.clear()

    def offline(name):
        calls.append(name)
        raise OSError("offline")

    monkeypatch.setattr(picker, "discover", offline)
    with picker.database(path) as db:
        third = picker.Catalog(db)
        third.activate("gecko-t")
        third.poll()
        assert "gecko-t" in calls
        assert third.known["gecko-t"] == [("gecko-t", "worker")]
        assert not third.verified
        assert "gecko-t" in third.message()
        assert "Offline:" in third.message()


def test_partial_failure_only_marks_missing_in_successful_provisioner(
    terminal, saved_db, monkeypatch
):
    picker.record(saved_db, ("proj-autophone", "unknown-phone"))
    monkeypatch.setattr(picker, "discover_provisioners", lambda: [])

    def discover(name):
        if name == "proj-autophone":
            raise OSError("offline")
        return [(name, "present")]

    monkeypatch.setattr(picker, "discover", discover)
    screen = Screen(["\x03"])
    picker.run_picker(screen, saved_db)
    assert any("gone" in line and "missing" in line for line in screen.lines)
    assert not any("unknown-phone" in line and "missing" in line for line in screen.lines)


@pytest.mark.parametrize(("query", "target", "expected"), [
    ("", ("gecko-t", "linux-worker"), 0),
    ("gecko-t", ("gecko-t", "linux-worker"), 0),
    ("linux", ("gecko-t", "linux-worker"), 1),
    ("zzz", ("gecko-t", "linux-worker"), None),
])
def test_fuzzy_matching(query, target, expected):
    assert picker.fuzzy_score(query, target) == expected


def test_fuzzy_matches_rank_below_substrings_and_require_all_terms():
    assert picker.fuzzy_score("gkt lnwrk", ("gecko-t", "linux-worker")) > 1
    assert picker.fuzzy_score("gkt zzz", ("gecko-t", "linux-worker")) is None


def test_refresh_does_not_duplicate_pending_requests(saved_db, monkeypatch):
    jobs = []

    class DeferredThread:
        def __init__(self, target, **_kwargs):
            jobs.append(target)

        def start(self):
            pass

    monkeypatch.setattr(picker.threading, "Thread", DeferredThread)
    monkeypatch.setattr(picker, "discover", lambda p: [(p, "present")])
    catalog = picker.Catalog(saved_db, "releng-hardware")
    assert len(jobs) == 1
    catalog.refresh()
    assert len(jobs) == 1
    assert not catalog.verified
    jobs.pop()()
    catalog.poll()
    assert catalog.verified == {"releng-hardware"}
    catalog.refresh()
    assert len(jobs) == 1
    assert not catalog.verified


def test_cached_provisioner_names_avoid_request_and_refresh_bypasses_cache(
    terminal, saved_db, monkeypatch
):
    picker.write_cache(saved_db, "provisioners", ["gecko-t"])
    calls = []
    monkeypatch.setattr(picker, "discover", lambda _: [])

    def names():
        calls.append("names")
        return ["gecko-t", "new-provisioner"]

    monkeypatch.setattr(picker, "discover_provisioners", names)
    catalog = picker.Catalog(saved_db)
    catalog.poll()
    assert "gecko-t" in catalog.names
    assert calls == []
    catalog.refresh()
    catalog.poll()
    assert calls == ["names"]
    assert "new-provisioner" in catalog.names


def test_unloaded_provisioners_are_searchable_and_counts_are_visible(
    terminal, saved_db, monkeypatch
):
    monkeypatch.setattr(picker, "discover_provisioners", lambda: ["gecko-t", "infra"])
    monkeypatch.setattr(picker, "discover", lambda p: [(p, "worker")])
    screen = Screen([*"infra", "\x03"])
    picker.run_picker(screen, saved_db)
    assert any("› infra" in line and "Browse" in line for line in screen.lines)
    assert any("5 provisioners" in line and "2 pools loaded" in line for line in screen.lines)
    assert any("1–1 of 1" in line for line in screen.lines)


@pytest.mark.parametrize(("keys", "worker"), [
    ([picker.curses.KEY_RIGHT], "worker-16"),
    ([picker.curses.KEY_LEFT], "worker-00"),
    ([picker.curses.KEY_RIGHT, picker.curses.KEY_RIGHT], "worker-29"),
    ([picker.curses.KEY_RIGHT, picker.curses.KEY_LEFT], "worker-00"),
    ([picker.curses.KEY_NPAGE, picker.curses.KEY_PPAGE], "worker-00"),
])
def test_page_navigation(terminal, tmp_path, monkeypatch, keys, worker):
    monkeypatch.setattr(
        picker, "discover", lambda p: [(p, f"worker-{i:02}") for i in range(30)]
    )
    with picker.database(tmp_path / "history.sqlite") as db:
        screen = Screen([*"worker-", *keys, "\x10"])
        assert picker.run_picker(screen, db, "releng-hardware") == (
            ("releng-hardware", worker), True
        )


def test_home_groups_frequently_used_and_provisioners_without_other_pools(
    terminal, saved_db, monkeypatch
):
    monkeypatch.setattr(picker, "discover_provisioners", lambda: ["gecko-t", "infra"])
    monkeypatch.setattr(picker, "discover", lambda p: [(p, "worker")])
    picker.record(saved_db, ("releng-hardware", "present"))
    screen = Screen(["\x10"])
    assert picker.run_picker(screen, saved_db) == (("releng-hardware", "present"), True)
    assert any("Frequently used" in line for line in screen.lines)
    assert any("Provisioners" in line for line in screen.lines)
    assert not any("proj-autophone/worker" in line for line in screen.lines)
    assert not any("★" in line or "  live" in line for line in screen.lines)
    first_group = next(i for i, line in enumerate(screen.lines) if "Frequently used" in line)
    used = next(i for i, line in enumerate(screen.lines) if "releng-hardware/present" in line)
    second_group = next(i for i, line in enumerate(screen.lines) if line.strip() == "Provisioners")
    assert first_group < used < second_group
    assert any("releng-hardware/present" in line and "2 uses" in line for line in screen.lines)


def test_selecting_preloaded_provisioner_browses_without_refetch(
    terminal, tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(picker, "discover_provisioners", lambda: ["gecko-t"])

    def discover(name):
        calls.append(name)
        return [(name, "worker")]

    monkeypatch.setattr(picker, "discover", discover)
    with picker.database(tmp_path / "history.sqlite") as db:
        screen = Screen([picker.curses.KEY_DOWN, "\n", "\x10"])
        assert picker.run_picker(screen, db) == (("proj-autophone", "worker"), True)
    assert calls == ["releng-hardware", "proj-autophone"]


def test_selection_stays_on_target_when_results_arrive(terminal, saved_db, monkeypatch):
    monkeypatch.setattr(picker, "discover_provisioners", lambda: ["gecko-t"])
    monkeypatch.setattr(picker, "discover", lambda p: [(p, "linux-z")])
    catalog = picker.Catalog(saved_db)
    catalog.poll()
    model = picker.Picker()
    model.search("linux")
    model.update(catalog, {})
    model.move(1)
    selected = model.target
    old_index = model.selected
    catalog.events.put(("workers:gecko-t", [("gecko-t", "linux-a")], None))
    catalog.poll()
    model.update(catalog, {})
    assert model.target == selected
    assert model.selected == old_index + 1


def test_loaded_provisioner_remains_selected_during_discovery(
    terminal, saved_db, monkeypatch
):
    monkeypatch.setattr(picker, "discover_provisioners", lambda: [])
    monkeypatch.setattr(picker, "discover", lambda p: [])
    catalog = picker.Catalog(saved_db)
    catalog.poll()
    model = picker.Picker()
    model.update(catalog, {})
    model.move(1)
    selected = model.target
    catalog.events.put(("provisioners", ["aaa-new"], None))
    catalog.poll()
    model.update(catalog, {})
    assert model.target == selected


@pytest.mark.parametrize("back", ["\x1b", "\x02", "\x7f"])
def test_scope_browsing_and_return_to_all_provisioners(
    terminal, tmp_path, monkeypatch, back
):
    monkeypatch.setattr(picker, "discover_provisioners", lambda: ["gecko-t"])
    monkeypatch.setattr(picker, "discover", lambda p: [(p, "linux-worker")])
    screen = Screen([*"gkt", "\n", back, "\x03"])
    with picker.database(tmp_path / "history.sqlite") as db:
        assert picker.run_picker(screen, db) is None
    assert any("tcwl · gecko-t" in frame and "Ctrl-B All provisioners" in frame for frame in screen.frames)
    assert "tcwl · gecko-t" not in screen.frames[-1]
    assert "Provisioners" in screen.frames[-1]


def test_clear_search_stays_in_scope_and_manual_pool_uses_scope(
    terminal, tmp_path, monkeypatch
):
    monkeypatch.setattr(picker, "discover", lambda p: [(p, "worker")])
    screen = Screen([*"discard", "\x15", *"new-pool", "\x10"])
    with picker.database(tmp_path / "history.sqlite") as db:
        assert picker.run_picker(screen, db, "gecko-t") == (("gecko-t", "new-pool"), True)
    assert "Search pools › new-pool" in screen.frames[-1]
    assert any("Search pools ›\n" in frame for frame in screen.frames)


def test_match_highlighting_corresponds_to_displayed_characters(
    terminal, tmp_path, monkeypatch
):
    monkeypatch.setattr(picker, "discover", lambda p: [(p, "linux-worker")])
    screen = Screen([*"lnwrk", "\x10"])
    with picker.database(tmp_path / "history.sqlite") as db:
        assert picker.run_picker(screen, db, "gecko-t") == (("gecko-t", "linux-worker"), True)
    highlighted = [text for _, _, text, attr in screen.segments if attr & picker.curses.A_UNDERLINE]
    assert "".join(highlighted[-5:]) == "lnwrk"


@pytest.mark.parametrize(("height", "width"), [(24, 80), (12, 40), (8, 24)])
def test_small_terminal_keeps_navigation_and_long_paths_usable(
    terminal, tmp_path, monkeypatch, height, width
):
    worker = "gecko-t-linux-" + "long-" * 20 + "wayland"
    monkeypatch.setattr(picker, "discover", lambda p: [(p, worker)])
    screen = Screen(["\x10"], height=height, width=width)
    with picker.database(tmp_path / "history.sqlite") as db:
        assert picker.run_picker(screen, db, "releng-hardware") == (("releng-hardware", worker), True)
    assert "…" in screen.frames[-1]
    assert all(len(line) < width for line in screen.frames[-1].splitlines())


def test_scoped_failure_shows_retry_state_and_does_not_open_a_placeholder(
    terminal, tmp_path, monkeypatch
):
    def offline(_):
        raise OSError("network unavailable")

    monkeypatch.setattr(picker, "discover", offline)
    screen = Screen(["\n", "\x1b", "\x03"])
    with picker.database(tmp_path / "history.sqlite") as db:
        assert picker.run_picker(screen, db, "gecko-t") is None
    assert any("Could not load pools" in frame for frame in screen.frames)
    assert any("network unavailable" in frame for frame in screen.frames)


def test_shortened_paths_keep_tail_and_highlight_positions():
    text = "releng-hardware/gecko-t-linux-wayland"
    label, positions = picker.shorten(text, 24)
    assert len(label) == 24
    assert label.endswith("linux-wayland")
    assert all(original is None or label[i] == text[original] for i, original in enumerate(positions))


def test_fuzzy_provisioner_match_beats_same_letters_inside_pool_path(
    terminal, tmp_path, monkeypatch
):
    monkeypatch.setattr(picker, "discover_provisioners", lambda: ["gecko-t"])
    monkeypatch.setattr(picker, "discover", lambda p: [(p, "gecko-t-linux-worker")])
    screen = Screen([*"gkt", "\n", "\x10"])
    with picker.database(tmp_path / "history.sqlite") as db:
        assert picker.run_picker(screen, db) == (("gecko-t", "gecko-t-linux-worker"), True)
    assert "tcwl · gecko-t" in screen.frames[-1]


def test_fuzzy_highlights_choose_the_tightest_matching_span():
    target = ("releng-hardware", "gecko-t-linux-worker")
    match = picker.fuzzy_match("gkt", target)
    assert match is not None
    _, positions = match
    text = "/".join(target)
    assert "".join(text[i] for i in sorted(positions)) == "gkt"
    assert min(positions) == text.index("gecko-t")


def test_escape_clears_filter_then_leaves_scope_and_never_quits(
    terminal, tmp_path, monkeypatch
):
    monkeypatch.setattr(picker, "discover", lambda p: [(p, "linux-worker")])
    screen = Screen([*"linux", "\x1b", "\x1b", "\x1b", "\x03"])
    with picker.database(tmp_path / "history.sqlite") as db:
        assert picker.run_picker(screen, db, "gecko-t") is None
    assert "Search pools › linux" in screen.frames[-4]
    assert "tcwl · gecko-t" in screen.frames[-3]
    assert "Search pools ›\n" in screen.frames[-3]
    assert "tcwl · gecko-t" not in screen.frames[-2]
    assert screen.frames[-2] == screen.frames[-1]
    assert "Ctrl-C quit" in screen.frames[-1]


def test_escape_clears_global_search_without_quitting(terminal, saved_db, monkeypatch):
    monkeypatch.setattr(picker, "discover", lambda p: [])
    screen = Screen([*"present", "\x1b", "\x1b", "\x03"])
    assert picker.run_picker(screen, saved_db) is None
    assert "Search › present" in screen.frames[-3]
    assert "Frequently used" in screen.frames[-2]
    assert screen.frames[-2] == screen.frames[-1]


def test_color_palette_preserves_selection_background_during_highlighting(
    terminal, tmp_path, monkeypatch
):
    pairs = []
    monkeypatch.setattr(picker.curses, "has_colors", lambda: True)
    monkeypatch.setattr(picker.curses, "start_color", lambda: None)
    monkeypatch.setattr(picker.curses, "use_default_colors", lambda: None)
    monkeypatch.setattr(picker.curses, "init_pair", lambda *args: pairs.append(args))
    monkeypatch.setattr(picker.curses, "color_pair", lambda n: n << 8)
    monkeypatch.setattr(picker, "discover", lambda p: [(p, "linux-worker")])
    screen = Screen([*"lnwrk", "\x10"])
    with picker.database(tmp_path / "history.sqlite") as db:
        assert picker.run_picker(screen, db, "gecko-t") == (("gecko-t", "linux-worker"), True)
    assert {number for number, _, _ in pairs} == {1, 2, 3, 4, 5, 6}
    assert pairs[-1] == (6, picker.curses.COLOR_BLACK, picker.curses.COLOR_CYAN)
    highlights = [attr for _, _, _, attr in screen.segments if attr & picker.curses.A_UNDERLINE]
    assert highlights
    assert all(attr & 0xFF00 == 6 << 8 for attr in highlights)
