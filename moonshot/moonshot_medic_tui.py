"""Terminal dashboard for an ordinary Moonshot Medic process."""

import curses
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from textwrap import shorten, wrap

STATUS_ENV = "MOONSHOT_MEDIC_TUI_STATUS"
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SPINNER_FRAMES = "◴◷◶◵"
SPINNER_INTERVAL = 0.8
MODES = {
    "Confirmed recovery": "Recovery enabled",
    "Recovery preview": "Recovery preview",
    "Diagnosis only": "Diagnosis only",
}
HOST_STATES = {
    "absent": ("Worker absent", "attention"),
    "clock_repaired": ("Clock repaired", "success"),
    "deferred": ("Deferred", "attention"),
    "eligible": ("Eligible", "accent"),
    "failed": ("Failed", "error"),
    "gdm_autologin_stall": ("GDM autologin stall", "attention"),
    "live": ("Worker live", "accent"),
    "matched": ("Not diagnosed", "attention"),
    "not_zombie": ("No zombie found", "accent"),
    "rebooted": ("Reboot verified", "success"),
    "recovered": ("Recovered", "success"),
    "ssh_restored": ("SSH restored", "success"),
    "task_resolution_stall": ("Task resolution stall", "attention"),
    "unverified": ("Unverified", "attention"),
    "worker_oom": ("Worker OOM", "attention"),
    "zombie": ("Confirmed zombie", "attention"),
}
HOST_GROUPS = (
    ("Needs attention", "attention", set()),
    ("Actions completed", "success", {"clock_repaired", "ssh_restored"}),
    ("Eligible for recovery", "accent", {"eligible"}),
)
Line = tuple[str, str]


class MedicErrors:
    """Keep the last stderr line for failures outside Medic's normal event logging."""

    def __init__(self, fd: int) -> None:
        self.fd = fd
        os.set_blocking(fd, False)
        self.latest = ""
        self.pending = b""
        self.closed = False

    def read(self) -> None:
        if self.closed:
            return
        try:
            chunk = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        self.closed = not chunk
        *lines, self.pending = (self.pending + chunk).split(b"\n")
        if self.closed:
            lines.append(self.pending)
        self.pending = self.pending[-4096:]
        for line in reversed(lines):
            if text := " ".join(ANSI_ESCAPE.sub("", line[-4096:].decode(errors="replace")).split()):
                self.latest = text
                break


def status_header(status: dict, returncode: int | None, stopping: bool) -> Line:
    phase = status.get("phase", "Starting Medic")
    next_run_at = status.get("next_run_at")
    mode = status.get("mode", "")
    tone = "accent" if status.get("confirm") else "preview"
    if returncode is not None:
        phase = f"{phase} · Exit status {returncode}"
        if returncode and status.get("phase") != "Recovery preview complete":
            tone = "error"
    elif stopping:
        phase = "Stopping after current work finishes"
        mode = ""
        tone = "attention"
    elif next_run_at is not None:
        remaining = max(0, math.ceil(next_run_at - time.time()))
        phase = f"Next scan in {remaining // 60}:{remaining % 60:02d}"
    else:
        frame = int(time.monotonic() / SPINNER_INTERVAL) % len(SPINNER_FRAMES)
        phase = f"{SPINNER_FRAMES[frame]} {phase}"
        if detail := status.get("detail"):
            phase += f" · {shorten(detail, width=36, placeholder=' …')}"

    return (f"{phase} · {MODES.get(mode, mode)}" if mode else phase, tone)


def host_state(state: str) -> Line:
    return HOST_STATES.get(state, (state.replace("_", " ").capitalize(), "attention"))


def check_heading(result: dict, label: str) -> Line:
    title = result["title"] or label
    text = " · ".join(part for part in (title, result["summary"], result["updated_at"]) if part)
    return f"  {text}", "attention" if result["alert"] else ""


