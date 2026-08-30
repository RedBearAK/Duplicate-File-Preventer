"""
Tests for engine/rules.py and engine/quarantine.py.

tests/test_rules_and_quarantine.py

Rules decide; quarantine moves. The cases that matter most are the ones
where a wrong answer loses data: a same-named-but-different file must NOT
be treated as a duplicate, a quarantined file must be restorable to
exactly where it came from, and cleanup must never delete anything
younger than the cutoff.

Runnable with pytest, but written to run standalone and report a score.
"""

import os
import time

from pathlib import Path
from datetime import datetime, timedelta

from _helpers import Sandbox, check, run_suite

from duplicate_preventer.engine.rules import (
    compare,
    find_candidates,
    original_exists,
    find_duplicate_of,
)
from duplicate_preventer.engine.quarantine import (
    clean_old,
    restore_file,
    list_quarantine,
    quarantine_file,
    destination_for,
    read_restore_info,
    QuarantineError,
)


def test_candidates_original_first_then_siblings():
    print("\nTesting candidate discovery...")
    with Sandbox() as box:
        original, dup = box.plant_pair("report.pdf", "report-2.pdf")
        sibling = box.plant("report-1.pdf")
        box.plant("reportage.pdf")          # shares the stem prefix: also a candidate
        box.plant("other.pdf")
        (box.watched / "report-3.pdf").mkdir()   # a directory with a matching name

        candidates = [os.path.basename(c) for c in find_candidates(dup)]
        passed = check(candidates and candidates[0] == "report.pdf",
                       "plain original comes first", f"order: {candidates}")
        passed &= check("report-1.pdf" in candidates, "numbered sibling included",
                        f"sibling missing: {candidates}")
        passed &= check("report-2.pdf" not in candidates, "the file itself excluded",
                        "file compared with itself")
        passed &= check("other.pdf" not in candidates, "unrelated file excluded",
                        "unrelated file included")
        passed &= check("report-3.pdf" not in candidates, "directory excluded",
                        "directory listed as candidate")
        passed &= check(original_exists(dup) and not original_exists(sibling + "x"),
                        "original_exists() agrees", "original_exists() disagrees")
        return passed


def test_compare_size_only():
    print("\nTesting size comparison...")
    with Sandbox() as box:
        original, dup = box.plant_pair(content=b"a" * 300)
        _o2, different = box.plant_pair("doc.pdf", "doc-1.pdf", content=b"a" * 300,
                                        dup_content=b"b" * 301)
        is_dup, reason = compare(dup, original, box.config)
        passed = check(is_dup and "size matches" in reason, f"same size: {reason}",
                       f"same size rejected: {reason}")
        is_dup, reason = compare(different, _o2, box.config)
        passed &= check(not is_dup and "size mismatch" in reason, f"different size: {reason}",
                        f"different size accepted: {reason}")
        return passed


def test_compare_hash_catches_same_size_different_content():
    print("\nTesting hash comparison...")
    with Sandbox(use_hash=True, hash_algorithm="sha256") as box:
        original, dup = box.plant_pair(content=b"a" * 300, dup_content=b"b" * 300)
        is_dup, reason = compare(dup, original, box.config)
        passed = check(not is_dup and "hash mismatch" in reason,
                       f"same size, different bytes rejected: {reason}",
                       f"WRONGLY accepted: {reason}")
        same_o, same_d = box.plant_pair("x.bin", "x-1.bin", content=b"z" * 50)
        is_dup, reason = compare(same_d, same_o, box.config)
        passed &= check(is_dup and "sha256 hash matches" in reason, f"identical bytes: {reason}",
                        f"identical bytes rejected: {reason}")
        return passed


