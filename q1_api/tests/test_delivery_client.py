"""Delivery boundary: immutable request identity and verified receipts."""

import asyncio
import importlib
import json

import httpx
import pytest


EXPORT = {
    "id": 42,
    "idempotency_key": "export-key-42",
    "file_name": "EXPORT-0042.csv",
    "sha256": "dbbad58ccae738a6f822bea3f49e50fecc9c93116a5b4deb26152eefe84fa9d1",
    "content": b"case_id,full_name\r\nCASE-42,Test Applicant\r\n",
}
RECEIPT = {
    "delivery_id": "DLV-0001",
    "idempotency_key": "export-key-42",
    "file_name": "EXPORT-0042.csv",
    "sha256": "dbbad58ccae738a6f822bea3f49e50fecc9c93116a5b4deb26152eefe84fa9d1",
    "ingested_at": 1791856800,
    "attempts": 1,
}


def api():
    return importlib.import_module("app.delivery_client")


async def send_response(response, export=None, **settings):
    module = api()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as http:
        return await module.DeliveryClient(module.DeliverySettings(**settings), http).send(
            EXPORT.copy() if export is None else export
        )


async def test_sends_exact_saved_bytes_key_filename_once_with_phase_timeouts():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=RECEIPT)

    module = api()
    original = EXPORT.copy()
    settings = module.DeliverySettings(url="https://onboarding.example/ingest", timeout_seconds=0.3)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await module.DeliveryClient(settings, http).send(original)
        assert not http.is_closed
    assert result.receipt == RECEIPT
    assert result.code is None
    assert result.reason is None
    assert result.retryable is False
    assert original == EXPORT
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://onboarding.example/ingest"
    assert request.content == b"case_id,full_name\r\nCASE-42,Test Applicant\r\n"
    assert request.headers["Idempotency-Key"] == "export-key-42"
    assert request.headers["X-File-Name"] == "EXPORT-0042.csv"
    assert request.headers["Content-Type"] == "text/csv"
    assert request.extensions["timeout"] == {"connect": 0.3, "read": 0.3, "write": 0.3, "pool": 0.3}


@pytest.mark.parametrize("deduplicated", [True, False])
async def test_deduplicated_receipt_is_verified_and_preserved(deduplicated):
    receipt = {**RECEIPT, "attempts": 2, "deduplicated": deduplicated}
    result = await send_response(httpx.Response(200, json=receipt))
    assert result.receipt == receipt


async def test_receipt_extra_fields_are_not_exposed():
    result = await send_response(httpx.Response(200, json={**RECEIPT, "traceback": "private"}))
    assert result.receipt == RECEIPT


async def test_receipt_validation_uses_snapshot_of_sent_identity():
    export = EXPORT.copy()

    def handler(request):
        export["idempotency_key"] = "changed-by-caller"
        export["file_name"] = "changed.csv"
        export["sha256"] = "0" * 64
        export["content"] = b"changed"
        return httpx.Response(200, json=RECEIPT)

    module = api()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await module.DeliveryClient(module.DeliverySettings(), http).send(export)
    assert result.receipt == RECEIPT


@pytest.mark.parametrize("key,value", [
    ("idempotency_key", "different-key"), ("file_name", "wrong.csv"), ("sha256", "0" * 64),
])
async def test_mismatched_receipt_never_reports_delivery(key, value):
    result = await send_response(httpx.Response(200, json={**RECEIPT, key: value}))
    assert result.receipt is None
    assert result.code == "receipt_mismatch"
    assert result.retryable is False
    assert value not in result.reason


@pytest.mark.parametrize("key", list(RECEIPT))
async def test_receipt_requires_all_contract_fields(key):
    receipt = {k: v for k, v in RECEIPT.items() if k != key}
    result = await send_response(httpx.Response(200, json=receipt))
    assert result.receipt is None
    assert result.code == "invalid_receipt"
    assert result.retryable is False


