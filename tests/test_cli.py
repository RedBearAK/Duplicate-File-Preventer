"""
Tests for the flag-driven front end and the dispatcher.

tests/test_cli.py

Everything here runs the real entry point in a subprocess with a
sandboxed --config, so it exercises __main__ dispatch, argparse, the
engine, and output formatting together - the way a user hits it.

Runnable with pytest, but written to run standalone and report a score.
"""

import os
import sys
import time
import signal
import subprocess

from pathlib import Path

from _helpers import Sandbox, check, wait_for, run_suite

from duplicate_preventer.engine.events import Event, Status
from duplicate_preventer.frontends.render import (
    tail_lines,
    event_line,
    status_summary,
    log_line_style,
)


SRC = str(Path(__file__).resolve().parent.parent / "src")


def run_tool(box, *flags, timeout=30, stdin=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    cmd = [sys.executable, "-m", "duplicate_preventer", "--config", box.config.config_file, *flags]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout, input=stdin)


def start_tool(box, *flags):
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    cmd = [sys.executable, "-u", "-m", "duplicate_preventer", "--config", box.config.config_file, *flags]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)


def test_version_and_help_go_through_cli():
    print("\nTesting --version / --help dispatch...")
    with Sandbox() as box:
        version = run_tool(box, "--version")
        help_text = run_tool(box, "--help")
        passed = check(version.returncode == 0 and "duplicate-preventer 2" in version.stdout,
                       f"version: {version.stdout.strip()}", f"version failed: {version.stderr}")
        passed &= check(help_text.returncode == 0 and "--follow-log" in help_text.stdout
                        and "--install-command" in help_text.stdout,
                        "help lists the new flags", "help missing flags")
        return passed


def test_once_scans_and_reports():
    print("\nTesting --once...")
    with Sandbox() as box:
        box.plant_pair()
        box.plant_pair("a.txt", "a-1.txt", content=b"1", dup_content=b"22")
        result = run_tool(box, "--once")
        out = result.stdout
        passed = check(result.returncode == 0, "exit 0", f"rc={result.returncode}: {result.stderr[-400:]}")
        passed &= check("Quarantined: 1" in out and "unique: 1" in out, "summary line correct",
                        f"output:\n{out}")
        passed &= check("quarantined" in out and "report-1.pdf" in out, "per-file event line shown",
                        f"output:\n{out}")
        passed &= check(box.quarantined_files() == [os.path.join(
            time.strftime("%Y-%m-%d"), "watched", "report-1.pdf")],
            "file quarantined", f"quarantine: {box.quarantined_files()}")
        return passed


def test_once_dry_run_moves_nothing():
    print("\nTesting --once --dry-run...")
    with Sandbox() as box:
        _o, dup = box.plant_pair()
        result = run_tool(box, "--once", "--dry-run")
        passed = check("DRY RUN" in result.stdout and "dry-run: 1" in result.stdout,
                       "dry run reported", f"output:\n{result.stdout}")
        passed &= check(os.path.exists(dup), "file untouched", "file moved in dry run")
        passed &= check(box.config.get("dry_run") is False, "config not modified",
                        "--dry-run leaked into the config file")
        return passed


def test_once_with_no_folders_fails_clearly():
    print("\nTesting --once with nothing configured...")
    with Sandbox() as box:
        box.config.set("watched_folders", [])
        result = run_tool(box, "--once")
        return check(result.returncode == 1 and "No folders configured" in result.stdout,
                     "clear message, exit 1", f"rc={result.returncode} out={result.stdout}")


def test_start_streams_events_and_stops_on_sigint():
    print("\nTesting --start streaming and Ctrl-C...")
    with Sandbox() as box:
        proc = start_tool(box, "--start")
        lines = []

        def read_until(needle, timeout=10):
            deadline = time.time() + timeout
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    time.sleep(0.05)
                    continue
                lines.append(line.rstrip("\n"))
                if needle in line:
                    return True
            return False

        passed = check(read_until("Watching:"), "prints watched folder", f"lines: {lines}")
        box.plant_pair()
        passed &= check(read_until("quarantined"), "streams the quarantined event", f"lines: {lines}")
        passed &= check(any("report-1.pdf" in l for l in lines), "event names the file", f"lines: {lines}")

        proc.send_signal(signal.SIGINT)
        try:
            rest, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            rest = ""
        lines.extend(rest.splitlines())
        passed &= check(proc.returncode == 0, "clean exit on SIGINT", f"rc={proc.returncode}")
        passed &= check(any("Stopped." in l and "1 quarantined" in l for l in lines),
                        "final summary", f"tail: {lines[-4:]}")
        return passed


def test_second_start_refuses_while_first_runs():
    print("\nTesting two --start processes...")
    with Sandbox() as box:
        first = start_tool(box, "--start")
        try:
            wait_for(lambda: os.path.exists(os.path.join(box.config.config_dir, "monitor.lock")))
            time.sleep(0.3)
            second = run_tool(box, "--start", timeout=20)
            passed = check(second.returncode == 1 and "another process" in second.stdout
                           and str(first.pid) in second.stdout,
                           f"refused naming PID {first.pid}", f"rc={second.returncode} out={second.stdout}")
        finally:
            first.send_signal(signal.SIGINT)
            first.communicate(timeout=10)
        return passed


