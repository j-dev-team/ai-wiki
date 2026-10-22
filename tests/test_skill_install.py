"""Skill installation copies the whole template tree and verifies references."""
from __future__ import annotations

import io
import json
import re
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import ai_wiki.cli as cli_module
from ai_wiki.variant import VariantSpec, create_variant_package, load_builtin_preset

PACKAGE = Path(cli_module.__file__).parent
TEMPLATE_ROOTS = (
    PACKAGE / "skill_templates",
    PACKAGE / "mission_skill_templates",
    PACKAGE / "deep_research_skill_templates",
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setenv("AI_WIKI_ROOT", str(wiki))
    return home


def _upgrade(*agents: str):
    args = ["upgrade-skill"]
    for agent in agents:
        args += ["--agent", agent]
    return CliRunner().invoke(cli_module.cli, args)


def _verification(output: str) -> dict:
    line = next(line for line in output.splitlines() if line.startswith('{"skill_upgrade_verification"'))
    return json.loads(line)


def _frontmatter(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])


@pytest.mark.parametrize("root", TEMPLATE_ROOTS, ids=lambda p: p.name)
def test_every_bundled_skill_reference_exists(root):
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    referenced = set(re.findall(r"references/[\w./-]+\.md", text))
    assert all((root / item).is_file() for item in referenced), referenced


def test_primary_skill_points_to_each_reference_with_a_trigger():
    text = (PACKAGE / "skill_templates" / "SKILL.md").read_text(encoding="utf-8")
    for name in ("templates", "enrichment", "protocol", "operations"):
        assert f"`references/{name}.md`" in text
    installable = cli_module._skill_template_files(PACKAGE / "skill_templates")
    assert not [path for path in installable if path.name.startswith("ref-")]


def test_leftover_legacy_ref_files_in_templates_are_not_installed(tmp_path):
    templates = tmp_path / "skill_templates"
    (templates / "references").mkdir(parents=True)
    (templates / "SKILL.md").write_text("Read `references/templates.md`.", encoding="utf-8")
    (templates / "references" / "templates.md").write_text("new", encoding="utf-8")
    (templates / "ref-templates.md").write_text("old", encoding="utf-8")
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / "ref-templates.md").write_text("old", encoding="utf-8")

    copied = cli_module._copy_skill_tree(templates, installed)
    assert copied == ["SKILL.md", "references/templates.md"]
    assert not (installed / "ref-templates.md").exists()


def test_primary_description_routes_to_dedicated_wikis_and_has_korean_triggers():
    description = _frontmatter(PACKAGE / "skill_templates" / "SKILL.md")["description"]
    assert len(description) <= 1024
    for keyword in ("조사", "개념", "기술", "일반 지식"):
        assert keyword in description
    for wiki in ("tax-wiki", "labor-wiki", "law-wiki", "business-wiki"):
        assert wiki in description
    assert "use that dedicated wiki first" in description
    assert "only when no dedicated wiki applies" in description


def test_paired_skill_descriptions_have_korean_triggers():
    missions = _frontmatter(PACKAGE / "mission_skill_templates" / "SKILL.md")["description"]
    research = _frontmatter(PACKAGE / "deep_research_skill_templates" / "SKILL.md")["description"]
    assert "미션" in missions and "재개" in missions
    assert "심층 조사" in research and "출처 비교" in research
    assert len(missions) <= 1024 and len(research) <= 1024


def test_enrichment_guide_uses_versioned_patch_not_legacy_update():
    text = (PACKAGE / "skill_templates" / "references" / "enrichment.md").read_text(encoding="utf-8")
    assert "ai-wiki update <id> --content-file" not in text.replace("`ai-wiki update <id> --content-file ...`", "")
    get_at = text.index("ai-wiki get <id>")
    dry_at = text.index("--if-version <version> --dry-run")
    apply_at = text.index("ai-wiki patch <id> --operations-file patch.json --if-version <version>\n")
    assert get_at < dry_at < apply_at


def test_upgrade_skill_installs_references_for_all_three_skills(fake_home):
    claude = fake_home / ".claude" / "skills"
    legacy = claude / "ai-wiki" / "ref-enrichment.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("old", encoding="utf-8")

    result = _upgrade("claude")
    assert result.exit_code == 0, result.output
    for name in ("templates", "enrichment", "protocol", "operations"):
        assert (claude / "ai-wiki" / "references" / f"{name}.md").is_file()
    assert not legacy.exists(), "stale top-level ref-*.md must be removed"
    assert (claude / "ai-wiki-missions" / "references" / "mission-examples.ko.md").is_file()
    research = (claude / "ai-wiki-deep-research" / "SKILL.md").read_text(encoding="utf-8")
    assert "__SKILL_NAME__" not in research and "name: ai-wiki-deep-research" in research

    report = _verification(result.output)
    assert all(item["matches"] for item in report["skill_upgrade_verification"])
    assert len(report["skill_reference_verification"]) == 3
    assert all(item["ok"] for item in report["skill_reference_verification"])


def test_upgrade_skill_copies_variant_references(fake_home, tmp_path, monkeypatch):
    spec = VariantSpec.from_mapping(load_builtin_preset("tax"))
    package = Path(create_variant_package(spec, output_dir=tmp_path / "pkg")["package_dir"])
    templates = package / "src" / spec.module_name / "skill_templates"
    monkeypatch.setattr(cli_module, "_skill_templates_dir", lambda: templates)

    result = _upgrade("codex")
    assert result.exit_code == 0, result.output
    installed = fake_home / ".codex" / "skills" / "ai-wiki"
    assert (installed / "references" / "domain-checklist.ko.md").is_file()
    assert "references/domain-checklist.ko.md" in (installed / "SKILL.md").read_text(encoding="utf-8")
    assert (installed.parent / "ai-wiki-missions" / "references" / "mission-examples.ko.md").is_file()


