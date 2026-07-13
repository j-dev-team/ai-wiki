from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ai_wiki.lifecycle import load_variant_spec


# This is intentionally broader than the old twelve exact-substring checks. It
# remains deterministic; a separate agent-run evaluator can consume the same
# case shape and record command/root observations without writing user data.
ROUTING_EVALS = [
    ("VAT filing deadline", "tax-wiki", "en", "single"),
    ("corporate income tax adjustment", "tax-wiki", "en", "single"),
    ("withholding tax return", "tax-wiki", "en", "single"),
    ("부가세 신고 기한", "tax-wiki", "ko", "single"),
    ("소득세 원천징수", "tax-wiki", "ko", "single"),
    ("tax-wiki effective date", "tax-wiki", "en", "explicit"),
    ("employment contract overtime", "labor-wiki", "en", "single"),
    ("employment work rules dismissal", "labor-wiki", "en", "single"),
    ("labor four major insurance", "labor-wiki", "en", "single"),
    ("근로계약서 취업규칙", "labor-wiki", "ko", "single"),
    ("해고 통지 임금", "labor-wiki", "ko", "single"),
    ("labor-wiki collective agreement", "labor-wiki", "en", "explicit"),
    ("contract termination notice", "law-wiki", "en", "single"),
    ("civil litigation damages", "law-wiki", "en", "single"),
    ("legal precedent contract interpretation", "law-wiki", "en", "single"),
    ("계약 해제 내용증명", "law-wiki", "ko", "single"),
    ("소송 손해배상 판례", "law-wiki", "ko", "single"),
    ("law-wiki jurisdiction", "law-wiki", "en", "explicit"),
    ("Python sqlite vector index", "ai-wiki", "en", "negative"),
    ("AI knowledge graph design", "ai-wiki", "en", "negative"),
    ("general research note", "ai-wiki", "en", "negative"),
    # Ties use the existing longest-trigger deterministic rule; the corpus
    # records that declared tie-break rather than pretending it is LLM intent.
    ("세금과 근로계약을 함께 검토", "labor-wiki", "ko", "multi-domain"),
    ("tax and employment contract", "labor-wiki", "en", "multi-domain"),
    ("typo vat return deadline", "tax-wiki", "en", "near-miss"),
]


# Skill triggering remains the host agent's responsibility.  This corpus is a
# deterministic contract check: it makes the intended research boundaries
# inspectable without pretending to execute an LLM selection or browse the web.
DEEP_RESEARCH_EVALS = [
    {"id": "ko-current-facts", "language": "ko", "scenario": "current", "report_requested": False},
    {"id": "en-conflicting-sources", "language": "en", "scenario": "conflict", "report_requested": False},
    {"id": "ko-local-evidence-sufficient", "language": "ko", "scenario": "local_sufficient", "report_requested": False},
    {"id": "en-local-evidence-gap", "language": "en", "scenario": "local_insufficient", "report_requested": False},
    {"id": "ko-law-variant", "language": "ko", "scenario": "domain_variant", "report_requested": True},
    {"id": "en-tax-variant", "language": "en", "scenario": "domain_variant", "report_requested": True},
    {"id": "ko-write-not-requested", "language": "ko", "scenario": "write_boundary", "report_requested": False},
    {"id": "en-write-requested", "language": "en", "scenario": "write_boundary", "report_requested": True},
    {"id": "ko-web-unavailable", "language": "ko", "scenario": "web_unavailable", "report_requested": False},
    {"id": "en-stop-condition", "language": "en", "scenario": "stop_condition", "report_requested": False},
]


