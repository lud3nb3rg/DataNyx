"""Take the credentials out of a URL, for anything that logs or shows one."""

from __future__ import annotations

import re

MARKER = "<CREDENTIALS>"
# Everything before the last @ of the address: a password can hold an @, and a token needs no colon.
_USER_INFO = re.compile(r"""(?<=://)[^/\s'"?#]+(?=@)""")
_QUERY_SECRET = re.compile(
    r"""(?i)([?&](?:password|passwd|pwd|pass|token|secret|api[_-]?key|access[_-]?token|auth[_-]?token|sslpassword)=)[^&\s'"#]+"""
)


def hide_url_credentials(text: str) -> str:
    """`postgresql://u:p@ss@host/db?token=x` becomes `postgresql://<CREDENTIALS>@host/db?token=<CREDENTIALS>`."""
    return _QUERY_SECRET.sub(rf"\g<1>{MARKER}", _USER_INFO.sub(MARKER, text))
