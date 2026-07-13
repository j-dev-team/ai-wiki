# AI Wiki 1.2.0 사용자 설명서

이 문서는 Windows에서 AI Wiki 1.2.0을 설치하고 일반 위키와 목적별 위키를 한 대의 PC에서 운영하는 전체 절차를 설명합니다. AI Wiki는 로컬 단독 사용 도구이며 계정·로그인·서버 설정이 필요 없습니다. 웹 서버를 인터넷에 공개하지 말고, 고객 정보나 민감정보가 있는 위키는 운영체제 사용자 계정과 디스크 접근 권한으로 보호하세요. 전체 저장소 암호화와 접근 통제는 운영체제에서 관리합니다.

## 1. 준비 사항

- Python 3.11 이상
- 인터넷 연결: 최초 설치와 벡터 모델 다운로드에 필요
- PowerShell 또는 Windows Terminal

버전을 확인합니다.

```powershell
python --version
python -m pip --version
```

`python` 명령을 찾지 못하면 Python을 먼저 설치하고, 설치 화면에서 `Add Python to PATH`를 선택합니다.

## 2. 설치와 업그레이드

### 처음 설치

```powershell
python -m pip install ai-wiki
ai-wiki --help
```

`pip` 대신 `python -m pip`를 사용하면 어떤 Python 환경에 설치되는지 더 명확합니다. 벡터 검색에 필요한 `sentence-transformers`와 `sqlite-vec`도 함께 설치됩니다.

### 기존 버전 업그레이드

실행 중인 `ai-wiki-web`과 목적별 위키 웹 서버를 먼저 종료합니다. 중요한 데이터는 업그레이드 전에 복사해 둡니다.

```powershell
python -m pip install --upgrade ai-wiki
ai-wiki upgrade-skill
ai-wiki migrate-schema
ai-wiki vindex
ai-wiki doctor
```

`migrate-schema`는 검사만 수행합니다. 1.2.0은 기존 v1·v2 문서를 읽을 때
정규화하고 실제로 수정된 문서만 현재 스키마로 저장하므로 일괄 `--apply`는 필요하지
않습니다. 시간 지식·엔터티·이벤트 연결을 새로 추가하는 문서만 v3 확장을 사용합니다.
사용자가 명시적으로 일괄 변환을 결정한 경우에만 백업 후 실행합니다.

`upgrade-skill`은 기본 AI Wiki 스킬과 `ai-wiki-missions`, `ai-wiki-deep-research`
스킬을 함께 갱신합니다. 중복되었거나 오래된 설치본은 `skill audit`으로 먼저
확인하고, 현재 위키에 맞는 스킬만 유지합니다.

정상 결과의 기준은 다음과 같습니다.

- `doctor`의 `status`가 `ok`
- `vector.ready`가 `true`
- `article_count`와 `vector.vector_count`가 같음

목적별 위키는 공용 엔진 업그레이드 후 패키지별 갱신도 실행합니다.

```powershell
ai-wiki variant upgrade D:\dev\law-wiki
ai-wiki variant upgrade D:\dev\labor-wiki
ai-wiki variant upgrade D:\dev\tax-wiki
```

각 `variant upgrade`는 먼저 백업을 만들고, 검증에 실패하면 이전 상태로 되돌립니다.

## 3. 일반 AI Wiki 시작하기

### AI 에이전트 기본 흐름

AI 에이전트는 다음 순서로 지식을 검색하고 활용합니다.

```powershell
ai-wiki capabilities
ai-wiki context "질문" --max-tokens 4000
ai-wiki get <문서-ID>
ai-wiki record-use <context-id> --citation "doc:<ID>#<경로>" --outcome answered
```

컨텍스트가 부족하면 외부 조사 후 전체 문서를 덮어쓰지 않고 patch합니다.

```powershell
ai-wiki patch <문서-ID> --operations-file patch.json --if-version 2 --dry-run
ai-wiki patch <문서-ID> --operations-file patch.json --if-version 2
```

출처 없는 지식은 confidence 0.5 이하의 검증 대기 초안으로 저장되며 일반
context에서는 제외됩니다.

### 위키 만들기

```powershell
ai-wiki init D:\wiki\my-wiki
```

초기화 과정에서 다음 항목을 선택합니다.

