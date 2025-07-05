"""
Main monitor class and user interface for Duplicate File Preventer
Provides interactive menu and monitoring control
"""
import os
import re
import shutil
import time
import argparse
from datetime import datetime, timedelta
from typing import Optional

from watchdog.observers import Observer
from rich.console import Console
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table

from .config import Config
from .duplicate_handler import DuplicateHandler
from .utils import clean_path, format_size, is_cloud_folder

console = Console()


class DuplicateMonitor:
    """Main application class"""
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.observer = None
        self.handler = None
        self.monitoring = False

    def show_menu(self):
        """Display main menu"""
        while True:
            console.clear()
            console.print("\n[bold cyan]═══ Duplicate File Preventer ═══[/bold cyan]\n")

            # Show current status
            status = "[green]●[/green] Active" if self.monitoring else "[red]●[/red] Stopped"
            dry_run = " [cyan](DRY RUN)[/cyan]" if self.config.get("dry_run", False) else ""
            console.print(f"Status: {status}{dry_run}\n")

            # Menu options
            console.print("1. 📁  Manage watched folders")
            console.print("2. ⚙️   Configure settings")
            console.print("3. 👁️   View current configuration")
            console.print("4. ▶️   Start monitoring" if not self.monitoring else "4. ⏸️   Stop monitoring")
            console.print("5. 🗑️   View quarantine")
            console.print("6. 📊  View logs & statistics")
            console.print("7. 💾  Save configuration\n")

            console.print("Q. 🚪  Quit\n")

            choice = Prompt.ask("Select option", choices=["1","2","3","4","5","6","7","Q"], case_sensitive=False)

            if choice == "1":
                self.manage_folders()
            elif choice == "2":
                self.configure_settings()
            elif choice == "3":
                self.view_configuration()
            elif choice == "4":
                self.toggle_monitoring()
            elif choice == "5":
                self.view_quarantine()
            elif choice == "6":
                self.view_statistics()
            elif choice == "7":
                self.config.save_config()
            elif choice == "Q":
                if self.monitoring:
                    self.stop_monitoring()
                console.print("\n[cyan]Goodbye![/cyan]\n")
                break

    def manage_folders(self):
        """Manage watched folders with drag-and-drop support"""
        while True:
            console.clear()
            console.print("\n[bold]Watched Folders[/bold]\n")

            # Display current folders
            folders = self.config.get("watched_folders", [])
            if folders:
                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("#", style="dim", width=3)
                table.add_column("Folder Path")
                table.add_column("Status")

                for i, folder in enumerate(folders, 1):
                    status = "✓ Valid" if os.path.exists(folder) else "✗ Missing"
                    table.add_row(str(i), folder, status)

                console.print(table)
            else:
                console.print("[yellow]No folders being watched[/yellow]")

            console.print("\n1. Add folder (drag & drop supported)")
            console.print("2. Remove folder")
            console.print("3. Edit folder path\n")

            console.print("0. Back to main menu\n")

            choice = Prompt.ask("Select option", choices=["1","2","3","0"], case_sensitive=False)

            if choice == "1":
                self._add_folder(folders)
            elif choice == "2":
                self._remove_folder(folders)
            elif choice == "3":
                self._edit_folder(folders)
            elif choice == "0":
                break

    def _add_folder(self, folders: list):
        """Add a new folder to watch"""
        console.print("\n[dim]Tip: You can drag and drop a folder here[/dim]")
        path = Prompt.ask("Enter folder path")

        # Clean up drag-and-drop paths
        path = clean_path(path)

        if os.path.exists(path) and os.path.isdir(path):
            # Resolve to absolute path
            path = os.path.abspath(path)

            if path not in folders:
                folders.append(path)
                self.config.set("watched_folders", folders)
                console.print(f"[green]Added: {path}[/green]")

                # Check if it's in Dropbox
                if "Dropbox" in path:
                    console.print("[yellow]Note: This appears to be a Dropbox folder.[/yellow]")
                    console.print("[yellow]Quarantined files will be stored outside Dropbox.[/yellow]")
            else:
                console.print("[yellow]Folder already in list[/yellow]")
        else:
            console.print("[red]Invalid folder path[/red]")
        input("\nPress Enter to continue...")

    def _remove_folder(self, folders: list):
        """Remove a folder from the watch list"""
        if not folders:
            return

        idx = IntPrompt.ask("Enter folder number to remove", 
                           default=0, show_default=False)
        if 1 <= idx <= len(folders):
            removed = folders.pop(idx - 1)
            self.config.set("watched_folders", folders)
            console.print(f"[green]Removed: {removed}[/green]")
        else:
            console.print("[red]Invalid number[/red]")
        input("\nPress Enter to continue...")

    def _edit_folder(self, folders: list):
        """Edit an existing folder path"""
        if not folders:
            return

        idx = IntPrompt.ask("Enter folder number to edit", 
                           default=0, show_default=False)
        if 1 <= idx <= len(folders):
            old_path = folders[idx - 1]
            console.print(f"\nCurrent path: {old_path}")
            console.print("[dim]Tip: Use arrow keys to edit, or paste new path[/dim]")

            # Suggest common edits
            if re.search(r'/20\d{2}/', old_path) or re.search(r'\\20\d{2}\\', old_path):
                year_match = re.search(r'20\d{2}', old_path)
                if year_match:
                    current_year = year_match.group()
                    new_year = str(int(current_year) + 1)
                    suggested = old_path.replace(current_year, new_year)
                    console.print(f"[cyan]Suggested: {suggested}[/cyan]")

            new_path = Prompt.ask("Enter new path", default=old_path)
            new_path = clean_path(new_path)

            if os.path.exists(new_path) and os.path.isdir(new_path):
                new_path = os.path.abspath(new_path)
                folders[idx - 1] = new_path
                self.config.set("watched_folders", folders)
                console.print(f"[green]Updated path to: {new_path}[/green]")
            else:
                console.print("[red]Invalid folder path - keeping original[/red]")
        else:
            console.print("[red]Invalid number[/red]")
        input("\nPress Enter to continue...")

    def configure_settings(self):
        """Configure application settings"""
        console.clear()
        console.print("\n[bold]Configuration Settings[/bold]\n")

        # Dry run mode
        console.print("[bold]Test Mode:[/bold]")
        dry_run = Confirm.ask("Enable dry run mode? (test without moving files)", 
                             default=self.config.get("dry_run", False))
        self.config.set("dry_run", dry_run)

        if dry_run:
            console.print("[cyan]Dry run enabled - no files will be moved[/cyan]")

        # Detection methods
        console.print("\n[bold]Detection Methods:[/bold]")

        # Size check
        check_size = Confirm.ask("Check file size?", 
                                default=self.config.get("check_size"))
        self.config.set("check_size", check_size)

        # Time window check
        check_time = Confirm.ask("Check creation time?", 
                                default=self.config.get("check_time"))
        self.config.set("check_time", check_time)

        if check_time:
            minutes = self.config.get("time_window") // 60
            minutes = IntPrompt.ask("Time window for duplicates (minutes)", 
                                   default=minutes, show_default=True)
            self.config.set("time_window", minutes * 60)

        # Hash verification (optional)
        use_hash = Confirm.ask("Enable hash verification? (more accurate but slower)", 
                               default=self.config.get("use_hash"))
        self.config.set("use_hash", use_hash)

        if use_hash:
            console.print("\nHash algorithms: md5 (fast), sha256 (secure), sha512 (most secure)")
            algo = Prompt.ask("Select hash algorithm", 
                             default=self.config.get("hash_algorithm"),
                             choices=["md5", "sha1", "sha256", "sha512"])
            self.config.set("hash_algorithm", algo)

        # Check interval
        console.print("\n[bold]Monitoring Settings:[/bold]")
        interval = IntPrompt.ask("File check interval (seconds)", 
                                default=self.config.get("check_interval"),
                                show_default=True)
        self.config.set("check_interval", interval)

        # Quarantine settings
        console.print("\n[bold]Quarantine Settings:[/bold]")
        current_quarantine = self.config.get("quarantine_path")
        console.print(f"Current quarantine path: {current_quarantine}")

        change_quarantine = Confirm.ask("Change quarantine location?", default=False)
        if change_quarantine:
            console.print("[dim]Tip: Choose a location outside of Dropbox/OneDrive/iCloud[/dim]")
            quarantine = Prompt.ask("Quarantine folder path",
                                   default=current_quarantine)
            quarantine = clean_path(quarantine)

            # Check if it's inside cloud folders
            if is_cloud_folder(quarantine):
                console.print("[yellow]⚠️  Warning: Quarantine folder appears to be in a cloud sync folder![/yellow]")
                console.print("[yellow]This will sync deleted files back to cloud storage.[/yellow]")
                if not Confirm.ask("Continue anyway?", default=False):
                    quarantine = current_quarantine

            self.config.set("quarantine_path", quarantine)

        days = IntPrompt.ask("Delete quarantined files after (days, 0=never)",
                            default=self.config.get("delete_after_days"))
        self.config.set("delete_after_days", days)

        # Logging settings
        console.print("\n[bold]Logging Settings:[/bold]")
        console.print("Log levels: DEBUG (verbose), INFO (normal), WARNING (important only)")
        log_level = Prompt.ask("Log level", 
                              default=self.config.get("log_level", "INFO"),
                              choices=["DEBUG", "INFO", "WARNING"])
        self.config.set("log_level", log_level)

        max_size = IntPrompt.ask("Max log file size (MB)",
                                default=self.config.get("log_max_size", 10))
        self.config.set("log_max_size", max_size)

        console.print("\n[green]Settings updated![/green]")

        # Show summary
        console.print("\n[bold]Current Detection Logic:[/bold]")
        logic_parts = []
        if check_size:
            logic_parts.append("matching file size")
        if check_time:
            logic_parts.append(f"created within {minutes} minutes")
        logic_parts.append("matching filename pattern (file-1, file-2, etc.)")
        if use_hash:
            logic_parts.append(f"verified by {algo.upper()} hash")

        console.print("Files are duplicates when: " + " AND ".join(logic_parts))

        input("\nPress Enter to continue...")

    def view_configuration(self):
        """Display current configuration"""
        console.clear()
        console.print("\n[bold]Current Configuration[/bold]\n")

        # Create a nice table
        table = Table(show_header=False, box=None)
        table.add_column("Setting", style="cyan")
        table.add_column("Value")

        # File locations
        table.add_row("[bold]File Locations[/bold]", "")
        table.add_row("  Config File", self.config.config_file)
        table.add_row("  Log File", self.config.get("log_file"))
        table.add_row("  Quarantine", self.config.get("quarantine_path"))

        # Check if quarantine is in cloud
        if is_cloud_folder(self.config.get("quarantine_path")):
            table.add_row("", "[yellow]⚠️  Inside cloud sync folder[/yellow]")

        table.add_row("", "")  # Empty row for spacing

        # Monitoring
        table.add_row("Watched Folders", str(len(self.config.get("watched_folders", []))))

        # Test mode
        if self.config.get("dry_run", False):
            table.add_row("Mode", "[cyan]DRY RUN (test mode)[/cyan]")

        # Detection methods
        table.add_row("", "")  # Empty row for spacing
        table.add_row("[bold]Detection Methods[/bold]", "")
        table.add_row("  File Size Check", "✓ Enabled" if self.config.get("check_size") else "✗ Disabled")
        table.add_row("  Time Window Check", "✓ Enabled" if self.config.get("check_time") else "✗ Disabled")
        if self.config.get("check_time"):
            table.add_row("  Time Window", f"{self.config.get('time_window') // 60} minutes")
        table.add_row("  Hash Verification", "✓ Enabled" if self.config.get("use_hash") else "✗ Disabled")
        if self.config.get("use_hash"):
            table.add_row("  Hash Algorithm", self.config.get("hash_algorithm").upper())

        # Other settings
        table.add_row("", "")  # Empty row for spacing
        table.add_row("Check Interval", f"{self.config.get('check_interval')} seconds")
        table.add_row("Auto-delete After", f"{self.config.get('delete_after_days')} days" 
                      if self.config.get('delete_after_days') > 0 else "Never")
        table.add_row("Log Level", self.config.get("log_level", "INFO"))
        table.add_row("Log Max Size", f"{self.config.get('log_max_size', 10)} MB")

        console.print(table)

        # Show watched folders if any
        folders = self.config.get("watched_folders", [])
        if folders:
            console.print("\n[bold]Watched Folders:[/bold]")
            for i, folder in enumerate(folders, 1):
                status = "✓" if os.path.exists(folder) else "✗"
                console.print(f"  {i}. {status} {folder}")

        input("\nPress Enter to continue...")

    def toggle_monitoring(self):
        """Start or stop monitoring"""
        if self.monitoring:
            self.stop_monitoring()
        else:
            self.start_monitoring()

    def start_monitoring(self):
        """Start the file system monitor"""
        folders = self.config.get("watched_folders", [])
        if not folders:
            console.print("[red]No folders to watch! Add folders first.[/red]")
            input("\nPress Enter to continue...")
            return

        console.print("[yellow]Starting monitor...[/yellow]")

        if self.config.get("dry_run", False):
            console.print("[cyan]Running in DRY RUN mode - no files will be moved[/cyan]")

        # Create handler
        self.handler = DuplicateHandler(self.config)

        self.observer = Observer()
        for folder in folders:
            if os.path.exists(folder):
                self.observer.schedule(self.handler, folder, recursive=True)
                console.print(f"[green]Watching: {folder}[/green]")
            else:
                console.print(f"[red]Skipping missing folder: {folder}[/red]")

        self.observer.start()
        self.monitoring = True
        console.print("\n[green]Monitor started successfully![/green]")
        console.print("[dim]Note: Only monitoring NEW files created while running[/dim]")
        input("\nPress Enter to continue...")

    def stop_monitoring(self):
        """Stop the file system monitor"""
        if self.observer:
            console.print("[yellow]Stopping monitor...[/yellow]")
            self.observer.stop()
            self.observer.join()
            self.monitoring = False
            console.print("[green]Monitor stopped.[/green]")
            input("\nPress Enter to continue...")

    def view_quarantine(self):
        """View quarantined files with path structure"""
        while True:
            console.clear()
            console.print("\n[bold]Quarantine Folder[/bold]\n")

            quarantine_path = self.config.get("quarantine_path")
            if not os.path.exists(quarantine_path):
                console.print("[yellow]Quarantine folder is empty[/yellow]")
                input("\nPress Enter to continue...")
                return
            else:
                # Count files and show structure
                total_files = 0
                total_size = 0
                file_tree = {}

                for root, dirs, files in os.walk(quarantine_path):
                    for file in files:
                        if not file.endswith('.restore_info'):
                            total_files += 1
                            file_path = os.path.join(root, file)
                            total_size += os.path.getsize(file_path)

                            # Build tree structure
                            rel_path = os.path.relpath(file_path, quarantine_path)
                            parts = rel_path.split(os.sep)

                            if len(parts) >= 2:  # Has date and possibly more structure
                                date = parts[0]
                                if date not in file_tree:
                                    file_tree[date] = []
                                file_tree[date].append(os.sep.join(parts[1:]))

                console.print(f"Location: {quarantine_path}")
                console.print(f"Total files: {total_files}")
                console.print(f"Total size: {format_size(total_size)}")

                # Show recent quarantined files by date
                if file_tree:
                    console.print("\n[bold]Quarantined files by date:[/bold]")

                    # Sort dates in reverse order (newest first)
                    for date in sorted(file_tree.keys(), reverse=True)[:5]:  # Show last 5 days
                        console.print(f"\n[cyan]{date}:[/cyan]")
                        for file_path in file_tree[date][:10]:  # Show up to 10 files per date
                            console.print(f"  → {file_path}")

                        if len(file_tree[date]) > 10:
                            console.print(f"  [dim]... and {len(file_tree[date]) - 10} more files[/dim]")

            # Options
            console.print("\n1. View restoration info for a file")
            console.print("2. Restore a file")
            console.print("3. Clean old quarantined files\n")

            console.print("0. Back\n")

            choice = Prompt.ask("Select option", choices=["1","2","3","0"], default="0")

            if choice == "1":
                self._view_restoration_info()
            elif choice == "2":
                self._restore_file()
            elif choice == "3":
                self._clean_old_quarantine()
            elif choice == "0":
                return

    def _view_restoration_info(self):
        """View restoration info for a specific file"""
        filename = Prompt.ask("\nEnter filename to check")

        quarantine_path = self.config.get("quarantine_path")
        found = False

        for root, dirs, files in os.walk(quarantine_path):
            for file in files:
                if file == filename and not file.endswith('.restore_info'):
                    info_file = os.path.join(root, file + '.restore_info')
                    if os.path.exists(info_file):
                        console.print(f"\n[bold]Restoration info for {filename}:[/bold]")
                        with open(info_file, 'r') as f:
                            console.print(f.read())
                        found = True
                        break

        if not found:
            console.print("[yellow]File not found in quarantine[/yellow]")

        input("\nPress Enter to continue...")

    def _restore_file(self):
        """Restore a quarantined file to its original location"""
        console.print("\n[yellow]Note: This will restore the file to its original location[/yellow]")
        filename = Prompt.ask("Enter filename to restore")

        quarantine_path = self.config.get("quarantine_path")
        found = False

        for root, dirs, files in os.walk(quarantine_path):
            for file in files:
                if file == filename and not file.endswith('.restore_info'):
                    quarantined_file = os.path.join(root, file)
                    info_file = quarantined_file + '.restore_info'

                    if os.path.exists(info_file):
                        # Read original path
                        with open(info_file, 'r') as f:
                            lines = f.readlines()
                            original_path = lines[0].replace('Original path: ', '').strip()

                        console.print(f"\nOriginal location: {original_path}")

                        if Confirm.ask("Restore this file?", default=False):
                            try:
                                # Create directory if needed
                                os.makedirs(os.path.dirname(original_path), exist_ok=True)

                                # Move file back
                                shutil.move(quarantined_file, original_path)

                                # Remove info file
                                os.remove(info_file)

                                console.print(f"[green]File restored to: {original_path}[/green]")
                                if self.handler:
                                    self.handler.logger.info(f"RESTORED: {quarantined_file} -> {original_path}")
                            except Exception as e:
                                console.print(f"[red]Error restoring file: {e}[/red]")
                                if self.handler:
                                    self.handler.logger.error(f"RESTORE FAILED: {quarantined_file} -> {original_path}: {e}")

                        found = True
                        break

        if not found:
            console.print("[yellow]File not found in quarantine[/yellow]")

        input("\nPress Enter to continue...")

    def _clean_old_quarantine(self):
        """Clean quarantined files older than configured days"""
        days = self.config.get("delete_after_days")
        if days == 0:
            console.print("[yellow]Auto-delete is disabled (set to 0 days)[/yellow]")
            input("\nPress Enter to continue...")
            return

        cutoff_date = datetime.now() - timedelta(days=days)
        console.print(f"\n[yellow]This will delete files quarantined before {cutoff_date.strftime('%Y-%m-%d')}[/yellow]")

        if not Confirm.ask("Continue?", default=False):
            return

        quarantine_path = self.config.get("quarantine_path")
        deleted_count = 0
        deleted_size = 0

        for root, dirs, files in os.walk(quarantine_path):
            for file in files:
                file_path = os.path.join(root, file)

                # Check file age
                mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                if mtime < cutoff_date:
                    try:
                        size = os.path.getsize(file_path)
                        os.remove(file_path)
                        deleted_count += 1
                        deleted_size += size
                        if self.handler:
                            self.handler.logger.info(f"CLEANED: {file_path} (age: {(datetime.now() - mtime).days} days)")
                    except Exception as e:
                        console.print(f"[red]Error deleting {file}: {e}[/red]")

        # Clean empty directories
        for root, dirs, files in os.walk(quarantine_path, topdown=False):
            if not dirs and not files and root != quarantine_path:
                try:
                    os.rmdir(root)
                except:
                    pass

        console.print(f"\n[green]Cleaned {deleted_count} files ({format_size(deleted_size)})[/green]")
        input("\nPress Enter to continue...")

    def view_statistics(self):
        """View monitoring statistics and logs"""
        while True:
            console.clear()
            console.print("\n[bold]Monitoring Statistics & Logs[/bold]\n")

            # Get session statistics if monitoring
            if self.monitoring and self.handler:
                stats = self.handler.get_statistics()

                # Display statistics table
                table = Table(show_header=False, box=None)
                table.add_column("Metric", style="cyan")
                table.add_column("Value")

                table.add_row("Session Started", stats["session_start"].strftime("%Y-%m-%d %H:%M:%S"))
                table.add_row("Uptime", str(stats["uptime"]).split('.')[0])  # Remove microseconds
                table.add_row("Files Checked", str(stats["files_checked"]))
                table.add_row("Duplicates Found", str(stats["duplicates_found"]))
                table.add_row("Success Rate", f"{stats['success_rate']:.1f}%")

                console.print(table)
                console.print("")

            # Log viewing options
            console.print("1. View recent activity (last 20 entries)")
            console.print("2. View errors and warnings only")
            console.print("3. View detailed debug log")
            console.print("4. Search logs")
            console.print("5. Export logs")
            console.print("6. Clear old logs\n")
            console.print("0. Back to main menu\n")

            choice = Prompt.ask("Select option", choices=["1","2","3","4","5","6","0"], case_sensitive=False)

            if choice == "1":
                self._view_recent_logs()
            elif choice == "2":
                self._view_error_logs()
            elif choice == "3":
                self._view_debug_logs()
            elif choice == "4":
                self._search_logs()
            elif choice == "5":
                self._export_logs()
            elif choice == "6":
                self._clear_old_logs()
            elif choice == "0":
                break

    def _view_recent_logs(self):
        """View recent log entries"""
        log_file = self.config.get("log_file")
        if not os.path.exists(log_file):
            console.print("[yellow]No log file found yet[/yellow]")
            input("\nPress Enter to continue...")
            return

        console.print("\n[bold]Recent Activity:[/bold]\n")

        # Read last 20 lines
        with open(log_file, 'r') as f:
            lines = f.readlines()

        for line in lines[-20:]:
            line = line.strip()
            if "ERROR" in line or "FAILED" in line:
                console.print(f"[red]{line}[/red]")
            elif "WARNING" in line:
                console.print(f"[yellow]{line}[/yellow]")
            elif "DUPLICATE CONFIRMED" in line or "QUARANTINED" in line:
                console.print(f"[green]{line}[/green]")
            elif "NO DUPLICATE" in line:
                console.print(f"[blue]{line}[/blue]")
            elif "DRY RUN" in line:
                console.print(f"[cyan]{line}[/cyan]")
            else:
                console.print(line)

        input("\nPress Enter to continue...")

    def _view_error_logs(self):
        """View only errors and warnings"""
        log_file = self.config.get("log_file")
        if not os.path.exists(log_file):
            console.print("[yellow]No log file found yet[/yellow]")
            input("\nPress Enter to continue...")
            return

        console.print("\n[bold]Errors and Warnings:[/bold]\n")

        error_count = 0
        warning_count = 0

        with open(log_file, 'r') as f:
            for line in f:
                if "ERROR" in line or "FAILED" in line:
                    console.print(f"[red]{line.strip()}[/red]")
                    error_count += 1
                elif "WARNING" in line:
                    console.print(f"[yellow]{line.strip()}[/yellow]")
                    warning_count += 1

        console.print(f"\nTotal: {error_count} errors, {warning_count} warnings")
        input("\nPress Enter to continue...")

    def _view_debug_logs(self):
        """View detailed debug information"""
        log_file = self.config.get("log_file")
        if not os.path.exists(log_file):
            console.print("[yellow]No log file found yet[/yellow]")
            input("\nPress Enter to continue...")
            return

        console.print("\n[bold]Debug Log (last 50 entries):[/bold]\n")

        with open(log_file, 'r') as f:
            lines = f.readlines()

        # Show last 50 lines with all details
        for line in lines[-50:]:
            console.print(line.strip())

        input("\nPress Enter to continue...")

    def _search_logs(self):
        """Search through logs"""
        search_term = Prompt.ask("\nEnter search term")

        log_file = self.config.get("log_file")
        if not os.path.exists(log_file):
            console.print("[yellow]No log file found yet[/yellow]")
            input("\nPress Enter to continue...")
            return

        console.print(f"\n[bold]Search results for '{search_term}':[/bold]\n")

        matches = 0
        with open(log_file, 'r') as f:
            for line in f:
                if search_term.lower() in line.lower():
                    console.print(line.strip())
                    matches += 1

        console.print(f"\nFound {matches} matches")
        input("\nPress Enter to continue...")

    def _export_logs(self):
        """Export logs to a file"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = f"duplicate_monitor_export_{timestamp}.log"

        log_file = self.config.get("log_file")
        if not os.path.exists(log_file):
            console.print("[yellow]No log file to export[/yellow]")
            input("\nPress Enter to continue...")
            return

        try:
            shutil.copy2(log_file, export_path)
            console.print(f"[green]Logs exported to: {export_path}[/green]")
        except Exception as e:
            console.print(f"[red]Export failed: {e}[/red]")

        input("\nPress Enter to continue...")

    def _clear_old_logs(self):
        """Clear old log entries"""
        if Confirm.ask("\nAre you sure you want to clear old logs?", default=False):
            log_file = self.config.get("log_file")

            # Backup current log first
            if os.path.exists(log_file):
                backup_name = log_file + ".backup"
                shutil.copy2(log_file, backup_name)
                console.print(f"[green]Backup created: {backup_name}[/green]")

                # Clear the log file
                with open(log_file, 'w') as f:
                    f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | INFO     | Log file cleared\n")

                console.print("[green]Log file cleared[/green]")
            else:
                console.print("[yellow]No log file to clear[/yellow]")

        input("\nPress Enter to continue...")


def main():
    """Main entry point with CLI argument support"""
    parser = argparse.ArgumentParser(
        description='Duplicate File Preventer - Prevents duplicate files from syncing to cloud storage'
    )
    parser.add_argument('--dry-run', '-d', action='store_true', 
                       help='Run in test mode without moving files')
    parser.add_argument('--config', '-c', 
                       help='Path to config file (default: platform-specific)')
    parser.add_argument('--start', '-s', action='store_true', 
                       help='Start monitoring immediately')
    args = parser.parse_args()

    console.print("\n[bold cyan]Duplicate File Preventer[/bold cyan]")
    console.print("Prevents duplicate files from syncing to cloud storage")
    console.print("Designed for Thunderbird FiltaQuilla attachment handling\n")

    # Create monitor with optional config path
    if args.config:
        # Use custom config file location
        custom_config = Config(config_file=args.config)
        monitor = DuplicateMonitor(custom_config)
    else:
        monitor = DuplicateMonitor()

    # Apply CLI arguments
    if args.dry_run:
        monitor.config.set('dry_run', True)
        console.print("[cyan]DRY RUN MODE ENABLED - No files will be moved[/cyan]\n")

    # Show config location on first run
    if not os.path.exists(monitor.config.config_file):
        console.print(f"[green]Creating configuration in:[/green]")
        console.print(f"  Config: {monitor.config.config_file}")
        console.print(f"  Logs: {monitor.config.get('log_file')}")
        console.print(f"  Quarantine: {monitor.config.get('quarantine_path')}\n")

    try:
        # Auto-start if requested
        if args.start:
            if monitor.config.get("watched_folders"):
                monitor.start_monitoring()
                console.print("\n[yellow]Press Ctrl+C to stop monitoring and return to menu[/yellow]")
                try:
                    while monitor.monitoring:
                        time.sleep(1)
                except KeyboardInterrupt:
                    monitor.stop_monitoring()
            else:
                console.print("[red]No folders configured to watch![/red]")
                console.print("Please configure folders first.\n")

        monitor.show_menu()
    except KeyboardInterrupt:
        console.print("\n\n[yellow]Interrupted by user[/yellow]")
        if monitor.monitoring:
            monitor.stop_monitoring()
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise
