from pathlib import Path

from ai_wiki.runtime import activate_variant, get_runtime


def test_activate_variant_maps_runtime_and_isolates_root(tmp_path, monkeypatch):
    root = tmp_path / "law-data"
    templates = tmp_path / "skills"
    monkeypatch.setenv("LAW_WIKI_ROOT", str(root))
    for name in (
        "AI_WIKI_COMMAND_NAME",
        "AI_WIKI_DISPLAY_NAME",
        "AI_WIKI_DOMAIN",
        "AI_WIKI_SKILL_NAME",
        "AI_WIKI_ENV_PREFIX",
        "AI_WIKI_CONFIG_FILENAME",
        "AI_WIKI_DEFAULT_PRESET",
        "AI_WIKI_SKILL_TEMPLATE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    runtime = activate_variant(
        {
            "command_name": "law-wiki",
            "display_name": "법률위키",
            "domain": "law",
            "skill_name": "law-wiki",
            "env_prefix": "LAW_WIKI",
            "config_filename": ".law-wiki.yaml",
        },
        skill_template_dir=templates,
    )

    assert runtime == get_runtime()
    assert runtime.command_name == "law-wiki"
    assert runtime.default_preset == "law"
    assert runtime.root_env_name == "LAW_WIKI_ROOT"
    assert runtime.skill_template_dir == Path(templates).resolve()
    assert Path(__import__("os").environ["AI_WIKI_ROOT"]) == root
