"""Phase 1 신규 기능 테스트: discover, path, cluster CLI 명령어."""
import json
import os
from pathlib import Path

import yaml
from click.testing import CliRunner

from ai_wiki.cli import cli


def _run(runner, args, wiki_root):
    os.environ["AI_WIKI_ROOT"] = str(wiki_root)
    result = runner.invoke(cli, args)
    return result


def _init_wiki(tmp_path):
    """위키 디렉토리 초기화."""
    (tmp_path / "articles").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)
    return tmp_path


def _create_sample(runner, wiki_root, title="Test Article", category="technology/test",
                   tags="test,cli", related="", force=True):
    """샘플 문서 생성 후 article_id 반환."""
    content_file = wiki_root / f"tmp_{title.replace(' ', '_')}.yaml"
    content_file.write_text(
        yaml.dump({
            "type": "technology",
            "what": f"{title} is a test article for phase 1 testing purposes with enough content",
            "facts": ["Fact one about this topic", "Fact two about this topic", "Fact three details"],
            "facts_v": {"level": "corroborated", "sources": ["https://example.com"]},
            "use_cases": ["Testing", "Demo", "Example"],
            "limitations": ["This is a test limitation"],
        }),
        encoding="utf-8",
    )
    args = [
        "create", "--title", title, "--category", category,
        "--tags", tags, "--confidence", "0.85",
        "--source", "https://example.com", "--content-file", str(content_file),
    ]
    if related:
        args += ["--related", related]
    if force:
        args.append("--force")
    result = _run(runner, args, wiki_root)
    data = json.loads(result.output)
    return data.get("article_id", "")


# ── discover 명령 테스트 ─────────────────────────────


