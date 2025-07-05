"""
Duplicate File Preventer Package
Automatic duplicate removal for Thunderbird FiltaQuilla
"""

# Year-Month-Day versioning scheme
__version__ = "20250704"
__author__ = "RedBearAK"

# Make key classes available at package level
from .config import Config
from .duplicate_handler import DuplicateHandler
from .duplicate_monitor import DuplicateMonitor

__all__ = ['Config', 'DuplicateHandler', 'DuplicateMonitor']
