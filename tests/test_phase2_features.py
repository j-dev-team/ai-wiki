"""Phase 2 기능 테스트."""
import json
import os
import tempfile
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from ai_wiki.cli import cli
from ai_wiki.models import Article
from ai_wiki.quality import (
    V_LEVELS, V_LEVEL_WEIGHTS, _calc_verification_rate, validate
)
from ai_wiki.schemas import (
    TYPE_SCHEMAS, BASE_SCHEMA, register_custom_types, compute_completeness
)


# -- Helpers --

def _setup_wiki(tmp_path):
    (tmp_path / "articles").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    return tmp_path


def _create_sample(runner, wiki_root):
    content_file = wiki_root / "tmp_content.yaml"
    content_file.write_text(
        yaml.dump({
            "type": "technology",
            "what": "Python pytest",
            "facts": ["pytest assert", "fixture system"],
            "facts_v": {"level": "corroborated", "sources": ["https://docs.pytest.org"]},
            "use_cases": ["unit test", "integration test"],
            "limitations": ["async needs plugin"],
        }),
        encoding="utf-8",
    )
    result = runner.invoke(cli, [
        "create", "--title", "Phase2 Test Article", "--category", "technology/test",
        "--tags", "test,phase2,pytest", "--confidence", "0.9",
        "--source", "https://docs.pytest.org", "--author", "test",
        "--content-file", str(content_file),
    ])
    data = json.loads(result.output)
    return data["article_id"]


# -- P2-4: 5단계 검증 레벨 테스트 --

class TestVerificationLevels:
    def test_v_levels_defined(self):
        assert "unverified" in V_LEVELS
        assert "sourced" in V_LEVELS
        assert "verified" in V_LEVELS
        assert "disputed" in V_LEVELS
        assert "human_verified" in V_LEVELS

    def test_level_weights_range(self):
        for level, weight in V_LEVEL_WEIGHTS.items():
            assert 0.0 <= weight <= 1.0, f"{level} weight={weight} out of range"

    def test_human_verified_highest_weight(self):
        assert V_LEVEL_WEIGHTS["human_verified"] == 1.0
        for level, weight in V_LEVEL_WEIGHTS.items():
            if level != "human_verified":
                assert weight < V_LEVEL_WEIGHTS["human_verified"]

    def test_unverified_lowest_weight(self):
        assert V_LEVEL_WEIGHTS["unverified"] == 0.0

    def test_calc_rate_human_verified(self):
        content = {"type": "technology", "facts_v": {"level": "human_verified"}}
        rate = _calc_verification_rate(content)
        assert rate == 1.0

    def test_calc_rate_sourced(self):
        content = {"type": "technology", "facts_v": {"level": "sourced"}}
        rate = _calc_verification_rate(content)
        assert rate == 0.3

    def test_calc_rate_disputed(self):
        content = {"type": "technology", "facts_v": {"level": "disputed"}}
        rate = _calc_verification_rate(content)
        assert rate == 0.2

    def test_calc_rate_mixed(self):
        content = {
            "facts_v": {"level": "human_verified"},
            "what_v": {"level": "unverified"},
        }
        rate = _calc_verification_rate(content)
        assert rate == 0.5

    def test_corroborated_equals_verified(self):
        assert V_LEVEL_WEIGHTS["corroborated"] == V_LEVEL_WEIGHTS["verified"]


# -- P2-6: 스키마 상속 + 동적 등록 테스트 --