1. 화면 언어: 한국어 또는 영어
2. 위키 표시 이름
3. 연결할 AI 에이전트: Claude Code, Antigravity CLI의 Gemini, GPT Codex
4. 카테고리 프리셋: 범용, 기술, 비즈니스, 연구

초기화가 끝나면 위키 루트와 설정이 등록됩니다. 현재 연결 상태는 다음 명령으로 확인합니다.

```powershell
ai-wiki doctor
ai-wiki list
```

### 가장 짧은 사용 흐름

```powershell
ai-wiki quickstart
ai-wiki search "검색할 내용"
ai-wiki list
ai-wiki-web
```

일반 위키 웹 UI의 기본 주소는 `http://127.0.0.1:5000`입니다.

## 4. 문서 작성과 수정

### 작성용 YAML 만들기

```powershell
ai-wiki template technology --output content.yaml
```

`content.yaml`을 편집합니다.

```yaml
type: technology
what: pytest는 Python 테스트 프레임워크다.
facts:
  - 일반 Python assert 문을 사용할 수 있다.
  - fixture로 테스트 준비 절차를 재사용할 수 있다.
language: Python
framework: pytest
use_cases:
  - 단위 테스트
  - 통합 테스트
limitations:
  - 비동기 테스트에는 플러그인이 필요할 수 있다.
_v:
  level: sourced
  sources:
    - https://docs.pytest.org/
  note: 공식 문서를 기준으로 확인
```

작성용 `content.yaml`에서는 위와 같은 간단한 `_v` 표기를 계속 사용할 수
있습니다. 저장할 때는 본문에서 제거되고 v2 문서의 최상위 `verification` 목록과
`source_ids` 참조로 정규화됩니다. 전체 스키마는 다음 명령으로 확인합니다.

```powershell
ai-wiki schema-json
```

문서를 생성합니다.

```powershell
ai-wiki create `
  --title "pytest 테스트 프레임워크" `
  --category "technology/python" `
  --tags "python,testing,pytest" `
  --source "https://docs.pytest.org/" `
  --confidence 0.9 `
  --content-file content.yaml
```

품질 게이트가 문서를 거부하면 출력된 누락 필드를 보완합니다. 출처나 검증 정보 없이 강제로 저장하는 방식은 권장하지 않습니다.

### 조회, 수정, 삭제

```powershell
ai-wiki get <문서-ID>
ai-wiki update <문서-ID> --content-file content.yaml
ai-wiki delete <문서-ID> --confirm
```

삭제 전에는 `get`으로 문서 ID와 제목을 다시 확인합니다.

## 5. 검색과 벡터 인덱스

### 하이브리드 검색

```powershell
ai-wiki search "근로계약서 작성 기준"
```

기본 검색은 문서 FTS5, 한국어 trigram 청크 FTS, 청크 벡터 의미 검색을
문서 다양성을 유지하는 RRF 방식으로 결합합니다. `context`의 `evidence`에는
원본 YAML의 `/content/data/...` 경로와 해당 청크 본문이 함께 들어갑니다.

### 순수 벡터 검색

```powershell
ai-wiki vsearch "직원을 해고할 때 확인할 사항"
```

정확히 같은 단어가 없어도 의미가 가까운 문서를 찾습니다.

### 벡터 검색 복구

```powershell
ai-wiki doctor
ai-wiki reindex
ai-wiki vindex
ai-wiki doctor
```

`chunk_index.ready`가 `false`이면 `reindex`를 실행합니다. 문서 수와 벡터 문서
수가 다르거나 `vector.ready`가 `false`이면 `vindex`를 실행합니다. 최초 실행은
다국어 임베딩 모델을 내려받으므로 시간이 걸릴 수 있습니다. 변경된 문서만
다시 임베딩하려면 `ai-wiki vindex --incremental`을 사용합니다.

벡터 검색이 반드시 필요한 AI 작업은 다음처럼 폴백을 금지할 수 있습니다.

```powershell
ai-wiki context "질문" --require-vector
```

위키별 정답 문서 평가 JSON이 있으면 유사도 수치를 해당 말뭉치에 맞춰 보정할
수 있습니다.

```powershell
ai-wiki vcalibrate --eval-file retrieval-eval.json
```

## 6. 웹 UI

```powershell
ai-wiki-web
```

- 기본 주소: `http://127.0.0.1:5000`
- 다른 포트 사용: `ai-wiki-web 5010`
- 화면 언어: 웹 UI의 한국어/English 선택 사용

