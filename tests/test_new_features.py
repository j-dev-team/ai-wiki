"""신규 기능 테스트: maintain, vsearch/vindex, git_auto_commit."""
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from ai_wiki.cli import cli
from ai_wiki.models import Article
from ai_wiki.storage import git_auto_commit, save_article


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


def _init_wiki(tmp_path):
    """위키 디렉토리 초기화."""
    (tmp_path / "articles").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path


# ── maintain 명령 테스트 ──────────────────────────


def test_maintain_basic(tmp_path):
    """maintain 명령이 정상적으로 실행되는지 확인."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    _create_sample(runner, wiki_root)
    result = _run(runner, ["maintain"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    assert "lint" in data
    assert "quality" in data
    assert "todo" in data
    assert data["total_articles"] >= 1


def test_maintain_empty_wiki(tmp_path):
    """문서가 없는 위키에서 maintain 실행."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    result = _run(runner, ["maintain"], wiki_root)
    data = json.loads(result.output)

    assert data["status"] == "ok"
    assert data["total_articles"] == 0
    assert data["lint"]["fixes_applied"] == 0


def test_maintain_lint_section(tmp_path):
    """maintain 결과에 lint 섹션이 올바르게 포함되는지 확인."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    _create_sample(runner, wiki_root)
    result = _run(runner, ["maintain"], wiki_root)
    data = json.loads(result.output)

    lint = data["lint"]
    assert "orphans" in lint
    assert "broken_refs" in lint
    assert "one_way_links" in lint
    assert "fixes_applied" in lint


def test_maintain_quality_section(tmp_path):
    """maintain 결과에 quality 섹션이 올바르게 포함되는지 확인."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    _create_sample(runner, wiki_root)
    result = _run(runner, ["maintain"], wiki_root)
    data = json.loads(result.output)

    quality = data["quality"]
    assert "maturity_summary" in quality
    assert "low_quality_count" in quality


# ── vsearch/vindex 명령 테스트 ────────────────────


def test_vsearch_import_error(tmp_path):
    """VectorIndex import 실패 시 에러를 정상적으로 반환하는지 확인."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    # VectorIndex 생성 시 예외를 발생시켜 import 실패 시뮬레이션
    with patch("ai_wiki.vector.VectorIndex", side_effect=ImportError("no sqlite_vec")):
        result = _run(runner, ["vsearch", "test query"], wiki_root)

    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["status"] == "error"


def test_vsearch_with_mocked_vector_index(tmp_path):
    """VectorIndex를 모킹하여 vsearch가 올바르게 동작하는지 확인."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    mock_vidx_instance = MagicMock()
    mock_vidx_instance.search.return_value = [
        {"id": "test-id", "title": "Mock Result", "category": "test",
         "distance": 0.1, "similarity": 0.9},
    ]
    mock_vidx_instance.close.return_value = None

    with patch("ai_wiki.vector.VectorIndex", return_value=mock_vidx_instance):
        result = _run(runner, ["vsearch", "test query"], wiki_root)

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["count"] == 1
    assert data["results"][0]["id"] == "test-id"


def test_vindex_with_mocked_vector_index(tmp_path):
    """VectorIndex를 모킹하여 vindex가 올바르게 동작하는지 확인."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    mock_vidx_instance = MagicMock()
    mock_vidx_instance.rebuild.return_value = 0
    mock_vidx_instance.close.return_value = None

    with patch("ai_wiki.vector.VectorIndex", return_value=mock_vidx_instance):
        result = _run(runner, ["vindex"], wiki_root)

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["action"] == "vector_reindexed"


def test_vindex_import_error(tmp_path):
    """VectorIndex import 실패 시 에러를 정상 반환."""
    runner = CliRunner()
    wiki_root = _init_wiki(tmp_path)

    with patch("ai_wiki.vector.VectorIndex", side_effect=ImportError("no sqlite_vec")):
        result = _run(runner, ["vindex"], wiki_root)

    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["status"] == "error"


# ── git_auto_commit 테스트 ────────────────────────


def test_git_auto_commit_no_git_dir(tmp_path):
    """git repo가 아닌 디렉토리에서 False 반환."""
    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    (tmp_path / "articles").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)

    result = git_auto_commit("test_action", "test-id", "Test Title")
    assert result is False
    os.environ.pop("AI_WIKI_ROOT", None)


def test_git_auto_commit_with_git_dir(tmp_path):
    """git repo에서 git add + commit 실행 확인."""
    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    (tmp_path / "articles").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)

    # git init
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), capture_output=True,
    )

    # 문서 파일 생성
    (tmp_path / "articles" / "test.yaml").write_text("id: test\n", encoding="utf-8")
    (tmp_path / "data" / "index.yaml").write_text("{}\n", encoding="utf-8")

    result = git_auto_commit("create", "test-id", "Test Title")
    assert result is True

    # 커밋 로그 확인
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=str(tmp_path), capture_output=True, text=True,
    )
    assert "wiki: create" in log.stdout

    os.environ.pop("AI_WIKI_ROOT", None)


def test_git_auto_commit_message_format(tmp_path):
    """커밋 메시지가 올바른 형식인지 확인."""
    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    (tmp_path / "articles").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)

    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), capture_output=True,
    )

    (tmp_path / "articles" / "test.yaml").write_text("id: test\n", encoding="utf-8")
    (tmp_path / "data" / "index.yaml").write_text("{}\n", encoding="utf-8")

    git_auto_commit("update", "my-article-id", "My Title")

    log = subprocess.run(
        ["git", "log", "--format=%s", "-1"],
        cwd=str(tmp_path), capture_output=True, text=True,
    )
    msg = log.stdout.strip()
    assert "wiki: update" in msg
    assert "'My Title'" in msg
    assert "(my-article-id)" in msg

    os.environ.pop("AI_WIKI_ROOT", None)


def test_git_auto_commit_no_title(tmp_path):
    """title 없이 호출해도 정상 동작."""
    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    (tmp_path / "articles").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)

    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), capture_output=True,
    )

    (tmp_path / "articles" / "test.yaml").write_text("id: test\n", encoding="utf-8")
    (tmp_path / "data" / "index.yaml").write_text("{}\n", encoding="utf-8")

    result = git_auto_commit("delete", "some-id")
    assert result is True

    os.environ.pop("AI_WIKI_ROOT", None)
