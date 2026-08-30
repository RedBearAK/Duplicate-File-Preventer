"""
Tests for frontends/installer.py: the --install-command launcher stub.

tests/test_installer.py

The stub must record the interpreter, be executable, refresh itself when
the interpreter changes, and above all never overwrite or delete a file
it did not write.

Runnable with pytest, but written to run standalone and report a score.
"""

import os
import sys
import stat
import tempfile
import subprocess

from pathlib import Path

from _helpers import check, run_suite

from duplicate_preventer.frontends.installer import (
    MARKER,
    dir_on_path,
    is_our_stub,
    install_command,
    uninstall_command,
    recorded_interpreter,
)


def collect():
    lines = []
    return lines, lines.append


def test_install_writes_executable_stub_with_interpreter():
    print("\nTesting install...")
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = os.path.join(tmp, "bin")
        lines, out = collect()
        path = install_command(bin_dir, out=out)
        passed = check(path and os.path.isfile(path), f"stub written: {path}", "no stub")
        text = Path(path).read_text()
        passed &= check(MARKER in text and sys.executable in text and "-m duplicate_preventer" in text,
                        "stub has marker, interpreter, module", f"stub text:\n{text}")
        passed &= check("PYTHONPATH" in text, "stub exports the package dir on PYTHONPATH",
                        "stub relies on the package being pip-installed")
        if os.name != "nt":
            passed &= check(os.stat(path).st_mode & stat.S_IXUSR, "executable bit set", "not executable")
        passed &= check(recorded_interpreter(path) == sys.executable, "interpreter readable back",
                        f"recorded: {recorded_interpreter(path)}")
        passed &= check(any("NOT on your PATH" in l for l in lines) and any("export PATH" in l or "setx" in l for l in lines),
                        "PATH hint printed", f"output: {lines}")
        return passed


def test_stub_actually_runs_the_tool():
    print("\nTesting the stub executes...")
    if os.name == "nt":
        print("  - skipped on Windows (sh stub)")
        return True
    with tempfile.TemporaryDirectory() as tmp:
        path = install_command(os.path.join(tmp, "bin"), out=lambda _: None)
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}   # the stub sets it
        result = subprocess.run([path, "--version"], capture_output=True, text=True, env=env)
        return check(result.returncode == 0 and "duplicate-file-preventer" in result.stdout,
                     f"stub ran: {result.stdout.strip()}",
                     f"stub failed: rc={result.returncode} {result.stderr[-300:]}")


def test_reinstall_is_idempotent_and_refreshes_interpreter():
    print("\nTesting reinstall...")
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = os.path.join(tmp, "bin")
        install_command(bin_dir, out=lambda _: None)
        lines, out = collect()
        install_command(bin_dir, out=out)
        passed = check(any("already current" in l for l in lines), "second install: already current",
                       f"output: {lines}")

        lines, out = collect()
        path = install_command(bin_dir, interpreter="/opt/other/python3", out=out)
        passed &= check(any(l.startswith("Updated") for l in lines) and
                        recorded_interpreter(path) == "/opt/other/python3",
                        "interpreter change rewrites stub", f"output: {lines}")
        return passed


def test_never_touches_foreign_files():
    print("\nTesting refusal to overwrite/remove foreign files...")
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = os.path.join(tmp, "bin")
        os.makedirs(bin_dir)
        name = "duplicate-file-preventer" + (".cmd" if os.name == "nt" else "")
        foreign = os.path.join(bin_dir, name)
        Path(foreign).write_text("#!/bin/sh\necho mine\n")

        lines, out = collect()
        result = install_command(bin_dir, out=out)
        passed = check(result is None and Path(foreign).read_text() == "#!/bin/sh\necho mine\n",
                       "install refused, file intact", f"result={result} output={lines}")
        passed &= check(not is_our_stub(foreign), "not recognised as ours", "foreign file claimed")

        lines, out = collect()
        removed = uninstall_command(bin_dir, out=out)
        passed &= check(not removed and os.path.exists(foreign), "uninstall refused, file intact",
                        f"removed={removed}")
        return passed


def test_uninstall_removes_own_stub():
    print("\nTesting uninstall...")
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = os.path.join(tmp, "bin")
        path = install_command(bin_dir, out=lambda _: None)
        removed = uninstall_command(bin_dir, out=lambda _: None)
        passed = check(removed and not os.path.exists(path), "stub removed", "stub remains")
        lines, out = collect()
        removed = uninstall_command(bin_dir, out=out)
        passed &= check(not removed and any("No launcher" in l for l in lines),
                        "second uninstall: nothing to do", f"output: {lines}")
        return passed


def test_dir_on_path_detection():
    print("\nTesting PATH detection...")
    with tempfile.TemporaryDirectory() as tmp:
        saved = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = tmp + os.pathsep + saved
            passed = check(dir_on_path(tmp), "dir found on PATH", "dir not found")
            passed &= check(dir_on_path(os.path.join(tmp, "..", os.path.basename(tmp))),
                            "normalised comparison", "unnormalised path missed")
            os.environ["PATH"] = saved
            passed &= check(not dir_on_path(tmp), "dir absent -> False", "false positive")
        finally:
            os.environ["PATH"] = saved
        return passed


def main():
    return run_suite("installer tests", [
        test_install_writes_executable_stub_with_interpreter,
        test_stub_actually_runs_the_tool,
        test_reinstall_is_idempotent_and_refreshes_interpreter,
        test_never_touches_foreign_files,
        test_uninstall_removes_own_stub,
        test_dir_on_path_detection,
    ])


if __name__ == '__main__':
    exit(0 if main() else 1)


# End of file #
