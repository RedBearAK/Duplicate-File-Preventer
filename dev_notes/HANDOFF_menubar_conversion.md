# Handoff: Duplicate-File-Preventer → optional macOS menu bar app

**Goal:** Allow Duplicate-File-Preventer to run as a persistent macOS menu bar app (via `rumps`),
while fully preserving its current cross-platform terminal operation (macOS + Linux; Windows
should keep working to the extent it does today, since `watchdog` supports all three).
The menu bar mode is an *optional front end*, never a requirement.

**Division of labor (firm):** the **terminal owns settings and logs**; the **menu bar owns
almost nothing** — Start/Stop and a visible sign that the watcher is alive and healthy.
No new GUI windows or dialogs, and the menu does not attempt to launch Terminal windows;
when the user wants to change settings or read the log, they open a terminal themselves
and run the existing TUI. This keeps the macOS-specific surface tiny and everything of
substance portable.

This document assumes the repo layout as of the current `main`:

```
duplicate_preventer/
    __init__.py
    __main__.py          # entry point -> duplicate_monitor.main()
    _version.py
    config.py            # Config: load/save (auto-save on every set())
    duplicate_handler.py # watchdog FileSystemEventHandler + quarantine + logging setup
    duplicate_monitor.py # DuplicateMonitor: rich menu, CLI flags (--start, --dry-run, --show-log)
    utils.py
pyproject.toml           # console scripts: duplicate-file-preventer, duplicate-monitor (pre-refactor)
requirements.txt         # watchdog, rich
```

Note `check_interval` is stored and editable in the settings screen but never read by any
code path. During Phase 0 either give it a job (the drain/reload cadence in §2b) or
remove it from the settings screen; don't carry a dead setting through the refactor.

