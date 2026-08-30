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
    is_english: bool | None = None  # None = could not determine
    is_physical: bool | None = None  # None = could not determine
    lookup_note: str = ""  # e.g. "not found on Open Library", "no language data"

    @property
    def passes_filter(self) -> bool:
        return self.is_english is True and self.is_physical is True

    @property
    def unverifiable(self) -> bool:
        return self.is_english is None or self.is_physical is None


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
