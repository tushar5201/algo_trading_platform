from datetime import timedelta

import structlog
from aiobreaker import CircuitBreaker, CircuitBreakerListener, CircuitBreakerState, CircuitBreakerError

from src.broker.kotak_neo import KotakAuthError, KotakAPIError, KotakRateLimitError

log = structlog.get_logger()


class LoggingListener(CircuitBreakerListener):
    def state_change(self, breaker: CircuitBreaker, old: str, new: str) -> None:
        old_name = old.name if hasattr(old, "name") else str(old)
        new_name = new.name if hasattr(new, "name") else str(new)

        if new == CircuitBreakerState.OPEN:
            log.error(
                "circuit_breaker.open",
                breaker=breaker.name,
                fail_count=breaker.fail_counter,
                reason=f"Too many failures, circuit opened for 60 seconds {breaker.name}"
            )
        elif new == CircuitBreakerState.HALF_OPEN:
            log.warning(
                "circuit_breaker.half_open",
                breaker=breaker.name,
                reason=f"Circuit is half-open, testing if the service has recovered {breaker.name}"
            )
        elif new == CircuitBreakerState.CLOSED:
            log.info(
                "circuit_breaker.closed",
                breaker=breaker.name,
                reason=f"Circuit closed, service has recovered {breaker.name}"
            )

    def failure(self, breaker: CircuitBreaker, error: BaseException) -> None:
        log.warning(
            "circuit_breaker.failure_recorded",
            breaker=breaker.name,
            fail_count=breaker.fail_counter,
            fail_max=breaker.fail_max,
            error=str(error)[:120]
        )

    def success(self, breaker: CircuitBreaker) -> None:
        if breaker.fail_counter > 0:
            log.info(
                "circuit_breaker.recovery",
                breaker=breaker.name,
                note=f"Successful call recorded"
            )


_listener = LoggingListener()


def _is_circuit_failure(exception: BaseException) -> bool:
    import httpx
    try:
        if isinstance(exception, KotakAuthError):
            return False
        if isinstance(exception, KotakRateLimitError):
            return True
        if isinstance(exception, KotakAPIError):
            return exception.status_code >= 500 or exception.status_code == 0
    except ImportError:
        pass

    if isinstance(exception, httpx.TimeoutException):
        return True
    if isinstance(exception, httpx.ConnectError):
        return True
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code >= 500

    return True


def _exclude_list():
    import httpx
    try:
        return [KotakRateLimitError]
    except ImportError:
        return []


kotak_quotes_breaker = CircuitBreaker(
    fail_max=3,
    timeout_duration=timedelta(seconds=60),
    name="kotak_quotes",
    listeners=[_listener],
)

kotak_orders_breaker = CircuitBreaker(
    fail_max=3,
    timeout_duration=timedelta(seconds=60),
    name="kotak_orders",
    listeners=[_listener],
)

kotak_auth_breaker = CircuitBreaker(
    fail_max=5,
    timeout_duration=timedelta(seconds=120),
    name="kotak_auth",
    listeners=[_listener],
)


def get_all_breaker_states() -> dict:
    def _describe(breaker: CircuitBreaker) -> dict:
        state = breaker.current_state
        state_name = state.name.lower() if hasattr(state, "name") else str(state).lower()

        info = {
            "state": state_name,
            "fail_count": breaker.fail_counter,
            "fail_max": breaker.fail_max,
        }

        if state == CircuitBreakerState.OPEN and breaker.opens_at:
            info["opens_at"] = breaker.opens_at.isoformat()
            info["seconds_until_retry"] = max(
                0,
                int(breaker.time_until_open.total_seconds())
                if breaker.time_until_open else 0
            )
        return info

    return {
        "kotak_quotes": _describe(kotak_quotes_breaker),
        "kotak_orders": _describe(kotak_orders_breaker),
        "kotak_auth": _describe(kotak_auth_breaker),
    }


def is_any_breaker_open() -> bool:
    return any(
        b.current_state == CircuitBreakerState.OPEN
        for b in [kotak_quotes_breaker, kotak_orders_breaker, kotak_auth_breaker]
    )


__all__ = [
    "kotak_quotes_breaker",
    "kotak_orders_breaker",
    "kotak_auth_breaker",
    "CircuitBreakerError",
    "get_all_breaker_states",
    "is_any_breaker_open",
]
