"""
--install-app: write a macOS .app bundle that launches the menu bar mode.

Not py2app. The bundle is four files and a launcher: a shell script under
Contents/MacOS that execs the recorded interpreter with
`-m duplicate_preventer --menubar`, an Info.plist with LSUIElement so
there is no Dock tile, and the icon. Because it holds no Python of its
own, it never goes stale with a Python or macOS upgrade; re-run
--install-app after rebuilding a venv and it repairs the recorded path,
the same way --install-command does for the terminal stub.

The bundle is what Login Items, Spotlight, and TCC permission grants
attach to, which is the whole reason it exists.

Pure stdlib. Can be generated on any OS (tests do); only codesigning and
Login Items need a Mac.
"""

import os
import sys
import stat
import shutil
import plistlib
import subprocess

from duplicate_preventer._version import __version__


APP_NAME = "Duplicate File Preventer"
BUNDLE_ID = "com.redbearak.duplicate-file-preventer"
MARKER = "# duplicate-file-preventer app launcher (managed by --install-app)"
LOG_RELATIVE = "Library/Logs/DuplicateFilePreventer/launcher.log"

ICON_SOURCE = os.path.join(os.path.dirname(__file__), "icons", "AppIcon.icns")


def default_app_dir():
    return os.path.expanduser("~/Applications")


def bundle_path(app_dir, name=APP_NAME):
    return os.path.join(app_dir, name + ".app")


def package_src_dir():
    """Directory that must be on PYTHONPATH for `import duplicate_preventer`."""
    import duplicate_preventer
    return os.path.dirname(os.path.dirname(os.path.abspath(duplicate_preventer.__file__)))


def launcher_text(interpreter, src_dir):
    return (
        "#!/bin/sh\n"
        f"{MARKER}\n"
        "# Regenerate with:  duplicate-file-preventer --install-app\n"
        f"export PYTHONPATH=\"{src_dir}${{PYTHONPATH:+:$PYTHONPATH}}\"\n"
        f"LOG=\"$HOME/{LOG_RELATIVE}\"\n"
        "mkdir -p \"$(dirname \"$LOG\")\"\n"
        f"exec \"{interpreter}\" -m duplicate_preventer --menubar \"$@\" >> \"$LOG\" 2>&1\n"
    )


def info_plist(name=APP_NAME):
    return {
        "CFBundleName": name,
        "CFBundleDisplayName": name,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleVersion": __version__,
        "CFBundleShortVersionString": __version__,
        "CFBundlePackageType": "APPL",
        "CFBundleExecutable": "launcher",
        "CFBundleIconFile": "AppIcon",
        "LSUIElement": True,            # menu bar only: no Dock tile, no app menu
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
    }


def is_our_bundle(path):
    launcher = os.path.join(path, "Contents", "MacOS", "launcher")
    try:
        with open(launcher, "r", encoding="utf-8", errors="replace") as handle:
            return MARKER in handle.read(512)
    except OSError:
        return False


def recorded_interpreter(path):
    launcher = os.path.join(path, "Contents", "MacOS", "launcher")
    if not is_our_bundle(path):
        return None
    try:
        with open(launcher, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith('exec "'):
                    return line.split('"')[1]
    except OSError:
        pass
    return None


def codesign(path, out):
    """Ad-hoc sign so the bundle has a stable identity for TCC. Mac only."""
    if sys.platform != "darwin" or not shutil.which("codesign"):
        return False
    result = subprocess.run(["codesign", "--force", "--sign", "-", path],
                            capture_output=True, text=True)
    if result.returncode != 0:
        out(f"codesign failed (bundle still works, TCC grants may not stick): "
            f"{result.stderr.strip()}")
        return False
    return True


def install_app(app_dir=None, interpreter=None, src_dir=None, name=APP_NAME, out=print):
    """
    Write (or refresh) the bundle. Returns its path, or None when it
    refused to overwrite something it did not create.
    """
    app_dir = os.path.abspath(app_dir or default_app_dir())
    interpreter = interpreter or sys.executable
    src_dir = src_dir or package_src_dir()
    path = bundle_path(app_dir, name)

    if os.path.exists(path) and not is_our_bundle(path):
        out(f"Refusing to overwrite {path}: it is not a bundle written by this tool.")
        return None

    existing = recorded_interpreter(path)
    contents = os.path.join(path, "Contents")
    macos = os.path.join(contents, "MacOS")
    resources = os.path.join(contents, "Resources")
    os.makedirs(macos, exist_ok=True)
    os.makedirs(resources, exist_ok=True)

    launcher = os.path.join(macos, "launcher")
    with open(launcher, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(launcher_text(interpreter, src_dir))
    os.chmod(launcher, os.stat(launcher).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    with open(os.path.join(contents, "Info.plist"), "wb") as handle:
        plistlib.dump(info_plist(name), handle)

    with open(os.path.join(contents, "PkgInfo"), "w", encoding="ascii") as handle:
        handle.write("APPL????")

    if os.path.isfile(ICON_SOURCE):
        shutil.copyfile(ICON_SOURCE, os.path.join(resources, "AppIcon.icns"))

    verb = "Updated" if existing else "Installed"
    out(f"{verb} app bundle: {path}")
    out(f"  interpreter: {interpreter}")
    out(f"  package dir: {src_dir}")
    if codesign(path, out):
        out("  signed: ad-hoc")

    out("")
    out("Next steps:")
    out(f"  open \"{path}\"                        # launch it now")
    out("  System Settings > General > Login Items > '+' and pick it   # launch at login")
    out(f"  launcher output: ~/{LOG_RELATIVE}")
    out("Expect macOS to ask again for folder permissions the first time the")
    out("bundle (rather than Terminal) watches Desktop, Documents or Downloads.")
    return path


def uninstall_app(app_dir=None, name=APP_NAME, out=print):
    app_dir = os.path.abspath(app_dir or default_app_dir())
    path = bundle_path(app_dir, name)
    if not os.path.exists(path):
        out(f"No app bundle at {path}")
        return False
    if not is_our_bundle(path):
        out(f"Refusing to remove {path}: it is not a bundle written by this tool.")
        return False
    shutil.rmtree(path)
    out(f"Removed app bundle: {path}")
    out("If it was a Login Item, remove it in System Settings > General > Login Items.")
    return True


# End of file #
