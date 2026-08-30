"""
UI-free engine for Duplicate File Preventer.

Import rule: nothing in this package imports rich, rumps, AppKit or any
other UI/GUI module, and nothing here writes to stdout/stderr. The engine
speaks through the file log and the event queue only. tests/test_engine.py
enforces both.
"""

from duplicate_preventer.engine.lock import MonitorLock
from duplicate_preventer.engine.config import Config, ConfigError
from duplicate_preventer.engine.events import Event, Status, EventQueue
from duplicate_preventer.engine.monitor import Engine, EngineError
from duplicate_preventer.engine.scanner import ScanResult, scan_folders

__all__ = [
    'Config', 'ConfigError', 'Engine', 'EngineError', 'Event', 'EventQueue',
    'MonitorLock', 'ScanResult', 'Status', 'scan_folders',
]


# End of file #
