"""
Utility functions for Duplicate File Preventer
Shared functions used across modules
"""
import os
import re
from pathlib import Path
from typing import Optional


def clean_path(path: str) -> str:
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


def get_relative_path(file_path: str, watched_folders: list) -> Optional[str]:
    """Get relative path from known base folders (Dropbox, etc.)"""
    # Common cloud folder names to look for
    cloud_folders = ["Dropbox", "OneDrive", "Google Drive", "iCloud Drive"]
    
    # Check watched folders first
    for watched in watched_folders:
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


def is_potential_duplicate(file_path: str, file_patterns: list) -> bool:
    """Check if filename matches duplicate patterns (like file-1.ext, file-2.ext)"""
    filename = os.path.basename(file_path)
    
    # Check against patterns
    for pattern in file_patterns:
        match = re.match(pattern, filename)
        if match:
            # Check for -1, -2 suffix pattern specifically
            if re.search(r'-\d+\.[^.]+$', filename):
                return True
    
    return False


def format_size(size_bytes: int) -> str:
    """Format bytes into human-readable size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def is_cloud_folder(path: str) -> bool:
    """Check if path is inside a cloud sync folder"""
    cloud_indicators = ["Dropbox", "OneDrive", "iCloud", "Google Drive"]
    return any(indicator in path for indicator in cloud_indicators)


def parse_time_window(time_str: str) -> Optional[int]:
    """Parse time window string like '5m', '2h', '3d' into seconds
    
    Supported units:
    - s/sec/seconds
    - m/min/minutes  
    - h/hr/hour/hours
    - d/day/days
    - w/wk/week/weeks
    - mo/month/months (30 days)
    - y/yr/year/years (365 days)
    
    Returns seconds as integer, or None if invalid format
    """
    time_str = time_str.strip().lower()
    match = re.match(r'^(\d+\.?\d*)\s*([a-z]+)$', time_str)
    
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2)

    units = {
        's': 1, 'sec': 1, 'second': 1, 'seconds': 1,
        'm': 60, 'min': 60, 'minute': 60, 'minutes': 60,
        'h': 3600, 'hr': 3600, 'hour': 3600, 'hours': 3600,
        'd': 86400, 'day': 86400, 'days': 86400,
        'w': 604800, 'wk': 604800, 'week': 604800, 'weeks': 604800,
        'mo': 2592000, 'month': 2592000, 'months': 2592000,  # 30 days
        'y': 31536000, 'yr': 31536000, 'year': 31536000, 'years': 31536000  # 365 days
    }

    return int(value * units[unit]) if unit in units else None


def format_time_window(seconds: int) -> str:
    """Format seconds into human-readable time string"""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    elif seconds < 86400:
        return f"{seconds // 3600}h"
    elif seconds < 604800:
        return f"{seconds // 86400}d"
    elif seconds < 2592000:
        return f"{seconds // 604800}w"
    elif seconds < 31536000:
        return f"{seconds // 2592000}mo"
    else:
        return f"{seconds // 31536000}y"
