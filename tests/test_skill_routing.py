from pathlib import Path

import yaml

from ai_wiki.skill_routing import (
    audit_skill_installation, evaluate_deep_research_contract,
    evaluate_skill_routing, install_variant_skills, route_skill,
)
from ai_wiki.variant import VariantSpec, create_variant_package, load_builtin_preset


def _packages(tmp_path):
    packages = []
    for preset in ("law", "labor", "tax"):
        spec = VariantSpec.from_mapping(load_builtin_preset(preset))
        package_dir = Path(create_variant_package(spec, output_dir=tmp_path)["package_dir"])
        (package_dir / spec.config_filename).write_text(yaml.safe_dump({"agents": []}), encoding="utf-8")
        packages.append(package_dir)
    return packages


def test_routing_eval_covers_dedicated_and_near_miss_queries(tmp_path):
    result = evaluate_skill_routing(_packages(tmp_path))
    assert result["total"] >= 12
    assert result["pass_rate"] == 1.0


def test_routing_uses_any_manifest_without_engine_specific_names():
    spec = VariantSpec.build(
        package_name="client-records-wiki",
        triggers=["account review", "meeting notes"],
    )

    assert route_skill("Find the latest account review", [spec]) == "client-records-wiki"
    assert route_skill("Use client-records-wiki for this", [spec]) == "client-records-wiki"
    assert route_skill("Explain vector indexes", [spec]) == "ai-wiki"


def test_skills_install_and_audit_all_agents(tmp_path, monkeypatch):
    packages = _packages(tmp_path)
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    for package_dir in packages:
        install_variant_skills(package_dir, ("claude", "gemini", "codex"))

    result = audit_skill_installation(packages)
    assert result["status"] == "ok"
    assert len(result["skills"]) == 36
    assert {row["skill_type"] for row in result["skills"]} == {
        "primary", "missions", "deep_research",
    }
    assert result["routing"]["pass_rate"] == 1.0
    for package_dir in packages:
        spec = VariantSpec.from_manifest(package_dir / "variant.yaml")
        primary = fake_home / ".gemini" / "config" / "skills" / spec.skill_name / "SKILL.md"
        compatibility = fake_home / ".agents" / "skills" / spec.skill_name / "SKILL.md"
        assert compatibility.read_bytes() == primary.read_bytes()


def test_alias_is_backed_up_migrated_and_audit_compares_template_hash(tmp_path, monkeypatch):
    package_dir = _packages(tmp_path)[0]
    spec = VariantSpec.from_manifest(package_dir / "variant.yaml")
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    legacy = fake_home / ".codex" / "skills" / spec.skill_name.replace("-", "_")
    legacy.mkdir(parents=True)
    (legacy / "SKILL.md").write_text("---\nversion: 0.0.1\n---\nstale", encoding="utf-8")

    result = install_variant_skills(package_dir, ("codex",))
    assert result["migrations"]
    assert not legacy.exists()
    assert Path(result["migrations"][0]["backup"]).joinpath("SKILL.md").exists()
    canonical = fake_home / ".codex" / "skills" / spec.skill_name / "SKILL.md"
    mission = fake_home / ".codex" / "skills" / f"{spec.skill_name}-missions" / "SKILL.md"
    assert canonical.exists()
    assert f"{spec.command_name} run next" in mission.read_text(encoding="utf-8")
    assert "ai-wiki run next" not in mission.read_text(encoding="utf-8")

    canonical.write_text("tampered", encoding="utf-8")
    audit = audit_skill_installation([package_dir])
    assert audit["status"] == "error"
    assert audit["problems"][0]["content_matches"] is False
    assert audit["problems"][0]["expected_version"] == "1.2.0"
    assert audit["problems"][0]["recovery"]


def test_routing_corpus_has_language_scenario_and_write_isolation_metadata(tmp_path):
    result = evaluate_skill_routing(_packages(tmp_path))
    assert result["total"] >= 20
    assert {"ko", "en"} <= set(result["coverage"]["languages"])
    assert {"negative", "multi-domain", "near-miss"} <= set(result["coverage"]["scenarios"])
    assert result["coverage"]["writes_attempted"] == 0
    assert result["mode"] == "deterministic_static_proxy"
    assert result["limitations"]
    assert all(item["write_allowed"] is False for item in result["results"])


def test_deep_research_contract_corpus_covers_boundaries_and_variants(tmp_path):
    package_dir = _packages(tmp_path)[0]
    spec = VariantSpec.from_manifest(package_dir / "variant.yaml")
    result = evaluate_deep_research_contract(
        package_dir / "src" / spec.module_name / "deep_research_skill_templates" / "SKILL.md",
        command_name=spec.command_name,
        root_env=f"{spec.env_prefix}_ROOT",
    )
    assert result["status"] == "ok"
    assert result["passed"] == result["total"] >= 10
    assert {"ko", "en"} <= set(result["coverage"]["languages"])
    assert {"current", "conflict", "domain_variant", "write_boundary", "web_unavailable"} <= set(result["coverage"]["scenarios"])
    assert result["coverage"]["write_requested_cases"] == 3
    assert result["requirements"]["read_only_default"] is True
    assert any(item["write_allowed"] is False for item in result["results"])
    assert any(item["write_allowed"] is True for item in result["results"])


def test_generic_law_labor_tax_and_woosong_mission_installation_are_isolated(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    package_specs = [
        VariantSpec.from_mapping(load_builtin_preset(name)) for name in ("law", "labor", "tax")
    ]
    package_specs.append(VariantSpec.build(
        package_name="woosong-wiki", display_name="Woosong Wiki", domain="business",
        triggers=["customer", "consultation"],
    ))
    packages = []
    for spec in package_specs:
        package = Path(create_variant_package(spec, output_dir=tmp_path)["package_dir"])
        (package / spec.config_filename).write_text(yaml.safe_dump({"agents": []}), encoding="utf-8")
        install_variant_skills(package, ("codex",))
        packages.append(package)

    audit = audit_skill_installation(packages)
    assert audit["status"] == "ok"
    generic = (Path(__file__).parents[1] / "src" / "ai_wiki" / "mission_skill_templates" / "SKILL.md").read_text(encoding="utf-8")
    assert "ai-wiki run next" in generic
    for spec in package_specs:
        text = (fake_home / ".codex" / "skills" / f"{spec.skill_name}-missions" / "SKILL.md").read_text(encoding="utf-8")
        research = (fake_home / ".codex" / "skills" / f"{spec.skill_name}-deep-research" / "SKILL.md").read_text(encoding="utf-8")
        assert f"{spec.command_name} run next" in text
        assert f"Expected root selector: `{spec.env_prefix}_ROOT`" in text
        assert "ai-wiki run next" not in text
        assert f"{spec.command_name}` with `{spec.env_prefix}_ROOT`" in research
