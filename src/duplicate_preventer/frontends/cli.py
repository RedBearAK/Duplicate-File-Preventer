"""
Non-interactive CLI: --start, --once, --show-log, --follow-log, and the
launcher installer. Plain line-oriented output; there is no menu to
protect here, so events stream to stdout as they happen.
"""

import os
import sys
import time
import signal
import argparse

from rich.console import Console

from duplicate_preventer._version import __version__
from duplicate_preventer.engine import Config, ConfigError, Engine, EngineError
from duplicate_preventer.frontends.render import (
    follow_log,
    event_markup,
    log_line_markup,
    status_summary,
)
from duplicate_preventer.frontends.installer import install_command, uninstall_command


IDLE_SUMMARY_SECONDS = 300      # --start prints a heartbeat line when idle this long
RELOAD_POLL_SECONDS = 0.5


def build_parser():
    parser = argparse.ArgumentParser(
        prog="duplicate-preventer",
        description="Duplicate File Preventer - quarantines FiltaQuilla's file-1.ext "
                    "duplicates before they sync to cloud storage. With no flags, "
                    "opens the interactive settings menu.")
    parser.add_argument('--version', '-V', action='version', version=f"%(prog)s {__version__}")
    parser.add_argument('--config', '-c', metavar='FILE',
                        help='config file (default: platform-specific location)')
    parser.add_argument('--dry-run', '-d', action='store_true',
                        help='test mode: log what would be quarantined, move nothing')

    mode = parser.add_argument_group('modes (mutually exclusive)')
    mode.add_argument('--start', '-s', action='store_true',
                      help='monitor in the foreground, streaming events; Ctrl-C stops')
    mode.add_argument('--once', '-o', action='store_true',
                      help='scan the watched folders for existing duplicates and exit')
    mode.add_argument('--show-log', '-l', action='store_true',
                      help='show the last 1000 log lines and exit')
    mode.add_argument('--follow-log', '-f', action='store_true',
                      help='show recent log lines, then follow new ones; Ctrl-C stops')
    mode.add_argument('--menubar', action='store_true',
                      help='macOS only: run as a menu bar app (needs the [menubar] extra)')
    mode.add_argument('--install-command', action='store_true',
                      help='write a launcher stub for this command onto your PATH')
    mode.add_argument('--uninstall-command', action='store_true',
                      help='remove the launcher stub written by --install-command')
    parser.add_argument('--dir', metavar='DIR',
                        help='with --install-command/--uninstall-command: the bin directory '
                             '(default: ~/.local/bin)')
    parser.add_argument('--lines', type=int, default=1000, metavar='N',
                        help='with --show-log/--follow-log: how many recent lines (default 1000)')
    return parser


def load_config(args, console):
    try:
        return Config(config_file=args.config)
    except ConfigError as error:
        console.print(f"[red]Cannot read configuration: {error}[/red]")
        sys.exit(2)


def run_cli(argv=None, console=None):
    """Entry point for flag-driven use. Returns the process exit code."""
    console = console or Console()
    args = build_parser().parse_args(argv)

    modes = [args.start, args.once, args.show_log, args.follow_log,
             args.install_command, args.uninstall_command]
    if sum(bool(m) for m in modes) > 1:
        console.print("[red]Choose only one of --start, --once, --show-log, "
                      "--follow-log, --install-command, --uninstall-command[/red]")
        return 2

    if args.install_command:
        return 0 if install_command(args.dir, out=console.print) else 1
    if args.uninstall_command:
        return 0 if uninstall_command(args.dir, out=console.print) else 1

    config = load_config(args, console)

    if args.show_log or args.follow_log:
        return _show_or_follow_log(config, console, follow=args.follow_log, lines=args.lines)

    if args.dry_run:
        config = config.detached(dry_run=True)
        console.print("[cyan]DRY RUN MODE - no files will be moved[/cyan]")

    if args.once:
        return _scan_once(config, console)
    if args.start:
        return _start(config, console)

    # No mode flag but --dry-run/--config given: fall through to the TUI.
    from duplicate_preventer.frontends.tui import run_tui
    return run_tui(config=config, console=console)


