from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

import yaml
from ai_wiki.yaml_loader import load_yaml_text
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, g, abort

from ai_wiki.index import WikiIndex
from ai_wiki.i18n import LANG_LABELS, SUPPORTED_LANGS, default_lang, display_label, display_value, translate
from ai_wiki.models import Article
from ai_wiki.catalog import rebuild_catalog
from ai_wiki.schemas import TYPE_SCHEMAS, build_content_template, register_custom_types
from ai_wiki.storage import (
    get_wiki_root,
    list_all_articles,
    load_article,
    load_article_with_path,
    delete_article_file,
    delete_source_files,
    atomic_save,
    atomic_update,
)
from ai_wiki.utils import generate_id
from ai_wiki.runtime import get_runtime

_RUNTIME = get_runtime()

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
app.secret_key = os.environ.get("AI_WIKI_SECRET", os.urandom(24).hex())
_TEAM_MODE = os.environ.get("AI_WIKI_TEAM_MODE") == "1"
if _TEAM_MODE:
    if "AI_WIKI_SECRET" not in os.environ:
        raise RuntimeError("team mode requires a persistent AI_WIKI_SECRET")
    app.config.update(
        SESSION_COOKIE_SECURE=os.environ.get("AI_WIKI_COOKIE_SECURE", "1") == "1",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
_team_security = None


def get_team_security():
    global _team_security
    if _team_security is None:
        from ai_wiki.team_security import TeamSecurity
        _team_security = TeamSecurity(get_wiki_root())
    return _team_security

# ── 위키 이름 설정 ──────────────────────────────────
def _load_wiki_name() -> str:
    try:
        from ai_wiki.storage import get_wiki_root
        config_path = get_wiki_root() / _RUNTIME.config_filename
        if config_path.exists():
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            return cfg.get("name", _RUNTIME.display_name)
    except Exception:
        pass
    return os.environ.get("AI_WIKI_NAME", _RUNTIME.display_name)

app.jinja_env.globals["wiki_name"] = _load_wiki_name()


@app.before_request
def _select_language() -> None:
    requested = request.args.get("lang", "").lower()
    if requested in SUPPORTED_LANGS:
        session["ai_wiki_lang"] = requested
    elif session.get("ai_wiki_lang") not in SUPPORTED_LANGS:
        session["ai_wiki_lang"] = default_lang()


@app.before_request
def _team_authentication():
    if not _TEAM_MODE or request.endpoint in {"team_login", "static"}:
        return None
    security = get_team_security()
    key = f"{request.remote_addr}:{request.endpoint}"
    if not security.rate_limit(key):
        abort(429)
    bearer = request.headers.get("Authorization", "")
    principal = security.verify_token(bearer[7:]) if bearer.startswith("Bearer ") else None
    if principal is None and session.get("team_user"):
        principal = {"id": session["team_user"], "roles": session.get("team_roles", [])}
    if principal is None:
        if request.path.startswith("/api/"):
            return jsonify({"status": "error", "error": {"code": "authentication_required"}}), 401
        return redirect(url_for("team_login", next=request.full_path))
    g.principal = principal
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        roles = set(principal.get("roles", []))
        required = {"owner"} if request.endpoint in {"delete_article", "api_delete_article"} else {
            "owner", "reviewer", "agent",
        }
        if not roles.intersection(required):
            security.audit(principal["id"], "web_mutation", request.path, False, "role denied")
            abort(403)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not bearer.startswith("Bearer "):
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if not supplied or not secrets.compare_digest(supplied, session.get("csrf_token", "")):
            abort(403)
    return None


@app.route("/team/login", methods=["GET", "POST"])
def team_login():
    if not _TEAM_MODE:
        abort(404)
    error = None
    if request.method == "POST":
        security = get_team_security()
        if not security.rate_limit(f"login:{request.remote_addr}", limit=10, window_seconds=300):
            abort(429)
        principal = security.verify_password(request.form.get("user_id", ""), request.form.get("password", ""))
        if principal:
            session.clear()
            session["team_user"] = principal["id"]
            session["team_roles"] = principal["roles"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(request.args.get("next") or url_for("home"))
        error = "Invalid credentials"
    return render_template("team_login.html", error=error)


def _current_lang() -> str:
    lang = session.get("ai_wiki_lang", default_lang())
    return lang if lang in SUPPORTED_LANGS else default_lang()


@app.context_processor
def _inject_i18n():
    lang = _current_lang()
    return {
        "lang": lang,
        "supported_langs": SUPPORTED_LANGS,
        "lang_labels": LANG_LABELS,
        "t": lambda key, **kwargs: translate(lang, key, **kwargs),
        "label": lambda value: display_label(value, lang),
        "easy": lambda value: display_value(value, lang),
        "team_mode": _TEAM_MODE,
        "csrf_token": session.get("csrf_token", ""),
    }


# ── #6: WikiIndex 싱글톤 ──────────────────────────

_wiki_index: WikiIndex | None = None


def get_index() -> WikiIndex:
    global _wiki_index
    register_custom_types(get_wiki_root() / _RUNTIME.config_filename, reset=True)
    if _wiki_index is None:
        _wiki_index = WikiIndex()
    return _wiki_index


def _vector_upsert(article: Article) -> None:
    """Keep vector search synchronized with document writes."""
    vidx = None
    try:
        from ai_wiki.vector import VectorIndex
        vidx = VectorIndex()
        vidx.upsert(article)
    finally:
        if vidx is not None:
            vidx.close()


def _vector_remove(article_id: str) -> None:
    vidx = None
    try:
        from ai_wiki.vector import VectorIndex
        vidx = VectorIndex()
        vidx.remove(article_id)
    finally:
        if vidx is not None:
            vidx.close()


# ── Jinja Filters ──────────────────────────────────

@app.template_filter("yaml_dump")
def yaml_dump_filter(data):
    if isinstance(data, (dict, list)):
        return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return str(data)


@app.template_filter("is_dict")
def is_dict_filter(value):
    return isinstance(value, dict)


@app.template_filter("is_list")
def is_list_filter(value):
    return isinstance(value, list)


# ── Pages ──────────────────────────────────────────

@app.route("/")
def home():
    idx = get_index()
    total = idx.count()
    recent = idx.get_recent_articles(limit=20)
    cat_stats = idx.get_category_stats()
    categories = {s["category"]: s["count"] for s in cat_stats}
    return render_template("home.html", total=total, recent=recent, categories=categories)


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip() or None
    results = []
    if q:
        idx = get_index()
        results = idx.search(q, category=category, limit=50)
    return render_template("search.html", q=q, category=category, results=results)


@app.route("/article/<path:article_id>")
def view_article(article_id: str):
    article = load_article(article_id)
    if not article:
        flash(f"문서를 찾을 수 없습니다: {article_id}", "error")
        return redirect(url_for("home"))
    # related ID → 제목 매핑 (N+1 제거: 한 번의 DB 쿼리)
    idx = get_index()
    idx.log_access("get", article_id=article_id)
    related_map = idx.get_titles_by_ids(article.related)
    for rel_id in article.related:
        if rel_id not in related_map:
            related_map[rel_id] = rel_id
    # maturity/quality
    from ai_wiki.quality import validate as _qv
    quality_report = _qv(article)
    # backlinks
    backlinks = idx.get_backlinks(article_id)
    # similar articles (태그/카테고리 기반)
    similar_candidates = idx.find_related_candidates(article, limit=6)
    similar = [c for c in similar_candidates if c["id"] not in article.related and c["id"] != article_id][:5]
    return render_template("article.html", article=article,
                           related_map=related_map, quality=quality_report,
                           backlinks=backlinks, similar=similar)
@app.route("/category/<path:category_name>")
def view_category(category_name: str):
    idx = get_index()
    filtered = idx.get_all_articles_meta(sort="modified", category=category_name)
    return render_template("category.html", category=category_name, articles=filtered)


@app.route("/create", methods=["GET", "POST"])
def create_article():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        content_yaml = request.form.get("content", "").strip()
        tags_str = request.form.get("tags", "").strip()
        confidence = float(request.form.get("confidence", 0.8))
        sources_str = request.form.get("sources", "").strip()
        author = request.form.get("author", "human").strip()

        if not title or not category or not content_yaml:
            flash("제목, 카테고리, 내용은 필수입니다.", "error")
            return render_template("edit.html", mode="create", article=None, form=request.form)

        try:
            content = load_yaml_text(content_yaml)
            if not isinstance(content, dict):
                content = {"text": content_yaml}
        except yaml.YAMLError:
            content = {"text": content_yaml}

        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        sources = [s.strip() for s in sources_str.split("\n") if s.strip()]
        article_id = generate_id(title, category)

        article = Article(
            id=article_id, title=title, category=category, content=content,
            tags=tags, confidence=confidence, sources=sources, author=author,
        )

        idx = get_index()
        atomic_save(
            article, idx,
            vector_upsert=_vector_upsert, vector_remove=_vector_remove,
        )
        rebuild_catalog()
        flash(f"문서가 생성되었습니다: {title}", "success")
        return redirect(url_for("view_article", article_id=article_id))

    content_type = request.args.get("type", "").strip() or "technology"
    if content_type not in TYPE_SCHEMAS:
        content_type = "technology"
    content_template = build_content_template(content_type)
    return render_template("edit.html", mode="create", article=None, form={},
                           content_type=content_type,
                           type_schemas=TYPE_SCHEMAS,
                           content_template=content_template)


@app.route("/edit/<path:article_id>", methods=["GET", "POST"])
def edit_article(article_id: str):
    article, old_path = load_article_with_path(article_id)
    if not article:
        flash("문서를 찾을 수 없습니다.", "error")
        return redirect(url_for("home"))

    if request.method == "POST":
        article.title = request.form.get("title", article.title).strip()
        article.category = request.form.get("category", article.category).strip()

        content_yaml = request.form.get("content", "").strip()
        try:
            content = load_yaml_text(content_yaml)
            if not isinstance(content, dict):
                content = {"text": content_yaml}
        except yaml.YAMLError:
            content = {"text": content_yaml}
        article.content = content

        tags_str = request.form.get("tags", "")
        article.tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        article.confidence = float(request.form.get("confidence", article.confidence))
        sources_str = request.form.get("sources", "")
        article.sources = [s.strip() for s in sources_str.split("\n") if s.strip()]
        article.author = request.form.get("author", article.author).strip()
        article.version += 1
        article.last_modified = datetime.now(timezone.utc)

        idx = get_index()
        atomic_update(
            article, old_path, idx,
            vector_upsert=_vector_upsert, vector_remove=_vector_remove,
        )
        rebuild_catalog()
        flash("문서가 수정되었습니다.", "success")
        return redirect(url_for("view_article", article_id=article_id))

    return render_template("edit.html", mode="edit", article=article, form={})


@app.route("/delete/<path:article_id>", methods=["POST"])
def delete_article(article_id: str):
    article = load_article(article_id)
    if not article:
        flash("문서를 찾을 수 없습니다.", "error")
        return redirect(url_for("home"))

    delete_article_file(article_id)
    delete_source_files(article_id)
    idx = get_index()
    idx.remove(article_id)
    _vector_remove(article_id)
    rebuild_catalog()
    flash(f"문서가 삭제되었습니다: {article.title}", "success")
    return redirect(url_for("home"))


@app.route("/stale")
def stale_articles():
    days = int(request.args.get("days", 90))
    idx = get_index()
    results = idx.get_stale(days)
    return render_template("stale.html", days=days, articles=results)


@app.route("/all")
def all_articles():
    sort = request.args.get("sort", "modified")
    idx = get_index()
    articles = idx.get_all_articles_meta(sort=sort)
    return render_template("all.html", articles=articles, sort=sort)


@app.route("/graph")
def graph_view():
    articles = list_all_articles()
    nodes = []
    edges = []
    node_ids = {a.id for a in articles}
    for a in articles:
        meta = a.content.get("_meta", {}) if isinstance(a.content, dict) else {}
        nodes.append({
            "id": a.id,
            "title": a.title,
            "category": a.category.split("/")[0],
            "maturity": meta.get("maturity", "unknown"),
            "confidence": a.confidence,
        })
        for rel_id in a.related:
            if rel_id in node_ids:  # 존재하는 node만 edge 추가
                edges.append({"source": a.id, "target": rel_id})
    return render_template("graph.html", nodes=nodes, edges=edges)


@app.route("/graph/<path:article_id>")
def local_graph_view(article_id: str):
    """특정 문서 중심의 로컬 그래프 (1~2단계 관계)."""
    center_article = load_article(article_id)
    if not center_article:
        flash(f"문서를 찾을 수 없습니다: {article_id}", "error")
        return redirect(url_for("home"))
    
    all_articles = list_all_articles()
    all_map = {a.id: a for a in all_articles}
    
    # 1단계 이웃
    step1_ids = set(center_article.related)
    for a in all_articles:
        if article_id in a.related:
            step1_ids.add(a.id)
    
    # 2단계 이웃
    step2_ids = set()
    for nid in step1_ids:
        nbr = all_map.get(nid)
        if nbr:
            for rid in nbr.related:
                if rid != article_id and rid not in step1_ids:
                    step2_ids.add(rid)
            for a in all_articles:
                if nid in a.related and a.id != article_id and a.id not in step1_ids:
                    step2_ids.add(a.id)
    
    included_ids = {article_id} | step1_ids | step2_ids
    nodes = []
    for a in all_articles:
        if a.id not in included_ids:
            continue
        meta = a.content.get("_meta", {}) if isinstance(a.content, dict) else {}
        distance = 0 if a.id == article_id else (1 if a.id in step1_ids else 2)
        nodes.append({
            "id": a.id,
            "title": a.title,
            "category": a.category.split("/")[0],
            "maturity": meta.get("maturity", "unknown"),
            "confidence": a.confidence,
            "distance": distance,
        })
    
    edges = []
    node_ids = {n["id"] for n in nodes}
    for a in all_articles:
        if a.id not in node_ids:
            continue
        for rel_id in a.related:
            if rel_id in node_ids:
                edges.append({"source": a.id, "target": rel_id})
    
    return render_template("local_graph.html", 
                           center_id=article_id,
                           center_title=center_article.title,
                           nodes=nodes, edges=edges)


@app.route("/dashboard")
def dashboard():
    from ai_wiki.quality import validate as _qv
    articles = list_all_articles()
    idx = get_index()
    reports = []
    maturity_dist = {"stub": 0, "draft": 0, "review": 0, "mature": 0}
    total_score = 0

    for a in articles:
        r = _qv(a)
        maturity_dist[r.maturity] = maturity_dist.get(r.maturity, 0) + 1
        total_score += r.quality_score
        reports.append({
            "id": a.id, "title": a.title, "category": a.category,
            "maturity": r.maturity, "score": r.quality_score,
            "errors": len(r.errors), "warnings": len(r.warnings),
        })

    reports.sort(key=lambda x: x["score"])
    avg_score = round(total_score / max(len(reports), 1), 2)

    # 카테고리별 문서 수
    cat_stats = idx.get_category_stats()
    cat_data = sorted(cat_stats, key=lambda x: x["count"], reverse=True)[:15]

    # 최근 활동 (최근 생성/수정된 문서 10개)
    recent = idx.get_recent_articles(limit=10)

    # 태그 클라우드
    all_tags = idx.get_all_tags()
    tag_cloud = all_tags[:50]  # 최대 50개

    # 접근 통계
    access_stats = idx.get_access_stats(limit=5)

    return render_template("dashboard.html",
                           total=len(articles), avg_score=avg_score,
                           maturity_dist=maturity_dist, reports=reports,
                           cat_data=cat_data, recent=recent,
                           tag_cloud=tag_cloud, access_stats=access_stats)


@app.route("/tag/<tagname>")
def view_tag(tagname: str):
    idx = get_index()
    articles = idx.get_articles_by_tag(tagname)
    return render_template("tag.html", tagname=tagname, articles=articles)


# ── API (JSON) ─────────────────────────────────────

@app.route("/api/create", methods=["POST"])
def api_create():
    """JSON으로 문서 생성."""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "JSON body required"}), 400

    title = data.get("title", "").strip()
    category = data.get("category", "").strip()
    content = data.get("content")

    if not title or not category or not content:
        return jsonify({"status": "error", "message": "title, category, content required"}), 400

    if not isinstance(content, dict):
        content = {"text": str(content)}

    tags = data.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    sources = data.get("sources", [])
    if isinstance(sources, str):
        sources = [sources]

    related = data.get("related", [])
    if isinstance(related, str):
        related = [r.strip() for r in related.split(",") if r.strip()]

    article_id = generate_id(title, category)
    article = Article(
        id=article_id, title=title, category=category, content=content,
        tags=tags, confidence=float(data.get("confidence", 0.8)),
        sources=sources, related=related, author=data.get("author", "api"),
    )

    idx = get_index()
    atomic_save(
        article, idx,
        vector_upsert=_vector_upsert, vector_remove=_vector_remove,
    )
    rebuild_catalog()
    return jsonify({"status": "ok", "action": "created", "article_id": article_id}), 201


