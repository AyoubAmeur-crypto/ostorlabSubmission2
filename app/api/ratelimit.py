"""Per-tenant rate limit on the public API: a fixed one-minute window in Redis.

Each authenticated request increments ``<prefix>:<tenant_id>:<window>``
(``window`` = whole windows since the epoch on ``app.core.clock``). Over the
limit: 429 with ``Retry-After`` = seconds until the next window. If Redis is
unreachable the request is let through and ``ratelimit.unavailable`` logged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import redis
from fastapi import Request, Response

from app.api.deps import TenantDep
from app.core import clock
from app.core.config import get_settings
from app.core.errors import RateLimited
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitState:
    limit: int
    remaining: int
    reset_after_s: int


class FixedWindowRateLimiter:
    def __init__(
        self,
        client: redis.Redis,
        *,
        window_s: int = 60,
        limit: int | None = None,
        prefix: str = "tw:ratelimit",
    ) -> None:
        self.client = client
        self.window_s = window_s
        self.prefix = prefix
        self._limit = limit  # None: TW_RATE_LIMIT_PER_MINUTE, read on every hit

    @classmethod
    def from_settings(cls) -> FixedWindowRateLimiter:
        client = redis.Redis.from_url(
            get_settings().redis_url, socket_timeout=0.5, socket_connect_timeout=0.5
        )
        return cls(client)

    def hit(self, tenant_id: int) -> RateLimitState | None:
        """Count one request. Returns the window state, or None when Redis is
        unavailable. Raises RateLimited when the tenant is over the limit."""
        limit = self._limit or get_settings().rate_limit_per_minute
        now = clock.now().timestamp()
        window = int(now // self.window_s)
        reset_after = max(1, math.ceil((window + 1) * self.window_s - now))
        key = f"{self.prefix}:{tenant_id}:{window}"
        try:
            pipe = self.client.pipeline()
            pipe.incr(key)
            pipe.expire(key, self.window_s)
            count = int(pipe.execute()[0])
        except redis.RedisError as exc:
            log.warning("ratelimit.unavailable", extra={"tenant_id": tenant_id, "error": str(exc)})
            return None
        if count > limit:
            raise RateLimited(reset_after)
        return RateLimitState(limit=limit, remaining=limit - count, reset_after_s=reset_after)


def enforce_rate_limit(request: Request, response: Response, ctx: TenantDep) -> None:
    """Router dependency for every tenant route (installed in app.api.main)."""
    limiter: FixedWindowRateLimiter = request.app.state.rate_limiter
    state = limiter.hit(ctx.tenant_id)
    if state is not None:
        response.headers["X-RateLimit-Limit"] = str(state.limit)
        response.headers["X-RateLimit-Remaining"] = str(state.remaining)
