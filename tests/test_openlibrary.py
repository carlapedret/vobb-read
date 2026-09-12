from vobb_read.models import Book
from vobb_read.openlibrary import (
    classify_format,
    classify_language,
    enrich_books,
    extract_language_codes,
)


def test_classify_format_physical():
    assert classify_format("Paperback") is True
    assert classify_format("Hardcover") is True
    assert classify_format("Mass Market Paperback") is True


def test_classify_format_non_physical():
    assert classify_format("Kindle Edition") is False
    assert classify_format("Audio CD") is False
    assert classify_format("Audible Audio") is False
    assert classify_format("ebook") is False


def test_classify_format_unknown():
    assert classify_format(None) is None
    assert classify_format("") is None
    assert classify_format("Zorbnix Format 3000") is None


def test_classify_language():
    assert classify_language(["eng"]) is True
    assert classify_language(["spa"]) is True
    assert classify_language(["ger", "eng"]) is True
    assert classify_language(["ger"]) is False
    assert classify_language([]) is None


def test_extract_language_codes():
    details = {"languages": [{"key": "/languages/eng"}, {"key": "/languages/fre"}]}
    assert extract_language_codes(details) == ["eng", "fre"]
    assert extract_language_codes({}) == []


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def test_enrich_books_filters_correctly(monkeypatch, tmp_path):
    books = [
        Book(book_id="1", title="English Paperback", author="A", isbn=None, isbn13="1111111111111"),
        Book(book_id="2", title="German Paperback", author="B", isbn=None, isbn13="2222222222222"),
        Book(book_id="3", title="English Kindle", author="C", isbn=None, isbn13="3333333333333"),
        Book(book_id="4", title="No ISBN13", author="D", isbn=None, isbn13=None),
        Book(book_id="5", title="Not On Open Library", author="E", isbn="5050505050", isbn13="5555555555555"),
        Book(book_id="6", title="Spanish Paperback", author="F", isbn=None, isbn13="6666666666666"),
        Book(book_id="7", title="English Unknown Format", author="G", isbn=None, isbn13="7777777777777"),
    ]

    batch_payload = {
        "ISBN:1111111111111": {"details": {"languages": [{"key": "/languages/eng"}], "physical_format": "Paperback"}},
        "ISBN:2222222222222": {"details": {"languages": [{"key": "/languages/ger"}], "physical_format": "Paperback"}},
        "ISBN:3333333333333": {"details": {"languages": [{"key": "/languages/eng"}], "physical_format": "Kindle Edition"}},
        "ISBN:6666666666666": {"details": {"languages": [{"key": "/languages/spa"}], "physical_format": "Paperback"}},
        # no physical_format at all -- Open Library just doesn't have it for this edition
        "ISBN:7777777777777": {"details": {"languages": [{"key": "/languages/eng"}]}},
        # 5555555555555 (isbn13) intentionally absent -> triggers single-lookup fallback (also 404) ->
        # then the ISBN10 fallback (5050505050) -> that one DOES resolve.
        "ISBN:5050505050": {"details": {"languages": [{"key": "/languages/eng"}], "physical_format": "Paperback"}},
    }

    def fake_get(url, params=None, headers=None, timeout=None):
        if "api/books" in url:
            bibkeys = (params or {}).get("bibkeys", "")
            hit = {k: v for k, v in batch_payload.items() if k in bibkeys}
            return _FakeResponse(hit)
        # single isbn13 endpoint fallback -- never has anything in this test
        return _FakeResponse({}, status_code=404)

    def fake_get_dispatch(url, params=None, headers=None, timeout=None):
        # vobb_read.openlibrary and vobb_read.googlebooks both do `import
        # requests`, which is the same module object either way -- so there's
        # only one `requests.get` to patch, dispatched here by URL. None of
        # these 7 books should ever need the Google Books fallback (each
        # already resolves via Open Library, or has no ISBN13 at all).
        if "googleapis.com" in url:
            raise AssertionError("should not call Google Books here")
        return fake_get(url, params=params, headers=headers, timeout=timeout)

    monkeypatch.setattr("vobb_read.openlibrary.requests.get", fake_get_dispatch)

    enriched = enrich_books(books, cache_path=tmp_path / "cache.json", google_cache_path=tmp_path / "google.json", delay=0)
    by_id = {e.book.book_id: e for e in enriched}

    assert by_id["1"].passes_filter is True
    assert by_id["2"].passes_filter is False
    assert by_id["2"].is_target_language is False
    assert by_id["3"].passes_filter is False
    assert by_id["3"].is_physical is False
    assert by_id["4"].passes_filter is False
    assert by_id["4"].unverifiable is True
    assert by_id["5"].passes_filter is True  # resolved via the ISBN10 fallback
    assert by_id["6"].passes_filter is True  # Spanish now passes too
    assert by_id["7"].passes_filter is True  # confirmed English + unknown format now passes (format filter loosened)
    assert by_id["7"].is_physical is None


def test_enrich_books_falls_back_to_google_books_when_open_library_has_nothing(monkeypatch, tmp_path):
    books = [
        # Not on Open Library at all, but Google Books has it in English -> recovered automatically.
        Book(book_id="1", title="Etna", author="Paul Yoon", isbn="1668020823", isbn13="9781668020821"),
        # Open Library has a record but no languages field; Google Books resolves it to German -> still excluded.
        Book(book_id="2", title="Some German Book", author="X", isbn=None, isbn13="2222222222222"),
        # Neither source has anything -> stays unverifiable, not a guess.
        Book(book_id="3", title="Totally Obscure", author="Y", isbn="3030303030", isbn13="3333333333333"),
    ]

    def fake_get(url, params=None, headers=None, timeout=None):
        # vobb_read.openlibrary and vobb_read.googlebooks both do `import
        # requests`, which is the same module object -- there's only one
        # `requests.get` to patch, dispatched here by URL/host.
        if "googleapis.com" in url:
            q = (params or {}).get("q", "")
            if "9781668020821" in q or "1668020823" in q:
                return _FakeResponse({"items": [{"volumeInfo": {"language": "en"}}]})
            if "2222222222222" in q:
                return _FakeResponse({"items": [{"volumeInfo": {"language": "de"}}]})
            return _FakeResponse({"items": []})
        if "api/books" in url:
            bibkeys = (params or {}).get("bibkeys", "")
            if "2222222222222" in bibkeys:
                return _FakeResponse({"ISBN:2222222222222": {"details": {}}})  # record exists, no languages field
            return _FakeResponse({})
        return _FakeResponse({}, status_code=404)  # single-edition OL endpoint fallback

    monkeypatch.setattr("vobb_read.openlibrary.requests.get", fake_get)

    enriched = enrich_books(books, cache_path=tmp_path / "cache.json", google_cache_path=tmp_path / "google.json", delay=0)
    by_id = {e.book.book_id: e for e in enriched}

    assert by_id["1"].passes_filter is True
    assert by_id["1"].is_target_language is True
    assert "Google Books" not in by_id["1"].lookup_note  # only annotated on exclusion, not success

    assert by_id["2"].passes_filter is False
    assert by_id["2"].is_target_language is False
    assert "Google Books" in by_id["2"].lookup_note

    assert by_id["3"].passes_filter is False
    assert by_id["3"].is_target_language is None
    assert "no language data from Open Library or Google Books" in by_id["3"].lookup_note
