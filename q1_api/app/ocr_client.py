"""One bounded OCR attempt; durable retries belong to the workflow service."""

import asyncio
import json
import math
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

import httpx


MAX_RESPONSE_BYTES = 64 * 1024
MAX_REASON_LENGTH = 500
FIELD_NAMES = ("id_number", "full_name", "dob", "issue_date", "confidence")


@dataclass(frozen=True)
class OCRSettings:
    url: str = "http://localhost:9100/ocr/parse"
    timeout_seconds: float = 1.0
    max_attempts: int = 6
    backoff_seconds: float = 0.1
    backoff_cap_seconds: float = 0.5

    def __post_init__(self):
        try:
            parsed = httpx.URL(self.url)
        except (httpx.InvalidURL, TypeError, ValueError) as exc:
            raise ValueError("OCR_URL must be an absolute HTTP(S) URL") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("OCR_URL must be an absolute HTTP(S) URL")
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
            url=os.environ.get("OCR_URL", cls.url),
            timeout_seconds=float(os.environ.get("OCR_TIMEOUT_SECONDS", cls.timeout_seconds)),
            max_attempts=int(os.environ.get("OCR_MAX_ATTEMPTS", cls.max_attempts)),
            backoff_seconds=float(os.environ.get("OCR_BACKOFF_SECONDS", cls.backoff_seconds)),
            backoff_cap_seconds=float(os.environ.get("OCR_BACKOFF_CAP_SECONDS", cls.backoff_cap_seconds)),
        )


@dataclass(frozen=True)
class OCRResult:
    fields: dict | None = None
    code: str | None = None
    reason: str | None = None
    retryable: bool = False


def _invalid_response():
    return OCRResult(code="invalid_response", reason="OCR returned an invalid response")


def _validated_fields(payload):
    if not isinstance(payload, dict) or any(key not in payload for key in FIELD_NAMES):
        return None
    identity = payload["id_number"]
    name = payload["full_name"]
    if not isinstance(identity, str) or not identity.startswith("TEST-ID-") or not identity[8:].strip():
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    # Reject controls and lone surrogates before persistence/API UTF-8 encoding.
    # Other Unicode remains unchanged, including legitimate non-ASCII names.
    if any(unicodedata.category(char) in {"Cc", "Cs"} for value in (identity, name) for char in value):
        return None
    for key in ("dob", "issue_date"):
        value = payload[key]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return None
        try:
            date.fromisoformat(value)
        except ValueError:
            return None
    confidence = payload["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    if not 0 <= confidence <= 1 or not math.isfinite(confidence):
        return None
    return {key: payload[key] for key in FIELD_NAMES}


def _http_failure(status, payload=None):
    reason = f"OCR service returned HTTP {status}"
    if status in {400, 422} and isinstance(payload, dict):
        for key in ("error", "reason", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                # Never copy an upstream stack trace or an arbitrary response body.
                if "traceback" not in value.lower():
                    clean = " ".join("".join(c if c.isprintable() else " " for c in value).split())
                    reason = clean[:MAX_REASON_LENGTH] or reason
                break
    return OCRResult(code=f"http_{status}", reason=reason, retryable=status == 429 or 500 <= status < 600)


class OCRClient:
    def __init__(self, settings: OCRSettings, http_client: httpx.AsyncClient):
        self.settings = settings
        self.http_client = http_client

    async def parse(self, case_id: str, image_ref: str) -> OCRResult:
        """Make one attempt with both phase timeouts and a whole-attempt deadline.

        The caller owns the HTTP client. Cancellation is deliberately not caught.
        """
        try:
            return await asyncio.wait_for(
                self._attempt(case_id, image_ref), timeout=self.settings.timeout_seconds
            )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return OCRResult(code="timeout", reason="OCR request timed out", retryable=True)
        except httpx.RequestError:
            return OCRResult(code="network_error", reason="OCR service could not be reached", retryable=True)

    async def _attempt(self, case_id, image_ref):
        async with self.http_client.stream(
            "POST", self.settings.url,
            json={"case_id": case_id, "image_ref": image_ref},
            timeout=httpx.Timeout(self.settings.timeout_seconds),
            follow_redirects=False,
        ) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    return _invalid_response() if response.status_code == 200 else _http_failure(response.status_code)
                body.extend(chunk)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                payload = None
            if response.status_code != 200:
                return _http_failure(response.status_code, payload)
            fields = _validated_fields(payload)
            return OCRResult(fields=fields) if fields is not None else _invalid_response()
