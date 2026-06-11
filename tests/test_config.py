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
