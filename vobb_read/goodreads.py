"""Fetch and parse a Goodreads shelf RSS feed.

Goodreads' shelf RSS (https://www.goodreads.com/review/list_rss/<user_id>?key=<key>&shelf=<shelf>)
carries a handful of custom, non-namespaced elements per <item> in addition to the
standard RSS fields -- notably <isbn>, <isbn13>, <author_name> and <book_id>.
feedparser exposes those directly as attributes on each parsed entry.

In practice, real shelves often have <isbn13> empty/missing while <isbn>
(the 10-digit ISBN) is populated -- confirmed against a live 100-book shelf,
where every single item had isbn13 blank. Rather than lose those books
entirely (the whole pipeline keys off ISBN13), we derive the ISBN13
ourselves: ISBN-13 is not independent data, it's a fixed, standard,
mechanical transformation of ISBN-10 (prefix "978", recompute the checksum
digit) -- see isbn10_to_isbn13 below.
"""

from __future__ import annotations

import re

import feedparser
import requests

from .models import Book

USER_AGENT = "vobb-read/0.1 (personal reading-list tool; https://github.com/carlapedret/vobb-read)"


class GoodreadsFetchError(RuntimeError):
    pass


def fetch_shelf(rss_url: str, timeout: int = 20) -> list[Book]:
    """Download the shelf RSS feed and parse it into Book records.

    Raises GoodreadsFetchError on network/HTTP problems. Parsing problems
    (malformed feed) surface via feedparser's own `bozo` flag, which we
    check inside parse_shelf_feed.
    """
    try:
        resp = requests.get(rss_url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise GoodreadsFetchError(f"Could not fetch Goodreads RSS feed: {exc}") from exc
    return parse_shelf_feed(resp.content)


def parse_shelf_feed(raw: bytes | str) -> list[Book]:
    """Parse raw RSS bytes/text (already downloaded) into Book records."""
    parsed = feedparser.parse(raw)

    if not parsed.entries and getattr(parsed, "bozo", 0):
        # feedparser sets bozo=1 for anything from "well-formed but odd" to
        # "not XML at all" -- only treat it as fatal when we also got zero
        # entries, since Goodreads feeds sometimes trip minor bozo warnings
        # while still parsing fine.
        exc = getattr(parsed, "bozo_exception", None)
        raise GoodreadsFetchError(f"Feed did not parse as RSS/XML: {exc}")

    books: list[Book] = []
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        author = (entry.get("author_name") or "").strip()
        isbn = _clean_isbn(entry.get("isbn"))
        isbn13 = _clean_isbn(entry.get("isbn13")) or (isbn10_to_isbn13(isbn) if isbn else None)
        book_id = str(entry.get("book_id") or entry.get("id") or "").strip()
        link = entry.get("link")

        if not title:
            # Skip entries with no title at all -- not a real book row.
            continue

        books.append(
            Book(
                book_id=book_id,
                title=title,
                author=author or "Unknown author",
                isbn=isbn,
                isbn13=isbn13,
                goodreads_link=link,
            )
        )
    return books


def _clean_isbn(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def isbn10_to_isbn13(isbn10: str) -> str | None:
    """Convert a 10-digit ISBN to its 13-digit form (prefix "978", recompute
    the checksum digit), per the standard ISBN-13 algorithm. Returns None if
    `isbn10` isn't a well-formed 10-digit ISBN."""
    digits = re.sub(r"[^0-9Xx]", "", isbn10)
    if len(digits) != 10:
        return None
    core9 = digits[:9]  # drop ISBN-10's own check digit (10th char); it's not part of ISBN-13
    if not core9.isdigit():
        return None
    prefixed = "978" + core9
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(prefixed))
    check_digit = (10 - (total % 10)) % 10
    return prefixed + str(check_digit)
