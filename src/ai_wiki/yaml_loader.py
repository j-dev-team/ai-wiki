"""Resource-bounded YAML loading with duplicate-key rejection."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

MAX_YAML_BYTES = 2 * 1024 * 1024
MAX_YAML_DEPTH = 50
MAX_YAML_ALIASES = 100


class StrictSafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: StrictSafeLoader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                "found an unhashable mapping key", key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found duplicate key: {key!r}", key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _check_depth(value: Any, depth: int = 0) -> None:
    if depth > MAX_YAML_DEPTH:
        raise ValueError(f"YAML nesting exceeds {MAX_YAML_DEPTH} levels")
    if isinstance(value, dict):
        for key, item in value.items():
            _check_depth(key, depth + 1)
            _check_depth(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_depth(item, depth + 1)


def load_yaml_text(text: str) -> Any:
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_YAML_BYTES:
        raise ValueError(f"YAML document exceeds {MAX_YAML_BYTES} bytes")
    alias_count = sum(
        isinstance(event, yaml.events.AliasEvent) for event in yaml.parse(text)
    )
    if alias_count > MAX_YAML_ALIASES:
        raise ValueError(f"YAML alias count exceeds {MAX_YAML_ALIASES}")
    value = yaml.load(text, Loader=StrictSafeLoader)
    _check_depth(value)
    return value


def load_yaml_file(path: Path) -> Any:
    if path.stat().st_size > MAX_YAML_BYTES:
        raise ValueError(f"YAML document exceeds {MAX_YAML_BYTES} bytes")
    return load_yaml_text(path.read_text(encoding="utf-8"))
