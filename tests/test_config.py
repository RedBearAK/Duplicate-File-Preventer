"""
Tests for engine/config.py.

tests/test_config.py

The two behaviors that matter for the menu-bar plan: saves are atomic
(a second process never reads a half-written file), and the engine can
tell when another process changed the file. Plus the compatibility
promise: the JSON format and keys are what the old tool wrote.

Runnable with pytest, but written to run standalone and report a score.
"""

import io
import os
import sys
import json
import time
import tempfile
import contextlib

from pathlib import Path

from _helpers import check, run_suite

from duplicate_preventer.engine import Config, ConfigError


def test_defaults_and_file_creation_on_first_save():
    print("\nTesting defaults and first save...")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sub", "duplicate_monitor.json")
        config = Config(config_file=path)
        passed = True
        passed &= check(os.path.isdir(os.path.dirname(path)), "config dir created",
                        "config dir missing")
        passed &= check(not os.path.exists(path), "no file until first save",
                        "file written on construction")
        passed &= check(config.get("check_size") is True and config.get("time_window") == 300,
                        "defaults present", f"defaults wrong: {config.config}")
        config.set("dry_run", True)
        passed &= check(os.path.exists(path), "set() wrote the file", "set() did not save")
        with open(path) as handle:
            on_disk = json.load(handle)
        passed &= check(on_disk["dry_run"] is True and "watched_folders" in on_disk,
                        "JSON has expected keys", f"unexpected JSON: {on_disk}")
        return passed


def test_set_is_silent():
    """The old Config printed 'Configuration saved' on every set(). Not any more."""
    print("\nTesting that set() prints nothing...")
    with tempfile.TemporaryDirectory() as tmp:
        config = Config(config_file=os.path.join(tmp, "c.json"))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            config.set("dry_run", True)
            config.update({"check_time": True, "time_window": 60})
            config.save_config()
        return check(out.getvalue() == "" and err.getvalue() == "",
                     "no output from set/update/save",
                     f"output: {out.getvalue()!r} / {err.getvalue()!r}")


def test_legacy_file_gets_new_keys_merged():
    """A file from an older version lacks newer keys; they are filled in, not lost."""
    print("\nTesting legacy config merge...")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "c.json")
        with open(path, "w") as handle:
            json.dump({"watched_folders": ["/old"], "dry_run": True}, handle)
        config = Config(config_file=path)
        passed = True
        passed &= check(config.get("watched_folders") == ["/old"], "existing value kept",
                        f"existing value lost: {config.get('watched_folders')}")
        passed &= check(config.get("dry_run") is True, "existing bool kept", "bool lost")
        passed &= check(config.get("hash_algorithm") == "sha256", "missing key defaulted",
                        "missing key not defaulted")
        return passed


def test_atomic_save_leaves_no_temp_files_and_replaces_in_place():
    print("\nTesting atomic save...")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "c.json")
        config = Config(config_file=path)
        for i in range(20):
            config.set("delete_after_days", i)
        leftovers = [n for n in os.listdir(tmp) if n != "c.json"]
        passed = check(not leftovers, "no temp files left behind", f"leftovers: {leftovers}")
        with open(path) as handle:
            data = json.load(handle)
        passed &= check(data["delete_after_days"] == 19, "last write wins",
                        f"got {data['delete_after_days']}")
        return passed


def test_change_detection_and_reload():
    print("\nTesting changed_on_disk() and reload()...")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "c.json")
        writer = Config(config_file=path)
        writer.set("watched_folders", ["/a"])

        reader = Config(config_file=path)
        passed = check(not reader.changed_on_disk(), "fresh load: unchanged",
                       "fresh load reported as changed")

        reader.set("dry_run", True)
        passed &= check(not reader.changed_on_disk(), "own save: unchanged",
                        "own save reported as changed")

        time.sleep(0.02)
        writer.reload()
        writer.set("watched_folders", ["/a", "/b"])
        passed &= check(reader.changed_on_disk(), "other writer: changed",
                        "other writer's save not detected")

        reader.reload()
        passed &= check(reader.get("watched_folders") == ["/a", "/b"], "reload picked up new value",
                        f"reload got {reader.get('watched_folders')}")
        passed &= check(not reader.changed_on_disk(), "after reload: unchanged",
                        "still changed after reload")
        return passed


def test_reload_failure_keeps_old_config():
    print("\nTesting reload() on a corrupt file...")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "c.json")
        config = Config(config_file=path)
        config.set("watched_folders", ["/keep"])
        with open(path, "w") as handle:
            handle.write("{ this is not json")

        raised = False
        try:
            config.reload()
        except ConfigError:
            raised = True
        passed = check(raised, "ConfigError raised", "no error on corrupt file")
        passed &= check(config.get("watched_folders") == ["/keep"], "old config retained",
                        f"config clobbered: {config.get('watched_folders')}")

        with open(path, "w") as handle:
            handle.write("[1, 2, 3]")
        raised = False
        try:
            config.reload()
        except ConfigError:
            raised = True
        passed &= check(raised, "non-object JSON rejected", "list accepted as config")
        return passed


def test_detached_copy_never_saves():
    print("\nTesting detached() copies...")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "c.json")
        config = Config(config_file=path)
        config.set("dry_run", False)
        mtime = os.path.getmtime(path)

        clone = config.detached(dry_run=True, check_time=True)
        time.sleep(0.02)
        clone.set("time_window", 1)
        clone.update({"use_hash": True})

        passed = check(clone.get("dry_run") is True and clone.get("check_time") is True,
                       "overrides applied", "overrides missing")
        passed &= check(config.get("dry_run") is False and config.get("use_hash") is False,
                        "original untouched", f"original changed: {config.config}")
        passed &= check(os.path.getmtime(path) == mtime, "file not rewritten",
                        "detached copy wrote the file")
        passed &= check(clone.config_file == config.config_file, "same config_file path",
                        "config_file path differs")
        return passed


def test_platform_default_dir_is_sane():
    print("\nTesting default config dir...")
    from duplicate_preventer.engine.config import default_config_dir, default_quarantine_path
    config_dir = default_config_dir()
    quarantine = default_quarantine_path()
    passed = check(os.path.isabs(config_dir) and "uplicate" in config_dir,
                   f"config dir: {config_dir}", f"odd config dir: {config_dir}")
    passed &= check(os.path.isabs(quarantine) and quarantine.endswith("Quarantined_Duplicates"),
                    f"quarantine: {quarantine}", f"odd quarantine: {quarantine}")
    if sys.platform.startswith("linux"):
        passed &= check(config_dir.endswith(os.path.join(".config", "duplicate-monitor")),
                        "Linux uses ~/.config/duplicate-monitor",
                        f"unexpected Linux dir {config_dir}")
    return passed


def main():
    return run_suite("config tests", [
        test_defaults_and_file_creation_on_first_save,
        test_set_is_silent,
        test_legacy_file_gets_new_keys_merged,
        test_atomic_save_leaves_no_temp_files_and_replaces_in_place,
        test_change_detection_and_reload,
        test_reload_failure_keeps_old_config,
        test_detached_copy_never_saves,
        test_platform_default_dir_is_sane,
    ])


if __name__ == '__main__':
    exit(0 if main() else 1)


# End of file #
