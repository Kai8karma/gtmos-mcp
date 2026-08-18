"""Offline tests for gtmos.audit.fetch's retry/backoff layer
(_request_with_retry) and the 401/403 token-safety guarantee. No real
network calls — urllib.request.urlopen is monkeypatched throughout.
"""

from __future__ import annotations

import email.message
import io
import json
import urllib.error

from gtmos.audit import fetch


def _http_error(code: int, retry_after: str | None = None, body: bytes = b"{}") -> urllib.error.HTTPError:
    hdrs = email.message.Message()
    if retry_after is not None:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError("http://x", code, "err", hdrs, io.BytesIO(body))


def _ok(payload: dict) -> io.BytesIO:
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


def test_request_with_retry_retries_429_then_succeeds(monkeypatch):
    payload = {"results": [], "paging": {}}
    responses = iter([_http_error(429, retry_after="2"), _ok(payload)])

    def fake_urlopen(request, timeout=30):
        resp = next(responses)
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    sleeps = []
    result = fetch._request_with_retry("http://x", {}, sleeper=sleeps.append)

    assert result == payload
    assert sleeps == [2.0]  # Retry-After honored over the fixed schedule


def test_request_with_retry_honors_fixed_schedule_when_no_retry_after(monkeypatch):
    payload = {"results": []}
    responses = iter([_http_error(500), _http_error(503), _ok(payload)])

    def fake_urlopen(request, timeout=30):
        resp = next(responses)
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    sleeps = []
    result = fetch._request_with_retry("http://x", {}, sleeper=sleeps.append)

    assert result == payload
    assert sleeps == [1, 2]


def test_request_with_retry_caps_retry_after_at_30s(monkeypatch):
    payload = {"results": []}
    responses = iter([_http_error(429, retry_after="9999"), _ok(payload)])

    def fake_urlopen(request, timeout=30):
        resp = next(responses)
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    sleeps = []
    fetch._request_with_retry("http://x", {}, sleeper=sleeps.append)

    assert sleeps == [30.0]


def test_request_with_retry_exhausts_attempts_and_raises(monkeypatch):
    def fake_urlopen(request, timeout=30):
        raise _http_error(429)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    sleeps = []
    try:
        fetch._request_with_retry("http://x", {}, attempts=3, sleeper=sleeps.append)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "429" in str(exc)
    assert sleeps == [1, 2]


def test_request_with_retry_network_error_retries_then_raises(monkeypatch):
    def fake_urlopen(request, timeout=30):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    sleeps = []
    try:
        fetch._request_with_retry("http://x", {}, attempts=3, sleeper=sleeps.append)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "unreachable" in str(exc)
    assert sleeps == [1, 2]


def test_request_with_retry_401_raises_immediately_no_token_leak(monkeypatch):
    token = "super-secret-hubspot-token-value"
    calls = []

    def fake_urlopen(request, timeout=30):
        calls.append(request)
        raise _http_error(401, body=b'{"message": "invalid"}')

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    sleeps = []
    try:
        fetch._request_with_retry("http://x", {"Authorization": f"Bearer {token}"}, sleeper=sleeps.append)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        message = str(exc)
        assert token not in message
        assert "invalid" in message.lower() or "scope" in message.lower()

    # 401 is not transient — must not retry / sleep at all.
    assert len(calls) == 1
    assert sleeps == []


def test_request_with_retry_403_raises_immediately(monkeypatch):
    def fake_urlopen(request, timeout=30):
        raise _http_error(403)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    sleeps = []
    try:
        fetch._request_with_retry("http://x", {}, sleeper=sleeps.append)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "invalid" in str(exc) or "scope" in str(exc)
    assert sleeps == []


def test_fetch_hubspot_paginates_through_request_with_retry(monkeypatch):
    page1 = {"results": [{"email": "a@x.com"}], "paging": {"next": {"after": "cursor1"}}}
    page2 = {"results": [{"email": "b@x.com"}], "paging": {}}
    pages = iter([page1, page2])

    def fake_retry(url, headers, attempts=3, sleeper=None):
        return next(pages)

    monkeypatch.setattr(fetch, "_request_with_retry", fake_retry)
    contacts = fetch.fetch_hubspot("tok")

    assert len(contacts) == 2
    assert contacts[0]["email"] == "a@x.com"
    assert contacts[1]["email"] == "b@x.com"
