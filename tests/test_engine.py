"""
Tests for the Engine: engine/monitor.py and friends, driven with no UI.

tests/test_engine.py

These are the Phase 0/1 acceptance criteria from the handoff, as code:

- engine/ imports no UI module and writes nothing to stdout/stderr
- a planted duplicate produces a `quarantined` event and a moved file
- healthy reflects the observer thread actually being alive
- editing the config file from "another process" is picked up live
- a second engine on the same config refuses to start

Runnable with pytest, but written to run standalone and report a score.
"""

import io
import os
import sys
import time
import json
import contextlib
import subprocess

from pathlib import Path

from _helpers import Sandbox, check, wait_for, run_suite

from duplicate_preventer.engine import (
    Config,
    Engine,
    EngineError,
    EventQueue,
    MonitorLock,
)


UI_MODULES = ("rich", "rumps", "AppKit", "Foundation", "objc")


@contextlib.contextmanager
def captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def events_of(engine, kind):
    return [e for e in engine.events.drain() if e.kind == kind]


def test_engine_package_imports_no_ui_and_is_silent():
    """Fresh interpreter: import the engine, drive it, check nothing leaks."""
    print("\nTesting engine isolation in a fresh interpreter...")
    script = r"""
import sys, io, contextlib, tempfile, os, time
out, err = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    import duplicate_preventer.engine as eng
    from duplicate_preventer.engine import Config, Engine
    with tempfile.TemporaryDirectory() as tmp:
        watched = os.path.join(tmp, "w"); os.makedirs(watched)
        cfg = Config(config_file=os.path.join(tmp, "c.json"))
        cfg.update({"watched_folders": [watched],
                    "quarantine_path": os.path.join(tmp, "q"),
                    "log_file": os.path.join(tmp, "log")})
        e = Engine(cfg)
        e.start()
        open(os.path.join(watched, "a.pdf"), "wb").write(b"x"*10)
        open(os.path.join(watched, "a-1.pdf"), "wb").write(b"x"*10)
        deadline = time.time() + 5
        while time.time() < deadline and e.counters.quarantined < 1:
            time.sleep(0.05)
        e.scan_once()
        e.stop()
        q = e.counters.quarantined
leaked = [m for m in %r if m in sys.modules]
print("LEAKED", leaked)
print("STDOUT_BYTES", len(out.getvalue()))
print("STDERR_BYTES", len(err.getvalue()))
print("QUARANTINED", q)
""" % (UI_MODULES,)
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parent.parent / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env)
    lines = dict(l.split(" ", 1) for l in result.stdout.strip().splitlines() if " " in l)

    passed = check(result.returncode == 0, "script ran", f"script failed: {result.stderr[-500:]}")
    passed &= check(lines.get("LEAKED") == "[]", "no UI module imported",
                    f"UI modules loaded: {lines.get('LEAKED')}")
    passed &= check(lines.get("STDOUT_BYTES") == "0", "stdout empty", f"stdout bytes: {lines.get('STDOUT_BYTES')}")
    passed &= check(lines.get("STDERR_BYTES") == "0", "stderr empty", f"stderr bytes: {lines.get('STDERR_BYTES')}")
    passed &= check(lines.get("QUARANTINED") == "1", "duplicate quarantined while silent",
                    f"quarantined: {lines.get('QUARANTINED')}")
    return passed


