"""
Tests for the interactive menu (frontends/tui.py).

tests/test_tui.py

The TUI is driven in-process: a rich Console writing to a buffer and a
scripted stdin. The behaviors under test are the ones the src-layout
rewrite changed on purpose - the status line reflects the watcher, the
menu never receives asynchronous output, the live view exits on Ctrl-C
and falls back to log-tailing when another process holds the lock.

Runnable with pytest, but written to run standalone and report a score.
"""

import io
import os
import sys
import time

from _helpers import Sandbox, check, wait_for, run_suite

from rich.console import Console

from duplicate_preventer.engine import Engine, Config
from duplicate_preventer.frontends.tui import DuplicateMonitorTUI


class Driver:
    """A TUI wired to a buffer console and a scripted stdin."""

    def __init__(self, box, script):
        self.buffer = io.StringIO()
        self.console = Console(file=self.buffer, force_terminal=False, width=110)
        self.tui = DuplicateMonitorTUI(config=box.config, console=self.console)
        self._script = script
        self._saved_stdin = None

    def __enter__(self):
        self._saved_stdin = sys.stdin
        sys.stdin = io.StringIO(self._script)
        return self

    def __exit__(self, *_exc):
        sys.stdin = self._saved_stdin
        self.tui.engine.stop()

    @property
    def output(self):
        return self.buffer.getvalue()


def test_status_line_reflects_monitoring_and_quarantine_count():
    print("\nTesting main-menu status line...")
    with Sandbox() as box:
        # 4 = start, then two Enters to refresh, then 4 = stop, Q = quit.
        # The duplicate is planted by a hook between the refreshes.
        with Driver(box, "4\n\n\n4\n\nQ\n") as drv:
            planted = {"done": False}
            original_refresh = drv.tui.refresh_state

            def refresh_and_plant():
                status = original_refresh()
                if status.monitoring and not planted["done"]:
                    planted["done"] = True
                    box.plant_pair()
                    wait_for(lambda: drv.tui.engine.counters.quarantined >= 1)
                    return original_refresh()
                return status

            drv.tui.refresh_state = refresh_and_plant
            drv.tui.show_menu()
            out = drv.output

        passed = check("Stopped" in out and "Start monitoring" in out, "initial: Stopped",
                       "no initial Stopped line")
        passed &= check("Active — watching 1 folder, 1 quarantined this session" in out,
                        "status line counts the quarantine", f"output tail:\n{out[-1200:]}")
        passed &= check("last:" in out and "report-1.pdf" in out, "last-event line shows the file",
                        "no last-event line")
        passed &= check("Stop monitoring" in out and "Monitor stopped" in out, "stop path",
                        "stop not reached")
        passed &= check("Goodbye" in out, "quit", "no goodbye")
        return passed


def test_watch_activity_live_view_returns_on_ctrl_c():
    print("\nTesting the live view...")
    with Sandbox() as box:
        with Driver(box, "4\n\n5\n4\n\nQ\n") as drv:
            calls = {"n": 0}

            def tick_then_interrupt():
                calls["n"] += 1
                if calls["n"] == 1:
                    box.plant_pair()
                    wait_for(lambda: drv.tui.engine.counters.quarantined >= 1)
                    return
                raise KeyboardInterrupt      # the user's Ctrl-C

            drv.tui._live_wait = tick_then_interrupt
            drv.tui.show_menu()
            out = drv.output

        passed = check("Watch activity" in out and "source: this process" in out,
                       "live view opened from the engine queue", "live view header missing")
        passed &= check("Recent activity" in out and "report-1.pdf" in out,
                        "quarantine shows in the activity table", f"output tail:\n{out[-1500:]}")
        passed &= check("Returning to menu" in out and "Goodbye" in out,
                        "Ctrl-C returned to the menu and the session continued",
                        "did not return to the menu")
        return passed


def test_live_view_tails_log_when_another_process_monitors():
    print("\nTesting live view fallback to the log file...")
    with Sandbox() as box:
        other = Engine(Config(config_file=box.config.config_file))
        other.start()                       # holds the lock in "another process"
        try:
            with Driver(box, "5\nQ\n") as drv:
                calls = {"n": 0}

                def tick_then_interrupt():
                    calls["n"] += 1
                    if calls["n"] <= 3:
                        if calls["n"] == 1:
                            with open(box.log_file, "a") as handle:
                                handle.write("2026-08-30 12:00:00 | INFO     | QUARANTINED: from-other-process\n")
                        time.sleep(0.6)
                        return
                    raise KeyboardInterrupt

                drv.tui._live_wait = tick_then_interrupt
                drv.tui.show_menu()
                out = drv.output
        finally:
            other.stop()

        passed = check(f"running in PID {os.getpid()}" in out, "menu labels start as locked elsewhere",
                       f"no locked label in:\n{out[:800]}")
        passed &= check("source: log file" in out, "live view sourced from log", "wrong source")
        passed &= check("from-other-process" in out, "tailed line rendered", f"tail:\n{out[-1500:]}")
        return passed


