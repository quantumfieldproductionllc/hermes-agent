from types import SimpleNamespace
from unittest.mock import patch

from gateway.config import Platform, PlatformConfig
from gateway.platform_registry import PlatformEntry


class _FakeEntryPoint:
    group = "hermes_agent.gateway_adapters"
    value = "fake:create_adapter"

    def __init__(self, factory, name="telegram_userbot"):
        self._factory = factory
        self.name = name

    def load(self):
        return self._factory


class _FakeEntryPoints(list):
    def select(self, *, group):
        return [ep for ep in self if ep.group == group]


def test_create_adapter_loads_third_party_entry_point():
    from gateway.run import GatewayRunner

    created_with = {}
    adapter = SimpleNamespace(name="telegram_userbot")

    def create_adapter(platform_config):
        created_with["config"] = platform_config
        return adapter

    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(
        group_sessions_per_user=True,
        thread_sessions_per_user=False,
    )
    platform_config = PlatformConfig(
        enabled=True,
        extra={"runtime_config_path": "/tmp/userbot.toml"},
    )

    with patch(
        "importlib.metadata.entry_points",
        return_value=_FakeEntryPoints([_FakeEntryPoint(create_adapter)]),
    ):
        result = runner._create_adapter(Platform.TELEGRAM_USERBOT, platform_config)

    assert result is adapter
    assert created_with["config"] is platform_config
    assert platform_config.extra["runtime_config_path"] == "/tmp/userbot.toml"


def test_create_adapter_entry_point_cannot_shadow_builtin_telegram():
    from gateway.platforms import telegram
    from gateway.run import GatewayRunner

    malicious_adapter = SimpleNamespace(name="malicious_telegram")
    builtin_adapter = SimpleNamespace(name="telegram")

    def create_adapter(_platform_config):
        return malicious_adapter

    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(
        group_sessions_per_user=True,
        thread_sessions_per_user=False,
    )
    platform_config = PlatformConfig(enabled=True, token="***")

    with patch(
        "importlib.metadata.entry_points",
        return_value=_FakeEntryPoints([
            _FakeEntryPoint(create_adapter, name="telegram"),
        ]),
    ) as entry_points, \
         patch.object(telegram, "check_telegram_requirements", return_value=True), \
         patch.object(telegram, "TelegramAdapter", return_value=builtin_adapter):
        result = runner._create_adapter(Platform.TELEGRAM, platform_config)

    assert result is builtin_adapter
    entry_points.assert_not_called()


def test_create_adapter_registry_cannot_shadow_builtin_telegram():
    from gateway.platform_registry import platform_registry
    from gateway.platforms import telegram
    from gateway.run import GatewayRunner

    malicious_adapter = SimpleNamespace(name="malicious_telegram")
    builtin_adapter = SimpleNamespace(name="telegram")
    entry = PlatformEntry(
        name="telegram",
        label="Shadow Telegram",
        adapter_factory=lambda _config: malicious_adapter,
        check_fn=lambda: True,
        source="plugin",
    )

    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(
        group_sessions_per_user=True,
        thread_sessions_per_user=False,
    )
    platform_config = PlatformConfig(enabled=True, token="***")

    platform_registry.register(entry)
    try:
        with patch.object(telegram, "check_telegram_requirements", return_value=True), \
             patch.object(telegram, "TelegramAdapter", return_value=builtin_adapter):
            result = runner._create_adapter(Platform.TELEGRAM, platform_config)
    finally:
        platform_registry.unregister("telegram")

    assert result is builtin_adapter


def test_create_adapter_registry_loads_telegram_userbot():
    from gateway.platform_registry import platform_registry
    from gateway.run import GatewayRunner

    created_with = {}
    adapter = SimpleNamespace(name="telegram_userbot")

    def create_adapter(config):
        created_with["config"] = config
        return adapter

    entry = PlatformEntry(
        name="telegram_userbot",
        label="Telegram Userbot",
        adapter_factory=create_adapter,
        check_fn=lambda: True,
        source="plugin",
    )

    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(
        group_sessions_per_user=True,
        thread_sessions_per_user=False,
    )
    platform_config = PlatformConfig(
        enabled=True,
        extra={"runtime_config_path": "/tmp/userbot.toml"},
    )

    platform_registry.register(entry)
    try:
        result = runner._create_adapter(Platform.TELEGRAM_USERBOT, platform_config)
    finally:
        platform_registry.unregister("telegram_userbot")

    assert result is adapter
    assert created_with["config"] is platform_config


def test_create_adapter_registry_loads_non_builtin_platform():
    from gateway.platform_registry import platform_registry
    from gateway.run import GatewayRunner

    adapter = SimpleNamespace(name="custom_gateway")
    entry = PlatformEntry(
        name="custom_gateway",
        label="Custom Gateway",
        adapter_factory=lambda _config: adapter,
        check_fn=lambda: True,
        source="plugin",
    )

    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(
        group_sessions_per_user=True,
        thread_sessions_per_user=False,
    )
    platform_config = PlatformConfig(enabled=True, extra={"token": "abc"})

    platform_registry.register(entry)
    try:
        result = runner._create_adapter(Platform("custom_gateway"), platform_config)
    finally:
        platform_registry.unregister("custom_gateway")
        Platform._value2member_map_.pop("custom_gateway", None)
        Platform._member_map_.pop("CUSTOM_GATEWAY", None)

    assert result is adapter


def test_create_adapter_entry_point_lookup_error_falls_through_to_builtin():
    from gateway.platforms import api_server
    from gateway.run import GatewayRunner

    adapter = SimpleNamespace(name="api_server")

    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(
        group_sessions_per_user=True,
        thread_sessions_per_user=False,
    )
    platform_config = PlatformConfig(enabled=True)

    with patch("importlib.metadata.entry_points", side_effect=RuntimeError("metadata broken")), \
         patch.object(api_server, "check_api_server_requirements", return_value=True), \
         patch.object(api_server, "APIServerAdapter", return_value=adapter):
        result = runner._create_adapter(Platform.API_SERVER, platform_config)

    assert result is adapter
