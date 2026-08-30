"""
Tests for the pure helpers in engine/utils.py.

tests/test_utils.py

These are the functions everything else leans on: the -N suffix pattern,
time-window parsing, and quarantine path derivation. A wrong answer here
quietly becomes a wrong quarantine decision, so the edge cases are spelled
out rather than assumed.

Runnable with pytest, but written to run standalone and report a score.
"""

import os

from _helpers import check, run_suite

from duplicate_preventer.engine.utils import (
    clean_path,
    format_size,
    base_name_for,
    is_cloud_folder,
    get_relative_path,
    parse_time_window,
    format_time_window,
    has_duplicate_suffix,
    is_potential_duplicate,
)


DEFAULT_PATTERNS = [r"(.+?)(-\d+)?(\.[^.]+)$"]


def test_duplicate_suffix_detection():
    """file-1.pdf yes; file.pdf, file-a.pdf, file-1 (no ext), 2024-01.pdf... careful."""
    print("\nTesting -N suffix detection...")
    passed = True

    yes = ["file-1.pdf", "report-12.xlsx", "a-0.txt", "my file-3.gz"]
    no = ["file.pdf", "file-a.pdf", "file-1", "file_1.pdf", "-1.pdf.bak"]

    for name in yes:
        passed &= check(has_duplicate_suffix(name), f"{name} detected",
                        f"{name} NOT detected")
    for name in no:
        passed &= check(not has_duplicate_suffix(name), f"{name} correctly ignored",
                        f"{name} wrongly detected")

    # Double extensions are NOT detected: the suffix must sit directly
    # before the last extension. Unchanged from the original tool; noted
    # here so a change shows up as a test failure, not a surprise.
    passed &= check(not has_duplicate_suffix("my file-3.tar.gz"),
                    "file-3.tar.gz not detected (double extension, by design)",
                    "file-3.tar.gz now detected - behavior changed")

    # A dated filename like 2024-01.pdf DOES match: the pattern can't tell
    # a date from a FiltaQuilla suffix. Document it so nobody "fixes" it
    # into silence without knowing the size/hash checks are the safety net.
    passed &= check(has_duplicate_suffix("2024-01.pdf"),
                    "2024-01.pdf matches (by design; content checks decide)",
                    "2024-01.pdf no longer matches - behavior changed")
    return passed


def test_base_name_for():
    print("\nTesting base name derivation...")
    passed = True
    cases = {
        "file-1.pdf": "file.pdf",
        "report-12.xlsx": "report.xlsx",
        "file.pdf": "file.pdf",
        "a-1-2.txt": "a-1.txt",
        "/some/dir/file-3.pdf": "file.pdf",
    }
    for name, expected in cases.items():
        got = base_name_for(name)
        passed &= check(got == expected, f"{name} -> {got}",
                        f"{name} -> {got}, expected {expected}")
    return passed


def test_is_potential_duplicate_uses_patterns_and_suffix():
    print("\nTesting is_potential_duplicate...")
    passed = True
    passed &= check(is_potential_duplicate("/x/file-1.pdf", DEFAULT_PATTERNS),
                    "file-1.pdf with default pattern", "file-1.pdf rejected")
    passed &= check(not is_potential_duplicate("/x/file.pdf", DEFAULT_PATTERNS),
                    "file.pdf rejected", "file.pdf accepted")
    passed &= check(not is_potential_duplicate("/x/file-1.pdf", []),
                    "empty pattern list matches nothing", "empty pattern list matched")
    passed &= check(not is_potential_duplicate("/x/file-1.pdf", [r"^zzz"]),
                    "non-matching pattern rejects", "non-matching pattern accepted")
    return passed


def test_time_window_round_trip():
    print("\nTesting time window parsing and formatting...")
    passed = True
    cases = {
        "5m": 300, "2h": 7200, "3d": 259200, "1w": 604800, "2mo": 5184000,
        "1y": 31536000, "90s": 90, "1.5h": 5400, " 10 min ": 600, "2 Hours": 7200,
    }
    for text, expected in cases.items():
        got = parse_time_window(text)
        passed &= check(got == expected, f"{text!r} -> {got}",
                        f"{text!r} -> {got}, expected {expected}")

    for bad in ["", "5", "m5", "5x", "five minutes", "5 m 3 s"]:
        passed &= check(parse_time_window(bad) is None, f"{bad!r} rejected",
                        f"{bad!r} accepted as {parse_time_window(bad)}")

    for seconds, text in [(300, "5m"), (7200, "2h"), (45, "45s"), (259200, "3d"),
                          (604800, "1w"), (2592000, "1mo"), (31536000, "1y")]:
        passed &= check(format_time_window(seconds) == text,
                        f"{seconds}s -> {text}",
                        f"{seconds}s -> {format_time_window(seconds)}, expected {text}")
    return passed


