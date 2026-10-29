"""Read original configuration locally; credential values never enter model context."""

from __future__ import annotations

import json
from pathlib import Path
import re
try:
    import tomllib
except ImportError:  # Python 3.10 project floor
    import tomli as tomllib

import yaml

from .app_delivery_skill.scripts import delivery


def document(source: Path, name: str):
    path = delivery.relative(source, name)
    text = delivery.text_of(path)
    suffix = path.suffix.lower()
    try:
        if suffix in {".yaml", ".yml"}:
            return yaml.safe_load(text)
        if suffix == ".json":
            return json.loads(text)
        if suffix == ".toml":
            return tomllib.loads(text)
        if path.name.startswith(".env") or suffix == ".properties":
            result = {}
            for line in text.splitlines():
                match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.*?)\s*$", line)
                if match:
                    value = match[2]
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    result[match[1]] = value
            return result
    except (ValueError, yaml.YAMLError):
        raise ValueError("无法读取原配置结构：" + name) from None
    raise ValueError("原配置引用仅支持 YAML、JSON、TOML、.env 和 properties：" + name)


def original_value(source: Path, reference: dict) -> str:
    if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
        raise ValueError("原配置引用需要 path 和 keys")
    keys = reference.get("keys")
    if not isinstance(keys, list) or not keys or any(type(k) not in {str, int} for k in keys):
        raise ValueError("原配置引用的 keys 必须是非空路径数组")
    value = document(source, reference["path"])
    try:
        for key in keys:
            if isinstance(value, list):
                if type(key) is not int or key < 0:
                    raise KeyError(key)
            elif not isinstance(value, dict):
                raise KeyError(key)
            value = value[key]
    except (KeyError, IndexError, TypeError):
        raise ValueError("找不到原配置字段：" + reference["path"] + " / " + ".".join(map(str, keys))) from None
    if value is None:
        return ""
    if type(value) not in {str, int, float, bool}:
        raise ValueError("原配置引用必须指向单个值")
    return str(value).lower() if type(value) is bool else str(value)


def loopback_connection(key: str, value: str) -> bool:
    """Conservative address hint, never returns credentials or assumes network reachability."""
    if key.upper() in {"HOST", "PORT", "BIND_HOST", "LISTEN_HOST"} or not re.search(r"URL|URI|DSN|HOST|ENDPOINT|CONNECTION|SERVER", key, re.I):
        return False
    return bool(re.search(r"(?:^|://|@|tcp\(|(?:host|server)\s*=)(?:localhost|127\.\d+\.\d+\.\d+|\[::1\])(?=[:/,;)]|$)", value.strip(), re.I))


def original_fields(source: Path, name: str) -> list[dict]:
    """Metadata only, including exact paths for masked fields; no original values."""
    try:
        root = document(source, name)
    except (ValueError, OSError):
        return []
    fields = []

    def walk(value, keys):
        if len(fields) >= 200 or len(keys) > 24:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if type(key) in {str, int}:
                    walk(item, [*keys, key])
        elif isinstance(value, list):
            for key, item in enumerate(value):
                walk(item, [*keys, key])
        elif keys and (value is None or type(value) in {str, int, float, bool}):
            metadata = {"source": {"path": name, "keys": keys}, "has_value": value is not None and str(value) != ""}
            if loopback_connection("_".join(map(str, keys)), str(value or "")):
                metadata["connection_hint"] = "loopback: points to the container itself after deployment; inspect whether dependency is in that same container"
            fields.append(metadata)

    walk(root, [])
    return fields


def runtime_configs(source: Path, configs: list[dict]) -> list[dict]:
    result = []
    for config in configs:
        value = original_value(source, config["source"]) if config.get("source") else config.get("default", "")
        result.append({"key": config["key"], "value": value, "secret": config["secret"], "required": config["required"], "description": config.get("description", ""), "value_source": "original" if config.get("source") else "default", "source_label": config["source"]["path"] + " / " + ".".join(map(str, config["source"]["keys"])) if config.get("source") else ""})
    return result
