from engram.core.schema import Kind, Memory, Status
from engram.core.store import MarkdownStore


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


def test_append_log_is_newest_first(tmp_path):
    store = MarkdownStore(tmp_path)
    first = store.add(Memory(fact="first fact", kind=Kind.tooling))
    second = store.add(Memory(fact="second fact", kind=Kind.tooling))
    store.append_log(first)
    store.append_log(second)
    text = (tmp_path / "memory-log.md").read_text()
    assert text.index("second fact") < text.index("first fact")
