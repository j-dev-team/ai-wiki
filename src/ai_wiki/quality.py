"""문서 품질 검증 엔진."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ai_wiki.models import Article
from ai_wiki.schemas import TYPE_SCHEMAS, compute_completeness, determine_maturity

# ── 검증 레벨 정의 ─────────────────────────────────────────────────────────────
# _v 필드에서 허용되는 검증 레벨 목록
V_LEVELS: list[str] = [
    "unverified",      # 출처 없음/검증 안 됨
    "sourced",         # 출처 있음
    "corroborated",    # 다중 출처로 교차 확인
    "verified",        # 검증 완료
    "disputed",        # 논쟁 중
    "human_verified",  # 인간 검증 완료 (최고 신뢰)
]

# 각 레벨에 대한 신뢰 가중치 (0.0 ~ 1.0)
V_LEVEL_WEIGHTS: dict[str, float] = {
    "unverified":     0.0,
    "sourced":        0.3,
    "corroborated":   0.7,
    "verified":       0.7,   # corroborated와 동일
    "disputed":       0.2,
    "human_verified": 1.0,
}


@dataclass
class QualityViolation:
    level: str      # "error" | "warning"
    code: str       # 규칙 코드
    message: str
    field: str = ""


@dataclass
class QualityReport:
    article_id: str
    title: str = ""
    maturity: str = "stub"
    quality_score: float = 0.0
    violations: list[QualityViolation] = field(default_factory=list)
    passed: bool = False
    gate_level: str = "FAILED"  # L1, L2, L3 게이트 통과 수준
    suggested_confidence: float | None = None

    @property
    def errors(self) -> list[QualityViolation]:
        return [v for v in self.violations if v.level == "error"]

    @property
    def warnings(self) -> list[QualityViolation]:
        return [v for v in self.violations if v.level == "warning"]

    def to_dict(self) -> dict:
        d = {
            "article_id": self.article_id,
            "title": self.title,
            "maturity": self.maturity,
            "quality_score": round(self.quality_score, 3),
            "passed": self.passed,
            "gate_level": self.gate_level,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "violations": [
                {"level": v.level, "code": v.code, "message": v.message, "field": v.field}
                for v in self.violations
            ],
        }
        if self.suggested_confidence is not None:
            d["suggested_confidence"] = self.suggested_confidence
        return d


# ── 임계값 ────────────────────────────────────────

THRESHOLDS = {
    "min_content_keys": 5,
    "min_content_chars": 200,
    "min_sources": 1,
    "min_tags": 2,
    "min_list_items": 2,
}


# ── 핵심 함수 ─────────────────────────────────────

def validate(article: Article) -> QualityReport:
    """Article 품질 검증. QualityReport 반환."""
    report = QualityReport(article_id=article.id, title=article.title)

    content = article.content
    text = article.content_as_text()
    data_keys = _data_keys(content)
    content_type = content.get("type", "") if isinstance(content, dict) else ""

    # ── ERROR ──

    if len(data_keys) < THRESHOLDS["min_content_keys"]:
        report.violations.append(QualityViolation(
            "error", "MIN_CONTENT_KEYS",
            f"content 키 {len(data_keys)}개 < 최소 {THRESHOLDS['min_content_keys']}개",
            "content",
        ))

    if len(text) < THRESHOLDS["min_content_chars"]:
        report.violations.append(QualityViolation(
            "error", "MIN_CONTENT_CHARS",
            f"content 텍스트 {len(text)}자 < 최소 {THRESHOLDS['min_content_chars']}자",
            "content",
        ))

    if len(article.sources) < THRESHOLDS["min_sources"]:
        report.violations.append(QualityViolation(
            "error", "MIN_SOURCES",
            f"출처 {len(article.sources)}개 < 최소 {THRESHOLDS['min_sources']}개",
            "sources",
        ))

    if not content_type:
        report.violations.append(QualityViolation(
            "error", "MISSING_TYPE",
            "content에 'type' 필드가 없습니다",
            "content.type",
        ))

    if content_type and content_type in TYPE_SCHEMAS:
        required = TYPE_SCHEMAS[content_type]["required"]
        missing = [k for k in required
                   if k not in content or not content.get(k)]
        if missing:
            report.violations.append(QualityViolation(
                "error", "TYPE_REQUIRED_KEYS",
                f"'{content_type}' 타입 필수키 누락: {', '.join(missing)}",
                "content",
            ))

    # ── ERROR: 검증률 ──

    v_rate = _calc_verification_rate(content, article.verification)
    v_total = _count_v_fields(content, article.verification)
    if v_total == 0:
        report.violations.append(QualityViolation(
            "error", "NO_VERIFICATION",
            "_v 메타데이터가 0개입니다. 수치/날짜/인용문에 _v를 추가하세요.",
            "content",
        ))

    # ── WARNING ──

    if len(article.tags) < THRESHOLDS["min_tags"]:
        report.violations.append(QualityViolation(
            "warning", "MIN_TAGS",
            f"태그 {len(article.tags)}개 < 권장 {THRESHOLDS['min_tags']}개",
            "tags",
        ))

    if isinstance(content, dict):
        for k, v in content.items():
            if k.startswith("_"):
                continue
            if isinstance(v, list) and 0 < len(v) < THRESHOLDS["min_list_items"]:
                report.violations.append(QualityViolation(
                    "warning", "SHORT_LIST",
                    f"'{k}' 항목 {len(v)}개 < 권장 {THRESHOLDS['min_list_items']}개",
                    f"content.{k}",
                ))

    word_count = _count_words(text)
    if word_count < 50:
        report.violations.append(QualityViolation(
            "warning", "LOW_WORD_COUNT",
            f"단어 {word_count}개 < 권장 50개",
            "content",
        ))

    # ── 성숙도 + 점수 ──

    comp, _, _ = compute_completeness(content)
    report.maturity = determine_maturity(
        comp, len(article.sources), len(article.related), article.confidence
    )
    report.quality_score = calculate_score(article)

    # #5: confidence 자동 계산 제안
    auto_conf = auto_confidence(article)
    if auto_conf != article.confidence:
        report.suggested_confidence = auto_conf
    report.passed = len(report.errors) == 0

    # Claude Code의 지적 반영: 단일 Pass가 아닌 3-Phase Gate (L1/L2/L3) 적용
    if not report.passed:
        report.gate_level = "FAILED"
    elif report.quality_score >= 0.85 and len(report.warnings) == 0 and v_rate >= 0.8:
        report.gate_level = "L3_PASSED" # 최고 품질: 휴먼 검증 완료 수준
    elif report.quality_score >= 0.65 and len(report.warnings) <= 1:
        report.gate_level = "L2_PASSED" # 중간 품질: 구조화 완료, 추가 보강 및 교차 검증 필요
    else:
        report.gate_level = "L1_PASSED" # 최소 품질: 강제 스키마만 통과한 초안 수준

    return report


def calculate_score(article: Article) -> float:
    """품질 점수 0.0~1.0. 구조 완성도 + 검증률 반영."""
    content = article.content
    text = article.content_as_text()
    words = _count_words(text)
    keys = len(_data_keys(content))

    # 구조 점수 (70%)
    key_score = min(keys / 10, 1.0) * 0.15
    word_score = min(words / 300, 1.0) * 0.20
    source_score = min(len(article.sources) / 3, 1.0) * 0.10
    tag_score = min(len(article.tags) / 5, 1.0) * 0.05
    ref_score = min(len(article.related) / 3, 1.0) * 0.10
    conf_score = article.confidence * 0.10

    # 검증률 점수 (30%) — _v 필드 중 verified/corroborated 비율
    verification_score = _calc_verification_rate(content, article.verification) * 0.30

    return round(key_score + word_score + source_score + tag_score +
                 ref_score + conf_score + verification_score, 3)


# ── 내부 헬퍼 ─────────────────────────────────────

def _data_keys(content: dict) -> set[str]:
    """_ prefix 제외한 실제 데이터 키."""
    if not isinstance(content, dict):
        return set()
    return {k for k in content if not k.startswith("_")}


def _count_words(text: str) -> int:
    """한영 혼합 텍스트 단어 수."""
    en = len(re.findall(r"[a-zA-Z]+", text))
    ko_text = re.sub(r"[a-zA-Z0-9]+", "", text)
    ko = len(ko_text.split())
    return en + ko


def _count_v_fields(content: dict, verification: list[dict] | None = None) -> int:
    """content 내 _v 필드 총 개수."""
    count = 0
    def scan(d):
        nonlocal count
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(k, str) and (k.endswith("_v") or k == "_v"):
                    count += 1
                else:
                    scan(v)
        elif isinstance(d, list):
            for item in d:
                scan(item)
    scan(content)
    return count + len(verification or [])


def _calc_verification_rate(content: dict, verification: list[dict] | None = None) -> float:
    """content 내 _v 필드의 V_LEVEL_WEIGHTS 평균. _v 없으면 0.5 (중립)."""
    from ai_wiki.quality import V_LEVEL_WEIGHTS as _VW
    if verification:
        weights = [_VW.get(item.get("level", "unverified"), 0.0) for item in verification]
        return sum(weights) / len(weights)
    if not isinstance(content, dict):
        return 0.5

    weights = []

    def scan(d):
        if isinstance(d, dict):
            for k, v in d.items():
                if not isinstance(k, str):
                    continue
                if k.endswith("_v") or k == "_v":
                    if isinstance(v, dict):
                        level = v.get("level", "unverified")
                        weights.append(_VW.get(level, 0.0))
                else:
                    scan(v)
        elif isinstance(d, list):
            for item in d:
                scan(item)

    scan(content)

    if not weights:
        return 0.5  # _v 없으면 중립
    return sum(weights) / len(weights)


# ── #5: confidence 자동 계산 ─────────────────────

def auto_confidence(article: Article) -> float:
    """출처 수 × 교차 검증 기반 confidence 자동 산출."""
    src_count = len(article.sources)
    has_disputes = False
    if isinstance(article.content, dict):
        has_disputes = bool(article.content.get("disputes"))

    if src_count == 0:
        base = 0.4
    elif src_count == 1:
        base = 0.7
    elif src_count >= 2:
        base = 0.85
    else:
        base = 0.5

    # 교차참조 보너스
    if len(article.related) >= 2:
        base = min(base + 0.05, 1.0)

    # disputes 존재 시 감점 (논쟁 중인 내용)
    if has_disputes:
        base = min(base, 0.8)

    return round(base, 2)
