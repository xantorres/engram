"""Engram configuration.

Resolved from ``~/.config/engram/config.toml`` with environment overrides, so a
private deployment can point the store at any vault and the extractor at any
OpenAI-compatible endpoint without touching code.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from engram.core.schema import Kind
from engram.core.tiers import CURATED_KINDS
from engram.extract.client import ExtractorConfig


class ConfigError(ValueError):
    """A config value has the wrong type or shape; engram fails closed."""


def _check_str(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string, got {type(value).__name__}")
    return value


def _check_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a boolean, got {type(value).__name__}")
    return value


def _check_int(value: object, name: str) -> int:
    # bool is an int subclass; reject it so a bare `true` can't pass as a count.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer, got {type(value).__name__}")
    return value


def _check_str_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of strings")
    return value


def _validate_allowlist(items: list[str]) -> None:
    """Every allowlisted kind must be real and non-curated.

    Curated kinds (identity, fiscal, people, ...) always require human review, so
    letting one into the allowlist would silently reopen the bypass it closes.
    """
    for item in items:
        try:
            kind = Kind(item)
        except ValueError as e:
            raise ConfigError(f"bridge.kind_allowlist has unknown kind: {item!r}") from e
        if kind in CURATED_KINDS:
            raise ConfigError(f"bridge.kind_allowlist may not contain curated kind: {item!r}")


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


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from e


@dataclass
class GcConfig:
    """Retention windows for the self-maintaining store."""

    bak_keep_days: int = 14
    audit_max_bytes: int = 5_000_000
    queue_done_keep_days: int = 30
    stale_grace_days: int = 30
    audit_archive_keep_days: int = 90


@dataclass
class RecallConfig:
    """How recall materializes into the user's context files."""

    auto_refresh: bool = False
    refresh_targets: list[str] = field(default_factory=list)
    limit: int = 30


def _load_gc(data: dict) -> GcConfig:
    section = data.get("gc", {})
    return GcConfig(
        bak_keep_days=_env_int(
            "ENGRAM_GC_BAK_KEEP_DAYS",
            _check_int(section.get("bak_keep_days", 14), "gc.bak_keep_days"),
        ),
        audit_max_bytes=_env_int(
            "ENGRAM_GC_AUDIT_MAX_BYTES",
            _check_int(section.get("audit_max_bytes", 5_000_000), "gc.audit_max_bytes"),
        ),
        queue_done_keep_days=_env_int(
            "ENGRAM_GC_QUEUE_DONE_KEEP_DAYS",
            _check_int(section.get("queue_done_keep_days", 30), "gc.queue_done_keep_days"),
        ),
        stale_grace_days=_env_int(
            "ENGRAM_GC_STALE_GRACE_DAYS",
            _check_int(section.get("stale_grace_days", 30), "gc.stale_grace_days"),
        ),
        audit_archive_keep_days=_env_int(
            "ENGRAM_GC_AUDIT_ARCHIVE_KEEP_DAYS",
            _check_int(section.get("audit_archive_keep_days", 90), "gc.audit_archive_keep_days"),
        ),
    )


def _load_recall(data: dict) -> RecallConfig:
    section = data.get("recall", {})
    toml_targets = section.get("refresh_targets")
    if toml_targets is not None:
        _check_str_list(toml_targets, "recall.refresh_targets")
    targets = _env_list("ENGRAM_RECALL_REFRESH_TARGETS", toml_targets)
    return RecallConfig(
        auto_refresh=_env_bool(
            "ENGRAM_RECALL_AUTO_REFRESH",
            _check_bool(section.get("auto_refresh", False), "recall.auto_refresh"),
        ),
        refresh_targets=targets or [],
        limit=_env_int(
            "ENGRAM_RECALL_LIMIT",
            _check_int(section.get("limit", 30), "recall.limit"),
        ),
    )


@dataclass
class Config:
    store_dir: Path
    extractor: ExtractorConfig
    autopromote: bool = False
    kind_allowlist: list[str] | None = None
    gc: GcConfig = field(default_factory=GcConfig)
    recall: RecallConfig = field(default_factory=RecallConfig)


def load(path: str | Path | None = None) -> Config:
    path = Path(path) if path is not None else _config_path()
    data: dict = {}
    if path.exists():
        data = tomllib.loads(path.read_text(encoding="utf-8"))

    store = data.get("store", {})
    extractor = data.get("extractor", {})
    bridge = data.get("bridge", {})

    store_dir_value = _check_str(store.get("dir"), "store.dir", optional=True)
    store_dir = Path(
        os.environ.get("ENGRAM_STORE", store_dir_value or default_store_dir())
    ).expanduser()

    toml_allowlist = bridge.get("kind_allowlist")
    if toml_allowlist is not None:
        _check_str_list(toml_allowlist, "bridge.kind_allowlist")
    kind_allowlist = _env_list("ENGRAM_BRIDGE_KIND_ALLOWLIST", toml_allowlist or None)
    if kind_allowlist is not None:
        _validate_allowlist(kind_allowlist)

    return Config(
        store_dir=store_dir,
        extractor=ExtractorConfig(
            base_url=os.environ.get(
                "ENGRAM_EXTRACTOR_URL",
                _check_str(extractor.get("base_url"), "extractor.base_url", optional=True)
                or "http://localhost:1234/v1",
            ),
            model=os.environ.get(
                "ENGRAM_EXTRACTOR_MODEL",
                _check_str(extractor.get("model"), "extractor.model", optional=True)
                or "local-model",
            ),
            api_key=os.environ.get(
                "ENGRAM_EXTRACTOR_KEY",
                _check_str(extractor.get("api_key"), "extractor.api_key", optional=True),
            ),
        ),
        autopromote=_env_bool(
            "ENGRAM_AUTOPROMOTE",
            _check_bool(bridge.get("autopromote", False), "bridge.autopromote"),
        ),
        kind_allowlist=kind_allowlist,
        gc=_load_gc(data),
        recall=_load_recall(data),
    )