def test_show_log_and_follow_log():
    print("\nTesting --show-log / --follow-log...")
    with Sandbox() as box:
        empty = run_tool(box, "--show-log")
        passed = check(empty.returncode == 0 and "No log file yet" in empty.stdout,
                       "no log yet handled", f"out={empty.stdout}")

        run_tool(box, "--once")     # creates the log
        shown = run_tool(box, "--show-log", "--lines", "5")
        passed &= check("SCAN COMPLETE" in shown.stdout, "shows log tail", f"out={shown.stdout}")

        proc = start_tool(box, "--follow-log", "--lines", "2")
        deadline = time.time() + 10
        seen_header = False
        while time.time() < deadline and not seen_header:
            line = proc.stdout.readline()
            seen_header = "Following log" in line
        with open(box.log_file, "a") as handle:
            handle.write("2026-08-30 12:00:00 | INFO     | QUARANTINED: planted-line\n")
        seen_new = False
        deadline = time.time() + 10
        while time.time() < deadline and not seen_new:
            line = proc.stdout.readline()
            seen_new = "planted-line" in line
        proc.send_signal(signal.SIGINT)
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        passed &= check(seen_header, "follow mode announced", "no follow header")
        passed &= check(seen_new, "new line followed live", "appended line never appeared")
        passed &= check(proc.returncode == 0, "clean exit on SIGINT", f"rc={proc.returncode}")
        return passed


def test_conflicting_modes_rejected():
    print("\nTesting mutually exclusive modes...")
    with Sandbox() as box:
        result = run_tool(box, "--once", "--start")
        return check(result.returncode == 2 and "Choose only one" in result.stdout,
                     "rejected with exit 2", f"rc={result.returncode} out={result.stdout}")


def test_menubar_refused_off_macos():
    print("\nTesting --menubar dispatch...")
    if sys.platform == "darwin":
        print("  - skipped on macOS")
        return True
    with Sandbox() as box:
        result = run_tool(box, "--menubar")
        return check(result.returncode != 0 and "only available on macOS" in result.stderr,
                     "refused with clear message", f"rc={result.returncode} err={result.stderr}")


def test_render_helpers():
    print("\nTesting render helpers...")
    passed = True
    for text, style in [("x | ERROR | y", "red"), ("FAILED - z", "red"), ("WARNING", "yellow"),
                        ("QUARANTINED: a", "green"), ("NO DUPLICATE FOUND", "blue"),
                        ("DRY RUN - WOULD", "cyan"), ("plain", None)]:
        passed &= check(log_line_style(text) == style, f"{text!r} -> {style}",
                        f"{text!r} -> {log_line_style(text)}")

    event = Event("quarantined", "/a/b/invoice-1.pdf", "Duplicate of invoice.pdf", ts=0)
    line = event_line(event)
    passed &= check("invoice-1.pdf" in line and "quarantined" in line and "Duplicate of" in line,
                    f"event line: {line}", f"event line odd: {line}")
    passed &= check(len(event_line(event, width=30)) <= 30 and event_line(event, width=30).endswith("…"),
                    "width truncation", f"truncated: {event_line(event, width=30)!r}")

    running = Status(monitoring=True, healthy=True, watched_folders=["/a", "/b"], quarantined_session=2)
    first, second = status_summary(running, event)
    passed &= check(first.startswith("Active") and "2 folders" in first and "2 quarantined" in first,
                    f"running summary: {first}", f"running summary odd: {first}")
    passed &= check(second.startswith("last:") and "invoice-1.pdf" in second,
                    f"last line: {second}", f"last line odd: {second}")
    sick = Status(monitoring=True, healthy=False, last_error="observer died")
    passed &= check(status_summary(sick)[0].startswith("Problem: observer died"),
                    "unhealthy summary", f"{status_summary(sick)}")
    elsewhere = Status(monitoring=False, lock_holder_pid=4242)
    passed &= check("4242" in status_summary(elsewhere)[0], "locked-elsewhere summary names PID",
                    f"{status_summary(elsewhere)}")
    dry = Status(monitoring=False, dry_run=True)
    passed &= check(status_summary(dry)[0] == "Stopped (DRY RUN)", "dry run tag",
                    f"{status_summary(dry)}")
    passed &= check(tail_lines("/definitely/not/here", 5) == [], "tail of missing file is []",
                    "tail of missing file raised or returned data")
    return passed


def main():
    return run_suite("cli tests", [
        test_version_and_help_go_through_cli,
        test_once_scans_and_reports,
        test_once_dry_run_moves_nothing,
        test_once_with_no_folders_fails_clearly,
        test_start_streams_events_and_stops_on_sigint,
        test_second_start_refuses_while_first_runs,
        test_show_log_and_follow_log,
        test_conflicting_modes_rejected,
        test_menubar_refused_off_macos,
        test_render_helpers,
    ])


if __name__ == '__main__':
    exit(0 if main() else 1)


# End of file #
