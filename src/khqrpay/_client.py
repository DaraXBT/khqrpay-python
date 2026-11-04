import http.client
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal
from typing import IO, Any, Dict, List, Mapping, Optional, Tuple

from . import webhooks as _webhooks
from ._errors import KHQRPayError
from ._version import __version__

__all__ = ["KHQRPay", "DEFAULT_BASE_URL", "DEFAULT_TIMEOUT", "USER_AGENT"]

DEFAULT_BASE_URL = "https://khqrpay.dev"
DEFAULT_TIMEOUT = 30.0
USER_AGENT = "khqrpay-python/" + __version__

_KEY_PREFIXES = ("kp_live_", "kp_test_")
_DIGITS = re.compile(r"[0-9]+")

JSONObject = Dict[str, Any]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects.

    urllib copies every header, Authorization included, onto the redirected
    request, even to another host. The secret key must only ever go to the
    base URL, so a 3xx surfaces as an error instead.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> Optional[urllib.request.Request]:
        return None


def _amount_to_string(value: Any) -> Any:
    """Turn a numeric amount into a string, with no rounding.

    Strings, and anything that isn't a number, pass through unchanged so the
    API can validate them.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("amount must be a finite number.")
        # Match String(n): 20000.0 -> "20000", 16.88 -> "16.88".
        if value.is_integer():
            return str(int(value))
        return repr(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("amount must be a finite number.")
        return format(value, "f")
    return value


def _parse_retry_after(headers: Any) -> Optional[int]:
    if headers is None:
        return None
    value = headers.get("Retry-After")
    if value is None:
        return None
    value = value.strip()
    if _DIGITS.fullmatch(value):
        return int(value)
    return None


_NOT_JSON = object()


def _parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return _NOT_JSON


def _api_error(status: int, raw: bytes, headers: Any) -> KHQRPayError:
    retry_after = _parse_retry_after(headers)
    payload = _parse_json(raw)
    if isinstance(payload, dict):
        code = payload.get("error")
        message = payload.get("message")
        if not isinstance(message, str) or not message:
            message = "KHQRPay returned HTTP %d." % status
        if isinstance(code, str) and code:
            return KHQRPayError(code, message, status, retry_after)
        return KHQRPayError("invalid_response", message, status, retry_after)
    return KHQRPayError(
        "invalid_response",
        "KHQRPay returned HTTP %d with a body that is not a JSON object." % status,
        status,
        retry_after,
    )


def _require_id(payment_id: Any) -> str:
    if not isinstance(payment_id, str) or payment_id == "":
        raise ValueError("payment_id must be a non-empty string.")
    return urllib.parse.quote(payment_id, safe="")


class Payments:
    """``client.payments``: create, retrieve, list and simulate payments."""

    def __init__(self, client: "KHQRPay") -> None:
        self._client = client

    def create(
        self,
        params: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
    ) -> JSONObject:
        """Create a payment: ``POST /v1/payments``.

        Args:
            params: The request body, sent as given. ``amount`` is required:
                a string such as ``"16.88"`` or ``"20000"``, or a number,
                which is converted to a string with no rounding. Optional:
                ``currency`` (``"USD"`` or ``"KHR"``), ``reference_id`` and
                ``metadata``.
            idempotency_key: Sent as the ``Idempotency-Key`` header. If you
                leave it out, the SDK uses ``params["idempotency_key"]`` when
                that is set, or else a random UUID v4. Retry with the same
                key to get the same payment back.

        Returns:
            The payment. Send the customer to ``payment["checkout_url"]``.
        """
        if not isinstance(params, Mapping):
            raise TypeError("params must be a dict, such as {'amount': '16.88'}.")
        body = dict(params)
        if "amount" in body:
            body["amount"] = _amount_to_string(body["amount"])

        if idempotency_key is None:
            from_body = body.get("idempotency_key")
            if isinstance(from_body, str) and from_body:
                # The header wins over the body, so a random header key would
                # silently defeat the caller's own key.
                idempotency_key = from_body
            else:
                idempotency_key = str(uuid.uuid4())
        elif not isinstance(idempotency_key, str) or idempotency_key == "":
            raise ValueError("idempotency_key must be a non-empty string.")

        return self._client._request(
            "POST", "/v1/payments", body=body, idempotency_key=idempotency_key
        )

    def retrieve(self, payment_id: str) -> JSONObject:
        """Retrieve a payment: ``GET /v1/payments/{id}``."""
        return self._client._request("GET", "/v1/payments/" + _require_id(payment_id))

    def list(
        self,
        *,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> JSONObject:
        """List payments, newest first: ``GET /v1/payments``.

        Args:
            status: ``"pending"``, ``"scanned"``, ``"paid"``, ``"expired"`` or
                ``"failed"``.
            limit: 1 to 100. The API defaults to 20.
            cursor: ``next_cursor`` from the previous page, exactly as you got it.

        Returns:
            ``{"object": "list", "data": [...], "has_more": bool,
            "next_cursor": str or None}``. List items leave out ``metadata``.
        """
        query = []  # type: List[Tuple[str, str]]
        if status is not None:
            query.append(("status", status))
        if limit is not None:
            query.append(("limit", str(limit)))
        if cursor is not None:
            query.append(("cursor", cursor))
        return self._client._request("GET", "/v1/payments", query=query)

    def simulate(self, payment_id: str, status: str) -> JSONObject:
        """Move a test payment to ``status``: ``POST /v1/test/payments/{id}/simulate``.

        Test keys only. ``status`` is ``"scanned"``, ``"paid"``, ``"expired"``
        or ``"failed"``. KHQRPay sends the webhook as it would for a live payment.
        """
        path = "/v1/test/payments/%s/simulate" % _require_id(payment_id)
        return self._client._request("POST", path, body={"status": status})


class Usage:
    """``client.usage``: your plan's quota."""

    def __init__(self, client: "KHQRPay") -> None:
        self._client = client

    def retrieve(self) -> JSONObject:
        """Check usage: ``GET /v1/usage``."""
        return self._client._request("GET", "/v1/usage")


