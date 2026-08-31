"""
macOS permission probes. Pure stdlib; safe to import anywhere.

Full Disk Access (FDA) has no prompt: a process either has it or gets
PermissionError. Probing a file that is always present and guarded ONLY by
FDA tells us which, without triggering any of the prompting services. The
refused probe is also what makes tccd list the app in the Full Disk Access
pane (switched off), so the user can find it there.

Never probe a watched folder or another app's container to test FDA: those
ARE the prompting services this exists to avoid.
"""

import os
import sys


# Each exists on any signed-in macOS account and is readable only with FDA.
FDA_CANARIES = (
    "~/Library/Application Support/com.apple.TCC/TCC.db",
    "~/Library/Safari/CloudTabs.db",
    "~/Library/Mail",
)

SETTINGS_URL_FULL_DISK_ACCESS = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")


def full_disk_access():
    """
    True / False on macOS; None when it cannot be determined (not macOS, or
    no canary present). Silent: touches nothing that prompts.
    """
    if sys.platform != "darwin":
        return None
    for canary in FDA_CANARIES:
        path = os.path.expanduser(canary)
        try:
            if os.path.isdir(path):
                os.listdir(path)
            else:
                with open(path, "rb") as handle:
                    handle.read(1)
            return True
        except PermissionError:
            return False
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return None


# End of file #
