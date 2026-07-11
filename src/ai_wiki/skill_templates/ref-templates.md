# AI Wiki YAML 템플릿

## 기술 문서

```yaml
type: technology
what: "기술의 정의"
language: "python"
facts:
  - "검증 가능한 핵심 사실"
facts_v:
  level: verified
  sources:
    - "https://example.com/source"
use_cases:
  - "사용 사례"
limitations:
  - "제약"
best_practices:
  - "권장 방법"
when_to_use:
  - "선택하면 좋은 상황"
when_not_to_use:
  - "피해야 하는 상황"
```

## 개념 문서

```yaml
type: concept
definition: "개념 정의"
domain: "적용 분야"
key_principles:
  - "핵심 원리"
examples:
  - "예시"
applications:
  - "활용"
related_concepts:
  - "관련 개념"
```

## 오류 해결 문서

```yaml
type: troubleshooting
error_message: "오류 메시지"
root_cause: "근본 원인"
solution: "해결 방법"
environment:
  - "OS / 런타임 / 버전"
prevention:
  - "재발 방지 방법"
related_errors:
  - "관련 오류"
```

## 비교 문서

```yaml
type: comparison
what: "비교 주제"
subjects:
  - "대상 A"
  - "대상 B"
criteria:
  - "비교 기준"
comparison_table:
  - criterion: "성능"
    subject_a: "..."
    subject_b: "..."
winner_by_use_case:
  - use_case: "상황"
    choice: "추천 대상"
conclusion: "요약 결론"
```

## 튜토리얼 문서

```yaml
type: tutorial
what: "무엇을 만드는지"
goal: "목표"
prerequisites:
  - "사전 조건"
steps:
  - order: 1
    action: "수행할 작업"
    result: "기대 결과"
common_mistakes:
  - "자주 하는 실수"
next_steps:
  - "다음 단계"
```

## 인용/검증 메타데이터

수치, 날짜, 버전, 정책, 법률, 인용에는 검증 메타데이터를 붙인다.

```yaml
release_date: "2026-06-27"
release_date_v:
  level: sourced
  sources:
    - "https://example.com/source"
  checked_at: "2026-06-27"
```
