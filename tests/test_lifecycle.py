import zipfile
from pathlib import Path

import pytest
import yaml

from ai_wiki.lifecycle import backup_variant, refresh_variant, restore_variant
from ai_wiki.variant import VariantSpec, create_variant_package


def _initialized_package(tmp_path: Path) -> Path:
    spec = VariantSpec.build(package_name="life-wiki", display_name="Life Wiki", domain="life")
    package_dir = Path(create_variant_package(spec, output_dir=tmp_path)["package_dir"])
    (package_dir / spec.config_filename).write_text(
        yaml.safe_dump({"name": "Life Wiki", "agents": ["codex"]}), encoding="utf-8"
    )
    (package_dir / "articles" / "life").mkdir(parents=True)
    (package_dir / "articles" / "life" / "entry.yaml").write_text("id: life-1\n", encoding="utf-8")
    (package_dir / "data").mkdir()
    (package_dir / "data" / "wiki.db").write_bytes(b"database")
    return package_dir


def test_backup_and_restore_round_trip(tmp_path):
    package_dir = _initialized_package(tmp_path)
    backup = backup_variant(package_dir, tmp_path / "backup.zip")
    (package_dir / "articles" / "life" / "entry.yaml").write_text("changed", encoding="utf-8")
    (package_dir / "extra.txt").write_text("remove me", encoding="utf-8")

    restored = restore_variant(Path(backup["archive"]), package_dir)

    assert restored["package_name"] == "life-wiki"
    assert (package_dir / "articles" / "life" / "entry.yaml").read_text(encoding="utf-8") == "id: life-1\n"
    assert not (package_dir / "extra.txt").exists()
    assert (package_dir / "data" / "wiki.db").read_bytes() == b"database"


def test_restore_rejects_path_traversal(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(".ai-wiki-backup.json", "{}")
        output.writestr("../escape.txt", "bad")

    with pytest.raises(ValueError, match="unsafe backup member"):
        restore_variant(archive, tmp_path / "target")


def test_refresh_rolls_back_when_validation_fails(tmp_path, monkeypatch):
    package_dir = _initialized_package(tmp_path)
    module_dir = package_dir / "src" / "life_wiki"
    (module_dir / "storage.py").write_text("legacy = True\n", encoding="utf-8")
    monkeypatch.setattr("ai_wiki.lifecycle.install_variant_package", lambda *args, **kwargs: {"installed": True})
    monkeypatch.setattr(
        "ai_wiki.lifecycle._validate_refreshed_variant",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("validation failed")),
    )

    with pytest.raises(RuntimeError, match="validation failed"):
        refresh_variant(package_dir)

    assert (module_dir / "storage.py").read_text(encoding="utf-8") == "legacy = True\n"
    assert (package_dir / "articles" / "life" / "entry.yaml").exists()
