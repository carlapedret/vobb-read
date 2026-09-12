from pathlib import Path

from vobb_read.goodreads import isbn10_to_isbn13, parse_shelf_feed

FIXTURE = Path(__file__).parent / "fixtures" / "goodreads_shelf_sample.xml"


def test_parses_books_from_shelf_feed():
    books = parse_shelf_feed(FIXTURE.read_bytes())
    assert len(books) == 3

    piranesi = books[0]
    assert piranesi.title == "Piranesi"
    assert piranesi.author == "Susanna Clarke"
    assert piranesi.isbn == "1635575637"
    assert piranesi.isbn13 == "9781635575639"
    assert piranesi.book_id == "50202953"
    assert piranesi.goodreads_link == "https://www.goodreads.com/review/show/1234567890"


def test_empty_isbn_fields_become_none():
    books = parse_shelf_feed(FIXTURE.read_bytes())
    untranslated = books[1]
    assert untranslated.isbn is None
    assert untranslated.isbn13 is None
    assert untranslated.author == "Someone Else"


def test_garbage_input_raises():
    import pytest

    from vobb_read.goodreads import GoodreadsFetchError

    with pytest.raises(GoodreadsFetchError):
        parse_shelf_feed(b"not xml at all, just some \x00\x01 bytes")


def test_isbn10_to_isbn13_known_value():
    # Real-world example: Goodreads gave isbn=1804271055 and blank isbn13.
    assert isbn10_to_isbn13("1804271055") == "9781804271056"


def test_isbn10_to_isbn13_rejects_malformed_input():
    assert isbn10_to_isbn13("not an isbn") is None
    assert isbn10_to_isbn13("12345") is None


def test_isbn13_is_derived_when_feed_omits_it():
    books = parse_shelf_feed(FIXTURE.read_bytes())
    perfection = books[2]
    assert perfection.title == "Perfection"
    assert perfection.isbn == "1804271055"
    assert perfection.isbn13 == "9781804271056"
