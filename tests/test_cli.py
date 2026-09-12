import json

from vobb_read.cli import _apply_overrides, _load_overrides
from vobb_read.models import Book, EnrichedBook


def test_load_overrides_missing_file(tmp_path):
    assert _load_overrides(tmp_path / "does_not_exist.json") == {}


def test_load_overrides_reads_isbn13_map(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"isbn13": {"9781982122799": "Flesh -- confirmed by hand"}}))
    assert _load_overrides(path) == {"9781982122799": "Flesh -- confirmed by hand"}


def test_apply_overrides_forces_book_into_filtered_list():
    book = Book(book_id="1", title="Flesh", author="David Szalay", isbn="198212279X", isbn13="9781982122799")
    enriched = EnrichedBook(book=book, lookup_note="ISBN13 9781982122799 not found on Open Library.")
    assert enriched.passes_filter is False  # unverifiable before the override

    _apply_overrides([enriched], {"9781982122799": "confirmed by hand"})

    assert enriched.passes_filter is True
    assert enriched.is_target_language is True
    assert enriched.is_physical is True
    assert "manual override" in enriched.lookup_note
    assert "confirmed by hand" in enriched.lookup_note


def test_apply_overrides_leaves_non_matching_books_untouched():
    book = Book(book_id="2", title="Other Book", author="Someone", isbn=None, isbn13="1111111111111")
    enriched = EnrichedBook(book=book)
    _apply_overrides([enriched], {"9781982122799": "unrelated"})
    assert enriched.is_target_language is None
    assert enriched.is_physical is None
