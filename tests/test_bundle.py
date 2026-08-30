"""
Tests for frontends/bundle.py: the --install-app bundle writer.

tests/test_bundle.py

The bundle is generated on any OS so its structure can be tested here;
only codesigning and Login Items are Mac-only, and codesign() is written
to return False quietly elsewhere. What must hold: the plist parses and
says LSUIElement, the launcher records the interpreter and the package
dir and execs --menubar, the icon is present, foreign bundles are never
touched, and a reinstall refreshes a changed interpreter.

Runnable with pytest, but written to run standalone and report a score.
"""

import os
import sys
import stat
import plistlib
import tempfile
import subprocess

from pathlib import Path

from _helpers import Sandbox, check, run_suite

from duplicate_preventer._version import __version__
from duplicate_preventer.frontends import bundle as bundle_module
from duplicate_preventer.frontends.bundle import (
    MARKER,
    APP_NAME,
    BUNDLE_ID,
    c_compiler,
    install_app,
    launcher_kind,
    is_our_bundle,
    uninstall_app,
    package_src_dir,
    recorded_interpreter,
)


class no_compiler:
    """Force the shell-script fallback for a test."""

    def __enter__(self):
        self._saved = bundle_module.c_compiler
        bundle_module.c_compiler = lambda: None

    def __exit__(self, *_exc):
        bundle_module.c_compiler = self._saved


def collect():
    lines = []
    return lines, lines.append


def test_bundle_structure():
    print("\nTesting bundle layout...")
    with tempfile.TemporaryDirectory() as tmp:
        lines, out = collect()
        path = install_app(tmp, out=out)
        contents = Path(path) / "Contents"
        passed = check(path and path.endswith(f"{APP_NAME}.app"), f"bundle at {path}", "no bundle")
        for rel in ["Info.plist", "PkgInfo", "MacOS/launcher", "Resources/AppIcon.icns"]:
            passed &= check((contents / rel).is_file(), f"{rel} present", f"{rel} missing")

        with open(contents / "Info.plist", "rb") as handle:
            plist = plistlib.load(handle)
        passed &= check(plist.get("LSUIElement") is True, "LSUIElement true (no Dock tile)",
                        f"LSUIElement: {plist.get('LSUIElement')}")
        passed &= check(plist.get("CFBundleExecutable") == "launcher" and plist.get("CFBundleIdentifier") == BUNDLE_ID
                        and plist.get("CFBundleIconFile") == "AppIcon",
                        "plist points at launcher, id, icon", f"plist: {plist}")
        passed &= check(plist.get("CFBundleShortVersionString") == __version__, "version stamped",
                        f"version: {plist.get('CFBundleShortVersionString')}")
        passed &= check((contents / "PkgInfo").read_text() == "APPL????", "PkgInfo", "PkgInfo wrong")
        passed &= check(any(l.startswith("Installed app bundle") for l in lines)
                        and any("Login Items" in l for l in lines), "output has next steps",
                        f"output: {lines}")
        return passed


