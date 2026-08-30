"""
Text rendering shared by the CLI and TUI: log-line coloring, event lines,
status summaries, and a pure-Python log follower (tail -f without tail).
"""

import os
import time

from datetime import datetime

from rich.markup import escape

from duplicate_preventer.engine.utils import format_time_window


def log_line_style(line):
    """Rich style name for a log line, or None for plain."""
    if "ERROR" in line or "FAILED" in line:
        return "red"
    if "WARNING" in line:
        return "yellow"
    if "DUPLICATE CONFIRMED" in line or "QUARANTINED" in line:
        return "green"
    if "NO DUPLICATE" in line:
        return "blue"
    if "DRY RUN" in line:
        return "cyan"
    return None


def log_line_markup(line):
    style = log_line_style(line)
    text = escape(line.rstrip("\n"))
    return f"[{style}]{text}[/{style}]" if style else text


EVENT_STYLES = {
    "quarantined": "green",
    "dry_run": "cyan",
    "error": "red",
    "checked": "yellow",
    "config_reloaded": "magenta",
    "started": "green",
    "stopped": "yellow",
    "scanned": "blue",
    "restored": "green",
}

EVENT_LABELS = {
    "quarantined": "quarantined",
    "dry_run": "dry run",
    "error": "ERROR",
    "checked": "checked",
    "config_reloaded": "config reloaded",
    "started": "started",
    "stopped": "stopped",
    "scanned": "scan done",
    "restored": "restored",
}


def event_line(event, width=None):
    """One-line description of an event, plain text."""
    label = EVENT_LABELS.get(event.kind, event.kind)
    name = os.path.basename(event.path) if event.path else ""
    parts = [event.time_str(), f"{label:<15}", name, event.detail]
    text = "  ".join(p for p in parts if p)
    if width and len(text) > width:
        text = text[:width - 1] + "…"
    return text


def event_markup(event, width=None):
    style = EVENT_STYLES.get(event.kind)
    text = escape(event_line(event, width))
    return f"[{style}]{text}[/{style}]" if style else text


def status_summary(status, last_event=None):
    """
    Two short plain-text lines for a menu header:
        ● Active — watching 3 folders, 2 quarantined this session
          last: invoice-1.pdf → quarantine  14:02:11
    """
    if not status.healthy:
        first = f"Problem: {status.last_error or 'see log'}"
    elif status.monitoring:
        first = (f"Active — watching {len(status.watched_folders)} folder"
                 f"{'s' if len(status.watched_folders) != 1 else ''}, "
                 f"{status.quarantined_session} quarantined this session")
    elif status.lock_holder_pid:
        first = f"Stopped here — monitoring is running in another process (PID {status.lock_holder_pid})"
    else:
        first = "Stopped"

    if status.dry_run:
        first += " (DRY RUN)"
    if status.dropped_events:
        first += f" [{status.dropped_events} events dropped]"

    second = f"last: {event_line(last_event)}" if last_event else ""
    return first, second


def uptime_str(started_at):
    if not started_at:
        return "-"
    seconds = int(time.time() - started_at)
    return str(datetime.utcfromtimestamp(seconds).strftime("%H:%M:%S")) if seconds < 86400 \
        else f"{seconds // 86400}d " + datetime.utcfromtimestamp(seconds).strftime("%H:%M:%S")


def detection_sentence(config):
    parts = []
    if config.get("check_size"):
        parts.append("matching file size")
    if config.get("check_time"):
        parts.append(f"created within {format_time_window(int(config.get('time_window')))}")
    parts.append("matching filename pattern (file-1, file-2, etc.)")
    if config.get("use_hash"):
        parts.append(f"verified by {str(config.get('hash_algorithm')).upper()} hash")
    return "Files are duplicates when: " + " AND ".join(parts)


def tail_lines(path, count):
    """Last `count` lines of a text file (whole-file read; logs are capped)."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            return handle.readlines()[-count:]
    except OSError:
        return []


def follow_log(path, on_line, should_stop, initial_lines=1000, poll=0.5):
    """
    Pure-Python tail -f. Calls on_line(text) for the last `initial_lines`
    and then for every new line until should_stop() is true. Handles
    rotation (file replaced/truncated) by reopening.
    """
    for line in tail_lines(path, initial_lines):
        on_line(line.rstrip("\n"))

    def open_at_end():
        handle = open(path, 'r', encoding='utf-8', errors='replace')
        handle.seek(0, os.SEEK_END)
        return handle, os.fstat(handle.fileno()).st_ino

    try:
        handle, inode = open_at_end()
    except OSError:
        handle, inode = None, None

    try:
        while not should_stop():
            if handle is None:
                try:
                    handle, inode = open_at_end()
                except OSError:
                    time.sleep(poll)
                    continue

            line = handle.readline()
            if line:
                on_line(line.rstrip("\n"))
                continue

            try:
                current = os.stat(path)
                rotated = current.st_ino != inode or current.st_size < handle.tell()
            except OSError:
                rotated = True
            if rotated:
                handle.close()
                handle = None
                continue
            time.sleep(poll)
    finally:
        if handle is not None:
            handle.close()


# End of file #
