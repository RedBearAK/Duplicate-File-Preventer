"""
Duplicate File Preventer

Watches folders for the file-1.ext / file-2.ext duplicates that
Thunderbird's FiltaQuilla extension sometimes creates, and moves them
aside before they sync to cloud storage.

The package is an engine (duplicate_preventer.engine) plus thin front
ends (duplicate_preventer.frontends): an interactive terminal menu, a
flag-driven CLI, and an optional macOS menu bar app.
"""

from duplicate_preventer._version import __version__
from duplicate_preventer.engine import Config, Engine

__all__ = ['Config', 'Engine', '__version__']


# End of file #
