"""
Duplicate File Preventer Package
Automatic duplicate removal for Thunderbird FiltaQuilla
"""

__version__ = "1.0.0"
__author__ = "Your Name"

# Make key classes available at package level
from .config import Config
from .duplicate_handler import DuplicateHandler
from .duplicate_monitor import DuplicateMonitor

__all__ = ['Config', 'DuplicateHandler', 'DuplicateMonitor']