def test_format_size():
    print("\nTesting size formatting...")
    passed = True
    for size, expected in [(0, "0.00 B"), (1023, "1023.00 B"), (1024, "1.00 KB"),
                           (1536, "1.50 KB"), (1024 ** 2, "1.00 MB"),
                           (1024 ** 4 * 2, "2.00 TB")]:
        got = format_size(size)
        passed &= check(got == expected, f"{size} -> {got}", f"{size} -> {got}, expected {expected}")
    return passed


def test_clean_path():
    print("\nTesting drag-and-drop path cleanup...")
    passed = True
    home = os.path.expanduser("~")
    cases = {
        '"/a/b c"': "/a/b c",
        "'/a/b'": "/a/b",
        "/a/b\\ c\\ d": "/a/b c d",
        "  /a/b  ": "/a/b",
        "~/x": os.path.join(home, "x"),
        '"': '"',            # a lone quote is not a quoted string
    }
    for raw, expected in cases.items():
        got = clean_path(raw)
        passed &= check(got == expected, f"{raw!r} -> {got!r}",
                        f"{raw!r} -> {got!r}, expected {expected!r}")
    return passed


def test_relative_path_preference_order():
    """Watched folder beats cloud folder beats home beats nothing."""
    print("\nTesting quarantine relative path derivation...")
    passed = True
    sep = os.sep
    home = os.path.expanduser("~")

    watched = sep.join(["", "srv", "mail", "attachments"])
    inside = sep.join([watched, "2026", "aug", "file-1.pdf"])
    got = get_relative_path(inside, [watched])
    expected = sep.join(["attachments", "2026", "aug"])
    passed &= check(got == expected, f"watched: {got}", f"watched: {got}, expected {expected}")

    top = sep.join([watched, "file-1.pdf"])
    got = get_relative_path(top, [watched])
    passed &= check(got == "attachments", f"watched root: {got}",
                    f"watched root: {got}, expected 'attachments'")

    cloud = sep.join(["", "Users", "me", "Dropbox", "Inbox", "file-1.pdf"])
    got = get_relative_path(cloud, [])
    expected = sep.join(["Dropbox", "Inbox"])
    passed &= check(got == expected, f"cloud: {got}", f"cloud: {got}, expected {expected}")

    in_home = os.path.join(home, "Stuff", "Here", "file-1.pdf")
    got = get_relative_path(in_home, [])
    expected = os.path.join("Stuff", "Here")
    passed &= check(got == expected, f"home: {got}", f"home: {got}, expected {expected}")

    got = get_relative_path(os.path.join(home, "file-1.pdf"), [])
    passed &= check(got is None, "home root -> None", f"home root -> {got}")

    got = get_relative_path(sep.join(["", "tmp", "file-1.pdf"]), [])
    passed &= check(got is None, "unrelated -> None", f"unrelated -> {got}")
    return passed


def test_is_cloud_folder():
    print("\nTesting cloud folder detection...")
    passed = True
    for path, expected in [("/Users/me/Dropbox/x", True), ("/home/me/OneDrive", True),
                           ("/Users/me/Library/Mobile Documents/iCloud~x", True),
                           ("/home/me/Google Drive/a", True), ("/home/me/work", False)]:
        got = is_cloud_folder(path)
        passed &= check(got == expected, f"{path} -> {got}", f"{path} -> {got}, expected {expected}")
    return passed


def main():
    return run_suite("utils tests", [
        test_duplicate_suffix_detection,
        test_base_name_for,
        test_is_potential_duplicate_uses_patterns_and_suffix,
        test_time_window_round_trip,
        test_format_size,
        test_clean_path,
        test_relative_path_preference_order,
        test_is_cloud_folder,
    ])


if __name__ == '__main__':
    exit(0 if main() else 1)


# End of file #
