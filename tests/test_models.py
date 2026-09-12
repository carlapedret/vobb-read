from vobb_read.models import Book, EnrichedBook

_BOOK = Book(book_id="1", title="T", author="A", isbn=None, isbn13="9780000000000")


def _enriched(is_target_language, is_physical):
    return EnrichedBook(book=_BOOK, is_target_language=is_target_language, is_physical=is_physical)


def test_passes_filter_language_strict_format_loose():
    # Language: must be a confirmed hit -- True passes, False/None both fail.
    assert _enriched(True, True).passes_filter is True
    assert _enriched(False, True).passes_filter is False
    assert _enriched(None, True).passes_filter is False

    # Format: only an explicit False (confirmed ebook/audio) fails it --
    # unknown format (None) passes as long as the language is confirmed.
    assert _enriched(True, False).passes_filter is False
    assert _enriched(True, None).passes_filter is True