def worker_lines(fleet_health: dict, diagnosis: dict, outcomes: dict) -> list[Line]:
    matches = fleet_health.get("hosts", {})
    findings = diagnosis.get("hosts", {})
    rows = []
    for host, event in sorted({**matches, **findings, **outcomes}.items()):
        label, tone = host_state(event["state"])
        reason = event["reason"]
        if host in outcomes and (finding := findings.get(host)):
            diagnosis_label, _ = host_state(finding["state"])
            reason = f"{diagnosis_label} ({finding['reason']}) · {reason}"
        if fleet_health and not fleet_health["alert"] and host not in matches:
            reason += " · Found during diagnosis"
        rows.append((f"    {host}  {label} · {reason}", tone))
    return sorted(rows, key=lambda row: row[1] not in ("error", "attention"))


def dashboard_lines(status: dict) -> list[Line]:
    recovery = status.get("recovery") or {}
    fleet_health = status.get("fleet_health") or {}
    diagnosis = status.get("diagnosis") or {}
    outcomes = status.get("diagnosis_outcomes", {})
    workers = worker_lines(fleet_health, diagnosis, outcomes)
    hosts = {**recovery.get("hosts", {}), **status.get("recovery_outcomes", {})}

    grouped = {title: [] for title, _, _ in HOST_GROUPS}
    for host, event in sorted(hosts.items()):
        state = event["state"]
        label, tone = host_state(state)
        group = next((title for title, _, states in HOST_GROUPS if state in states),
                     "Needs attention")
        grouped[group].append((f"  {host}  {label} · {event['reason']}", tone))

    lines = []
    for title, tone, _ in HOST_GROUPS:
        if rows := grouped[title]:
            lines.extend([(f"{title} · {len(rows)}", tone), *rows, ("", "")])

    if diagnosis and not hosts and not workers and not any(
        check.get("alert") for check in (recovery, fleet_health, diagnosis)
    ):
        lines.append(("No hosts flagged in this scan", "success"))

    if started_at := status.get("scan_started_at"):
        if lines and lines[-1][0]:
            lines.append(("", ""))
        lines.append((f"Scan details · started {started_at}", "accent"))
    if recovery:
        lines.append(check_heading(recovery, "Check 1 · Automated recovery"))
        if query := recovery["query"]:
            lines.append((f"    Filter  {query}", "muted"))
        if recovery["hosts"]:
            lines.append((f"    Eligible hosts  {' '.join(recovery['hosts'])}", ""))
        if action := recovery["action"]:
            lines.append((f"    Preview  {action}", ""))
    if fleet_health:
        lines.append(check_heading(fleet_health, "Check 2 · Fleetroll health"))
        if query := fleet_health["query"]:
            lines.append((f"    Filter  {query}", "muted"))
        if diagnosis.get("alert"):
            lines.append((f"    {diagnosis['title']}", "attention"))
    elif diagnosis:
        lines.append(check_heading(diagnosis, "Worker diagnosis"))
        if query := diagnosis["query"]:
            lines.append((f"    Filter  {query}", "muted"))
    lines.extend(workers)
    return lines


def activity_lines(status: dict, width: int, failure: str = "", failure_time: str = "") -> list[Line]:
    events = status.get("activity", [])
    if failure and not any(event["message"] == f"Error · {failure.removeprefix('[ERROR] ')}"
                           for event in events):
        events = [*events, {"time": failure_time, "host": "", "message": failure, "tone": "error"}]
    return [
        (clip_line("  ".join(part for part in (event["time"], event["host"], event["message"]) if part),
                   width), event["tone"])
        for event in events
    ] or [(clip_line("Waiting for log entries.", width), "muted")]


def clip_line(line: str, width: int) -> str:
    # Leave room for an ellipsis if the message exceeds the available terminal columns.
    line = ANSI_ESCAPE.sub("", line)
    line = "".join(char for char in line if not unicodedata.category(char).startswith("C"))
    if len(line) <= width and line.isascii():
        return line
    columns = 0
    ellipsis_at = 0
    for index, char in enumerate(line):
        if not unicodedata.category(char).startswith("M"):
            columns += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if columns < width:
            ellipsis_at = index + 1
        if columns > width:
            return line[:ellipsis_at].rstrip() + "…"
    return line


