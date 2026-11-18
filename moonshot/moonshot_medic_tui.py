"""Terminal dashboard for an ordinary Moonshot Medic process."""

import codecs
import curses
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path
from textwrap import wrap


STATUS_ENV = "MOONSHOT_MEDIC_TUI_STATUS"
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SECTIONS = ("recovery", "fleet_health", "diagnosis")


class MedicLogs:
    def __init__(self, fd: int) -> None:
        self.fd = fd
        os.set_blocking(fd, False)
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.lines: deque[str] = deque(maxlen=1000)
        self.pending = ""
        self.closed = False

    def read(self) -> None:
        if self.closed:
            return
        try:
            chunk = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        self.closed = not chunk
        text = self.pending + self.decoder.decode(chunk, final=self.closed)
        *lines, self.pending = text.split("\n")
        self.lines.extend(line[-4096:] for line in lines)
        self.pending = self.pending[-4096:]

    def display_lines(self) -> list[str]:
        lines = list(self.lines)
        if self.pending:
            lines.append(self.pending)
        return [ANSI_ESCAPE.sub("", line.rstrip("\r")).replace("\r", " ").expandtabs(4)
                for line in lines]


def status_lines(status: dict, returncode: int | None, stopping: bool) -> list[str]:
    phase = status.get("phase", "Starting Medic")
    if returncode is not None:
        phase = f"{phase} | Exit status {returncode}"
    elif stopping:
        phase = f"Stop requested | {phase}"
    elif status.get("next_run_at") is not None:
        remaining = max(0, int(status["next_run_at"] - time.time() + 0.999))
        phase = f"Next scan in {remaining // 60}:{remaining % 60:02d}"

    lines = [phase]
    if mode := status.get("mode"):
        lines.append(f"Mode: {mode}")
    if detail := status.get("detail"):
        lines.append(detail)
    for key in SECTIONS:
        if result := status.get(key):
            lines.extend(["", result["title"], f"Updated {result['updated_at']}", *result["lines"]])
    return lines


def draw_pane(screen, bounds: tuple[int, int, int, int], title: str, lines: list[str],
              offset: int, *, active: bool, follow: bool = False,
              pin_first: bool = False, logo: tuple[str, ...] = ()) -> tuple[int, int]:
    height, width, y, x = bounds
    pane = screen.derwin(height, width, y, x)
    pane.box()
    pane.addnstr(0, 2, f" {title} ", width - 4, curses.A_BOLD if active else 0)
    content_width = width - 4
    body_height = height - 2
    pinned = []
    if body_height >= len(logo) + 3 and all(len(row) <= content_width for row in logo):
        pinned.extend(logo)
    if pin_first and lines:
        phase_rows = (body_height - len(pinned)) // 2
        pinned.extend(wrap(lines[0], content_width)[:phase_rows])
        lines = lines[1:]
    rows = [row for line in lines for row in (wrap(line, content_width) or [""])]
    page = body_height - len(pinned)
    limit = max(0, len(rows) - page)
    start = limit if follow else max(0, min(offset, limit))
    for row, line in enumerate([*pinned, *rows[start:start + page]], 1):
        pane.addnstr(row, 2, line, content_width)
    return start, page


def display(screen, process: subprocess.Popen, status_file: Path, logs: MedicLogs,
            stop_requested: "StopRequest") -> int:
    screen.keypad(True)
    screen.timeout(200)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    status = {}
    focus = 0
    offsets = [0, 0]
    pages = [1, 1]
    follow_logs = True
    while True:
        logs.read()
        try:
            status = json.loads(status_file.read_text())
        except (OSError, ValueError):
            pass
        returncode = process.poll()
        if stop_requested.requested and returncode is not None:
            return returncode

        screen.erase()
        height, width = screen.getmaxyx()
        if height >= 12 and width >= 36:
            try:
                main_height = (height - 1) * 3 // 5
                panes = ((main_height, width, 0, 0),
                         (height - 1 - main_height, width, main_height, 0))
                offsets[0], pages[0] = draw_pane(
                    screen, panes[0], "Moonshot Medic", status_lines(status, returncode, stop_requested.requested),
                    offsets[0], active=focus == 0, pin_first=True, logo=tuple(status.get("logo", ())),
                )
                offsets[1], pages[1] = draw_pane(
                    screen, panes[1], "Logs" if follow_logs else "Logs (paused)", logs.display_lines(),
                    offsets[1], active=focus == 1, follow=follow_logs,
                )
            except curses.error:
                # Terminal resize can invalidate a pane while it is being painted.
                pass
        footer = "Tab: switch pane | ↑/↓: scroll | End: follow logs | q: stop"
        if returncode is not None:
            footer = "Tab: switch pane | ↑/↓: scroll | q: close"
        elif height < 12 or width < 36:
            footer = "Resize for dashboard | q: stop"
        if width > 1:
            screen.addnstr(height - 1, 0, footer, width - 1)
        screen.refresh()

        key = screen.getch()
        if key in (ord("q"), ord("Q")):
            if returncode is not None:
                return returncode
            request_stop(process, stop_requested)
        elif key == ord("\t"):
            focus = 1 - focus
        elif key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE, curses.KEY_HOME):
            if focus == 1:
                follow_logs = False
            offsets[focus] = {
                curses.KEY_UP: offsets[focus] - 1,
                curses.KEY_DOWN: offsets[focus] + 1,
                curses.KEY_PPAGE: offsets[focus] - pages[focus],
                curses.KEY_NPAGE: offsets[focus] + pages[focus],
                curses.KEY_HOME: 0,
            }[key]
        elif key == curses.KEY_END:
            focus = 1
            follow_logs = True


class StopRequest:
    def __init__(self) -> None:
        self.requested = False


def request_stop(process: subprocess.Popen, stop_requested: StopRequest) -> None:
    if not stop_requested.requested:
        stop_requested.requested = True
        if process.poll() is None:
            process.send_signal(signal.SIGINT)


def run_dashboard(command: list[str]) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("--tui requires an interactive terminal.", file=sys.stderr)
        return 1

    def launch(screen) -> int:
        with tempfile.TemporaryDirectory(prefix="moonshot-medic-tui-") as directory:
            status_file = Path(directory) / "status.json"
            with subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True, env={**os.environ, STATUS_ENV: str(status_file)},
            ) as process:
                logs = MedicLogs(process.stdout.fileno())
                stop_requested = StopRequest()
                signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
                previous = {
                    sig: signal.signal(sig, lambda *_: request_stop(process, stop_requested)) for sig in signals
                }
                try:
                    returncode = display(screen, process, status_file, logs, stop_requested)
                    return returncode if returncode >= 0 else 128 - returncode
                finally:
                    request_stop(process, stop_requested)
                    while process.poll() is None:
                        logs.read()
                        time.sleep(0.05)
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)

    try:
        return curses.wrapper(launch)
    except (curses.error, OSError) as exc:
        print(f"Cannot run Medic dashboard: {exc}", file=sys.stderr)
        return 1
