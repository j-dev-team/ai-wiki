"""타입별 문서 스키마 정의 및 completeness 계산."""
from __future__ import annotations

import copy


# 타입별 필수(required) / 선택(optional) 필드 정의
# completeness = (채워진 required * 1.0 + 채워진 optional * 0.5)
#              / (required 수 * 1.0 + optional 수 * 0.5)

TYPE_SCHEMAS: dict[str, dict[str, list[str]]] = {
    "legacy": {
        "required": ["type", "original_type"],
        "optional": [],
    },
    "technology": {
        "required": ["type", "what", "facts"],
        "optional": ["language", "framework", "use_cases", "limitations",
                      "workarounds", "best_practices", "when_to_use", "when_not_to_use"],
    },
    "event": {
        "required": ["type", "what", "period", "location", "participants", "facts"],
        "optional": ["timeline", "casualties", "causes", "consequences",
                      "key_figures", "common_misconceptions", "disputes",
                      "historiography", "related_events"],
    },
    "concept": {
        "required": ["type", "definition", "domain", "key_principles"],
        "optional": ["examples", "applications", "related_concepts", "debates"],
    },
    "troubleshooting": {
        "required": ["type", "error_message", "root_cause", "solution"],
        "optional": ["environment", "prevention", "related_errors"],
    },
    "person": {
        "required": ["type", "birth", "nationality", "roles", "key_achievements"],
        "optional": ["death", "education", "family", "timeline",
                      "controversies", "historical_assessment", "key_quotes"],
    },
    "policy": {
        "required": ["type", "jurisdiction", "purpose", "key_provisions"],
        "optional": ["effective_date", "status", "impact", "criticism"],
    },
    "api_reference": {
        "required": ["type", "service", "base_url", "endpoints"],
        "optional": ["auth", "rate_limits", "examples", "errors"],
    },
    "synthesis": {
        "required": ["type", "question", "answer", "derived_from"],
        "optional": ["insight", "methodology"],
    },
    "ingested": {
        "required": ["type", "original_filename", "file_type"],
        "optional": ["status", "notes"],
    },
    "comparison": {
        "required": ["type", "what", "subjects", "criteria", "comparison_table"],
        "optional": ["winner_by_use_case", "conclusion", "caveats",
                      "when_to_choose", "benchmark_data"],
    },
    "tutorial": {
        "required": ["type", "what", "prerequisites", "steps"],
        "optional": ["goal", "time_estimate", "difficulty",
                      "common_mistakes", "next_steps", "working_example"],
    },
}

DEFAULT_SCHEMA: dict[str, list[str]] = {
    "required": ["type", "what"],
    "optional": [],
}

BUILTIN_TYPE_SCHEMAS = copy.deepcopy(TYPE_SCHEMAS)


_LIST_FIELDS = {
    "facts", "use_cases", "limitations", "workarounds", "best_practices",
    "when_to_use", "when_not_to_use", "timeline", "participants", "causes",
    "consequences", "key_figures", "common_misconceptions", "disputes",
    "historiography", "related_events", "key_principles", "examples",
    "applications", "related_concepts", "debates", "environment",
    "prevention", "related_errors", "roles", "key_achievements", "education",
    "family", "controversies", "key_quotes", "key_provisions", "impact",
    "criticism", "endpoints", "rate_limits", "errors", "derived_from",
    "subjects", "criteria", "winner_by_use_case", "caveats",
    "when_to_choose", "benchmark_data", "prerequisites", "steps",
    "common_mistakes", "next_steps",
}


_DICT_FIELDS = {
    "comparison_table", "working_example", "auth", "methodology",
}


def _sample_value(field: str):
    if field == "type":
        return ""
    if field in _LIST_FIELDS:
        return [f"Fill {field} item 1", f"Fill {field} item 2"]
    if field in _DICT_FIELDS:
        return {"summary": f"Fill {field}"}
    if field.endswith("_date"):
        return "YYYY-MM-DD"
    if field in {"birth", "death", "period", "effective_date"}:
        return "YYYY-MM-DD or period"
    if field in {"confidence", "quality_score", "completeness"}:
        return 0.8
    return f"Fill {field}"


def build_content_template(type_name: str = "technology", include_optional: bool = True) -> dict:
    """Build a starter YAML content dict from a registered type schema."""
    schema = TYPE_SCHEMAS.get(type_name, DEFAULT_SCHEMA)
    content: dict = {"type": type_name}

    for key in schema["required"]:
        if key == "type":
            continue
        content[key] = _sample_value(key)

    if include_optional:
        for key in schema["optional"][:4]:
            if key not in content:
                content[key] = _sample_value(key)

    if "what" not in content and type_name not in {"concept", "troubleshooting"}:
        content["what"] = f"Fill {type_name} summary"

    while len([k for k in content if not k.startswith("_")]) < 5:
        content[f"detail_{len(content)}"] = "Fill additional detail"

    content["_v"] = {
        "level": "sourced",
        "sources": ["https://example.com/source"],
        "note": "Replace with real verification metadata",
    }
    return content

