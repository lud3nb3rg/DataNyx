"""Which paths are protected, stored in `.datanyx/protect.json` at the project's root."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..sources.base import SourceError

CONFIG_DIR = ".datanyx"
CONFIG_FILE = "protect.json"
VERSION = 1


@dataclass(frozen=True)
class ProtectConfig:
    """`paths` are absolute. `block_broad_search` refuses searches that would also cover them."""

    root: Path
    paths: tuple[Path, ...]
    block_broad_search: bool = True

    def display(self, path: Path) -> str:
        """A protected path as the project knows it: relative when it is inside the project."""
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    @property
    def all_protected(self) -> tuple[Path, ...]:
        """The protected paths, and the local settings file, which lists the terms DataNyx hides."""
        local = self.root / "datanyx.local.toml"
        return (*self.paths, local) if local.is_file() else self.paths

    @property
    def guarded_files(self) -> tuple[Path, ...]:
        """The files that set protection up. An agent that could edit them could switch it off."""
        return (
            self.root / ".claude" / "settings.json",
            self.root / ".claude" / "settings.local.json",
            self.root / ".mcp.json",
            self.root / "datanyx.toml",
            self.root / "datanyx.local.toml",
            self.root / CONFIG_DIR / CONFIG_FILE,
            # A setting in the user's own file (disableAllHooks) would turn the hook off here too.
            Path.home() / ".claude" / "settings.json",
            Path.home() / ".claude" / "settings.local.json",
        )


def config_path(root: Path) -> Path:
    return root / CONFIG_DIR / CONFIG_FILE


def find_config(start: Path) -> ProtectConfig | None:
    """The configuration for the project that contains `start`, or None."""
    for folder in (start, *start.parents):
        candidate = config_path(folder)
        if candidate.is_file():
            return load_config(folder)
    return None


def load_config(root: Path) -> ProtectConfig:
    try:
        data = json.loads(config_path(root).read_text(encoding="utf-8"))
        paths = tuple(_resolve(root, item) for item in data["paths"])
        return ProtectConfig(root.resolve(), paths, bool(data.get("block_broad_search", True)))
    except (OSError, ValueError, KeyError, TypeError):
        raise SourceError("the protection settings in .datanyx/protect.json can't be read") from None


def save_config(root: Path, paths: list[str], block_broad_search: bool) -> None:
    target = config_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": VERSION, "paths": paths, "block_broad_search": block_broad_search}
    target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def stored_form(root: Path, path: Path) -> str:
    """How a path is written in settings: relative to the project if inside it, else absolute."""
    resolved = Path(os.path.realpath(path))
    try:
        return resolved.relative_to(Path(os.path.realpath(root))).as_posix()
    except ValueError:
        return resolved.as_posix()


def _resolve(root: Path, stored: str) -> Path:
    path = Path(stored)
    return Path(os.path.realpath(path if path.is_absolute() else root / path))
