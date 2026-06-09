from engram.capture.importer import import_markdown_dir, infer_kind
from engram.core.schema import Kind, LearnedBy, Status
from engram.core.store import MarkdownStore


def test_infer_kind_sensitive_keywords_win():
    assert infer_kind("My VAT number is 123") == Kind.fiscal
    assert infer_kind("Born in Madrid, passport on file") == Kind.identity


def test_infer_kind_falls_back_to_type_hint():
    assert infer_kind("uses pnpm", "project") == Kind.project
    assert infer_kind("likes dark mode", "feedback") == Kind.preference


def test_import_markdown_dir(tmp_path):
    src = tmp_path / "mem"
    src.mkdir()
    (src / "a.md").write_text(
        "---\ndescription: Prefers pnpm over npm\ntype: feedback\n---\nbody", encoding="utf-8"
    )
    (src / "b.md").write_text(
        "---\ndescription: VAT number is 12345678X\ntype: user\n---\nbody", encoding="utf-8"
    )
    (src / "MEMORY.md").write_text("- skipped index line\n", encoding="utf-8")

    staged = import_markdown_dir(MarkdownStore(tmp_path / "store"), src)
    by_fact = {m.fact: m for m in staged}
    assert len(staged) == 2
    assert by_fact["Prefers pnpm over npm"].kind == Kind.preference
    assert by_fact["VAT number is 12345678X"].kind == Kind.fiscal  # sensitive override
    assert all(m.learned_by == LearnedBy.imported for m in staged)
    assert all(m.status == Status.pending for m in staged)
