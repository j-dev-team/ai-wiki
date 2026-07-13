# AI Wiki 릴리스 및 업그레이드 워크플로우

이 문서는 AI Wiki의 새 버전을 GitHub와 PyPI에 게시하고 기존 설치본을 업그레이드하는 공식 절차입니다. 릴리스는 공용 소스 저장소에서 수행하며, 실데이터가 있는 위키 디렉터리는 배포 소스로 사용하지 않습니다.

## 기본 원칙

- PyPI에 게시된 버전은 수정하거나 같은 번호로 다시 올릴 수 없습니다.
- 모든 변경은 새 버전 번호를 사용합니다.
- GitHub 커밋과 `vX.Y.Z` 태그가 PyPI 산출물의 소스와 정확히 같아야 합니다.
- 테스트, wheel/sdist 빌드와 `twine check`가 모두 통과한 뒤에만 게시합니다.
- `dist/*` 전체를 업로드하지 않고 해당 버전의 wheel과 sdist만 명시합니다.
- PyPI 토큰은 채팅, 명령 인자, 로그에 출력하지 않습니다.
- 고객·상담 데이터가 있는 실제 위키 디렉터리는 빌드에 포함하지 않습니다.

## 버전 정책

AI Wiki는 `MAJOR.MINOR.PATCH` 형식을 사용합니다.

| 변경 종류 | 예시 | 기준 |
| --- | --- | --- |
| PATCH | `0.2.0 -> 0.2.1` | 버그 수정, 호환되는 문서·UI 개선 |
| MINOR | `0.2.1 -> 0.3.0` | 새 명령, 새 연동, 호환되는 기능 추가 |
| MAJOR | `0.9.0 -> 1.0.0` | 안정 API 확정 또는 호환되지 않는 변경 |

PyPI는 같은 버전의 파일을 교체할 수 없으므로, 게시 후 문제가 발견되면 반드시 다음 PATCH 버전을 만듭니다.

## 1. 개발 완료 확인

다음을 먼저 확인합니다.

- 요구사항과 회귀 테스트 완료
- 벡터 검색을 포함한 핵심 기능 정상
- Codex와 Antigravity CLI의 Gemini 실평가 24/24 통과
- Claude Code 스킬·프로토콜 정적 호환성 확인(유료 계정 실평가는 선택)
- 데이터 마이그레이션이 있으면 백업·복원 시험 완료
- 한국어·영어 UI와 문서 갱신

## 2. 버전 변경

버전 문자열은 다음 위치에서 같은 값이어야 합니다.

```text
pyproject.toml
src/ai_wiki/__init__.py
src/ai_wiki/variant.py
src/ai_wiki/templates/base.html
src/ai_wiki/skill_templates/SKILL.md
src/ai_wiki/mission_skill_templates/SKILL.md
src/ai_wiki/deep_research_skill_templates/SKILL.md
```

`variant.py`에는 생성되는 목적별 패키지의 버전과 공용 엔진 의존 범위가 있습니다. 예를 들어 `0.3.0` 릴리스는 다음 범위를 사용합니다.

```toml
dependencies = ["ai-wiki>=0.3,<0.4"]
```

`CHANGELOG.md` 맨 위에 릴리스 날짜와 사용자 관점의 변경사항을 기록합니다.

```markdown
## 0.3.0 - 2026-XX-XX

- Added ...
- Fixed ...
```

## 3. 사용설명서 갱신

기능이나 명령이 바뀌면 다음 문서를 같이 수정합니다.

- `README.md`: PyPI와 GitHub 첫 화면
- `README.ko.md`: 한국어 빠른 시작
- `docs/USER_GUIDE.ko.md`: 전체 사용설명서
- `docs/VARIANTS.md`: 목적별 위키 설치와 수명주기
- `CHANGELOG.md`: 버전 변경 기록
- 자기참조 문서: 새 엔진 기능과 설치 스킬의 실제 동작

문서의 설치·업그레이드 명령은 실제 CLI `--help`와 대조합니다.

## 4. 변경 커밋

사용자 데이터나 임시 산출물을 포함하지 않았는지 확인합니다.

```powershell
git status --short
git diff --check
git diff --stat
```

릴리스 대상 파일만 명시적으로 stage하고 커밋합니다.

```powershell
git add -- <릴리스 대상 파일>
git commit -m "release: ai-wiki X.Y.Z"
```

`articles/`, `data/`, `.pypirc`, API 키, 모델 캐시와 실제 위키 백업은 절대 커밋하지 않습니다.

## 5. 자동 사전 검증과 빌드

저장소 루트에서 실행합니다.

```powershell
.\scripts\release.ps1 -Version X.Y.Z
```

기본 실행은 외부 게시를 하지 않고 다음 작업만 수행합니다.

1. 릴리스 버전 문자열 일치 확인
2. `CHANGELOG.md` 항목 확인
3. tracked 작업트리 청결 확인
4. PyPI에 같은 버전이 이미 존재하면 즉시 중단
5. canonical `src` 기준 전체 pytest
6. 기존 `dist`, `build`, egg-info 제거
7. 릴리스 전용 가상환경 생성
8. wheel과 sdist 빌드
9. `twine check`
10. 필수 wheel 리소스 검사
11. SHA-256 출력 및 `release-dist` 복사

