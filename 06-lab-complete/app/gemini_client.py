from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.chat_service import LLMReply, ProviderUnavailableError


@dataclass
class GeminiClient:
    api_key: str
    model: str
    timeout_seconds: float
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    def __call__(self, question: str, history: list[dict]) -> LLMReply:
        contents = []
        for item in history:
            role = "model" if item["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": item["content"]}]})

        if not contents:
            contents = [{"role": "user", "parts": [{"text": question}]}]

        url = f"{self.base_url}/models/{self.model}:generateContent"
        try:
            response = httpx.post(
                url,
                params={"key": self.api_key},
                json={"contents": contents},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Gemini request failed") from exc

        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise ProviderUnavailableError("Gemini returned no candidates")

        text = "".join(
            part.get("text", "")
            for part in candidates[0].get("content", {}).get("parts", [])
        ).strip()
        if not text:
            raise ProviderUnavailableError("Gemini returned an empty response")

        usage = data.get("usageMetadata", {})
        return LLMReply(
            text=text,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
        )


def build_llm(settings):
    if settings.llm_provider == "gemini":
        return GeminiClient(
            api_key=settings.gemini_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.gemini_timeout_seconds,
            base_url=settings.gemini_api_base_url,
        )

    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")
