"""
macOS menu bar front end (rumps). Phase 2 of the handoff.

STATUS: written to the handoff design but NOT yet exercised on a Mac. Treat
as a starting point, not a shipped feature. The threading contract is the
part that must hold: rumps is touched only from timer/menu callbacks (main
thread); the engine's watchdog thread only ever touches the queue.

Deliberately minimal: status line, Start/Stop, About/Help, Quit. Settings
and logs live in the terminal (run `duplicate-preventer` / `--follow-log`).
"""

import signal

import rumps    # ImportError here is caught by __main__ with a helpful message

from duplicate_preventer.engine import Config, Engine, EngineError


GLYPH_RUNNING = "🟢"
GLYPH_PAUSED = "⏸"
GLYPH_ERROR = "⚠️"

TICK_SECONDS = 0.5


class DupePreventerApp(rumps.App):

    def __init__(self, engine):
        super().__init__("DupePrev", title=GLYPH_PAUSED, quit_button=None)
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
            self.title = GLYPH_ERROR
            self.status_item.title = f"Problem: {st.last_error or 'see log'}"
        elif st.monitoring:
            self.title = GLYPH_RUNNING
            self.status_item.title = (
                f"Watching {len(st.watched_folders)} folders - "
                f"{st.quarantined_session} quarantined this session"
                + (" (dry run)" if st.dry_run else ""))
        else:
            self.title = GLYPH_PAUSED
            self.status_item.title = "Status: not monitoring"
        self.toggle_item.title = "Stop monitoring" if st.monitoring else "Start monitoring"


def run_menubar(config=None):
    engine = Engine(config or Config())
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
