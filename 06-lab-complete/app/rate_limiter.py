from __future__ import annotations

import json
import math
import time

from fastapi import HTTPException


class RedisRateLimiter:
    def __init__(
        self,
        redis_client,
        *,
        max_requests: int = 10,
        window_seconds: int = 60,
    ):
        self.redis = redis_client
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def check(self, user_id: str) -> dict[str, int]:
        now = time.time()
        key = f"rate_limit:{user_id}"
        active_window = [
            timestamp
            for timestamp in self._load_timestamps(key)
            if timestamp > now - self.window_seconds
        ]

        if len(active_window) >= self.max_requests:
            retry_after = max(
                1,
                math.ceil(active_window[0] + self.window_seconds - now),
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Rate limit exceeded",
                    "limit": self.max_requests,
                    "window_seconds": self.window_seconds,
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        active_window.append(now)
        self.redis.setex(key, self.window_seconds, json.dumps(active_window))

        return {
            "limit": self.max_requests,
            "remaining": self.max_requests - len(active_window),
            "window_seconds": self.window_seconds,
        }

    def _load_timestamps(self, key: str) -> list[float]:
        raw_value = self.redis.get(key)
        if not raw_value:
            return []
        return [float(item) for item in json.loads(raw_value)]
