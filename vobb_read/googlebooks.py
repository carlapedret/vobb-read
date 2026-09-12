"""A small fallback against the Google Books API, for language only.

Used only when Open Library has no usable language data for a book -- no
record at all under either ISBN, or a record that's missing the languages
field. This is a second real, independent data source, not a guess: Google
Books catalogs the same published editions and very often has a language
code even for books Open Library hasn't indexed yet (common for smaller
presses, translations, and very recent releases -- confirmed against real
excluded books like "Etna" by Paul Yoon, 2026-09-12).

We only ever pull `language` from here, never a physical/ebook distinction --
Google Books' schema has no real equivalent of Open Library's
physical_format, and the project's format filter is already lenient for
unknown formats (see models.py's passes_filter), so there's nothing useful
to gain by guessing at that here.

https://developers.google.com/books/docs/v1/using#WorkingVolumes
No API key is required for this volume of personal, low-rate lookups.
"""

from __future__ import annotations

import requests

API_URL = "https://www.googleapis.com/books/v1/volumes"
USER_AGENT = "vobb-read/0.1 (personal reading-list tool; https://github.com/carlapedret/vobb-read)"

# Google Books reports language as an ISO 639-1 (2-letter) code. Keep this in
# lockstep with openlibrary.TARGET_LANGUAGES (Open Library/MARC 3-letter
# codes) -- add the matching 2-letter code here whenever you add a language
# there (e.g. French: "fre" there, "fr" here).
TARGET_LANGUAGES = ["en", "es"]


def classify_language(code: str | None) -> bool | None:
    """True = one of TARGET_LANGUAGES, False = a real code that isn't one of
    them, None = no code at all (Google Books didn't have it either)."""
    if not code:
        return None
    return code in TARGET_LANGUAGES


def _fetch_by_isbn(isbn: str, timeout: int) -> str | None:
    try:
        resp = requests.get(
            API_URL,
            params={"q": f"isbn:{isbn}"},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException:
        return None
    for item in payload.get("items") or []:
        lang = item.get("volumeInfo", {}).get("language")
        if lang:
            return lang
    return None


def fetch_language(isbn13: str | None, isbn10: str | None, timeout: int = 20) -> str | None:
    """Try ISBN13 first, then ISBN10 (same reasoning as Open Library's own
    ISBN10 fallback -- they name the same physical edition, so either being
    catalogued is equally good evidence). Returns Google's raw language code
    (e.g. "en", "es", "de"), or None if neither ISBN turned up anything."""
    if isbn13:
        lang = _fetch_by_isbn(isbn13, timeout)
        if lang:
            return lang
    if isbn10:
        lang = _fetch_by_isbn(isbn10, timeout)
        if lang:
            return lang
    return None
