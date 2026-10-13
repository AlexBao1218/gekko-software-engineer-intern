"""Exercise the OCR boundary without relying on the vendor's random failures."""

import asyncio
import importlib
import json
from dataclasses import FrozenInstanceError

import httpx
import pytest


VALID_FIELDS = {
    "id_number": "TEST-ID-123456",
    "full_name": "Test Applicant 0042",
    "dob": "1980-02-29",
    "issue_date": "2024-01-01",
    "confidence": 0.93,
}


def api():
    return importlib.import_module("app.ocr_client")


async def parse_response(response, **settings):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as http:
        module = api()
        return await module.OCRClient(module.OCRSettings(**settings), http).parse(
            "CASE-42", "scan_42.jpg"
        )


async def test_valid_response_uses_contract_and_configured_phase_timeouts():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=VALID_FIELDS)

    module = api()
    settings = module.OCRSettings(url="https://ocr.example/parse", timeout_seconds=0.3)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await module.OCRClient(settings, http).parse("CASE-42", "scan_42.jpg")
        assert not http.is_closed
    assert result.fields == VALID_FIELDS
    assert result.code is None
    assert result.reason is None
    assert result.retryable is False
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "https://ocr.example/parse"
    assert json.loads(requests[0].content) == {"case_id": "CASE-42", "image_ref": "scan_42.jpg"}
    assert requests[0].extensions["timeout"] == {
        "connect": 0.3, "read": 0.3, "write": 0.3, "pool": 0.3
    }


@pytest.mark.parametrize("confidence", [0, 1, 0.125])
async def test_confidence_numeric_boundaries_are_valid(confidence):
    result = await parse_response(httpx.Response(200, json={**VALID_FIELDS, "confidence": confidence}))
    assert result.fields["confidence"] == confidence


@pytest.mark.parametrize("key,value", [
    ("id_number", "REAL-ID-123456"), ("id_number", "TEST-ID-"), ("id_number", 123),
    ("full_name", " \t"), ("full_name", None),
    ("dob", "2001-02-29"), ("dob", "1980-2-29"), ("dob", "19800229"),
    ("dob", "1980-02-29T00:00:00"), ("issue_date", "2024-04-31"),
    ("issue_date", "2024-01-01\n"), ("issue_date", 20240101),
    ("confidence", True), ("confidence", "0.93"), ("confidence", None),
    ("confidence", -0.01), ("confidence", 1.01),
])
async def test_invalid_success_schema_is_permanent(key, value):
    result = await parse_response(httpx.Response(200, json={**VALID_FIELDS, key: value}))
    assert result.fields is None
    assert result.code == "invalid_response"
    assert result.retryable is False


@pytest.mark.parametrize("key", list(VALID_FIELDS))
async def test_missing_required_fields_are_permanent(key):
    fields = {k: v for k, v in VALID_FIELDS.items() if k != key}
    result = await parse_response(httpx.Response(200, json=fields))
    assert result.code == "invalid_response"
    assert result.retryable is False


@pytest.mark.parametrize("body", [b"not json", b"null", b"[]", b'"text"', b"\xff", b'{"confidence": NaN}'])
async def test_unreadable_success_body_is_permanent(body):
    result = await parse_response(httpx.Response(200, content=body))
    assert result.fields is None
    assert result.code == "invalid_response"
    assert result.retryable is False


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), float("-inf")])
async def test_nonfinite_confidence_is_permanent(confidence):
    body = json.dumps({**VALID_FIELDS, "confidence": confidence}).encode()
    result = await parse_response(httpx.Response(200, content=body))
    assert result.code == "invalid_response"


async def test_success_keeps_only_contract_fields_and_preserves_source_values():
    fields = {**VALID_FIELDS, "full_name": "  Test Applicant 0042  ", "traceback": "private"}
    result = await parse_response(httpx.Response(200, json=fields))
    assert result.fields == {**VALID_FIELDS, "full_name": "  Test Applicant 0042  "}


