# AI Wiki 문서 작성 프로토콜

## 1. 검색 우선

새 답변이나 조사를 시작하기 전에 기존 위키를 검색한다.

```bash
ai-wiki search "핵심 키워드"
ai-wiki vsearch "의미 기반 질의"
```

검색 결과가 있으면 다음을 확인한다.

- 문서 ID
- 제목과 카테고리
- confidence
- sources
- last_verified
- related/backlinks

## 2. 조사와 검증

저장할 가치가 있는 지식은 다음 기준을 만족해야 한다.

- 반복해서 쓰일 가능성이 있다.
- 특정 프로젝트에만 묶이지 않는다.
- 출처를 붙일 수 있다.
- 구조화된 필드로 표현할 수 있다.

최신성이나 정확성이 중요한 주제는 반드시 외부 자료로 확인한다. 확인하지 못한 값은 낮은 confidence 또는 `unverified`로 표시한다.

## 3. YAML 작성

문서는 Markdown 산문이 아니라 구조화 YAML로 작성한다.

권장 기본 구조:

```yaml
type: technology
what: "대상을 한 문장으로 설명"
facts:
  - "검증 가능한 핵심 사실"
facts_v:
  level: verified
  sources:
    - "https://example.com/source"
use_cases:
  - "사용 사례"
limitations:
  - "제약 또는 주의점"
best_practices:
  - "권장 방법"
```

## 4. 검증 메타데이터

검증 수준은 `_v` 또는 `<field>_v`에 기록한다.

```yaml
release_year: 1991
release_year_v:
  level: verified
  sources:
    - "https://docs.python.org/"
```

권장 level:

- `unverified`: 아직 검증하지 않음
- `sourced`: 단일 출처 확인
- `corroborated`: 복수 출처로 교차 확인
- `verified`: 신뢰 가능한 출처로 확인
- `human_verified`: 사람이 검증
- `disputed`: 논쟁 또는 충돌 있음

## 5. 저장

```bash
ai-wiki create \
  --title "문서 제목" \
  --category "technology/python" \
  --tags "python,testing" \
  --source "https://example.com/source" \
  --confidence 0.9 \
  --content-file content.yaml
```

유사 문서가 있으면 새 문서를 만들지 말고 기존 문서를 보강한다.

## 6. 저장 후 점검

```bash
ai-wiki quality <id>
ai-wiki lint --fix
ai-wiki doctor
```

품질 점수가 낮으면 필드, 출처, related, 검증 메타데이터를 보강한다.
