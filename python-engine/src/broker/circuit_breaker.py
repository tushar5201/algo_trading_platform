from datetime import timedelta

import structlog
from aiobreaker import CircuitBreaker, CircuitBreakerListener, CircuitBreakerState, CircuitBreakerError

from src.broker.errors import KotakAuthError, KotakBusinessError, KotakRateLimitError

log = structlog.get_logger()

_NOT_OUTAGE = [KotakAuthError, KotakBusinessError, KotakRateLimitError]

class LoggingListener(CircuitBreakerListener):
    def state_change(self, breaker: CircuitBreaker, old, new) -> None:
        if new == CircuitBreakerState.OPEN:
            log.error(
                "circuit_breaker.open",
                breaker=breaker.name,
                fail_count=breaker.fail_counter,
                reason=f"Too many failures, circuit opened for 60 seconds {breaker.timeout_duration}"
            )
        elif new == CircuitBreakerState.HALF_OPEN:
            log.warning(
                "circuit_breaker.half_open",
                breaker=breaker.name,
                reason=f"Circuit is half-open, testing if the service has recovered"
            )
        elif new == CircuitBreakerState.CLOSED:
            log.info(
                "circuit_breaker.closed",
                breaker=breaker.name,
                reason=f"Circuit closed, service has recovered"
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

kotak_quotes_breaker = CircuitBreaker(
    fail_max=3,
    timeout_duration=timedelta(seconds=60),
    exclude=_NOT_OUTAGE,
    name="kotak_quotes",
    listeners=[_listener],
)

kotak_auth_breaker = CircuitBreaker(
    fail_max=5,
    timeout_duration=timedelta(seconds=120),
    exclude=_NOT_OUTAGE,
    name="kotak_auth",
    listeners=[_listener],
)

kotak_orders_breaker = CircuitBreaker(
    fail_max=3,
    timeout_duration=timedelta(seconds=60),
    exclude=_NOT_OUTAGE,
    name="kotak_orders",
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

        if state == CircuitBreakerState.OPEN:
            info["timeout_seconds"] = int(breaker.timeout_duration.total_seconds())
        return info

    return {
        "kotak_quotes": _describe(kotak_quotes_breaker),
        "kotak_orders": _describe(kotak_orders_breaker),
        "kotak_auth": _describe(kotak_auth_breaker),
    }


def is_any_breaker_open() -> bool:
    return any(
        b.current_state == CircuitBreakerState.OPEN
        for b in (kotak_quotes_breaker, kotak_orders_breaker, kotak_auth_breaker)
    )

def is_breaker_open(breaker: CircuitBreaker) -> bool:
    return breaker.current_state == CircuitBreakerState.OPEN

__all__ = [
    "kotak_quotes_breaker",
    "kotak_orders_breaker",
    "kotak_auth_breaker",
    "CircuitBreakerError",
    "get_all_breaker_states",
    "is_any_breaker_open",
    "is_breaker_open",
]
