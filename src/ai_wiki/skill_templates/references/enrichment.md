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

1. 문서와 현재 version 조회

```bash
ai-wiki get <id>          # 응답 data.document.version 값을 기록한다
ai-wiki quality <id>
```

2. 부족한 필드 확인

- `quality` 오류/경고
- sources 수와 각 주장에 연결된 verification 기록
- related 문서 수
- `content.data`에서 비어 있거나 오래된 필드

3. 조사

최신 정보, 수치, 날짜, 제품/법/정책/인물 정보는 외부 출처로 확인한다.

4. JSON Patch 작성 (`patch.json`)

RFC 6902 하위 집합(`test`, `add`, `replace`, `remove`)만 쓴다. 기존 사실을 지우지 말고 `add`로 보강한다.

```json
[
  {"op": "test", "path": "/metadata/document_version", "value": 3},
  {"op": "add", "path": "/content/data/best_practices/-", "value": "새로 확인한 권장 사항"},
  {"op": "add", "path": "/content/data/limitations/-", "value": "확인된 제약"},
  {"op": "add", "path": "/sources/-", "value": {"id": "src-new-1", "url": "https://example.com/source", "title": "출처 제목"}},
  {"op": "add", "path": "/verification/-", "value": {"path": "/content/data/best_practices", "level": "sourced", "source_ids": ["src-new-1"]}}
]
```

5. 미리보기 후 적용 — 같은 version을 `--if-version`에 넘긴다

```bash
ai-wiki patch <id> --operations-file patch.json --if-version <version> --dry-run
ai-wiki patch <id> --operations-file patch.json --if-version <version>
```

`version_conflict`가 나오면 재시도하지 말고 1단계부터 다시 조회한다. 예전 방식인
`ai-wiki update <id> --content-file ...`(전체 문서 덮어쓰기)는 쓰지 않는다.

6. 재검증

```bash
ai-wiki quality <id>
ai-wiki context "보강한 주제" --max-tokens 2000
```

## 금지 사항

- 출처 없는 확정적 보강
- 기존 사실 삭제
- 검증되지 않은 최신 정보 저장
- `update --content-file` 등 전체 문서 덮어쓰기로 변경 이력을 우회하는 행위

기존 내용과 충돌하는 정보는 `disputes` 또는 `limitations`에 남기고 confidence를 조정한다.
