"""
Duplicate File Preventer Package
Automatic duplicate removal for Thunderbird FiltaQuilla
"""

# Make key classes available at package level
from .config import Config
from .duplicate_handler import DuplicateHandler
from .duplicate_monitor import DuplicateMonitor
from ._version import __version__

__all__ = ['Config', 'DuplicateHandler', 'DuplicateMonitor']
