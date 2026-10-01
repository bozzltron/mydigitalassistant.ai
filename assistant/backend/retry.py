"""Transient-failure retries for external hops.

The assistant is multi-model and tool-heavy: one turn can touch Ollama several
times, a search backend, and one or more URLs. Any of those can blip without
being broken — a model swap, a connection reset, a 503 while a runner reloads,
a rate limit. A retry is the difference between a hiccup and a failed turn.

Only *idempotent* hops are retried. Side-effecting tools (``write_file``,
``delete_file``, ``upsert_slot``, ``edit_file``, ...) are deliberately excluded:
the tool loop already hands their errors to the model, which decides whether
repeating the call is safe. Blindly retrying a delete or a write can double-apply
it, and the model — which can see the result — is the right place to make that
call.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 0.25
DEFAULT_MAX_DELAY = 2.0

# Status codes worth a second try: the server is up but momentarily unable
# (overloaded / restarting / rate-limiting). 4xx client errors are not retried —
# the request itself is wrong, and repeating it just spends another round trip.
_TRANSIENT_STATUS = frozenset({429, 502, 503, 504})


def is_transient_http_error(exc: BaseException) -> bool:
    """True for HTTP failures a retry can plausibly clear."""
    if isinstance(exc, httpx.TransportError):
        # ConnectError, Read/Write/PoolTimeout, RemoteProtocolError,
        # ServerDisconnected — the connection layer, not the application.
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_STATUS
    return False


# Substrings that mark a *string* error as transient. Tool handlers report
# failures as ``ToolResult.error`` strings, so the exception-type predicate above
# cannot see them.
_TRANSIENT_MESSAGE_MARKERS = (
    "disk i/o error",
    "database is locked",
    "database table is locked",
    "unable to open database",
    "connection reset",
    "connection refused",
    "connection aborted",
    "temporarily unavailable",
    "timed out",
    "timeout",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "too many requests",
)


def is_transient_error_message(message: str) -> bool:
    """True when an error string looks like a transient failure worth retrying."""
    text = (message or "").lower()
    return any(marker in text for marker in _TRANSIENT_MESSAGE_MARKERS)


async def retry_transient(
    fn: Callable[[], Awaitable],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    label: str = "request",
    retry_on: Callable[[BaseException], bool] = is_transient_http_error,
):
    """Await ``fn()``, retrying transient failures with jittered backoff.

    ``fn`` is called again from scratch on each attempt, so it must be
    idempotent. Each retry is logged at WARNING so a flapping dependency stays
    visible rather than being silently absorbed.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            if attempt >= attempts or not retry_on(exc):
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delay *= random.uniform(0.8, 1.2)
            logger.warning(
                "%s failed (attempt %d/%d): %s; retrying in %.2fs",
                label,
                attempt,
                attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
