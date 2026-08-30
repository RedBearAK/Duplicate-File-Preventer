"""
The one code path that takes a suspicious file from "looks like file-N.ext"
to "quarantined / left alone", for both the live watcher and one-shot scans.

Output goes to the log and the event queue. Never to a terminal.
"""

import os
import time
import threading

from datetime import datetime

from duplicate_preventer.engine.rules import find_duplicate_of
from duplicate_preventer.engine.utils import (
    file_creation_time,
    is_potential_duplicate,
)
from duplicate_preventer.engine.events import Event
from duplicate_preventer.engine.logsetup import get_logger
from duplicate_preventer.engine.quarantine import quarantine_file, QuarantineError


class Counters:
    """Session counters, safe to bump from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self.checked = 0
        self.quarantined = 0
        self.errors = 0

    def bump(self, name):
        with self._lock:
            setattr(self, name, getattr(self, name) + 1)


# A create event fires when the file is opened, not when the writer is done.
# Comparing sizes at that instant compares against a partial file, so a
# real duplicate can be waved through as "size mismatch". Wait for the size
# and mtime to hold still for `settle_seconds` before deciding.
SETTLE_INTERVAL = 0.25      # seconds between samples
DEFAULT_SETTLE_SECONDS = 1.0
SETTLE_TIMEOUT = 60.0       # give up waiting (huge attachment still copying)


def wait_until_stable(path, settle_seconds=DEFAULT_SETTLE_SECONDS,
                      interval=SETTLE_INTERVAL, timeout=SETTLE_TIMEOUT):
    """
    Block until (size, mtime) has been unchanged for settle_seconds, or the
    file vanishes, or timeout. Returns True if stable, False otherwise.
    """
    deadline = time.time() + timeout
    stable_since = None
    last = None
    while time.time() < deadline:
        try:
            stat_info = os.stat(path)
        except OSError:
            return False
        sample = (stat_info.st_size, stat_info.st_mtime_ns)
        now = time.time()
        if sample != last or stat_info.st_size == 0:
            last = sample
            stable_since = now
        elif now - stable_since >= settle_seconds:
            return True
        time.sleep(interval)
    return False


class DuplicateProcessor:

    def __init__(self, config, events, counters=None, logger=None):
        self.config = config
        self.events = events
        self.counters = counters or Counters()
        self.logger = logger or get_logger()

    def is_candidate(self, file_path):
        return is_potential_duplicate(file_path, self.config.get("file_patterns", []))

    def process(self, file_path, settle=False):
        """
        Check one file. With settle=True (live events), first wait for the
        writer to finish. Returns one of:
            "quarantined", "dry_run", "unique", "missing", "error"
        """
        filename = os.path.basename(file_path)
        self.counters.bump("checked")

        settle_seconds = float(self.config.get("settle_seconds", DEFAULT_SETTLE_SECONDS))
        if settle and not wait_until_stable(file_path, settle_seconds):
            if not os.path.exists(file_path):
                self.logger.info(f"SKIPPED (vanished): {file_path}")
                return "missing"
            self.logger.warning(f"Size never settled, checking anyway: {file_path}")

        try:
            file_size = os.path.getsize(file_path)
            created = datetime.fromtimestamp(file_creation_time(file_path))
        except OSError as error:
            # Created-then-gone is normal for temp files; not an error.
            self.logger.info(f"SKIPPED (vanished): {file_path} ({error})")
            return "missing"

        self.logger.info(f"Analyzing: {filename} (Size: {file_size} bytes, "
                         f"Created: {created.strftime('%Y-%m-%d %H:%M:%S')})")
        self.events.put(Event("checked", file_path, f"{file_size} bytes"))

        original, reason = find_duplicate_of(file_path, self.config)

        if original is None:
            self.logger.info(f"NO DUPLICATE FOUND: {filename} appears to be unique ({reason})")
            return "unique"

        original_name = os.path.basename(original)
        self.logger.info(f"DUPLICATE CONFIRMED: {filename} is duplicate of "
                         f"{original_name} ({reason})")
        why = f"Duplicate of {original_name}"

        if self.config.get("dry_run", False):
            self.logger.info(f"DRY RUN - WOULD QUARANTINE: {file_path} (Reason: {why})")
            self.events.put(Event("dry_run", file_path, why))
            return "dry_run"

        try:
            dest_path, size = quarantine_file(file_path, why, self.config)
        except QuarantineError as error:
            self.counters.bump("errors")
            self.logger.error(f"FAILED - {error}")
            self.events.put(Event("error", file_path, str(error)))
            return "error"

        self.counters.bump("quarantined")
        self.logger.info(f"QUARANTINED: {file_path} -> {dest_path} "
                         f"(Size: {size} bytes, Reason: {why})")
        self.events.put(Event("quarantined", file_path, f"{why} -> {dest_path}"))
        return "quarantined"


# End of file #