def wrap_lines(lines: list[Line], width: int) -> list[Line]:
    return [(row, tone) for line, tone in lines
            for row in (wrap(line, width, subsequent_indent="  " if line.startswith("  ") else "") or [""])]


def draw_pane(screen, bounds: tuple[int, int, int, int], title: str, rows: list[Line],
              offset: int, *, active: bool, follow: bool = False,
              header: list[Line] | None = None, logo: list[Line] | None = None,
              palette: dict[str, int]) -> tuple[int, int]:
    height, width, y, x = bounds
    pane = screen.derwin(height, width, y, x)
    pane.box()
    pane.addnstr(0, 2, f" {title} ", width - 4, palette["accent"] if active else curses.A_BOLD)
    content_width = width - 4
    body_height = height - 2
    header = header or []
    logo = logo or []
    pinned = logo + header if body_height > len(logo) + len(header) else header[:body_height - 1]
    page = body_height - len(pinned)
    limit = max(0, len(rows) - page)
    start = limit if follow else max(0, min(offset, limit))
    for row, (line, tone) in enumerate([*pinned, *rows[start:start + page]], 1):
        pane.addnstr(row, 2, line, content_width, palette.get(tone, curses.A_NORMAL))
    return start, page


def color_palette() -> dict[str, int]:
    colors = {
        "accent": curses.COLOR_CYAN,
        "attention": curses.COLOR_YELLOW,
        "success": curses.COLOR_GREEN,
        "error": curses.COLOR_RED,
        "preview": curses.COLOR_MAGENTA,
    }
    palette = dict.fromkeys(colors, curses.A_BOLD)
    palette["muted"] = curses.A_DIM
    try:
        if not curses.has_colors():
            return palette
        background = curses.COLOR_BLACK
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            pass
        for pair, (tone, color) in enumerate(colors.items(), 1):
            curses.init_pair(pair, color, background)
            palette[tone] |= curses.color_pair(pair)
    except curses.error:
        pass
    return palette


