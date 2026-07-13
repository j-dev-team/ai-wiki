"""quality.py 단위 테스트."""
from ai_wiki.models import Article
from ai_wiki.quality import validate, calculate_score


def _make(**overrides):
    defaults = dict(
        id="test-q-abc", title="Test", category="technology/test",
        content={
            "type": "technology",
            "what": "Python testing framework for quality assurance and validation",
            "facts": ["fact 1", "fact 2", "fact 3"],
            "facts_v": {"level": "corroborated", "sources": ["https://example.com"]},
            "use_cases": ["unit test", "integration test"],
            "limitations": ["async needs plugin"],
        },
        tags=["python", "test", "quality"],
        confidence=0.85,
        sources=["https://example.com"],
        related=[], author="test",
    )
    defaults.update(overrides)
    return Article(**defaults)


def test_valid_passes():
    report = validate(_make())
    assert report.passed is True
    assert len(report.errors) == 0


def test_min_content_keys_error():
    report = validate(_make(content={"type": "technology"}))
    assert report.passed is False
    assert any(e.code == "MIN_CONTENT_KEYS" for e in report.errors)


def test_min_content_chars_error():
    report = validate(_make(content={"type": "technology", "what": "x", "a": 1, "b": 2, "c": 3}))
    assert report.passed is False
    assert any(e.code == "MIN_CONTENT_CHARS" for e in report.errors)


def test_no_sources_error():
    report = validate(_make(sources=[]))
    assert report.passed is False
    assert any(e.code == "MIN_SOURCES" for e in report.errors)


def test_missing_type_error():
    report = validate(_make(content={"what": "no type", "a": 1, "b": 2, "c": 3, "d": 4}))
    assert report.passed is False
    assert any(e.code == "MISSING_TYPE" for e in report.errors)


def test_type_required_keys_error():
    report = validate(_make(content={
        "type": "event", "what": "test event" * 20,
        "a": 1, "b": 2, "c": 3,
    }))
    assert report.passed is False
    assert any(e.code == "TYPE_REQUIRED_KEYS" for e in report.errors)


def test_min_tags_warning():
    report = validate(_make(tags=["only"]))
    assert report.passed is True  # warning은 통과에 영향 없음
    assert any(w.code == "MIN_TAGS" for w in report.warnings)


def test_unexplained_entity_anonymization_warning():
    content = dict(_make().content)
    content["participants"] = ["A\uC528", "\uC775\uBA85 \uACE0\uAC1D"]

    report = validate(_make(content=content))

    assert any(w.code == "UNEXPLAINED_ENTITY_ANONYMIZATION" for w in report.warnings)


def test_explained_entity_anonymization_preserves_quality():
    content = dict(_make().content)
    content["participants"] = ["A\uC528"]
    content["identity_handling"] = {
        "reason": "The public source withheld the participant's name.",
        "scope": "participant name only",
        "source_disclosure_status": "name withheld by source",
        "preserved_attributes": ["role", "organization", "nationality"],
    }

    report = validate(_make(content=content))

    assert not any(w.code == "UNEXPLAINED_ENTITY_ANONYMIZATION" for w in report.warnings)


def test_multiple_errors():
    report = validate(_make(content={"x": "y"}, sources=[], tags=[]))
    assert report.passed is False
    assert len(report.errors) >= 3


def test_maturity_stub():
    report = validate(_make(content={"type": "technology", "what": "x"},
                            sources=[], tags=[], confidence=0.3))
    assert report.maturity == "stub"


def test_maturity_draft():
    report = validate(_make(confidence=0.5))
    assert report.maturity in ("draft", "review")


def test_maturity_mature():
    report = validate(_make(
        content={
            "type": "technology",
            "what": "detailed description " * 30,
            "facts": ["f1", "f2", "f3"],
            "use_cases": ["u1", "u2"],
            "limitations": ["l1"],
            "workarounds": ["w1"],
            "best_practices": ["b1"],
            "when_to_use": ["wtu1"],
            "when_not_to_use": ["wntu1"],
            "language": "python",
        },
        sources=["s1", "s2", "s3"],
        related=["r1", "r2", "r3"],
        confidence=0.95,
    ))
    assert report.maturity == "mature"


def test_score_range():
    for conf in [0.0, 0.5, 1.0]:
        score = calculate_score(_make(confidence=conf))
        assert 0.0 <= score <= 1.0


def test_score_increases_with_quality():
    poor = calculate_score(_make(content={"type": "t", "what": "x"}, sources=[], tags=[]))
    rich = calculate_score(_make(
        sources=["s1", "s2", "s3"], tags=["a", "b", "c", "d", "e"],
        related=["r1", "r2"], confidence=0.95,
    ))
    assert rich > poor


def test_to_dict():
    report = validate(_make())
    d = report.to_dict()
    assert "maturity" in d
    assert "quality_score" in d
    assert "violations" in d
    assert isinstance(d["violations"], list)
