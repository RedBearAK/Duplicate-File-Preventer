# Handoff: lessons from building Stickies-to-Markdown, for Duplicate-File-Preventer

**Direction:** Stickies-to-Markdown (S2M) → DFP. The earlier handoff went the
other way: DFP's engine/front-end split, bundle recipe and menu bar pattern
were the starting point for S2M. S2M is now in daily use on a Mac (watcher,
menu bar app, Login Item, multiple outputs). This document collects what
was learned *after* that handoff - things that were wrong or missing in it,
and patterns DFP should take back. Everything marked **verified** was
observed on a real Mac (dev_notes/MAC_FINDINGS.md in the S2M repo has the
logs); the rest is design that held up under S2M's test suite.

S2M repo: `Stickies-to-Markdown` (dev_beta). Files referenced below are
under `src/stickies_to_markdown/` unless a path is given.

---

## 1. TCC: the finding that matters most

The DFP handoff's §3.1 was right about attribution (compiled launcher,
stable bundle identifier, ad-hoc signature) and wrong about one thing: it
assumed a stable identity was *sufficient* for grants to persist. It is
sufficient for the folder services (Desktop, Documents, Downloads, and by
extension Dropbox folders under them). It is **not** sufficient for the
newer **App Data** service (`kTCCServiceSystemPolicyAppData`, the "would
like to access data from other apps" prompt) that guards
`~/Library/Containers/<other app>/`.

**Verified, from the TCC log:**

- Attribution worked exactly as designed: `responsible = com.redbearak.stickies-to-markdown`
  (the launcher), `accessing = org.python.python`. The grant was created
  with `identifier_type=Bundle ID`.
- Every launch logged `Session scoped auth is invalid for client` followed
  by a fresh `type=Create`. tccd stores the App Data grant **session-scoped**
  for an ad-hoc-signed app, so the prompt returns on every launch.
- `codesign -dr -` on an ad-hoc bundle shows `designated => cdhash H"..."`.
  Setting an explicit identifier-based requirement
  (`--requirements '=designated => identifier "..."'`) **did not change
  tccd's behavior.** It keys on the absence of a signing certificate, not
  on the requirement text.
- `~/Library/Application Support/com.apple.TCC/TCC.db` cannot be read even
  with `sudo` on current macOS. The unified log is the only view:
  `log show --last 10m --predicate 'subsystem == "com.apple.TCC"' | grep -E 'type=(Create|Delete)|Session scoped|AUTHREQ_PROMPTING'`

**Resolution (verified): Full Disk Access.** FDA
(`kTCCServiceSystemPolicyAllFiles`) persists by Bundle ID for any signature
and is a superset of App Data. Once granted, the per-launch prompt never
fires. The app now does what mature Mac apps do:

1. Before touching the protected location, probe an **FDA-only canary**
   that is always present and behind no prompting service:
   `~/Library/Application Support/com.apple.TCC/TCC.db` (fallbacks:
   `~/Library/Safari/CloudTabs.db`, `~/Library/Mail`). `PermissionError`
   = no FDA; success = FDA; `FileNotFoundError` = try the next canary.
   The probe is silent because FDA has no prompt.
2. The refused probe is what makes tccd list the app in the Full Disk
   Access pane, **switched off**. That is how apps "already appear there".
3. Offer an alert: *Open System Settings* / *Later*. The first runs
   `open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"`.
4. Re-probe on the existing UI tick; when FDA appears, start the engine.
   "Later" starts anyway on the per-launch-prompt path.

Observed: dialog → pane → switch (Touch ID) → watching; second launch
silent. Code: `engine/stickies.py::full_disk_access()` (~30 lines),
`frontends/menubar.py` (`grant_fda`, `_recheck_fda`, the autostart timer).

**What DFP should do:** port it as a *reaction*, not an unconditional
check. DFP watches Dropbox folders, which persist fine; a Dropbox-only user
must never see an FDA dialog. DFP already has the right hook - the health
probe that lists each watched folder at start and turns the icon yellow on
`PermissionError`. Add: on that failure, if `full_disk_access()` is False,
run the alert → pane → poll → autostart sequence instead of only going
yellow. Carry the About-dialog line over too ("Asked for permission at
every launch? Grant the app Full Disk Access once").

Two nuances: the probe must run inside the bundle's process tree so tccd
attributes it to the app (the compiled launcher already guarantees this),
and it must use an FDA-only canary, never the watched path itself -
probing a container path *is* the App Data prompt you are avoiding.

The self-signed-certificate route (`--install-app --self-sign`, automated
with openssl + `security`) exists in S2M but is **untested**; FDA made it
unnecessary. Don't reach for it first.

## 2. Menu bar app: corrections to the DFP handoff's §3.3 and §5b

- **Never copy another app's icon files, even "generic" shapes.** S2M
  shipped DFP's status PNGs verbatim because the handoff called them
  generic glyphs; the menu bar then showed DFP's icon while Finder showed
  S2M's. Each app generates its own set from one script
  (`frontends/icons/make_icons.py`, Pillow only: status PNGs + `@2x` +
  `AppIcon.icns`).
- **`Engine.start()` must be all-or-nothing.** A start that raised after
  logging its banner left the lock held and `monitoring=False`; the menu
  said "stopped" while the process owned the lock, and a second click hit
  the same wall with no log line explaining why. Now every phase logs
  ("Probing the container (a permission prompt may appear)...",
  "Container: readable", "Watcher running") and any exception unwinds -
  lock released, observer stopped, state reset - then re-raises as
  `EngineError`. DFP's `Engine.start()` has the same shape and the same
  hole.
- **rumps callbacks must catch everything.** An uncaught exception in a
  menu callback dies silently (traceback to the launcher log, UI unchanged).
  Wrap `engine.start()` in the toggle with `except Exception` and show it
  in an alert.
- **Alerts need the event loop.** An alert issued before `app.run()` never
  appears. Autostart logic runs from a one-shot `rumps.Timer(0.2)` that
  stops itself.
- **`bring_to_front()` before anything that may prompt**, including TCC
  prompts, not just `rumps.alert`.
- **The status line must say something when there is nothing to do.** A
  grey icon with "stopped" when no folder is configured looks broken.
  S2M: "No mirror folder - set it in the terminal", and choosing Start
  opens the narrow About-style alert with the two lines to type. Keep it
  short; the status item is not wide.
- **The About text is written for NSAlert's narrow column** (already in
  `MENUBAR_UI_NOTES.md`: label above, command below, no inline comments,
  ~35 chars/line). Verified fine on the Mac.

## 3. Watcher patterns worth back-porting

Even though DFP's domain is different, these came out of S2M's watcher and
apply to any watchdog-based tool:

- **Single worker draining a per-key pending set.** Watchdog events only
  record "key X touched at time T" (observer thread). One worker thread
  picks keys whose last touch is older than the debounce, oldest first.
  Bursts (16 creations in 3 s, verified) drain in order; nothing runs
  concurrently. `engine/monitor.py`.
- **Retries for "exists but not ready".** A path that exists but isn't yet
  in its final shape (S2M: a newborn note is a flat *file* before it
  becomes a package directory) is re-queued up to N times, then dropped
  with a warning. Generic: don't treat an unfinished write as deletion.
- **Deletion is the *container* vanishing, not a file inside it** - a file
  can be absent mid-save (observed 0.5 s gap between delete and create of
  the same path under a temp-and-rename writer).
- **Count "unchanged", don't suppress it.** Attribute-only rewrites produce
  events that end as no-ops; a status line showing "3 converted, 41
  unchanged" proves the watcher is alive, which matters because a denied
  permission also looks like silence.
- **Writers read config live.** S2M's writer caches nothing from config
  (`dry_run`, `read_only` are properties), so hot reload applies to the
  next item without rebuilding. DFP's processor may cache; check.
- **Volatile fields excluded from the rewrite comparison.** Any
  `modified`-style field derived from the source's mtime churns on every
  attribute fiddle by the source app. S2M compares everything except
  `synced-at` and `modified`; `created` is sticky once recorded (the source
  file is *replaced* on every save, so its birth time is meaningless).
- **Health = probe, not assumption** was already in the handoff and held:
  a denied permission delivers zero watchdog events forever with no error.

## 4. Configuration: the two-level model

S2M's config grew a second level and DFP may want the same shape if it ever
watches folders with different policies:

```json
{ "global keys ...": "...",
  "outputs": [ {"name": "vault", "output_dir": "...", "flavor": "...", "on_delete": "..."},
               {"name": "plain", ...} ] }
```

- Global keys govern reading/watching/logging; each block has its own
  folder-level policies. One conversion fans out to one `Writer` per block,
  each with its own index and policies.
- A pre-multi-output flat file is **migrated** on first load into a single
  block named `default`, written back once. Nobody edits their config by
  hand to upgrade.
- CLI: `--set KEY=VALUE` (global), `--set NAME.KEY=VALUE` (one block),
  bare per-block keys apply to the first block (legacy), `--add-output
  NAME=PATH`, `--remove-output NAME`. `--set` coerces types from the
  defaults (`a,b` → list).
- TUI: globals on the Settings screen, blocks listed beneath as `A`, `B`, …
  with `+`/`-`, a per-block screen behind each letter.
- **Default subfolder.** An output points at a *folder*; the tool creates
  `Synced_from_Stickies/` inside it (no double nesting if the folder already
  is that name; blank = write directly). This exists because the first
  real run spilled ten files into an Obsidian vault root. DFP's analog is
  the quarantine folder; the lesson is "never write into a folder the user
  named, write into a folder you own inside it, unless told otherwise".
- **`--purge-mirror DIR [--yes]`**: remove only files carrying the tool's
  marker, list first, delete with `--yes`. The cleanup after a folder
  mistake should be one command, not Finder work on chmod-444 files.

## 5. TUI lessons (rich)

- **`Prompt.ask(choices=...)` is case-sensitive.** Letter choices (`A`)
  must be validated by hand after `.upper()`, or `a` is rejected.
- **A screen must never block on a probe.** The first pandoc availability
  check (subprocess) took over a second cold; the Settings screen showed a
  header and nothing else. Probes are cached and non-blocking for UI
  (`pandoc_available(block=False)` returns None while a background thread
  works), or shown as an explicit "Checking..." line *before* the screen
  draws. DFP: anything that shells out from a screen (e.g. `xcode-select`)
  gets the same treatment.
- **A screen must never blank silently.** Settings is wrapped: any
  exception prints the error and the config path, then pauses.
- **Scripted TUI tests work** with a `rich.Console(file=StringIO)`, `ask`
  and `Confirm.ask` replaced by an iterator over key strings, and `pause`
  replaced by a `wait_for(...)` when the scripted sequence would outrun a
  background worker. `tests/test_tui.py`.

## 6. Diagnostics: the observation-session script

`dev_notes/mac_verify.py` paid for itself many times over. The generally
reusable parts:

- **Annotated open-ended watch.** Poll the tree every 200 ms recording
  `(inode, size, mtime, is_dir)` per path; print CREATED/DELETED/CHANGED/
  BECAME DIR lines; **inode tracking distinguishes in-place rewrite from
  temp-and-rename** automatically; `select()` on stdin lets the user type
  narration that is stamped into the same log; a `+Ns` column gives time
  since the last note. This settled Stickies' autosave timing in one
  five-minute session after five 25-second windows had failed.
- **Steps are independent and crash-isolated**, selectable with
  `--steps 3,4`, each writing `FINDING:` lines to a timestamped log plus a
  summary. Re-runs after code changes are cheap.
- **Findings go into a `MAC_FINDINGS.md` as facts** replacing the
  handoff's assumptions, with an explicit "Still open" list. Several
  assumptions in the S2M handoff (§4 storage, §5.3 conversion) were wrong
  and were caught this way before code depended on them.

DFP could use the same script shape to characterize Dropbox/Finder rename
and duplicate patterns rather than inferring them.

## 7. Build/deploy environment lessons

- **The tech_bin stub replaces `--install-command`.** In the
  `tech_bin_stubs/` paradigm the stub *is* the command; running the tool's
  own installer would put a second `stickies2md` in `~/.local/bin` with
  PATH order deciding. The stub header says so.
- **`--install-app` records `sys.executable`.** Through a stub that is
  whatever `python3` resolves to; run it with the shared venv active or the
  bundle pins the system Python and dies on `import watchdog` at login,
  with nobody watching. DFP's `--install-app` has the identical hazard.
- **Signing identity is remembered** in `Info.plist` (`S2MCodesignIdentity`)
  so re-installs after venv rebuilds reuse it. Bundle identifier is a
  one-way door (TCC); it never changes.
- **Machine identity for per-machine behavior:** 8 hex of SHA-256 over
  macOS `IOPlatformUUID` (`ioreg -rd1 -c IOPlatformExpertDevice`) or Linux
  `/etc/machine-id` - stable across renames and OS reinstalls - with the
  hostname kept as the human label only. S2M writes both into every file
  and isolates two Macs' files in a shared folder by the id. Same idea as
  Toshy's machine-specific config sections.
- **Archives:** in-place `./`-prefixed `.tgz` that extracts over the repo
  root; exclude `.git`, `__pycache__`, `.pytest_cache`, `build/`,
  `*.egg-info` explicitly (all three of the latter leaked at least once).
- **Style:** US spelling throughout (a sweep found 90 `colour`/`grey`
  instances); every path and file name in log lines, TUI screens and CLI
  output is single-quoted.

## 8. Test-suite patterns

- **Engine isolation test** (already in DFP): extended to grep for any UI
  library and to run the engine in a subprocess asserting empty
  stdout/stderr on *both* the healthy and the unhealthy path. An
  unconfigured logger leaks warnings via `lastResort`; a `NullHandler` at
  import time in `logsetup.py` closes that hole - DFP should check it has
  one.
- **Bundle tests run the compiled C launcher on Linux**: PYTHONPATH, spawn,
  log redirection, exit-code propagation and SIGTERM forwarding are all
  verified there; only codesign and Login Items need a Mac.
- **A `notes_in(folder)` helper** that excludes tool-maintained files (S2M's
  `_About` readme) from "how many items are there" assertions; every
  direct `glob("*.md")` in tests was a latent failure once the tool started
  writing its own note.
- **One flake in 90 tests**: a debounce-window race in the watcher suite
  that failed once under the full runner and never again in a dozen
  reruns. Left strict, noted.

## 9. Things S2M did that DFP probably should not copy

- The multi-tier converter with pandoc/markdownify is domain-specific.
- The Obsidian flavor machinery (`cssclasses`, snippet installation into
  `.obsidian/snippets/` + `appearance.json`, plugin-vocabulary emitters
  read from plugin source) is domain-specific.
- The `_About` self-describing note is worth *considering* for DFP's
  quarantine folder ("why is this file here, what put it here, is it safe
  to delete") - same maintained-marker-file mechanics - but it is optional.

## 10. Open items in S2M, for context

- `--self-sign` untested (FDA made it moot).
- The one watcher-suite flake.
- Live-typing autosave interval measured from one run (~10-12 s idle
  debounce); the mirror promises "15-20 s" on that basis.

# End of file #
