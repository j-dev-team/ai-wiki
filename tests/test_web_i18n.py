from __future__ import annotations

from ai_wiki.web import app


def test_web_ui_defaults_to_korean():
    app.config["TESTING"] = True
    with app.test_client() as client:
        response = client.get("/create")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "새 문서" in body
    assert "문서 유형" in body
    assert "한국어" in body


def test_web_ui_language_switch_persists_in_session():
    app.config["TESTING"] = True
    with app.test_client() as client:
        english = client.get("/create?lang=en")
        assert english.status_code == 200
        assert "New Article" in english.get_data(as_text=True)

        persisted = client.get("/create")
        assert persisted.status_code == 200
        body = persisted.get_data(as_text=True)
        assert "New Article" in body
        assert "Content Type" in body

        korean = client.get("/create?lang=ko")
        assert korean.status_code == 200
        assert "새 문서" in korean.get_data(as_text=True)