def test_compare_time_window():
    print("\nTesting creation time window...")
    with Sandbox(check_time=True, time_window=60) as box:
        original, dup = box.plant_pair()
        is_dup, reason = compare(dup, original, box.config)
        passed = check(is_dup and "time within" in reason, f"just created: {reason}",
                       f"just created rejected: {reason}")

        old = time.time() - 3600
        os.utime(original, (old, old))
        is_dup, reason = compare(dup, original, box.config)
        passed &= check(not is_dup and "outside window" in reason,
                        f"hour-old original rejected: {reason}",
                        f"hour-old original accepted: {reason}")
        return passed


def test_all_checks_disabled_still_returns_a_reason():
    print("\nTesting all content checks off...")
    with Sandbox(check_size=False) as box:
        original, dup = box.plant_pair(content=b"a", dup_content=b"bbbbbb")
        is_dup, reason = compare(dup, original, box.config)
        return check(is_dup and "pattern only" in reason,
                     f"pattern-only match labelled: {reason}",
                     f"unexpected: {is_dup}, {reason}")


def test_find_duplicate_of_end_to_end():
    print("\nTesting find_duplicate_of...")
    with Sandbox() as box:
        original, dup = box.plant_pair()
        found, reason = find_duplicate_of(dup, box.config)
        passed = check(found == original, "matched the original", f"got {found}: {reason}")

        lonely = box.plant("alone-1.pdf")
        found, reason = find_duplicate_of(lonely, box.config)
        passed &= check(found is None and "no candidate" in reason, f"no original: {reason}",
                        f"unexpected: {found}, {reason}")

        plain = box.plant("plain.pdf")
        found, reason = find_duplicate_of(plain, box.config)
        passed &= check(found is None and "no -N suffix" in reason, f"plain name: {reason}",
                        f"unexpected: {found}, {reason}")
        return passed


def test_quarantine_preserves_structure_and_writes_restore_info():
    print("\nTesting quarantine move...")
    with Sandbox() as box:
        sub = box.watched / "2026" / "aug"
        original, dup = box.plant_pair(folder=sub)
        dest, size = quarantine_file(dup, "Duplicate of report.pdf", box.config)

        passed = check(not os.path.exists(dup), "source gone", "source still there")
        passed &= check(os.path.isfile(dest) and size == 200, f"dest exists ({size} bytes)",
                        f"dest missing or wrong size: {dest} {size}")
        expected_tail = os.path.join(datetime.now().strftime("%Y-%m-%d"), "watched",
                                     "2026", "aug", "report-1.pdf")
        passed &= check(dest.endswith(expected_tail), f"structure preserved: ...{expected_tail}",
                        f"dest was {dest}")
        info = read_restore_info(dest)
        passed &= check(info and info.get("Original path") == dup and "Reason" in info,
                        "restore info records original path", f"restore info: {info}")
        passed &= check(os.path.isfile(original), "original untouched", "original disappeared")
        return passed


def test_quarantine_collision_gets_numbered_name():
    print("\nTesting quarantine name collisions...")
    with Sandbox() as box:
        names = []
        for _ in range(3):
            _o, dup = box.plant_pair()
            dest, _size = quarantine_file(dup, "test", box.config)
            names.append(os.path.basename(dest))
        passed = check(names == ["report-1.pdf", "report-1_1.pdf", "report-1_2.pdf"],
                       f"collision naming: {names}", f"unexpected names: {names}")
        passed &= check(len(box.quarantined_files()) == 3, "all three kept",
                        f"files: {box.quarantined_files()}")
        return passed


def test_destination_for_falls_back_to_date_folder():
    print("\nTesting destination fallback for unknown paths...")
    with Sandbox() as box:
        dest = destination_for(os.sep + os.path.join("nowhere", "special", "x-1.pdf"), box.config)
        rel = os.path.relpath(dest, str(box.quarantine))
        parts = rel.split(os.sep)
        return check(len(parts) == 2 and parts[1] == "x-1.pdf",
                     f"fallback: {rel}", f"unexpected fallback: {rel}")