def evaluate_deep_research_contract(template: Path, *, command_name: str, root_env: str) -> dict[str, Any]:
    """Check that a skill template declares every safety/quality boundary."""
    text = template.read_text(encoding="utf-8") if template.is_file() else ""
    requirements = {
        "wiki_isolation": command_name in text and root_env in text,
        "local_context_first": "context" in text,
        "external_evidence": "authoritative external" in text,
        "claim_ledger": "claim ledger" in text,
        "conflict_and_time": "conflict" in text and "time" in text,
        "stop_condition": "Stop when" in text,
        "read_only_default": "read-only` is the default" in text,
        "explicit_write_boundary": "only when the user requests" in text,
        "web_unavailable": "web access is unavailable" in text,
    }
    passed = all(requirements.values())
    results = [{
        **case,
        "write_allowed": case["report_requested"],
        "passed": passed,
    } for case in DEEP_RESEARCH_EVALS]
    return {
        "status": "ok" if passed else "error",
        "mode": "deterministic_contract_proxy",
        "limitations": [
            "This evaluator verifies the installed skill contract, not host-LLM trigger selection or live web browsing.",
        ],
        "requirements": requirements,
        "passed": sum(item["passed"] for item in results),
        "total": len(results),
        "coverage": {
            "languages": sorted({item["language"] for item in results}),
            "scenarios": sorted({item["scenario"] for item in results}),
            "write_requested_cases": sum(item["report_requested"] for item in results),
        },
        "results": results,
    }


def skill_aliases(spec: Any) -> tuple[str, ...]:
    """Return declared aliases plus the historical underscore directory alias."""
    aliases = list(getattr(spec, "skill_aliases", ()) or ())
    underscore = str(spec.skill_name).replace("-", "_")
    if underscore != spec.skill_name:
        aliases.append(underscore)
    return tuple(dict.fromkeys(alias for alias in aliases if alias != spec.skill_name))


def route_skill(query: str, specs: list[Any]) -> str:
    normalized = query.casefold()
    for spec in specs:
        names = (spec.skill_name, *skill_aliases(spec))
        if any(name.casefold() in normalized for name in names):
            return spec.skill_name
    if "ai-wiki" in normalized:
        return "ai-wiki"

    scored: list[tuple[int, int, str]] = []
    for spec in specs:
        matches = [trigger for trigger in spec.triggers if trigger.casefold() in normalized]
        if matches:
            scored.append((len(matches), sum(len(trigger) for trigger in matches), spec.skill_name))
    return max(scored)[2] if scored else "ai-wiki"


def evaluate_skill_routing(package_dirs: list[Path]) -> dict[str, Any]:
    specs = [load_variant_spec(path.resolve()) for path in package_dirs]
    installed_names = {spec.skill_name for spec in specs}
    results = []
    for query, expected, language, scenario in ROUTING_EVALS:
        # A corpus may be used with a subset of variants in unit tests.
        if expected != "ai-wiki" and expected not in installed_names:
            continue
        actual = route_skill(query, specs)
        results.append({
            "query": query, "expected": expected, "actual": actual,
            "language": language, "scenario": scenario,
            "expected_cli": expected, "expected_root": f"{expected.replace('-', '_').upper()}_ROOT" if expected != "ai-wiki" else "AI_WIKI_ROOT",
            "write_allowed": False,
            "passed": actual == expected,
        })
    passed = sum(1 for result in results if result["passed"])
    return {
        "status": "ok" if passed == len(results) else "error",
        "mode": "deterministic_static_proxy",
        "limitations": [
            "This evaluator does not invoke an LLM skill selector.",
            "No user wiki write is attempted; CLI and root are expected values for a dry-run harness.",
        ],
        "passed": passed,
        "total": len(results),
        "pass_rate": passed / len(results) if results else 0.0,
        "coverage": {
            "languages": sorted({item["language"] for item in results}),
            "scenarios": sorted({item["scenario"] for item in results}),
            "writes_attempted": 0,
        },
        "results": results,
    }


def _agent_bases() -> dict[str, list[Path]]:
    home = Path.home()
    return {
        "claude": [home / ".claude" / "skills"],
        "gemini": [home / ".gemini" / "config" / "skills", home / ".agents" / "skills"],
        "codex": [home / ".codex" / "skills"],
    }