@pytest.mark.parametrize("key,value", [
    ("delivery_id", ""), ("delivery_id", "  "), ("delivery_id", None),
    ("delivery_id", "DLV-\ud800"), ("delivery_id", "DLV-\u0000"),
    ("idempotency_key", 123), ("file_name", []), ("sha256", None),
    ("ingested_at", True), ("ingested_at", -1), ("ingested_at", 1.5), ("ingested_at", "123"),
    ("ingested_at", float("nan")), ("ingested_at", float("inf")),
    ("attempts", True), ("attempts", 0), ("attempts", -1), ("attempts", 1.0),
    ("attempts", "1"), ("deduplicated", 1), ("deduplicated", "true"), ("deduplicated", None),
])
async def test_invalid_receipt_schema_is_permanent(key, value):
    body = json.dumps({**RECEIPT, key: value}, ensure_ascii=True).encode("ascii")
    result = await send_response(httpx.Response(200, content=body))
    assert result.receipt is None
    assert result.code == "invalid_receipt"
    assert result.retryable is False


async def test_zero_unix_time_and_valid_unicode_delivery_id_are_accepted():
    receipt = {**RECEIPT, "delivery_id": "DLV-測試", "ingested_at": 0}
    result = await send_response(httpx.Response(200, json=receipt))
    assert result.receipt == receipt


@pytest.mark.parametrize("body", [b"bad JSON", b"null", b"[]", b'"receipt"', b"\xff"])
async def test_unreadable_receipt_body_is_permanent(body):
    result = await send_response(httpx.Response(200, content=body))
    assert result.code == "invalid_receipt"
    assert result.retryable is False


@pytest.mark.parametrize("key,value", [
    ("sha256", "0" * 64), ("content", b"changed"), ("content", "text"),
    ("idempotency_key", ""), ("idempotency_key", "bad\r\nheader"),
    ("file_name", ""), ("file_name", "bad\u0000.csv"), ("file_name", "\ud800.csv"),
    ("idempotency_key", "測試"), ("file_name", "測試.csv"),
    ("idempotency_key", " export-key-42"), ("file_name", "EXPORT-0042.csv "),
])
async def test_invalid_export_is_rejected_without_network(key, value):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=RECEIPT)

    module = api()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await module.DeliveryClient(module.DeliverySettings(), http).send({**EXPORT, key: value})
    assert result.code == "invalid_export"
    assert result.receipt is None
    assert result.retryable is False
    assert requests == []


@pytest.mark.parametrize("status,retryable", [(400, False), (422, False), (429, True), (500, True), (503, True), (404, False), (302, False)])
async def test_http_classification_performs_one_attempt(status, retryable):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"error": "upstream issue"})

    module = api()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as http:
        result = await module.DeliveryClient(module.DeliverySettings(), http).send(EXPORT)
    assert result.receipt is None
    assert result.code == f"http_{status}"
    assert result.retryable is retryable
    assert len(requests) == 1


async def test_http_400_reason_is_preserved():
    result = await send_response(httpx.Response(400, json={"error": "missing Idempotency-Key header"}))
    assert result.reason == "missing Idempotency-Key header"


@pytest.mark.parametrize("body", [b"<html>private</html>", b'{"error":{"secret":"private"}}', b'{"error":"Traceback private"}', b"\xff"])
async def test_error_response_does_not_leak_unstructured_or_traceback_content(body):
    result = await send_response(httpx.Response(400, content=body))
    assert result.reason
    assert "private" not in result.reason
    assert "secret" not in result.reason


