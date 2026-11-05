"""The secret key that decides which fake value each real value becomes.

With the same key, every run gives the same fakes, so a fresh export of the same data lines up
with the last one. Without a key file a new random key is used, and the fakes can't be matched
to a later run's. Anyone with the key can test a guess ("is this fake the one for Ana Silva?"),
so it is created readable by its owner only and belongs with the real data, not the output.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from ..fileperms import restrict_to_owner
from ..sources.base import SourceError

MIN_BYTES = 16


def load_key(path: str | None) -> bytes:
    """The key in `path`, creating the file with a new random key if it doesn't exist."""
    if path is None:
        return secrets.token_bytes(32)
    if os.path.exists(path):
        try:
            key = bytes.fromhex(Path(path).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            raise SourceError("the key file can't be read; it should hold a hexadecimal key") from None
        if len(key) < MIN_BYTES:
            raise SourceError("the key in the key file is too short")
        return key
    key = secrets.token_bytes(32)
    try:
        with open(path, "x", encoding="utf-8", opener=lambda file, flags: os.open(file, flags, 0o600)) as stream:
            stream.write(key.hex() + chr(10))
    except OSError as error:
        raise SourceError(f"cannot create the key file: {error.strerror}") from None
    if os.name == "nt":
        restrict_to_owner(path)
    return key
