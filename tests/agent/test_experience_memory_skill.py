import re
from pathlib import Path


SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "autonomous-ai-agents"
    / "experience-memory"
    / "SKILL.md"
)


def _frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match is not None
    data = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.startswith("  "):
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    return data


def test_experience_memory_skill_exists_with_valid_metadata():
    text = SKILL_PATH.read_text(encoding="utf-8")
    meta = _frontmatter(text)

    assert meta["name"] == "experience-memory"
    assert meta["description"] == "Use local experience memory deliberately."
    assert len(meta["description"]) <= 60
    assert meta["description"].endswith(".")
    assert meta["platforms"] == "[linux, macos, windows]"
    assert "Teknium" in meta["author"]


def test_experience_memory_skill_uses_modern_section_order():
    text = SKILL_PATH.read_text(encoding="utf-8")
    sections = [
        "# Experience Memory Skill",
        "## When to Use",
        "## Prerequisites",
        "## How to Run",
        "## Quick Reference",
        "## Procedure",
        "## Pitfalls",
        "## Verification",
    ]
    positions = [text.index(section) for section in sections]

    assert positions == sorted(positions)


def test_experience_memory_skill_guides_native_tool_privacy_and_correction():
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "`experience_memory`" in text
    assert "Do not store secrets" in text
    assert "raw transcripts" in text
    assert "retract" in text
    assert "tombstone" in text
    assert "supersede" in text
    assert "scrub" in text
    assert "Current user instructions" in text