class TestSchemaInheritance:
    def test_base_schema_defined(self):
        assert "required" in BASE_SCHEMA
        assert "optional" in BASE_SCHEMA
        assert "type" in BASE_SCHEMA["required"]

    def test_all_types_have_type_in_required(self):
        for type_name, schema in TYPE_SCHEMAS.items():
            assert "type" in schema["required"], f"{type_name} missing type in required"

    def test_technology_schema_complete(self):
        schema = TYPE_SCHEMAS["technology"]
        assert "what" in schema["required"]
        assert "facts" in schema["required"]
        assert "use_cases" in schema["optional"]

    def test_register_custom_type(self, tmp_path):
        config = {
            "custom_types": {
                "my_custom": {
                    "required": ["type", "custom_field1"],
                    "optional": ["custom_opt1"],
                }
            }
        }
        config_path = tmp_path / ".ai-wiki.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")
        registered = register_custom_types(config_path)
        assert "my_custom" in registered
        assert "my_custom" in TYPE_SCHEMAS
        assert "custom_field1" in TYPE_SCHEMAS["my_custom"]["required"]
        TYPE_SCHEMAS.pop("my_custom", None)

    def test_register_custom_type_with_extends(self, tmp_path):
        config = {
            "custom_types": {
                "extended_tech": {
                    "required": ["type", "what", "facts", "extra_field"],
                    "optional": ["extra_opt"],
                    "extends": "technology",
                }
            }
        }
        config_path = tmp_path / ".ai-wiki.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")
        registered = register_custom_types(config_path)
        assert "extended_tech" in registered
        assert "extra_field" in TYPE_SCHEMAS["extended_tech"]["required"]
        TYPE_SCHEMAS.pop("extended_tech", None)

    def test_register_nonexistent_config(self, tmp_path):
        result = register_custom_types(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_completeness_uses_inheritance(self):
        content = {"type": "technology", "what": "test", "facts": ["f1", "f2"]}
        comp, missing, hints = compute_completeness(content)
        assert isinstance(comp, float)
        assert 0.0 <= comp <= 1.0


# -- P2-2: 인간 검증 레이어 테스트 --

class TestHumanVerification:
    def test_verify_human_flag(self, tmp_path):
        wiki_root = _setup_wiki(tmp_path)
        runner = CliRunner()
        article_id = _create_sample(runner, wiki_root)
        result = runner.invoke(cli, ["verify", article_id, "--human", "--by", "test_reviewer"])
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["action"] == "human_verified"
        assert data["human_verified"] is True
        assert data["verified_by"] == "test_reviewer"
        os.environ.pop("AI_WIKI_ROOT", None)

    def test_verify_without_human_flag(self, tmp_path):
        wiki_root = _setup_wiki(tmp_path)
        runner = CliRunner()
        article_id = _create_sample(runner, wiki_root)
        result = runner.invoke(cli, ["verify", article_id])
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["action"] == "verified"
        assert "human_verified" not in data
        os.environ.pop("AI_WIKI_ROOT", None)

    def test_human_verified_stored_in_meta(self, tmp_path):
        wiki_root = _setup_wiki(tmp_path)
        runner = CliRunner()
        article_id = _create_sample(runner, wiki_root)
        runner.invoke(cli, ["verify", article_id, "--human", "--by", "reviewer1"])
        result = runner.invoke(cli, ["get", article_id])
        data = json.loads(result.output)
        meta = data["article"].get("content", {}).get("_meta", {})
        assert meta.get("human_verified") is True
        assert meta.get("verified_by") == "reviewer1"
        os.environ.pop("AI_WIKI_ROOT", None)

    def test_index_human_verified_methods(self, tmp_path):
        from ai_wiki.index import WikiIndex
        idx = WikiIndex(tmp_path / "test.db")
        from datetime import datetime, timezone
        article = Article(
            id="hv-test-abc123",
            title="HV Test",
            category="test",
            content={"type": "technology", "what": "test", "facts": ["f1"]},
            sources=["https://example.com"],
            tags=["test"],
        )
        idx.upsert(article, "test/hv-test-abc123.yaml")
        status = idx.get_human_verified_status("hv-test-abc123")
        assert status["human_verified"] is False
        idx.set_human_verified("hv-test-abc123", "tester", "2026-01-01T00:00:00Z")
        status = idx.get_human_verified_status("hv-test-abc123")
        assert status["human_verified"] is True
        assert status["verified_by"] == "tester"
        idx.close()


# -- P2-5: 마크다운 내보내기 테스트 --

class TestMarkdownExport:
    def test_export_single_md(self, tmp_path):
        wiki_root = _setup_wiki(tmp_path)
        runner = CliRunner()
        article_id = _create_sample(runner, wiki_root)
        out_file = tmp_path / "output.md"
        result = runner.invoke(cli, ["export", article_id, "--format", "md", "--output", str(out_file)])
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["format"] == "md"
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "Phase2 Test Article" in content
        assert "##" in content
        os.environ.pop("AI_WIKI_ROOT", None)

    def test_export_stdout(self, tmp_path):
        wiki_root = _setup_wiki(tmp_path)
        runner = CliRunner()
        article_id = _create_sample(runner, wiki_root)
        result = runner.invoke(cli, ["export", article_id, "--format", "md"])
        assert result.exit_code == 0
        assert "# " in result.output
        os.environ.pop("AI_WIKI_ROOT", None)

    def test_export_all(self, tmp_path):
        wiki_root = _setup_wiki(tmp_path)
        runner = CliRunner()
        _create_sample(runner, wiki_root)
        out_dir = tmp_path / "exports"
        result = runner.invoke(cli, [
            "export-all", "--format", "md", "--output-dir", str(out_dir)
        ])
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["exported_count"] >= 1
        assert out_dir.exists()
        md_files = list(out_dir.glob("*.md"))
        assert len(md_files) >= 1
        os.environ.pop("AI_WIKI_ROOT", None)

    def test_export_yaml_format(self, tmp_path):
        wiki_root = _setup_wiki(tmp_path)
        runner = CliRunner()
        article_id = _create_sample(runner, wiki_root)
        out_file = tmp_path / "output.yaml"
        result = runner.invoke(cli, ["export", article_id, "--format", "yaml", "--output", str(out_file)])
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["format"] == "yaml"
        content = out_file.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)
        os.environ.pop("AI_WIKI_ROOT", None)

    def test_export_nonexistent(self, tmp_path):
        wiki_root = _setup_wiki(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "nonexistent-id-xyz"])
        assert result.exit_code != 0
        os.environ.pop("AI_WIKI_ROOT", None)


