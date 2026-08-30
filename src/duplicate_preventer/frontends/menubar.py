"""
macOS menu bar front end (rumps). Phase 2 of the handoff.

STATUS: runs on macOS from Terminal (Phase 2). The threading contract is the
part that must hold: rumps is touched only from timer/menu callbacks (main
thread); the engine's watchdog thread only ever touches the queue.

Deliberately minimal: status line, Start/Stop, About/Help, Quit. Settings
and logs live in the terminal (run `duplicate-preventer` / `--follow-log`).
"""

import os
import signal

import rumps    # ImportError here is caught by __main__ with a helpful message

from rumps.rumps import NSApplication    # rumps already imported AppKit; reuse it

from duplicate_preventer.engine import Config, Engine, EngineError


# Full-colour icons rather than macOS "template" images, so the status corner
# can carry colour: green dot = watching, red square = stopped, yellow warning
# triangle = problem. The sheets are a mid grey that reads on both light and
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

    def __init__(self, engine):
        super().__init__("DupePrev", icon=ICON_STOPPED, quit_button=None)
        self.engine = engine
        self.status_item = rumps.MenuItem("Status: not monitoring")
        self.status_item.set_callback(None)
        self.toggle_item = rumps.MenuItem("Start monitoring", callback=self.toggle)
        self.about_item = rumps.MenuItem("About / Help", callback=self.show_about)
        self.quit_item = rumps.MenuItem("Quit", callback=self.quit)
        self.menu = [self.status_item, None, self.toggle_item, None,
                     self.about_item, self.quit_item]
        self._tick_timer = rumps.Timer(self._tick, TICK_SECONDS)
        self._tick_timer.start()

    def show_about(self, _):
        bring_to_front()
        rumps.alert(
            title="Duplicate File Preventer",
            message=("This menu controls Start/Stop only.\n\n"
                     "Settings and logs live in the terminal:\n\n"
                     "    duplicate-preventer                # settings menu\n"
                     "    duplicate-preventer --follow-log   # live log\n\n"
                     "Changes made there apply live - no restart needed."))

    def toggle(self, _):
        if self.engine.status().monitoring:
            self.engine.stop()
        else:
            try:
                self.engine.start()
            except EngineError as error:
                bring_to_front()
                rumps.alert(title="Cannot start", message=str(error))
        self._refresh()

    def quit(self, _=None):
        self._tick_timer.stop()
        self.engine.stop()
        rumps.quit_application()

    def _tick(self, _timer):
        self.engine.events.drain()          # the log has the details
        self.engine.reload_config_if_changed()
        self._refresh()

    def _refresh(self):
        st = self.engine.status()
        if not st.healthy:
            self.icon = ICON_PROBLEM
            self.status_item.title = f"Problem: {st.last_error or 'see log'}"
        elif st.monitoring:
            self.icon = ICON_WATCHING
            self.status_item.title = (
                f"Watching {len(st.watched_folders)} folders - "
                f"{st.quarantined_session} quarantined this session"
                + (" (dry run)" if st.dry_run else ""))
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
        if engine.config.get("watched_folders"):
            try:
                engine.start()
            except EngineError:
                pass            # icon shows "not monitoring"; menu explains on Start
        app.run()
    finally:
        engine.stop()
    return 0


# End of file #
