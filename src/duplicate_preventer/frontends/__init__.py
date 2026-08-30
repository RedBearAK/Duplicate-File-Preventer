"""
Front ends: everything that shows something to a human.

    cli.py       flag-driven, non-interactive
    tui.py       the interactive rich menu (settings + logs)
    menubar.py   macOS menu bar app (optional; guarded import of rumps)
    installer.py the --install-command launcher stub (pure stdlib)
    render.py    shared text rendering helpers

Importing this package must not import rumps; menubar is imported only
by __main__ when --menubar is given.
"""


# End of file #
