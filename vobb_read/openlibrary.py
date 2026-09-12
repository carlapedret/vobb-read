"""Look up ISBN13s against the Open Library Books API.

https://openlibrary.org/dev/docs/api/books

We use the bulk `/api/books?bibkeys=ISBN:...&jscmd=details` endpoint (batched,
to keep request counts low and be polite to Open Library), and fall back to
the per-edition `/isbn/<isbn13>.json` endpoint for anything the batch call
didn't resolve.

If both of those come up empty for a book's ISBN13 and we also have its
original ISBN10 (the common case, since Goodreads' feed often only supplies
that -- see goodreads.py), we try ISBN10 as a bibkey too before giving up.
This isn't a language/format guess: ISBN10 and the derived ISBN13 name the
exact same physical edition, so if Open Library happens to have that
edition catalogued under only one of the two equivalent identifiers, we
still want to find it. Confirmed useful on a real shelf -- e.g. "Flesh" by
David Szalay resolved by ISBN10 after its derived ISBN13 came up empty
(too new to be catalogued under that key yet).

If Open Library still has no language data at that point -- either no
record at all, or a record missing the languages field -- we try Google
Books (googlebooks.py) as a second, independent real data source before
giving up. This is what recovers most of the "not found on Open Library"
books automatically instead of needing a manual override per book (see
config/overrides.json for the ones neither source has).

We NEVER infer language or format from title/publisher -- if neither
Open Library nor Google Books has usable data for a book,
is_target_language / is_physical come back as None ("unverifiable") rather
than a guess, and the
book is excluded from the final filtered list with a note explaining why,
so it can be checked by hand.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from . import googlebooks
from .models import Book, EnrichedBook

API_BOOKS_URL = "https://openlibrary.org/api/books"
API_ISBN_URL = "https://openlibrary.org/isbn/{isbn13}.json"
USER_AGENT = "vobb-read/0.1 (personal reading-list tool; https://github.com/carlapedret/vobb-read)"

BATCH_SIZE = 20  # Open Library docs allow up to ~100 bibkeys per call; stay conservative.

# Open Library language codes we accept. Edit this list to change which
# languages pass the filter -- e.g. add "fre" for French. Each code is
# Open Library's own (usually MARC/ISO 639-2), taken from the /languages/xxx
# key on an edition's "languages" field.
TARGET_LANGUAGES = ["eng", "spa"]

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


class GoogleBooksCache:
    """Tiny on-disk cache of isbn13 -> Google Books language code (or None).
    Separate file from OpenLibraryCache since it's a different, smaller
    lookup (language only, only tried when Open Library came up short)."""

    def __init__(self, path: Path | None):
        self.path = path
        self._data: dict = {}
        if self.path and self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def has(self, isbn13: str) -> bool:
        return isbn13 in self._data

    def get(self, isbn13: str) -> str | None:
        return self._data.get(isbn13)

    def set(self, isbn13: str, lang: str | None) -> None:
        self._data[isbn13] = lang

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
    """True = at least one of TARGET_LANGUAGES present, False = languages
    known but none of them match, None = no language data at all."""
    if not language_codes:
        return None
    return any(code in language_codes for code in TARGET_LANGUAGES)


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


def _resolve_after_batch_miss(book: Book, timeout: int, delay: float) -> dict | None:
    """Called only when the batch call for `book.isbn13` came up empty.
    Tries, in order: the single-edition endpoint for the ISBN13, then (if we
    have one) the book's original ISBN10 as a bibkey -- see module docstring
    for why ISBN10 is a legitimate fallback, not a guess. Returns the first
    hit, or None if nothing resolved."""
    try:
        details = _fetch_single(book.isbn13, timeout)
    except requests.RequestException:
        details = None
    time.sleep(delay)
    if details is not None:
        return details

    if book.isbn:
        try:
            alt = _fetch_batch([book.isbn], timeout)
        except requests.RequestException:
            alt = {}
        time.sleep(delay)
        details = alt.get(book.isbn)
        if details is not None:
            return details

    return None


def enrich_books(
    books: list[Book],
    cache_path: Path | None = None,
    google_cache_path: Path | None = None,
    delay: float = 0.5,
    timeout: int = 20,
) -> list[EnrichedBook]:
    """Look up every book's ISBN13 (falling back to its ISBN10 if the ISBN13
    doesn't resolve -- see module docstring) on Open Library and classify it.
    Falls back to Google Books for language only when Open Library has none.

    Books without an ISBN13 in the Goodreads feed at all are unverifiable.
    """
    cache = OpenLibraryCache(cache_path)
    google_cache = GoogleBooksCache(google_cache_path)

    to_fetch = [b for b in books if b.isbn13 and not cache.has(b.isbn13)]
    for i in range(0, len(to_fetch), BATCH_SIZE):
        batch_books = to_fetch[i : i + BATCH_SIZE]
        batch_isbn13 = [b.isbn13 for b in batch_books]
        results = _fetch_batch(batch_isbn13, timeout)
        for book in batch_books:
            details = results.get(book.isbn13)
            if details is None:
                details = _resolve_after_batch_miss(book, timeout, delay)
            cache.set(book.isbn13, details)
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
        lang_codes = extract_language_codes(details) if details is not None else []
        fmt = details.get("physical_format") if details is not None else None
        is_target_language = classify_language(lang_codes) if details is not None else None
        is_physical = classify_format(fmt) if details is not None else None

        google_lang = None
        if is_target_language is None:
            # No Open Library record at all, or a record with no usable
            # language field -- try Google Books before giving up.
            if google_cache.has(book.isbn13):
                google_lang = google_cache.get(book.isbn13)
            else:
                google_lang = googlebooks.fetch_language(book.isbn13, book.isbn, timeout)
                google_cache.set(book.isbn13, google_lang)
                time.sleep(delay)
            if google_lang:
                is_target_language = googlebooks.classify_language(google_lang)

        notes = []
        if details is None:
            reason = "not found on Open Library (tried ISBN13" + (
                " and ISBN10)" if book.isbn else ", no ISBN10 to fall back to)"
            )
            notes.append(f"ISBN13 {book.isbn13} {reason}")
        if is_target_language is None:
            notes.append("no language data from Open Library or Google Books")
        elif is_target_language is False:
            source = "Google Books" if google_lang and not lang_codes else "Open Library"
            lang_display = google_lang if (google_lang and not lang_codes) else ", ".join(lang_codes)
            notes.append(f"not English/Spanish ({source} language: {lang_display or 'unknown'})")
        if details is not None and is_physical is None:
            notes.append(f"could not classify physical_format={fmt!r}")

        enriched.append(
            EnrichedBook(
                book=book,
                language_codes=lang_codes or ([google_lang] if google_lang else []),
                physical_format=fmt,
                is_target_language=is_target_language,
                is_physical=is_physical,
                lookup_note="; ".join(notes),
            )
        )

    google_cache.save()
    return enriched
