import json
from types import SimpleNamespace

import model_tools
from agent.agent_runtime_helpers import invoke_tool
from agent.tool_executor import execute_tool_calls_sequential


class _Guardrails:
    def before_call(self, name, args):
        return SimpleNamespace(allows_execution=True)


class _Hints:
    def check_tool_call(self, name, args):
        return ""


class _Checkpoint:
    enabled = False


class _ExperienceMemory:
    def __init__(self):
        self.calls = []

    def handle_tool_call(self, name, args, **kwargs):
        self.calls.append((name, args, kwargs))
        return json.dumps({"ok": True, "name": name, "action": args.get("action")})


def _agent():
    eme = _ExperienceMemory()
    return SimpleNamespace(
        _experience_memory=eme,
        _experience_memory_tool_names={"experience_memory"},
        session_id="session-1",
        _interrupt_requested=False,
        _tool_guardrails=_Guardrails(),
        quiet_mode=True,
        verbose_logging=False,
        log_prefix_chars=80,
        log_prefix="",
        _checkpoint_mgr=_Checkpoint(),
        tool_progress_callback=None,
        tool_start_callback=None,
        tool_complete_callback=None,
        tool_delay=0,
        _current_tool=None,
        _subdirectory_hints=_Hints(),
        valid_tool_names={"experience_memory"},
        enabled_toolsets=["experience_memory"],
        disabled_toolsets=[],
        _memory_manager=None,
        _memory_store=None,
        clarify_callback=None,
        _touch_activity=lambda *a, **k: None,
        _should_emit_quiet_tool_messages=lambda: False,
        _tool_result_content_for_active_model=lambda name, result: result,
        _apply_pending_steer_to_tool_results=lambda messages, count: None,
        _record_file_mutation_result=lambda *a, **k: None,
        _append_guardrail_observation=lambda name, args, result, failed=False: result,
    )


def test_invoke_tool_routes_experience_memory_before_registry():
    agent = _agent()
    result = json.loads(
        invoke_tool(
            agent,
            "experience_memory",
            {"action": "status"},
            "task-1",
            tool_call_id="tc-1",
            pre_tool_block_checked=True,
        )
    )

    assert result == {"ok": True, "name": "experience_memory", "action": "status"}
    assert agent._experience_memory.calls[0][2]["tool_call_id"] == "tc-1"


def test_execute_tool_calls_sequential_routes_experience_memory_before_registry():
    agent = _agent()
    assistant_message = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                id="tc-1",
                function=SimpleNamespace(
                    name="experience_memory",
                    arguments=json.dumps({"action": "status"}),
                ),
            )
        ]
    )
    messages = []

    execute_tool_calls_sequential(agent, assistant_message, messages, "task-1")

    assert len(agent._experience_memory.calls) == 1
    assert len(messages) == 1
    assert messages[0]["name"] == "experience_memory"
    assert json.loads(messages[0]["content"])["ok"] is True


def test_model_tools_registry_guard_for_experience_memory():
    result = json.loads(model_tools.handle_function_call("experience_memory", {"action": "status"}))

    assert result["ok"] is False
    assert "agent-level dynamic tool" in result["error"]