@app.route("/api/update/<path:article_id>", methods=["PUT"])
def api_update(article_id: str):
    """JSON으로 문서 수정."""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "JSON body required"}), 400

    article, old_path = load_article_with_path(article_id)
    if not article:
        return jsonify({"status": "error", "message": f"Not found: {article_id}"}), 404

    if "title" in data:
        article.title = data["title"]
    if "tags" in data:
        t = data["tags"]
        article.tags = [x.strip() for x in t.split(",") if x.strip()] if isinstance(t, str) else t
    if "confidence" in data:
        article.confidence = float(data["confidence"])
    if "sources" in data:
        article.sources = data["sources"] if isinstance(data["sources"], list) else [data["sources"]]
    if "related" in data:
        r = data["related"]
        new = [x.strip() for x in r.split(",") if x.strip()] if isinstance(r, str) else r
        article.related = list(dict.fromkeys(article.related + new))
    if "content" in data:
        c = data["content"]
        article.content = c if isinstance(c, dict) else {"text": str(c)}
    if "author" in data:
        article.author = data["author"]

    article.version += 1
    article.last_modified = datetime.now(timezone.utc)

    idx = get_index()
    atomic_update(
        article, old_path, idx,
        vector_upsert=_vector_upsert, vector_remove=_vector_remove,
    )
    rebuild_catalog()
    return jsonify({"status": "ok", "action": "updated", "article_id": article_id, "version": article.version})


