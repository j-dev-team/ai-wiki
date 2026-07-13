# AI Wiki - AI 에이전트용 구조화 지식 위키

AI Wiki는 AI가 지식을 검색하고 읽고 부분 수정하며 재사용하는 **AI 중심 백과사전**입니다. YAML은 영속 원본이고 Codex·Claude·Gemini는 안정적인 CLI JSON 프로토콜을 사용합니다. 웹 UI는 사람이 검토하는 보조 기능입니다.

[English README](https://github.com/j-dev-team/ai-wiki/blob/master/README.md)

[AI Wiki 1.1.3 통합 사용설명서](https://github.com/j-dev-team/ai-wiki/blob/master/docs/USER_GUIDE.ko.md) | [목적별 독립 위키 안내](https://github.com/j-dev-team/ai-wiki/blob/master/docs/VARIANTS.md)

> 보안 안내: 기본 설치는 로컬 전용이며 외부 네트워크에 공개하면 안 됩니다. 팀 환경에는 `pip install "ai-wiki[team]"`으로 인증 세션, API 토큰, RBAC, CSRF·속도 제한, 감사 로그와 민감 필드 암호화를 추가할 수 있습니다. 전체 저장소와 운영체제 계정 보호는 별도로 관리해야 합니다.

## 핵심 특징

- 구조화 YAML 저장: Markdown 산문이 아니라 AI가 재사용하기 좋은 key-value 데이터로 저장
- 하이브리드 검색: SQLite FTS5 키워드 검색과 벡터 의미 검색을 결합
- 벡터 검색 필수 지원: `sentence-transformers`와 `sqlite-vec` 기반 의미 검색
- 품질 게이트: 출처, 필드 수, 내용 길이, 검증 메타데이터를 기준으로 문서 품질 평가
- 관계 관리: related/backlinks, 고립 문서, 단방향 링크, 깨진 참조 점검
- AI 에이전트 연동: Claude Code, Antigravity CLI의 Gemini, GPT Codex용 skill 파일 생성
- 로컬 웹 UI: 검색, 문서 조회/작성/수정, 그래프, 대시보드 제공
- 운영 명령: `doctor`, `lint`, `maintain`, `quality`, `todo`, `gaps`, `stale`, `vindex`
- Mission 작업 관리: revision 고정 계획, 승인, 작업 lease, 완료 기준별 증거, 독립 검토와 인계
- 시간·엔터티 지식: 사실의 유효 시점과 출처를 보존하고, 사건·참여자·연속 이벤트를 연결

## 설치

Python 3.11 이상이 필요합니다.

```powershell
python -m pip install ai-wiki
ai-wiki init D:\wiki\my-wiki

# 초기화 시 위키 구조, AI 작업 흐름, 출처와 검증 경로를 설명하는
# 자기참조 문서가 자동으로 생성됩니다.
```

기존 설치본 업그레이드:

```powershell
python -m pip install --upgrade ai-wiki
ai-wiki upgrade-skill
ai-wiki vindex
ai-wiki doctor
```

설치부터 목적별 위키, 백업, 복원, 문제 해결까지는 [통합 사용설명서](https://github.com/j-dev-team/ai-wiki/blob/master/docs/USER_GUIDE.ko.md)를 참고하세요.

개발 설치:

```bash
git clone https://github.com/j-dev-team/ai-wiki.git
cd ai-wiki
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[test]"
```

## 빠른 시작

```bash
# 위키 초기화
ai-wiki init D:\wiki\my-wiki

# 검색
ai-wiki context "python testing" --max-tokens 4000

# 문서 목록
ai-wiki list

# 문서 조회
ai-wiki get <document-id>

# 벡터 인덱스 생성
ai-wiki vindex

# 벡터 검색 상태 진단
ai-wiki doctor

# 웹 UI 실행
ai-wiki-web
```

기본 웹 UI 주소는 `http://127.0.0.1:5000`입니다. 로그인 기능이 없으므로 외부 네트워크에 공개하지 마세요.

## 목적별 독립 위키

법률, 노무, 세무처럼 데이터와 스킬을 분리해야 하는 위키는 한 명령으로 설치할 수 있습니다.

```powershell
ai-wiki variant install law-wiki `
  --preset law `
  --output-dir D:\dev `
  --agent codex `
  --lang ko
```

설치 명령은 패키지 생성, 초기화, 스킬 설치, 벡터 인덱싱과 상태 진단을 자동 수행합니다. 전체 수명주기는 [목적별 독립 위키 안내](https://github.com/j-dev-team/ai-wiki/blob/master/docs/VARIANTS.md)를 참고하세요.

## 문서 생성 예시

```powershell
ai-wiki template technology --output content.yaml
# content.yaml의 예시 값을 실제 내용과 출처로 수정합니다.

ai-wiki create `
  --title "pytest 테스트 프레임워크" `
  --category "technology/python" `
  --tags "python,testing,pytest" `
  --source "https://docs.pytest.org/" `
  --confidence 0.9 `
  --content-file content.yaml
```

## 주요 명령

| 명령 | 설명 |
| --- | --- |
| `ai-wiki capabilities` | AI 프로토콜·스키마·지원 기능 확인 |
| `ai-wiki context "질문"` | 토큰 예산 내 근거·출처·citation 생성 |
| `ai-wiki patch <id> ... --if-version N` | 충돌 방지 부분 수정 |
| `ai-wiki record-use <context-id> ...` | AI가 실제 사용한 근거 기록 |
| `ai-wiki create --document-file 문서.json` | AI JSON 문서 검증·생성 |
| `ai-wiki init [path]` | 위키 디렉터리와 설정 초기화 |
| `ai-wiki create` | YAML 문서 생성 |
| `ai-wiki get <id>` | 문서 조회 |
| `ai-wiki update <id>` | 문서 수정 |
| `ai-wiki delete <id> --confirm` | 문서 삭제 |
| `ai-wiki search "query"` | FTS5 + 벡터 하이브리드 검색 |
| `ai-wiki vsearch "query"` | 순수 벡터 검색 |
| `ai-wiki vindex` | 벡터 인덱스 재생성 |
| `ai-wiki doctor` | 저장소와 벡터 검색 상태 진단 |
| `ai-wiki quality <id>` | 단일 문서 품질 보고 |
| `ai-wiki quality-all` | 전체 문서 품질 점검 |
| `ai-wiki lint --fix` | 깨진 참조/단방향 링크 점검 및 자동 수정 |
| `ai-wiki maintain` | lint, 품질 요약, todo를 한 번에 실행 |
| `ai-wiki todo` | 보강이 필요한 작업 목록 생성 |
| `ai-wiki gaps` | 카테고리별 약한 영역 분석 |
| `ai-wiki stale` | 검증일이 오래된 문서 목록 |
| `ai-wiki export <id>` | Markdown/YAML 내보내기 |
| `ai-wiki destroy [path]` | 위키와 설치된 skill 파일 제거 |

## 데이터 구조

초기화 후 위키 디렉터리는 다음처럼 구성됩니다.

```text
my-wiki/
  articles/       # YAML 문서 원본
  data/
    wiki.db       # SQLite FTS/메타 인덱스
    vectors.db    # 벡터 인덱스
  sources/        # 원본 자료 파일
  logs/           # 작업 로그
  .ai-wiki.yaml   # 위키 설정
```

문서 파일은 `articles/<category>/<slug>.yaml`에 저장됩니다. SQLite DB는 검색과 메타데이터 조회를 빠르게 하기 위한 인덱스입니다. 원본 데이터는 YAML 파일입니다.

## 품질 시스템

문서 생성 시 기본 품질 조건을 검사합니다.

- `content` 필드 5개 이상
- 내용 텍스트 200자 이상
- 출처 1개 이상
- `content.type` 필수
- `_v` 검증 메타데이터 권장

품질 점수는 구조, 단어 수, 출처, 태그, 관련 문서, confidence, 검증률을 조합해 계산합니다.

## AI 에이전트 연동

`ai-wiki init`은 선택한 에이전트 경로에 skill 파일을 설치합니다.

| 에이전트 | 경로 |
| --- | --- |
| Claude Code | `~/.claude/skills/<wiki-name>/` |
| Antigravity CLI의 Gemini | `~/.gemini/config/skills/<wiki-name>/` |
| GPT Codex | `~/.codex/skills/<wiki-name>/` |

skill 파일을 최신 버전으로 다시 설치하려면:

```bash
ai-wiki upgrade-skill
```

## 개발 및 테스트

```bash
python -m compileall -q src tests
python -m pytest -q
python -m pip wheel . --no-deps
```

GitHub와 PyPI에 새 버전을 게시할 때는 [릴리스 및 업그레이드 워크플로우](https://github.com/j-dev-team/ai-wiki/blob/master/docs/RELEASE.ko.md)와 `scripts/release.ps1`을 사용합니다.

## 현재 수준

AI Wiki는 개인 또는 로컬 개발 환경에서 AI 에이전트의 장기 지식 저장소로 쓰기 적합합니다. 팀 협업, 외부 공개 서비스, 인증이 필요한 운영 환경에는 아직 맞지 않습니다.
