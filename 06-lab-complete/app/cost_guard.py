from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException


PRICE_PER_1K_INPUT_TOKENS = 0.00015
PRICE_PER_1K_OUTPUT_TOKENS = 0.0006


class RedisCostGuard:
    def __init__(self, redis_client, *, monthly_budget_usd: float = 10.0):
        self.redis = redis_client
        self.monthly_budget_usd = monthly_budget_usd

    def check_budget(self, user_id: str) -> float:
        used_usd = self.current_spend(user_id)
        if used_usd >= self.monthly_budget_usd:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "Monthly budget exceeded",
                    "used_usd": round(used_usd, 6),
                    "budget_usd": self.monthly_budget_usd,
                    "resets_at": "start of next UTC month",
                },
            )
        return used_usd

    def record_usage(
        self,
        user_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> dict[str, float]:
        used_usd = self.current_spend(user_id)
        request_cost = self.calculate_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        updated_total = used_usd + request_cost
        self.redis.setex(
            self._budget_key(user_id),
            self._ttl_seconds_until_next_month(),
            json.dumps(updated_total),
        )
        return {
            "request_cost_usd": round(request_cost, 6),
            "used_usd": round(updated_total, 6),
            "budget_usd": self.monthly_budget_usd,
            "remaining_usd": round(max(0.0, self.monthly_budget_usd - updated_total), 6),
        }

    def current_spend(self, user_id: str) -> float:
        raw_value = self.redis.get(self._budget_key(user_id))
        return float(json.loads(raw_value)) if raw_value else 0.0

    def calculate_cost(self, *, input_tokens: int, output_tokens: int) -> float:
        input_cost = (input_tokens / 1000) * PRICE_PER_1K_INPUT_TOKENS
        output_cost = (output_tokens / 1000) * PRICE_PER_1K_OUTPUT_TOKENS
        return input_cost + output_cost

    def _budget_key(self, user_id: str) -> str:
        month_key = datetime.now(timezone.utc).strftime("%Y-%m")
        return f"budget:{user_id}:{month_key}"

    def _ttl_seconds_until_next_month(self) -> int:
        now = datetime.now(timezone.utc)
        if now.month == 12:
            next_month = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
        return max(1, int((next_month - now).total_seconds()))