@app.route("/api/delete/<path:article_id>", methods=["DELETE"])
def api_delete(article_id: str):
    """문서 삭제."""
    article = load_article(article_id)
    if not article:
        return jsonify({"status": "error", "message": f"Not found: {article_id}"}), 404

    delete_article_file(article_id)
    delete_source_files(article_id)
    idx = get_index()
    idx.remove(article_id)
    _vector_remove(article_id)
    rebuild_catalog()
    return jsonify({"status": "ok", "action": "deleted", "article_id": article_id})


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "")
    category = request.args.get("category") or None
    limit = int(request.args.get("limit", 20))
    idx = get_index()
    results = idx.search(q, category=category, limit=limit)
    return jsonify({"status": "ok", "count": len(results), "results": results})


@app.route("/api/stats")
def api_stats():
    idx = get_index()
    cat_stats = idx.get_category_stats()
    categories = {s["category"]: s["count"] for s in cat_stats}
    return jsonify({"status": "ok", "total": idx.count(), "categories": categories})


@app.route("/missions")
def missions_view():
    from ai_wiki.policy import PolicyDenied

    kind = request.args.get("kind") or None
    status = request.args.get("status") or None
    plan_id = request.args.get("plan") or None
    query_error = None
    access_error = None
    page = {"items": [], "total": 0, "limit": 25, "offset": 0, "has_more": False}
    store = None
    try:
        if kind and kind not in _MISSION_KINDS:
            raise ValueError("invalid_kind")
        if status and status not in _MISSION_STATUSES:
            raise ValueError("invalid_status")
        limit = _positive_query_int("limit", 25) or 25
        offset = int(request.args.get("offset", "0"))
        if offset < 0:
            raise ValueError("invalid_offset")
        store, reader = _mission_reader()
        page = reader.list(
            kind=kind, status=status, plan_id=plan_id,
            run_id=request.args.get("run") or None,
            limit=limit, offset=offset,
        )
    except PolicyDenied:
        access_error = "permission_denied"
    except (TypeError, ValueError) as exc:
        query_error = str(exc) or "invalid_query"
    finally:
        if store is not None:
            store.close()

    def page_url(offset: int) -> str:
        values = request.args.to_dict(flat=True)
        values["offset"] = str(offset)
        values["limit"] = str(page["limit"])
        return url_for("missions_view", **values)

    previous_url = page_url(max(0, page["offset"] - page["limit"])) if page["offset"] else None
    next_url = page_url(page["offset"] + page["limit"]) if page["has_more"] else None
    response = render_template(
        "missions.html", missions=page["items"], total=page["total"],
        limit=page["limit"], offset=page["offset"], has_more=page["has_more"],
        page_end=page["offset"] + len(page["items"]),
        previous_url=previous_url, next_url=next_url,
        kind_options=sorted(_MISSION_KINDS), status_options=sorted(_MISSION_STATUSES),
        selected_kind=kind or "", selected_status=status or "", selected_plan=plan_id or "",
        active_filters=bool(kind or status or plan_id or request.args.get("run")),
        query_error=query_error, access_error=access_error,
    )
    if access_error:
        return response, 403
    if query_error:
        return response, 400
    return response