def test_start_refused_when_locked_keeps_menu_usable():
    print("\nTesting Start while another process holds the lock...")
    with Sandbox() as box:
        other = Engine(Config(config_file=box.config.config_file))
        other.start()
        try:
            with Driver(box, "4\n\n3\n\nQ\n") as drv:
                drv.tui.show_menu()
                out = drv.output
        finally:
            other.stop()
        passed = check("already running in another process" in out, "refusal message",
                       f"no refusal in:\n{out[-1500:]}")
        passed &= check("Current Configuration" in out, "settings screen still reachable",
                        "menu unusable after refusal")
        return passed


def test_folder_add_remove_and_config_view():
    print("\nTesting folder management...")
    with Sandbox() as box:
        extra = box.root / "extra"
        extra.mkdir()
        quoted = f'"{extra}"'
        script = f"1\n1\n{quoted}\n\n1\n{extra}\n\n2\n2\n\n0\n3\n\nQ\n"
        with Driver(box, script) as drv:
            drv.tui.show_menu()
            out = drv.output
        passed = check(f"Added: {extra}" in out, "quoted drag-and-drop path added", f"out:\n{out[-2000:]}")
        passed &= check("Folder already in list" in out, "duplicate add rejected", "duplicate added")
        passed &= check(f"Removed: {extra}" in out, "removed by number", "remove failed")
        passed &= check(box.config.get("watched_folders") == [str(box.watched)],
                        "config back to one folder", f"folders: {box.config.get('watched_folders')}")
        passed &= check("Current Configuration" in out and "File Size Check" in out,
                        "config view rendered", "config view missing")
        return passed


def test_clean_existing_duplicates_dry_then_real():
    print("\nTesting Clean existing duplicates...")
    with Sandbox() as box:
        _o, dup = box.plant_pair()
        # 8: no time window, hash default, dry-run yes, proceed -> then again with dry-run no.
        script = "8\nn\nn\ny\ny\n\n8\nn\nn\nn\ny\n\nQ\n"
        with Driver(box, script) as drv:
            drv.tui.show_menu()
            out = drv.output
        passed = check("This was a dry run" in out, "first pass dry run", f"out:\n{out[-2500:]}")
        passed &= check(out.count("Scan Complete") == 2, "two scans ran", f"count={out.count('Scan Complete')}")
        passed &= check(not os.path.exists(dup) and box.quarantined_files(),
                        "second pass quarantined", "file not moved")
        passed &= check(box.config.get("dry_run") is False, "persistent config untouched",
                        "scan overrides leaked into config")
        return passed


def test_quarantine_view_and_restore():
    print("\nTesting quarantine view and restore...")
    with Sandbox() as box:
        _o, dup = box.plant_pair()
        Engine(box.config).scan_once()
        script = "6\n1\nreport-1.pdf\n\n2\nreport-1.pdf\ny\n\n0\nQ\n"
        with Driver(box, script) as drv:
            drv.tui.show_menu()
            out = drv.output
        passed = check("Total files: 1" in out and "report-1.pdf" in out, "quarantine listed",
                       f"out:\n{out[-2000:]}")
        passed &= check(f"Original path: {dup}" in out, "restoration info shown", "no restore info")
        passed &= check(f"File restored to: {dup}" in out and os.path.exists(dup),
                        "restored", "restore failed")
        return passed


def test_configure_settings_saves_and_applies_live():
    print("\nTesting settings screen with a running monitor...")
    with Sandbox() as box:
        # start, then settings: dry_run y, size y, time y + "2h", hash n,
        # change quarantine n, days 7, level INFO, size 10 -> then stop, quit
        script = "4\n\n2\ny\ny\ny\n2h\nn\nn\nmove\n7\nINFO\n10\n\n4\n\nQ\n"
        with Driver(box, script) as drv:
            drv.tui.show_menu()
            status = drv.tui.engine.status()
            out = drv.output
        passed = check(box.config.get("dry_run") is True and box.config.get("time_window") == 7200
                       and box.config.get("delete_after_days") == 7,
                       "settings saved", f"config: {box.config.config}")
        passed &= check("created within 2h" in out, "detection sentence", "sentence missing")
        passed &= check("(DRY RUN)" in out, "status line picked up dry run live", "dry run not shown")
        return passed


def main():
    return run_suite("tui tests", [
        test_status_line_reflects_monitoring_and_quarantine_count,
        test_watch_activity_live_view_returns_on_ctrl_c,
        test_live_view_tails_log_when_another_process_monitors,
        test_start_refused_when_locked_keeps_menu_usable,
        test_folder_add_remove_and_config_view,
        test_clean_existing_duplicates_dry_then_real,
        test_quarantine_view_and_restore,
        test_configure_settings_saves_and_applies_live,
    ])


if __name__ == '__main__':
    exit(0 if main() else 1)


# End of file #
