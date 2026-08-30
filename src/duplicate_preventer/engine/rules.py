"""
Duplicate detection rules.

Pure decisions about files: is this a candidate, what might it duplicate,
does it in fact duplicate it. Nothing here moves a file or talks to a
human; it returns answers with reasons so callers can log or display them.
"""

import os
import hashlib

from duplicate_preventer.engine.utils import (
    base_name_for,
    format_time_window,
    file_creation_time,
    has_duplicate_suffix,
)


def find_candidates(file_path):
    """
    Files in the same directory that file-N.ext might duplicate: the plain
    file.ext first, then any other file sharing the stem (file-1.ext,
    file-2.ext ...). Missing or unreadable directories yield [].
    """
    filename = os.path.basename(file_path)
    dir_path = os.path.dirname(file_path)
    base_name = base_name_for(filename)
    stem = base_name.rsplit('.', 1)[0]

    candidates = []
    original_path = os.path.join(dir_path, base_name)
    if os.path.isfile(original_path):
        candidates.append(original_path)

    try:
        names = sorted(os.listdir(dir_path))
    except OSError:
        return candidates

    for name in names:
        if name == filename or name == base_name:
            continue
        if not name.startswith(stem):
            continue
        full_path = os.path.join(dir_path, name)
        if os.path.isfile(full_path):
            candidates.append(full_path)

    return candidates


def original_exists(file_path):
    """True when file-N.ext sits next to a plain file.ext."""
    base_name = base_name_for(os.path.basename(file_path))
    return os.path.isfile(os.path.join(os.path.dirname(file_path), base_name))


def file_hash(path, algorithm):
    hasher = hashlib.new(algorithm)
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def compare(file_path, candidate_path, config):
    """
    (is_duplicate, reason). Every enabled check must pass; the reason
    lists the checks that passed, or the first that failed.
    """
    checks_passed = []

    if config.get("check_size", True):
        file_size = os.path.getsize(file_path)
        candidate_size = os.path.getsize(candidate_path)
        if file_size != candidate_size:
            return False, f"size mismatch ({file_size} vs {candidate_size} bytes)"
        checks_passed.append(f"size matches ({file_size} bytes)")

    if config.get("check_time", False):
        time_window = int(config.get("time_window", 300))
        time_diff = abs(file_creation_time(file_path) - file_creation_time(candidate_path))
        if time_diff > time_window:
            return False, (f"time outside window ({time_diff:.1f}s > "
                           f"{format_time_window(time_window)})")
        checks_passed.append(f"time within {time_diff:.1f}s")

    if config.get("use_hash", False):
        algorithm = config.get("hash_algorithm", "sha256")
        if file_hash(file_path, algorithm) != file_hash(candidate_path, algorithm):
            return False, f"{algorithm} hash mismatch"
        checks_passed.append(f"{algorithm} hash matches")

    if not checks_passed:
        # Every check disabled: the name pattern alone is all we have.
        checks_passed.append("filename pattern only (all content checks disabled)")

    return True, "; ".join(checks_passed)


def find_duplicate_of(file_path, config):
    """
    (candidate_path, reason) for the first candidate that file_path
    duplicates, or (None, reason) explaining why none matched.
    """
    if not has_duplicate_suffix(file_path):
        return None, "filename has no -N suffix"

    candidates = find_candidates(file_path)
    if not candidates:
        return None, "no candidate originals in directory"

    failures = []
    for candidate in candidates:
        try:
            is_dup, reason = compare(file_path, candidate, config)
        except OSError as error:
            failures.append(f"{os.path.basename(candidate)}: {error}")
            continue
        if is_dup:
            return candidate, reason
        failures.append(f"{os.path.basename(candidate)}: {reason}")

    return None, "; ".join(failures)


# End of file #
