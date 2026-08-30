"""
Entry point and dispatch.

    duplicate-file-preventer                  interactive menu (settings + logs)
    duplicate-file-preventer --start          monitor in the foreground
    duplicate-file-preventer --once           scan existing files and exit
    duplicate-file-preventer --follow-log     live log
    duplicate-file-preventer --menubar        macOS menu bar app (optional extra)
    duplicate-file-preventer --install-command
    duplicate-file-preventer --install-app   macOS .app bundle for Login Items
"""

import sys
import platform


CLI_FLAGS = ("--start", "-s", "--once", "-o", "--show-log", "-l", "--follow-log", "-f",
             "--install-command", "--uninstall-command", "--install-app", "--uninstall-app",
             "--dry-run", "-d",
             "--config", "-c", "--version", "-V", "--help", "-h")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--menubar" in argv:
        if platform.system() != "Darwin":
            sys.exit("--menubar is only available on macOS")
        try:
            from duplicate_preventer.frontends.menubar import run_menubar
        except ImportError as error:
            sys.exit("Menu bar mode needs the optional extra: "
                     f"pip install '.[menubar]'  ({error})")
        from duplicate_preventer.engine import Config
        config_file = None
        if "--config" in argv:
            config_file = argv[argv.index("--config") + 1]
        return run_menubar(Config(config_file=config_file))

    if any(a in CLI_FLAGS or a.startswith("--config=") for a in argv):
        from duplicate_preventer.frontends.cli import run_cli
        return run_cli(argv)

    from duplicate_preventer.frontends.tui import run_tui
    return run_tui()


if __name__ == "__main__":
    sys.exit(main())


# End of file #