# --- modes -------------------------------------------------------------------

def _show_or_follow_log(config, console, follow, lines):
    log_file = config.get("log_file")
    if not os.path.exists(log_file):
        console.print(f"[yellow]No log file yet at {log_file}[/yellow]")
        return 0

    console.print(f"[bold]Log file:[/bold] {log_file}")
    if not follow:
        from duplicate_preventer.frontends.render import tail_lines
        for line in tail_lines(log_file, lines):
            console.print(log_line_markup(line))
        return 0

    console.print("[dim]Following log - press Ctrl-C to stop[/dim]\n")
    stopping = {"flag": False}

    def handler(_signum, _frame):
        stopping["flag"] = True

    previous = signal.signal(signal.SIGINT, handler)
    try:
        follow_log(log_file, lambda text: console.print(log_line_markup(text)),
                   lambda: stopping["flag"], initial_lines=lines)
    finally:
        signal.signal(signal.SIGINT, previous)
    console.print("\n[yellow]Stopped following log[/yellow]")
    return 0


def _scan_once(config, console):
    folders = config.get("watched_folders", [])
    if not folders:
        console.print("[red]No folders configured. Run without flags to add some.[/red]")
        return 1

    engine = Engine(config)
    console.print(f"Scanning {len(folders)} folder(s): {engine.detection_summary()}")

    def progress(count, path):
        if count % 25 == 0:
            console.print(f"  [dim]checked {count} candidates...[/dim]")

    result = engine.scan_once(progress=progress)
    for event in engine.events.drain():
        if event.kind in ("quarantined", "dry_run", "error"):
            console.print(event_markup(event))

    for folder in result.skipped_folders:
        console.print(f"[red]Skipped missing folder: {folder}[/red]")
    console.print(f"\nCandidates seen: {result.scanned}   with an original present: {result.processed}")
    console.print(f"Quarantined: {result.quarantined}   dry-run: {result.dry_run}   "
                  f"unique: {result.unique}   errors: {result.errors}")
    return 1 if result.errors else 0


def _start(config, console):
    if not config.get("watched_folders"):
        console.print("[red]No folders configured. Run without flags to add some.[/red]")
        return 1

    engine = Engine(config)
    try:
        engine.start()
    except EngineError as error:
        console.print(f"[red]{error}[/red]")
        return 1

    status = engine.status()
    for folder in status.watched_folders:
        console.print(f"[green]Watching: {folder}[/green]")
    console.print(f"Detection: {engine.detection_summary()}")
    console.print("[dim]Settings edited in the menu apply live. Ctrl-C to stop.[/dim]\n")

    stopping = {"flag": False}

    def handler(_signum, _frame):
        stopping["flag"] = True

    previous_int = signal.signal(signal.SIGINT, handler)
    previous_term = signal.signal(signal.SIGTERM, handler)
    last_output = time.time()
    was_healthy = True

    try:
        while not stopping["flag"]:
            time.sleep(RELOAD_POLL_SECONDS)
            engine.reload_config_if_changed()
            for event in engine.events.drain():
                if event.kind != "checked":
                    console.print(event_markup(event))
                    last_output = time.time()

            status = engine.status()
            if was_healthy and not status.healthy:
                console.print(f"[red]Problem: {status.last_error}[/red]")
                last_output = time.time()
            was_healthy = status.healthy

            if time.time() - last_output >= IDLE_SUMMARY_SECONDS:
                first, _second = status_summary(status)
                console.print(f"[dim]{time.strftime('%H:%M:%S')}  {first}[/dim]")
                last_output = time.time()
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        engine.stop()

    status = engine.status()
    console.print(f"\n[yellow]Stopped.[/yellow] {status.quarantined_session} quarantined "
                  f"this session, {status.checked_session} files checked.")
    return 0


# End of file #
