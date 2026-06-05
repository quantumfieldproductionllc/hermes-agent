from hermes_constants import reset_hermes_home_override, set_hermes_home_override


def test_experience_memory_defaults_are_disabled():
    from hermes_cli.config import DEFAULT_CONFIG

    cfg = DEFAULT_CONFIG["experience_memory"]
    assert cfg["enabled"] is False
    assert cfg["mode"] == "shadow"
    assert cfg["tools_enabled"] is True
    assert cfg["prefetch_enabled"] is False
    assert cfg["extraction"]["enabled"] is False
    assert cfg["projections"]["enabled"] is False
    assert "experience_memory" not in DEFAULT_CONFIG.get("auxiliary", {})


def test_ensure_hermes_home_creates_experience_directory(tmp_path):
    from hermes_cli.config import ensure_hermes_home

    token = set_hermes_home_override(tmp_path)
    try:
        ensure_hermes_home()
    finally:
        reset_hermes_home_override(token)

    assert (tmp_path / "experience").is_dir()


def test_experience_memory_is_configurable_toolset():
    from hermes_cli.tools_config import CONFIGURABLE_TOOLSETS

    keys = {ts_key for ts_key, _, _ in CONFIGURABLE_TOOLSETS}
    assert "experience_memory" in keys
