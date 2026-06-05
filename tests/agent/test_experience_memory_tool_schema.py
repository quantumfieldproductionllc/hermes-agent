from agent.experience_memory.tool_schema import experience_memory_tool_schema


def test_tool_schema_root_description_teaches_recall_record_and_correct():
    schema = experience_memory_tool_schema()
    description = schema["description"].lower()

    assert "use recall before answering" in description
    assert "use record only" in description
    assert "use correct" in description
    assert "current user instructions" in description


def test_record_fields_warn_against_secrets_transcripts_and_one_off_records():
    props = experience_memory_tool_schema()["parameters"]["properties"]
    body_description = props["body"]["description"].lower()
    evidence_description = props["evidence"]["description"].lower()

    assert "secrets" in body_description
    assert "raw transcripts" in body_description
    assert "one-off todos" in body_description
    assert "raw tool payloads" in evidence_description


def test_correct_schema_distinguishes_retract_tombstone_supersede_and_scrub():
    props = experience_memory_tool_schema()["parameters"]["properties"]
    operation_description = props["operation"]["description"].lower()

    for word in ("retract", "tombstone", "supersede", "scrub"):
        assert word in operation_description


def test_supersede_schema_requires_replacement_title_and_body():
    all_of = experience_memory_tool_schema()["parameters"]["allOf"]

    supersede_rule = next(
        rule
        for rule in all_of
        if rule.get("if", {}).get("properties", {}).get("operation", {}).get("const") == "supersede"
    )

    assert set(supersede_rule["then"]["required"]) == {"replacement_title", "replacement_body"}


def test_schema_does_not_add_actions():
    action_enum = experience_memory_tool_schema()["parameters"]["properties"]["action"]["enum"]

    assert action_enum == ["status", "record", "recall", "correct"]
