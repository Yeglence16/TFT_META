import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.riot_client import (
    RiotApiKeyExpiredError,
    RiotClient,
    RiotRateLimitExceededError,
    _TokenBucket,
)


def make_response(status_code, json_data=None, headers=None):
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = json_data

    def raise_for_status():
        if status_code >= 400:
            raise RuntimeError(f"HTTP {status_code}")

    response.raise_for_status.side_effect = raise_for_status
    return response


@pytest.fixture
def client():
    c = RiotClient("fake-key")
    yield c
    c.close()


def test_200_returns_json(client):
    client._http.get = MagicMock(return_value=make_response(200, {"ok": True}))
    assert client.get("http://x") == {"ok": True}
    assert client._http.get.call_count == 1


def test_404_returns_none_and_does_not_raise(client):
    client._http.get = MagicMock(return_value=make_response(404))
    assert client.get("http://x") is None


def test_403_raises_immediately_with_no_retry(client):
    client._http.get = MagicMock(return_value=make_response(403))
    with pytest.raises(RiotApiKeyExpiredError):
        client.get("http://x")
    assert client._http.get.call_count == 1


def test_401_raises_immediately_with_no_retry(client):
    # Regression: a dead key returned 401 (not 403)
    # on /tft/match/v1/matches/{matchId}, which previously fell through to
    # the final raise_for_status() and crashed with a raw HTTPStatusError
    # instead of the clean, resumable RiotApiKeyExpiredError path.
    client._http.get = MagicMock(return_value=make_response(401))
    with pytest.raises(RiotApiKeyExpiredError):
        client.get("http://x")
    assert client._http.get.call_count == 1


def test_429_honors_retry_after_then_succeeds(client, monkeypatch):
    sleeps = []
    monkeypatch.setattr("src.riot_client.time.sleep", lambda s: sleeps.append(s))
    client._http.get = MagicMock(
        side_effect=[
            make_response(429, headers={"Retry-After": "2"}),
            make_response(200, {"ok": True}),
        ]
    )
    assert client.get("http://x") == {"ok": True}
    assert sleeps == [2.0]


def test_429_raises_after_max_attempts(monkeypatch):
    client = RiotClient("fake-key", max_429_attempts=3)
    monkeypatch.setattr("src.riot_client.time.sleep", lambda s: None)
    client._http.get = MagicMock(return_value=make_response(429, headers={"Retry-After": "0"}))
    with pytest.raises(RiotRateLimitExceededError):
        client.get("http://x")
    assert client._http.get.call_count == 3
    client.close()


def test_5xx_retries_then_raises(monkeypatch):
    client = RiotClient("fake-key", max_5xx_attempts=3)
    monkeypatch.setattr("src.riot_client.time.sleep", lambda s: None)
    client._http.get = MagicMock(return_value=make_response(500))
    with pytest.raises(RuntimeError):
        client.get("http://x")
    assert client._http.get.call_count == 3
    client.close()


def test_5xx_recovers_within_attempt_budget(monkeypatch):
    client = RiotClient("fake-key", max_5xx_attempts=3)
    monkeypatch.setattr("src.riot_client.time.sleep", lambda s: None)
    client._http.get = MagicMock(
        side_effect=[make_response(503), make_response(200, {"ok": True})]
    )
    assert client.get("http://x") == {"ok": True}
    client.close()


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_token_bucket_blocks_once_window_is_full(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr("src.riot_client.time.monotonic", clock.monotonic)
    monkeypatch.setattr("src.riot_client.time.sleep", clock.sleep)

    bucket = _TokenBucket(max_requests=2, window_seconds=1.0)
    bucket.wait_for_slot()
    bucket.wait_for_slot()
    assert clock.now == 0.0  # first two slots are free, no waiting

    bucket.wait_for_slot()
    assert clock.now == pytest.approx(1.0)  # third had to wait out the window


def test_get_waits_on_both_short_and_long_buckets(client):
    client._short_bucket.wait_for_slot = MagicMock()
    client._long_bucket.wait_for_slot = MagicMock()
    client._http.get = MagicMock(return_value=make_response(200, {"ok": True}))

    client.get("http://x")

    client._short_bucket.wait_for_slot.assert_called_once()
    client._long_bucket.wait_for_slot.assert_called_once()
