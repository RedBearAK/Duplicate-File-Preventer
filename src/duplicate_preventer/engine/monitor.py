"""
The Engine: owns the watchdog observer, the monitoring lock, the event
queue and the session counters. Every front end drives one of these.

Threading: watchdog delivers events on its observer thread. The handler
below only touches the processor (log + queue + counters), never a UI.
status() and reload_config_if_changed() are cheap and safe from any thread.
"""

import os
import time
import threading

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from duplicate_preventer.engine.lock import MonitorLock
from duplicate_preventer.engine.config import Config, ConfigError
from duplicate_preventer.engine.events import Event, Status, EventQueue
from duplicate_preventer.engine.scanner import scan_folders
from duplicate_preventer.engine.logsetup import setup_logging, get_logger
from duplicate_preventer.engine.processor import DuplicateProcessor, Counters
from duplicate_preventer.engine.quarantine import count_quarantined


class EngineError(Exception):
    """start() could not proceed (typically: lock held elsewhere)."""


class _Handler(FileSystemEventHandler):
    """Watchdog callback -> DuplicateProcessor. Runs on the observer thread."""

    def __init__(self, processor):
        super().__init__()
        self.processor = processor

    def on_created(self, event):
        if event.is_directory:
            return
        path = event.src_path
        if self.processor.is_candidate(path):
            self.processor.logger.info(f"Potential duplicate detected: {path}")
            self.processor.process(path, settle=True)


