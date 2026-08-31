"""
macOS menu bar front end (rumps). Phase 2 of the handoff.

STATUS: runs on macOS from Terminal (Phase 2). The threading contract is the
part that must hold: rumps is touched only from timer/menu callbacks (main
thread); the engine's watchdog thread only ever touches the queue.

Deliberately minimal: status line, Start/Stop, About/Help, Quit. Settings
and logs live in the terminal (run `duplicate-file-preventer` / `--follow-log`).
"""

import os
import signal
import subprocess

import rumps    # ImportError here is caught by __main__ with a helpful message

from rumps.rumps import NSApplication    # rumps already imported AppKit; reuse it

from duplicate_preventer.engine import Config, Engine, EngineError
from duplicate_preventer.engine.permissions import full_disk_access, SETTINGS_URL_FULL_DISK_ACCESS


# Full-color icons rather than macOS "template" images, so the status corner
# can carry color: green dot = watching, red square = stopped, yellow warning
# triangle = problem. The sheets are a mid gray that reads on both light and
# dark menu bars. AppKit picks the @2x file on Retina by naming convention.
ICON_DIR = os.path.join(os.path.dirname(__file__), "icons")
ICON_WATCHING = os.path.join(ICON_DIR, "watching.png")
ICON_STOPPED = os.path.join(ICON_DIR, "stopped.png")
ICON_PROBLEM = os.path.join(ICON_DIR, "problem.png")

TICK_SECONDS = 0.5


# NSApplicationActivationPolicyAccessory: no Dock tile, no main menu bar.
# The same thing LSUIElement=true does for a bundle, but set from code so
# it also applies when launched from Terminal.
ACTIVATION_POLICY_ACCESSORY = 1


def become_accessory_app():
    NSApplication.sharedApplication().setActivationPolicy_(ACTIVATION_POLICY_ACCESSORY)


def bring_to_front():
    """
    A process launched from Terminal is not the frontmost app, so an NSAlert
    opens behind everything and the Dock icon bounces instead. Activating
    first puts the dialog where the user is looking. With the accessory
    policy this raises the dialog without taking over the menu bar.
    """
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)


