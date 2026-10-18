from dataclasses import dataclass
from typing import Optional

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_RETRY_AFTER = 30.0
MAX_RETRY_AFTER = 24 * 3600.0

class LLMError(Exception):
    """Non-retryable failure (e.g. bad API key). Messages never contain the key or headers."""

class LLMRateLimited(LLMError):
    def __init__(self, retry_after:float):
        super().__init__("Rate limited!")
        self.retry_after = retry_after

class LLMUnavailable(LLMError):
    """Timeout, connection failure, 5xx or a garbled reply: transient."""

class LLMBadRequest(LLMError):
    """400/ 422, e.g Groq's JSON mode rejected the model's output."""

@dataclass
class LLMResponse:
    content: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    finish_reason: str = ""

def _retry_after(headers: httpx.Headers) -> float:
    try:
        return min(max(float(headers.get("retry-after", "")), 1.0), MAX_RETRY_AFTER)
    except ValueError:
        return DEFAULT_RETRY_AFTER

def _error_text(resp: httpx.Response) -> str:
    try:
        msg = resp.json()["error"]["message"]
    except Exception:
        msg = resp.text
    return " ".join(str(msg).split())[:200]

class GroqClient:
    def __init__(self, api_key: str, model: str, timeout: float, max_tokens: int, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.model, self._max_tokens = model, max_tokens
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def complete(self, messages: list[dict], json_mode: bool = True) -> LLMResponse:
        body: dict = {"model": self.model, "messages": messages, "max_tokens": self._max_tokens, "temperature": 0.2}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            resp = await self._http.post(GROQ_URL, headers=self._headers, json=body)
        except httpx.HTTPError as e:  # timeouts and connection errors
            raise LLMUnavailable(type(e).__name__)
        code = resp.status_code
        if code == 429:
            raise LLMRateLimited(_retry_after(resp.headers))
        if code in (401, 403):
            raise LLMError("Groq authentication failed (check GROQ_API_KEY)")
        if code == 404:
            raise LLMError("Groq model or endpoint not found (check GROQ_MODEL)")
        if code in (400, 422):
            raise LLMBadRequest(f"HTTP {code}: {_error_text(resp)}")
        if code >= 400:
            raise LLMUnavailable(f"HTTP {code}")
        try:
            data = resp.json()
            choice = data["choices"][0]
            usage = data.get("usage") or {}
            return LLMResponse(content=choice["message"].get("content"),
                               tokens_in=int(usage.get("prompt_tokens") or 0),
                               tokens_out=int(usage.get("completion_tokens") or 0),
                               model=str(data.get("model") or self.model),
                               finish_reason=str(choice.get("finish_reason") or ""))
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMUnavailable("malformed response")

    async def aclose(self) -> None:
        await self._http.aclose()


