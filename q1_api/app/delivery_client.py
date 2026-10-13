"""Send one immutable export and verify the downstream receipt for that export."""

import asyncio
import hashlib
import json
import math
import os
import unicodedata
from dataclasses import dataclass

import httpx


MAX_RESPONSE_BYTES = 64 * 1024
MAX_REASON_LENGTH = 500
RECEIPT_FIELDS = ("delivery_id", "idempotency_key", "file_name", "sha256", "ingested_at", "attempts")


@dataclass(frozen=True)
class DeliverySettings:
    url: str = "http://localhost:9200/ingest"
    timeout_seconds: float = 1.0
    max_attempts: int = 4
    backoff_seconds: float = 0.1
    backoff_cap_seconds: float = 0.5

    def __post_init__(self):
        try:
            parsed = httpx.URL(self.url)
        except (httpx.InvalidURL, TypeError, ValueError) as exc:
            raise ValueError("DELIVERY_URL must be an absolute HTTP(S) URL") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("DELIVERY_URL must be an absolute HTTP(S) URL")
        for name in ("timeout_seconds", "backoff_seconds", "backoff_cap_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite number")
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(f"{name} must be a finite number")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be an integer between 1 and 10")
        if not 0 <= self.backoff_seconds <= 10 or not 0 <= self.backoff_cap_seconds <= 10:
            raise ValueError("backoff values must be between 0 and 10 seconds")

    @classmethod
    def from_env(cls):
        return cls(
            url=os.environ.get("DELIVERY_URL", cls.url),
            timeout_seconds=float(os.environ.get("DELIVERY_TIMEOUT_SECONDS", cls.timeout_seconds)),
            max_attempts=int(os.environ.get("DELIVERY_MAX_ATTEMPTS", cls.max_attempts)),
            backoff_seconds=float(os.environ.get("DELIVERY_BACKOFF_SECONDS", cls.backoff_seconds)),
            backoff_cap_seconds=float(os.environ.get("DELIVERY_BACKOFF_CAP_SECONDS", cls.backoff_cap_seconds)),
        )


@dataclass(frozen=True)
class DeliveryResult:
    receipt: dict | None = None
    code: str | None = None
    reason: str | None = None
    retryable: bool = False


def _safe_text(value):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(unicodedata.category(char) in {"Cc", "Cs"} for char in value)
    )


def _valid_export(export):
    if not isinstance(export, dict) or not isinstance(export.get("content"), bytes):
        return False
    for key in ("idempotency_key", "file_name"):
        value = export.get(key)
        # These are sent verbatim as HTTP headers; never silently recode their identity.
        if not _safe_text(value) or not value.isascii() or value != value.strip():
            return False
    return export.get("sha256") == hashlib.sha256(export["content"]).hexdigest()


def _invalid_receipt():
    return DeliveryResult(code="invalid_receipt", reason="Delivery service returned an invalid receipt")


def _verify_receipt(payload, export):
    if not isinstance(payload, dict) or any(key not in payload for key in RECEIPT_FIELDS):
        return _invalid_receipt()
    if not all(_safe_text(payload[key]) for key in ("delivery_id", "idempotency_key", "file_name", "sha256")):
        return _invalid_receipt()
    if type(payload["ingested_at"]) is not int or payload["ingested_at"] < 0:
        return _invalid_receipt()
    if type(payload["attempts"]) is not int or payload["attempts"] < 1:
        return _invalid_receipt()
    if "deduplicated" in payload and type(payload["deduplicated"]) is not bool:
        return _invalid_receipt()
    if any(payload[key] != export[key] for key in ("idempotency_key", "file_name", "sha256")):
        return DeliveryResult(code="receipt_mismatch", reason="Delivery receipt does not match the saved export")
    receipt = {key: payload[key] for key in RECEIPT_FIELDS}
    if "deduplicated" in payload:
        receipt["deduplicated"] = payload["deduplicated"]
    return DeliveryResult(receipt=receipt)


def _http_failure(status, payload=None):
    reason = f"Delivery service returned HTTP {status}"
    if status == 400 and isinstance(payload, dict):
        value = payload.get("error")
        if isinstance(value, str) and "traceback" not in value.lower():
            clean = " ".join("".join(c if c.isprintable() else " " for c in value).split())
            reason = clean[:MAX_REASON_LENGTH] or reason
    return DeliveryResult(code=f"http_{status}", reason=reason, retryable=status == 429 or 500 <= status < 600)


class DeliveryClient:
    def __init__(self, settings: DeliverySettings, http_client: httpx.AsyncClient):
        self.settings = settings
        self.http_client = http_client

    async def send(self, export: dict) -> DeliveryResult:
        """One attempt; the caller owns both durable retries and the HTTP client.

        Timeouts do not prove that ingestion failed. The workflow must retry the
        same saved bytes and key. External cancellation deliberately propagates.
        """
        # Pin immutable bytes and identity before yielding to network I/O.
        if isinstance(export, dict):
            export = export.copy()
        if not _valid_export(export):
            return DeliveryResult(code="invalid_export", reason="Saved export content or delivery identity is invalid")
        try:
            return await asyncio.wait_for(self._attempt(export), timeout=self.settings.timeout_seconds)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return DeliveryResult(code="timeout", reason="Delivery request timed out; ingestion is unconfirmed", retryable=True)
        except httpx.RequestError:
            return DeliveryResult(code="network_error", reason="Delivery service could not be reached", retryable=True)

    async def _attempt(self, export):
        async with self.http_client.stream(
            "POST", self.settings.url, content=export["content"],
            headers={
                "Idempotency-Key": export["idempotency_key"],
                "X-File-Name": export["file_name"],
                "Content-Type": "text/csv",
            },
            timeout=httpx.Timeout(self.settings.timeout_seconds), follow_redirects=False,
        ) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    return _invalid_receipt() if response.status_code == 200 else _http_failure(response.status_code)
                body.extend(chunk)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                payload = None
            if response.status_code != 200:
                return _http_failure(response.status_code, payload)
            return _verify_receipt(payload, export)
