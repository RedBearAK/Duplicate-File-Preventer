"""
Shared scaffolding for the test modules.

tests/_helpers.py

Every test builds a real config file, real folders and real files under a
temporary directory: no mocks, no patched filesystem. Importing this module
also makes `duplicate_preventer` importable straight from the checkout so
`python3 tests/test_x.py` works without an install.
"""

import os
import sys
import time
import tempfile

from pathlib import Path

# src layout: make the package importable from a bare checkout.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from duplicate_preventer.engine import Config      # noqa: E402


class Sandbox:
    """
    A temp dir holding: a watched folder, a quarantine folder, a config
    file pointing at both, and a log file. Use as a context manager.
    """

    def __init__(self, **config_overrides):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.watched = self.root / "watched"
        self.quarantine = self.root / "quarantine"
        self.config_dir = self.root / "config"
        self.watched.mkdir()
        self.config_dir.mkdir()

        self.config = Config(config_file=str(self.config_dir / "duplicate_monitor.json"))
        settings = {
            "watched_folders": [str(self.watched)],
            "quarantine_path": str(self.quarantine),
            "log_file": str(self.config_dir / "duplicate_monitor.log"),
            "check_size": True,
            "check_time": False,
            "use_hash": False,
            "dry_run": False,
        }
        settings.update(config_overrides)
        self.config.update(settings)

    @property
    def log_file(self):
        return self.config.get("log_file")

    def log_text(self):
        try:
            return Path(self.log_file).read_text(encoding="utf-8")
        except OSError:
            return ""

    def plant(self, name, content=b"x" * 100, folder=None):
        """Create a file in the watched folder (or `folder`). Returns its path."""
        folder = Path(folder) if folder else self.watched
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(content)
        return str(path)

    def plant_pair(self, base="report.pdf", dup="report-1.pdf",
                   content=b"y" * 200, dup_content=None, folder=None):
        """An original and a duplicate. Returns (original_path, dup_path)."""
        original = self.plant(base, content, folder)
        duplicate = self.plant(dup, content if dup_content is None else dup_content, folder)
        return original, duplicate

    def quarantined_files(self):
        if not self.quarantine.is_dir():
            return []
        return sorted(str(p.relative_to(self.quarantine))
                      for p in self.quarantine.rglob("*")
                      if p.is_file() and not p.name.endswith(".restore_info"))

    def close(self):
        self._tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def wait_for(predicate, timeout=5.0, interval=0.05):
    """Poll until predicate() is truthy or timeout; returns the final value."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def check(condition, ok_text, fail_text):
    """Print a ✓/✗ line and return the condition."""
    if condition:
        print(f"  ✓ {ok_text}")
    else:
        print(f"  ✗ {fail_text}")
    return bool(condition)


def run_suite(title, tests):
    """Run test functions, print a score, return True when all passed."""
    print(f"=== {title} ===")
    passed = 0
    for test_func in tests:
        try:
            if test_func():
                passed += 1
        except Exception as error:
            print(f"  ✗ {test_func.__name__} crashed: {type(error).__name__}: {error}")
    print(f"\n=== Results: {passed}/{len(tests)} tests passed ===")
    if passed == len(tests):
        print(f"✅ All {title} passed!")
        return True
    print(f"❌ Some {title} failed!")
    return False


# End of file #
