from ai_wiki.storage import save_article


def test_upsert_and_search(wiki_root, wiki_index, sample_article):
    save_article(sample_article)
    wiki_index.upsert(sample_article, "articles/test.yaml")
    results = wiki_index.search("sample")
    assert len(results) >= 1
    assert results[0]["id"] == sample_article.id


def test_search_with_category(wiki_root, wiki_index, sample_article, sample_article_b):
    sample_article_b.category = "history/korea"
    save_article(sample_article)
    save_article(sample_article_b)
    wiki_index.upsert(sample_article, "a.yaml")
    wiki_index.upsert(sample_article_b, "b.yaml")

    results = wiki_index.search("article", category="technology/python")
    ids = [r["id"] for r in results]
    assert sample_article.id in ids
    assert sample_article_b.id not in ids


def test_remove(wiki_root, wiki_index, sample_article):
    wiki_index.upsert(sample_article, "a.yaml")
    wiki_index.remove(sample_article.id)
    results = wiki_index.search("sample")
    assert len(results) == 0


def test_find_similar_titles(wiki_root, wiki_index, sample_article):
    wiki_index.upsert(sample_article, "a.yaml")
    similar = wiki_index.find_similar_titles("Sample Article Guide")
    assert len(similar) >= 1
    assert similar[0]["similarity"] >= 0.6


def test_get_stale(wiki_root, wiki_index, sample_article):
    wiki_index.upsert(sample_article, "a.yaml")
    # 새로 생성된 문서는 stale이 아님
    stale = wiki_index.get_stale(days=1)
    assert len(stale) == 0


def test_rebuild(wiki_root, wiki_index, sample_article, sample_article_b):
    wiki_index.rebuild([
        (sample_article, "a.yaml"),
        (sample_article_b, "b.yaml"),
    ])
    assert wiki_index.count() == 2


def test_rebuild_recovers_corrupt_document_fts(wiki_root, wiki_index, sample_article):
    wiki_index.upsert(sample_article, "a.yaml")
    wiki_index.conn.execute(
        "UPDATE articles_fts_data SET block = zeroblob(length(block)) WHERE id = "
        "(SELECT max(id) FROM articles_fts_data)"
    )
    wiki_index.conn.commit()

    assert wiki_index.fts_integrity()["ready"] is False

    wiki_index.rebuild([(sample_article, "a.yaml")])

    assert wiki_index.fts_integrity()["ready"] is True
    assert wiki_index.search("sample")[0]["id"] == sample_article.id


def test_orphans(wiki_root, wiki_index, sample_article):
    wiki_index.upsert(sample_article, "a.yaml")
    orphans = wiki_index.get_orphans()
    assert len(orphans) == 1
    assert orphans[0]["id"] == sample_article.id


def test_one_way_links(wiki_root, wiki_index, sample_article, sample_article_b):
    sample_article.related = [sample_article_b.id]
    wiki_index.upsert(sample_article, "a.yaml")
    wiki_index.upsert(sample_article_b, "b.yaml")
    one_way = wiki_index.get_one_way_links()
    assert len(one_way) == 1
    assert one_way[0]["from"] == sample_article.id


def test_relations_table(wiki_root, wiki_index, sample_article, sample_article_b):
    sample_article.related = [sample_article_b.id]
    sample_article_b.related = [sample_article.id]
    wiki_index.upsert(sample_article, "a.yaml")
    wiki_index.upsert(sample_article_b, "b.yaml")
    one_way = wiki_index.get_one_way_links()
    assert len(one_way) == 0  # 양방향이므로 단방향 없음


def test_sources_table(wiki_root, wiki_index, sample_article):
    wiki_index.upsert(sample_article, "a.yaml")
    no_src = wiki_index.get_no_sources()
    assert len(no_src) == 0  # sample_article에 source 있음

    sample_article_b_no_src = sample_article
    sample_article_b_no_src.id = "no-src-test"
    sample_article_b_no_src.sources = []
    wiki_index.upsert(sample_article_b_no_src, "b.yaml")
    no_src = wiki_index.get_no_sources()
    assert len(no_src) == 1


def test_find_related_candidates(wiki_root, wiki_index, sample_article, sample_article_b):
    save_article(sample_article)
    save_article(sample_article_b)
    wiki_index.upsert(sample_article, "a.yaml")
    wiki_index.upsert(sample_article_b, "b.yaml")
    candidates = wiki_index.find_related_candidates(sample_article)
    assert len(candidates) >= 1
    assert candidates[0]["id"] == sample_article_b.id