서버를 종료하려면 실행 중인 터미널에서 `Ctrl+C`를 누릅니다. AI Wiki는 로컬 단독 사용 기준이므로 `0.0.0.0` 외부 바인딩이나 공유기 포트 개방을 하지 마세요.

## 7. Mission으로 AI 작업 관리

Mission은 조사 보고서, 승인된 작업 계획, 실행 작업, 완료 기준별 증거, 독립 검토와 인계를 revision으로 연결합니다. AI가 긴 작업을 재개할 때는 전체 이력 대신 다음 작업에 필요한 최소 완전 컨텍스트를 읽습니다.

```powershell
# 실행 중인 Mission의 진행률과 다음 작업 확인
ai-wiki run summary <run-id>
ai-wiki run next <run-id>

# 특정 작업에 필요한 계획·기준·기존 증거만 읽기
ai-wiki task context <run-id> <task-id>

# 특정 완료 기준의 증거만 검토하기
ai-wiki run evidence <run-id> --criterion <criterion-id>
```

전체 감사 원장은 독립 검토나 장애 분석이 필요할 때만 `ai-wiki run status <run-id> --full`로 확인합니다. Mission 실행 절차는 설치된 `ai-wiki-missions` 스킬을 사용합니다.

## 8. 목적별 독립 위키

### 제공 프리셋 확인

```powershell
ai-wiki variant presets
ai-wiki variant show-preset law
```

주요 프리셋에는 `general`, `tech`, `business`, `research`, `law`, `labor`, `tax`, `corporate`, `personal`이 있습니다.

### 한 명령으로 설치

```powershell
ai-wiki variant install law-wiki `
  --preset law `
  --output-dir D:\dev `
  --agent codex `
  --lang ko
```

이 명령은 다음 작업을 자동으로 수행합니다.

1. 얇은 목적별 패키지 생성
2. editable 패키지 설치
3. 독립 설정, DB, 벡터 DB와 웹 포트 생성
4. AI 에이전트 스킬 설치
5. 초기 문서 벡터 인덱싱
6. `doctor` 진단

설치 후에는 전용 명령을 사용합니다.

```powershell
law-wiki doctor
law-wiki search "계약 해제"
law-wiki vsearch "계약을 끝내는 방법"
law-wiki-web
```

패키지별 웹 포트는 `variant.yaml`의 `web_port`에서 확인할 수 있습니다.

### 다른 에이전트 스킬 추가

```powershell
ai-wiki variant install-skills D:\dev\law-wiki --agent claude
ai-wiki variant install-skills D:\dev\law-wiki --agent gemini
ai-wiki variant install-skills D:\dev\law-wiki --agent codex
```

Gemini 에이전트는 기존 `gemini` 명령 대신 Antigravity CLI의 `agy`를 사용합니다.
Windows에서는 다음 명령으로 설치하고, 유료 Gemini 계정으로 한 번 인증합니다.

```powershell
irm https://antigravity.google/cli/install.ps1 | iex
agy
```

인증 후 `agy --print "OK라고 답해"`로 비대화식 실행을 확인할 수 있습니다.

스킬 설치 경로는 다음과 같습니다.

| 에이전트 | 경로 |
| --- | --- |
| Claude Code | `~/.claude/skills/<위키명>/` |
| Antigravity CLI의 Gemini | `~/.gemini/config/skills/<위키명>/` |
| GPT Codex | `~/.codex/skills/<위키명>/` |

## 9. 사용자 정의 위키

내장 프리셋에 없는 분야도 매니페스트로 만들 수 있습니다.

```powershell
ai-wiki variant init-manifest patent-wiki `
  --preset general `
  --display-name "특허 위키" `
  --domain patent `
  --command patent-wiki `
  --description "특허 조사와 출원 지식 위키" `
  --trigger "특허" `
  --trigger "출원" `
  --output D:\dev\patent-wiki.yaml
```

생성된 YAML을 검토한 뒤 설치합니다.

```powershell
ai-wiki variant install `
  --manifest D:\dev\patent-wiki.yaml `
  --output-dir D:\dev `
  --agent codex `
  --lang ko
```

패키지명, 명령 이름, 환경변수 접두사, 설정 파일, 웹 포트가 다른 위키와 겹치지 않게 설정해야 합니다.

## 10. 백업, 복원, 업데이트, 제거

### 백업

