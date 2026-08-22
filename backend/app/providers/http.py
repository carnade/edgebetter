"""Shared HTTP helper. Providers stay thin so an upstream shape change is a one-file fix."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class ProviderError(RuntimeError):
    """Upstream failed. Callers decide whether that is fatal or tolerable."""


def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = 2,
    backoff: float = 1.5,
    timeout: httpx.Timeout | None = None,
) -> Any:
    """GET and parse JSON, retrying transient failures with exponential backoff.

    A non-JSON or empty body is treated as failure rather than as empty data -- some
    upstreams answer a throttled request with an empty 200 rather than an error status.
    """
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = httpx.get(
                url, params=params, headers=headers, timeout=timeout or DEFAULT_TIMEOUT
            )
            resp.raise_for_status()
            if not resp.content.strip():
                raise ProviderError(f"empty body from {url}")
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - retry policy is uniform across failure kinds
            last = exc
            if attempt < retries:
                delay = backoff**attempt
                log.debug("GET %s failed (%s); retrying in %.1fs", url, exc, delay)
                time.sleep(delay)
    raise ProviderError(f"GET {url} failed after {retries + 1} attempts: {last}") from last
