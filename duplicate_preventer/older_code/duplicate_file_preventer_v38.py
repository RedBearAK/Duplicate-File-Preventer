#!/usr/bin/env python3
"""
Duplicate File Preventer - Automatic duplicate removal for Thunderbird FiltaQuilla
Prevents duplicate files from syncing to cloud storage by monitoring and quarantining
"""

import json
import os
import re
import hashlib
import shutil
import stat
import platform
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional, Tuple
import time
import logging
from logging.handlers import RotatingFileHandler

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from rich.console import Console
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich import print as rprint

console = Console()

class Config:
    """Manages configuration with interactive updates"""
    def __init__(self, config_file=None):
        # Platform-specific config locations
        if config_file is None:
            self.config_dir = self._get_config_dir()
            self.config_file = os.path.join(self.config_dir, "duplicate_monitor.json")
        else:
            self.config_file = config_file
            self.config_dir = os.path.dirname(config_file)
        
        # Ensure config directory exists
        os.makedirs(self.config_dir, exist_ok=True)
        
        self.default_config = {
            "watched_folders": [],
            "quarantine_path": self._get_default_quarantine_path(),
            "check_interval": 5,  # seconds
            "use_hash": False,  # Hash verification OFF by default
            "hash_algorithm": "sha256",
            "time_window": 300,  # 5 minutes in seconds
            "check_time": True,  # Check creation time
            "check_size": True,  # Check file size
            "file_patterns": [r"(.+?)(-\d+)?(\.[^.]+)$"],  # matches file-1.ext
            "log_file": os.path.join(self.config_dir, "duplicate_monitor.log"),
            "log_level": "INFO",  # DEBUG, INFO, WARNING, ERROR
            "log_max_size": 10,  # MB
            "log_backup_count": 5,
            "delete_after_days": 30,
            "dry_run": False,  # Dry run mode
            "enabled": True
        }
        self.config = self.load_config()
        
    def _get_config_dir(self) -> str:
        """Get platform-specific configuration directory"""
        if platform.system() == "Windows":
            # Windows: %APPDATA%\DuplicateMonitor
            base = os.environ.get('APPDATA', os.path.expanduser('~'))
            return os.path.join(base, 'DuplicateMonitor')
        elif platform.system() == "Darwin":
            # macOS: ~/Library/Application Support/DuplicateMonitor
            return os.path.expanduser('~/Library/Application Support/DuplicateMonitor')
        else:
            # Linux: ~/.config/duplicate-monitor
            xdg_config = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
            return os.path.join(xdg_config, 'duplicate-monitor')
    
    def _get_default_quarantine_path(self) -> str:
        """Get default quarantine path outside of common cloud folders"""
        home = Path.home()
        
        # Try to detect and avoid cloud folders
        if platform.system() == "Windows":
            # Windows: Use Documents\Quarantined_Duplicates if not in OneDrive
            docs = home / "Documents"
            if "OneDrive" not in str(docs):
                return str(docs / "Quarantined_Duplicates")
        elif platform.system() == "Darwin":
            # macOS: Use ~/Quarantined_Duplicates (outside of iCloud Documents)
            return str(home / "Quarantined_Duplicates")
        
        # Default: Home directory
        return str(home / "Quarantined_Duplicates")
    
    def load_config(self) -> dict:
        """Load configuration from file or create default"""
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                loaded = json.load(f)
                # Merge with defaults to add any new keys
                for key, value in self.default_config.items():
                    if key not in loaded:
                        loaded[key] = value
                return loaded
        return self.default_config.copy()
    
    def save_config(self):
        """Save current configuration to file"""
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)
        console.print(f"[green]Configuration saved to {self.config_file}[/green]")

