#!/usr/bin/env python3

"""
Duplicate File Preventer - Entry point
Prevents duplicate files from syncing to cloud storage
"""

import sys
from pathlib import Path

# Add the script directory to Python path so imports work
sys.path.insert(0, str(Path(__file__).parent))

from duplicate_preventer.duplicate_monitor import main

if __name__ == "__main__":
    main()