class DupePreventerApp(rumps.App):

    def __init__(self, engine, autostart=True):
        super().__init__("DupePrev", icon=ICON_STOPPED, quit_button=None)
        self.engine = engine
        self.status_item = rumps.MenuItem("Status: not monitoring")
        self.status_item.set_callback(None)
        self.toggle_item = rumps.MenuItem("Start monitoring", callback=self.toggle)
        self.about_item = rumps.MenuItem("About / Help", callback=self.show_about)
        self.quit_item = rumps.MenuItem("Quit", callback=self.quit)
        self.menu = [self.status_item, None, self.toggle_item, None,
                     self.about_item, self.quit_item]
        self._fda_offered = False
        self._tick_timer = rumps.Timer(self._tick, TICK_SECONDS)
        self._tick_timer.start()
        # Anything that may show an alert must run inside the event loop, so
        # the initial start is deferred to a one-shot timer rather than done
        # before app.run().
        self._autostart_timer = None
        if autostart:
            self._autostart_timer = rumps.Timer(self._autostart, 0.2)
            self._autostart_timer.start()

    # --- alerts (all main-thread; rumps callbacks only) ---------------------

    def show_about(self, _):
        bring_to_front()
        rumps.alert(
            title="Duplicate File Preventer",
            message=("This menu controls Start/Stop only.\n\n"
                     "Settings and logs live in the terminal.\n\n"
                     "To change settings, run:\n"
                     "    duplicate-file-preventer\n\n"
                     "To watch the log live:\n"
                     "    duplicate-file-preventer --follow-log\n\n"
                     "Changes made there apply live.\n\n"
                     "Asked for folder permission at every\n"
                     "launch? Grant the app Full Disk Access\n"
                     "once, in System Settings > Privacy &\n"
                     "Security."))

    def show_unconfigured(self):
        bring_to_front()
        rumps.alert(
            title="No folders to watch",
            message=("Add folders in the terminal:\n\n"
                     "    duplicate-file-preventer\n\n"
                     "then choose Start monitoring here.\n"
                     "The app picks up the change live."))

    def offer_full_disk_access(self):
        """
        Reached only when a watched folder was denied AND the app lacks
        Full Disk Access. FDA persists per bundle for any signature, so
        one grant ends the per-launch prompting.
        """
        if self._fda_offered:
            return
        self._fda_offered = True
        bring_to_front()
        choice = rumps.alert(
            title="Folder access was denied",
            message=("macOS refused access to a watched\n"
                     "folder. The reliable fix is to grant\n"
                     "this app Full Disk Access once:\n\n"
                     "System Settings > Privacy & Security\n"
                     "> Full Disk Access > switch on\n"
                     "'Duplicate File Preventer'.\n\n"
                     "Monitoring starts by itself once the\n"
                     "switch is on."),
            ok="Open System Settings", cancel="Later")
        if choice == 1:
            subprocess.Popen(["open", SETTINGS_URL_FULL_DISK_ACCESS])

    # --- actions -------------------------------------------------------------

    def _try_start(self):
        """Start the engine; report any failure in an alert. Returns True on success."""
        if not self.engine.config.get("watched_folders"):
            self.show_unconfigured()
            return False
        try:
            bring_to_front()            # a folder permission prompt may follow
            self.engine.start()
        except EngineError as error:
            bring_to_front()
            rumps.alert(title="Cannot start", message=str(error))
            return False
        except Exception as error:        # a rumps callback that raises dies silently
            self.engine.logger.error(f"Unexpected start failure: {error!r}")
            bring_to_front()
            rumps.alert(title="Cannot start", message=f"{type(error).__name__}: {error}")
            return False
        return True

    def _autostart(self, timer):
        timer.stop()
        try:
            if self.engine.config.get("watched_folders"):
                self._try_start()
            self._after_start_checks()
        except Exception as error:
            self.engine.logger.error(f"Autostart failure: {error!r}")
        self._refresh()

    def toggle(self, _):
        try:
            if self.engine.status().monitoring:
                self.engine.stop()
            elif self._try_start():
                self._after_start_checks()
        except Exception as error:
            self.engine.logger.error(f"Toggle failure: {error!r}")
            bring_to_front()
            rumps.alert(title="Duplicate File Preventer", message=f"{type(error).__name__}: {error}")
        self._refresh()

    def _after_start_checks(self):
        st = self.engine.status()
        if st.denied_folders and full_disk_access() is False:
            self.offer_full_disk_access()

    def quit(self, _=None):
        self._tick_timer.stop()
        try:
            self.engine.stop()
        finally:
            rumps.quit_application()

    # --- periodic ------------------------------------------------------------

    def _tick(self, _timer):
        try:
            self.engine.events.drain()          # the log has the details
            self.engine.reload_config_if_changed()
            self._recheck_denied()
            self._refresh()
        except Exception as error:
            self.engine.logger.error(f"Tick failure: {error!r}")

    def _recheck_denied(self):
        """Once FDA is granted (or the folder grant flips on), restart so the
        denied folders join the watch without user action."""
        st = self.engine.status()
        if not st.monitoring or not st.denied_folders:
            return
        for folder in st.denied_folders:
            try:
                os.listdir(folder)
            except OSError:
                return
        self.engine.logger.info("Denied folder now readable; restarting watcher")
        self.engine.stop()
        self._try_start()

    def _refresh(self):
        st = self.engine.status()
        configured = bool(self.engine.config.get("watched_folders"))
        if not st.healthy:
            self.icon = ICON_PROBLEM
            self.status_item.title = f"Problem: {st.last_error or 'see log'}"
        elif st.monitoring:
            self.icon = ICON_WATCHING
            count = len(st.watched_folders)
            self.status_item.title = (
                f"Watching {count} folder{'s' if count != 1 else ''} - "
                f"{st.quarantined_session} quarantined this session"
                + (f", {len(st.denied_folders)} denied" if st.denied_folders else "")
                + (" (dry run)" if st.dry_run else ""))
        elif not configured:
            self.icon = ICON_STOPPED
            self.status_item.title = "No folders configured - add them in the terminal"
        elif st.lock_holder_pid:
            self.icon = ICON_STOPPED
            self.status_item.title = f"Monitoring runs in another process (PID {st.lock_holder_pid})"
        else:
            self.icon = ICON_STOPPED
            self.status_item.title = "Status: not monitoring"
        self.toggle_item.title = "Stop monitoring" if st.monitoring else "Start monitoring"


def run_menubar(config=None):
    engine = Engine(config or Config())
    become_accessory_app()
    app = DupePreventerApp(engine)

    def on_signal(_signum, _frame):
        app.quit()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        app.run()
    finally:
        engine.stop()
    return 0


# End of file #
