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


def _create_sample(runner, wiki_root):
    """샘플 문서 생성 후 article_id 반환. 품질 게이트를 통과할 수 있는 충분한 content."""
    content_file = wiki_root / "tmp_content.yaml"
    content_file.write_text(
        yaml.dump({
            "type": "technology",
            "what": "Python pytest는 파이썬 생태계에서 가장 널리 사용되는 테스트 프레임워크이다",
            "facts": [
                "pytest는 간결한 assert 문법을 지원한다",
                "fixture 시스템으로 테스트 의존성을 관리한다",
                "parametrize로 여러 입력을 테스트한다",
            ],
            "facts_v": {"level": "corroborated", "sources": ["https://docs.pytest.org"]},
            "use_cases": ["단위 테스트", "통합 테스트", "E2E 테스트"],
            "limitations": ["비동기 테스트에 추가 플러그인 필요"],
        }),
        encoding="utf-8",
    )
    result = _run(runner, [
        "create", "--title", "Test CLI Article", "--category", "technology/test",
        "--tags", "test,cli,pytest", "--confidence", "0.9",
        "--source", "https://docs.pytest.org", "--author", "test",
        "--content-file", str(content_file),
    ], wiki_root)
    data = json.loads(result.output)
    return data["article_id"]


def test_create_and_get(tmp_path):
    runner = CliRunner()
    wiki_root = tmp_path
    (wiki_root / "articles").mkdir()
    (wiki_root / "data").mkdir()
    (wiki_root / "logs").mkdir()

    article_id = _create_sample(runner, wiki_root)
    assert article_id

    result = _run(runner, ["get", article_id], wiki_root)
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["protocol_version"] == "1.0"
    assert data["data"]["document"]["title"] == "Test CLI Article"

    legacy = _run(runner, ["get", article_id, "--legacy"], wiki_root)
    assert json.loads(legacy.output)["article"]["title"] == "Test CLI Article"


def test_search(tmp_path):
    runner = CliRunner()
    wiki_root = tmp_path
    (wiki_root / "articles").mkdir()
    (wiki_root / "data").mkdir()
    (wiki_root / "logs").mkdir()

    _create_sample(runner, wiki_root)
    result = _run(runner, ["search", "test"], wiki_root)
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["count"] >= 1


def test_update(tmp_path):
    runner = CliRunner()
    wiki_root = tmp_path
    (wiki_root / "articles").mkdir()
    (wiki_root / "data").mkdir()
    (wiki_root / "logs").mkdir()

    article_id = _create_sample(runner, wiki_root)
    result = _run(runner, [
        "update", article_id, "--confidence", "0.95", "--tags", "updated,test",
    ], wiki_root)
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["version"] == 2


def test_delete(tmp_path):
    runner = CliRunner()
    wiki_root = tmp_path
    (wiki_root / "articles").mkdir()
    (wiki_root / "data").mkdir()
    (wiki_root / "logs").mkdir()

    article_id = _create_sample(runner, wiki_root)
    result = _run(runner, ["delete", article_id, "--confirm"], wiki_root)
    data = json.loads(result.output)
    assert data["status"] == "ok"

    result = _run(runner, ["get", article_id], wiki_root)
    assert result.exit_code == 1


def test_lint(tmp_path):
    runner = CliRunner()
    wiki_root = tmp_path
    (wiki_root / "articles").mkdir()
    (wiki_root / "data").mkdir()
    (wiki_root / "logs").mkdir()

    _create_sample(runner, wiki_root)
    result = _run(runner, ["lint"], wiki_root)
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert "orphan_articles" in data["issues"]
    assert "potential_conflicts" in data["issues"]


def test_reindex(tmp_path):
    runner = CliRunner()
    wiki_root = tmp_path
    (wiki_root / "articles").mkdir()
    (wiki_root / "data").mkdir()
    (wiki_root / "logs").mkdir()

    _create_sample(runner, wiki_root)
    result = _run(runner, ["reindex"], wiki_root)
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["article_count"] >= 1


def test_gaps(tmp_path):
    runner = CliRunner()
    wiki_root = tmp_path
    (wiki_root / "articles").mkdir()
    (wiki_root / "data").mkdir()
    (wiki_root / "logs").mkdir()

    _create_sample(runner, wiki_root)
    result = _run(runner, ["gaps"], wiki_root)
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert len(data["categories"]) >= 1


def test_lint_fix(tmp_path):
    runner = CliRunner()
    wiki_root = tmp_path
    (wiki_root / "articles").mkdir()
    (wiki_root / "data").mkdir()
    (wiki_root / "logs").mkdir()

    result = _run(runner, ["lint", "--fix"], wiki_root)
    data = json.loads(result.output)
    assert data["status"] == "ok"
