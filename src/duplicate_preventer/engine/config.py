"""
Configuration: load, auto-save, hot-reload support.

File format and location are unchanged from the pre-src-layout versions so
existing installs keep their settings. Two things ARE new:

- Saves are atomic (temp file + os.replace) because a second process may be
  reading the file while the terminal edits it.
- set() is silent. Telling the user "saved" is a front-end decision.
"""

import os
import copy
import json
import platform
import tempfile

from pathlib import Path


CONFIG_FILENAME = "duplicate_monitor.json"
LOG_FILENAME = "duplicate_monitor.log"


class ConfigError(Exception):
    """Config file exists but cannot be parsed."""


def default_config_dir():
    """Platform-standard configuration directory."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
        return os.path.join(base, 'DuplicateMonitor')
    if system == "Darwin":
        return os.path.expanduser('~/Library/Application Support/DuplicateMonitor')
    xdg_config = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
    return os.path.join(xdg_config, 'duplicate-monitor')


def default_quarantine_path():
    """A quarantine location outside the usual cloud-synced folders."""
    home = Path.home()
    if platform.system() == "Windows":
        docs = home / "Documents"
        if "OneDrive" not in str(docs):
            return str(docs / "Quarantined_Duplicates")
    return str(home / "Quarantined_Duplicates")


class Config:
    """
    Auto-saving configuration.

    Every set() writes the file unless the instance is detached (see
    detached()), which is how one-off scans get temporary overrides
    without touching the user's settings.
    """

    def __init__(self, config_file=None, autosave=True):
        if config_file is None:
            self.config_dir = default_config_dir()
            self.config_file = os.path.join(self.config_dir, CONFIG_FILENAME)
        else:
            self.config_file = os.path.abspath(config_file)
            self.config_dir = os.path.dirname(self.config_file)

        os.makedirs(self.config_dir, exist_ok=True)
        self.autosave = autosave

        self.default_config = {
            "watched_folders": [],
            "quarantine_path": default_quarantine_path(),
            "check_interval": 5,            # seconds; front-end refresh cadence
            "settle_seconds": 1.0,          # wait for a new file to stop growing
            "use_hash": False,
            "hash_algorithm": "sha256",
            "time_window": 300,             # seconds
            "check_time": False,
            "check_size": True,
            "file_patterns": [r"(.+?)(-\d+)?(\.[^.]+)$"],
            "log_file": os.path.join(self.config_dir, LOG_FILENAME),
            "log_level": "INFO",
            "log_max_size": 10,             # MB
            "log_backup_count": 5,
            "delete_after_days": 30,
            "dry_run": False,
            "enabled": True,
        }

        self._disk_signature = None
        self.config = self.load_config()

    # --- load / save -------------------------------------------------------

    def _signature(self):
        """(mtime_ns, size) of the file on disk, or None when absent."""
        try:
            stat_info = os.stat(self.config_file)
        except OSError:
            return None
        return (stat_info.st_mtime_ns, stat_info.st_size)

    def load_config(self):
        """Read the file, merging in defaults for any missing keys."""
        if not os.path.exists(self.config_file):
            self._disk_signature = None
            return copy.deepcopy(self.default_config)

        try:
            with open(self.config_file, 'r', encoding='utf-8') as handle:
                loaded = json.load(handle)
        except (OSError, ValueError) as error:
            raise ConfigError(f"{self.config_file}: {error}") from error

        if not isinstance(loaded, dict):
            raise ConfigError(f"{self.config_file}: top level is not an object")

        for key, value in self.default_config.items():
            if key not in loaded:
                loaded[key] = copy.deepcopy(value)

        self._disk_signature = self._signature()
        return loaded

    def save_config(self):
        """Atomic write: temp file in the same directory, then os.replace()."""
        os.makedirs(self.config_dir, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix=".duplicate_monitor.", suffix=".json.tmp", dir=self.config_dir)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(self.config, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.config_file)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
        self._disk_signature = self._signature()

    # --- access ------------------------------------------------------------

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        if self.autosave:
            self.save_config()

    def update(self, values):
        """Set several keys with a single save."""
        self.config.update(values)
        if self.autosave:
            self.save_config()

    # --- hot reload --------------------------------------------------------

    def changed_on_disk(self):
        """True when another writer has touched the file since we last read it."""
        return self._signature() != self._disk_signature

    def reload(self):
        """
        Re-read the file. On failure the in-memory config is left alone and
        ConfigError propagates; the caller decides how loudly to complain.
        """
        self.config = self.load_config()

    # --- detached copies ---------------------------------------------------

    def detached(self, **overrides):
        """
        A copy that never saves. Used for one-off scans with different
        settings than the user's persistent ones.
        """
        clone = copy.copy(self)
        clone.autosave = False
        clone.config = copy.deepcopy(self.config)
        clone.config.update(overrides)
        return clone


# End of file #
