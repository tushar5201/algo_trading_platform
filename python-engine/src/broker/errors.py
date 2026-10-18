class KotakAPIError(Exception):
    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class KotakAuthError(KotakAPIError):
    """Session missing / expired / rejected. Never retried, never trips the breaker."""


class KotakRateLimitError(KotakAPIError):
    """HTTP 429. Safe to retry (the request was not processed)."""


class KotakBusinessError(KotakAPIError):
    """Broker understood the request and refused it (stat=Not_Ok, 4xx).
    Not a service outage: never retried, never trips the breaker."""

class KotakIPNotAllowedError(KotakBusinessError):
    """Kotak refused an ORDER because of its source IP (stCode 100008 "unauthorized", or 1037 session/IP
    mismatch). Since 1 Apr 2026 place/modify/cancel are accepted only from a whitelisted static IP with a
    session created from that same IP. The session itself is fine, so it must NOT be deleted, and
    re-logging in cannot help until the IP is whitelisted."""

class KotakOrderUncertainError(KotakAPIError):
    """An order request may or may not have reached the broker (timeout / connection
    dropped after sending). The order MUST NOT be retried or assumed failed."""