class DuplicateHandler(FileSystemEventHandler):
    """Handles file system events and checks for duplicates"""
    def __init__(self, config: Config):
        self.config = config
        self.processed_files: Set[str] = set()
        self.file_hashes: Dict[str, str] = {}
        self.setup_logging()
        self.check_count = 0
        self.duplicate_count = 0
        self.session_start = datetime.now()
        
    def setup_logging(self):
        """Setup intelligent logging with rotation"""
        log_file = self.config.config["log_file"]
        log_level = getattr(logging, self.config.config.get("log_level", "INFO"))
        max_bytes = self.config.config.get("log_max_size", 10) * 1024 * 1024  # MB to bytes
        backup_count = self.config.config.get("log_backup_count", 5)
        
        # Create logger
        self.logger = logging.getLogger('DuplicatePreventer')
        self.logger.setLevel(log_level)
        
        # Remove existing handlers
        self.logger.handlers.clear()
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # File handler with rotation
        file_handler = RotatingFileHandler(
            log_file, 
            maxBytes=max_bytes,
            backupCount=backup_count
        )
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
        
        # Also log to console if in DEBUG mode
        if log_level == logging.DEBUG:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)
        
        self.logger.info("="*60)
        self.logger.info(f"Duplicate File Preventer started - Session: {self.session_start}")
        self.logger.info(f"Config: {self.config.config_file}")
        self.logger.info(f"Dry run mode: {'ENABLED' if self.config.config.get('dry_run', False) else 'DISABLED'}")
        self.logger.info(f"Detection: Size={self.config.config['check_size']}, "
                        f"Time={self.config.config['check_time']} ({self.config.config['time_window']}s window), "
                        f"Hash={self.config.config['use_hash']}")
        
    def on_created(self, event):
        """Handle new file creation events"""
        if event.is_directory:
            return
            
        file_path = event.src_path
        
        # Check if file matches duplicate pattern
        if self._is_potential_duplicate(file_path):
            self.check_count += 1
            console.print(f"[yellow]Checking potential duplicate: {os.path.basename(file_path)}[/yellow]")
            self.logger.info(f"Potential duplicate detected: {file_path}")
            self._handle_duplicate(file_path)
    
    def _is_potential_duplicate(self, file_path: str) -> bool:
        """Check if filename matches duplicate patterns (like file-1.ext, file-2.ext)"""
        filename = os.path.basename(file_path)
        
        # Check against patterns
        for pattern in self.config.config["file_patterns"]:
            match = re.match(pattern, filename)
            if match:
                # Check for -1, -2 suffix pattern specifically
                if re.search(r'-\d+\.[^.]+$', filename):
                    return True
        
        return False
    
    def _handle_duplicate(self, file_path: str):
        """Process potential duplicate file with detailed logging"""
        filename = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        
        # Get creation time (platform-specific)
        if platform.system() == 'Windows':
            file_ctime = os.path.getctime(file_path)
        else:
            stat_info = os.stat(file_path)
            file_ctime = min(stat_info.st_ctime, stat_info.st_mtime)
        
        self.logger.info(f"Analyzing: {filename} (Size: {file_size} bytes, "
                        f"Created: {datetime.fromtimestamp(file_ctime).strftime('%Y-%m-%d %H:%M:%S')})")
        
        # Find potential original files
        base_name = re.sub(r'-\d+(\.[^.]+)$', r'\1', filename)
        dir_path = os.path.dirname(file_path)
        
        # Log search process
        self.logger.debug(f"Looking for original files with base name: {base_name}")
        
        # Build list of candidates
        candidates = []
        original_path = os.path.join(dir_path, base_name)
        if os.path.exists(original_path):
            candidates.append(original_path)
            
        # Look for other numbered versions
        for f in os.listdir(dir_path):
            if f != filename and f.startswith(base_name.rsplit('.', 1)[0]):
                full_path = os.path.join(dir_path, f)
                if os.path.isfile(full_path):
                    candidates.append(full_path)
        
        self.logger.debug(f"Found {len(candidates)} candidate files to compare")
        
        # Check each candidate
        duplicate_found = False
        for candidate in candidates:
            self.logger.debug(f"Comparing with: {os.path.basename(candidate)}")
            
            is_dup, reason = self._check_duplicate_with_reason(file_path, candidate, file_size, file_ctime)
            
            if is_dup:
                self.logger.info(f"DUPLICATE CONFIRMED: {filename} is duplicate of "
                               f"{os.path.basename(candidate)} ({reason})")
                self._quarantine_file(file_path, f"Duplicate of {os.path.basename(candidate)}")
                duplicate_found = True
                self.duplicate_count += 1
                break
            else:
                self.logger.debug(f"Not a duplicate of {os.path.basename(candidate)}: {reason}")
        
        if not duplicate_found:
            self.logger.info(f"NO DUPLICATE FOUND: {filename} appears to be unique")
    
    def _check_duplicate_with_reason(self, file_path: str, original_path: str, 
                                    file_size: int, file_ctime: float) -> Tuple[bool, str]:
        """Check if file is duplicate and return reason"""
        reasons = []
        checks_passed = []
        
        original_size = os.path.getsize(original_path)
        
        # Get original creation time
        if platform.system() == 'Windows':
            original_ctime = os.path.getctime(original_path)
        else:
            stat_info = os.stat(original_path)
            original_ctime = min(stat_info.st_ctime, stat_info.st_mtime)
        
        # Size check
        if self.config.config["check_size"]:
            if file_size == original_size:
                checks_passed.append(f"size matches ({file_size} bytes)")
            else:
                reasons.append(f"size mismatch ({file_size} vs {original_size} bytes)")
                return False, "; ".join(reasons)
        
        # Time window check
        if self.config.config["check_time"]:
            time_diff = abs(file_ctime - original_ctime)
            if time_diff <= self.config.config["time_window"]:
                checks_passed.append(f"time within {time_diff:.1f}s")
            else:
                reasons.append(f"time outside window ({time_diff:.1f}s > {self.config.config['time_window']}s)")
                return False, "; ".join(reasons)
        
        # Hash check
        if self.config.config["use_hash"]:
            if self._files_are_identical(original_path, file_path):
                checks_passed.append(f"{self.config.config['hash_algorithm']} hash matches")
            else:
                reasons.append(f"{self.config.config['hash_algorithm']} hash mismatch")
                return False, "; ".join(reasons)
        
        # All checks passed
        return True, "; ".join(checks_passed)
    
    def _is_duplicate(self, file_path: str, original_path: str, file_size: int, file_ctime: float) -> bool:
        """Check if file is a duplicate based on configured criteria"""
        is_dup, _ = self._check_duplicate_with_reason(file_path, original_path, file_size, file_ctime)
        return is_dup
    
    def _files_are_identical(self, file1: str, file2: str) -> bool:
        """Compare files using hash"""
        hash_algo = self.config.config["hash_algorithm"]
        
        def get_file_hash(filepath):
            hasher = hashlib.new(hash_algo)
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    hasher.update(chunk)
            return hasher.hexdigest()
        
        return get_file_hash(file1) == get_file_hash(file2)
    
    def _quarantine_file(self, file_path: str, reason: str):
        """Move file to quarantine folder with path preservation"""
        if self.config.config.get("dry_run", False):
            # Dry run mode - just log what would happen
            console.print(f"[cyan]DRY RUN: Would quarantine {os.path.basename(file_path)}[/cyan]")
            self.logger.info(f"DRY RUN - WOULD QUARANTINE: {file_path} (Reason: {reason})")
            return
            
        quarantine_base = self.config.config["quarantine_path"]
        date_folder = datetime.now().strftime("%Y-%m-%d")
        
        # Find the relative path from a known cloud/base folder
        relative_path = self._get_relative_path(file_path)
        
        # Create quarantine path preserving directory structure
        if relative_path:
            quarantine_path = os.path.join(quarantine_base, date_folder, relative_path)
        else:
            # Fallback to just date folder if we can't determine relative path
            quarantine_path = os.path.join(quarantine_base, date_folder)
        
        os.makedirs(quarantine_path, exist_ok=True)
        
        filename = os.path.basename(file_path)
        dest_path = os.path.join(quarantine_path, filename)
        
        # Handle existing files in quarantine
        counter = 1
        while os.path.exists(dest_path):
            name, ext = os.path.splitext(filename)
            dest_path = os.path.join(quarantine_path, f"{name}_{counter}{ext}")
            counter += 1
        
        try:
            # Get file size before moving
            file_size = os.path.getsize(file_path)
            
            # Move the file
            shutil.move(file_path, dest_path)
            
            # Log success
            console.print(f"[green]✓ Moved duplicate: {filename} → quarantine[/green]")
            self.logger.info(f"QUARANTINED: {file_path} -> {dest_path} "
                           f"(Size: {file_size} bytes, Reason: {reason})")
            
            # Create restoration info file
            info_path = dest_path + ".restore_info"
            with open(info_path, 'w') as f:
                f.write(f"Original path: {file_path}\n")
                f.write(f"Quarantined: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Reason: {reason}\n")
                f.write(f"Size: {file_size} bytes\n")
            
            # Track statistics
            self.processed_files.add(filename)
            
        except PermissionError:
            console.print(f"[red]Permission denied: {filename}[/red]")
            self.logger.error(f"FAILED - Permission denied: {file_path}")
        except Exception as e:
            console.print(f"[red]Error moving file: {e}[/red]")
            self.logger.error(f"FAILED - Error moving {file_path}: {str(e)}")
    
    def _get_relative_path(self, file_path: str) -> Optional[str]:
        """Get relative path from known base folders (Dropbox, etc.)"""
        # Common cloud folder names to look for
        cloud_folders = ["Dropbox", "OneDrive", "Google Drive", "iCloud Drive"]
        
        # Also check watched folders
        for watched in self.config.config["watched_folders"]:
            if file_path.startswith(watched):
                # Get the relative path from the watched folder
                rel_path = os.path.relpath(os.path.dirname(file_path), watched)
                # Include the watched folder name for context
                folder_name = os.path.basename(watched)
                if rel_path == ".":
                    return folder_name
                else:
                    return os.path.join(folder_name, rel_path)
        
        # Check for cloud folders in path
        path_parts = file_path.split(os.sep)
        for i, part in enumerate(path_parts):
            if part in cloud_folders:
                # Found a cloud folder, get path from there
                cloud_relative = os.sep.join(path_parts[i:-1])  # Exclude filename
                return cloud_relative
        
        # Check if file is in user's home directory
        home = str(Path.home())
        if file_path.startswith(home):
            # Get path relative to home, excluding filename
            dir_path = os.path.dirname(file_path)
            rel_from_home = os.path.relpath(dir_path, home)
            if rel_from_home != ".":
                return rel_from_home
        
        return None
    
    def get_statistics(self) -> dict:
        """Get current session statistics"""
        uptime = datetime.now() - self.session_start
        return {
            "session_start": self.session_start,
            "uptime": uptime,
            "files_checked": self.check_count,
            "duplicates_found": self.duplicate_count,
            "success_rate": (self.duplicate_count / self.check_count * 100) if self.check_count > 0 else 0
        }

