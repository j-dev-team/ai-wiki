"""Run an isolated Korean chunk-retrieval and citation quality gate."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

from ai_wiki.agent_protocol import build_context, estimate_tokens
from ai_wiki.index import WikiIndex
from ai_wiki.models import Article
from ai_wiki.storage import get_relative_path, save_article
from ai_wiki.vector import VectorIndex


CASES = [
    ("solar", "태양광 발전", "태양광 패널은 햇빛을 전기에너지로 변환한다.",
     ["햇빛으로 전기를 만드는 장치", "태양 복사 에너지를 전력으로 바꾸는 방법", "광전 효과를 이용한 발전 설비"]),
    ("insulin", "인슐린", "인슐린은 혈당을 조절하는 췌장 호르몬이다.",
     ["혈액 속 포도당 농도를 낮추는 호르몬", "췌장에서 분비되어 당 수치를 조절하는 물질", "식후 혈당 관리에 관여하는 호르몬"]),
    ("atomic", "원자적 트랜잭션", "원자성은 데이터 작업이 전부 성공하거나 전부 취소되는 성질이다.",
     ["여러 데이터 변경을 모두 실행하거나 모두 되돌리는 성질", "중간 상태를 남기지 않는 데이터 작업", "all or nothing 데이터 처리 원칙"]),
    ("employment", "근로계약서", "근로계약서는 임금과 근로시간 등 근로조건을 기록한 문서다.",
     ["입사할 때 급여와 근무시간을 적는 문서", "사용자와 근로자가 근무 조건을 합의한 서류", "신규 직원의 보수와 업무 조건을 명시하는 계약"]),
    ("cache", "캐시 무효화", "원본 데이터가 바뀌면 오래된 캐시를 갱신하거나 제거해야 한다.",
     ["원본 변경 후 저장된 빠른 복사본을 새로 만드는 절차", "오래된 임시 저장 데이터 제거", "데이터 변경 시 캐시 일관성을 유지하는 방법"]),
    ("embedding", "텍스트 임베딩", "텍스트 임베딩은 언어의 의미를 수치 벡터로 표현한다.",
     ["문장의 뜻을 숫자 배열로 바꾸는 기술", "언어 의미의 수학적 좌표 표현", "비슷한 문장을 가까운 숫자 공간에 배치하는 방식"]),
    ("vat", "부가가치세", "부가가치세는 재화와 용역의 거래 과정에서 발생하는 간접세다.",
     ["상품 거래 과정에 붙는 간접세", "사업자가 매출과 매입을 기준으로 신고하는 세금", "재화와 서비스 공급에 과세되는 세목"]),
    ("dismissal", "해고 예고", "사용자는 원칙적으로 해고 30일 전에 근로자에게 예고해야 한다.",
     ["직원을 내보내기 전에 통지해야 하는 기간", "근로관계 종료를 한 달 전에 알리는 제도", "해고 전 사전 통보 의무"]),
    ("dns", "DNS", "DNS는 사람이 읽는 도메인 이름을 IP 주소로 변환한다.",
     ["웹사이트 이름을 네트워크 주소로 바꾸는 시스템", "도메인으로 서버 IP를 찾는 절차", "인터넷 전화번호부 역할을 하는 서비스"]),
    ("photosynthesis", "광합성", "식물은 빛을 이용해 이산화탄소와 물에서 유기물을 만든다.",
     ["식물이 햇빛으로 양분을 만드는 과정", "빛 에너지를 화학 에너지로 저장하는 생물 반응", "잎에서 이산화탄소를 이용해 영양분을 합성하는 작용"]),
    ("inflation", "인플레이션", "인플레이션은 전반적인 물가 수준이 지속해서 상승하는 현상이다.",
     ["돈의 구매력이 떨어지고 상품 가격이 계속 오르는 현상", "경제 전체의 가격 수준 상승", "같은 돈으로 살 수 있는 물건이 줄어드는 상황"]),
    ("encryption", "공개키 암호화", "공개키 암호화는 서로 다른 공개키와 개인키를 사용하는 암호 방식이다.",
     ["암호화와 복호화에 서로 다른 열쇠를 쓰는 기술", "누구나 아는 키와 소유자만 가진 키의 조합", "비대칭 키를 사용하는 보안 방식"]),
    ("backup", "증분 백업", "증분 백업은 직전 백업 이후 변경된 데이터만 저장한다.",
     ["마지막 저장 이후 바뀐 파일만 복사하는 백업", "전체를 매번 복제하지 않는 백업 방식", "변경분만 보관해 용량을 줄이는 데이터 보호"]),
    ("version", "버전 관리", "버전 관리는 파일 변경 이력과 협업 상태를 체계적으로 기록한다.",
     ["소스 코드 수정 기록을 추적하는 시스템", "이전 파일 상태로 돌아가기 위한 변경 이력 관리", "여러 개발자의 변경을 합치는 도구"]),
    ("vaccine", "백신", "백신은 면역계가 특정 병원체를 미리 인식하도록 훈련한다.",
     ["감염 전에 면역 반응을 준비시키는 의약품", "몸이 병원체를 기억하게 만드는 예방 수단", "특정 감염병에 대한 방어 능력을 형성하는 제제"]),
    ("rag", "검색 증강 생성", "검색 증강 생성은 외부 근거를 검색해 생성 모델의 답변에 제공한다.",
     ["문서를 먼저 찾고 그 근거로 AI가 답하는 방식", "외부 지식을 언어 모델 문맥에 넣는 기술", "검색 결과를 활용해 생성 답변의 근거를 강화하는 구조"]),
]


def run_gate() -> dict:
    previous_root = os.environ.get("AI_WIKI_ROOT")
    with tempfile.TemporaryDirectory(prefix="ai-wiki-vector-gate-") as temp:
        root = Path(temp)
        (root / "articles").mkdir()
        (root / "data").mkdir()
        os.environ["AI_WIKI_ROOT"] = str(root)
        index = WikiIndex(root / "data" / "wiki.db")
        articles = []
        try:
            for number, (slug, title, fact, _queries) in enumerate(CASES):
                filler = "배경 설명은 검색 정답과 무관하다. " * 180 if number % 4 == 0 else "간단한 배경 설명"
                article = Article(
                    id=f"eval-ko-{slug}-abc123", title=title,
                    category="evaluation/korean", tags=["평가", slug], confidence=0.95,
                    sources=[f"https://example.com/eval/{slug}"], author="quality-gate",
                    content={
                        "type": "knowledge", "summary": f"{title}에 관한 검증 문서다.",
                        "facts": [filler, fact], "limitations": ["격리 평가 데이터"],
                    },
                )
                article.verification = [{
                    "path": "/content/data/facts/1", "level": "verified",
                    "source_ids": ["src-1"],
                }]
                path = save_article(article)
                index.upsert(article, get_relative_path(path))
                articles.append(article)

            vector = VectorIndex(root / "data" / "vectors.db")
            started = time.perf_counter()
            build = vector.upsert_many(articles, rebuild=True)
            indexing_seconds = time.perf_counter() - started
            results = []
            citation_passed = 0
            budget_violations = 0
            samples = []
            latencies = []
            for slug, _title, _fact, queries in CASES:
                expected = f"eval-ko-{slug}-abc123"
                for query in queries:
                    query_started = time.perf_counter()
                    ranked = index.search(query, limit=5, require_vector=True)
                    latencies.append((time.perf_counter() - query_started) * 1000)
                    ids = [item["id"] for item in ranked]
                    vector_ids = [item["id"] for item in vector.search(query, limit=16)]
                    chunk_fts_ids = [item["id"] for item in index._search_chunk_fts(query, limit=100)]
                    document_fts_ids = [item["id"] for item in index._search_fts(query, limit=16)]
                    passed = expected in ids
                    for item in ranked:
                        samples.append((float(item.get("vector_similarity") or -1.0), int(item["id"] == expected)))
                    context = build_context(index, query, max_tokens=4000, require_vector=True)
                    citation_paths = [item["path"] for item in context["data"]["citations"]]
                    citation_ok = "/content/data/facts/1" in citation_paths
                    citation_passed += int(citation_ok)
                    budget_violations += int(estimate_tokens(context) > 4000)
                    results.append({
                        "query": query, "expected": expected, "ids": ids,
                        "vector_ids": vector_ids, "chunk_fts_ids": chunk_fts_ids,
                        "document_fts_ids": document_fts_ids,
                        "pass": passed, "citation_pass": citation_ok,
                    })
            calibration = vector.calibrate(samples)
            vector.close()
            latencies.sort()
            total = len(results)
            report = {
                "status": "ok" if all(item["pass"] for item in results)
                and citation_passed == total and budget_violations == 0 else "failed",
                "documents": len(articles), "queries": total,
                "chunks": build["chunks"], "indexing_seconds": round(indexing_seconds, 3),
                "recall_at_5": sum(item["pass"] for item in results) / total,
                "citation_accuracy": citation_passed / total,
                "token_budget_violations": budget_violations,
                "latency_ms_p50": round(latencies[len(latencies) // 2], 3),
                "latency_ms_p95": round(latencies[int(len(latencies) * 0.95) - 1], 3),
                "calibration": calibration,
                "results": results,
            }
            return report
        finally:
            index.close()
            if previous_root is None:
                os.environ.pop("AI_WIKI_ROOT", None)
            else:
                os.environ["AI_WIKI_ROOT"] = previous_root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_gate()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
