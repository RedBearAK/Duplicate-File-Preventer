"""
Configuration management for Duplicate File Preventer
Handles loading, saving, and platform-specific paths
"""
import json
import os
import platform
from pathlib import Path
from typing import Dict, Any

from rich.console import Console

console = Console()


class Config:
    """Manages configuration with interactive updates"""
    def __init__(self, config_file: str = None):
        # Platform-specific config locations
        if config_file is None:
            self.config_dir = self._get_config_dir()
            self.config_file = os.path.join(self.config_dir, "duplicate_monitor.json")
        else:
            self.config_file = config_file
            self.config_dir = os.path.dirname(config_file)

        # Ensure config directory exists
        os.makedirs(self.config_dir, exist_ok=True)

        self.default_config = {
            "watched_folders": [],
            "quarantine_path": self._get_default_quarantine_path(),
            "check_interval": 5,  # seconds
            "use_hash": False,  # Hash verification OFF by default
            "hash_algorithm": "sha256",
            "time_window": 300,  # 5 minutes in seconds
            "check_time": False,  # Time window OFF by default
            "check_size": True,  # Check file size
            "file_patterns": [r"(.+?)(-\d+)?(\.[^.]+)$"],  # matches file-1.ext
            "log_file": os.path.join(self.config_dir, "duplicate_monitor.log"),
            "log_level": "INFO",  # DEBUG, INFO, WARNING, ERROR
            "log_max_size": 10,  # MB
            "log_backup_count": 5,
            "delete_after_days": 30,
            "dry_run": False,  # Dry run mode
            "enabled": True
        }
        self.config = self.load_config()

    def _get_config_dir(self) -> str:
        """Get platform-specific configuration directory"""
        if platform.system() == "Windows":
            # Windows: %APPDATA%\DuplicateMonitor
            base = os.environ.get('APPDATA', os.path.expanduser('~'))
            return os.path.join(base, 'DuplicateMonitor')
        elif platform.system() == "Darwin":
            # macOS: ~/Library/Application Support/DuplicateMonitor
            return os.path.expanduser('~/Library/Application Support/DuplicateMonitor')
        else:
            # Linux: ~/.config/duplicate-monitor
            xdg_config = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
            return os.path.join(xdg_config, 'duplicate-monitor')

    def _get_default_quarantine_path(self) -> str:
        """Get default quarantine path outside of common cloud folders"""
        home = Path.home()

        # Try to detect and avoid cloud folders
        if platform.system() == "Windows":
            # Windows: Use Documents\Quarantined_Duplicates if not in OneDrive
            docs = home / "Documents"
            if "OneDrive" not in str(docs):
                return str(docs / "Quarantined_Duplicates")
        elif platform.system() == "Darwin":
            # macOS: Use ~/Quarantined_Duplicates (outside of iCloud Documents)
            return str(home / "Quarantined_Duplicates")

        # Default: Home directory
        return str(home / "Quarantined_Duplicates")

    def load_config(self) -> Dict[str, Any]:
        """Load configuration from file or create default"""
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                loaded = json.load(f)
                # Merge with defaults to add any new keys
                for key, value in self.default_config.items():
                    if key not in loaded:
                        loaded[key] = value
                return loaded
        return self.default_config.copy()

    def save_config(self) -> None:
        """Save current configuration to file"""
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)
        console.print(f"[green]Configuration saved to {self.config_file}[/green]")

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value with optional default"""
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set configuration value"""
        self.config[key] = value
        self.save_config()
