"""Verify KHQRPay webhook signatures.

KHQRPay signs every webhook with the ``X-KHQRPay-Signature`` header::

    X-KHQRPay-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256>

``v1`` is the HMAC-SHA256 of ``"<t>.<raw body>"``, keyed with the endpoint's
whole signing secret (``whsec_...``) as UTF-8.

IMPORTANT: pass the raw request body, exactly as received, and verify it
BEFORE you parse any JSON. A body that was parsed and serialized again will
not match the signature.

No API key is needed: use ``khqrpay.webhooks.verify(...)`` or
``KHQRPay.webhooks.verify(...)`` directly.
"""

import hashlib
import hmac
import json
import re
import time
from typing import Any, Dict, Optional, Union

from ._errors import KHQRPayError

__all__ = [
    "SIGNATURE_HEADER",
    "DEFAULT_TOLERANCE_SEC",
    "verify",
    "construct_event",
]

#: The header that carries the signature.
SIGNATURE_HEADER = "X-KHQRPay-Signature"

#: How far, in seconds, the signed timestamp may be from now.
DEFAULT_TOLERANCE_SEC = 300

RawBody = Union[bytes, bytearray, memoryview, str]
Number = Union[int, float]

# ASCII only: str.isdigit() and int() also accept other scripts' digits,
# signs, spaces and underscores.
_TIMESTAMP = re.compile(r"[0-9]+")
_SIGNATURE = re.compile(r"[0-9a-fA-F]{64}")


def _body_bytes(raw_body: Any) -> Optional[bytes]:
    if isinstance(raw_body, str):
        return raw_body.encode("utf-8")
    if isinstance(raw_body, (bytes, bytearray, memoryview)):
        return bytes(raw_body)
    return None


def _failure(
    raw_body: Any,
    signature_header: Any,
    secret: Any,
    tolerance_sec: Any,
    now: Any,
) -> Optional[str]:
    """Return why the signature fails, or ``None`` when it is valid."""
    # 1. The header is missing or empty.
    if not isinstance(signature_header, str) or signature_header == "":
        return "The X-KHQRPay-Signature header is missing."

    # Split on ",", then each part on the first "=". A repeated field keeps
    # its last value.
    fields = {}  # type: Dict[str, str]
    for part in signature_header.split(","):
        name, sep, value = part.partition("=")
        if sep:
            fields[name] = value

    # 2. t is missing or not an integer.
    timestamp = fields.get("t")
    if timestamp is None or not _TIMESTAMP.fullmatch(timestamp):
        return "The signature header has no valid timestamp (t)."

    # 3. v1 is missing or not 64 hex characters.
    signature = fields.get("v1")
    if signature is None or not _SIGNATURE.fullmatch(signature):
        return "The signature header has no valid v1 signature."

    # 4. |now - t| > tolerance.
    if now is None:
        now = int(time.time())
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        return "now must be a number of seconds."
    if isinstance(tolerance_sec, bool) or not isinstance(tolerance_sec, (int, float)):
        return "tolerance_sec must be a number of seconds."
    if abs(now - int(timestamp)) > tolerance_sec:
        return "The signature timestamp is outside the tolerance."

    if isinstance(secret, str):
        key = secret.encode("utf-8")
    elif isinstance(secret, (bytes, bytearray)):
        key = bytes(secret)
    else:
        return "The webhook signing secret is missing."
    if not key:
        # An empty key would accept signatures anyone can compute.
        return "The webhook signing secret is empty."

    body = _body_bytes(raw_body)
    if body is None:
        return "The raw body must be bytes or str."

    expected = hmac.new(
        key, timestamp.encode("ascii") + b"." + body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        return "The signature does not match the body."
    return None


def verify(
    raw_body: RawBody,
    signature_header: Optional[str],
    secret: str,
    *,
    tolerance_sec: Number = DEFAULT_TOLERANCE_SEC,
    now: Optional[Number] = None,
) -> bool:
    """Return ``True`` when ``signature_header`` is a valid signature of ``raw_body``.

    Never raises: any problem, including a wrong argument type, returns ``False``.

    Args:
        raw_body: The exact bytes received (``str`` is encoded as UTF-8).
            Verify before parsing any JSON.
        signature_header: The ``X-KHQRPay-Signature`` header value.
        secret: The endpoint's signing secret, including ``whsec_``.
        tolerance_sec: How far the signed timestamp may be from ``now``.
        now: The current time in Unix seconds. Defaults to the system clock.
    """
    try:
        return _failure(raw_body, signature_header, secret, tolerance_sec, now) is None
    except Exception:
        return False


def construct_event(
    raw_body: RawBody,
    signature_header: Optional[str],
    secret: str,
    *,
    tolerance_sec: Number = DEFAULT_TOLERANCE_SEC,
    now: Optional[Number] = None,
) -> Dict[str, Any]:
    """Verify the signature, then return the parsed event.

    Raises:
        KHQRPayError: ``code="invalid_signature"`` (status 400) when the check
            fails, or ``code="invalid_payload"`` (status 400) when a correctly
            signed body isn't a JSON object.
    """
    try:
        reason = _failure(raw_body, signature_header, secret, tolerance_sec, now)
    except Exception:
        reason = "The signature could not be checked."
    if reason is not None:
        raise KHQRPayError("invalid_signature", reason, status=400)

    try:
        text = raw_body if isinstance(raw_body, str) else bytes(raw_body).decode("utf-8")
        event = json.loads(text)
    except ValueError:
        event = None
    if not isinstance(event, dict):
        raise KHQRPayError(
            "invalid_payload", "The webhook body is not a JSON object.", status=400
        )
    return event
