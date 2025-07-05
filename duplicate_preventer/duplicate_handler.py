"""
File system event handler for duplicate detection
Monitors file creation and checks for duplicates
"""
import os
import re
import hashlib
import shutil
import stat
import platform
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Set, Tuple, Optional
from logging.handlers import RotatingFileHandler

from watchdog.events import FileSystemEventHandler
from rich.console import Console

from .config import Config
from .utils import get_relative_path, is_potential_duplicate, format_time_window

console = Console()


class DuplicateHandler(FileSystemEventHandler):
    """Handles file system events and checks for duplicates"""
    def __init__(self, config: Config):
        self.config = config
        self.processed_files: Set[str] = set()
        self.file_hashes: Dict[str, str] = {}
        self.check_count = 0
        self.duplicate_count = 0
        self.session_start = datetime.now()
        self.setup_logging()

    def setup_logging(self):
        """Setup intelligent logging with rotation"""
        log_file = self.config.get("log_file")
        log_level = getattr(logging, self.config.get("log_level", "INFO"))
        max_bytes = self.config.get("log_max_size", 10) * 1024 * 1024  # MB to bytes
        backup_count = self.config.get("log_backup_count", 5)

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
        self.logger.info(f"Dry run mode: {'ENABLED' if self.config.get('dry_run', False) else 'DISABLED'}")

        # Format detection settings
        detection_parts = []
        if self.config.get('check_size'):
            detection_parts.append("Size=ON")
        else:
            detection_parts.append("Size=OFF")

        if self.config.get('check_time'):
            time_window = format_time_window(self.config.get('time_window'))
            detection_parts.append(f"Time=ON ({time_window})")
        else:
            detection_parts.append("Time=OFF")

        if self.config.get('use_hash'):
            detection_parts.append(f"Hash=ON ({self.config.get('hash_algorithm')})")
        else:
            detection_parts.append("Hash=OFF")

        self.logger.info(f"Detection: {', '.join(detection_parts)}")

    def on_created(self, event):
        """Handle new file creation events"""
        if event.is_directory:
            return

        file_path = event.src_path

        # Check if file matches duplicate pattern
        if is_potential_duplicate(file_path, self.config.get("file_patterns", [])):
            self.check_count += 1
            console.print(f"[yellow]Checking potential duplicate: {os.path.basename(file_path)}[/yellow]")
            self.logger.info(f"Potential duplicate detected: {file_path}")
            self._handle_duplicate(file_path)

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
        if self.config.get("check_size"):
            if file_size == original_size:
                checks_passed.append(f"size matches ({file_size} bytes)")
            else:
                reasons.append(f"size mismatch ({file_size} vs {original_size} bytes)")
                return False, "; ".join(reasons)

        # Time window check
        if self.config.get("check_time"):
            time_diff = abs(file_ctime - original_ctime)
            time_window = self.config.get("time_window")
            if time_diff <= time_window:
                checks_passed.append(f"time within {time_diff:.1f}s")
            else:
                time_window_str = format_time_window(time_window)
                reasons.append(f"time outside window ({time_diff:.1f}s > {time_window_str})")
                return False, "; ".join(reasons)

        # Hash check
        if self.config.get("use_hash"):
            if self._files_are_identical(original_path, file_path):
                checks_passed.append(f"{self.config.get('hash_algorithm')} hash matches")
            else:
                reasons.append(f"{self.config.get('hash_algorithm')} hash mismatch")
                return False, "; ".join(reasons)

        # All checks passed
        return True, "; ".join(checks_passed)

    def _files_are_identical(self, file1: str, file2: str) -> bool:
        """Compare files using hash"""
        hash_algo = self.config.get("hash_algorithm")

        def get_file_hash(filepath):
            hasher = hashlib.new(hash_algo)
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    hasher.update(chunk)
            return hasher.hexdigest()

        return get_file_hash(file1) == get_file_hash(file2)

    def _quarantine_file(self, file_path: str, reason: str):
        """Move file to quarantine folder with path preservation"""
        if self.config.get("dry_run", False):
            # Dry run mode - just log what would happen
            console.print(f"[cyan]DRY RUN: Would quarantine {os.path.basename(file_path)}[/cyan]")
            self.logger.info(f"DRY RUN - WOULD QUARANTINE: {file_path} (Reason: {reason})")
            return

        quarantine_base = self.config.get("quarantine_path")
        date_folder = datetime.now().strftime("%Y-%m-%d")

        # Find the relative path from a known cloud/base folder
        relative_path = get_relative_path(file_path, self.config.get("watched_folders", []))

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
