# AI Wiki AI 중심성 최종 평가

- 평가일: 2026-07-12
- 코드 기준: `v0.5.0` 릴리스 후보
- 대상 에이전트: Codex, Gemini
- 제외 에이전트: Claude (유료 인증 불가로 완료 기준에서 제외)
- 운영 범위: 로컬 단일 사용자 AI 지식 백과사전
- 종합 점수: **100/100**
- 판정: **합의된 완료 기준 전부 충족**

## 완료 정의

이 평가의 100%는 절대적인 무결점 선언이 아니다. 다음으로 합의한 제품 범위와 측정
게이트를 모두 통과했다는 의미다.

- AI가 안정적인 JSON 계약으로 지식을 검색하고 읽을 수 있다.
- AI가 받은 근거와 citation 경로가 실제 context 값으로 연결된다.
- AI가 토큰 예산 안에서 정확·의미·관계 검색을 사용할 수 있다.
- AI가 버전 충돌, 스키마, 품질, 출처 정책을 지키며 부분 수정·생성할 수 있다.
- YAML, SQLite, vector 중 하나가 실패하면 성공으로 처리하지 않고 저장을 복원한다.
- 목적별 위키와 스킬이 다른 root의 문서·DB·사용 기록을 참조하지 않는다.
- Codex와 Gemini가 같은 12개 실제 작업을 각각 전부 수행한다.
- 기존 실제 문서는 평가 과정에서 변경하지 않는다.

## 최종 점수표

| 평가 축 | 점수 | 근거 |
|---|---:|---|
| AI를 위한 위키 | 100/100 | 1,000건 exact·semantic·relation Recall@5 100%, citation 값 포함률 100%, 토큰 초과 0건 |
| AI에 의한 위키 | 100/100 | Codex 12/12, Gemini 12/12, 안전한 context·record-use·patch·create 수행 |
| AI의 위키 | 100/100 | YAML 원본, 사용 기록, 검증 상태, root 격리, 세 저장소 일관성 및 실패 복구 |
| 종합 | **100/100** | 합의된 모든 필수 게이트 통과 |

## 정량 결과

| 게이트 | 결과 | 판정 |
|---|---:|---|
| 전체 회귀 테스트 | 181/181 | 통과 |
| 격리 평가 문서 | 1,000건 | 통과 |
| YAML·SQLite·vector 개수 | 1,000 / 1,000 / 1,000 | 통과 |
| exact Recall@5 | 100% | 통과 |
| semantic Recall@5 | 100% | 통과 |
| relation Recall@5 | 100% | 통과 |
| 상충 주장 동시 검색 | 2/2 | 통과 |
| pending 기본 제외·명시 포함 | 정상 | 통과 |
| 토큰 예산 위반 | 0건 | 통과 |
| citation 원본 경로 오류 | 0건 | 통과 |
| citation 값 context 포함률 | 100% | 통과 |
| create 벡터 실패 복구 | YAML 0, SQLite 0, 성공 응답 0 | 통과 |
| patch 벡터 실패 복구 | 이전 YAML·버전·인덱스 복원 | 통과 |
| custom type root 격리 | 교차 잔존 0건 | 통과 |
| 목적별 위키 라우팅 | 12/12 | 통과 |
| Codex 실제 작업 | 12/12 | 통과 |
| Gemini 실제 작업 | 12/12 | 통과 |

## 해결된 핵심 결함

### 1. Context와 citation 불일치

이전에는 compact context에서 생략된 필드의 citation도 반환할 수 있었다. 이제 compact
payload 안에서 실제 값을 찾을 수 있는 경로만 발급한다. `/content/data` 전체 검증은
compact에 포함된 개별 필드 경로로 구체화한다.

질의와 관련된 비표준 필드도 최대 3개까지 우선 포함한다. 따라서 `architecture`를
질문하면 자기참조 문서의 `architecture` 값과 해당 citation이 함께 반환된다.

### 2. 벡터 저장 실패 은폐

