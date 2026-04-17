from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import HTTPException


@dataclass
class LLMReply:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderUnavailableError(RuntimeError):
    pass


class ChatService:
    def __init__(
        self,
        *,
        settings,
        redis_client,
        rate_limiter,
        cost_guard,
        llm_func: Callable[[str, list[dict]], str | LLMReply],
        load_history,
        save_history,
    ):
        self.settings = settings
        self.redis = redis_client
        self.rate_limiter = rate_limiter
        self.cost_guard = cost_guard
        self.llm_func = llm_func
        self.load_history = load_history
        self.save_history = save_history

    def ask(self, *, user_id: str, question: str) -> dict:
        self.cost_guard.check_budget(user_id)
        rate_info = self.rate_limiter.check(user_id)
        history = self.load_history(self.redis, user_id)

        user_message = {
            "role": "user",
            "content": question,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        history_with_question = history + [user_message]
        llm_context = history_with_question[-self.settings.model_context_messages :]

        try:
            raw_reply = self.llm_func(question, llm_context)
        except ProviderUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail="The bot is temporarily unavailable. Please try again.",
            ) from exc

        reply = raw_reply if isinstance(raw_reply, LLMReply) else LLMReply(text=str(raw_reply))

        assistant_message = {
            "role": "assistant",
            "content": reply.text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        updated_history = (history_with_question + [assistant_message])[
            -self.settings.conversation_history_limit :
        ]
        self.save_history(
            self.redis,
            user_id,
            updated_history,
            ttl_seconds=self.settings.conversation_ttl_seconds,
        )

        input_tokens = reply.input_tokens or len(question.split()) * 2
        output_tokens = reply.output_tokens or len(reply.text.split()) * 2
        usage = self.cost_guard.record_usage(
            user_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        usage.update(rate_info)
        usage["context_messages_used"] = len(llm_context)

        return {
            "user_id": user_id,
            "question": question,
            "answer": reply.text,
            "history_length": len(updated_history),
            "usage": usage,
        }