def _copy_skill(source_dir: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for source in source_dir.rglob("*.md"):
        destination = target / source.relative_to(source_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _backup_and_remove(path: Path) -> str:
    backup_root = path.parent / ".ai-wiki-skill-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_root / f"{path.name}-{stamp}"
    number = 1
    while backup.exists():
        number += 1
        backup = backup_root / f"{path.name}-{stamp}-{number}"
    shutil.copytree(path, backup)
    shutil.rmtree(path)
    return str(backup)


def install_variant_skills(package_dir: Path, agents: tuple[str, ...]) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    spec = load_variant_spec(package_dir)
    source_dir = package_dir / "src" / spec.module_name / "skill_templates"
    mission_source = package_dir / "src" / spec.module_name / "mission_skill_templates"
    research_source = package_dir / "src" / spec.module_name / "deep_research_skill_templates"
    if not (source_dir / "SKILL.md").is_file() or not (mission_source / "SKILL.md").is_file():
        raise FileNotFoundError(f"skill templates not found for {spec.skill_name}")
    bases = _agent_bases()
    installed, migrations = [], []
    for agent in agents:
        if agent not in bases:
            raise ValueError(f"unsupported agent: {agent}")
        for base in bases[agent]:
            canonical = base / spec.skill_name
            for alias in skill_aliases(spec):
                legacy = base / alias
                if legacy.exists():
                    backup = _backup_and_remove(legacy)
                    migrations.append({"agent": agent, "legacy_path": str(legacy), "backup": backup})
            _copy_skill(source_dir, canonical)
            _copy_skill(mission_source, base / f"{spec.skill_name}-missions")
            if (research_source / "SKILL.md").is_file():
                _copy_skill(research_source, base / f"{spec.skill_name}-deep-research")
        installed.append({"agent": agent, "path": str(bases[agent][0] / spec.skill_name)})
    config_path = package_dir / spec.config_filename
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config["agents"] = list(dict.fromkeys([*(config.get("agents") or []), *agents]))
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {
        "status": "ok", "action": "variant_skills_installed", "package_name": spec.package_name,
        "installed": installed, "agents": config["agents"], "migrations": migrations,
    }


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _skill_version(path: Path) -> str | None:
    if not path.is_file():
        return None
    match = __import__("re").search(r"^version:\s*([^\s]+)", path.read_text(encoding="utf-8"), __import__("re").MULTILINE)
    return match.group(1) if match else None


def audit_skill_installation(package_dirs: list[Path]) -> dict[str, Any]:
    rows, problems = [], []
    bases = _agent_bases()
    for package_dir in package_dirs:
        package_dir = package_dir.resolve()
        spec = load_variant_spec(package_dir)
        config = yaml.safe_load((package_dir / spec.config_filename).read_text(encoding="utf-8")) or {}
        template_root = package_dir / "src" / spec.module_name
        skill_types = (
            ("primary", template_root / "skill_templates" / "SKILL.md", spec.skill_name),
            ("missions", template_root / "mission_skill_templates" / "SKILL.md", f"{spec.skill_name}-missions"),
            ("deep_research", template_root / "deep_research_skill_templates" / "SKILL.md", f"{spec.skill_name}-deep-research"),
        )
        for agent in config.get("agents") or []:
            for base in bases.get(agent, []):
                for skill_type, expected, skill_directory in skill_types:
                    canonical = base / skill_directory / "SKILL.md"
                    actual_hash = _sha256(canonical)
                    expected_hash = _sha256(expected)
                    actual_version = _skill_version(canonical)
                    expected_version = _skill_version(expected)
                    aliases = (
                        [{"path": str(base / alias), "exists": (base / alias).exists()} for alias in skill_aliases(spec)]
                        if skill_type == "primary" else []
                    )
                    duplicate_aliases = [item["path"] for item in aliases if item["exists"]]
                    ok = bool(actual_hash and actual_hash == expected_hash and actual_version == expected_version and not duplicate_aliases)
                    row = {
                        "package_name": spec.package_name, "agent": agent, "skill_type": skill_type,
                        "path": str(canonical.parent), "exists": canonical.exists(),
                        "expected_sha256": expected_hash, "actual_sha256": actual_hash,
                        "expected_version": expected_version, "actual_version": actual_version,
                        "content_matches": actual_hash == expected_hash, "aliases": aliases,
                        "duplicate_aliases": duplicate_aliases, "ok": ok,
                        "recovery": "Run the variant upgrade after backing up any user-modified alias directory.",
                    }
                    rows.append(row)
                    if not ok:
                        problems.append(row)
    routing = evaluate_skill_routing(package_dirs)
    return {
        "status": "ok" if not problems and routing["status"] == "ok" else "error",
        "installed": not problems, "missing": [row["path"] for row in problems if not row["exists"]],
        "problems": problems, "skills": rows, "routing": routing,
    }
