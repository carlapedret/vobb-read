from pathlib import Path

from vobb_read.goodreads import parse_shelf_feed

FIXTURE = Path(__file__).parent / "fixtures" / "goodreads_shelf_sample.xml"


def test_parses_books_from_shelf_feed():
    books = parse_shelf_feed(FIXTURE.read_bytes())
    assert len(books) == 2

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
