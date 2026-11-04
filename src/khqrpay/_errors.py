from typing import Any, Optional, Tuple


class KHQRPayError(Exception):
    """The one error type the SDK raises for API, transport and signature failures.

    Branch on ``code``, never on ``message``.

    Attributes:
        code: The API's error code (for example ``"invalid_request"`` or
            ``"rate_limited"``), ``"network_error"`` when KHQRPay couldn't be
            reached, ``"invalid_response"`` when the reply wasn't the JSON we
            expected, or ``"invalid_signature"`` from webhook verification.
        message: A human-readable explanation. It may change between releases.
        status: The HTTP status, or 0 when there was no HTTP response.
        retry_after: Seconds from the ``Retry-After`` header, or ``None``.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 0,
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return "KHQRPayError(code=%r, status=%r, message=%r, retry_after=%r)" % (
            self.code,
            self.status,
            self.message,
            self.retry_after,
        )

    def __reduce__(self) -> Tuple[Any, ...]:
        return (type(self), (self.code, self.message, self.status, self.retry_after))
