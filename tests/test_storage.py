from pathlib import Path

from ai_wiki.storage import (
    save_article, load_article, load_article_with_path,
    delete_article_file, list_all_articles, save_source_file,
    list_source_files, delete_source_files,
)


def test_save_and_load(wiki_root, sample_article):
    path = save_article(sample_article)
    assert path.exists()
    assert path.suffix == ".yaml"

    loaded = load_article(sample_article.id)
    assert loaded is not None
    assert loaded.title == sample_article.title
    assert loaded.content == sample_article.content


def test_load_nonexistent(wiki_root):
    assert load_article("nonexistent-id") is None


def test_load_with_path(wiki_root, sample_article):
    save_article(sample_article)
    article, path = load_article_with_path(sample_article.id)
    assert article is not None
    assert path is not None
    assert path.exists()


def test_delete_article(wiki_root, sample_article):
    save_article(sample_article)
    assert delete_article_file(sample_article.id) is True
    assert load_article(sample_article.id) is None


def test_list_all(wiki_root, sample_article, sample_article_b):
    save_article(sample_article)
    save_article(sample_article_b)
    articles = list_all_articles()
    assert len(articles) == 2
    ids = {a.id for a in articles}
    assert sample_article.id in ids
    assert sample_article_b.id in ids


def test_category_directory_creation(wiki_root, sample_article):
    sample_article.category = "deep/nested/category"
    path = save_article(sample_article)
    assert "deep" in str(path)
    assert path.exists()


def test_source_file_lifecycle(wiki_root, sample_article, tmp_path):
    # 원본 파일 생성
    src_file = tmp_path / "test_doc.txt"
    src_file.write_text("test content")

    # 저장
    dest = save_source_file(sample_article.id, src_file, "test desc")
    assert dest.exists()

    # 목록 조회
    files = list_source_files(sample_article.id)
    assert len(files) == 1
    assert files[0]["filename"] == "test_doc.txt"

    # 삭제
    assert delete_source_files(sample_article.id) is True
    assert list_source_files(sample_article.id) == []
