from vobb_read.models import Book, BranchHolding, CatalogResult, EnrichedBook
from vobb_read.report import generate_markdown


def _enriched(title, author, isbn13="9780000000000"):
    book = Book(book_id=title, title=title, author=author, isbn=None, isbn13=isbn13)
    return EnrichedBook(book=book, language_codes=["eng"], physical_format="Paperback", is_english=True, is_physical=True)


def test_report_puts_zero_match_books_at_bottom():
    matched = CatalogResult(
        enriched=_enriched("Held Book", "Author A"),
        found=True,
        matched_by="isbn13",
        holdings=[
            BranchHolding(
                branch_id="zlb", branch_label="ZLB Zentral- und Landesbibliothek", matched_name="ZLB", status="available"
            )
        ],
    )
    not_found = CatalogResult(enriched=_enriched("Missing Book", "Author B"), found=False)
    found_elsewhere = CatalogResult(enriched=_enriched("Found Elsewhere", "Author C"), found=True, matched_by="title_author")

    md = generate_markdown([not_found, found_elsewhere, matched])

    held_idx = md.index("## Held at a target branch")
    not_held_idx = md.index("## Not held at any target branch")
    assert held_idx < not_held_idx

    held_section = md[held_idx:not_held_idx]
    not_held_section = md[not_held_idx:]

    assert "Held Book" in held_section
    assert "Missing Book" not in held_section
    assert "Missing Book" in not_held_section
    assert "Found Elsewhere" in not_held_section


def test_report_includes_excluded_appendix():
    excluded_book = Book(book_id="x", title="Excluded Book", author="Author X", isbn=None, isbn13="9781111111111")
    excluded = EnrichedBook(excluded_book, is_english=False, language_codes=["ger"], lookup_note="not English (language: ger)")

    md = generate_markdown([], excluded=[excluded])
    assert "Appendix: excluded" in md
    assert "Excluded Book" in md
    assert "not English" in md
