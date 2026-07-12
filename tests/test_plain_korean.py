from ai_wiki.i18n import display_label, display_value, translate


def test_ai_work_pages_use_plain_korean_labels():
    assert translate("ko", "nav.missions") == "작업 관리"
    assert translate("ko", "nav.calibration") == "검색 품질"
    assert display_label("work_run", "ko") == "작업 실행 기록"
    assert display_label("acceptance_criteria", "ko") == "완료 기준"
    assert display_value("AI-first context citation", "ko") == "AI 우선 답변용 자료 인용 근거"
    assert display_value("in_review", "ko") == "검토 중"
