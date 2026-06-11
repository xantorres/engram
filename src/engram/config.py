"""Engram configuration.

Resolved from ``~/.config/engram/config.toml`` with environment overrides, so a
private deployment can point the store at any vault and the extractor at any
OpenAI-compatible endpoint without touching code.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from engram.extract.client import ExtractorConfig


def default_store_dir() -> Path:
    return Path.home() / ".local" / "share" / "engram"


def _config_path() -> Path:
    return Path(os.environ.get("ENGRAM_CONFIG", Path.home() / ".config" / "engram" / "config.toml"))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: list[str] | None) -> list[str] | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    stripped = raw.strip()
    if not stripped:
        return None
    items = [item.strip() for item in stripped.split(",") if item.strip()]
    return items or None


@dataclass
class Config:
    store_dir: Path
    extractor: ExtractorConfig
    autopromote: bool = False
    kind_allowlist: list[str] | None = None


def load(path: str | Path | None = None) -> Config:
    path = Path(path) if path is not None else _config_path()
    data: dict = {}
    if path.exists():
        data = tomllib.loads(path.read_text(encoding="utf-8"))

    store = data.get("store", {})
    extractor = data.get("extractor", {})
    bridge = data.get("bridge", {})

    store_dir = Path(
        os.environ.get("ENGRAM_STORE", store.get("dir") or default_store_dir())
    ).expanduser()

    toml_allowlist = bridge.get("kind_allowlist") or None
    kind_allowlist = _env_list("ENGRAM_BRIDGE_KIND_ALLOWLIST", toml_allowlist)

    return Config(
        store_dir=store_dir,
        extractor=ExtractorConfig(
            base_url=os.environ.get(
                "ENGRAM_EXTRACTOR_URL", extractor.get("base_url", "http://localhost:1234/v1")
            ),
            model=os.environ.get("ENGRAM_EXTRACTOR_MODEL", extractor.get("model", "local-model")),
            api_key=os.environ.get("ENGRAM_EXTRACTOR_KEY", extractor.get("api_key")),
        ),
        autopromote=_env_bool("ENGRAM_AUTOPROMOTE", bool(bridge.get("autopromote", False))),
        kind_allowlist=kind_allowlist,
    )
