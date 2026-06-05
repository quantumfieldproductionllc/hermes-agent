"""Toolset gating for the dynamic Experience Memory Engine tool."""

from __future__ import annotations

from typing import Iterable


def _as_names(toolsets: Iterable[str] | None) -> list[str] | None:
    if toolsets is None:
        return None
    return [str(name).strip() for name in toolsets if str(name).strip()]


def _resolves_memory(toolset_name: str) -> bool:
    try:
        from toolsets import resolve_toolset, validate_toolset
    except Exception:
        return False
    try:
        if not validate_toolset(toolset_name):
            return False
        return "memory" in set(resolve_toolset(toolset_name))
    except Exception:
        return False


def experience_memory_tools_allowed(enabled_toolsets, disabled_toolsets) -> bool:
    """Return whether the dynamic ``experience_memory`` schema may be injected.

    The logical ``experience_memory`` toolset intentionally resolves to no
    registry tools, so literal toolset names are meaningful in addition to
    normal ``toolsets.resolve_toolset`` expansion.
    """
    enabled = _as_names(enabled_toolsets)
    disabled = _as_names(disabled_toolsets) or []

    disabled_set = set(disabled)
    if disabled_set & {"all", "*", "experience_memory", "memory"}:
        return False
    if any(_resolves_memory(name) for name in disabled):
        return False

    if enabled is None:
        return True
    if not enabled:
        return False

    enabled_set = set(enabled)
    if enabled_set & {"all", "*", "experience_memory", "memory"}:
        return True
    return any(_resolves_memory(name) for name in enabled)
