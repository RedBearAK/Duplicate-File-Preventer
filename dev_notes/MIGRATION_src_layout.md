# Migration note: flat package → src layout + engine/front-end split

Branch: `dev_beta`, 2026-08-30. This is Phase 0 + Phase 1 of
`HANDOFF_menubar_conversion.md`, plus the §2b output discipline and the §5b
launcher installer. The menu bar front end itself (Phase 2) is present as an
untested file.

## What moved where

| Before | After |
|---|---|
| `duplicate_preventer/config.py` | `src/duplicate_preventer/engine/config.py` |
| `duplicate_preventer/utils.py` | `src/duplicate_preventer/engine/utils.py` |
| `duplicate_preventer/duplicate_handler.py` | split: `engine/rules.py` (decide), `engine/quarantine.py` (move/restore/clean), `engine/processor.py` (the one check-and-quarantine path), `engine/logsetup.py` (file log), `engine/monitor.py` (`_Handler`) |
| `duplicate_preventer/duplicate_monitor.py` | split: `engine/monitor.py` (`Engine`: start/stop/status/reload/lock), `engine/scanner.py` (one-shot scan), `frontends/tui.py` (the menu), `frontends/cli.py` (flags) |
| `duplicate_preventer/__main__.py` | `src/duplicate_preventer/__main__.py` (dispatch only) |
| — | `engine/events.py`, `engine/lock.py`, `frontends/render.py`, `frontends/installer.py`, `frontends/menubar.py` |
| `HANDOFF_menubar_conversion.md` (root) | `dev_notes/` |
| — | `tests/` (7 modules, 66 tests), `requirements-dev.txt`, `pytest.ini` section in pyproject |

Public API: `from duplicate_preventer import Config, Engine`. The old
`DuplicateHandler` / `DuplicateMonitor` classes are gone; nothing outside the
repo imported them.

## Compatibility kept

- Config file location, name, and JSON keys are unchanged. Old files load; new
  keys (`settle_seconds`) are merged in with defaults.
- Log file location, format, and the keyword vocabulary (`QUARANTINED:`,
  `DUPLICATE CONFIRMED:`, `NO DUPLICATE FOUND:`, `DRY RUN - WOULD QUARANTINE:`)
  are unchanged, so old logs and the colorizer still agree.
- Quarantine layout (`<root>/<date>/<relative path>/<file>` + `.restore_info`)
  is unchanged; old quarantines restore.
- Flags `--start`, `--dry-run`, `--show-log`, `--config` behave as before.

## Deliberate behavior changes

1. **The watcher no longer prints into the menu.** The engine writes only to
   the log and the event queue. The menu status line, the new "Watch activity"
   view (option 5), and `--start`'s streaming output replace it. The DEBUG
   console log handler is gone.
2. **`Config.set()` is silent.** No more "Configuration saved" after every
   prompt in the settings screen.
3. **New files are compared after they stop growing** (`settle_seconds`, default
   1.0 s). The old code compared at the create event, i.e. against a partial
   file, and could pass a real duplicate as "size mismatch".
4. **One monitor per config, OS-level lock.** `--start` and the menu both take
   the lock; a second starter is told the holder PID. The old pid-file check is
   gone. The lock vanishes with the process, so no stale-lock cleanup.
5. **Hot reload.** A running monitor re-reads the config when another process
   saves it (0.5 s poll), restarts the observer if the folder list changed, and
   flags itself unhealthy (without stopping) if the file is unreadable.
6. **Console script:** one name, `duplicate-preventer`. `duplicate-monitor` is
   dropped.
7. **`check_interval`** is kept in the config for compatibility but is no longer
   shown in the settings screen and has no effect.
8. **Python floor 3.9** (watchdog 6 needs it; the old ">=3.7" was already not
   true for current watchdog).

## New flags

`--once`, `--follow-log`, `--lines N`, `--install-command [--dir DIR]`,
`--uninstall-command`, `--menubar`, `--version`.

## Known gaps / next

- `frontends/menubar.py` is written to the handoff design but has not been run
  on a Mac. Phase 2 acceptance is still open.
- Watching only handles `on_created`. Apps that write to a temp name and
  rename into place would need `on_moved` (dest) as well; not needed for
  FiltaQuilla as far as is known.
- Double-extension names (`file-1.tar.gz`) are not detected, as before;
  `tests/test_utils.py` pins that so a change is visible.
