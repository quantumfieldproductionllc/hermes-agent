"""Regression coverage for telegram_userbot slash-command gating."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _clear_auth_env(monkeypatch) -> None:
    for key in (
        "TELEGRAM_USERBOT_ALLOWED_USERS",
        "TELEGRAM_USERBOT_ALLOW_ALL_USERS",
        "GATEWAY_ALLOWED_USERS",
        "GATEWAY_ALLOW_ALL_USERS",
    ):
        monkeypatch.delenv(key, raising=False)


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM_USERBOT,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_make_source(), message_id="m1")


def _make_runner(config: GatewayConfig | dict | None = None):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = config or GatewayConfig(
        platforms={
            Platform.TELEGRAM_USERBOT: PlatformConfig(
                enabled=True,
                extra={"runtime_config_path": "/tmp/userbot.toml"},
            )
        }
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM_USERBOT: adapter}
    runner.hooks = SimpleNamespace(
        emit=AsyncMock(),
        emit_collect=AsyncMock(return_value=[]),
        loaded_hooks=False,
    )
    runner.session_store = MagicMock()
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    return runner, adapter


@pytest.mark.asyncio
async def test_pre_gateway_dispatch_rewrite_to_slash_is_blocked(monkeypatch):
    """A plugin rewrite from text to slash must not bypass the userbot slash gate."""
    runner, _adapter = _make_runner()
    runner._is_user_authorized = MagicMock(return_value=True)
    runner._handle_status_command = AsyncMock(
        side_effect=AssertionError("rewritten /status must be blocked")
    )

    def runtime_guard(*, event, gateway, session_store):
        return {"action": "skip", "reason": "external_participant_command_blocked"}

    def pre_dispatch(name, **kwargs):
        if name == "pre_gateway_dispatch":
            return [{"action": "rewrite", "text": "/status"}]
        return []

    monkeypatch.setattr(runner, "_telegram_userbot_runtime_guard", lambda: runtime_guard)
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", pre_dispatch)

    result = await runner._handle_message(_make_event("status please"))

    runner._handle_status_command.assert_not_awaited()
    assert result is not None
    assert "Slash commands are disabled" in result


@pytest.mark.asyncio
async def test_command_hook_rewrite_to_slash_is_rechecked(monkeypatch):
    """command:<name> rewrites must pass the userbot slash gate again."""
    runner, _adapter = _make_runner()
    runner._is_user_authorized = MagicMock(return_value=True)
    runner._handle_restart_command = AsyncMock(
        side_effect=AssertionError("rewritten /restart must be blocked")
    )
    runner.hooks.emit_collect = AsyncMock(
        return_value=[
            {
                "decision": "rewrite",
                "command_name": "restart",
                "raw_args": "",
            }
        ]
    )
    decisions = iter(
        [
            {"action": "allow"},
            {"action": "skip", "reason": "external_participant_command_blocked"},
        ]
    )

    def runtime_guard(*, event, gateway, session_store):
        return next(decisions)

    monkeypatch.setattr(runner, "_telegram_userbot_runtime_guard", lambda: runtime_guard)
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_args, **_kwargs: [])

    result = await runner._handle_message(_make_event("/status"))

    runner._handle_restart_command.assert_not_awaited()
    assert result is not None
    assert "Slash commands are disabled" in result


@pytest.mark.asyncio
async def test_quick_command_alias_rewrite_to_slash_is_rechecked(monkeypatch):
    """Quick-command aliases must not become a second slash-dispatch bypass."""
    runner, _adapter = _make_runner(
        {
            "quick_commands": {
                "shortcut": {"type": "alias", "target": "/status"},
            }
        }
    )
    runner._is_user_authorized = MagicMock(return_value=True)
    decisions = iter(
        [
            {"action": "allow"},
            {"action": "skip", "reason": "external_participant_command_blocked"},
        ]
    )

    def runtime_guard(*, event, gateway, session_store):
        return next(decisions)

    def fail_plugin_lookup(_command):
        raise AssertionError("quick-alias rewrite must be blocked before plugin dispatch")

    monkeypatch.setattr(runner, "_telegram_userbot_runtime_guard", lambda: runtime_guard)
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("hermes_cli.plugins.get_plugin_command_handler", fail_plugin_lookup)

    result = await runner._handle_message(_make_event("/shortcut"))

    assert result is not None
    assert "Slash commands are disabled" in result


@pytest.mark.asyncio
async def test_runtime_admin_peer_still_requires_hermes_authorization(monkeypatch):
    """allowed_admin_peers pass the slash gate, not the normal Hermes auth gate."""
    _clear_auth_env(monkeypatch)
    runner, adapter = _make_runner()
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = False
    runner.pairing_store._is_rate_limited.return_value = False
    runner.pairing_store.generate_code.return_value = "12345"
    runner._handle_status_command = AsyncMock(
        side_effect=AssertionError("unauthorized admin peer must not dispatch /status")
    )

    def runtime_guard(*, event, gateway, session_store):
        return {"action": "allow"}

    monkeypatch.setattr(runner, "_telegram_userbot_runtime_guard", lambda: runtime_guard)
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_args, **_kwargs: [])

    result = await runner._handle_message(_make_event("/status"))

    assert result is None
    runner._handle_status_command.assert_not_awaited()
    runner.pairing_store.generate_code.assert_called_once()
    adapter.send.assert_awaited_once()