def test_no_print_statements_in_engine_source():
    print("\nTesting engine source for stray print/console calls...")
    engine_dir = Path(__file__).resolve().parent.parent / "src" / "duplicate_preventer" / "engine"
    offenders = []
    for path in sorted(engine_dir.glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "print(" in stripped or "console." in stripped or "StreamHandler" in stripped \
                    or "import rich" in stripped or "from rich" in stripped:
                offenders.append(f"{path.name}:{number}: {stripped}")
    return check(not offenders, f"{len(list(engine_dir.glob('*.py')))} engine files clean",
                 "offenders:\n    " + "\n    ".join(offenders))


def test_live_duplicate_is_quarantined_with_event():
    print("\nTesting live monitoring on a planted duplicate...")
    with Sandbox() as box:
        engine = Engine(box.config)
        with captured() as (out, err):
            engine.start()
            started = events_of(engine, "started")
            box.plant("invoice.pdf", b"q" * 500)
            dup = box.plant("invoice-1.pdf", b"q" * 500)
            wait_for(lambda: engine.counters.quarantined >= 1)
            quarantined = events_of(engine, "quarantined")
            engine.stop()
            stopped = events_of(engine, "stopped")

        passed = check(started, "started event", "no started event")
        passed &= check(len(quarantined) == 1 and quarantined[0].path == dup,
                        f"quarantined event for {os.path.basename(dup)}",
                        f"events: {quarantined}")
        passed &= check(not os.path.exists(dup), "file moved out of watched folder",
                        "file still in watched folder")
        passed &= check(box.quarantined_files() and box.quarantined_files()[0].endswith("invoice-1.pdf"),
                        f"in quarantine: {box.quarantined_files()}", f"quarantine: {box.quarantined_files()}")
        passed &= check("QUARANTINED:" in box.log_text() and "DUPLICATE CONFIRMED" in box.log_text(),
                        "log has the decision and the move", "log missing lines")
        passed &= check(stopped, "stopped event", "no stopped event")
        passed &= check(out.getvalue() == "" and err.getvalue() == "", "nothing printed",
                        f"printed: {out.getvalue()!r} {err.getvalue()!r}")
        return passed


def test_slow_writer_is_compared_after_it_finishes():
    """
    The create event fires when the file is opened. A duplicate written in
    chunks used to be compared against its own partial size and passed as
    'unique'. The engine now waits for the size to settle first.
    """
    print("\nTesting a duplicate written slowly in chunks...")
    with Sandbox() as box:
        engine = Engine(box.config)
        engine.start()
        box.plant("big.pdf", b"z" * 5000)
        dup = str(box.watched / "big-1.pdf")
        with open(dup, "wb") as handle:
            for _ in range(5):
                handle.write(b"z" * 1000)
                handle.flush()
                time.sleep(0.4)
        quarantined = wait_for(lambda: engine.counters.quarantined >= 1, timeout=10)
        engine.stop()
        passed = check(quarantined and not os.path.exists(dup), "quarantined after the writer finished",
                       "compared against a partial file")
        passed &= check("Size: 5000 bytes" in box.log_text(), "log shows the full size",
                        "log shows a partial size")
        return passed


def test_rename_into_pattern_is_caught():
    """Finder: Duplicate -> 'report copy.pdf', rename -> 'report-1.pdf'. That's a move, not a create."""
    print("\nTesting a rename into the -N pattern...")
    with Sandbox() as box:
        engine = Engine(box.config)
        engine.start()
        box.plant("report.pdf", b"r" * 300)
        staged = box.plant("report copy.pdf", b"r" * 300)
        time.sleep(0.3)
        final = str(box.watched / "report-1.pdf")
        os.rename(staged, final)
        quarantined = wait_for(lambda: engine.counters.quarantined >= 1, timeout=10)
        engine.stop()
        passed = check(quarantined and not os.path.exists(final), "renamed duplicate quarantined",
                       "rename not detected")
        passed &= check("Potential duplicate renamed" in box.log_text(), "logged as a rename",
                        "log missing rename line")
        return passed


def test_unique_file_is_left_alone():
    print("\nTesting a -1 file with a different-size original...")
    with Sandbox() as box:
        engine = Engine(box.config)
        engine.start()
        box.plant("photo.jpg", b"a" * 100)
        dup = box.plant("photo-1.jpg", b"a" * 101)
        wait_for(lambda: "NO DUPLICATE FOUND" in box.log_text())
        engine.stop()
        passed = check(os.path.exists(dup), "file kept", "file quarantined!")
        passed &= check("NO DUPLICATE FOUND" in box.log_text(), "log explains", "no log explanation")
        passed &= check(not events_of(engine, "quarantined"), "no quarantined event",
                        "spurious quarantined event")
        return passed


def test_dry_run_moves_nothing_but_reports():
    print("\nTesting dry run...")
    with Sandbox(dry_run=True) as box:
        engine = Engine(box.config)
        engine.start()
        _o, dup = box.plant_pair()
        wait_for(lambda: "WOULD QUARANTINE" in box.log_text())
        events = events_of(engine, "dry_run")
        engine.stop()
        passed = check(os.path.exists(dup), "file untouched", "dry run moved the file!")
        passed &= check(len(events) == 1, "dry_run event emitted", f"events: {events}")
        passed &= check(engine.status().quarantined_session == 0, "counter not bumped",
                        f"counter: {engine.status().quarantined_session}")
        passed &= check("DRY RUN - WOULD QUARANTINE" in box.log_text(), "logged", "not logged")
        return passed


def test_status_health_tracks_observer_liveness():
    print("\nTesting healthy reflects the observer thread...")
    with Sandbox() as box:
        engine = Engine(box.config)
        before = engine.status()
        passed = check(not before.monitoring and before.healthy, "idle: healthy, not monitoring",
                       f"idle status: {before.as_dict()}")
        engine.start()
        running = engine.status()
        passed &= check(running.monitoring and running.healthy and running.started_at,
                        "running: healthy", f"running status: {running.as_dict()}")
        passed &= check(running.watched_folders == [str(box.watched)], "watched folders reported",
                        f"folders: {running.watched_folders}")

        engine._kill_observer_for_test()
        dead = engine.status()
        passed &= check(dead.monitoring and not dead.healthy and "observer" in (dead.last_error or ""),
                        f"dead observer: healthy=False ({dead.last_error})",
                        f"dead observer not detected: {dead.as_dict()}")
        engine.stop()
        after = engine.status()
        passed &= check(not after.monitoring and after.healthy, "stopped: healthy again",
                        f"after stop: {after.as_dict()}")
        return passed


def test_missing_folder_reported_not_fatal():
    print("\nTesting missing watched folder...")
    with Sandbox() as box:
        missing = str(box.root / "gone")
        box.config.set("watched_folders", [str(box.watched), missing])
        engine = Engine(box.config)
        engine.start()
        status = engine.status()
        errors = events_of(engine, "error")
        passed = check(status.monitoring and status.healthy, "still healthy with one good folder",
                       f"status: {status.as_dict()}")
        passed &= check(status.watched_folders == [str(box.watched)], "only the real folder watched",
                        f"watched: {status.watched_folders}")
        passed &= check(any(e.path == missing for e in errors), "error event names the folder",
                        f"errors: {errors}")
        engine.stop()

        box.config.set("watched_folders", [missing])
        engine = Engine(box.config)
        engine.start()
        status = engine.status()
        passed &= check(status.monitoring and not status.healthy and "no usable" in status.last_error,
                        "zero usable folders -> unhealthy", f"status: {status.as_dict()}")
        engine.stop()
        return passed


def test_hot_reload_changes_watch_list_without_restart():
    print("\nTesting hot reload from another writer...")
    with Sandbox() as box:
        second = box.root / "second"
        second.mkdir()
        engine = Engine(box.config)
        engine.start()

        passed = check(not engine.reload_config_if_changed(), "no change: no reload",
                       "reload reported with no change")

        # "Another process": a separate Config instance on the same file.
        time.sleep(0.02)
        other = Config(config_file=box.config.config_file)
        other.set("watched_folders", [str(box.watched), str(second)])

        reloaded = wait_for(engine.reload_config_if_changed)
        passed &= check(reloaded, "reload detected", "reload not detected")
        status = engine.status()
        passed &= check(status.monitoring and status.healthy and str(second) in status.watched_folders,
                        "second folder now watched", f"status: {status.as_dict()}")
        passed &= check(events_of(engine, "config_reloaded"), "config_reloaded event", "no event")

        # And the new folder is really live.
        (second / "a.pdf").write_bytes(b"k" * 40)
        dup = str(second / "a-1.pdf")
        Path(dup).write_bytes(b"k" * 40)
        wait_for(lambda: engine.counters.quarantined >= 1)
        passed &= check(not os.path.exists(dup), "duplicate in new folder quarantined",
                        "new folder not actually watched")

        # A settings-only change (dry_run) is applied live too.
        time.sleep(0.02)
        other.set("dry_run", True)
        wait_for(engine.reload_config_if_changed)
        box.plant("b.pdf", b"m" * 30)
        dup2 = box.plant("b-1.pdf", b"m" * 30)
        wait_for(lambda: "WOULD QUARANTINE" in box.log_text())
        passed &= check(os.path.exists(dup2) and engine.status().dry_run,
                        "dry_run applied live", "dry_run change ignored")
        engine.stop()
        return passed


def test_hot_reload_survives_corrupt_config():
    print("\nTesting hot reload with a corrupt file...")
    with Sandbox() as box:
        engine = Engine(box.config)
        engine.start()
        time.sleep(0.02)
        with open(box.config.config_file, "w") as handle:
            handle.write("{ nope")

        reloaded = engine.reload_config_if_changed()
        status = engine.status()
        passed = check(not reloaded, "reload returned False", "reload claimed success")
        passed &= check(status.monitoring and not status.healthy and "unreadable" in status.last_error,
                        f"unhealthy with reason: {status.last_error}", f"status: {status.as_dict()}")
        passed &= check(engine.observer_alive(), "observer kept running", "observer died")
        passed &= check(events_of(engine, "error"), "error event", "no error event")

        # Fix the file: health recovers.
        time.sleep(0.02)
        good = dict(box.config.config)
        with open(box.config.config_file, "w") as handle:
            json.dump(good, handle)
        wait_for(engine.reload_config_if_changed)
        passed &= check(engine.status().healthy, "healthy again after fix", "stuck unhealthy")
        engine.stop()
        return passed


def test_second_engine_refuses_and_reports_holder():
    print("\nTesting the monitoring lock (in-process)...")
    with Sandbox() as box:
        first = Engine(box.config)
        first.start()
        second = Engine(Config(config_file=box.config.config_file))
        raised = None
        try:
            second.start()
        except EngineError as error:
            raised = error
        passed = check(raised is not None and str(os.getpid()) in str(raised),
                       f"second start refused: {raised}", f"second start did not refuse ({raised})")
        passed &= check(second.status().lock_holder_pid == os.getpid(),
                        "status names the holder PID", f"holder: {second.status().lock_holder_pid}")
        first.stop()
        second.start()
        passed &= check(second.status().monitoring, "lock released on stop; second can start",
                        "second still blocked after first stopped")
        second.stop()
        return passed


def test_lock_is_held_across_processes():
    print("\nTesting the monitoring lock (cross-process)...")
    with Sandbox() as box:
        lock = MonitorLock(box.config.config_dir)
        lock.acquire()
        script = (f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parent.parent / 'src')!r})\n"
                  "from duplicate_preventer.engine import MonitorLock\n"
                  f"l = MonitorLock({box.config.config_dir!r})\n"
                  "print('GOT' if l.acquire() else 'BLOCKED', l.holder_pid())\n")
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        passed = check(result.stdout.startswith("BLOCKED"), f"child blocked: {result.stdout.strip()}",
                       f"child: {result.stdout!r} {result.stderr[-300:]}")
        passed &= check(str(os.getpid()) in result.stdout, "child sees our PID", "PID missing")
        lock.release()
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        passed &= check(result.stdout.startswith("GOT"), "child acquires after release",
                        f"child: {result.stdout!r}")
        return passed


