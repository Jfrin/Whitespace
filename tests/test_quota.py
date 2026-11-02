import pytest

from whitespace.extract.errors import ExtractorStop
from whitespace.extract.quota import TokenRateLimiter, estimated_tokens


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class RequestLimiter:
    def __init__(self, clock):
        self.clock = clock
        self.calls = 0

    def wait(self):
        self.calls += 1
        self.clock.now += 1


def test_token_budget_waits_for_rolling_window_and_records_after_request_pacing():
    clock = FakeClock()
    requests = RequestLimiter(clock)
    limiter = TokenRateLimiter(10, requests, clock=clock.clock, sleep=clock.sleep)
    limiter.wait(6)  # sent at t=1, after request pacing
    limiter.wait(4)  # sent at t=2
    limiter.wait(5)  # first reservation expires at t=61
    assert clock.sleeps == [59]
    assert clock.now == 62 and requests.calls == 3


def test_token_budget_rejects_oversized_request_and_supports_zero():
    clock = FakeClock()
    requests = RequestLimiter(clock)
    limiter = TokenRateLimiter(10, requests, clock=clock.clock, sleep=clock.sleep)
    with pytest.raises(ExtractorStop, match="exceeds the configured"):
        limiter.wait(11)
    assert requests.calls == 0
    TokenRateLimiter(0, requests, clock=clock.clock, sleep=clock.sleep).wait(1000)
    assert requests.calls == 1
    with pytest.raises(ValueError, match="non-negative"):
        TokenRateLimiter(-1, requests)


def test_estimate_includes_utf8_payload_and_maximum_completion_tokens():
    base = {"messages": [{"content": "é"}], "max_completion_tokens": 10}
    assert estimated_tokens({**base, "max_completion_tokens": 20}) > estimated_tokens(base)
    assert estimated_tokens(base) > 10 + len("é".encode())
