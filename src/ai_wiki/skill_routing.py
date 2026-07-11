from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

from ai_wiki.lifecycle import load_variant_spec


ROUTING_EVALS = [
    ("부가세 신고 절차와 최근 세법개정 내용을 정리해", "tax-wiki"),
    ("법인세 중간예납 신고 때 주의사항을 저장해", "tax-wiki"),
    ("원천세 신고 기한을 찾아줘", "tax-wiki"),
    ("근로계약서와 취업규칙의 필수 조항을 조사해", "labor-wiki"),
    ("부당해고와 임금 체불 대응 절차를 찾아줘", "labor-wiki"),
    ("4대보험 가입 기준을 정리해", "labor-wiki"),
    ("계약 해제 관련 판례와 내용증명 작성 기준을 조사해", "law-wiki"),
    ("민사소송에서 계약 해석 법리를 정리해", "law-wiki"),
    ("손해배상 판례를 검색해", "law-wiki"),
    ("Python에서 sqlite 오류가 나는 이유를 조사해", "ai-wiki"),
    ("벡터 데이터베이스의 장단점을 설명해", "ai-wiki"),
    ("조선 근현대사 연구 자료를 위키에서 찾아줘", "ai-wiki"),
]


def route_skill(query: str, specs: list[Any]) -> str:
    normalized = query.casefold()
    for spec in specs:
        if spec.skill_name.casefold() in normalized:
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
    results = []
    for query, expected in ROUTING_EVALS:
        actual = route_skill(query, specs)
        results.append({"query": query, "expected": expected, "actual": actual, "passed": actual == expected})
    passed = sum(1 for result in results if result["passed"])
    return {
        "status": "ok" if passed == len(results) else "error",
        "passed": passed,
        "total": len(results),
        "pass_rate": passed / len(results),
        "results": results,
    }


def install_variant_skills(package_dir: Path, agents: tuple[str, ...]) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    spec = load_variant_spec(package_dir)
    source_dir = package_dir / "src" / spec.module_name / "skill_templates"
    skill_files = list(source_dir.glob("*.md"))
    if not skill_files:
        raise FileNotFoundError(f"skill templates not found: {source_dir}")
    bases = {
        "claude": Path.home() / ".claude" / "skills",
        "gemini": Path.home() / ".agents" / "skills",
        "codex": Path.home() / ".codex" / "skills",
    }
    installed = []
    for agent in agents:
        if agent not in bases:
            raise ValueError(f"unsupported agent: {agent}")
        destination = bases[agent] / spec.skill_name
        destination.mkdir(parents=True, exist_ok=True)
        for source in skill_files:
            shutil.copy2(source, destination / source.name)
        installed.append({"agent": agent, "path": str(destination)})
    config_path = package_dir / spec.config_filename
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config["agents"] = list(dict.fromkeys([*(config.get("agents") or []), *agents]))
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {
        "status": "ok",
        "action": "variant_skills_installed",
        "package_name": spec.package_name,
        "installed": installed,
        "agents": config["agents"],
    }


def audit_skill_installation(package_dirs: list[Path]) -> dict[str, Any]:
    rows = []
    missing = []
    bases = {
        "claude": Path.home() / ".claude" / "skills",
        "gemini": Path.home() / ".agents" / "skills",
        "codex": Path.home() / ".codex" / "skills",
    }
    for package_dir in package_dirs:
        package_dir = package_dir.resolve()
        spec = load_variant_spec(package_dir)
        config = yaml.safe_load((package_dir / spec.config_filename).read_text(encoding="utf-8")) or {}
        for agent in config.get("agents") or []:
            path = bases[agent] / spec.skill_name
            exists = (path / "SKILL.md").exists()
            rows.append({"package_name": spec.package_name, "agent": agent, "path": str(path), "exists": exists})
            if not exists:
                missing.append(str(path))
    routing = evaluate_skill_routing(package_dirs)
    return {
        "status": "ok" if not missing and routing["status"] == "ok" else "error",
        "installed": not missing,
        "missing": missing,
        "skills": rows,
        "routing": routing,
    }
