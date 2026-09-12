"""Shared data structures used across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Book:
    """A single item pulled from the Goodreads shelf RSS feed."""

    book_id: str
    title: str
    author: str
    isbn: str | None
    isbn13: str | None
    goodreads_link: str | None = None

    def display(self) -> str:
        return f"{self.title} — {self.author}"


@dataclass
class EnrichedBook:
    """A Book plus what Open Library told us about it."""

    book: Book
    language_codes: list[str] = field(default_factory=list)
    physical_format: str | None = None
    is_target_language: bool | None = None  # None = could not determine. "Target" = openlibrary.TARGET_LANGUAGES
    is_physical: bool | None = None  # None = could not determine
    lookup_note: str = ""  # e.g. "not found on Open Library", "no language data"

    @property
    def passes_filter(self) -> bool:
        """Language stays strict (must be a confirmed TARGET_LANGUAGES hit --
        None/unknown does NOT pass). Format is deliberately looser: only an
        *explicit* ebook/audio classification (is_physical is False) drops a
        book; unknown format (None, e.g. Open Library has no physical_format
        for that edition) passes through rather than being excluded, per an
        explicit user decision (2026-09-12) trading a little precision for
        a lot more coverage -- real physical books were getting dropped
        purely because Open Library's data was incomplete, not because
        there was any actual evidence they were ebooks."""
        return self.is_target_language is True and self.is_physical is not False

    @property
    def unverifiable(self) -> bool:
        return self.is_target_language is None or self.is_physical is None


@dataclass
class BranchHolding:
    branch_id: str
    branch_label: str
    matched_name: str  # which name variant was actually seen in the catalog text
    status: str  # "available" | "on_loan" | "reference_only" | "unknown"
    raw_text: str = ""  # original snippet, for debugging/manual review


@dataclass
class CatalogResult:
    enriched: EnrichedBook
    found: bool = False
    matched_by: str = ""  # "isbn13" | "title_author" | ""
    holdings: list[BranchHolding] = field(default_factory=list)
    error: str = ""
