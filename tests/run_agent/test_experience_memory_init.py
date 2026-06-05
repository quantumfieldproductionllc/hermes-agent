from unittest.mock import patch

import pytest

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from run_agent import AIAgent


def _tool_defs(*names):
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"{name} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


@pytest.fixture()
def hermes_home(tmp_path):
    token = set_hermes_home_override(tmp_path)
    try:
        yield tmp_path
    finally:
        reset_hermes_home_override(token)


def _write_config(home, *, enabled=True, tools_enabled=True):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        f"""
experience_memory:
  enabled: {str(enabled).lower()}
  mode: shadow
  tools_enabled: {str(tools_enabled).lower()}
  prefetch_enabled: false
""".strip()
        + "\n"
    )


def _make_agent(**kwargs):
    with (
        patch("run_agent.get_tool_definitions", return_value=_tool_defs()),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        return AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            **kwargs,
        )


def test_experience_memory_disabled_by_default(hermes_home):
    agent = _make_agent(enabled_toolsets=["experience_memory"])
    try:
        assert agent._experience_memory is None
        assert "experience_memory" not in agent.valid_tool_names
        assert not (hermes_home / "experience" / "experience.db").exists()
    finally:
        agent.shutdown_memory_provider([])


def test_experience_memory_initializes_when_enabled(hermes_home):
    _write_config(hermes_home, enabled=True)

    agent = _make_agent(enabled_toolsets=["experience_memory"])
    try:
        assert agent._experience_memory is not None
        assert "experience_memory" in agent.valid_tool_names
        assert "experience_memory" in agent._experience_memory_tool_names
        assert (hermes_home / "experience" / "experience.db").exists()
    finally:
        agent.shutdown_memory_provider([])


def test_skip_memory_disables_experience_memory_even_when_enabled(hermes_home):
    _write_config(hermes_home, enabled=True)

    agent = _make_agent(enabled_toolsets=["experience_memory"], skip_memory=True)
    try:
        assert agent._experience_memory is None
        assert "experience_memory" not in agent.valid_tool_names
    finally:
        agent.shutdown_memory_provider([])


def test_disabled_toolsets_suppress_dynamic_schema(hermes_home):
    _write_config(hermes_home, enabled=True)

    agent = _make_agent(enabled_toolsets=["experience_memory"], disabled_toolsets=["memory"])
    try:
        assert agent._experience_memory is not None
        assert "experience_memory" not in agent.valid_tool_names
        assert agent._experience_memory_tool_names == set()
    finally:
        agent.shutdown_memory_provider([])


def test_gateway_metadata_initializes_chat_scope(hermes_home):
    _write_config(hermes_home, enabled=True)

    agent = _make_agent(
        enabled_toolsets=["experience_memory"],
        platform="telegram",
        user_id="user-1",
        chat_id="chat-1",
    )
    try:
        assert agent._experience_memory is not None
        assert agent._experience_memory.scope.scope_level == "chat"
        assert agent._experience_memory.scope.user_scope_hash
        assert agent._experience_memory.scope.chat_scope_hash
    finally:
        agent.shutdown_memory_provider([])