def test_upgrade_skill_fails_when_skill_md_points_to_missing_file(fake_home, tmp_path, monkeypatch):
    templates = tmp_path / "broken" / "skill_templates"
    templates.mkdir(parents=True)
    (templates / "SKILL.md").write_text(
        "---\nname: ai-wiki\nversion: 9.9.9\n---\nRead `references/absent.md` first.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "_skill_templates_dir", lambda: templates)
    result = _upgrade("claude")
    assert result.exit_code != 0
    report = _verification(result.output)
    broken = [item for item in report["skill_reference_verification"] if not item["ok"]]
    assert broken and broken[0]["missing"] == ["references/absent.md"]


def test_init_style_install_also_copies_references(tmp_path, monkeypatch):
    monkeypatch.setitem(cli_module._AGENT_SKILL_PATHS, "codex", lambda name: tmp_path / name)
    files = cli_module._skill_template_files(cli_module._skill_templates_dir())
    cli_module._install_skills_for_agents(["codex"], "sample-wiki", files)
    assert (tmp_path / "sample-wiki" / "references" / "templates.md").is_file()
    assert cli_module._missing_skill_references(tmp_path / "sample-wiki") == []
    assert cli_module._missing_skill_references(tmp_path / "sample-wiki-missions") == []


# ── embedding model loading keeps stdout clean ─────────────────────────────

@pytest.fixture
def noisy_sentence_transformers(monkeypatch):
    seen = {}

    class NoisyModel:
        def __init__(self, name, *args, **kwargs):
            import os
            import warnings
            seen["offline"] = os.environ.get("HF_HUB_OFFLINE")
            print("Loading weights: 100%|##########|")  # stray stdout chatter
            warnings.warn("unauthenticated requests to the HF Hub")

        def encode(self, texts, **kwargs):
            print("Batches: 100%")
            return [[1.0] + [0.0] * 383 for _ in texts]

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = NoisyModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    import ai_wiki.vector as vector
    monkeypatch.setattr(vector, "_model", None)
    # setenv-then-delenv records "absent" so values written by the loader are undone.
    for name in (
        "HF_HUB_OFFLINE", "AI_WIKI_MODEL_VERBOSE", "HF_HUB_DISABLE_PROGRESS_BARS",
        "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_DISABLE_IMPLICIT_TOKEN", "TRANSFORMERS_VERBOSITY",
        "TRANSFORMERS_NO_ADVISORY_WARNINGS", "TOKENIZERS_PARALLELISM",
        # A developer's own cache settings must not leak into cache detection.
        "SENTENCE_TRANSFORMERS_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME",
    ):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    return vector, seen


def _fake_cached_model(cache: Path, model: str) -> None:
    snapshot = cache / ("models--" + model.replace("/", "--")) / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "modules.json").write_text("[]", encoding="utf-8")
    (snapshot / "model.safetensors").write_bytes(b"0")


def test_cached_model_loads_offline_without_stdout_noise(noisy_sentence_transformers, tmp_path, monkeypatch):
    vector, seen = noisy_sentence_transformers
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    _fake_cached_model(tmp_path / "hub", vector._MODEL_NAME)
    assert vector._model_is_cached()

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        vectors = vector._encode_texts(["hello"], query=True)
    assert stdout.getvalue() == ""
    assert seen["offline"] == "1"
    assert len(vectors[0]) == 384


def test_uncached_model_stays_online_and_explicit_setting_wins(noisy_sentence_transformers, tmp_path, monkeypatch):
    vector, seen = noisy_sentence_transformers
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf-home"))
    assert not vector._model_is_cached()
    with redirect_stdout(io.StringIO()):
        vector._get_model()
    assert seen["offline"] is None

    monkeypatch.setattr(vector, "_model", None)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    _fake_cached_model(tmp_path / "empty", vector._MODEL_NAME)
    with redirect_stdout(io.StringIO()):
        vector._get_model()
    assert seen["offline"] == "0"


@pytest.mark.parametrize("preset", ["law", "labor", "tax", "business", "corporate", "research", "personal", "tech"])
def test_variant_companion_descriptions_route_and_trigger_in_korean(preset, tmp_path):
    spec = VariantSpec.from_mapping(load_builtin_preset(preset))
    package = Path(create_variant_package(spec, output_dir=tmp_path)["package_dir"])
    module = package / "src" / spec.module_name
    missions = _frontmatter(module / "mission_skill_templates" / "SKILL.md")
    research = _frontmatter(module / "deep_research_skill_templates" / "SKILL.md")

    assert missions["name"] == f"{spec.skill_name}-missions"
    assert research["name"] == f"{spec.skill_name}-deep-research"
    for description in (missions["description"], research["description"]):
        assert 300 <= len(description) <= 1024
        assert spec.command_name in description
        assert f"{spec.env_prefix}_ROOT" in description
        assert spec.triggers[0] in description
    for keyword in ("미션", "재개", "핸드오프"):
        assert keyword in missions["description"]
    assert "ai-wiki-missions" in missions["description"]
    for keyword in ("심층 조사", "출처 비교", "리서치 보고서"):
        assert keyword in research["description"]
    assert "ai-wiki-deep-research" in research["description"]
