"""
Quarantine: move a duplicate aside, never delete it.

Layout under the quarantine root:

    <root>/<YYYY-MM-DD>/<relative path from watched or cloud folder>/<file>
    ...plus a sidecar <file>.restore_info recording where it came from.

Every function returns data or raises; nothing prints.
"""

import os
import shutil

from datetime import datetime, timedelta

from duplicate_preventer.engine.utils import get_relative_path


RESTORE_SUFFIX = '.restore_info'
README_NAME = "_ABOUT_THIS_FOLDER.txt"
README_TEXT = """This folder is maintained by Duplicate File Preventer.

Files here were found next to an original with the same name minus a -1/-2
suffix, judged identical by the configured checks, and moved aside so they
would not sync to cloud storage. Nothing here is deleted automatically unless
you set an auto-delete age in the tool's settings.

Each file has a sidecar ending in .restore_info recording where it came from.
To put a file back, run `duplicate-file-preventer`, open View quarantine,
and choose Restore. Deleting files here by hand is safe; the originals remain
where they were.
"""


class QuarantineError(Exception):
    """A move, restore or cleanup could not be completed."""


def destination_for(file_path, config, now=None):
    """Where file_path would land in quarantine (collision-free)."""
    now = now or datetime.now()
    quarantine_base = config.get("quarantine_path")
    date_folder = now.strftime("%Y-%m-%d")
    relative_path = get_relative_path(file_path, config.get("watched_folders", []))

    if relative_path:
        quarantine_dir = os.path.join(quarantine_base, date_folder, relative_path)
    else:
        quarantine_dir = os.path.join(quarantine_base, date_folder)

    filename = os.path.basename(file_path)
    dest_path = os.path.join(quarantine_dir, filename)

    counter = 1
    name, ext = os.path.splitext(filename)
    while os.path.exists(dest_path):
        dest_path = os.path.join(quarantine_dir, f"{name}_{counter}{ext}")
        counter += 1

    return dest_path


def ensure_readme(config):
    """Write the folder explainer once; never overwrite a user's edits."""
    root = config.get("quarantine_path")
    path = os.path.join(root, README_NAME)
    if os.path.exists(path):
        return
    try:
        os.makedirs(root, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(README_TEXT)
    except OSError:
        pass


def quarantine_file(file_path, reason, config):
    """
    Put file_path into quarantine and write its restore sidecar.
    Returns (dest_path, size_bytes). Raises QuarantineError on failure.

    quarantine_method "copy_delete" (default) copies the file to quarantine,
    verifies the size, then deletes the original: to a cloud sync client the
    file was deleted, not moved out of the synced folder, so Dropbox's
    "moved out of Dropbox" prompt never fires. "move" renames it instead.
    """
    dest_path = destination_for(file_path, config)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    ensure_readme(config)
    method = str(config.get("quarantine_method", "move")).lower()

    try:
        file_size = os.path.getsize(file_path)
        if method == "copy_delete":
            shutil.copy2(file_path, dest_path)
            if os.path.getsize(dest_path) != file_size:
                os.remove(dest_path)
                raise QuarantineError(f"copy size mismatch, original kept: {file_path}")
            os.remove(file_path)
        else:
            shutil.move(file_path, dest_path)
    except PermissionError as error:
        raise QuarantineError(f"permission denied: {file_path}") from error
    except OSError as error:
        raise QuarantineError(f"{file_path}: {error}") from error

    with open(dest_path + RESTORE_SUFFIX, 'w', encoding='utf-8') as handle:
        handle.write(f"Original path: {file_path}\n")
        handle.write(f"Quarantined: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        handle.write(f"Reason: {reason}\n")
        handle.write(f"Size: {file_size} bytes\n")

    return dest_path, file_size


def read_restore_info(quarantined_path):
    """Sidecar contents as a dict, or None when the sidecar is missing."""
    info_path = quarantined_path + RESTORE_SUFFIX
    if not os.path.exists(info_path):
        return None
    info = {}
    with open(info_path, 'r', encoding='utf-8') as handle:
        for line in handle:
            if ': ' in line:
                key, value = line.rstrip('\n').split(': ', 1)
                info[key] = value
    return info


def list_quarantine(config):
    """
    Every quarantined file as a dict: path, name, size, date (the top-level
    date folder), rel (path under that date folder). Sidecars excluded.
    """
    root = config.get("quarantine_path")
    entries = []
    if not os.path.isdir(root):
        return entries

    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(RESTORE_SUFFIX) or (dirpath == root and name == README_NAME):
                continue
            path = os.path.join(dirpath, name)
            rel_path = os.path.relpath(path, root)
            parts = rel_path.split(os.sep)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            entries.append({
                "path": path,
                "name": name,
                "size": size,
                "date": parts[0] if len(parts) >= 2 else "",
                "rel": os.sep.join(parts[1:]) if len(parts) >= 2 else rel_path,
            })

    entries.sort(key=lambda e: (e["date"], e["rel"]))
    return entries


def count_quarantined(config):
    """Number of quarantined files on disk (used once at engine start)."""
    return len(list_quarantine(config))


def find_quarantined(config, filename):
    """First quarantined file with this basename, or None."""
    for entry in list_quarantine(config):
        if entry["name"] == filename:
            return entry
    return None


def restore_file(quarantined_path):
    """
    Move a quarantined file back to the path recorded in its sidecar.
    Returns the restored path. Raises QuarantineError if that's impossible.
    """
    info = read_restore_info(quarantined_path)
    if not info or "Original path" not in info:
        raise QuarantineError(f"no restore info for {quarantined_path}")

    original_path = info["Original path"]
    if os.path.exists(original_path):
        raise QuarantineError(f"a file already exists at {original_path}")

    try:
        os.makedirs(os.path.dirname(original_path), exist_ok=True)
        shutil.move(quarantined_path, original_path)
        os.remove(quarantined_path + RESTORE_SUFFIX)
    except OSError as error:
        raise QuarantineError(f"restore failed: {error}") from error

    return original_path


def clean_old(config, days=None, now=None):
    """
    Delete quarantined files (and sidecars) older than `days`, then prune
    empty directories. Returns (deleted_count, deleted_bytes, errors).
    days=0 means never; nothing is touched.
    """
    days = config.get("delete_after_days", 30) if days is None else days
    if not days or days <= 0:
        return 0, 0, []

    root = config.get("quarantine_path")
    if not os.path.isdir(root):
        return 0, 0, []

    cutoff = (now or datetime.now()) - timedelta(days=days)
    deleted_count = 0
    deleted_bytes = 0
    errors = []

    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if dirpath == root and name == README_NAME:
                continue
            path = os.path.join(dirpath, name)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(path))
                if mtime >= cutoff:
                    continue
                size = os.path.getsize(path)
                os.remove(path)
            except OSError as error:
                errors.append(f"{path}: {error}")
                continue
            if not name.endswith(RESTORE_SUFFIX):
                deleted_count += 1
                deleted_bytes += size

    # Bottom-up so a parent is re-checked after its children are removed;
    # os.walk's `dirs` list is stale by then, hence the live listdir().
    for dirpath, _dirs, _files in os.walk(root, topdown=False):
        if dirpath == root:
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
        except OSError:
            pass

    return deleted_count, deleted_bytes, errors


# End of file #
