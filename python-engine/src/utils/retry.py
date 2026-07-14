import httpx
import structlog
from tenacity import RetryCallState, retry_if_exception, wait_fixed, wait_exponential_jitter, stop_after_attempt, \
    AsyncRetrying
from tenacity import retry

log = structlog.getLogger()

def _is_retryable(exc: BaseException) -> bool:
    try:
        from src.broker.kotak_neo import KotakAuthError, KotakRateLimitError, KotakAPIError
    except ImportError:
        KotakAuthError = KotakRateLimitError = KotakAPIError = ()

    if isinstance(exc, KotakAuthError):
        return False
    if isinstance(exc, KotakRateLimitError):
        return True
    if isinstance(exc, KotakAPIError):
        status = getattr(exc, "status_code", 0) or 0
        return status >= 500 or status == 0
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)

    try:
        from aiohttp import CircuitBreakerError
        if isinstance(exc, CircuitBreakerError):
            return False
    except ImportError:
        pass
    return False

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

def _log_final_failure(label: str):
    def _hook(retry_state: RetryCallState) -> None:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        log.error(
            "retry.exhausted",
            api=label,
            total_attempts = retry_state.attempt_number,
            final_error=str(exc)[:200] if exc else "unknown",
        )
        if exc:
            raise exc
    return _hook

def retry_quotes(fn):
    return retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential_jitter(initial=1, max=8, jitter=1),
        stop=stop_after_attempt(3),
        before_sleep=_log_before_sleep("orders"),
        retry_error_callback=_log_final_failure("orders"),
    )(fn)

def retry_auth(fn):
    return retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential_jitter(initial=2, max=15, jitter=2),
        stop=stop_after_attempt(3),
        before_sleep=_log_before_sleep("auth"),
        retry_error_callback=_log_final_failure("auth"),
    )(fn)

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
        retry=retry_if_exception(_is_retryable),
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
