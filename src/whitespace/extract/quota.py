"""Per-process Azure request and estimated token rate limits."""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable

from whitespace.extract.errors import ExtractorStop
from whitespace.ingest.base import RateLimiter

DEFAULT_RPM = 1212
DEFAULT_TPM = 1_212_000


def estimated_tokens(body: dict) -> int:
    """Conservative UTF-8 byte estimate plus the requested maximum completion tokens.

    This is not Azure's internal rate-limit estimate or the model's billed token count.
    """
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return len(payload) + int(body.get("max_completion_tokens", 0)) + 64


class TokenRateLimiter:
    """Reserve estimated tokens in a rolling minute, then apply request pacing."""

    def __init__(
        self,
        tpm: int,
        request_limiter: RateLimiter,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if tpm < 0:
            raise ValueError("tpm must be non-negative")
        self.tpm, self.request_limiter, self.clock, self.sleep = tpm, request_limiter, clock, sleep
        self._events: deque[tuple[float, int]] = deque()
        self._used = 0

    def _expire(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= 60:
            self._used -= self._events.popleft()[1]

    def wait(self, tokens: int) -> None:
        if self.tpm and tokens > self.tpm:
            raise ExtractorStop(
                f"Estimated request size ({tokens} tokens) exceeds the configured "
                f"tokens/min limit ({self.tpm}); increase it or reduce the request size"
            )
        if self.tpm:
            while True:
                now = self.clock()
                self._expire(now)
                if self._used + tokens <= self.tpm:
                    break
                self.sleep(max(0.0, 60 - (now - self._events[0][0])))
        self.request_limiter.wait()
        if self.tpm:
            now = self.clock()
            self._expire(now)
            self._events.append((now, tokens))
            self._used += tokens