def test_compiled_launcher_when_a_compiler_exists():
    print("\nTesting the compiled launcher...")
    if not c_compiler():
        print("  - no C compiler here; skipped (shell fallback is tested separately)")
        return True
    with tempfile.TemporaryDirectory() as tmp:
        lines, out = collect()
        path = install_app(tmp, out=out)
        macos = Path(path) / "Contents" / "MacOS"
        passed = check(launcher_kind(path) == "compiled" and any("launcher:    compiled" in l for l in lines),
                       "compiled launcher built", f"kind={launcher_kind(path)} output={lines}")
        head = (macos / "launcher").read_bytes()[:4]
        passed &= check(head in (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"),
                        f"launcher is a native executable ({head!r})", f"launcher header {head!r}")
        source = (macos / "launcher.c").read_text()
        passed &= check(MARKER in source and f'interpreter="{sys.executable}"' in source
                        and f'src_dir="{package_src_dir()}"' in source,
                        "source kept beside it records interpreter and package dir", "source missing paths")
        passed &= check(recorded_interpreter(path) == sys.executable, "interpreter readable back",
                        f"recorded: {recorded_interpreter(path)}")
        passed &= check(not any("no C compiler" in l for l in lines), "no fallback warning", f"{lines}")
        return passed


def test_shell_launcher_fallback_without_compiler():
    print("\nTesting the shell launcher fallback...")
    with tempfile.TemporaryDirectory() as tmp, no_compiler():
        lines, out = collect()
        path = install_app(tmp, out=out)
        launcher = Path(path) / "Contents" / "MacOS" / "launcher"
        text = launcher.read_text()
        passed = check(launcher_kind(path) == "script" and text.startswith("#!/bin/sh") and MARKER in text,
                       "sh script with marker", f"kind={launcher_kind(path)} text:\n{text}")
        passed &= check(f'"{sys.executable}" -m duplicate_preventer --menubar' in text and "exec " not in text,
                        "runs this interpreter with --menubar as a child (no exec)", f"text:\n{text}")
        passed &= check("wait " in text and "trap " in text and "cd /" in text,
                        "waits on the child, forwards signals, cd /", "missing wait/trap/cd")
        passed &= check(package_src_dir() in text and "PYTHONPATH" in text,
                        f"PYTHONPATH includes {package_src_dir()}", "package dir not exported")
        passed &= check(not (Path(path) / "Contents" / "MacOS" / "launcher.c").exists(),
                        "no stale C source", "launcher.c left behind")
        passed &= check(any("no C compiler" in l for l in lines), "warns about attribution", f"{lines}")
        passed &= check(recorded_interpreter(path) == sys.executable, "interpreter readable back",
                        f"recorded: {recorded_interpreter(path)}")
        if os.name != "nt":
            passed &= check(os.stat(launcher).st_mode & stat.S_IXUSR, "executable", "not executable")
        return passed


def _run_launcher(path, tmp):
    launcher = Path(path) / "Contents" / "MacOS" / "launcher"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["HOME"] = tmp                      # keep the launcher log inside the sandbox
    result = subprocess.run([str(launcher)], capture_output=True, text=True, env=env, timeout=30)
    log = Path(tmp) / "Library" / "Logs" / "DuplicateFilePreventer" / "launcher.log"
    return result, (log.read_text() if log.exists() else "")


def test_launchers_run_and_reach_the_dispatcher():
    """
    Run each launcher on a non-Mac: it starts --menubar, which the
    dispatcher refuses off macOS with a clear message into the launcher
    log. That proves PYTHONPATH, the spawn and the log redirection.
    """
    print("\nTesting the launchers execute...")
    if os.name == "nt":
        print("  - skipped on Windows")
        return True
    if sys.platform == "darwin":
        print("  - on macOS this would start the menu bar app; skipped")
        return True
    passed = True
    for label, ctx in [("compiled", None), ("script", no_compiler())]:
        with tempfile.TemporaryDirectory() as tmp:
            if ctx is not None:
                ctx.__enter__()
            try:
                if label == "compiled" and not c_compiler():
                    print("  - no C compiler; compiled launcher skipped")
                    continue
                path = install_app(tmp, out=lambda _: None)
                result, log_text = _run_launcher(path, tmp)
            finally:
                if ctx is not None:
                    ctx.__exit__(None, None, None)
            passed &= check("only available on macOS" in log_text,
                            f"{label}: reached the dispatcher's platform gate via the log",
                            f"{label}: rc={result.returncode} log={log_text!r} err={result.stderr[-300:]}")
            passed &= check(result.returncode == 1, f"{label}: child exit code propagated (1)",
                            f"{label}: rc={result.returncode}")
    return passed


def test_compiled_launcher_forwards_sigterm():
    """SIGTERM to the launcher must reach the child, or Quit/logout would orphan the engine."""
    print("\nTesting signal forwarding...")
    if os.name == "nt" or sys.platform == "darwin" or not c_compiler():
        print("  - skipped here")
        return True
    import signal, time
    with tempfile.TemporaryDirectory() as tmp:
        # Point the launcher at a fake interpreter that sleeps, so it stays alive.
        fake = Path(tmp) / "fake_python"
        fake.write_text("#!/bin/sh\ntrap 'exit 143' TERM\nwhile :; do sleep 0.1; done\n")
        fake.chmod(0o755)
        path = install_app(tmp, interpreter=str(fake), out=lambda _: None)
        launcher = Path(path) / "Contents" / "MacOS" / "launcher"
        env = dict(os.environ)
        env["HOME"] = tmp
        proc = subprocess.Popen([str(launcher)], env=env)
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        try:
            rc = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            return check(False, "", "launcher did not exit after SIGTERM (child not signalled)")
        return check(rc == 143, f"launcher exited with the child's code ({rc})", f"rc={rc}")


def test_reinstall_refreshes_interpreter_and_never_touches_foreign_bundle():
    print("\nTesting reinstall and refusal...")
    with tempfile.TemporaryDirectory() as tmp:
        install_app(tmp, out=lambda _: None)
        lines, out = collect()
        path = install_app(tmp, interpreter="/opt/other/python3", out=out)
        passed = check(any(l.startswith("Updated") for l in lines)
                       and recorded_interpreter(path) == "/opt/other/python3",
                       "reinstall rewrote the interpreter", f"output: {lines}")

        foreign = Path(tmp) / "other" / f"{APP_NAME}.app" / "Contents" / "MacOS"
        foreign.mkdir(parents=True)
        (foreign / "launcher").write_text("#!/bin/sh\necho someone else's app\n")
        lines, out = collect()
        result = install_app(str(Path(tmp) / "other"), out=out)
        passed &= check(result is None and "someone else" in (foreign / "launcher").read_text(),
                        "foreign bundle left alone", f"result={result} output={lines}")
        passed &= check(not is_our_bundle(str(foreign.parent.parent)), "not recognised as ours",
                        "foreign bundle claimed")
        removed = uninstall_app(str(Path(tmp) / "other"), out=lambda _: None)
        passed &= check(not removed and foreign.exists(), "uninstall refused foreign bundle",
                        "uninstall removed a foreign bundle")
        return passed


def test_uninstall_removes_own_bundle():
    print("\nTesting uninstall...")
    with tempfile.TemporaryDirectory() as tmp:
        path = install_app(tmp, out=lambda _: None)
        removed = uninstall_app(tmp, out=lambda _: None)
        passed = check(removed and not os.path.exists(path), "bundle removed", "bundle remains")
        lines, out = collect()
        passed &= check(not uninstall_app(tmp, out=out) and any("No app bundle" in l for l in lines),
                        "second uninstall: nothing to do", f"output: {lines}")
        return passed


def test_cli_flags_reach_the_bundle_writer():
    print("\nTesting --install-app / --uninstall-app through the CLI...")
    with Sandbox() as box:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
        env["TERM"] = "dumb"
        apps = str(box.root / "Applications")
        base = [sys.executable, "-m", "duplicate_preventer", "--config", box.config.config_file]
        installed = subprocess.run(base + ["--install-app", "--dir", apps], capture_output=True, text=True, env=env)
        bundle = Path(apps) / f"{APP_NAME}.app"
        passed = check(installed.returncode == 0 and bundle.is_dir(), "installed via CLI",
                       f"rc={installed.returncode} out={installed.stdout} err={installed.stderr[-300:]}")
        removed = subprocess.run(base + ["--uninstall-app", "--dir", apps], capture_output=True, text=True, env=env)
        passed &= check(removed.returncode == 0 and not bundle.exists(), "removed via CLI",
                        f"rc={removed.returncode} out={removed.stdout}")
        both = subprocess.run(base + ["--install-app", "--install-command"], capture_output=True, text=True, env=env)
        passed &= check(both.returncode == 2, "mutually exclusive with other modes", f"rc={both.returncode}")
        return passed


def main():
    return run_suite("bundle tests", [
        test_bundle_structure,
        test_compiled_launcher_when_a_compiler_exists,
        test_shell_launcher_fallback_without_compiler,
        test_launchers_run_and_reach_the_dispatcher,
        test_compiled_launcher_forwards_sigterm,
        test_reinstall_refreshes_interpreter_and_never_touches_foreign_bundle,
        test_uninstall_removes_own_bundle,
        test_cli_flags_reach_the_bundle_writer,
    ])


if __name__ == '__main__':
    exit(0 if main() else 1)


# End of file #
