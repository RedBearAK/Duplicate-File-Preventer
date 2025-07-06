# Duplicate File Preventer

Monitors folders for duplicate files created by Thunderbird's FiltaQuilla extension and automatically quarantines them before they sync to cloud storage.

## Problem

FiltaQuilla sometimes creates duplicate attachments (file-1.pdf, file-2.pdf) when saving from Thunderbird. This tool catches and removes those duplicates in real-time.

## Features

- **Real-time monitoring** - Watches folders for new duplicate files
- **One-shot cleanup** - Scan existing folders for duplicates
- **Safe quarantine** - Moves duplicates to a separate folder (not deleted)
- **Flexible detection** - By filename pattern, size, creation time, and optional hash
- **Auto-save** - All configuration changes save automatically

## Installation

```bash
git clone https://github.com/yourusername/Duplicate-File-Preventer.git
cd Duplicate-File-Preventer
pip3 install -r requirements.txt
```

## Usage

```bash
# Run interactive menu
python3 main.py

# Start monitoring immediately
python3 main.py --start

# Dry-run mode (test without moving files)
python3 main.py --dry-run

# Quick log check
python3 main.py --show-log
```

## Basic Workflow

1. Run the tool
2. Add folders to monitor (option 1)
3. Start monitoring (option 4)
4. Or use "Clean existing duplicates" (option 8) for one-time cleanup

## Configuration

- **macOS**: `~/Library/Application Support/DuplicateMonitor/`
- **Linux**: `~/.config/duplicate-monitor/`
- **Windows**: `%APPDATA%\DuplicateMonitor\`

## Requirements

- Python 3.7+
- watchdog
- rich

## License

GNU General Public License 3.0