class DuplicateMonitor:
    """Main application class"""
    def __init__(self):
        self.config = Config()
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
            dry_run = " [cyan](DRY RUN)[/cyan]" if self.config.config.get("dry_run", False) else ""
            console.print(f"Status: {status}{dry_run}\n")
            
            # Menu options
            console.print("1. 📁 Manage watched folders")
            console.print("2. ⚙️  Configure settings")
            console.print("3. 👁️  View current configuration")
            console.print("4. ▶️  Start monitoring" if not self.monitoring else "4. ⏸️  Stop monitoring")
            console.print("5. 🗑️  View quarantine")
            console.print("6. 📊 View logs & statistics")
            console.print("7. 💾 Save configuration")
            console.print("8. 🚪 Exit\n")
            
            choice = Prompt.ask("Select option", choices=["1","2","3","4","5","6","7","8"])
            
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
            elif choice == "8":
                if self.monitoring:
                    self.stop_monitoring()
                console.print("\n[cyan]Goodbye![/cyan]")
                break
    
    def manage_folders(self):
        """Manage watched folders with drag-and-drop support"""
        while True:
            console.clear()
            console.print("\n[bold]Watched Folders[/bold]\n")
            
            # Display current folders
            folders = self.config.config["watched_folders"]
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
            console.print("3. Edit folder path")
            console.print("4. Back to main menu\n")
            
            choice = Prompt.ask("Select option", choices=["1","2","3","4"])
            
            if choice == "1":
                console.print("\n[dim]Tip: You can drag and drop a folder here[/dim]")
                path = Prompt.ask("Enter folder path")
                
                # Clean up drag-and-drop paths
                path = self._clean_path(path)
                
                if os.path.exists(path) and os.path.isdir(path):
                    # Resolve to absolute path
                    path = os.path.abspath(path)
                    
                    if path not in folders:
                        folders.append(path)
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
                
            elif choice == "2":
                if folders:
                    idx = IntPrompt.ask("Enter folder number to remove", 
                                       default=0, show_default=False)
                    if 1 <= idx <= len(folders):
                        removed = folders.pop(idx - 1)
                        console.print(f"[green]Removed: {removed}[/green]")
                    else:
                        console.print("[red]Invalid number[/red]")
                    input("\nPress Enter to continue...")
                    
            elif choice == "3":
                if folders:
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
                        new_path = self._clean_path(new_path)
                        
                        if os.path.exists(new_path) and os.path.isdir(new_path):
                            new_path = os.path.abspath(new_path)
                            folders[idx - 1] = new_path
                            console.print(f"[green]Updated path to: {new_path}[/green]")
                        else:
                            console.print("[red]Invalid folder path - keeping original[/red]")
                    else:
                        console.print("[red]Invalid number[/red]")
                    input("\nPress Enter to continue...")
                    
            elif choice == "4":
                break
    
    def _clean_path(self, path: str) -> str:
        """Clean up paths from drag-and-drop or copy-paste"""
        # Remove quotes
        path = path.strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        elif path.startswith("'") and path.endswith("'"):
            path = path[1:-1]
        
        # Handle escaped spaces (replace '\ ' with ' ')
        path = path.replace('\\ ', ' ')
        
        # Expand ~ to home directory
        path = os.path.expanduser(path)
        
        return path
    
    def configure_settings(self):
        """Configure application settings"""
        console.clear()
        console.print("\n[bold]Configuration Settings[/bold]\n")
        
        # Dry run mode
        console.print("[bold]Test Mode:[/bold]")
        dry_run = Confirm.ask("Enable dry run mode? (test without moving files)", 
                             default=self.config.config.get("dry_run", False))
        self.config.config["dry_run"] = dry_run
        
        if dry_run:
            console.print("[cyan]Dry run enabled - no files will be moved[/cyan]")
        
        # Detection methods
        console.print("\n[bold]Detection Methods:[/bold]")
        
        # Size check
        check_size = Confirm.ask("Check file size?", 
                                default=self.config.config["check_size"])
        self.config.config["check_size"] = check_size
        
        # Time window check
        check_time = Confirm.ask("Check creation time?", 
                                default=self.config.config["check_time"])
        self.config.config["check_time"] = check_time
        
        if check_time:
            minutes = self.config.config["time_window"] // 60
            minutes = IntPrompt.ask("Time window for duplicates (minutes)", 
                                   default=minutes, show_default=True)
            self.config.config["time_window"] = minutes * 60
        
        # Hash verification (optional)
        use_hash = Confirm.ask("Enable hash verification? (more accurate but slower)", 
                               default=self.config.config["use_hash"])
        self.config.config["use_hash"] = use_hash
        
        if use_hash:
            console.print("\nHash algorithms: md5 (fast), sha256 (secure), sha512 (most secure)")
            algo = Prompt.ask("Select hash algorithm", 
                             default=self.config.config["hash_algorithm"],
                             choices=["md5", "sha1", "sha256", "sha512"])
            self.config.config["hash_algorithm"] = algo
        
        # Check interval
        console.print("\n[bold]Monitoring Settings:[/bold]")
        interval = IntPrompt.ask("File check interval (seconds)", 
                                default=self.config.config["check_interval"],
                                show_default=True)
        self.config.config["check_interval"] = interval
        
        # Quarantine settings
        console.print("\n[bold]Quarantine Settings:[/bold]")
        current_quarantine = self.config.config["quarantine_path"]
        console.print(f"Current quarantine path: {current_quarantine}")
        
        change_quarantine = Confirm.ask("Change quarantine location?", default=False)
        if change_quarantine:
            console.print("[dim]Tip: Choose a location outside of Dropbox/OneDrive/iCloud[/dim]")
            quarantine = Prompt.ask("Quarantine folder path",
                                   default=current_quarantine)
            quarantine = self._clean_path(quarantine)
            
            # Check if it's inside cloud folders
            cloud_indicators = ["Dropbox", "OneDrive", "iCloud", "Google Drive"]
            is_cloud = any(indicator in quarantine for indicator in cloud_indicators)
            
            if is_cloud:
                console.print("[yellow]⚠️  Warning: Quarantine folder appears to be in a cloud sync folder![/yellow]")
                console.print("[yellow]This will sync deleted files back to cloud storage.[/yellow]")
                if not Confirm.ask("Continue anyway?", default=False):
                    quarantine = current_quarantine
            
            self.config.config["quarantine_path"] = quarantine
        
        days = IntPrompt.ask("Delete quarantined files after (days, 0=never)",
                            default=self.config.config["delete_after_days"])
        self.config.config["delete_after_days"] = days
        
        # Logging settings
        console.print("\n[bold]Logging Settings:[/bold]")
        console.print("Log levels: DEBUG (verbose), INFO (normal), WARNING (important only)")
        log_level = Prompt.ask("Log level", 
                              default=self.config.config.get("log_level", "INFO"),
                              choices=["DEBUG", "INFO", "WARNING"])
        self.config.config["log_level"] = log_level
        
        max_size = IntPrompt.ask("Max log file size (MB)",
                                default=self.config.config.get("log_max_size", 10))
        self.config.config["log_max_size"] = max_size
        
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
        table.add_row("  Log File", self.config.config["log_file"])
        table.add_row("  Quarantine", self.config.config["quarantine_path"])
        
        # Check if quarantine is in cloud
        if any(cloud in self.config.config["quarantine_path"] 
               for cloud in ["Dropbox", "OneDrive", "iCloud", "Google Drive"]):
            table.add_row("", "[yellow]⚠️  Inside cloud sync folder[/yellow]")
        
        table.add_row("", "")  # Empty row for spacing
        
        # Monitoring
        table.add_row("Watched Folders", str(len(self.config.config["watched_folders"])))
        
        # Test mode
        if self.config.config.get("dry_run", False):
            table.add_row("Mode", "[cyan]DRY RUN (test mode)[/cyan]")
        
        # Detection methods
        table.add_row("", "")  # Empty row for spacing
        table.add_row("[bold]Detection Methods[/bold]", "")
        table.add_row("  File Size Check", "✓ Enabled" if self.config.config["check_size"] else "✗ Disabled")
        table.add_row("  Time Window Check", "✓ Enabled" if self.config.config["check_time"] else "✗ Disabled")
        if self.config.config["check_time"]:
            table.add_row("  Time Window", f"{self.config.config['time_window'] // 60} minutes")
        table.add_row("  Hash Verification", "✓ Enabled" if self.config.config["use_hash"] else "✗ Disabled")
        if self.config.config["use_hash"]:
            table.add_row("  Hash Algorithm", self.config.config["hash_algorithm"].upper())
        
        # Other settings
        table.add_row("", "")  # Empty row for spacing
        table.add_row("Check Interval", f"{self.config.config['check_interval']} seconds")
        table.add_row("Auto-delete After", f"{self.config.config['delete_after_days']} days" 
                      if self.config.config['delete_after_days'] > 0 else "Never")
        table.add_row("Log Level", self.config.config.get("log_level", "INFO"))
        table.add_row("Log Max Size", f"{self.config.config.get('log_max_size', 10)} MB")
        
        console.print(table)
        
        # Show watched folders if any
        if self.config.config["watched_folders"]:
            console.print("\n[bold]Watched Folders:[/bold]")
            for i, folder in enumerate(self.config.config["watched_folders"], 1):
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
        if not self.config.config["watched_folders"]:
            console.print("[red]No folders to watch! Add folders first.[/red]")
            input("\nPress Enter to continue...")
            return
        
        console.print("[yellow]Starting monitor...[/yellow]")
        
        if self.config.config.get("dry_run", False):
            console.print("[cyan]Running in DRY RUN mode - no files will be moved[/cyan]")
        
        # Create handler
        self.handler = DuplicateHandler(self.config)
        
        self.observer = Observer()
        for folder in self.config.config["watched_folders"]:
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
            
            quarantine_path = self.config.config["quarantine_path"]
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
                console.print(f"Total size: {total_size / 1024 / 1024:.2f} MB")
                
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
            console.print("3. Clean old quarantined files")
            console.print("4. Back\n")
            
            choice = Prompt.ask("Select option", choices=["1","2","3","4"], default="4")
            
            if choice == "1":
                self._view_restoration_info()
            elif choice == "2":
                self._restore_file()
            elif choice == "3":
                self._clean_old_quarantine()
            elif choice == "4":
                return
    
    def _view_restoration_info(self):
        """View restoration info for a specific file"""
        filename = Prompt.ask("\nEnter filename to check")
        
        quarantine_path = self.config.config["quarantine_path"]
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
        
        quarantine_path = self.config.config["quarantine_path"]
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
        days = self.config.config["delete_after_days"]
        if days == 0:
            console.print("[yellow]Auto-delete is disabled (set to 0 days)[/yellow]")
            input("\nPress Enter to continue...")
            return
        
        cutoff_date = datetime.now() - timedelta(days=days)
        console.print(f"\n[yellow]This will delete files quarantined before {cutoff_date.strftime('%Y-%m-%d')}[/yellow]")
        
        if not Confirm.ask("Continue?", default=False):
            return
        
        quarantine_path = self.config.config["quarantine_path"]
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
        
        console.print(f"\n[green]Cleaned {deleted_count} files ({deleted_size / 1024 / 1024:.2f} MB)[/green]")
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
            console.print("6. Clear old logs")
            console.print("7. Back to main menu\n")
            
            choice = Prompt.ask("Select option", choices=["1","2","3","4","5","6","7"])
            
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
            elif choice == "7":
                break
    
    def _view_recent_logs(self):
        """View recent log entries"""
        log_file = self.config.config["log_file"]
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
        log_file = self.config.config["log_file"]
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
        log_file = self.config.config["log_file"]
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
        
        log_file = self.config.config["log_file"]
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
        
        log_file = self.config.config["log_file"]
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
            log_file = self.config.config["log_file"]
            
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
    parser = argparse.ArgumentParser(description='Duplicate File Preventer - Prevents duplicate files from syncing to cloud storage')
    parser.add_argument('--dry-run', '-d', action='store_true', help='Run in test mode without moving files')
    parser.add_argument('--config', '-c', help='Path to config file (default: platform-specific)')
    parser.add_argument('--start', '-s', action='store_true', help='Start monitoring immediately')
    args = parser.parse_args()
    
    console.print("\n[bold cyan]Duplicate File Preventer[/bold cyan]")
    console.print("Prevents duplicate files from syncing to cloud storage")
    console.print("Designed for Thunderbird FiltaQuilla attachment handling\n")
    
    # Create monitor with optional config path
    if args.config:
        # Use custom config file location
        custom_config = Config(config_file=args.config)
        monitor = DuplicateMonitor()
        monitor.config = custom_config
    else:
        monitor = DuplicateMonitor()
    
    # Apply CLI arguments
    if args.dry_run:
        monitor.config.config['dry_run'] = True
        console.print("[cyan]DRY RUN MODE ENABLED - No files will be moved[/cyan]\n")
    
    # Show config location on first run
    if not os.path.exists(monitor.config.config_file):
        console.print(f"[green]Creating configuration in:[/green]")
        console.print(f"  Config: {monitor.config.config_file}")
        console.print(f"  Logs: {monitor.config.config['log_file']}")
        console.print(f"  Quarantine: {monitor.config.config['quarantine_path']}\n")
    
    try:
        # Auto-start if requested
        if args.start:
            if monitor.config.config["watched_folders"]:
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

if __name__ == "__main__":
    main()
