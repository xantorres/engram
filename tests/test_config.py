import pytest

from engram.config import ConfigError, load


def _write(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_autopromote_toml_string_false_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_AUTOPROMOTE", raising=False)
    path = _write(tmp_path, '[bridge]\nautopromote = "false"\n')
    with pytest.raises(ConfigError):
        load(path)


def test_kind_allowlist_non_list_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_BRIDGE_KIND_ALLOWLIST", raising=False)
    path = _write(tmp_path, '[bridge]\nkind_allowlist = "tooling"\n')
    with pytest.raises(ConfigError):
        load(path)


def test_kind_allowlist_non_str_items_raise(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_BRIDGE_KIND_ALLOWLIST", raising=False)
    path = _write(tmp_path, "[bridge]\nkind_allowlist = [1, 2]\n")
    with pytest.raises(ConfigError):
        load(path)


def test_extractor_base_url_non_string_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_EXTRACTOR_URL", raising=False)
    path = _write(tmp_path, "[extractor]\nbase_url = 123\n")
    with pytest.raises(ConfigError):
        load(path)


def test_kind_allowlist_rejects_curated_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_BRIDGE_KIND_ALLOWLIST", "fiscal")
    with pytest.raises(ConfigError):
        load(tmp_path / "none.toml")


def test_kind_allowlist_rejects_unknown_kind(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_BRIDGE_KIND_ALLOWLIST", raising=False)
    path = _write(tmp_path, '[bridge]\nkind_allowlist = ["banana"]\n')
    with pytest.raises(ConfigError):
        load(path)


def test_valid_config_loads(tmp_path, monkeypatch):
    for var in (
        "ENGRAM_AUTOPROMOTE",
        "ENGRAM_BRIDGE_KIND_ALLOWLIST",
        "ENGRAM_EXTRACTOR_URL",
        "ENGRAM_STORE",
    ):
        monkeypatch.delenv(var, raising=False)
    path = _write(
        tmp_path,
        '[store]\ndir = "/tmp/x"\n\n'
        '[extractor]\nbase_url = "http://h/v1"\nmodel = "m"\n\n'
        '[bridge]\nautopromote = true\nkind_allowlist = ["tooling"]\n',
    )
    cfg = load(path)
    assert cfg.autopromote is True
    assert cfg.kind_allowlist == ["tooling"]


_GC_RECALL_VARS = (
    "ENGRAM_GC_BAK_KEEP_DAYS",
    "ENGRAM_GC_AUDIT_MAX_BYTES",
    "ENGRAM_GC_QUEUE_DONE_KEEP_DAYS",
    "ENGRAM_GC_STALE_GRACE_DAYS",
    "ENGRAM_GC_AUDIT_ARCHIVE_KEEP_DAYS",
    "ENGRAM_RECALL_AUTO_REFRESH",
    "ENGRAM_RECALL_REFRESH_TARGETS",
    "ENGRAM_RECALL_LIMIT",
)


def _clear_gc_recall(monkeypatch):
    for var in _GC_RECALL_VARS:
        monkeypatch.delenv(var, raising=False)


def test_gc_recall_defaults(tmp_path, monkeypatch):
    _clear_gc_recall(monkeypatch)
    cfg = load(tmp_path / "none.toml")
    assert cfg.gc.bak_keep_days == 14
    assert cfg.gc.audit_max_bytes == 5_000_000
    assert cfg.gc.queue_done_keep_days == 30
    assert cfg.gc.stale_grace_days == 30
    assert cfg.gc.audit_archive_keep_days == 90
    assert cfg.recall.auto_refresh is False
    assert cfg.recall.refresh_targets == []
    assert cfg.recall.limit == 30


def test_gc_bak_keep_days_non_int_raises(tmp_path, monkeypatch):
    _clear_gc_recall(monkeypatch)
    path = _write(tmp_path, '[gc]\nbak_keep_days = "x"\n')
    with pytest.raises(ConfigError):
        load(path)


def test_gc_audit_archive_keep_days_non_int_raises(tmp_path, monkeypatch):
    _clear_gc_recall(monkeypatch)
    path = _write(tmp_path, '[gc]\naudit_archive_keep_days = "x"\n')
    with pytest.raises(ConfigError):
        load(path)


def test_gc_ignores_legacy_archive_key(tmp_path, monkeypatch):
    """Withdrawing the knob must not break configs that still set it."""
    _clear_gc_recall(monkeypatch)
    path = _write(tmp_path, '[gc]\narchive = "yes"\nbak_keep_days = 7\n')
    cfg = load(path)
    assert cfg.gc.bak_keep_days == 7


def test_recall_limit_non_int_raises(tmp_path, monkeypatch):
    _clear_gc_recall(monkeypatch)
    path = _write(tmp_path, "[recall]\nlimit = 1.5\n")
    with pytest.raises(ConfigError):
        load(path)


def test_gc_recall_toml_loads(tmp_path, monkeypatch):
    _clear_gc_recall(monkeypatch)
    path = _write(
        tmp_path,
        "[gc]\nbak_keep_days = 7\naudit_max_bytes = 1000\n"
        "audit_archive_keep_days = 7\n\n"
        '[recall]\nauto_refresh = true\nrefresh_targets = ["~/docs/AGENTS.md"]\nlimit = 5\n',
    )
    cfg = load(path)
    assert cfg.gc.bak_keep_days == 7
    assert cfg.gc.audit_max_bytes == 1000
    assert cfg.gc.audit_archive_keep_days == 7
    assert cfg.recall.auto_refresh is True
    assert cfg.recall.refresh_targets == ["~/docs/AGENTS.md"]
    assert cfg.recall.limit == 5


def test_gc_recall_env_overrides(tmp_path, monkeypatch):
    _clear_gc_recall(monkeypatch)
    monkeypatch.setenv("ENGRAM_GC_BAK_KEEP_DAYS", "0")
    monkeypatch.setenv("ENGRAM_GC_AUDIT_ARCHIVE_KEEP_DAYS", "3")
    monkeypatch.setenv("ENGRAM_RECALL_AUTO_REFRESH", "true")
    monkeypatch.setenv("ENGRAM_RECALL_REFRESH_TARGETS", "/tmp/AGENTS.md,/tmp/CLAUDE.md")
    monkeypatch.setenv("ENGRAM_RECALL_LIMIT", "9")
    cfg = load(tmp_path / "none.toml")
    assert cfg.gc.bak_keep_days == 0
    assert cfg.gc.audit_archive_keep_days == 3
    assert cfg.recall.auto_refresh is True
    assert cfg.recall.refresh_targets == ["/tmp/AGENTS.md", "/tmp/CLAUDE.md"]
    assert cfg.recall.limit == 9


def test_gc_env_non_int_raises(tmp_path, monkeypatch):
    _clear_gc_recall(monkeypatch)
    monkeypatch.setenv("ENGRAM_GC_AUDIT_MAX_BYTES", "lots")
    with pytest.raises(ConfigError):
        load(tmp_path / "none.toml")
