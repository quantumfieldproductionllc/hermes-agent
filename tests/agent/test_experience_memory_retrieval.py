from agent.experience_memory.models import ExperienceQuery, ExperienceRecord, ExperienceScope
from agent.experience_memory.retrieval import sanitize_fts_query
from agent.experience_memory.store import ExperienceStore


def test_sanitize_fts_query_strips_question_stopwords_for_prefetch():
    assert sanitize_fts_query("How should we run targeted Hermes EME tests?") == (
        "run targeted Hermes EME tests"
    )


def test_invalid_fts_query_returns_empty_without_raising(tmp_path):
    store = ExperienceStore(db_path=tmp_path / "experience.db")
    store.open()
    scope = ExperienceScope(profile_id="p")
    try:
        store.record(
            ExperienceRecord(
                kind="case",
                scope=scope,
                title="C++ parser",
                body="handle special syntax safely",
            )
        )
        assert store.recall(ExperienceQuery(query='("', scope=scope, limit=5)) == []
    finally:
        store.close()


def test_recall_truncates_to_token_budget(tmp_path):
    store = ExperienceStore(db_path=tmp_path / "experience.db")
    store.open()
    scope = ExperienceScope(profile_id="p")
    try:
        store.record(
            ExperienceRecord(
                kind="case",
                scope=scope,
                title="Budget record",
                body="budget " + ("x" * 200),
            )
        )
        results = store.recall(
            ExperienceQuery(query="budget", scope=scope, limit=1, token_budget=20)
        )
        assert len(results) == 1
        assert len(results[0].body) < 200
        assert results[0].body.endswith("...")
    finally:
        store.close()