class Engine:

    def __init__(self, config=None, event_queue_size=500):
        self.config = config or Config()
        self.events = EventQueue(maxsize=event_queue_size)
        self.counters = Counters()
        self.logger = get_logger()
        self.lock = MonitorLock(self.config.config_dir)

        self._observer = None
        self._handler = None
        self._state_lock = threading.RLock()
        self._monitoring = False
        self._last_error = None
        self._started_at = None
        self._active_folders = []
        self._quarantined_at_start = 0
        self._logging_signature = None

    # --- lifecycle ---------------------------------------------------------

    def start(self):
        """
        Take the lock, configure logging, start watching. Raises EngineError
        when another process already monitors this config. A configured
        folder that is missing is skipped and reported, not fatal; zero
        usable folders is reported as unhealthy.
        """
        with self._state_lock:
            if self._monitoring:
                return
            if not self.lock.acquire():
                raise EngineError(
                    f"monitoring is already running in another process "
                    f"(PID {self.lock.holder_pid() or 'unknown'})")

            self._ensure_logging()
            self.logger.info("=" * 60)
            self.logger.info(f"Duplicate File Preventer started - PID {os.getpid()}")
            self.logger.info(f"Config: {self.config.config_file}")
            self.logger.info(f"Dry run mode: "
                             f"{'ENABLED' if self.config.get('dry_run') else 'DISABLED'}")
            self.logger.info(f"Detection: {self.detection_summary()}")

            try:
                self._quarantined_at_start = count_quarantined(self.config)
            except OSError:
                self._quarantined_at_start = 0

            self._last_error = None
            self._start_observer()
            self._monitoring = True
            self._started_at = time.time()
            count = len(self._active_folders)
            self.events.put(Event("started", "", f"{count} folder{'s' if count != 1 else ''}"))

    def stop(self):
        with self._state_lock:
            if not self._monitoring and self._observer is None:
                self.lock.release()
                return
            self._stop_observer()
            self._monitoring = False
            self.lock.release()
            self.logger.info("Monitor stopped")
            self.events.put(Event("stopped"))

    def _start_observer(self):
        processor = DuplicateProcessor(self.config, self.events, self.counters, self.logger)
        self._handler = _Handler(processor)
        self._observer = Observer()
        self._active_folders = []

        for folder in self.config.get("watched_folders", []):
            if os.path.isdir(folder):
                try:
                    self._observer.schedule(self._handler, folder, recursive=True)
                except OSError as error:
                    self.logger.error(f"Cannot watch {folder}: {error}")
                    self.events.put(Event("error", folder, f"cannot watch: {error}"))
                    continue
                self._active_folders.append(folder)
                self.logger.info(f"Watching: {folder}")
            else:
                self.logger.warning(f"Skipping missing folder: {folder}")
                self.events.put(Event("error", folder, "folder missing, not watched"))

        if not self._active_folders:
            self._last_error = "no usable folders to watch"
            self.logger.error(self._last_error)
            self.events.put(Event("error", "", self._last_error))

        self._observer.start()

    def _stop_observer(self):
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=5)
        except Exception as error:
            self.logger.error(f"Error stopping observer: {error}")
        self._observer = None
        self._handler = None
        self._active_folders = []

    def _ensure_logging(self):
        signature = (self.config.get("log_file"), self.config.get("log_level"),
                     self.config.get("log_max_size"), self.config.get("log_backup_count"))
        if signature != self._logging_signature:
            self.logger = setup_logging(self.config)
            self._logging_signature = signature

    # --- one-shot ----------------------------------------------------------

    def scan_once(self, config=None, progress=None):
        """
        Scan the watched folders for existing duplicates. `config` lets a
        front end pass a detached copy with different settings (dry run,
        no time window ...). Returns a ScanResult. Does not need the lock
        and does not need monitoring to be running.
        """
        scan_config = config or self.config
        self._ensure_logging()
        self.logger.info(f"SCAN START ({scan_config.get('watched_folders')}) - "
                         f"{self._describe(scan_config)}")
        result = scan_folders(scan_config, self.events, progress=progress, logger=self.logger)
        self.logger.info(f"SCAN COMPLETE: {result.as_dict()}")
        return result

    # --- status ------------------------------------------------------------

    def observer_alive(self):
        observer = self._observer
        return observer is not None and observer.is_alive()

    def status(self):
        with self._state_lock:
            monitoring = self._monitoring
            alive = self.observer_alive()
            last_error = self._last_error

            if monitoring and not alive:
                last_error = last_error or "observer thread is not running"

            healthy = last_error is None and (alive if monitoring else True)

            holder = None
            if not self.lock.held and self.lock.is_locked_elsewhere():
                holder = self.lock.holder_pid()

            return Status(
                monitoring=monitoring,
                healthy=healthy,
                last_error=last_error,
                watched_folders=list(self._active_folders) if monitoring
                else list(self.config.get("watched_folders", [])),
                quarantined_session=self.counters.quarantined,
                quarantined_total=self._quarantined_at_start + self.counters.quarantined,
                checked_session=self.counters.checked,
                last_event_ts=None,
                last_event=None,
                dry_run=bool(self.config.get("dry_run", False)),
                dropped_events=self.events.dropped,
                lock_holder_pid=holder,
                started_at=self._started_at,
            )

    def detection_summary(self):
        return self._describe(self.config)

    @staticmethod
    def _describe(config):
        from duplicate_preventer.engine.utils import format_time_window
        parts = ["Size=ON" if config.get("check_size") else "Size=OFF"]
        if config.get("check_time"):
            parts.append(f"Time=ON ({format_time_window(int(config.get('time_window')))})")
        else:
            parts.append("Time=OFF")
        if config.get("use_hash"):
            parts.append(f"Hash=ON ({config.get('hash_algorithm')})")
        else:
            parts.append("Hash=OFF")
        return ", ".join(parts)

    # --- hot reload --------------------------------------------------------

    def reload_config_if_changed(self):
        """
        Re-read the config file if another process changed it. Restarts
        the observer when the folder list changed. A parse failure keeps
        the old config and marks the engine unhealthy until the file is
        readable again. Returns True when a reload happened.
        """
        if not self.config.changed_on_disk():
            return False

        with self._state_lock:
            old_folders = list(self.config.get("watched_folders", []))
            try:
                self.config.reload()
            except ConfigError as error:
                self._last_error = f"config unreadable: {error}"
                self.logger.error(self._last_error)
                self.events.put(Event("error", self.config.config_file, str(error)))
                return False

            if self._last_error and self._last_error.startswith("config unreadable"):
                self._last_error = None

            new_folders = list(self.config.get("watched_folders", []))
            self._ensure_logging()
            detail = "settings"

            if self._monitoring and new_folders != old_folders:
                self._stop_observer()
                self._last_error = None
                self._start_observer()
                detail = f"watch list -> {len(self._active_folders)} folders"
            elif self._monitoring and self._handler is not None:
                # Same folders: the processor reads config live, so just
                # make sure it is looking at the reloaded instance.
                self._handler.processor.config = self.config

            self.logger.info(f"Config reloaded ({detail}); detection: "
                             f"{self.detection_summary()}")
            self.events.put(Event("config_reloaded", self.config.config_file, detail))
            return True

    # --- test hook ---------------------------------------------------------

    def _kill_observer_for_test(self):
        """Simulate the observer thread dying. Tests only."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)


# End of file #
