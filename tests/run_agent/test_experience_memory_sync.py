from unittest.mock import MagicMock

from run_agent import AIAgent


def _bare_agent():
    agent = AIAgent.__new__(AIAgent)
    agent._experience_memory = MagicMock()
    agent.session_id = "session-1"
    agent._user_turn_count = 4
    return agent


def test_experience_memory_sync_completed_turn_calls_engine():
    agent = _bare_agent()
    messages = [{"role": "user", "content": "Remember that this repo uses scripts/run_tests.sh."}]

    agent._sync_experience_memory_for_turn(
        original_user_message="Remember that this repo uses scripts/run_tests.sh.",
        final_response="ack",
        completed=True,
        failed=False,
        interrupted=False,
        messages=messages,
    )

    agent._experience_memory.sync_turn.assert_called_once_with(
        original_user_message="Remember that this repo uses scripts/run_tests.sh.",
        final_response="ack",
        messages=messages,
        completed=True,
        failed=False,
        interrupted=False,
        session_id="session-1",
        source_turn_index=4,
        turn_error=None,
    )


def test_experience_memory_sync_skips_incomplete_failed_interrupted_or_error_turns():
    for kwargs in (
        {"completed": False, "failed": False, "interrupted": False, "turn_error": None},
        {"completed": True, "failed": True, "interrupted": False, "turn_error": None},
        {"completed": True, "failed": False, "interrupted": True, "turn_error": None},
        {"completed": True, "failed": False, "interrupted": False, "turn_error": "boom"},
    ):
        agent = _bare_agent()
        agent._sync_experience_memory_for_turn(
            original_user_message="Remember that this repo uses scripts/run_tests.sh.",
            final_response="ack",
            messages=[],
            **kwargs,
        )
        agent._experience_memory.sync_turn.assert_not_called()


def test_experience_memory_sync_fail_open_when_engine_raises():
    agent = _bare_agent()
    agent._experience_memory.sync_turn.side_effect = RuntimeError("store locked")

    agent._sync_experience_memory_for_turn(
        original_user_message="Remember that this repo uses scripts/run_tests.sh.",
        final_response="ack",
        completed=True,
        failed=False,
        interrupted=False,
        messages=[],
    )
