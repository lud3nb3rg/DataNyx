"""Keep an agent from reading protected data directly: permissions, a guard hook, an MCP config."""

from __future__ import annotations

from .config import ProtectConfig, find_config, load_config
from .guard import decide, run_hook
from .settings import Change, apply, plan_protect, plan_remove, self_test, split_command

__all__ = [
    "Change",
    "ProtectConfig",
    "apply",
    "decide",
    "find_config",
    "load_config",
    "plan_protect",
    "plan_remove",
    "run_hook",
    "self_test",
    "split_command",
]
