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
6. **Console script:** one name, `duplicate-file-preventer`. `duplicate-monitor` is
   dropped.
7. **`check_interval`** is kept in the config for compatibility but is no longer
   shown in the settings screen and has no effect.
8. **Python floor 3.9** (watchdog 6 needs it; the old ">=3.7" was already not
   true for current watchdog).

## Added after first macOS use (20260830.5)

- `on_moved` is handled: a rename into the `-N` pattern (Finder Duplicate then
  rename; temp-then-rename writers) is checked like a create.
- Bundle launcher is a compiled C program (`frontends/launcher_template.c`,
  built by `--install-app` with the system compiler) that spawns the interpreter
  as a child and waits. Tested on a Mac: a shell-script launcher, even one that
  spawns rather than execs, still gets the TCC grant pinned to the interpreter
  path (`identifier_type=Path, .../python3.12`), because tccd will not credit
  /bin/sh as the responsible process. A signed Mach-O inside the bundle is what
  it credits. Shell script remains as the fallback when no compiler is present,
  with a warning.
- `quarantine_method` config key: `copy_delete` (default) or `move`. Confirmed
  on a Mac: Dropbox prompts on files moved out of its folder, not on deletions,
  so copy-verify-delete keeps the tool silent. In the settings screen.

## Back-ported from Stickies-to-Markdown (20260831.1)

From `dev_notes/HANDOFF_lessons_for_DFP.md` (the S2M → DFP handoff):

- `Engine.start()` is all-or-nothing: any failure after the lock is taken
  unwinds the observer, releases the lock, logs `START FAILED`, and re-raises
  as `EngineError`. Previously a failure left the lock held with
  `monitoring=False`, which the menu showed as "stopped" but Start could not fix.
- Each watched folder is probed with `os.listdir()` before scheduling, so a
  TCC denial is an explicit `PermissionError` (logged, `Status.denied_folders`)
  instead of a watcher that silently delivers nothing.
- `engine/logsetup.py` installs a `NullHandler` at import so an engine that
  logs before `start()` (a reload error, a probe) never falls back to
  logging's stderr `lastResort` handler. Tested on the unhealthy path.
- `engine/permissions.py`: `full_disk_access()` probes an FDA-only canary
  (never a watched folder). The menu bar app offers Full Disk Access - alert
  with *Open System Settings* / *Later* - only when a watched folder was
  denied and FDA is absent, then restarts the watcher on its own once the
  folder becomes readable. Dropbox-only users never see it. Folder-service
  grants persist per bundle; FDA is for the container/App Data case where
  ad-hoc-signed apps are re-prompted every launch (verified in S2M).
- Menu bar: every rumps callback catches everything and reports in an alert
  (an uncaught exception in a callback dies silently); the initial start runs
  from a one-shot timer inside the event loop so its alerts can appear;
  `bring_to_front()` precedes anything that may prompt; explicit status text
  when nothing is configured or another process holds the lock; About text
  laid out for NSAlert's narrow column and mentions the FDA remedy.
- `--install-app` verifies the interpreter it is about to record can import
  `duplicate_preventer`, `watchdog`, `rich` (and `rumps` on macOS) and refuses
  otherwise. Through a generic `python3` stub the recorded interpreter is
  whatever is active; a bundle pinned to the system Python dies at login with
  nobody watching.
- Quarantine root gets `_ABOUT_THIS_FOLDER.txt` (what the folder is, how to
  restore, safe to delete), written once, excluded from listings and cleanup.
- US spelling throughout the source.

Not adopted (domain-specific or not needed here): multi-output config, the
single-worker pending-set watcher (DFP settles per file on the dispatch
thread, which is adequate at attachment volumes), machine-id stamping,
`--purge`, self-signing.

## New flags

`--once`, `--follow-log`, `--lines N`, `--install-command [--dir DIR]`,
`--uninstall-command`, `--install-app [--dir DIR]`, `--uninstall-app`,
`--menubar`, `--version`.

## Known gaps / next

- `frontends/menubar.py` and `--install-app` run on macOS (Phases 2 and 3 done
  2026-08-30). Not yet tried: launching the bundle as a Login Item and the TCC
  re-prompt for protected folders.
- Watching only handles `on_created`. Apps that write to a temp name and
  rename into place would need `on_moved` (dest) as well; not needed for
  FiltaQuilla as far as is known.
- Double-extension names (`file-1.tar.gz`) are not detected, as before;
  `tests/test_utils.py` pins that so a change is visible.
