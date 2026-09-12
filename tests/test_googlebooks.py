from vobb_read.googlebooks import classify_language, fetch_language


def test_classify_language():
    assert classify_language("en") is True
    assert classify_language("es") is True
    assert classify_language("de") is False
    assert classify_language(None) is None
    assert classify_language("") is None


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def test_fetch_language_by_isbn13(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert params["q"] == "isbn:9781234567897"
        return _FakeResponse({"items": [{"volumeInfo": {"language": "en"}}]})

    monkeypatch.setattr("vobb_read.googlebooks.requests.get", fake_get)
    assert fetch_language("9781234567897", "1234567890") == "en"


def test_fetch_language_falls_back_to_isbn10(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(params["q"])
        if "9781234567897" in params["q"]:
            return _FakeResponse({"items": []})  # nothing under ISBN13
        return _FakeResponse({"items": [{"volumeInfo": {"language": "es"}}]})

    monkeypatch.setattr("vobb_read.googlebooks.requests.get", fake_get)
    assert fetch_language("9781234567897", "1234567890") == "es"
    assert calls == ["isbn:9781234567897", "isbn:1234567890"]


def test_fetch_language_nothing_found(monkeypatch):
    monkeypatch.setattr(
        "vobb_read.googlebooks.requests.get",
        lambda *a, **k: _FakeResponse({"items": []}),
    )
    assert fetch_language("9781234567897", "1234567890") is None


def test_fetch_language_no_isbn10_to_try(monkeypatch):
    monkeypatch.setattr(
        "vobb_read.googlebooks.requests.get",
        lambda *a, **k: _FakeResponse({"items": []}),
    )
    assert fetch_language("9781234567897", None) is None


def test_fetch_language_request_error_is_non_fatal(monkeypatch):
    import requests

    def fake_get(*a, **k):
        raise requests.RequestException("boom")

    monkeypatch.setattr("vobb_read.googlebooks.requests.get", fake_get)
    assert fetch_language("9781234567897", "1234567890") is None
