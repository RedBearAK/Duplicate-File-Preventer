# Duplicate File Preventer

Watches folders for the `file-1.pdf`, `file-2.pdf` duplicates that Thunderbird's
FiltaQuilla extension sometimes creates when saving attachments, and moves them
aside before they sync to cloud storage. Nothing is ever deleted: duplicates go
to a quarantine folder with a note saying where they came from, and can be
restored from the menu.

## Try it without installing

```bash
git clone https://github.com/RedBearAK/Duplicate-File-Preventer.git
cd Duplicate-File-Preventer
pip3 install -r requirements.txt
python3 -m duplicate_preventer          # interactive menu
```

## Install the `duplicate-preventer` command

Pick one:

```bash
# Isolated install, command on ~/.local/bin, survives Python upgrades:
pipx install 'git+https://github.com/RedBearAK/Duplicate-File-Preventer'

# From a checkout into a venv you manage, then let the tool put a launcher on PATH:
python3 -m venv ~/.venvs/dfp && ~/.venvs/dfp/bin/pip install -e .
~/.venvs/dfp/bin/duplicate-preventer --install-command
```

`--install-command` writes a two-line stub into `~/.local/bin` (or `--dir DIR`)
that records which interpreter to use. Re-run it after rebuilding the venv and
it repairs itself. It never edits your shell startup files; if the directory
isn't on your PATH it prints the line to add.

## Use

```bash
duplicate-preventer                 # interactive menu: folders, settings, logs, quarantine
duplicate-preventer --start         # monitor in the foreground, events stream to the terminal
duplicate-preventer --once          # scan the watched folders for existing duplicates, then exit
duplicate-preventer --once --dry-run
duplicate-preventer --follow-log    # live log, Ctrl-C to stop
duplicate-preventer --show-log
```

Typical first run: open the menu, add folders (option 1), then either start
monitoring (4) for new files or run "Clean existing duplicates" (8) for what's
already there. Start with dry run on; the log tells you exactly what would move.

The menu's status line shows what the watcher is doing, and option 5 opens a
live activity view (Ctrl-C returns to the menu). Activity never scrolls over
the menu itself; details are always in the log.

Settings changed in the menu apply live to a monitor that is already running,
whether that monitor was started from this menu, from `--start` in another
terminal, or from the macOS menu bar app. Only one monitor can run per
configuration; the menu tells you when another process has it.

## How duplicates are decided

A file is quarantined when it is named like `something-N.ext`, a `something.ext`
sits next to it, and every enabled check passes: file size (on by default),
creation time within a window (off by default), and a content hash (off by
default; slower, but catches same-size-different-content). New files are
compared only after they have stopped growing, so a large attachment still
being written is not mistaken for a mismatch.

## macOS menu bar app (optional, in progress)

```bash
pip install '.[menubar]'                # pulls in rumps, macOS only
duplicate-preventer --menubar
```

The menu bar controls Start/Stop and shows health; settings and logs stay in
the terminal. See `dev_notes/HANDOFF_menubar_conversion.md` for the plan and
current status.

## Where things live

| | macOS | Linux | Windows |
|---|---|---|---|
| Config + log | `~/Library/Application Support/DuplicateMonitor/` | `~/.config/duplicate-monitor/` | `%APPDATA%\DuplicateMonitor\` |
| Quarantine | `~/Quarantined_Duplicates/` | `~/Quarantined_Duplicates/` | `Documents\Quarantined_Duplicates\` |

## Development

```bash
pip install -r requirements-dev.txt
pytest                      # whole suite
python3 tests/run_all.py    # same, standalone, with per-test output
python3 tests/test_engine.py
```

Source is under `src/duplicate_preventer/`: a UI-free `engine/` (watchdog,
rules, quarantine, config, lock) and thin `frontends/` (menu, CLI, menu bar).
The engine never writes to the terminal; the test suite enforces that.

Requires Python 3.9+, `watchdog`, `rich`. GPL-3.0.
