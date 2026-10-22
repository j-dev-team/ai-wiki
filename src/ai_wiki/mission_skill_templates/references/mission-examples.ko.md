# AI Wiki Mission 최소 유효 예시

이 스킬의 Mission은 반드시 `ai-wiki`와 `AI_WIKI_ROOT`를 사용한다. 전용 위키
(예: tax-wiki, labor-wiki, law-wiki)의 Mission은 그 위키의 `*-missions` 스킬과 CLI로만 다룬다.

## 문서 종류별 필수 내용

- **ResearchReport**: `workspace_root`, 위키 언어로 쓴 scope 요약·findings(설명 포함)·recommendations.
  스크린샷과 식별자는 본문을 보조할 뿐 대신하지 않는다.
- **WorkPlan**: 승인 대기 상태로 만든다. 작업마다 instructions·acceptance_criteria·verification을 둔다.
  작성자가 스스로 승인하지 않는다.
- **WorkRun**: 승인된 정확한 plan revision을 고정한다. evidence에는 파일·명령·테스트 결과와
  criterion ID를 연결한다.
- **Handoff**: 현재 상태, 바뀐 파일, 남은 작업, 근거, 막힌 이유를 위키 언어로 적는다.

## 실행 순서

```bash
ai-wiki capabilities                                   # data.language.authoring_language 확인
ai-wiki mission create --document-file plan.json --dry-run
ai-wiki mission create --document-file plan.json
ai-wiki plan get <plan-id>                             # 정확한 revision과 승인 상태 확인
ai-wiki run start <plan-id> --principal <agent-id>     # 승인된 revision만
ai-wiki run next <run-id>                              # 일반 실행 시 단 한 번의 읽기
ai-wiki task claim <run-id> <task-id> --principal <agent-id> --if-revision <run-revision>
ai-wiki task submit <run-id> <task-id> --evidence-file evidence.json --result "<결과 요약>" --principal <agent-id> --if-revision <run-revision>
```

`version_conflict` 또는 revision 불일치가 나오면 `ai-wiki run next <run-id>`로 다시 읽은 뒤 진행한다.

## 검토

제출자는 완료를 결정하지 않는다. reviewer 또는 owner가 독립적으로 evidence와 기준을 대조한다.

```bash
ai-wiki run evidence <run-id> --criterion <criterion-id>
ai-wiki task verify <run-id> <task-id> --decision completed --reason "<근거>" --principal <reviewer-id> --if-revision <run-revision>
```
