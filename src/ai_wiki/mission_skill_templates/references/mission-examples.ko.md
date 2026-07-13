# Mission 최소 유효 예시

이 문서는 Mission 명령으로 생성할 때 필요한 최소 필드를 보여 준다. 한국어 위키의
사람이 읽는 문장은 한국어로 작성하고, 명령·경로·해시·ID는 원문을 유지한다.

## ResearchReport

```json
{"mission_schema_version":1,"kind":"research_report","id":"research-example","revision":1,"status":"proposed","metadata":{"created_at":"2026-01-01T00:00:00Z","modified_at":"2026-01-01T00:00:00Z","created_by":"agent","namespace":"artifacts","source_language":"ko"},"payload":{"workspace_root":"D:/wiki","scope":["조사 범위"],"findings":[{"id":"F1","title":"발견","detail":"근거가 있는 조사 결과를 충분히 설명한다.","evidence_ids":[]}],"recommendations":["권장 조치"],"sufficient":true}}
```

## WorkPlan

```json
{"mission_schema_version":1,"kind":"work_plan","id":"plan-example","revision":1,"status":"proposed","metadata":{"created_at":"2026-01-01T00:00:00Z","modified_at":"2026-01-01T00:00:00Z","created_by":"agent","namespace":"plans","source_language":"ko"},"payload":{"plan_id":"plan-example","objective":"승인 후 수행할 목표","scope":["변경 범위"],"acceptance_criteria":["전역 완료 기준"],"tasks":[{"id":"T1","title":"작업","instructions":"수행할 구체 지시","acceptance_criteria":["작업 완료 기준"],"verification":["pytest -q"],"dependencies":[]}],"approval":{"required":true,"status":"pending"}}}
```

## 실행 순서

1. 승인된 정확한 plan revision을 읽는다.
2. `run next` 또는 `task context`로 한 작업만 읽고 `run_revision`을 포함해 claim한다.
3. 파일·명령·테스트·결정 근거를 `task submit`으로 제출한다.
4. 제출자와 다른 owner 또는 reviewer가 `task verify --decision completed`로 완료를 결정한다.
5. 중단되면 변경 파일, 남은 일, 차단 사유, evidence ID, 다음 담당자를 handoff에 기록한다.