@pytest.mark.parametrize("key,value", [
    ("id_number", "TEST-ID-\ud800"), ("full_name", "\ud800"),
    ("id_number", "TEST-ID-123\u0000"), ("full_name", "Applicant\u0000"),
    ("id_number", "TEST-ID-123\n"), ("full_name", "Applicant\u0085"),
])
async def test_unsafe_text_from_raw_json_is_rejected_before_persistence(key, value):
    # Escaped surrogates are legal JSON input, but cannot be encoded in a UTF-8 API response.
    body = json.dumps({**VALID_FIELDS, key: value}, ensure_ascii=True).encode("ascii")
    result = await parse_response(httpx.Response(200, content=body))
    assert result.code == "invalid_response"
    assert result.fields is None
    assert result.retryable is False


async def test_legitimate_unicode_text_is_preserved():
    fields = {**VALID_FIELDS, "id_number": "TEST-ID-測試123", "full_name": "李曉明 José"}
    result = await parse_response(httpx.Response(200, json=fields))
    assert result.fields == fields


@pytest.mark.parametrize("status,retryable", [(400, False), (422, False), (429, True), (500, True), (503, True), (404, False), (302, False)])
async def test_http_classification_does_not_retry_inside_client(status, retryable):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": "vendor reason"})

    module = api()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await module.OCRClient(module.OCRSettings(), http).parse("42", "scan")
    assert calls == 1
    assert result.fields is None
    assert result.code == f"http_{status}"
    assert result.retryable is retryable


@pytest.mark.parametrize("status,key", [(400, "error"), (422, "error"), (422, "reason"), (422, "detail")])
async def test_permanent_vendor_reason_is_preserved(status, key):
    reason = "cannot read identity-card fields (scan quality)"
    result = await parse_response(httpx.Response(status, json={key: reason}))
    assert result.reason == reason


@pytest.mark.parametrize("body", [b"<html>private traceback</html>", b'{"error":{"secret":"private"}}', b'{"error": ["private"]}', b'{"error": null}', b"\xff"])
async def test_unstructured_error_bodies_do_not_leak_into_reason(body):
    result = await parse_response(httpx.Response(422, content=body))
    assert result.code == "http_422"
    assert result.reason
    assert "private" not in result.reason
    json.dumps(result.reason, allow_nan=False)


async def test_vendor_reason_is_single_line_and_bounded():
    result = await parse_response(httpx.Response(422, json={"error": "x\n\u0000" * 500}))
    assert 0 < len(result.reason) <= 500
    assert "\n" not in result.reason
    assert "\u0000" not in result.reason


async def test_control_only_vendor_reason_uses_nonempty_fallback():
    result = await parse_response(httpx.Response(422, json={"error": "\u0000\u0001"}))
    assert result.reason
    assert result.reason.isprintable()


@pytest.mark.parametrize("status", [400, 422, 500])
async def test_traceback_in_structured_error_does_not_leak(status):
    reason = 'Traceback (most recent call last):\n File "/private/secret.py", line 4'
    result = await parse_response(httpx.Response(status, json={"error": reason}))
    assert "secret" not in result.reason
    assert "Traceback" not in result.reason


@pytest.mark.parametrize("error,code", [
    (httpx.ConnectError("secret endpoint"), "network_error"),
    (httpx.ReadError("private details"), "network_error"),
    (httpx.RemoteProtocolError("private details"), "network_error"),
    (httpx.ReadTimeout("private details"), "timeout"),
    (httpx.ConnectTimeout("private details"), "timeout"),
])
async def test_transport_failures_are_safe_and_retryable(error, code):
    def handler(request):
        raise error

    module = api()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await module.OCRClient(module.OCRSettings(), http).parse("42", "scan")
    assert result.code == code
    assert result.retryable is True
    assert "private" not in result.reason
    assert "secret" not in result.reason


