from ai_wiki.models import Article, _flatten_dict


def test_article_creation(sample_article):
    assert sample_article.id == "test-sample-article-abc123"
    assert sample_article.confidence == 0.85
    assert sample_article.version == 1


def test_to_dict_roundtrip(sample_article):
    d = sample_article.to_dict()
    restored = Article.from_yaml(d)
    assert restored.id == sample_article.id
    assert restored.title == sample_article.title
    assert restored.content == sample_article.content
    assert restored.tags == sample_article.tags


def test_meta_dict_excludes_content(sample_article):
    meta = sample_article.meta_dict()
    assert "content" not in meta
    assert "id" in meta
    assert "title" in meta


def test_from_yaml_string_tags():
    data = {"id": "x", "title": "t", "category": "c", "content": {},
            "tags": "a, b, c"}
    article = Article.from_yaml(data)
    assert article.tags == ["a", "b", "c"]


def test_from_yaml_string_sources():
    data = {"id": "x", "title": "t", "category": "c", "content": {},
            "sources": "https://example.com"}
    article = Article.from_yaml(data)
    assert article.sources == ["https://example.com"]


def test_from_yaml_string_content():
    data = {"id": "x", "title": "t", "category": "c", "content": "plain text"}
    article = Article.from_yaml(data)
    assert article.content == {"text": "plain text"}


def test_content_as_text(sample_article):
    text = sample_article.content_as_text()
    assert "python" in text
    assert "fact 1" in text


def test_parse_dt_formats():
    dt = Article._parse_dt("2026-04-08T10:00:00Z")
    assert dt.year == 2026
    dt2 = Article._parse_dt("2026-04-08")
    assert dt2.month == 4


def test_flatten_dict():
    d = {"a": "1", "b": ["x", "y"], "c": {"d": "2"}}
    text = _flatten_dict(d)
    assert "a: 1" in text
    assert "x" in text
    assert "d: 2" in text
