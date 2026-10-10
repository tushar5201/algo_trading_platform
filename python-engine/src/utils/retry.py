import httpx
import structlog
from tenacity import RetryCallState, retry_if_exception, wait_exponential_jitter, stop_after_attempt, \
    AsyncRetrying
from tenacity import retry

from src.broker.errors import KotakAuthError, KotakBusinessError, KotakOrderUncertainError, KotakRateLimitError, \
    KotakAPIError

log = structlog.getLogger()
_TRANSPORT_ERRORS = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)

def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (KotakAuthError, KotakBusinessError, KotakOrderUncertainError)):
        return False
    if isinstance(exc, KotakRateLimitError):
        return True
    if isinstance(exc, KotakAPIError):
        return (exc.status_code or 0) >= 500
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, _TRANSPORT_ERRORS)

def _is_retryable_order(exc: BaseException) -> bool:
    if isinstance(exc, KotakRateLimitError):
        return True
    return isinstance(exc, httpx.ConnectError)

def _log_before_sleep(label: str):
    def _hook(retry_state: RetryCallState) -> None:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        wait_time = (
            retry_state.next_action.sleep
            if retry_state.next_action else 0
        )
        log.warning(
            "retry.attempt_failed",
            api=label,
            attempt=retry_state.attempt_number,
            wait_sec = round(wait_time, 2),
            error = str(exc)[:150] if exc else "unknown",
        )
    return _hook

def retrying_quotes() -> AsyncRetrying:
    return AsyncRetrying(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential_jitter(initial=0.5, max=4, jitter=0.5),
        stop=stop_after_attempt(3),
        before_sleep=_log_before_sleep("quotes"),
        reraise=True,
    )

def retrying_orders() -> AsyncRetrying:
    return AsyncRetrying(
        retry=retry_if_exception(_is_retryable_order),
        wait=wait_exponential_jitter(initial=1, max=8, jitter=1),
        stop=stop_after_attempt(3),
        before_sleep=_log_before_sleep("orders"),
        reraise=True,
    )

def retrying_auth() -> AsyncRetrying:
    return AsyncRetrying(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential_jitter(initial=2, max=15, jitter=2),
        stop=stop_after_attempt(3),
        before_sleep=_log_before_sleep("auth"),
        reraise=True,
    )
