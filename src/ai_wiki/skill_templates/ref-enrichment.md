# AI Wiki 보강 지침

보강은 기존 문서를 더 정확하고 재사용 가능하게 만드는 작업이다. 문서를 덮어쓰기보다 필드를 추가하고, 출처와 검증 상태를 강화한다.

## 보강 대상

다음 문서는 우선 보강한다.

- `ai-wiki todo`에 표시되는 문서
- `quality_score`가 낮은 문서
- `maturity`가 `stub` 또는 `draft`인 문서
- 출처가 없거나 confidence가 낮은 문서
- related/backlinks가 없는 고립 문서
- `last_verified`가 오래된 문서

## 절차

1. 문서 조회

```bash
ai-wiki get <id>
ai-wiki quality <id>
```

2. 부족한 필드 확인

- `_meta.missing_fields`
- `quality` 오류/경고
- sources 수
- related 문서 수
- `_v` 검증 메타데이터

3. 조사

최신 정보, 수치, 날짜, 제품/법/정책/인물 정보는 외부 출처로 확인한다.

4. YAML 조각 작성

```yaml
best_practices:
  - "새로 확인한 권장 사항"
best_practices_v:
  level: sourced
  sources:
    - "https://example.com/source"
limitations:
  - "확인된 제약"
```

5. 업데이트

```bash
ai-wiki update <id> --content-file patch.yaml --source "https://example.com/source"
```

6. 재검증

```bash
ai-wiki quality <id>
ai-wiki lint --fix
```

## 금지 사항

- 출처 없는 확정적 보강
- 기존 사실 삭제
- 검증되지 않은 최신 정보 저장
- `_changelog` 없이 의미 있는 변경을 숨기는 행위

기존 내용과 충돌하는 정보는 `disputes` 또는 `limitations`에 남기고 confidence를 조정한다.