HINT_TEMPLATES: dict[str, str] = {
    "casualties": "양측 사상자 통계 필요",
    "consequences": "정치적/경제적/사회적 영향 분석 필요",
    "timeline": "시간순 주요 사건 정리 필요",
    "causes": "직접적/구조적 원인 분석 필요",
    "disputes": "학술적 논쟁/이견 정리 필요",
    "historiography": "시대별 해석 변천사 정리 필요",
    "key_figures": "주요 인물 역할 정리 필요",
    "common_misconceptions": "흔한 오해/잘못된 통설 정리 필요",
    "workarounds": "대안/우회 방법 정리 필요",
    "examples": "구체적 예시 추가 필요",
    "limitations": "제약사항/한계 정리 필요",
    "controversies": "논란/비판 사항 정리 필요",
    "historical_assessment": "역사적 평가 (긍정/부정) 정리 필요",
    "comparison_table": "항목별 비교표 필요",
    "subjects": "비교 대상 목록 필요",
    "criteria": "비교 기준 목록 필요",
    "steps": "단계별 절차 작성 필요",
    "prerequisites": "사전 조건/환경 정리 필요",
    "common_mistakes": "자주 하는 실수 정리 필요",
}


def _is_filled(content: dict, key: str) -> bool:
    """필드가 실질적으로 채워져 있는지 확인."""
    val = content.get(key)
    if val is None:
        return False
    if isinstance(val, (str, list, dict)) and not val:
        return False
    return True


def compute_completeness(content: dict) -> tuple[float, list[str], list[str]]:
    """completeness 비율, 누락 필드, 보강 힌트를 반환."""
    if not isinstance(content, dict):
        return 0.0, [], []

    content_type = content.get("type", "")
    schema = TYPE_SCHEMAS.get(content_type, DEFAULT_SCHEMA)

    required = schema["required"]
    optional = schema["optional"]

    filled_req = sum(1 for k in required if _is_filled(content, k))
    filled_opt = sum(1 for k in optional if _is_filled(content, k))

    req_weight = 1.0
    opt_weight = 0.5

    total_weight = len(required) * req_weight + len(optional) * opt_weight
    if total_weight == 0:
        return 1.0, [], []

    filled_weight = filled_req * req_weight + filled_opt * opt_weight
    completeness = round(filled_weight / total_weight, 2)

    missing = [k for k in required if not _is_filled(content, k)]
    missing += [k for k in optional if not _is_filled(content, k)]

    hints = []
    for f in missing:
        is_req = f in required
        hint_text = HINT_TEMPLATES.get(f, f"{f} 필드 작성 필요")
        hints.append(f"{f}: {hint_text} ({'필수' if is_req else '선택'})")

    return completeness, missing, hints


def determine_maturity(completeness: float, sources_count: int,
                       related_count: int, confidence: float) -> str:
    """completeness + 메타데이터 기반 성숙도 판정."""
    if completeness >= 0.8 and sources_count >= 2 and related_count >= 2 and confidence >= 0.7:
        return "mature"
    if completeness >= 0.6 and sources_count >= 1 and related_count >= 1:
        return "review"
    if completeness >= 0.3:
        return "draft"
    return "stub"


# ── 베이스 스키마 (모든 타입의 기본) ────────────────────────────────────────────
BASE_SCHEMA: dict[str, list[str]] = {
    "required": ["type"],
    "optional": ["what", "summary", "notes"],
}


def register_custom_types(config_path, *, reset: bool = False) -> dict:
    """위키 설정 파일(.ai-wiki.yaml)에서 custom_types를 로드해 TYPE_SCHEMAS에 등록.

    Args:
        config_path: .ai-wiki.yaml 파일 경로 (Path 또는 str)

    Returns:
        등록된 타입 이름 -> 스키마 dict
    """
    from pathlib import Path as _Path
    import yaml as _yaml

    if reset:
        TYPE_SCHEMAS.clear()
        TYPE_SCHEMAS.update(copy.deepcopy(BUILTIN_TYPE_SCHEMAS))

    config_path = _Path(config_path)
    if not config_path.exists():
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = _yaml.safe_load(f)
    except Exception:
        return {}

    custom_types = config.get("custom_types", {}) if isinstance(config, dict) else {}
    registered = {}

    for type_name, schema_def in custom_types.items():
        if not isinstance(schema_def, dict):
            continue

        # extends: 상위 타입으로부터 상속
        extends = schema_def.get("extends")
        if extends and extends in TYPE_SCHEMAS:
            base = TYPE_SCHEMAS[extends]
            merged_required = list(base["required"]) + [
                k for k in schema_def.get("required", []) if k not in base["required"]
            ]
            merged_optional = list(base["optional"]) + [
                k for k in schema_def.get("optional", []) if k not in base["optional"]
            ]
        else:
            merged_required = schema_def.get("required", ["type"])
            merged_optional = schema_def.get("optional", [])

        TYPE_SCHEMAS[type_name] = {
            "required": merged_required,
            "optional": merged_optional,
        }
        registered[type_name] = TYPE_SCHEMAS[type_name]

    return registered