def test_restore_round_trip_and_refusals():
    print("\nTesting restore...")
    with Sandbox() as box:
        original, dup = box.plant_pair()
        dest, _ = quarantine_file(dup, "test", box.config)
        restored = restore_file(dest)
        passed = check(restored == dup and os.path.isfile(dup), "restored to original path",
                       f"restored to {restored}")
        passed &= check(not os.path.exists(dest) and not os.path.exists(dest + ".restore_info"),
                        "quarantine copy and sidecar removed", "leftovers in quarantine")

        dest, _ = quarantine_file(dup, "test", box.config)
        Path(dup).write_bytes(b"new file at the old path")
        refused = False
        try:
            restore_file(dest)
        except QuarantineError:
            refused = True
        passed &= check(refused and os.path.isfile(dest), "refuses to overwrite an existing file",
                        "overwrote a file during restore")

        os.remove(dest + ".restore_info")
        refused = False
        try:
            restore_file(dest)
        except QuarantineError:
            refused = True
        passed &= check(refused, "refuses without restore info", "restored without sidecar")
        return passed


def test_list_quarantine_groups_by_date():
    print("\nTesting quarantine listing...")
    with Sandbox() as box:
        for name in ["a-1.pdf", "b-1.pdf"]:
            box.plant(name.replace("-1", ""))
            quarantine_file(box.plant(name), "t", box.config)
        entries = list_quarantine(box.config)
        today = datetime.now().strftime("%Y-%m-%d")
        passed = check(len(entries) == 2, "two entries", f"{len(entries)} entries")
        passed &= check(all(e["date"] == today for e in entries), "dated today",
                        f"dates: {[e['date'] for e in entries]}")
        passed &= check(all(e["size"] == 100 for e in entries), "sizes recorded",
                        f"sizes: {[e['size'] for e in entries]}")
        return passed


def test_clean_old_respects_cutoff_and_prunes_dirs():
    print("\nTesting quarantine cleanup...")
    with Sandbox(delete_after_days=30) as box:
        box.plant("old.pdf")
        old_dest, _ = quarantine_file(box.plant("old-1.pdf"), "t", box.config)
        box.plant("new.pdf")
        new_dest, _ = quarantine_file(box.plant("new-1.pdf"), "t", box.config)

        ancient = time.time() - 40 * 86400
        for path in (old_dest, old_dest + ".restore_info"):
            os.utime(path, (ancient, ancient))

        count, size, errors = clean_old(box.config)
        passed = check(count == 1 and size == 100 and not errors,
                       f"deleted 1 old file ({size} bytes)", f"count={count} size={size} errors={errors}")
        passed &= check(not os.path.exists(old_dest) and not os.path.exists(old_dest + ".restore_info"),
                        "old file and sidecar gone", "old file survived")
        passed &= check(os.path.isfile(new_dest), "new file kept", "new file deleted!")

        count, _, _ = clean_old(box.config, days=0)
        passed &= check(count == 0 and os.path.isfile(new_dest), "days=0 deletes nothing",
                        "days=0 deleted something")

        # Move everything to the past, clean, and the date folders should vanish.
        for path in (new_dest, new_dest + ".restore_info"):
            os.utime(path, (ancient, ancient))
        clean_old(box.config)
        remaining = [p for p in box.quarantine.rglob("*")]
        passed &= check(not remaining, "empty date folders pruned",
                        f"leftover paths: {remaining}")
        return passed


def main():
    return run_suite("rules & quarantine tests", [
        test_candidates_original_first_then_siblings,
        test_compare_size_only,
        test_compare_hash_catches_same_size_different_content,
        test_compare_time_window,
        test_all_checks_disabled_still_returns_a_reason,
        test_find_duplicate_of_end_to_end,
        test_quarantine_preserves_structure_and_writes_restore_info,
        test_quarantine_collision_gets_numbered_name,
        test_destination_for_falls_back_to_date_folder,
        test_restore_round_trip_and_refusals,
        test_list_quarantine_groups_by_date,
        test_clean_old_respects_cutoff_and_prunes_dirs,
    ])


if __name__ == '__main__':
    exit(0 if main() else 1)


# End of file #
