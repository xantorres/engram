import os
import stat

import pytest

from engram.core.schema import Kind, Memory, Status
from engram.core.store import MarkdownStore, StoreFormatError


def test_malformed_frontmatter_raises_store_format_error(tmp_path):
    (tmp_path / "memory.md").write_text("no frontmatter here", encoding="utf-8")
    store = MarkdownStore(tmp_path)
    with pytest.raises(StoreFormatError):
        store.list()


def test_add_refuses_to_overwrite_malformed_registry(tmp_path):
    registry = tmp_path / "memory.md"
    original = "totally not a valid store file"
    registry.write_text(original, encoding="utf-8")
    store = MarkdownStore(tmp_path)
    with pytest.raises(StoreFormatError):
        store.add(Memory(fact="new", kind=Kind.tooling))
    assert registry.read_text(encoding="utf-8") == original


def test_missing_registry_still_returns_empty(tmp_path):
    assert MarkdownStore(tmp_path).list() == []


def test_queue_get_rejects_traversal_id(tmp_path):
    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="x", kind=Kind.tooling))
    store.enqueue(mem, dest="memory.md")
    (tmp_path / "secret.json").write_text('{"leaked": true}', encoding="utf-8")
    assert store.queue_get("../secret") is None


def test_resolve_queue_ignores_traversal_id(tmp_path):
    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="x", kind=Kind.tooling))
    store.enqueue(mem, dest="memory.md")
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")
    store.resolve_queue("../outside")
    assert (tmp_path / "outside.json").exists()


def test_store_init_tightens_existing_modes(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    registry = root / "memory.md"
    registry.write_text("---\nschema: memory.v1\n---\n", encoding="utf-8")
    os.chmod(registry, 0o644)
    os.chmod(root, 0o755)

    MarkdownStore(root)
    assert stat.S_IMODE(registry.stat().st_mode) == 0o600
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_queue_and_done_dirs_are_0700(tmp_path):
    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="a", kind=Kind.tooling))
    store.enqueue(mem, dest="memory.md")
    assert stat.S_IMODE((tmp_path / "queue").stat().st_mode) == 0o700
    store.resolve_queue(mem.id)
    assert stat.S_IMODE((tmp_path / "queue" / "_done").stat().st_mode) == 0o700


def test_add_allocates_id_and_persists(tmp_path):
    store = MarkdownStore(tmp_path)
    saved = store.add(Memory(fact="prefers pnpm", kind=Kind.tooling))
    assert saved.id == "mem-0001"
    assert (tmp_path / "memory.md").exists()

    reloaded = MarkdownStore(tmp_path)
    assert reloaded.get("mem-0001").fact == "prefers pnpm"


def test_ids_are_monotonic(tmp_path):
    store = MarkdownStore(tmp_path)
    a = store.add(Memory(fact="a"))
    b = store.add(Memory(fact="b"))
    assert (a.id, b.id) == ("mem-0001", "mem-0002")


def test_list_filters_by_status(tmp_path):
    store = MarkdownStore(tmp_path)
    store.add(Memory(fact="a", status=Status.pending))
    store.add(Memory(fact="b", status=Status.promoted))
    assert len(store.list(status=Status.promoted)) == 1


def test_update_persists(tmp_path):
    store = MarkdownStore(tmp_path)
    m = store.add(Memory(fact="a"))
    store.update(m.model_copy(update={"status": Status.promoted}))
    assert store.get(m.id).status == Status.promoted


def test_append_log_flattens_multiline_fact(tmp_path):
    store = MarkdownStore(tmp_path)
    mem = store.add(Memory(fact="alpha\nbeta\ngamma", kind=Kind.tooling))
    store.append_log(mem)
    text = (tmp_path / "memory-log.md").read_text()
    entry_line = next(line for line in text.splitlines() if "alpha" in line)
    assert "alpha beta gamma" in entry_line


def test_append_log_is_newest_first(tmp_path):
    store = MarkdownStore(tmp_path)
    first = store.add(Memory(fact="first fact", kind=Kind.tooling))
    second = store.add(Memory(fact="second fact", kind=Kind.tooling))
    store.append_log(first)
    store.append_log(second)
    text = (tmp_path / "memory-log.md").read_text()
    assert text.index("second fact") < text.index("first fact")
