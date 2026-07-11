from __future__ import annotations

import os
from pathlib import Path

import pytest

from ai_wiki.models import Article
from ai_wiki.index import WikiIndex


@pytest.fixture
def wiki_root(tmp_path):
    """격리된 위키 루트 디렉토리."""
    (tmp_path / "articles").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    yield tmp_path
    os.environ.pop("AI_WIKI_ROOT", None)


@pytest.fixture
def wiki_index(wiki_root):
    """격리된 WikiIndex."""
    idx = WikiIndex(wiki_root / "data" / "wiki.db")
    yield idx
    idx.close()


@pytest.fixture
def sample_article():
    """테스트용 Article."""
    return Article(
        id="test-sample-article-abc123",
        title="Sample Article",
        category="technology/python",
        content={
            "type": "technology",
            "language": "python",
            "what": "A sample test article",
            "facts": ["fact 1", "fact 2"],
        },
        tags=["python", "test"],
        confidence=0.85,
        sources=["https://example.com"],
        related=[],
        author="test",
    )


@pytest.fixture
def sample_article_b():
    """두 번째 테스트용 Article."""
    return Article(
        id="test-another-article-def456",
        title="Another Article",
        category="technology/python",
        content={
            "type": "technology",
            "language": "python",
            "what": "Another test article",
            "facts": ["fact A"],
        },
        tags=["python", "testing"],
        confidence=0.9,
        sources=["https://example.com/2"],
        related=[],
        author="test",
    )