class KHQRPay:
    """The KHQRPay API client.

    Example::

        import os
        from khqrpay import KHQRPay

        client = KHQRPay(os.environ["KHQRPAY_SECRET_KEY"])
        payment = client.payments.create({"amount": "16.88", "currency": "USD"})

    Args:
        secret_key: A ``kp_live_...`` or ``kp_test_...`` secret key. Keep it on
            your server. The SDK only ever sends it in the ``Authorization``
            header to ``base_url``.
        base_url: Defaults to ``https://khqrpay.dev``.
        timeout: Seconds to wait for the network. Defaults to 30.

    The SDK never retries by itself.
    """

    #: Webhook verification. Needs no key: ``KHQRPay.webhooks.verify(...)``.
    webhooks = _webhooks

    def __init__(
        self,
        secret_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        # Error messages here never include the key, not even part of it.
        if not isinstance(secret_key, str):
            raise TypeError(
                "KHQRPay needs your secret key as a string, starting with kp_live_ or kp_test_."
            )
        if secret_key == "":
            raise ValueError(
                "KHQRPay needs a secret key. Copy the kp_live_ or kp_test_ key from your dashboard."
            )
        if not secret_key.startswith(_KEY_PREFIXES) or len(secret_key) <= len("kp_live_"):
            raise ValueError(
                "That isn't a KHQRPay secret key: it must start with kp_live_ or kp_test_."
            )
        if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in secret_key):
            raise ValueError(
                "The secret key contains whitespace or control characters. Check how you load it."
            )

        if not isinstance(base_url, str):
            raise TypeError("base_url must be a string, such as 'https://khqrpay.dev'.")
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("https", "http") or not parsed.netloc:
            raise ValueError("base_url must be an http(s) URL, such as 'https://khqrpay.dev'.")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not have a query string or fragment.")

        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not timeout > 0
            or not math.isfinite(timeout)
        ):
            raise ValueError("timeout must be a positive number of seconds.")

        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._timeout = float(timeout)
        self._opener = urllib.request.build_opener(_NoRedirect)
        self.payments = Payments(self)
        self.usage = Usage(self)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout(self) -> float:
        return self._timeout

    def __repr__(self) -> str:
        mode = "live" if self._secret_key.startswith("kp_live_") else "test"
        return "<KHQRPay mode=%s base_url=%r>" % (mode, self._base_url)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[List[Tuple[str, str]]] = None,
        body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> JSONObject:
        url = self._base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)

        headers = {
            "Authorization": "Bearer " + self._secret_key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        data = None  # type: Optional[bytes]
        if body is not None:
            data = json.dumps(
                body, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                status = response.status
                raw = response.read()
                response_headers = response.headers
        except urllib.error.HTTPError as err:
            # Any non-2xx, including a 3xx we refused to follow.
            try:
                raw = err.read()
            except Exception:
                raw = b""
            finally:
                err.close()
            raise _api_error(err.code, raw, err.headers) from None
        except (urllib.error.URLError, http.client.HTTPException, OSError) as err:
            reason = getattr(err, "reason", None) or err
            hint = ""
            if "CERTIFICATE_VERIFY_FAILED" in str(reason):
                # Python from python.org on macOS ships without trusted certificates until its
                # "Install Certificates.command" is run; nothing about the request itself is wrong.
                hint = (
                    " Your Python can't verify HTTPS certificates. On macOS with Python from"
                    " python.org, run \"Install Certificates.command\" in its Applications folder."
                )
            raise KHQRPayError(
                "network_error", "Could not reach KHQRPay: %s.%s" % (reason, hint), status=0
            ) from err

        payload = _parse_json(raw)
        if not isinstance(payload, dict):
            raise KHQRPayError(
                "invalid_response",
                "KHQRPay returned HTTP %d with a body that is not a JSON object." % status,
                status,
                _parse_retry_after(response_headers),
            )
        return payload
