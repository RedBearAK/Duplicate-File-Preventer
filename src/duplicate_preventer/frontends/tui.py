"""
Interactive terminal menu: the settings editor and log viewer.

Rendering rule: this module prints only from the main thread, and only
when it chooses to. Nothing arrives from the watcher unasked - the menu
pulls events from the engine queue when it redraws, and the "Watch
activity" screen is the deliberate live view (Ctrl-C leaves it).
"""

import os
import re
import time
import threading

from datetime import datetime
from collections import deque

from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.console import Console, Group

from duplicate_preventer._version import __version__
from duplicate_preventer.engine import Config, Engine, EngineError
from duplicate_preventer.engine.utils import (
    clean_path,
    format_size,
    is_cloud_folder,
    parse_time_window,
    format_time_window,
)
from duplicate_preventer.engine.quarantine import (
    clean_old,
    restore_file,
    list_quarantine,
    find_quarantined,
    read_restore_info,
    QuarantineError,
)
from duplicate_preventer.frontends.render import (
    uptime_str,
    tail_lines,
    follow_log,
    event_markup,
    status_summary,
    log_line_markup,
    detection_sentence,
)


RECENT_EVENTS = 200
LIVE_ROWS = 15
LIVE_REFRESH = 0.5


class DuplicateMonitorTUI:

    def __init__(self, config=None, console=None):
        self.console = console or Console()
        self.engine = Engine(config or Config())
        self.config = self.engine.config
        self.recent = deque(maxlen=RECENT_EVENTS)

    # --- helpers -----------------------------------------------------------

    def pause(self):
        self.console.input("\n[dim]Press Enter to continue...[/dim]")

    def refresh_state(self):
        """Pull config changes and events; never blocks."""
        self.engine.reload_config_if_changed()
        self.config = self.engine.config
        for event in self.engine.events.drain():
            self.recent.append(event)
        return self.engine.status()

    def last_notable_event(self):
        for event in reversed(self.recent):
            if event.kind != "checked":
                return event
        return None

    # --- main menu ---------------------------------------------------------

    def show_menu(self):
        while True:
            status = self.refresh_state()
            self.console.clear()
            self.console.print(f"\n[bold cyan]═══ Duplicate File Preventer v{__version__} ═══[/bold cyan]\n")

            first, second = status_summary(status, self.last_notable_event())
            dot = "[green]●[/green]" if status.monitoring and status.healthy else \
                  "[red]●[/red]" if not status.healthy else "[yellow]●[/yellow]"
            self.console.print(f"Status: {dot} {first}")
            if second:
                self.console.print(f"        [dim]{second}[/dim]")
            self.console.print("[dim]Auto-save: enabled — settings apply live to a running monitor[/dim]\n")

            locked_elsewhere = status.lock_holder_pid is not None and not status.monitoring
            self.console.print("1. 📁  Manage watched folders")
            self.console.print("2. ⚙️   Configure settings")
            self.console.print("3. 👁️   View current configuration")
            if status.monitoring:
                self.console.print("4. ⏸️   Stop monitoring")
            elif locked_elsewhere:
                self.console.print(f"4. ▶️   Start monitoring [dim](running in PID {status.lock_holder_pid})[/dim]")
            else:
                self.console.print("4. ▶️   Start monitoring")
            self.console.print("5. 📡  Watch activity (live view)")
            self.console.print("6. 🗑️   View quarantine")
            self.console.print("7. 📊  View logs & statistics")
            self.console.print("8. 🧹  Clean existing duplicates\n")
            self.console.print("Q. 🚪  Quit\n")
            if status.monitoring:
                self.console.print("[dim]Press Enter to refresh the status line[/dim]\n")

            choice = Prompt.ask("Select option", default="", show_default=False,
                                console=self.console).strip().upper()

            if not choice:
                continue
            if choice == "1":
                self.manage_folders()
            elif choice == "2":
                self.configure_settings()
            elif choice == "3":
                self.view_configuration()
            elif choice == "4":
                self.toggle_monitoring()
            elif choice == "5":
                self.watch_activity()
            elif choice == "6":
                self.view_quarantine()
            elif choice == "7":
                self.view_statistics()
            elif choice == "8":
                self.clean_existing_duplicates()
            elif choice == "Q":
                self.engine.stop()
                self.console.print("\n[cyan]Goodbye![/cyan]\n")
                return
            else:
                self.console.print(f"[red]Invalid option: {choice}[/red]")
                self.pause()

    # --- monitoring --------------------------------------------------------

    def toggle_monitoring(self):
        status = self.engine.status()
        if status.monitoring:
            self.engine.stop()
            self.console.print("[green]Monitor stopped.[/green]")
            self.pause()
            return

        if not self.config.get("watched_folders"):
            self.console.print("[red]No folders to watch! Add folders first.[/red]")
            self.pause()
            return

        try:
            self.engine.start()
        except EngineError as error:
            self.console.print(f"[red]{error}[/red]")
            self.console.print("[yellow]Settings and logs are still available here; "
                               "that process picks up changes live.[/yellow]")
            self.pause()
            return

        status = self.refresh_state()
        if self.config.get("dry_run"):
            self.console.print("[cyan]Running in DRY RUN mode - no files will be moved[/cyan]")
        for folder in status.watched_folders:
            self.console.print(f"[green]Watching: {folder}[/green]")
        for folder in self.config.get("watched_folders", []):
            if folder not in status.watched_folders:
                self.console.print(f"[red]Skipping missing folder: {folder}[/red]")
        if status.healthy:
            self.console.print("\n[green]Monitor started.[/green] Only NEW files are checked; "
                               "use option 8 for existing ones.")
        else:
            self.console.print(f"\n[red]Started with a problem: {status.last_error}[/red]")
        self.pause()

    def watch_activity(self):
        """Live view. Reads the engine queue, or tails the log when another process monitors."""
        status = self.refresh_state()
        from_log = not status.monitoring and status.lock_holder_pid is not None
        log_file = self.config.get("log_file")

        self.console.clear()
        source = f"log file (monitor is PID {status.lock_holder_pid})" if from_log else "this process"
        self.console.print(f"[bold]Watch activity[/bold] — source: {source}")
        self.console.print("[dim]Press Ctrl-C to return to the menu[/dim]\n")

        log_lines = deque(maxlen=LIVE_ROWS)
        stop_tail = {"flag": False}
        tail_thread = None
        if from_log and os.path.exists(log_file):
            tail_thread = threading.Thread(
                target=follow_log,
                args=(log_file, log_lines.append, lambda: stop_tail["flag"], LIVE_ROWS),
                daemon=True)
            tail_thread.start()

        def render():
            status = self.refresh_state()
            first, _second = status_summary(status)
            header = f"[bold]{first}[/bold]"
            if status.monitoring:
                header += (f"\nchecked {status.checked_session}, quarantined "
                           f"{status.quarantined_session} this session, "
                           f"{status.quarantined_total} in quarantine total, "
                           f"uptime {uptime_str(status.started_at)}")
            header += f"\n[dim]detection: {self.engine.detection_summary()}[/dim]"

            table = Table(show_header=False, box=None, padding=(0, 1))
            table.add_column("event", overflow="ellipsis", no_wrap=True)
            if from_log:
                for line in list(log_lines):
                    table.add_row(log_line_markup(line))
            else:
                rows = [e for e in self.recent if e.kind != "checked"][-LIVE_ROWS:]
                for event in rows:
                    table.add_row(event_markup(event, width=self.console.width - 6))
            if table.row_count == 0:
                table.add_row("[dim]No activity yet[/dim]")

            return Group(Panel(header, title="Status", border_style="cyan"),
                         Panel(table, title="Recent activity", border_style="dim"))

        try:
            with Live(render(), console=self.console, refresh_per_second=4, screen=False) as live:
                while True:
                    self._live_wait()
                    live.update(render())
        except KeyboardInterrupt:
            pass
        finally:
            stop_tail["flag"] = True
            if tail_thread:
                tail_thread.join(timeout=2)
        self.console.print("\n[dim]Returning to menu[/dim]")

    def _live_wait(self):
        """One tick of the live view. Tests override this to inject Ctrl-C."""
        time.sleep(LIVE_REFRESH)

    # --- folders -----------------------------------------------------------

    def manage_folders(self):
        while True:
            self.refresh_state()
            self.console.clear()
            self.console.print("\n[bold]Watched Folders[/bold]\n")

            folders = list(self.config.get("watched_folders", []))
            if folders:
                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("#", style="dim", width=3)
                table.add_column("Folder Path")
                table.add_column("Status")
                for i, folder in enumerate(folders, 1):
                    table.add_row(str(i), folder,
                                  "✓ Valid" if os.path.isdir(folder) else "✗ Missing")
                self.console.print(table)
            else:
                self.console.print("[yellow]No folders being watched[/yellow]")

            self.console.print("\n1. Add folder (drag & drop supported)")
            self.console.print("2. Remove folder")
            self.console.print("3. Edit folder path\n")
            self.console.print("0. Back to main menu\n")

            choice = Prompt.ask("Select option", choices=["1", "2", "3", "0"], console=self.console)
            if choice == "1":
                self._add_folder(folders)
            elif choice == "2":
                self._remove_folder(folders)
            elif choice == "3":
                self._edit_folder(folders)
            else:
                return

    def _add_folder(self, folders):
        self.console.print("\n[dim]Tip: You can drag and drop a folder here[/dim]")
        path = clean_path(Prompt.ask("Enter folder path", console=self.console))

        if not os.path.isdir(path):
            self.console.print("[red]Invalid folder path[/red]")
            self.pause()
            return

        path = os.path.abspath(path)
        if path in folders:
            self.console.print("[yellow]Folder already in list[/yellow]")
        else:
            folders.append(path)
            self.config.set("watched_folders", folders)
            self.console.print(f"[green]Added: {path}[/green]  [dim](saved)[/dim]")
            if is_cloud_folder(path):
                self.console.print("[yellow]Note: this looks like a cloud-synced folder. "
                                   "Quarantined files are stored outside it.[/yellow]")
        self.pause()

    def _remove_folder(self, folders):
        if not folders:
            return
        idx = IntPrompt.ask("Enter folder number to remove", default=0,
                            show_default=False, console=self.console)
        if 1 <= idx <= len(folders):
            removed = folders.pop(idx - 1)
            self.config.set("watched_folders", folders)
            self.console.print(f"[green]Removed: {removed}[/green]")
        else:
            self.console.print("[red]Invalid number[/red]")
        self.pause()

    def _edit_folder(self, folders):
        if not folders:
            return
        idx = IntPrompt.ask("Enter folder number to edit", default=0,
                            show_default=False, console=self.console)
        if not 1 <= idx <= len(folders):
            self.console.print("[red]Invalid number[/red]")
            self.pause()
            return

        old_path = folders[idx - 1]
        self.console.print(f"\nCurrent path: {old_path}")
        year_match = re.search(r'(?<=[/\\])(20\d{2})(?=[/\\])', old_path)
        if year_match:
            year = year_match.group(1)
            suggested = old_path.replace(year, str(int(year) + 1))
            self.console.print(f"[cyan]Suggested: {suggested}[/cyan]")

        new_path = clean_path(Prompt.ask("Enter new path", default=old_path, console=self.console))
        if os.path.isdir(new_path):
            folders[idx - 1] = os.path.abspath(new_path)
            self.config.set("watched_folders", folders)
            self.console.print(f"[green]Updated path to: {folders[idx - 1]}[/green]")
        else:
            self.console.print("[red]Invalid folder path - keeping original[/red]")
        self.pause()

    # --- settings ----------------------------------------------------------

    def configure_settings(self):
        self.refresh_state()
        console = self.console
        config = self.config
        console.clear()
        console.print("\n[bold]Configuration Settings[/bold]\n")
        changes = {}

        console.print("[bold]Test Mode:[/bold]")
        changes["dry_run"] = Confirm.ask("Enable dry run mode? (test without moving files)",
                                         default=config.get("dry_run", False), console=console)

        console.print("\n[bold]Detection Methods:[/bold]")
        changes["check_size"] = Confirm.ask("Check file size?", default=config.get("check_size"),
                                            console=console)
        changes["check_time"] = Confirm.ask("Check creation time?", default=config.get("check_time"),
                                            console=console)
        if changes["check_time"]:
            current = format_time_window(int(config.get("time_window")))
            console.print(f"\nCurrent time window: {current}")
            console.print("[dim]Format: number + unit (5m, 2h, 3d, 1w, 2mo, 1y)[/dim]")
            while True:
                seconds = parse_time_window(Prompt.ask("Time window for duplicates",
                                                       default=current, console=console))
                if seconds:
                    changes["time_window"] = seconds
                    break
                console.print("[red]Invalid format. Use: 5m, 2h, 3d, 1w, 2mo, 1y[/red]")

        changes["use_hash"] = Confirm.ask("Enable hash verification? (more accurate but slower)",
                                          default=config.get("use_hash"), console=console)
        if changes["use_hash"]:
            console.print("\nHash algorithms: md5 (fast), sha256 (secure), sha512 (most secure)")
            changes["hash_algorithm"] = Prompt.ask(
                "Select hash algorithm", default=config.get("hash_algorithm"),
                choices=["md5", "sha1", "sha256", "sha512"], console=console)

        console.print("\n[bold]Quarantine Settings:[/bold]")
        current_quarantine = config.get("quarantine_path")
        console.print(f"Current quarantine path: {current_quarantine}")
        if Confirm.ask("Change quarantine location?", default=False, console=console):
            console.print("[dim]Tip: choose a location outside of Dropbox/OneDrive/iCloud[/dim]")
            quarantine = clean_path(Prompt.ask("Quarantine folder path",
                                               default=current_quarantine, console=console))
            if is_cloud_folder(quarantine):
                console.print("[yellow]⚠️  This looks like a cloud sync folder - quarantined "
                              "files would sync right back.[/yellow]")
                if not Confirm.ask("Continue anyway?", default=False, console=console):
                    quarantine = current_quarantine
            changes["quarantine_path"] = quarantine

        console.print("[dim]move = rename into quarantine; copy_delete = copy, verify, then delete the "
                      "original (looks like a deletion to Dropbox, so no 'moved out' prompt)[/dim]")
        changes["quarantine_method"] = Prompt.ask(
            "Quarantine method", default=config.get("quarantine_method", "move"),
            choices=["move", "copy_delete"], console=console)
        changes["delete_after_days"] = IntPrompt.ask(
            "Delete quarantined files after (days, 0=never)",
            default=config.get("delete_after_days"), console=console)

        console.print("\n[bold]Logging Settings:[/bold]")
        console.print("Log levels: DEBUG (verbose), INFO (normal), WARNING (important only)")
        changes["log_level"] = Prompt.ask("Log level", default=config.get("log_level", "INFO"),
                                          choices=["DEBUG", "INFO", "WARNING"], console=console)
        changes["log_max_size"] = IntPrompt.ask("Max log file size (MB)",
                                                default=config.get("log_max_size", 10),
                                                console=console)

        config.update(changes)
        console.print("\n[green]Settings updated and saved.[/green]")
        console.print("\n[bold]Current Detection Logic:[/bold]")
        console.print(detection_sentence(config))
        self.pause()

    def view_configuration(self):
        status = self.refresh_state()
        config = self.config
        self.console.clear()
        self.console.print("\n[bold]Current Configuration[/bold]\n")

        table = Table(show_header=False, box=None)
        table.add_column("Setting", style="cyan")
        table.add_column("Value")

        table.add_row("[bold]File Locations[/bold]", "")
        table.add_row("  Config File", config.config_file)
        table.add_row("  Log File", config.get("log_file"))
        table.add_row("  Quarantine", config.get("quarantine_path"))
        table.add_row("  Quarantine Method", config.get("quarantine_method", "move"))
        if is_cloud_folder(config.get("quarantine_path")):
            table.add_row("", "[yellow]⚠️  Inside cloud sync folder[/yellow]")
        table.add_row("", "")
        table.add_row("Watched Folders", str(len(config.get("watched_folders", []))))
        if config.get("dry_run"):
            table.add_row("Mode", "[cyan]DRY RUN (test mode)[/cyan]")
        table.add_row("", "")
        table.add_row("[bold]Detection Methods[/bold]", "")
        table.add_row("  File Size Check", "✓ Enabled" if config.get("check_size") else "✗ Disabled")
        table.add_row("  Time Window Check", "✓ Enabled" if config.get("check_time") else "✗ Disabled")
        if config.get("check_time"):
            table.add_row("  Time Window", format_time_window(int(config.get("time_window"))))
        table.add_row("  Hash Verification", "✓ Enabled" if config.get("use_hash") else "✗ Disabled")
        if config.get("use_hash"):
            table.add_row("  Hash Algorithm", str(config.get("hash_algorithm")).upper())
        table.add_row("", "")
        days = config.get("delete_after_days", 0)
        table.add_row("Auto-delete After", f"{days} days" if days > 0 else "Never")
        table.add_row("Log Level", config.get("log_level", "INFO"))
        table.add_row("Log Max Size", f"{config.get('log_max_size', 10)} MB")
        if status.lock_holder_pid:
            table.add_row("Monitor", f"running in another process (PID {status.lock_holder_pid})")
        self.console.print(table)

        folders = config.get("watched_folders", [])
        if folders:
            self.console.print("\n[bold]Watched Folders:[/bold]")
            for i, folder in enumerate(folders, 1):
                self.console.print(f"  {i}. {'✓' if os.path.isdir(folder) else '✗'} {folder}")

        self.console.print("\n[dim]All changes are saved automatically and apply live[/dim]")
        try:
            mtime = os.path.getmtime(config.config_file)
            self.console.print(f"[dim]Last saved: {datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M:%S}[/dim]")
        except OSError:
            pass
        self.pause()

    # --- one-shot cleanup --------------------------------------------------

    def clean_existing_duplicates(self):
        self.refresh_state()
        console = self.console
        console.clear()
        console.print("\n[bold]Clean Existing Duplicates[/bold]\n")

        folders = self.config.get("watched_folders", [])
        if not folders:
            console.print("[red]No folders configured to scan![/red]")
            self.pause()
            return

        console.print("[bold]Will scan these folders (recursively):[/bold]")
        for folder in folders:
            mark = "✓" if os.path.isdir(folder) else "✗"
            console.print(f"  {mark} {folder}" + ("" if os.path.isdir(folder) else " [red](missing)[/red]"))
        console.print("\n[yellow]Looks for file-1.ext, file-2.ext ... sitting next to file.ext[/yellow]")

        overrides = {}
        if Confirm.ask("\nUse time window check?", default=False, console=console):
            current = format_time_window(int(self.config.get("time_window", 300)))
            console.print(f"Current time window: {current}")
            console.print("[dim]Format: number + unit (5m, 2h, 3d, 1w, 2mo, 1y)[/dim]")
            while True:
                seconds = parse_time_window(Prompt.ask("Time window for existing files",
                                                       default=current, console=console))
                if seconds:
                    overrides["check_time"] = True
                    overrides["time_window"] = seconds
                    break
                console.print("[red]Invalid format. Use: 5m, 2h, 3d, 1w, 2mo, 1y[/red]")
        else:
            overrides["check_time"] = False

        current_hash = self.config.get("use_hash", False)
        console.print(f"\nCurrent hash verification: {'ON' if current_hash else 'OFF'}")
        overrides["use_hash"] = Confirm.ask("Use hash verification for this scan?",
                                            default=current_hash, console=console)
        overrides["dry_run"] = Confirm.ask("\nRun in dry-run mode?", default=True, console=console)

        scan_config = self.config.detached(**overrides)
        console.print("\n[bold]Detection settings for this scan:[/bold]")
        console.print(f"  {self.engine._describe(scan_config)}; "
                      f"{'DRY RUN - nothing moved' if overrides['dry_run'] else 'files WILL be quarantined'}")
        if not Confirm.ask("\nProceed with scan?", default=True, console=console):
            return

        console.print("\n[yellow]Scanning...[/yellow]\n")

        def progress(count, _path):
            if count % 10 == 0:
                console.print(f"  [dim]Checked {count} candidates...[/dim]")

        result = self.engine.scan_once(config=scan_config, progress=progress)
        for event in self.engine.events.drain():
            self.recent.append(event)
            if event.kind in ("quarantined", "dry_run", "error"):
                console.print("  " + event_markup(event))

        console.print(f"\n[bold]Scan Complete[/bold]")
        console.print(f"Candidates seen: {result.scanned}   with an original present: {result.processed}")
        console.print(f"Quarantined: {result.quarantined}   dry-run: {result.dry_run}   "
                      f"unique: {result.unique}   errors: {result.errors}")
        if overrides["dry_run"] and result.dry_run:
            console.print("\n[cyan]This was a dry run. Run again with dry-run OFF to move files.[/cyan]")
        self.pause()

    # --- quarantine --------------------------------------------------------

    def view_quarantine(self):
        while True:
            self.refresh_state()
            console = self.console
            console.clear()
            console.print("\n[bold]Quarantine Folder[/bold]\n")

            root = self.config.get("quarantine_path")
            entries = list_quarantine(self.config)
            console.print(f"Location: {root}")
            if not entries:
                console.print("[yellow]Quarantine folder is empty[/yellow]")
            else:
                console.print(f"Total files: {len(entries)}")
                console.print(f"Total size: {format_size(sum(e['size'] for e in entries))}")
                by_date = {}
                for entry in entries:
                    by_date.setdefault(entry["date"], []).append(entry["rel"])
                console.print("\n[bold]Quarantined files by date:[/bold]")
                for date in sorted(by_date, reverse=True)[:5]:
                    console.print(f"\n[cyan]{date}:[/cyan]")
                    for rel in by_date[date][:10]:
                        console.print(f"  → {rel}")
                    if len(by_date[date]) > 10:
                        console.print(f"  [dim]... and {len(by_date[date]) - 10} more files[/dim]")

            console.print("\n1. View restoration info for a file")
            console.print("2. Restore a file")
            console.print("3. Clean old quarantined files\n")
            console.print("0. Back\n")

            choice = Prompt.ask("Select option", choices=["1", "2", "3", "0"], default="0",
                                console=console)
            if choice == "1":
                self._view_restoration_info()
            elif choice == "2":
                self._restore_file()
            elif choice == "3":
                self._clean_old_quarantine()
            else:
                return

    def _view_restoration_info(self):
        filename = Prompt.ask("\nEnter filename to check", console=self.console)
        entry = find_quarantined(self.config, filename)
        info = read_restore_info(entry["path"]) if entry else None
        if info:
            self.console.print(f"\n[bold]Restoration info for {filename}:[/bold]")
            for key, value in info.items():
                self.console.print(f"{key}: {value}")
        else:
            self.console.print("[yellow]File not found in quarantine[/yellow]")
        self.pause()

    def _restore_file(self):
        self.console.print("\n[yellow]This restores the file to its original location[/yellow]")
        filename = Prompt.ask("Enter filename to restore", console=self.console)
        entry = find_quarantined(self.config, filename)
        info = read_restore_info(entry["path"]) if entry else None
        if not info:
            self.console.print("[yellow]File not found in quarantine[/yellow]")
            self.pause()
            return

        self.console.print(f"\nOriginal location: {info.get('Original path', '?')}")
        if Confirm.ask("Restore this file?", default=False, console=self.console):
            try:
                restored = restore_file(entry["path"])
                self.console.print(f"[green]File restored to: {restored}[/green]")
                self.engine.logger.info(f"RESTORED: {entry['path']} -> {restored}")
            except QuarantineError as error:
                self.console.print(f"[red]Error restoring file: {error}[/red]")
                self.engine.logger.error(f"RESTORE FAILED: {entry['path']}: {error}")
        self.pause()

    def _clean_old_quarantine(self):
        days = self.config.get("delete_after_days", 0)
        if not days:
            self.console.print("[yellow]Auto-delete is disabled (set to 0 days)[/yellow]")
            self.pause()
            return

        cutoff = datetime.now().date()
        self.console.print(f"\n[yellow]This will delete files quarantined more than {days} days "
                           f"before {cutoff}[/yellow]")
        if not Confirm.ask("Continue?", default=False, console=self.console):
            return

        count, size, errors = clean_old(self.config, days)
        for error in errors:
            self.console.print(f"[red]{error}[/red]")
        self.console.print(f"\n[green]Cleaned {count} files ({format_size(size)})[/green]")
        self.engine.logger.info(f"CLEANED quarantine: {count} files, {size} bytes, {len(errors)} errors")
        self.pause()

    # --- logs & statistics -------------------------------------------------

    def view_statistics(self):
        while True:
            status = self.refresh_state()
            console = self.console
            console.clear()
            console.print("\n[bold]Monitoring Statistics & Logs[/bold]\n")

            if status.monitoring:
                table = Table(show_header=False, box=None)
                table.add_column("Metric", style="cyan")
                table.add_column("Value")
                started = datetime.fromtimestamp(status.started_at) if status.started_at else None
                table.add_row("Session Started", f"{started:%Y-%m-%d %H:%M:%S}" if started else "-")
                table.add_row("Uptime", uptime_str(status.started_at))
                table.add_row("Files Checked", str(status.checked_session))
                table.add_row("Duplicates Found", str(status.quarantined_session))
                table.add_row("In Quarantine (total)", str(status.quarantined_total))
                table.add_row("Healthy", "yes" if status.healthy else f"NO - {status.last_error}")
                console.print(table)
                console.print("")

            console.print("1. View recent activity (last 20 entries)")
            console.print("2. View errors and warnings only")
            console.print("3. View detailed log (last 50 entries)")
            console.print("4. Search logs")
            console.print("5. Export logs")
            console.print("6. Clear old logs\n")
            console.print("0. Back to main menu\n")

            choice = Prompt.ask("Select option", choices=["1", "2", "3", "4", "5", "6", "0"],
                                console=console)
            if choice == "1":
                self._show_log_tail(20, "Recent Activity")
            elif choice == "2":
                self._view_error_logs()
            elif choice == "3":
                self._show_log_tail(50, "Detailed Log")
            elif choice == "4":
                self._search_logs()
            elif choice == "5":
                self._export_logs()
            elif choice == "6":
                self._clear_old_logs()
            else:
                return

    def _log_file_or_none(self):
        log_file = self.config.get("log_file")
        if not os.path.exists(log_file):
            self.console.print("[yellow]No log file found yet[/yellow]")
            self.pause()
            return None
        return log_file

    def _show_log_tail(self, count, title):
        log_file = self._log_file_or_none()
        if not log_file:
            return
        self.console.print(f"\n[bold]{title}:[/bold]\n")
        for line in tail_lines(log_file, count):
            self.console.print(log_line_markup(line))
        self.pause()

    def _view_error_logs(self):
        log_file = self._log_file_or_none()
        if not log_file:
            return
        self.console.print("\n[bold]Errors and Warnings:[/bold]\n")
        errors = warnings = 0
        with open(log_file, 'r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                if "ERROR" in line or "FAILED" in line:
                    self.console.print(log_line_markup(line))
                    errors += 1
                elif "WARNING" in line:
                    self.console.print(log_line_markup(line))
                    warnings += 1
        self.console.print(f"\nTotal: {errors} errors, {warnings} warnings")
        self.pause()

    def _search_logs(self):
        term = Prompt.ask("\nEnter search term", console=self.console)
        log_file = self._log_file_or_none()
        if not log_file:
            return
        self.console.print(f"\n[bold]Search results for '{term}':[/bold]\n")
        matches = 0
        with open(log_file, 'r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                if term.lower() in line.lower():
                    self.console.print(log_line_markup(line))
                    matches += 1
        self.console.print(f"\nFound {matches} matches")
        self.pause()

    def _export_logs(self):
        import shutil
        log_file = self._log_file_or_none()
        if not log_file:
            return
        export_path = f"duplicate_monitor_export_{datetime.now():%Y%m%d_%H%M%S}.log"
        try:
            shutil.copy2(log_file, export_path)
            self.console.print(f"[green]Logs exported to: {os.path.abspath(export_path)}[/green]")
        except OSError as error:
            self.console.print(f"[red]Export failed: {error}[/red]")
        self.pause()

    def _clear_old_logs(self):
        import shutil
        if not Confirm.ask("\nAre you sure you want to clear old logs?", default=False,
                           console=self.console):
            return
        log_file = self.config.get("log_file")
        if not os.path.exists(log_file):
            self.console.print("[yellow]No log file to clear[/yellow]")
        else:
            shutil.copy2(log_file, log_file + ".backup")
            self.console.print(f"[green]Backup created: {log_file}.backup[/green]")
            with open(log_file, 'w', encoding='utf-8') as handle:
                handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | INFO     | Log file cleared\n")
            self.console.print("[green]Log file cleared[/green]")
        self.pause()


def run_tui(config=None, console=None):
    """Entry point for the interactive menu. Returns the process exit code."""
    console = console or Console()
    console.print("\n[bold cyan]Duplicate File Preventer[/bold cyan]")
    console.print("Prevents duplicate files from syncing to cloud storage")
    console.print("Designed for Thunderbird FiltaQuilla attachment handling\n")

    tui = DuplicateMonitorTUI(config=config, console=console)

    if not os.path.exists(tui.config.config_file):
        console.print("[green]Creating configuration in:[/green]")
        console.print(f"  Config: {tui.config.config_file}")
        console.print(f"  Logs: {tui.config.get('log_file')}")
        console.print(f"  Quarantine: {tui.config.get('quarantine_path')}\n")
        tui.config.save_config()

    try:
        tui.show_menu()
    except KeyboardInterrupt:
        console.print("\n\n[yellow]Interrupted by user[/yellow]")
    finally:
        tui.engine.stop()
    return 0


# End of file #
