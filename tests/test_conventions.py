"""_meta, _changelog, _v 컨벤션 호환성 테스트."""
from ai_wiki.models import Article
from ai_wiki.quality import validate, calculate_score


def _legacy_article():
    """_meta/_changelog/_v 없는 기존 문서."""
    return Article(
        id="legacy-test-abc", title="Legacy", category="technology/python",
        content={
            "type": "technology",
            "what": "기존 형식의 문서" * 10,
            "facts": ["f1", "f2", "f3"],
            "use_cases": ["u1", "u2"],
            "limitations": ["l1"],
        },
        tags=["python", "legacy"], confidence=0.8,
        sources=["https://example.com"], related=[], author="test",
    )


def test_validate_no_meta():
    """_meta 없는 문서 → validate 크래시 없음."""
    article = _legacy_article()
    assert "_meta" not in article.content
    report = validate(article)
    assert report is not None
    assert isinstance(report.passed, bool)


def test_validate_no_changelog():
    """_changelog 없는 문서 → 정상 동작."""
    article = _legacy_article()
    report = validate(article)
    changelog_errors = [e for e in report.errors if "changelog" in e.code.lower()]
    assert len(changelog_errors) == 0


def test_score_no_v():
    """_v 없어도 점수 계산 정상."""
    score = calculate_score(_legacy_article())
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_meta_roundtrip():
    """_meta가 content에 있을 때 to_dict→from_yaml 보존."""
    article = _legacy_article()
    article.set_meta({"maturity": "draft", "completeness": 0.65})
    d = article.to_dict()
    restored = Article.from_yaml(d)
    assert restored.content["_meta"]["maturity"] == "draft"
    assert restored.content["_meta"]["completeness"] == 0.65


def test_meta_excluded_from_search_text():
    """_meta는 content_as_text()에서 제외."""
    article = _legacy_article()
    article.set_meta({"maturity": "draft", "quality_score": 0.9})
    text = article.content_as_text()
    assert "quality_score" not in text
    assert "기존 형식" in text


def test_changelog_excluded_from_search_text():
    """_changelog는 content_as_text()에서 제외."""
    article = _legacy_article()
    article.append_changelog("created", ["type", "what"], "test")
    text = article.content_as_text()
    assert "_changelog" not in text


def test_content_keys_excludes_reserved():
    """content_keys()는 _meta, _changelog 제외."""
    article = _legacy_article()
    article.set_meta({"maturity": "draft"})
    article.append_changelog("created", ["type"], "test")
    keys = article.content_keys()
    assert "_meta" not in keys
    assert "_changelog" not in keys
    assert "type" in keys
    assert "what" in keys


def test_append_changelog():
    """append_changelog가 content._changelog에 항목 추가."""
    article = _legacy_article()
    article.append_changelog("created", ["type", "what"], "초기 생성")
    article.append_changelog("enriched", ["facts"], "팩트 보강")
    cl = article.content.get("_changelog", [])
    assert len(cl) == 2
    assert cl[0]["action"] == "created"
    assert cl[1]["action"] == "enriched"