웹 서버를 종료한 다음 실행합니다.

```powershell
ai-wiki variant backup D:\dev\law-wiki
```

저장 위치를 지정할 수도 있습니다.

```powershell
ai-wiki variant backup D:\dev\law-wiki `
  --output D:\backup\law-wiki-2026-07-11.zip
```

### 복원

복원은 현재 패키지 내용을 백업 파일의 상태로 교체합니다. 대상 경로와 백업 파일을 다시 확인한 뒤 실행합니다.

```powershell
ai-wiki variant restore `
  D:\backup\law-wiki-2026-07-11.zip `
  D:\dev\law-wiki
```

복원 후 검증합니다.

```powershell
law-wiki doctor
law-wiki search "기존 문서 제목"
```

### 목적별 패키지 업데이트

```powershell
python -m pip install --upgrade ai-wiki
ai-wiki variant upgrade D:\dev\law-wiki
law-wiki doctor
```

### 프로그램만 제거하고 데이터 보존

```powershell
ai-wiki variant uninstall D:\dev\law-wiki
```

### 데이터까지 완전히 제거

```powershell
ai-wiki variant uninstall D:\dev\law-wiki --purge --yes
```

`--purge --yes`는 패키지 루트를 삭제합니다. 이 명령은 자동 백업을 만든 뒤 실행되지만, 백업 파일 위치를 확인하기 전에는 반복 실행하지 마세요.

## 11. 여러 위키 점검

설정, 명령, DB, 환경변수와 포트가 서로 겹치지 않는지 검사합니다.

```powershell
ai-wiki variant audit-isolation `
  D:\dev\law-wiki `
  D:\dev\labor-wiki `
  D:\dev\tax-wiki
```

설치된 스킬과 질문 라우팅 충돌을 검사합니다.

```powershell
ai-wiki variant audit-skills `
  D:\dev\law-wiki `
  D:\dev\labor-wiki `
  D:\dev\tax-wiki
```

## 12. 정기 관리

```powershell
ai-wiki doctor
ai-wiki maintain
ai-wiki quality-all
ai-wiki stale
ai-wiki verify-queue
```

권장 주기는 다음과 같습니다.

- 문서를 대량 추가한 후: `vindex`, `doctor`
- 매주: `maintain`, `verify-queue`
- 업그레이드 전: 백업
- 중요한 데이터 변경 후: 별도 외장 디스크나 보호된 저장소에 백업 복사

## 13. 문제 해결

### `ai-wiki` 명령을 찾지 못함

```powershell
python -m pip show ai-wiki
python -m pip install --upgrade ai-wiki
```

설치 경로의 `Scripts` 디렉터리가 PATH에 포함되어 있는지도 확인합니다.

### 다른 위키의 문서가 검색됨

```powershell
ai-wiki doctor
ai-wiki variant audit-isolation <위키경로1> <위키경로2>
```

일반 위키는 `AI_WIKI_ROOT`, 목적별 위키는 각 매니페스트에 정의된 전용 환경변수를 사용합니다.

### 벡터 검색이 준비되지 않음

```powershell
python -m pip install --upgrade ai-wiki
ai-wiki vindex
ai-wiki doctor
```

### 웹 페이지가 열리지 않음

1. 웹 서버 터미널이 실행 중인지 확인합니다.
2. 출력된 포트 번호를 확인합니다.
3. 같은 포트를 다른 프로그램이 사용한다면 다른 포트로 실행합니다.

```powershell
ai-wiki-web 5010
```

### 스킬이 이전 명령을 사용함

```powershell
ai-wiki upgrade-skill
```

목적별 위키는 해당 패키지에 스킬을 다시 설치합니다.

```powershell
ai-wiki variant install-skills D:\dev\law-wiki --agent codex
```

## 14. 데이터 위치와 보안

일반 위키의 기본 구조는 다음과 같습니다.

```text
my-wiki/
  articles/          YAML 원본 문서
  data/wiki.db       키워드 검색 및 메타데이터 DB
  data/vectors.db    벡터 인덱스
  sources/           원본 자료
  logs/              작업 로그
  .ai-wiki.yaml      위키 설정
```

`articles/`가 지식의 원본이며 DB는 검색용 인덱스입니다. 하지만 관계, 통계와 운영 상태를 함께 복구하려면 위키 루트 전체를 백업하는 것이 안전합니다.