def test_scan_once_handles_existing_files_recursively():
    print("\nTesting scan_once...")
    with Sandbox() as box:
        deep = box.watched / "a" / "b"
        box.plant_pair(folder=deep)
        box.plant_pair("x.txt", "x-1.txt", content=b"1" * 10, dup_content=b"1" * 11)
        box.plant("orphan-1.pdf")
        engine = Engine(box.config)
        with captured() as (out, err):
            result = engine.scan_once()
        passed = check(result.scanned == 3 and result.processed == 2, f"scanned 3, processed 2",
                       f"scanned {result.scanned}, processed {result.processed}")
        passed &= check(result.quarantined == 1 and result.unique == 1,
                        "1 quarantined, 1 unique", f"{result.as_dict()}")
        passed &= check(os.path.exists(str(box.watched / "orphan-1.pdf")), "orphan untouched",
                        "orphan moved")
        passed &= check(events_of(engine, "scanned"), "scanned event", "no scanned event")
        passed &= check(out.getvalue() == "" and err.getvalue() == "", "silent", "printed")

        with captured():
            dry = engine.scan_once(config=box.config.detached(dry_run=True))
        passed &= check(dry.processed == 1 and dry.dry_run == 0 and dry.unique == 1,
                        "second pass finds nothing new", f"{dry.as_dict()}")
        return passed