async def test_reason_is_bounded_and_safe_for_utf8_json():
    body = json.dumps({"error": "x\u0000\ud800\n" * 600}).encode("ascii")
    result = await send_response(httpx.Response(400, content=body))
    assert 0 < len(result.reason) <= 500
    assert result.reason.isprintable()
    json.dumps(result.reason, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("error,code", [
    (httpx.ConnectError("private"), "network_error"), (httpx.ReadError("private"), "network_error"),
    (httpx.ReadTimeout("private"), "timeout"), (httpx.ConnectTimeout("private"), "timeout"),
])
async def test_transport_failure_is_retryable_without_exposing_details(error, code):
    def handler(request):
        raise error

    module = api()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await module.DeliveryClient(module.DeliverySettings(), http).send(EXPORT)
    assert result.code == code
    assert result.retryable is True
    assert "private" not in result.reason


async def test_header_wait_is_bounded_by_whole_attempt_deadline():
    cancelled = asyncio.Event()

    async def handler(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    module = api()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = module.DeliveryClient(module.DeliverySettings(timeout_seconds=0.02), http)
        result = await asyncio.wait_for(client.send(EXPORT), timeout=0.5)
    assert result.code == "timeout"
    assert result.retryable is True
    assert cancelled.is_set()


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


async def test_body_wait_is_bounded_and_response_closed():
    stream = ControlledStream([b"{"], wait=True)
    result = await asyncio.wait_for(
        send_response(httpx.Response(200, stream=stream), timeout_seconds=0.02), timeout=0.5
    )
    assert result.code == "timeout"
    assert result.retryable is True
    assert stream.closed


async def test_external_cancellation_propagates_and_closes_response():
    stream = ControlledStream(wait=True)
    task = asyncio.create_task(send_response(httpx.Response(200, stream=stream)))
    try:
        await asyncio.wait_for(stream.started.wait(), timeout=0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed
    finally:
        task.cancel()


async def test_response_limit_stops_unbounded_stream():
    stream = ControlledStream([b"x" * 16384] * 100)
    result = await send_response(httpx.Response(200, stream=stream))
    assert result.code == "invalid_receipt"
    assert result.retryable is False
    assert stream.count <= 5
    assert stream.closed


async def test_python_310_asyncio_timeout_identity_is_caught(monkeypatch):
    class LegacyAsyncioTimeoutError(Exception):
        pass

    module = api()
    monkeypatch.setattr(module.asyncio, "TimeoutError", LegacyAsyncioTimeoutError)

    def handler(request):
        raise LegacyAsyncioTimeoutError("private")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await module.DeliveryClient(module.DeliverySettings(), http).send(EXPORT)
    assert result.code == "timeout"
    assert result.retryable is True


def test_settings_from_environment(monkeypatch):
    for name, value in {
        "DELIVERY_URL": "https://onboarding.example/ingest", "DELIVERY_TIMEOUT_SECONDS": "0.25",
        "DELIVERY_MAX_ATTEMPTS": "3", "DELIVERY_BACKOFF_SECONDS": "0.05", "DELIVERY_BACKOFF_CAP_SECONDS": "0.2",
    }.items():
        monkeypatch.setenv(name, value)
    settings = api().DeliverySettings.from_env()
    assert settings.url == "https://onboarding.example/ingest"
    assert settings.timeout_seconds == 0.25
    assert settings.max_attempts == 3
    assert settings.backoff_seconds == 0.05
    assert settings.backoff_cap_seconds == 0.2


@pytest.mark.parametrize("key,value", [
    ("url", "file:///tmp/ingest"), ("url", "ftp://example.com"), ("url", "http://"),
    ("timeout_seconds", 0), ("timeout_seconds", -1), ("timeout_seconds", float("nan")),
    ("timeout_seconds", float("inf")), ("timeout_seconds", True),
    ("max_attempts", 0), ("max_attempts", 11), ("max_attempts", 1.5), ("max_attempts", True),
    ("backoff_seconds", -1), ("backoff_seconds", 11), ("backoff_seconds", float("inf")),
    ("backoff_cap_seconds", -1), ("backoff_cap_seconds", 11), ("backoff_cap_seconds", float("nan")),
])
def test_invalid_settings_are_rejected(key, value):
    with pytest.raises(ValueError):
        api().DeliverySettings(**{key: value})


@pytest.mark.parametrize("key,value", [
    ("DELIVERY_TIMEOUT_SECONDS", "nan"), ("DELIVERY_TIMEOUT_SECONDS", "bad"),
    ("DELIVERY_MAX_ATTEMPTS", "1.5"), ("DELIVERY_MAX_ATTEMPTS", "11"),
    ("DELIVERY_BACKOFF_SECONDS", "inf"), ("DELIVERY_BACKOFF_CAP_SECONDS", "-1"),
    ("DELIVERY_URL", "file:///tmp/ingest"),
])
def test_invalid_environment_fails_early(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        api().DeliverySettings.from_env()
