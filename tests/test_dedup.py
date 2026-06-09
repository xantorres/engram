from engram.core import dedup


def test_duplicate_paraphrase():
    result = dedup.compare("I prefer pnpm over npm", "Prefers pnpm over npm for installs")
    assert result == "duplicate"


def test_conflict_same_field_different_value():
    assert dedup.compare("My VAT number is 12345678A", "My VAT number is 99999999X") == "conflict"


def test_distinct_unrelated_facts():
    assert dedup.compare("I prefer pnpm", "I live in Cyprus") == "distinct"


def test_different_fields_same_token_class_are_distinct():
    # A company id and a personal id are different facts, not a conflict.
    assert dedup.compare("Company TIC is 12345678A", "Personal TIC is 87654321B") == "distinct"


def test_precision_tokens_extracted():
    tokens = dedup.precision_tokens("VAT 12345678A on 2026-06-09")
    assert "12345678A" in tokens
    assert "2026-06-09" in tokens