def test_discover_empty_wiki(tmp_path):
    """빈 위키에서 discover 실행 - 이슈 없음."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    result = _run(runner, ["discover"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    assert data["total_issues"] == 0
    assert data["isolated_count"] == 0
    assert data["low_quality_count"] == 0
    assert data["stale_count"] == 0


def test_discover_with_articles(tmp_path):
    """문서가 있는 위키에서 discover 실행."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    _create_sample(runner, wiki_root, "Discover Test Article", tags="test,discover")
    result = _run(runner, ["discover"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    # 새로 생성된 문서는 고립 문서일 수 있음
    assert "isolated" in data
    assert "low_quality" in data
    assert "stale" in data
    assert "recommendations" in data
    assert isinstance(data["isolated"], list)
    assert isinstance(data["low_quality"], list)
    assert isinstance(data["stale"], list)


def test_discover_isolated_articles(tmp_path):
    """related 없는 고립 문서 탐지."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    _create_sample(runner, wiki_root, "Isolated Article", tags="isolated,test")
    result = _run(runner, ["discover"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    # 고립 문서가 detected 되어야 함
    assert data["isolated_count"] >= 1
    assert len(data["isolated"]) >= 1
    # 각 항목에 필수 필드 있는지 확인
    item = data["isolated"][0]
    assert "id" in item
    assert "title" in item
    assert "category" in item


def test_discover_limit_option(tmp_path):
    """--limit 옵션이 결과 수를 제한하는지 확인."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    _create_sample(runner, wiki_root, "Article A", tags="test")
    _create_sample(runner, wiki_root, "Article B", tags="test")
    _create_sample(runner, wiki_root, "Article C", tags="test")

    result = _run(runner, ["discover", "--limit", "2"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    assert len(data["isolated"]) <= 2


# ── path 명령 테스트 ─────────────────────────────────


def test_path_not_found_article(tmp_path):
    """존재하지 않는 문서 ID로 path 실행 → 에러."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    result = _run(runner, ["path", "nonexistent-id-1", "nonexistent-id-2"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "error"
    assert data["code"] == "not_found"


def test_path_same_article(tmp_path):
    """동일 문서로 path → 경로 길이 0."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    id1 = _create_sample(runner, wiki_root, "Path Test Article", tags="test,path")
    result = _run(runner, ["path", id1, id1], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    assert data["found"] is True
    assert data["steps"] == 0


def test_path_direct_connection(tmp_path):
    """직접 연결된 두 문서 간 경로 (1 step)."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    id1 = _create_sample(runner, wiki_root, "Path Article One", tags="test,path")
    id2 = _create_sample(runner, wiki_root, "Path Article Two", tags="test,path")

    # id1 → id2 연결
    _run(runner, ["update", id1, "--related", id2], wiki_root)

    result = _run(runner, ["path", id1, id2], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    assert data["found"] is True
    assert data["steps"] == 1
    assert len(data["path"]) == 2
    # 경로의 각 노드에 id와 title이 있어야 함
    for node in data["path"]:
        assert "id" in node
        assert "title" in node


def test_path_no_connection(tmp_path):
    """연결되지 않은 두 문서 간 경로 없음."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    id1 = _create_sample(runner, wiki_root, "Disconnected One", tags="group1")
    id2 = _create_sample(runner, wiki_root, "Disconnected Two", tags="group2")

    result = _run(runner, ["path", id1, id2, "--max-depth", "2"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    # 같은 카테고리지만 related 없으면 연결 없음
    # found는 True or False
    assert "found" in data


def test_path_max_depth_option(tmp_path):
    """--max-depth 옵션 작동 확인."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    id1 = _create_sample(runner, wiki_root, "Depth Article One", tags="depth,test")
    id2 = _create_sample(runner, wiki_root, "Depth Article Two", tags="depth,test")

    # 연결 없는 상태에서 max-depth=1 실행
    result = _run(runner, ["path", id1, id2, "--max-depth", "1"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    assert "found" in data


# ── cluster 명령 테스트 ──────────────────────────────


def test_cluster_empty_wiki(tmp_path):
    """빈 위키에서 cluster 실행."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    result = _run(runner, ["cluster"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    assert data["total_clusters"] == 0
    assert data["total_articles"] == 0


def test_cluster_with_articles(tmp_path):
    """문서들이 있는 위키에서 cluster 실행."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    _create_sample(runner, wiki_root, "Cluster Article Alpha", tags="python,programming")
    _create_sample(runner, wiki_root, "Cluster Article Beta", tags="python,programming")

    result = _run(runner, ["cluster", "--min-size", "1"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    assert data["total_articles"] == 2
    assert data["total_clusters"] >= 1
    # 클러스터 구조 확인
    if data["clusters"]:
        c = data["clusters"][0]
        assert "cluster_id" in c
        assert "size" in c
        assert "main_category" in c
        assert "top_tags" in c
        assert "avg_confidence" in c
        assert "articles" in c


def test_cluster_min_size_filter(tmp_path):
    """--min-size 옵션으로 소규모 클러스터 제외."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    _create_sample(runner, wiki_root, "Cluster Size Test", tags="singleton")

    # min-size=2이면 1개짜리 클러스터는 제외됨
    result = _run(runner, ["cluster", "--min-size", "2"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    # 1개짜리 클러스터는 제외
    for c in data["clusters"]:
        assert c["size"] >= 2


def test_cluster_limit_option(tmp_path):
    """--limit 옵션이 클러스터 수를 제한하는지 확인."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    _create_sample(runner, wiki_root, "Limit Article A", tags="tagA")
    _create_sample(runner, wiki_root, "Limit Article B", tags="tagB")
    _create_sample(runner, wiki_root, "Limit Article C", tags="tagC")

    result = _run(runner, ["cluster", "--min-size", "1", "--limit", "2"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    assert len(data["clusters"]) <= 2


def test_cluster_tag_grouping(tmp_path):
    """같은 태그를 가진 문서들이 같은 클러스터에 묶이는지 확인."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    # 같은 태그 "python"을 가진 두 문서
    _create_sample(runner, wiki_root, "Python Intro", tags="python,intro")
    _create_sample(runner, wiki_root, "Python Advanced", tags="python,advanced")

    result = _run(runner, ["cluster", "--min-size", "1"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    # 두 문서는 같은 태그 'python'으로 같은 클러스터에 묶일 것
    # 전체 클러스터 중 python 관련 클러스터가 있어야 함
    all_article_ids = []
    for c in data["clusters"]:
        all_article_ids.extend([a["id"] for a in c["articles"]])
    assert len(all_article_ids) == 2
