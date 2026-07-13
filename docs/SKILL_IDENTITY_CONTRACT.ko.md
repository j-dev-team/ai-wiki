# 스킬 정체성·별칭·루트 계약

목적별 AI Wiki는 아래 네 값으로 스킬과 Mission의 저장 위치를 결정한다.

| 값 | 기준 원본 | 용도 |
| --- | --- | --- |
| `skill_name` | `VariantSpec` | 정식 스킬 디렉터리와 스킬 frontmatter 이름 |
| `skill_aliases` | `VariantSpec` 및 과거 밑줄 표기 | 구버전 탐지·백업 마이그레이션 대상 |
| `command_name` | `VariantSpec` | 에이전트가 실행해야 하는 전용 CLI |
| `env_prefix` | `VariantSpec` | `<env_prefix>_ROOT` 전용 위키 루트 선택자 |

`<skill_name>-missions`는 Mission 스킬의 정식 식별자다. 일반 AI Wiki는
`ai-wiki`와 `ai-wiki-missions`를 사용한다. 법률·노무·세무·업무 variant는
각각 `law-wiki`, `labor-wiki`, `tax-wiki`, `woosong-wiki`와 그 Mission
식별자를 사용한다.

## 설치·업그레이드 규칙

1. init과 upgrade는 사용자 편집 위키 이름이 아니라 `skill_name`에만 쓴다.
2. 정식 경로 외의 등록 별칭(예: `woosong_wiki`)을 발견하면 먼저
   `.ai-wiki-skill-backups/`에 전체 디렉터리를 복사한다.
3. 백업이 성공한 경우에만 별칭 경로를 제거하고 정식 템플릿을 설치한다.
4. 위키 데이터, 문서, Mission 원장에는 어떤 이동·삭제도 하지 않는다.
5. 실패하면 정식 경로와 별칭을 그대로 보존하고 오류를 반환한다.

## 감사 규칙

감사는 agent별 정식 `SKILL.md`의 SHA-256을 배포 템플릿과 비교하고, 등록된
별칭 디렉터리의 존재를 중복 오류로 보고한다. 존재 여부만으로 `OK`를 반환하지
않는다. Mission 스킬은 같은 agent 루트에서 `<skill_name>-missions`로 설치하고
그 안의 CLI 예시가 `command_name`을 사용하는지 스냅샷 테스트로 확인한다.

## 되돌림

별칭을 마이그레이션한 뒤 문제가 생기면 해당 agent의
`.ai-wiki-skill-backups/<alias>-<timestamp>/`를 원래 별칭 이름으로 복원한 뒤,
정식 스킬 디렉터리를 검사한다. 이 절차는 스킬 파일만 다루며 사용자 지식이나
Mission 데이터에는 영향을 주지 않는다.
