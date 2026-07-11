# AI Wiki 운영 명령 지침

## 상태 진단

```bash
ai-wiki doctor
```

확인 항목:

- 위키 경로
- 문서 수
- `sqlite-vec` 설치 상태
- `sentence-transformers` 설치 상태
- `vectors.db` 존재 여부
- 벡터 인덱스 문서 수
- 필요한 조치

모델 로딩까지 확인하려면:

```bash
ai-wiki doctor --load-model
```

## 검색

```bash
ai-wiki search "query"
ai-wiki vsearch "semantic query"
```

기본 `search`는 FTS5와 벡터 검색을 결합한다. 벡터 인덱스가 비어 있으면 `ai-wiki vindex`를 실행한다.

## 인덱스 재생성

```bash
ai-wiki reindex
ai-wiki vindex
```

- `reindex`: YAML 원본을 읽어 SQLite FTS/메타 인덱스 재생성
- `vindex`: 모든 문서의 임베딩을 다시 생성

## 위키 유지보수

```bash
ai-wiki lint
ai-wiki lint --fix
ai-wiki maintain
```

- 깨진 related 참조
- 단방향 링크
- 출처 없는 문서
- 낮은 confidence
- 고립 문서

## 품질 관리

```bash
ai-wiki quality <id>
ai-wiki quality-all
ai-wiki todo
ai-wiki gaps
ai-wiki stale
```

운영 루틴:

1. `ai-wiki doctor`
2. `ai-wiki maintain`
3. `ai-wiki todo`
4. 중요한 문서부터 보강
5. `ai-wiki vindex`

## 백업과 삭제

AI Wiki는 로컬 파일 기반이다. 삭제 전 위키 디렉터리를 백업한다.

```bash
ai-wiki destroy <path> --confirm
```

`destroy`는 위키 디렉터리와 설치된 skill 파일을 삭제한다.
