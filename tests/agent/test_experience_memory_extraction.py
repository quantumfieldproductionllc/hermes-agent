from agent.experience_memory.extraction import extract_explicit_signals


def _config(*, allow_user_model=True):
    return {
        "privacy": {
            "allow_user_model": allow_user_model,
            "store_raw_excerpts": False,
        },
        "extraction": {
            "max_records_per_turn": 3,
            "max_title_chars": 90,
            "max_body_chars": 700,
            "max_raw_excerpt_chars": 240,
        },
    }


def _one(message: str, *, allow_user_model=True):
    records = extract_explicit_signals(
        message,
        config=_config(allow_user_model=allow_user_model),
        source_turn_index=7,
    )
    assert len(records) == 1
    return records[0]


def test_remember_that_extracts_durable_repo_rule_or_case():
    record = _one("Remember that this repo uses scripts/run_tests.sh for tests.")

    assert record.kind in {"rule", "case"}
    assert record.signal == "remember_that"
    assert record.body == "this repo uses scripts/run_tests.sh for tests."
    assert record.evidence == {
        "source": "completed_turn_sync",
        "signal": "remember_that",
        "source_turn_index": 7,
    }


def test_for_future_reference_user_preference_becomes_user_model_update():
    record = _one("For future reference: I prefer concise summaries.")

    assert record.kind == "user_model_update"
    assert record.signal == "for_future_reference"


def test_explicit_kind_signals_are_deterministic():
    cases = [
        ("Record this as a decision: keep EME as local SQLite.", "decision"),
        (
            "Remember this as a rejected hypothesis: direct registry dispatch is wrong for EME.",
            "rejected_hypothesis",
        ),
        ("Record this as a skill candidate: document the EME workflow.", "skill_candidate"),
    ]

    for message, expected_kind in cases:
        assert _one(message).kind == expected_kind


def test_pronoun_only_and_one_off_reminders_are_skipped():
    assert extract_explicit_signals("Remember this.", config=_config()) == []
    assert extract_explicit_signals("Remember to send the report tomorrow.", config=_config()) == []


def test_remember_to_requires_durable_marker():
    record = _one("Remember to always run Hermes tests through scripts/run_tests.sh.")

    assert record.kind == "rule"
    assert record.body == "always run Hermes tests through scripts/run_tests.sh."


def test_likely_secret_payload_is_skipped_without_storing_secret():
    fake_secret = "sk-" + ("a" * 24)
    message = f"Remember that the temporary API key is {fake_secret}."

    assert extract_explicit_signals(message, config=_config()) == []


def test_natural_language_credential_payloads_are_skipped():
    messages = [
        "Remember that my password is hunter2 for the staging database.",
        "Remember that the token is abcdef1234567890abcdef for staging.",
        "Remember that the secret is swordfish for the staging database.",
        "Remember that my password for staging database is hunter2.",
        "Remember that the API key for staging is abcdef1234567890abcdef.",
        "Remember that the database secret for staging is swordfish.",
        "Remember that my login credentials are hunter2 for the staging database.",
    ]

    for message in messages:
        assert extract_explicit_signals(message, config=_config()) == []


def test_negated_memory_instructions_are_skipped():
    messages = [
        "Do not remember that I prefer verbose summaries for this repo.",
        "Please don't remember that this repo uses uv for tests.",
        "Never record this: the project uses pytest for tests.",
        "I don't want you to remember that this repo uses uv for tests.",
        "Do not ever remember that this repo uses uv for tests.",
        "Please do not ever record this: the repo uses uv for tests.",
    ]

    for message in messages:
        assert extract_explicit_signals(message, config=_config()) == []


def test_limits_to_first_three_accepted_records():
    message = (
        "Remember that this repo uses scripts/run_tests.sh for tests. "
        "Record this as a decision: keep EME as local SQLite. "
        "Remember this as a rejected hypothesis: direct registry dispatch is wrong for EME. "
        "Record this as a skill candidate: document the EME workflow."
    )

    records = extract_explicit_signals(message, config=_config())

    assert len(records) == 3
    assert [record.signal for record in records] == [
        "remember_that",
        "record_decision",
        "remember_rejected_hypothesis",
    ]


def test_user_model_update_skipped_when_policy_disallows_it():
    records = extract_explicit_signals(
        "For future reference: I prefer concise summaries.",
        config=_config(allow_user_model=False),
    )

    assert records == []