벡터 upsert 예외를 무시하던 동작을 제거했다. AI create와 patch, 일반 CLI 쓰기, 웹 쓰기는
성공 응답 전에 vector 저장을 확인한다. 실패하면 새 YAML과 SQLite 행을 제거하거나 이전
문서·버전·인덱스를 복원하고 `storage_failed`를 반환한다.

YAML·SQLite 완료 후 벡터 저장 전에 프로세스가 종료되는 경우에는 `pending-vector`
marker가 남는다. 다음 WikiIndex 시작에서 현재 YAML을 기준으로 벡터를 재생성한 뒤
marker를 제거한다.

### 3. 관계 문서가 limit 뒤에 묻히는 문제

관계 후보를 직접 후보 20건 뒤에 추가하던 방식을 바꿨다. 상위 직접 문서의 관계 문서는
부모 문서 바로 뒤에 결합한다. 관계 문서가 이미 낮은 순위의 hybrid 후보여도 기존 위치에서
제거하고 관계 후보로 승격한다.

### 4. Custom type 자동 로딩 실패

각 CLI 실행은 현재 root의 설정으로 타입 레지스트리를 초기화한다. 기본 타입으로 먼저
재설정한 뒤 현재 root의 custom type만 등록하므로 다른 위키의 타입이 남지 않는다.
capabilities의 content types와 JSON Schema에도 동적으로 반영된다.

Source URL의 HTTP(S) 제약과 content type 열거형도 JSON Schema에서 발견할 수 있다.

### 5. Gemini 스킬 발견 경로 불일치

Gemini/Antigravity 전용 위치와 Gemini CLI가 우선 발견하는 `~/.agents/skills`에 같은
스킬을 설치한다. 감사 명령은 두 사본의 존재와 SHA-256 일치까지 확인한다.

현재 Codex, Gemini 전용 경로와 Gemini 호환 경로의 `ai-wiki` 스킬 해시는 동일하며,
Gemini CLI가 활성화한 스킬에는 mandatory workflow와 `record-use`가 포함되어 있다.

## 실제 에이전트 평가

두 에이전트는 각각 다음 12개 작업을 격리 root에서 수행했다.

1. capabilities 확인
2. context와 실제 citation 획득
3. compact get
4. full get
5. raw get
6. fields projection get
7. record-use 저장
8. patch dry-run
9. patch 실제 적용
10. stale version conflict 확인
11. 출처 없는 pending 초안 생성
12. pending 기본 제외와 명시 포함 확인

Codex와 Gemini 모두 작업 보고뿐 아니라 명령 로그, 문서 버전, context usage DB,
pending YAML 상태와 레거시 대조 파일 해시로 검증됐다.

## 범위 제한

- Claude는 지원 코드와 스킬을 제거한 것이 아니라 실제 완료 게이트에서만 제외했다.
- 웹 UI에는 인증·권한·저장 암호화가 없다. 100점은 문서화된 로컬 단일 사용자 범위에
  한정된다. 외부 네트워크나 다중 사용자 서비스는 별도 보안 목표가 필요하다.
- 평가는 v0.5.0 릴리스 후보 소스와 동일한 로컬 작업 트리에서 수행했다.

## 실제 문서 보호

실제 AI Wiki 문서는 1건이며 평가 전후 SHA-256은 모두 다음과 같다.

`7B269319C415293BDF7232BFD354A178F001C18D6B428ED1676F5E5899CF8F34`

일괄 마이그레이션이나 실제 문서 내용 수정은 수행하지 않았다.

## 증거 파일

- `docs/audits/ai-first-quality-gate-0.5.0.json`
- `docs/audits/ai-first-agent-eval-0.5.0.json`
- `docs/audits/ai-first-evaluation-0.5.0.json`
- `tests/test_agent_protocol.py`
- `tests/test_schema_v2.py`
- `tests/test_phase2_features.py`
- `tests/test_skill_routing.py`
- `tests/test_storage.py`
