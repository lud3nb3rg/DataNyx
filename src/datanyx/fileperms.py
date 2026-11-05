"""Make a file readable by its owner only, on every system."""

from __future__ import annotations

import os
import subprocess
import sys


def restrict_to_owner(path: str) -> bool:
    """Leave only the current user with access. POSIX modes are set when a file is created;
    on Windows the permissions are reset and then granted to the user alone. True if it worked."""
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o600)
        except OSError:
            return False
        return True
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    account = rf"{domain}\{user}" if domain and user else user
    try:
        # /reset first: when the folder has nothing to inherit, a new file gets explicit
        # entries (SYSTEM, Administrators) that /inheritance:r alone would leave in place.
        steps = (["/reset"], ["/inheritance:r", "/grant:r", f"{account}:(R,W,D)"])
        results = [subprocess.run(["icacls", path, *step], capture_output=True, timeout=15, check=False) for step in steps]
    except (OSError, subprocess.SubprocessError):
        return False
    return all(result.returncode == 0 for result in results) and bool(user)