_MISSION_KINDS = {
    "research_report", "work_plan", "work_run", "knowledge_candidate",
}
_MISSION_STATUSES = {
    "draft", "proposed", "approved", "rejected", "active", "superseded",
    "cancelled", "completed", "created", "running", "paused", "blocked",
    "failed", "in_review", "pending", "promoted",
}


def _mission_reader():
    from ai_wiki.missions import MissionControlReader, MissionStore
    from ai_wiki.policy import Principal, SecurityPolicy

    root = get_wiki_root()
    policy = SecurityPolicy(root)
    if _TEAM_MODE:
        principal = Principal(
            str(g.principal["id"]),
            frozenset(str(role) for role in g.principal.get("roles", [])),
        )
    else:
        principal = policy.resolve()
    store = MissionStore(root)
    return store, MissionControlReader(
        store, policy, principal, display_language=_current_lang(),
    )


def _positive_query_int(name: str, default: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"invalid_{name}") from exc
    if value < 1:
        raise ValueError(f"invalid_{name}")
    return value


@app.route("/api/missions")
def api_missions():
    from ai_wiki.policy import PolicyDenied

    kind = request.args.get("kind") or None
    status = request.args.get("status") or None
    if kind and kind not in _MISSION_KINDS:
        return jsonify({"status": "error", "error": {"code": "invalid_kind"}}), 400
    if status and status not in _MISSION_STATUSES:
        return jsonify({"status": "error", "error": {"code": "invalid_status"}}), 400
    try:
        limit = _positive_query_int("limit", 50)
        raw_offset = request.args.get("offset", "0")
        offset = int(raw_offset)
        if offset < 0:
            raise ValueError("invalid_offset")
        store, reader = _mission_reader()
        try:
            page = reader.list(
                kind=kind, status=status,
                plan_id=request.args.get("plan") or None,
                run_id=request.args.get("run") or None,
                limit=limit or 50, offset=offset,
            )
        finally:
            store.close()
    except PolicyDenied as exc:
        return jsonify({"status": "error", "error": {"code": exc.code}}), 403
    except (TypeError, ValueError) as exc:
        return jsonify({
            "status": "error", "error": {"code": str(exc) or "invalid_query"},
        }), 400
    return jsonify({
        "status": "ok", "count": len(page["items"]),
        "total": page["total"], "limit": page["limit"],
        "offset": page["offset"], "has_more": page["has_more"],
        "missions": page["items"],
    })


