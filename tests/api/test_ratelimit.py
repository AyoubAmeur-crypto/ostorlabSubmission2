import redis
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.ratelimit import FixedWindowRateLimiter
from app.core.config import override_settings


def test_responses_carry_limit_headers(client, acme, frozen_clock):
    with override_settings(rate_limit_per_minute=5):
        first = client.get("/v1/assets", headers=acme.headers)
        second = client.get("/v1/assets", headers=acme.headers)
    assert first.headers["X-RateLimit-Limit"] == "5"
    assert first.headers["X-RateLimit-Remaining"] == "4"
    assert second.headers["X-RateLimit-Remaining"] == "3"


def test_over_the_limit_is_429_until_the_next_window(client, acme, northpeak, frozen_clock):
    frozen_clock.advance(seconds=15)  # 09:00:15, 45 s left in the window
    with override_settings(rate_limit_per_minute=2):
        assert client.get("/v1/assets", headers=acme.headers).status_code == 200
        last = client.get("/v1/assets", headers=acme.headers)
        assert (last.status_code, last.headers["X-RateLimit-Remaining"]) == (200, "0")  # 1.9.1
        refused = client.get("/v1/assets", headers=acme.headers)
        assert refused.status_code == 429
        assert refused.headers["Retry-After"] == "45"
        assert refused.json()["error"]["code"] == "rate_limited"
        assert client.get("/v1/assets", headers=northpeak.headers).status_code == 200

        frozen_clock.advance(seconds=45)
        assert client.get("/v1/assets", headers=acme.headers).status_code == 200


def test_redis_outage_fails_open(db_session, acme):
    unreachable = redis.Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    client = TestClient(create_app(rate_limiter=FixedWindowRateLimiter(unreachable)))
    response = client.get("/v1/assets", headers=acme.headers)
    assert response.status_code == 200
    assert "X-RateLimit-Limit" not in response.headers
