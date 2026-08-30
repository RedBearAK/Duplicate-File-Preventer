"""
One-shot scan of existing files in the watched folders.

Walks every watched folder recursively, hands each file-N.ext that sits
next to a plain file.ext to the DuplicateProcessor, and returns a summary.
Progress is reported through an optional callback so a front end can show
a counter without the scanner knowing what a terminal is.
"""

import os

from duplicate_preventer.engine.rules import original_exists
from duplicate_preventer.engine.utils import has_duplicate_suffix
from duplicate_preventer.engine.events import Event
from duplicate_preventer.engine.processor import DuplicateProcessor, Counters


class ScanResult:

    __slots__ = ("scanned", "processed", "quarantined", "dry_run",
                 "unique", "errors", "skipped_folders", "per_folder")

    def __init__(self):
        self.scanned = 0            # files with the -N suffix seen
        self.processed = 0          # of those, ones with an original present
        self.quarantined = 0
        self.dry_run = 0
        self.unique = 0
        self.errors = 0
        self.skipped_folders = []   # configured but missing
        self.per_folder = {}        # folder -> processed count

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}


def scan_folders(config, events, folders=None, progress=None, logger=None):
    """
    Scan `folders` (default: config's watched_folders). `progress`, if
    given, is called as progress(scanned_so_far, current_path).
    """
    folders = list(folders if folders is not None else config.get("watched_folders", []))
    result = ScanResult()
    processor = DuplicateProcessor(config, events, Counters(), logger)

    for folder in folders:
        if not os.path.isdir(folder):
            result.skipped_folders.append(folder)
            continue

        folder_count = 0
        for root, _dirs, files in os.walk(folder):
            for filename in sorted(files):
                if not has_duplicate_suffix(filename):
                    continue
                result.scanned += 1
                file_path = os.path.join(root, filename)
                if progress:
                    progress(result.scanned, file_path)
                if not original_exists(file_path):
                    continue

                outcome = processor.process(file_path)
                result.processed += 1
                folder_count += 1
                if outcome == "quarantined":
                    result.quarantined += 1
                elif outcome == "dry_run":
                    result.dry_run += 1
                elif outcome == "unique":
                    result.unique += 1
                elif outcome == "error":
                    result.errors += 1

        result.per_folder[folder] = folder_count

    events.put(Event("scanned", "", f"{result.processed} processed, "
                     f"{result.quarantined} quarantined, {result.dry_run} dry-run"))
    return result


# End of file #
