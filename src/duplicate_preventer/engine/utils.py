"""
Shared helpers for the engine and the front ends.

Pure functions only: no console output, no state. Anything here must be
safe to import from a watchdog thread or from a test with no UI installed.
"""

import os
import re

from pathlib import Path


CLOUD_FOLDER_NAMES = ("Dropbox", "OneDrive", "Google Drive", "iCloud Drive")
CLOUD_INDICATORS = ("Dropbox", "OneDrive", "iCloud", "Google Drive")

# file-1.pdf, report-12.xlsx ... the FiltaQuilla duplicate naming pattern.
DUPLICATE_SUFFIX_RE = re.compile(r'-\d+(\.[^.]+)$')


def clean_path(path):
    """Clean up a path from drag-and-drop or copy-paste: quotes, escapes, ~."""
    path = path.strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in ('"', "'"):
        path = path[1:-1]
    path = path.replace('\\ ', ' ')
    return os.path.expanduser(path)


def get_relative_path(file_path, watched_folders):
    """
    Directory of file_path expressed relative to a known base, for
    structure-preserving quarantine. Returns None when no base applies.

    Preference order: a watched folder, a cloud folder name found in the
    path, the user's home directory.
    """
    for watched in watched_folders:
        if file_path.startswith(watched):
            rel_path = os.path.relpath(os.path.dirname(file_path), watched)
            folder_name = os.path.basename(watched)
            if rel_path == ".":
                return folder_name
            return os.path.join(folder_name, rel_path)

    path_parts = file_path.split(os.sep)
    for i, part in enumerate(path_parts):
        if part in CLOUD_FOLDER_NAMES:
            return os.sep.join(path_parts[i:-1])

    home = str(Path.home())
    if file_path.startswith(home):
        rel_from_home = os.path.relpath(os.path.dirname(file_path), home)
        if rel_from_home != ".":
            return rel_from_home

    return None


def has_duplicate_suffix(filename):
    """True for names like file-1.ext, file-23.ext."""
    return DUPLICATE_SUFFIX_RE.search(os.path.basename(filename)) is not None


def is_potential_duplicate(file_path, file_patterns):
    """
    Filename matches one of the configured patterns AND carries the -N
    suffix. The patterns list is kept for config compatibility; the suffix
    is what actually decides.
    """
    filename = os.path.basename(file_path)
    for pattern in file_patterns:
        if re.match(pattern, filename) and has_duplicate_suffix(filename):
            return True
    return False


def base_name_for(filename):
    """file-2.pdf -> file.pdf. Unchanged when there is no -N suffix."""
    return DUPLICATE_SUFFIX_RE.sub(r'\1', os.path.basename(filename))


def format_size(size_bytes):
    """Human-readable byte count."""
    size = float(size_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"


def is_cloud_folder(path):
    """True when the path looks like it lives inside a cloud sync folder."""
    return any(indicator in path for indicator in CLOUD_INDICATORS)


_TIME_UNITS = {
    's': 1, 'sec': 1, 'second': 1, 'seconds': 1,
    'm': 60, 'min': 60, 'minute': 60, 'minutes': 60,
    'h': 3600, 'hr': 3600, 'hour': 3600, 'hours': 3600,
    'd': 86400, 'day': 86400, 'days': 86400,
    'w': 604800, 'wk': 604800, 'week': 604800, 'weeks': 604800,
    'mo': 2592000, 'month': 2592000, 'months': 2592000,      # 30 days
    'y': 31536000, 'yr': 31536000, 'year': 31536000, 'years': 31536000,
}


def parse_time_window(time_str):
    """'5m', '2h', '3d', '1w', '2mo', '1y' -> seconds, or None if invalid."""
    match = re.match(r'^(\d+\.?\d*)\s*([a-z]+)$', time_str.strip().lower())
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    if unit not in _TIME_UNITS:
        return None
    return int(value * _TIME_UNITS[unit])


def format_time_window(seconds):
    """Seconds -> the largest whole unit that fits: 300 -> '5m'."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    if seconds < 604800:
        return f"{seconds // 86400}d"
    if seconds < 2592000:
        return f"{seconds // 604800}w"
    if seconds < 31536000:
        return f"{seconds // 2592000}mo"
    return f"{seconds // 31536000}y"


def file_creation_time(path):
    """
    Best-effort creation time. Windows ctime is creation; on POSIX ctime is
    the inode change time, so take the earlier of ctime and mtime.
    """
    stat_info = os.stat(path)
    if os.name == 'nt':
        return stat_info.st_ctime
    return min(stat_info.st_ctime, stat_info.st_mtime)


# End of file #
