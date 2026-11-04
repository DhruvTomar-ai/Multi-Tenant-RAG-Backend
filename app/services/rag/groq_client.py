"""The only code that talks to Groq. One shared client, and SDK errors become safe internal errors.

The groq package is imported on first use, so startup does not depend on it.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_client: Any = None


class LLMError(Exception):
    """Each code maps to one HTTP response. Messages are safe to return to clients."""

    code = "llm_error"
    http_status = 502
    message = "The answer service returned an error. Try again."

    def __init__(self, retry_after: str | None = None):
        super().__init__(self.message)
        self.retry_after = retry_after


class LLMNotConfigured(LLMError):
    code = "llm_not_configured"
    http_status = 503
    message = "The answer service is not available."


class LLMAuthenticationFailed(LLMError):
    code = "llm_authentication_failed"
    http_status = 503
    message = "The answer service is not available."


class LLMRateLimited(LLMError):
    code = "llm_rate_limited"
    http_status = 429
    message = "The answer service is busy. Try again shortly."


class LLMTimeout(LLMError):
    code = "llm_timeout"
    http_status = 504
    message = "The answer service took too long to respond. Try again."


class LLMUnavailable(LLMError):
    code = "llm_unavailable"
    http_status = 503
    message = "The answer service is temporarily unavailable. Try again."


class LLMBadResponse(LLMError):
    code = "llm_bad_response"
    http_status = 502
    message = "The answer service returned an unusable response. Try again."


@dataclass(frozen=True)
class LLMResult:
    content: str
    model: str
    finish_reason: str | None


def get_client() -> Any:
    global _client
    key = settings.groq_api_key.get_secret_value().strip() if settings.groq_api_key else ""
    if not key:
        raise LLMNotConfigured()
    if _client is None:
        with _lock:
            if _client is None:
                from groq import Groq

                _client = Groq(api_key=key, timeout=settings.groq_timeout_seconds, max_retries=settings.groq_max_retries)
    return _client


def complete_json(messages: list[dict[str, str]]) -> LLMResult:
    """Ask for one JSON object. Any failure raises an LLMError, and details go to the log only."""
    import groq

    client = get_client()
    try:
        response = client.chat.completions.create(
            model=settings.groq_model,
            messages=messages,
            temperature=settings.groq_temperature,
            max_tokens=settings.groq_max_tokens,
            response_format={"type": "json_object"},
        )
    except groq.AuthenticationError as exc:
        logger.error("Groq rejected the API key (status %s)", exc.status_code)
        raise LLMAuthenticationFailed() from exc
    except groq.PermissionDeniedError as exc:
        logger.error("Groq denied access (status %s)", exc.status_code)
        raise LLMAuthenticationFailed() from exc
    except groq.RateLimitError as exc:
        logger.warning("Groq rate limit reached after retries")
        raise LLMRateLimited(retry_after=exc.response.headers.get("retry-after")) from exc
    except groq.APITimeoutError as exc:
        logger.warning("Groq timed out after retries")
        raise LLMTimeout() from exc
    except groq.APIConnectionError as exc:
        logger.warning("Could not reach Groq: %s", type(exc).__name__)
        raise LLMUnavailable() from exc
    except groq.InternalServerError as exc:
        logger.warning("Groq service error (status %s)", exc.status_code)
        raise LLMUnavailable() from exc
    except groq.APIStatusError as exc:
        # For example a retired model, or a request Groq rejects. Retrying cannot help.
        logger.error("Groq rejected the request (status %s, model %s)", exc.status_code, settings.groq_model)
        raise LLMError() from exc
    except groq.APIError as exc:
        logger.error("Groq client error: %s", type(exc).__name__)
        raise LLMBadResponse() from exc

    choices = getattr(response, "choices", None)
    if not choices or not choices[0].message or not choices[0].message.content:
        logger.error("Groq returned no content")
        raise LLMBadResponse()
    return LLMResult(content=choices[0].message.content, model=response.model or settings.groq_model, finish_reason=choices[0].finish_reason)
