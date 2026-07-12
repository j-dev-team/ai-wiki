from pathlib import Path

import yaml

from ai_wiki.skill_routing import audit_skill_installation, evaluate_skill_routing, install_variant_skills, route_skill
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
    assert len(result["skills"]) == 9
    assert result["routing"]["pass_rate"] == 1.0
    for package_dir in packages:
        spec = VariantSpec.from_manifest(package_dir / "variant.yaml")
        primary = fake_home / ".gemini" / "config" / "skills" / spec.skill_name / "SKILL.md"
        compatibility = fake_home / ".agents" / "skills" / spec.skill_name / "SKILL.md"
        assert compatibility.read_bytes() == primary.read_bytes()