def test_event_queue_drops_oldest_and_counts():
    print("\nTesting bounded event queue...")
    from duplicate_preventer.engine.events import Event
    q = EventQueue(maxsize=3)
    for i in range(5):
        q.put(Event("checked", f"/f{i}"))
    got = [e.path for e in q.drain()]
    passed = check(got == ["/f2", "/f3", "/f4"], f"kept newest three: {got}", f"kept: {got}")
    passed &= check(q.dropped == 2, "two drops counted", f"dropped: {q.dropped}")
    raised = False
    try:
        Event("bogus")
    except ValueError:
        raised = True
    passed &= check(raised, "unknown event kind rejected", "unknown kind accepted")
    return passed


def main():
    return run_suite("engine tests", [
        test_engine_package_imports_no_ui_and_is_silent,
        test_no_print_statements_in_engine_source,
        test_live_duplicate_is_quarantined_with_event,
        test_slow_writer_is_compared_after_it_finishes,
        test_rename_into_pattern_is_caught,
        test_unique_file_is_left_alone,
        test_dry_run_moves_nothing_but_reports,
        test_status_health_tracks_observer_liveness,
        test_missing_folder_reported_not_fatal,
        test_hot_reload_changes_watch_list_without_restart,
        test_hot_reload_survives_corrupt_config,
        test_second_engine_refuses_and_reports_holder,
        test_lock_is_held_across_processes,
        test_scan_once_handles_existing_files_recursively,
        test_event_queue_drops_oldest_and_counts,
    ])


if __name__ == '__main__':
    exit(0 if main() else 1)


# End of file #
