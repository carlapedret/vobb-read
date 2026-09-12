from vobb_read.models import Book, BranchHolding, CatalogResult, EnrichedBook
from vobb_read.report import generate_html, generate_markdown


def _enriched(title, author, isbn13="9780000000000"):
    book = Book(book_id=title, title=title, author=author, isbn=None, isbn13=isbn13)
    return EnrichedBook(
        book=book, language_codes=["eng"], physical_format="Paperback", is_target_language=True, is_physical=True
    )


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
    excluded = EnrichedBook(
        excluded_book, is_target_language=False, language_codes=["ger"], lookup_note="not English/Spanish (language: ger)"
    )

    md = generate_markdown([], excluded=[excluded])
    assert "Appendix: excluded" in md
    assert "Excluded Book" in md
    assert "not English/Spanish" in md


def test_html_report_puts_zero_match_books_at_bottom():
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

    html = generate_html([not_found, matched])

    held_idx = html.index("Held at a target branch")
    not_held_idx = html.index("Not held at any target branch")
    assert held_idx < not_held_idx

    held_section = html[held_idx:not_held_idx]
    not_held_section = html[not_held_idx:]
    assert "Held Book" in held_section
    assert "Missing Book" not in held_section
    assert "Missing Book" in not_held_section


def test_html_report_includes_status_badge_and_escapes_html():
    matched = CatalogResult(
        enriched=_enriched("Piranesi & <Co>", "Susanna Clarke"),
        found=True,
        matched_by="isbn13",
        holdings=[
            BranchHolding(
                branch_id="zlb", branch_label="ZLB Zentral- und Landesbibliothek", matched_name="ZLB", status="available"
            )
        ],
    )
    html = generate_html([matched])
    assert "Available now" in html
    assert "status-available" in html
    assert "Piranesi &amp; &lt;Co&gt;" in html
    assert "<Co>" not in html  # must be escaped, not injected raw


def test_html_report_includes_excluded_appendix():
    excluded_book = Book(book_id="x", title="Excluded Book", author="Author X", isbn=None, isbn13="9781111111111")
    excluded = EnrichedBook(
        excluded_book, is_target_language=False, language_codes=["ger"], lookup_note="not English/Spanish (language: ger)"
    )
    html = generate_html([], excluded=[excluded])
    assert "excluded before the catalog check" in html
    assert "Excluded Book" in html
    assert "not English/Spanish" in html


def test_html_report_is_self_contained_no_external_resources():
    html = generate_html([])
    assert "http://" not in html
    assert "https://" not in html