@app.route("/api/missions/<path:mission_id>")
def api_mission_detail(mission_id: str):
    from ai_wiki.policy import PolicyDenied

    try:
        revision = _positive_query_int("revision")
        store, reader = _mission_reader()
        try:
            detail = reader.detail(mission_id, revision)
        finally:
            store.close()
    except PolicyDenied as exc:
        return jsonify({"status": "error", "error": {"code": exc.code}}), 403
    except ValueError as exc:
        return jsonify({
            "status": "error", "error": {"code": str(exc) or "invalid_query"},
        }), 400
    if detail is None:
        return jsonify({
            "status": "error", "error": {"code": "mission_not_found"},
        }), 404
    return jsonify({
        "status": "ok", "mission": detail, "meta": {"policy": detail["policy"]},
    })


@app.route("/missions/<path:mission_id>")
def mission_detail_view(mission_id: str):
    from ai_wiki.policy import PolicyDenied

    detail = None
    error = None
    previous_revision = None
    next_revision = None
    store = None
    try:
        revision = _positive_query_int("revision")
        store, reader = _mission_reader()
        detail = reader.detail(mission_id, revision)
        if detail is None:
            error = "not_found"
        else:
            current = int(detail["revision"])
            if current > 1 and store.index_record(mission_id, current - 1):
                previous_revision = current - 1
            if store.index_record(mission_id, current + 1):
                next_revision = current + 1
    except PolicyDenied:
        error = "permission_denied"
    except (TypeError, ValueError):
        error = "invalid_revision"
    finally:
        if store is not None:
            store.close()
    response = render_template(
        "mission_detail.html", mission=detail, mission_id=mission_id,
        error=error, previous_revision=previous_revision, next_revision=next_revision,
    )
    if error == "permission_denied":
        return response, 403
    if error == "invalid_revision":
        return response, 400
    if error == "not_found":
        return response, 404
    return response