Config lives in platform-standard dirs (`~/Library/Application Support/DuplicateMonitor/` on
macOS, `~/.config/duplicate-monitor/` on Linux, `%APPDATA%\DuplicateMonitor\` on Windows).
Keep all of that unchanged.

---

## 1. Target architecture

Three thin front ends over one UI-free engine. The engine must import **no** UI or
platform-GUI modules (no `rich`, no `rumps`, no AppKit) so it stays testable and portable.

```
duplicate_preventer/
    engine/
        __init__.py
        monitor.py        # watchdog observer lifecycle: start/stop/pause + liveness
        scanner.py        # one-shot duplicate scan of existing files
        rules.py          # duplicate detection: name pattern, size, ctime, optional hash
        quarantine.py     # move-aside logic (never delete)
        config.py         # load/save auto-saving config + hot-reload support (new)
        events.py         # event + status dataclasses, thread-safe event queue
        lock.py           # cross-platform "monitoring lock" (new, see §4)
        logsetup.py       # file logging (existing behavior)
    frontends/
        __init__.py
        cli.py            # flag-driven, non-interactive: --start, --once, --dry-run, --show-log
        tui.py            # the existing rich interactive menu = the settings & log UI
        menubar.py        # macOS-only rumps front end (guarded import), Start/Stop + health only
    __main__.py           # dispatch: default = tui, --menubar = menubar, flags = cli
```

**Rule of thumb for the split:** if a function decides *what* to do with a file, it lives in
`engine/`. If it decides *how to show* something to a human, it lives in `frontends/`.
The Phase 0 refactor should produce zero behavior change — same menu, same flags,
same config files, same log lines.

### Engine public surface (keep it this small)

```python
# engine/events.py
from dataclasses import dataclass, field
import queue, time

@dataclass(frozen=True)
class Event:
    kind: str          # "quarantined" | "scanned" | "started" | "stopped"
                       # | "config_reloaded" | "error"
    path: str = ""
    detail: str = ""
    ts: float = field(default_factory=time.time)

@dataclass
class Status:
    monitoring: bool
    healthy: bool               # observers alive, config parseable, no unrecovered error
    last_error: str | None
    watched_folders: list[str]
    quarantined_session: int
    quarantined_total: int
    last_event_ts: float | None
    dry_run: bool

# engine/monitor.py
class Engine:
    def __init__(self, config: Config): ...
    def start(self) -> None: ...          # spawn watchdog observer(s); takes monitoring lock
    def stop(self) -> None: ...           # releases monitoring lock
    def scan_once(self) -> int: ...       # one-shot cleanup, returns count handled
    def status(self) -> Status: ...       # cheap snapshot, safe from any thread
    def reload_config_if_changed(self) -> bool: ...   # see §3
    events: "queue.Queue[Event]"          # engine PUTS, front ends GET; engine never blocks on it
```

The `events` queue is the only channel from engine to UI. The engine must `put_nowait()`
and tolerate a full/undrained queue (bounded queue, drop oldest, count drops in `Status`)
so a stalled front end can never stall quarantining.

**Health is computed, not assumed.** `status().healthy` must actually check
`observer.is_alive()` on the watchdog thread(s) and whether the last config reload
parsed. A menu bar icon that says "running" while the observer thread died is worse
than no icon; this field is the whole reason the menu bar mode exists.

---

## 2. The threading rule (the one that causes crashes if ignored)

`watchdog` delivers filesystem events on its **observer thread**. AppKit (and therefore
rumps UI state — titles, menu items, notifications) must only be touched from the
**main thread**, where rumps runs the NSApplication event loop.

Never call rumps from a watchdog handler. Instead, drain the engine's event queue from a
`rumps.Timer`, whose callbacks run on the main thread:

```python
# frontends/menubar.py — deliberately minimal: status, Start/Stop, Quit.
import queue, threading
import rumps
from duplicate_preventer.engine import Engine, Event

GLYPH_RUNNING = "🟢"   # or template .icns images in the bundle phase
GLYPH_PAUSED  = "⏸"
GLYPH_ERROR   = "⚠️"

class DupePreventerApp(rumps.App):
    def __init__(self, engine: Engine):
        super().__init__("DupePrev", title=GLYPH_PAUSED)
        self.engine = engine
        self.status_item = rumps.MenuItem("Status: not monitoring")
        self.status_item.set_callback(None)           # display-only, not clickable
        self.toggle_item = rumps.MenuItem("Start monitoring", callback=self.toggle)
        self.about_item = rumps.MenuItem("About / Help", callback=self.show_about)
        self.menu = [self.status_item, None, self.toggle_item, None, self.about_item]
        # rumps supplies the Quit item automatically.
        self._drain = rumps.Timer(self._tick, 0.5)
        self._drain.start()

    def show_about(self, _):
        # rumps.alert wraps NSAlert — native dialog, no extra frameworks.
        # Plain text only (no clickable links), modal, main-thread-only:
        # safe from menu callbacks, never from watchdog threads.
        rumps.alert(
            title="Duplicate File Preventer",
            message=("This menu controls Start/Stop only.\n\n"
                     "Settings and logs live in the terminal:\n\n"
                     "    duplicate-file-preventer            # settings menu\n"
                     "    duplicate-file-preventer --follow-log\n\n"
                     "Changes made there apply live — no restart needed."))

    def toggle(self, _):
        if self.engine.status().monitoring:
            self.engine.stop()
        else:
            self.engine.start()
        self._refresh()

    def _tick(self, _timer):
        try:
            while True:
                self.engine.events.get_nowait()   # nothing rendered per-event; log has details
        except queue.Empty:
            pass
        self.engine.reload_config_if_changed()    # settings edited in the terminal apply live
        self._refresh()

    def _refresh(self):
        st = self.engine.status()
        if not st.healthy:
            self.title = GLYPH_ERROR
            self.status_item.title = f"Problem: {st.last_error or 'see log'}"
        elif st.monitoring:
            self.title = GLYPH_RUNNING
            self.status_item.title = (
                f"Watching {len(st.watched_folders)} folders — "
                f"{st.quarantined_session} quarantined this session")
        else:
            self.title = GLYPH_PAUSED
            self.status_item.title = "Status: not monitoring"
        self.toggle_item.title = ("Stop monitoring" if st.monitoring
                                  else "Start monitoring")
```

Notes:

- All UI mutation happens inside timer/menu callbacks (main thread). The watchdog side
  only ever touches the queue and engine internals.
- Per-event notifications are deliberately **omitted**. The log file is the record;
  the status line carries the session count. (Notifications from an unbundled Python
  are unreliable anyway; if wanted later, add them in Phase 4 with throttling.)
- The drain timer doubles as the config-reload heartbeat (§3) and the health poll.
  Keep `status()` and `reload_config_if_changed()` cheap: a few stat() calls and
  int reads under a lock, never a filesystem walk.
- If a long `scan_once()` action is ever added to the menu, run it on a
  `threading.Thread(daemon=True)` and let the outcome land in the log; never block
  a menu callback.

---

## 2b. Output discipline: the TUI must stop being scrolled away by the watcher

### The current defect

Today the interactive menu is unusable while monitoring is active with any real traffic.
`DuplicateHandler` runs on the watchdog observer thread and calls `console.print(...)`
directly ("Checking potential duplicate…", "✓ Moved duplicate…", permission errors).
`Config.set()` prints "Configuration saved" on every save, and `setup_logging()` attaches a
`StreamHandler` to the console when the log level is DEBUG. All of it lands on stdout
while the main thread is parked in `Prompt.ask()`, so the menu is scrolled off screen by
another thread's writes. Pressing Enter "fixes" it only because the menu loop happens to
`console.clear()` and redraw. To a user who doesn't know that, the program looks broken.

The engine extraction in §1 removes the cause; this section makes the rule explicit so it
survives Phase 0 and so the TUI gets a deliberate replacement rather than silence.

### The rule

1. **Nothing in `engine/` writes to stdout or stderr.** Ever. Not `print`, not `rich`,
   not a logging `StreamHandler`. The engine's two outputs are the file log and the
   `events` queue. Every `console.print` currently in `duplicate_handler.py` and
   `config.py` becomes a log line (most already have one) and/or an `Event`. The DEBUG
   `StreamHandler` is deleted; DEBUG changes only what reaches the file.
2. **A front end writes to the terminal only from its own main/input thread.** The TUI
   and CLI *pull* from the queue when they choose to render; nothing is pushed at them.
   This is the same contract the menu bar front end lives under (§2), applied to text.
3. **`Config.set()` is silent.** The TUI prints its own "auto-saved" note if it wants one.

The acceptance test is mechanical: run `--start` against a tmpdir with stdout/stderr
captured, plant duplicates, and assert the capture contains only what `cli.py` itself
chose to emit (and is empty when the engine is driven with no front end at all).

### What the TUI shows instead — three explicit, user-chosen views

**Level 1 — the main menu status line, while monitoring.**
When the menu redraws (on Enter, as today) it drains the queue and shows a one-line
summary instead of a bare "Active":

```
Status: ● Active — watching 3 folders, 2 quarantined this session
        last: invoice-1.pdf → quarantine  14:02:11
```

Enter-to-refresh stays, but now it is a feature with visible effect, not a repair.

**Level 2 — a "Watch activity" menu option (the live screen).**
A `rich.live.Live` view: status header (from `Engine.status()`) plus a table of the last
~15 events, refreshed on a 0.5 s timer. The same tick calls
`engine.reload_config_if_changed()`, so this view is the terminal twin of the menu bar's
drain timer. Exit with Ctrl‑C, caught and returned to the menu. "You're in the live screen;
Ctrl‑C leaves it" is a model users already have; interleaved output under a prompt is not.

If the monitoring lock (§4) says another process owns the watcher, this view degrades to
tailing the log file (same rendering, source is the file instead of the queue) so it is
still useful when the menu bar app is the one monitoring.

**Level 3 — `--start` and `--follow-log` in `cli.py` (plain streaming).**
No menu to protect, so these emit one line per event to stdout, color optional. This is
the mode for a scrolling terminal pane, for piping, and for a launchd/systemd unit on
Linux. `--start` should also render Level‑1‑style summaries on a slow cadence (e.g. every
few minutes when idle) so a long-running pane proves it is alive.

### Consequences for the refactor

- `tui.py` becomes just another consumer of `Engine.events` and `Engine.status()`; it has
  no privileged channel. Whether the engine lives in the TUI's own process or in the menu
  bar process changes only where Level 2 reads from.
- The existing `--show-log` (`tail -n 1000 -f`) is kept as-is; `--follow-log` is the
  in-Python equivalent so Windows gets it too, and so the TUI's Level 2 fallback can reuse it.
- Rich remains a `frontends/` dependency only. The engine's smoke tests import neither
  `rich` nor `rumps`.

---

## 3. Settings and logs stay in the terminal — including while the menu bar app runs

The existing rich TUI is the settings editor and log viewer, full stop. The new
requirement this creates: **config changes made in a terminal must reach an engine
that is already running inside the menu bar process.** Two mechanisms, both cheap:

1. **Atomic config writes (TUI/engine side).** Every config save writes to a temp file
   in the same directory and `os.replace()`s it over the real one. This is required
   once two processes touch the file: it guarantees the reader never sees a
   half-written config. Apply it to the existing auto-save path in `config.py`.

2. **Hot reload (engine side).** `reload_config_if_changed()` stats the config file,
   compares mtime+size to the last load, and on change: re-parse; if the watch list
   changed, restart the observers; emit `Event("config_reloaded")`; on parse failure,
   keep the old config, set `healthy=False`/`last_error`, emit `Event("error")`.
   The menu bar's drain timer calls this every 0.5 s; the standalone `--start` CLI
   mode should call it on the same cadence from its own loop so terminal-launched
   monitoring gets the same live-settings behavior.

Log viewing needs no new work: `--show-log` already exists; optionally add
`--follow-log` (tail -f behavior) since with the menu bar running, the terminal
becomes the *only* place details appear. Worth the ~15 lines.

---

## 4. One monitor at a time: the monitoring lock

With settings editable from a second process, prevent the accident of two engines
watching the same folders (double quarantine attempts, races on the same file):

- `engine/lock.py`: a pidfile in the config dir locked with `fcntl.flock` (POSIX) /
  `msvcrt.locking` (Windows). Taken by `Engine.start()` — regardless of which front
  end started it — released by `stop()` and on process exit.
- If the lock is held: `--menubar` and `--start` exit with a clear message naming the
  holder PID; the TUI still opens normally but disables/labels its own
  "start monitoring" action ("monitoring is running in another process") while all
  settings editing and log viewing remain available.

This is the piece that makes "menu bar runs the watcher, terminal edits the settings"
safe rather than merely convenient.

---

## 5. Optional dependency and dispatch

rumps (and its PyObjC dependency) must not be installed or imported on Linux/Windows.

`pyproject.toml`:

```toml
[project.optional-dependencies]
menubar = ["rumps>=0.4 ; sys_platform == 'darwin'"]

[project.scripts]
duplicate-file-preventer = "duplicate_preventer.__main__:main"
```

One command name, matching the tool. The old `duplicate-monitor` alias was dropped
(too generic); anyone who had it aliased can re-point at `duplicate-file-preventer`.

`__main__.py` dispatch:

```python
import sys, platform

def main():
    argv = sys.argv[1:]
    if "--menubar" in argv:
        if platform.system() != "Darwin":
            sys.exit("--menubar is only available on macOS")
        try:
            from duplicate_preventer.frontends.menubar import run_menubar
        except ImportError:
            sys.exit("Menu bar mode needs the optional extra: pip install '.[menubar]'")
        return run_menubar()
    if any(a in argv for a in ("--start", "--once", "--dry-run",
                               "--show-log", "--follow-log")):
        from duplicate_preventer.frontends.cli import run_cli
        return run_cli(argv)
    from duplicate_preventer.frontends.tui import run_tui
    return run_tui()
```

- The guarded import keeps `import duplicate_preventer` clean everywhere.
- No `rich`/stdout UI in menu bar mode; the file log is the record.
- Environment note: rumps's own docs warn against classic `virtualenv` and recommend
  stdlib `venv` (or a py2app bundle). If the shared-home-venv setup is in play, make
  sure it is a stdlib `venv`.

---

## 5b. Getting the terminal command onto PATH (for someone who just cloned the repo)

Today the command is put on PATH by hand (a symlink into a personal bin dir pointing at a
stub script). That is fine for the author and useless for anyone else. `[project.scripts]`
already exists, so the fix is mostly documentation plus one convenience flag.

### The supported paths, in order of preference

1. **pipx (recommended in the README).** Isolated venv, command on `~/.local/bin`,
   survives system-Python upgrades, and `pipx upgrade` / `pipx reinstall` re-create the
   launcher if it goes stale:
   ```
   pipx install 'git+https://github.com/RedBearAK/Duplicate-File-Preventer'
   pipx install '.[menubar]'     # from a checkout, macOS, with the optional extra
   ```
2. **pip into a venv you manage** (`python3 -m venv ~/.venvs/dfp && ~/.venvs/dfp/bin/pip
   install -e '.[menubar]'`). The launcher lives in the venv's `bin/`; the user adds that
   dir to PATH or symlinks the launcher. This is the setup the menu bar phase and the
   `.app` bundle assume anyway (the bundle execs the venv interpreter).
3. **No install at all:** `python3 -m duplicate_preventer` from the checkout, after
   `pip install -r requirements.txt`. Always works; the README should lead with it as
   the "just try it" line.

### `--install-command`: the tool maintains its own launcher

For case 2 (and for the tech-bin/stub habit), add a small self-installer so the
launcher is regenerated by the tool rather than by hand:

```
duplicate-file-preventer --install-command            # writes ~/.local/bin/duplicate-file-preventer
duplicate-file-preventer --install-command --dir DIR  # somewhere else on PATH
duplicate-file-preventer --uninstall-command
```

Behavior:

- Writes a **two-line POSIX shell stub** (`#!/bin/sh` + `exec "<sys.executable>" -m
  duplicate_preventer "$@"`) — a stub, not a symlink, so it records *which* interpreter
  and keeps working when the checkout moves or the venv's `bin/` isn't on PATH.
  On Windows write a `.cmd` with the same `exec` line.
- Idempotent: rewrites the stub if the recorded interpreter differs from the current one,
  which is the "maintain availability" part — re-run it after rebuilding a venv and the
  command is fixed. It refuses to overwrite a file it did not write (check for a marker
  comment line) so it can't clobber a user's own script.
- Prints one line telling the user whether the target dir is on PATH, and the exact
  `export PATH=…` line to add if not. It does **not** edit shell rc files.
- Lives in `frontends/cli.py`; the stub itself is pure stdlib, no `rich`.

This does not replace pipx; it is the escape hatch for developers running from a
checkout, and it is what the Phase 3 `.app` launcher can reuse (same interpreter-resolution
logic, different wrapper).

### Related: what *not* to do

- Don't ship a `setup.sh` that edits `.zshrc`/`.bashrc`. Print the line; let the user paste it.
- Don't try to install into `/usr/local/bin` by default — permissions, Homebrew ownership,
  and SIP make that a support burden. `~/.local/bin` is the XDG-blessed default on Linux
  and works on macOS once it's on PATH.
- Don't hard-code the interpreter in the repo; always resolve `sys.executable` at
  install time.

---

## 6. macOS permissions (smaller problem here than it sounds)

TCC ("Transparency, Consent, and Control") is the macOS permission system; grants attach
to the **executed binary/bundle**, not to the script.

- Watching arbitrary user folders needs nothing special.
- Watching `~/Desktop`, `~/Documents`, or `~/Downloads` triggers per-folder prompts
  (or needs Full Disk Access) — and the grant lands on whatever is hosting Python:
  Terminal today, the `.app` bundle in Phase 3. Expect to re-approve after switching
  launch methods. A denied grant fails as `PermissionError` on listing, and watchdog
  may simply deliver nothing — both must surface as `healthy=False` + `last_error`
  so the menu icon flips to the warning glyph. Silent no-op is the worst failure mode.

---

## 7. Phased plan

**Phase 0 — extract the engine (no new features).**
Move logic into `engine/` per the layout above; `tui.py` becomes a caller of it.
Acceptance: existing interactive menu, `--start`, `--dry-run`, `--show-log` behave
identically on macOS and Linux; any existing tests pass; new smoke test drives
`Engine` + `scan_once()` against a tmpdir with no UI imported; **`engine/` contains no
`print`, no `rich` import, and no logging `StreamHandler`** (grep-able, and the smoke
test asserts captured stdout/stderr are empty). The one visible behavior change allowed:
the watcher no longer writes into the menu — the menu status line (§2b Level 1) is the
replacement. `--install-command` / `--uninstall-command` land here too (§5b), since
they only need `sys.executable` and a dispatcher.

**Phase 1 — events, status/health, atomic config writes, hot reload, lock.**
Add `events.py`, `lock.py`; make quarantine/scan/errors emit `Event`s; implement
`status()` with real observer-liveness checking and `reload_config_if_changed()`.
Acceptance: (a) a test drains the queue and sees a `quarantined` event when a
duplicate is planted while monitoring a tmpdir; (b) editing the config file while
`--start` is running changes the watched-folder set without a restart; (c) a second
`--start` refuses to run; (d) the TUI's "Watch activity" live view (§2b Level 2)
shows planted duplicates, exits on Ctrl‑C back to the menu, and falls back to log-tailing
when another process holds the lock; (e) `--follow-log` works on all three platforms
without `tail`.

**Phase 2 — `--menubar` front end, run from Terminal.** *Done 2026-08-30 on macOS.*
Icon set (overlapping sheets with a strike; green dot = watching, red square =
stopped, yellow warning triangle = problem) lives in `frontends/icons/`, full-colour rather than
template so the status corner can carry colour. Alerts call
`NSApp.activateIgnoringOtherApps_(True)` first; without it an unbundled Python
launched from Terminal shows the dialog behind everything and bounces the Dock
icon. The Dock rocket itself only goes away with the Phase 3 bundle (`LSUIElement`).
Implement `frontends/menubar.py` as in §2; install with `pip install -e '.[menubar]'`;
run `python -m duplicate_preventer --menubar` from a terminal. Acceptance: Start/Stop
from the menu works; planting a duplicate bumps the session count; editing settings
in the TUI from another terminal is picked up live; killing the observer thread (test
hook) flips the icon to the warning glyph within a second; Ctrl-C in the launching
terminal exits cleanly (`engine.stop()` in a `finally`).

**Phase 3 — optional `.app` bundle.** *Done 2026-08-30: `--install-app` in
`frontends/bundle.py`. Handcrafted, not py2app: `Contents/MacOS/launcher` is a
shell script that exports PYTHONPATH (the package dir) and execs the recorded
interpreter with `--menubar`, logging to `~/Library/Logs/DuplicateFilePreventer/`.
`Info.plist` sets `LSUIElement`; `AppIcon.icns` is generated from
`frontends/icons/app_icon.svg` via Pillow; ad-hoc `codesign` runs when available.
Idempotent, refreshes a changed interpreter, refuses foreign bundles. Login Items
is left to the user via System Settings (printed in the next steps).*
Original plan follows.
Only after Phase 2 is boringly stable. Either py2app (`LSUIElement: True` in the plist
so there is no Dock icon) or the handcrafted four-file bundle whose `MacOS/` launcher
execs the venv interpreter with `--menubar`. Ad-hoc `codesign -s - --force` for a
stable identity; drop in `~/Applications` for Spotlight; add to Login Items for
launch-at-login. Build this bundle **early in the phase** — py2app is historically the
fragile link across Python/macOS upgrades, so prove the build before polishing it.

**Phase 4 — niceties, all optional.** Pause-for-30-minutes timer; throttled summary
notifications. (Icons and `--follow-log` done.)

---

## 8. Pitfalls checklist

- [ ] No AppKit/rumps calls from watchdog threads — everything crosses via the queue.
- [ ] Engine never blocks putting events; queue is bounded; drops are counted in `Status`.
- [ ] `healthy` reflects real observer liveness, not "start() was called once".
- [ ] Config saves are atomic (`os.replace`), from every writer, before hot reload ships.
- [ ] Hot reload keeps the old config and flags unhealthy on parse failure — never dies.
- [ ] Monitoring lock is engine-level, so it protects `--start` and TUI-started
      monitoring too, not just menu bar mode; TUI stays usable for settings while locked.
- [ ] `engine/` never writes to stdout/stderr; front ends render only from their own
      main thread by pulling from the queue (§2b). The DEBUG console handler is gone.
- [ ] The TUI has a deliberate live view with an obvious exit (Ctrl‑C); nothing is ever
      printed underneath a waiting prompt.
- [ ] The one console-script name is `duplicate-file-preventer`; no aliases.
- [ ] README leads with `python3 -m duplicate_preventer` and `pipx install`; no rc-file editing
      anywhere; `--install-command` refuses to overwrite files it didn't write.
- [ ] No `rich`/stdout UI in menu bar mode; no attempts to spawn Terminal windows from
      the menu.
- [ ] Guarded rumps import; `--menubar` refused off-macOS with a clear message.
- [ ] stdlib `venv`, not virtualenv (rumps executable-copying caveat).
- [ ] TCC re-approval expected when moving from Terminal-hosted to bundle-hosted.
- [ ] `engine.stop()` runs on quit (rumps quit item, SIGINT, SIGTERM).
- [ ] Linux/Windows CI (or at least a manual run) after Phase 0 and Phase 2 to prove
      nothing darwin-only leaked into `engine/`, `cli.py`, or `tui.py`.

## 9. Explicit non-goals

- No settings or log UI in the menu bar, and no launching of Terminal windows from it.
  The menu is: status line, Start/Stop, About/Help, Quit.
- No macOS dialogs/windows beyond rumps' built-in `alert`, used once: the About/Help
  signpost that tells the user settings and logs live in the terminal. It informs;
  it never edits.
- No Linux tray icon now. The front-end seam makes a future `pystray`/AppIndicator
  front end a sibling of `menubar.py`, not a rewrite — that is enough.
- No IPC/daemon split. The menu bar app *is* the process that owns the watcher;
  the terminal talks to it only through the config file (hot reload) and reads the
  log file directly.

## 10. Relevance to Stickies-to-Markdown (why this is worth doing first)

This conversion is the low-risk rehearsal for the planned Stickies-to-Markdown menu
bar app: same rumps + watchdog + queue-drain pattern, same optional-extra packaging,
same terminal-owns-settings model, same bundle recipe — but without the Full Disk
Access/TCC identity puzzle, because this tool watches ordinary user folders.
Everything proven here (threading pattern, hot reload, monitoring lock, py2app build,
quit handling) transfers directly; the only new problem left for Stickies-to-Markdown
afterward is the protected-container grant.
