from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from run_agent import AIAgent


def _mock_response(content: str = "done"):
    message = SimpleNamespace(
        content=content,
        reasoning=None,
        reasoning_content=None,
        tool_calls=None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop", index=0)],
        usage=None,
        model="test/model",
        id="chatcmpl-test",
    )


def _make_agent(*, api_mode: str = "chat_completions"):
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            api_mode=api_mode,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            max_iterations=3,
        )
    agent.client = MagicMock()
    agent.client.chat.completions.create.return_value = _mock_response()
    agent._disable_streaming = True
    return agent


def _fake_plugin_context(name, **kwargs):
    if name == "pre_llm_call":
        return [{"context": "<plugin-context>plugin fact</plugin-context>"}]
    return []


def _user_message_from_request(agent):
    sent = agent.client.chat.completions.create.call_args.kwargs["messages"]
    return next(msg for msg in sent if msg.get("role") == "user")


def test_experience_memory_context_injected_into_current_api_user_message_only(monkeypatch):
    agent = _make_agent()
    block = "<experience-memory-context>\nrecord\n</experience-memory-context>"
    engine = MagicMock()
    engine.prefetch.return_value = block
    agent._experience_memory = engine
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_plugin_context)

    result = agent.run_conversation("How do Hermes tests run?")

    api_user = _user_message_from_request(agent)
    content = api_user["content"]
    assert "How do Hermes tests run?" in content
    assert "<memory-context>" not in content or content.index("<experience-memory-context>") > content.index("<memory-context>")
    assert block in content
    assert content.index(block) < content.index("<plugin-context>")
    assert result["messages"][0]["content"] == "How do Hermes tests run?"
    assert all(
        "<experience-memory-context>" not in str(msg.get("content"))
        for msg in result["messages"]
        if msg.get("role") == "system"
    )
    engine.prefetch.assert_called_once_with("How do Hermes tests run?", session_id=agent.session_id or "")


def test_external_memory_eme_and_plugin_context_order_is_stable(monkeypatch):
    agent = _make_agent()
    agent._memory_manager = MagicMock()
    agent._memory_manager.prefetch_all.return_value = "external fact"
    agent._memory_manager.build_system_prompt.return_value = ""
    block = "<experience-memory-context>\neme fact\n</experience-memory-context>"
    engine = MagicMock()
    engine.prefetch.return_value = block
    agent._experience_memory = engine
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_plugin_context)

    agent.run_conversation("Use prior local context")

    content = _user_message_from_request(agent)["content"]
    assert content.index("<memory-context>") < content.index("<experience-memory-context>")
    assert content.index("<experience-memory-context>") < content.index("<plugin-context>")


def test_multimodal_injection_uses_fresh_api_list_and_does_not_mutate_original(monkeypatch):
    agent = _make_agent()
    block = "<experience-memory-context>\nvision rule\n</experience-memory-context>"
    engine = MagicMock()
    engine.prefetch.return_value = block
    agent._experience_memory = engine
    original_content = [
        {"type": "text", "text": "What should I remember about this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="}},
    ]
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda name, **kwargs: [])

    result = agent.run_conversation(original_content)

    api_content = _user_message_from_request(agent)["content"]
    assert isinstance(api_content, str)
    assert api_content is not original_content
    assert len(original_content) == 2
    assert "What should I remember about this?" in api_content
    assert block in api_content
    assert result["messages"][0]["content"] is original_content
    engine.prefetch.assert_called_once_with(
        "What should I remember about this?",
        session_id=agent.session_id or "",
    )


def test_multimodal_without_text_skips_experience_memory_prefetch(monkeypatch):
    agent = _make_agent()
    engine = MagicMock()
    engine.prefetch.return_value = "<experience-memory-context>x</experience-memory-context>"
    agent._experience_memory = engine
    content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="}}]
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda name, **kwargs: [])

    result = agent.run_conversation(content)

    engine.prefetch.assert_not_called()
    assert result["messages"][0]["content"] is content


def test_codex_app_server_skips_hidden_experience_memory_prefetch(monkeypatch):
    agent = _make_agent(api_mode="codex_app_server")
    engine = MagicMock()
    engine.prefetch.return_value = "<experience-memory-context>x</experience-memory-context>"
    agent._experience_memory = engine
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda name, **kwargs: [])

    def fake_codex_turn(**kwargs):
        return {
            "final_response": "codex ok",
            "messages": kwargs["messages"],
            "api_calls": 1,
            "completed": True,
        }

    agent._run_codex_app_server_turn = fake_codex_turn

    result = agent.run_conversation("Remember that codex should skip hidden prefetch.")

    assert result["final_response"] == "codex ok"
    engine.prefetch.assert_not_called()