필수 wheel 리소스에는 기본 스킬, Mission 스킬과 예시, Deep Research 스킬도
포함됩니다. 누락되면 설치는 성공해도 새 위키나 기존 위키가 불완전한 스킬을 받게
되므로 게시 전에 반드시 수정합니다.

실패한 단계가 있으면 게시하지 않고 원인을 수정한 뒤 새 커밋에서 다시 실행합니다.

이미 PyPI에 게시된 버전은 사전 빌드도 거부합니다. 게시 이후 소스에서 같은 버전을 다시 빌드하면 공식 파일과 해시가 다른 산출물이 생길 수 있기 때문입니다.

## 6. 실제 게시

사전 검증이 성공한 동일 커밋에서 실행합니다.

```powershell
.\scripts\release.ps1 -Version X.Y.Z -Publish
```

스크립트는 게시 직전에 `vX.Y.Z`를 입력하도록 요구합니다. 확인 후 다음 작업을 수행합니다.

1. PyPI에 같은 버전이 없는지 확인
2. `~/.pypirc` 프로젝트 토큰 설정 확인
3. 현재 HEAD에 annotated tag 생성 또는 일치 확인
4. GitHub `master` 푸시
5. GitHub `vX.Y.Z` 태그 푸시
6. 해당 버전 wheel과 sdist만 PyPI 업로드
7. PyPI JSON API에서 버전과 파일 해시 확인

비대화형 자동화가 꼭 필요할 때만 `-Yes`를 추가합니다.

```powershell
.\scripts\release.ps1 -Version X.Y.Z -Publish -Yes
```

## 7. 게시 후 설치 검증

PyPI 캐시가 아닌 공개 인덱스에서 설치합니다.

```powershell
python -m venv .verify-venv
.\.verify-venv\Scripts\python.exe -m pip install --no-cache-dir ai-wiki==X.Y.Z
.\.verify-venv\Scripts\python.exe -c "import ai_wiki; print(ai_wiki.__version__)"
.\.verify-venv\Scripts\ai-wiki.exe variant presets
```

최종 인수시험에는 임시 목적별 위키를 사용합니다.

```text
설치 -> 목적별 위키 생성 -> 스킬 설치 -> 초기 문서
-> doctor -> 키워드 검색 -> 벡터 검색
-> 백업 -> 업그레이드 -> 복원 -> 제거
```

실제 운영 위키나 사용자 데이터로 릴리스 시험을 하지 않습니다.

## 8. 기존 사용자 업그레이드 안내

일반 위키 사용자는 다음 순서로 업그레이드합니다.

```powershell
python -m pip install --upgrade ai-wiki
ai-wiki upgrade-skill
ai-wiki vindex
ai-wiki doctor
```

업그레이드 후 `ai-wiki skill audit`으로 기본·Mission·Deep Research 스킬이 같은
버전인지와 중복 설치본이 없는지를 확인합니다. 과거 별칭이 남아 있으면 해당 위키의
`upgrade-skill` 또는 variant 업그레이드를 실행해 현재 스킬 이름으로 정리합니다.

목적별 위키는 공용 엔진 업그레이드 후 패키지별 갱신을 실행합니다.

```powershell
ai-wiki variant upgrade D:\dev\law-wiki
ai-wiki variant upgrade D:\dev\labor-wiki
ai-wiki variant upgrade D:\dev\tax-wiki
```

업그레이드 전에 실행 중인 웹 서버를 종료하고 중요한 위키를 백업합니다.

## 9. 실패 대응

### 빌드 전에 실패

코드나 문서를 수정하고 테스트부터 다시 실행합니다. 버전 번호는 유지할 수 있습니다.

### GitHub 푸시 후 PyPI 업로드 실패

- 태그를 임의로 다른 커밋으로 옮기지 않습니다.
- PyPI에 일부 파일이 올라갔는지 먼저 확인합니다.
- 게시되지 않은 파일만 복구할 수 있는지 검토합니다.
- 애매하면 다음 PATCH 버전을 만들어 전체 절차를 다시 수행합니다.

### PyPI 게시 후 결함 발견

기존 릴리스를 삭제하거나 파일을 바꾸지 않습니다. 필요하면 해당 버전을 yank하고 수정한 다음 PATCH 버전을 게시합니다.

### 토큰 노출

1. PyPI 계정의 API token 관리에서 즉시 폐기
2. `ai-wiki` 프로젝트 범위의 새 토큰 생성
3. 로컬 `~/.pypirc` 교체
4. 채팅, 로그와 셸 기록에 노출된 값이 없는지 확인

## 10. 정기 개선

수동 토큰 대신 GitHub Actions와 PyPI Trusted Publishing을 도입하면 장기 토큰을 PC에 저장하지 않아도 됩니다. 도입 전에는 GitHub 저장소, `release.yml`, PyPI 프로젝트와 GitHub `pypi` environment를 정확히 연결하고 수동 승인자를 설정합니다.