@app.route("/temporal/<path:article_id>")
def temporal_view(article_id: str):
    from ai_wiki.temporal import TemporalQueries
    query = TemporalQueries(get_index())
    return render_template(
        "temporal.html", article_id=article_id,
        current=query.current(article_id), timeline=query.timeline(article_id),
        disputed=query.disputed(article_id), conflicts=query.propose_conflicts(article_id),
    )


@app.route("/calibration")
def calibration_view():
    from ai_wiki.calibration import CalibrationManager
    from ai_wiki.vector import VectorIndex
    vector = VectorIndex()
    try:
        status = CalibrationManager(get_index(), vector).status()
    finally:
        vector.close()
    return render_template("calibration.html", status=status)



@app.route("/api/tags")
def api_tags():
    """태그 목록 API."""
    idx = get_index()
    tags = idx.get_all_tags()
    return jsonify({"status": "ok", "tags": tags})


@app.route("/api/tags/<tagname>")
def api_tag_articles(tagname):
    """특정 태그의 문서 목록 API."""
    idx = get_index()
    articles = idx.get_articles_by_tag(tagname)
    return jsonify({"status": "ok", "tag": tagname, "articles": articles})




@app.route("/api/schema/<type_name>")
def api_schema(type_name):
    """타입별 스키마 정보 API."""
    schema = TYPE_SCHEMAS.get(type_name, None)
    if schema is None:
        return jsonify({"status": "error", "message": f"Unknown type: {type_name}"}), 404
    return jsonify({
        "status": "ok",
        "type": type_name,
        "required": schema["required"],
        "optional": schema["optional"],
        "template": build_content_template(type_name),
    })


def main():
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else _RUNTIME.web_port
    host = os.environ.get("AI_WIKI_HOST", "127.0.0.1")
    display_host = "localhost" if host in ("127.0.0.1", "localhost") else host
    print(f"{_RUNTIME.display_name} Web UI: http://{display_host}:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