async def test_whole_attempt_deadline_bounds_header_wait():
    cancelled = asyncio.Event()

    async def handler(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    module = api()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = module.OCRClient(module.OCRSettings(timeout_seconds=0.02), http)
        result = await asyncio.wait_for(client.parse("42", "scan"), timeout=0.5)
    assert result.code == "timeout"
    assert result.retryable is True
    assert cancelled.is_set()


async def test_asyncio_timeout_is_caught_when_distinct_from_builtin(monkeypatch):
    # Python 3.10 asyncio.TimeoutError was distinct from built-in TimeoutError.
    class LegacyAsyncioTimeoutError(Exception):
        pass

    module = api()
    monkeypatch.setattr(module.asyncio, "TimeoutError", LegacyAsyncioTimeoutError)

    def handler(request):
        raise LegacyAsyncioTimeoutError("private timeout details")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await module.OCRClient(module.OCRSettings(), http).parse("42", "scan")
    assert result.code == "timeout"
    assert result.retryable is True
    assert "private" not in result.reason


class ControlledStream(httpx.AsyncByteStream):
    def __init__(self, chunks=(), wait=False):
        self.chunks = chunks
        self.wait = wait
        self.closed = False
        self.started = asyncio.Event()
        self.count = 0

    async def __aiter__(self):
        self.started.set()
        for chunk in self.chunks:
            self.count += 1
            yield chunk
        if self.wait:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


async def test_deadline_covers_response_body_and_closes_stream():
    stream = ControlledStream([b"{"], wait=True)
    result = await asyncio.wait_for(
        parse_response(httpx.Response(200, stream=stream), timeout_seconds=0.02), timeout=0.5
    )
    assert result.code == "timeout"
    assert stream.closed


async def test_external_cancellation_propagates_and_closes_stream():
    stream = ControlledStream(wait=True)
    task = asyncio.create_task(parse_response(httpx.Response(200, stream=stream)))
    try:
        await asyncio.wait_for(stream.started.wait(), timeout=0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed
    finally:
        task.cancel()


async def test_oversized_response_stops_streaming_before_unbounded_body():
    stream = ControlledStream([b"x" * 16384] * 100)
    result = await parse_response(httpx.Response(200, stream=stream))
    assert result.code == "invalid_response"
    assert result.retryable is False
    assert stream.closed
    assert stream.count <= 5


def test_settings_from_environment_controls_request_policy(monkeypatch):
    values = {
        "OCR_URL": "https://ocr.example/parse", "OCR_TIMEOUT_SECONDS": "0.25",
        "OCR_MAX_ATTEMPTS": "4", "OCR_BACKOFF_SECONDS": "0.05", "OCR_BACKOFF_CAP_SECONDS": "0.2",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    settings = api().OCRSettings.from_env()
    assert settings.url == "https://ocr.example/parse"
    assert settings.timeout_seconds == 0.25
    assert settings.max_attempts == 4
    assert settings.backoff_seconds == 0.05
    assert settings.backoff_cap_seconds == 0.2
    with pytest.raises(FrozenInstanceError):
        settings.max_attempts = 5


@pytest.mark.parametrize("key,value", [
    ("url", "file:///tmp/ocr"), ("url", "ftp://ocr.example"), ("url", "http://"),
    ("timeout_seconds", 0), ("timeout_seconds", -1), ("timeout_seconds", float("nan")),
    ("timeout_seconds", float("inf")), ("timeout_seconds", True),
    ("max_attempts", 0), ("max_attempts", 11), ("max_attempts", 1.5), ("max_attempts", True),
    ("backoff_seconds", -1), ("backoff_seconds", 11), ("backoff_seconds", float("inf")),
    ("backoff_cap_seconds", -1), ("backoff_cap_seconds", 11), ("backoff_cap_seconds", float("nan")),
])
def test_invalid_direct_settings_fail_before_network(key, value):
    with pytest.raises(ValueError):
        api().OCRSettings(**{key: value})


@pytest.mark.parametrize("key,value", [
    ("OCR_TIMEOUT_SECONDS", "nan"), ("OCR_TIMEOUT_SECONDS", "broken"),
    ("OCR_MAX_ATTEMPTS", "1.5"), ("OCR_MAX_ATTEMPTS", "11"),
    ("OCR_BACKOFF_SECONDS", "inf"), ("OCR_BACKOFF_CAP_SECONDS", "-1"),
    ("OCR_URL", "file:///tmp/ocr"),
])
def test_invalid_environment_settings_fail_early(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        api().OCRSettings.from_env()
