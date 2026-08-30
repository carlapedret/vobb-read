"""Look up ISBN13s against the Open Library Books API.

https://openlibrary.org/dev/docs/api/books

We use the bulk `/api/books?bibkeys=ISBN:...&jscmd=details` endpoint (batched,
to keep request counts low and be polite to Open Library), and fall back to
the per-edition `/isbn/<isbn13>.json` endpoint for anything the batch call
didn't resolve.

We NEVER infer language or format from title/publisher -- if Open Library
has no usable data for a book, is_english / is_physical come back as None
("unverifiable") rather than a guess, and the book is excluded from the
final filtered list with a note explaining why, so it can be checked by hand.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from .models import Book, EnrichedBook

API_BOOKS_URL = "https://openlibrary.org/api/books"
API_ISBN_URL = "https://openlibrary.org/isbn/{isbn13}.json"
USER_AGENT = "vobb-read/0.1 (personal reading-list tool; https://github.com/carlapedret/vobb-read)"

BATCH_SIZE = 20  # Open Library docs allow up to ~100 bibkeys per call; stay conservative.

# Format-string classification. Open Library's `physical_format` field is a
# free-text string from the publisher record, not a controlled vocabulary,
# so this is necessarily a keyword heuristic. Anything that matches neither
# list is treated as unknown (unverifiable), never guessed.
NON_PHYSICAL_KEYWORDS = [
    "ebook",
    "e-book",
    "electronic",
    "kindle",
    "audio",
    "audiobook",
    "mp3",
    "audible",
    "playaway",
    "downloadable",
    "digital",
    "streaming",
]
PHYSICAL_KEYWORDS = [
    "paperback",
    "hardcover",
    "hardback",
    "hard cover",
    "board book",
    "spiral",
    "library binding",
    "mass market",
    "trade paper",
    "pamphlet",
    "leather bound",
    "ring-bound",
    "plastic comb",
    "print",
    "book",
]


class OpenLibraryCache:
    """A tiny on-disk JSON cache of isbn13 -> details (or None if unresolved).

    Keeps re-runs fast and avoids hammering Open Library for books we've
    already looked up. Never committed to the repo (see .gitignore).
    """

    def __init__(self, path: Path | None):
        self.path = path
        self._data: dict = {}
        if self.path and self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, isbn13: str):
        entry = self._data.get(isbn13)
        return entry["details"] if entry else None

    def has(self, isbn13: str) -> bool:
        return isbn13 in self._data

    def set(self, isbn13: str, details: dict | None):
        self._data[isbn13] = {"details": details}

    def save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))


def classify_format(physical_format: str | None) -> bool | None:
    """True = physical book, False = ebook/audio/etc, None = can't tell."""
    if not physical_format or not physical_format.strip():
        return None
    low = physical_format.lower()
    if any(k in low for k in NON_PHYSICAL_KEYWORDS):
        return False
    if any(k in low for k in PHYSICAL_KEYWORDS):
        return True
    return None


def classify_language(language_codes: list[str]) -> bool | None:
    """True = English present, False = languages known but not English, None = no data."""
    if not language_codes:
        return None
    return "eng" in language_codes


def extract_language_codes(details: dict) -> list[str]:
    codes = []
    for lang in details.get("languages", []) or []:
        key = lang.get("key", "")  # e.g. "/languages/eng"
        code = key.rsplit("/", 1)[-1]
        if code:
            codes.append(code)
    return codes


def _fetch_batch(isbn13_list: list[str], timeout: int) -> dict[str, dict | None]:
    if not isbn13_list:
        return {}
    bibkeys = ",".join(f"ISBN:{isbn}" for isbn in isbn13_list)
    resp = requests.get(
        API_BOOKS_URL,
        params={"bibkeys": bibkeys, "format": "json", "jscmd": "details"},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    result: dict[str, dict | None] = {isbn: None for isbn in isbn13_list}
    for isbn in isbn13_list:
        entry = payload.get(f"ISBN:{isbn}")
        if entry:
            result[isbn] = entry.get("details", {})
    return result


def _fetch_single(isbn13: str, timeout: int) -> dict | None:
    resp = requests.get(
        API_ISBN_URL.format(isbn13=isbn13),
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def enrich_books(
    books: list[Book],
    cache_path: Path | None = None,
    delay: float = 0.5,
    timeout: int = 20,
) -> list[EnrichedBook]:
    """Look up every book's ISBN13 on Open Library and classify it.

    Books without an ISBN13 in the Goodreads feed are returned unverifiable
    (we do not fall back to ISBN10 or title search for language/format --
    the task calls for ISBN13 specifically).
    """
    cache = OpenLibraryCache(cache_path)

    to_fetch = [b.isbn13 for b in books if b.isbn13 and not cache.has(b.isbn13)]
    for i in range(0, len(to_fetch), BATCH_SIZE):
        batch = to_fetch[i : i + BATCH_SIZE]
        results = _fetch_batch(batch, timeout)
        for isbn13, details in results.items():
            if details is None:
                # Not resolved by the batch call -- try the single-edition
                # endpoint before giving up, then be polite either way.
                try:
                    single = _fetch_single(isbn13, timeout)
                except requests.RequestException:
                    single = None
                cache.set(isbn13, single)
                time.sleep(delay)
            else:
                cache.set(isbn13, details)
        time.sleep(delay)
    cache.save()

    enriched: list[EnrichedBook] = []
    for book in books:
        if not book.isbn13:
            enriched.append(
                EnrichedBook(
                    book=book,
                    lookup_note="No ISBN13 in Goodreads feed -- cannot verify via Open Library.",
                )
            )
            continue

        details = cache.get(book.isbn13)
        if details is None:
            enriched.append(
                EnrichedBook(
                    book=book,
                    lookup_note=f"ISBN13 {book.isbn13} not found on Open Library.",
                )
            )
            continue

        lang_codes = extract_language_codes(details)
        fmt = details.get("physical_format")
        is_english = classify_language(lang_codes)
        is_physical = classify_format(fmt)

        notes = []
        if is_english is None:
            notes.append("Open Library has no language data for this edition")
        if is_physical is None:
            notes.append(f"could not classify physical_format={fmt!r}")

        enriched.append(
            EnrichedBook(
                book=book,
                language_codes=lang_codes,
                physical_format=fmt,
                is_english=is_english,
                is_physical=is_physical,
                lookup_note="; ".join(notes),
            )
        )

    return enriched