# -- P2-1: 구조화 폼 (schemas) 테스트 --

class TestStructuredForm:
    def test_api_schema_endpoint_coverage(self):
        for type_name, schema in TYPE_SCHEMAS.items():
            assert "required" in schema, f"{type_name} missing required"
            assert "optional" in schema, f"{type_name} missing optional"
            assert len(schema["required"]) > 0, f"{type_name} required is empty"

    def test_type_count(self):
        assert len(TYPE_SCHEMAS) >= 10

    def test_all_required_fields_unique(self):
        for type_name, schema in TYPE_SCHEMAS.items():
            req_set = set(schema["required"])
            opt_set = set(schema["optional"])
            overlap = req_set & opt_set
            assert len(overlap) == 0, f"{type_name} has overlap: {overlap}"


# -- P2-1: 웹 편집 UI 구조화 폼 (Flask) 테스트 --

class TestWebStructuredForm:
    @pytest.fixture
    def web_app(self, tmp_path):
        """Flask 테스트 클라이언트 설정."""
        _setup_wiki(tmp_path)
        import ai_wiki.web as web_module
        web_module._wiki_index = None
        web_module.app.config["TESTING"] = True
        with web_module.app.test_client() as client:
            yield client
        os.environ.pop("AI_WIKI_ROOT", None)

    def test_create_page_get(self, web_app):
        resp = web_app.get("/create")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "content_type" in body

    def test_create_page_type_selector(self, web_app):
        resp = web_app.get("/create?type=event")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "event" in body

    def test_api_schema_technology(self, web_app):
        resp = web_app.get("/api/schema/technology")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"
        assert "what" in data["required"]
        assert "facts" in data["required"]

    def test_api_schema_event(self, web_app):
        resp = web_app.get("/api/schema/event")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "period" in data["required"]
        assert "participants" in data["required"]

    def test_api_schema_all_types(self, web_app):
        for type_name in TYPE_SCHEMAS:
            resp = web_app.get("/api/schema/" + type_name)
            assert resp.status_code == 200, type_name + " schema endpoint failed"
            data = json.loads(resp.data)
            assert data["status"] == "ok"
            assert len(data["required"]) > 0

    def test_create_post_valid(self, web_app):
        yaml_lines = ["type: technology", "what: 웹 테스트", "facts:", "  - fact1"]
        yaml_str = chr(10).join(yaml_lines)
        form_data = {
            "title": "Web Test Article",
            "category": "test/web",
            "tags": "web,test",
            "confidence": "0.8",
            "author": "tester",
            "sources": "",
            "content_type": "technology",
            "content": yaml_str,
        }
        resp = web_app.post("/create", data=form_data, follow_redirects=True)
        assert resp.status_code == 200