def display(screen, process: subprocess.Popen, status_file: Path, errors: MedicErrors,
            stop_requested: "StopRequest") -> int:
    screen.keypad(True)
    screen.timeout(200)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    palette = color_palette()
    status = {}
    focus = 0
    offsets = [0, 0]
    follow = True
    pages = [1, 1]
    failure_time = ""
    while True:
        errors.read()
        try:
            status = json.loads(status_file.read_text())
        except (OSError, ValueError):
            pass
        returncode = process.poll()
        if stop_requested.requested and returncode is not None:
            return returncode
        if returncode and not failure_time:
            errors.read()
            failure_time = datetime.now().isoformat(sep=" ", timespec="seconds")
        running = returncode is None and not stop_requested.requested
        can_rescan = running and status.get("next_run_at") is not None

        screen.erase()
        height, width = screen.getmaxyx()
        header = status_header(status, returncode, stop_requested.requested)
        if stop_requested.requested:
            footer = "Ctrl-C: exit immediately"
        else:
            footer = "q: close" if returncode is not None else "q: stop"
        if height >= 12 and width >= 36:
            try:
                content_width = width - 4
                header_rows = wrap_lines([header], content_width)
                logo = [(line, "accent") for line in status.get("logo", ())] + [("", "")]
                if len(logo) == 1 or any(len(line) > content_width for line, _ in logo):
                    logo = []
                results = wrap_lines(dashboard_lines(status), content_width)
                desired = len(logo) + len(header_rows) + max(1, len(results)) + 3
                main_height = min(max(5, desired), height - 1 - max(4, (height - 1) // 3))
                panes = ((main_height, width, 0, 0),
                         (height - 1 - main_height, width, main_height, 0))
                offsets[0], pages[0] = draw_pane(
                    screen, panes[0], "Moonshot Medic",
                    results, offsets[0], active=focus == 0,
                    header=header_rows + [("", "")], logo=logo, palette=palette,
                )
                failure = errors.latest if returncode and status.get("phase") != "Recovery preview complete" else ""
                activity = activity_lines(status, content_width, failure, failure_time)
                offsets[1], pages[1] = draw_pane(
                    screen, panes[1], "Logs" if follow else "Logs (paused)",
                    activity, offsets[1],
                    active=focus == 1, follow=follow, palette=palette,
                )
            except curses.error:
                # Terminal resize can invalidate a pane while it is being painted.
                pass
            footer += " | Tab: pane | ↑/↓: scroll | End: follow"
        else:
            if width > 1:
                for row, (line, tone) in enumerate(wrap_lines([header], width - 1)[:height - 1]):
                    screen.addnstr(row, 0, line, width - 1, palette[tone])
            footer += " | Resize for dashboard"
        if can_rescan:
            footer = "r: recheck now | " + footer
        elif running and status.get("looping"):
            footer = "r: recheck when idle | " + footer
        if width > 1:
            screen.addnstr(height - 1, 0, footer, width - 1)
        screen.refresh()

        key = screen.getch()
        if key in (ord("q"), ord("Q")):
            if returncode is not None:
                return returncode
            request_stop(process, stop_requested)
        elif key == 3:  # Ctrl-C can also arrive as a character in raw terminals.
            request_stop(process, stop_requested, escalate=True)
        elif key in (ord("r"), ord("R")) and can_rescan:
            process.send_signal(signal.SIGUSR1)
        elif key == ord("\t"):
            focus = 1 - focus
        elif key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE, curses.KEY_HOME):
            if focus == 1:
                follow = False
            offsets[focus] = {
                curses.KEY_UP: offsets[focus] - 1,
                curses.KEY_DOWN: offsets[focus] + 1,
                curses.KEY_PPAGE: offsets[focus] - pages[focus],
                curses.KEY_NPAGE: offsets[focus] + pages[focus],
                curses.KEY_HOME: 0,
            }[key]
        elif key == curses.KEY_END:
            focus = 1
            follow = True


class StopRequest:
    def __init__(self) -> None:
        self.requested = False
        self.forced = False


def request_stop(process: subprocess.Popen, stop_requested: StopRequest, *, escalate: bool = False) -> None:
    if not stop_requested.requested:
        stop_requested.requested = True
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
    elif escalate and not stop_requested.forced and process.poll() is None:
        stop_requested.forced = True
        # Medic runs in its own process group so an immediate exit also ends its commands.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_dashboard(command: list[str]) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("--tui requires an interactive terminal.", file=sys.stderr)
        return 1

    def launch(screen) -> int:
        with tempfile.TemporaryDirectory(prefix="moonshot-medic-tui-") as directory:
            status_file = Path(directory) / "status.json"
            with subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                start_new_session=True, env={**os.environ, STATUS_ENV: str(status_file)},
            ) as process:
                errors = MedicErrors(process.stderr.fileno())
                stop_requested = StopRequest()
                signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

                def handle_signal(sig, _frame):
                    request_stop(process, stop_requested, escalate=sig == signal.SIGINT)

                previous = {
                    sig: signal.signal(sig, handle_signal) for sig in signals
                }
                try:
                    returncode = display(screen, process, status_file, errors, stop_requested)
                    if stop_requested.forced:
                        return 128 + signal.SIGINT
                    return returncode if returncode >= 0 else 128 - returncode
                finally:
                    request_stop(process, stop_requested)
                    while process.poll() is None:
                        errors.read()
                        time.sleep(0.05)
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)

    try:
        return curses.wrapper(launch)
    except (curses.error, OSError) as exc:
        print(f"Cannot run Medic dashboard: {exc}", file=sys.stderr)
        return 1
