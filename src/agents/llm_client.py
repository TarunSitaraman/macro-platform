"""Multi-provider LLM client: Gemini (primary) → OpenRouter (fallback)."""

import json
import logging
from typing import Any, Optional

import httpx

from src.config import MODEL_ROUTES, get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class LLMError(Exception):
    """Raised when all candidates in a tier's fallback chain are exhausted."""


class LLMClient:
    """Sends chat completion requests with per-tier provider fallback chains."""

    def __init__(self):
        def _client(base_url: str, key: str, extra_headers: dict | None = None) -> httpx.AsyncClient | None:
            if not key:
                return None
            return httpx.AsyncClient(
                base_url=base_url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", **(extra_headers or {})},
                timeout=120.0,
            )

        self._groq = _client(settings.groq_base_url, settings.groq_api_key)
        self._gemini = _client(settings.gemini_base_url, settings.gemini_api_key)
        self._openrouter = _client(
            settings.openrouter_base_url,
            settings.openrouter_api_key,
            {"HTTP-Referer": "https://hexaware-macro-platform.io", "X-Title": "Hexaware Macro Platform"},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        for c in (self._groq, self._gemini, self._openrouter):
            if c:
                await c.aclose()

    async def chat(
        self,
        messages: list[dict],
        tier: str = "medium",
        response_format: Optional[dict] = None,
        system: Optional[str] = None,
    ) -> tuple[str, str]:
        """
        Send a chat completion request, trying each candidate in order.
        Returns (content, model_used).
        """
        route = MODEL_ROUTES[tier]

        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        failures: list[str] = []
        last_exc: Optional[Exception] = None
        for candidate in route["candidates"]:
            provider = candidate["provider"]
            model = candidate["model"]

            http = {"groq": self._groq, "gemini": self._gemini, "openrouter": self._openrouter}.get(provider)
            if not http:
                failures.append(f"{provider}/{model}: skipped (API key not set)")
                continue

            try:
                content = await self._call(
                    http=http,
                    model=model,
                    messages=all_messages,
                    max_tokens=route["max_tokens"],
                    temperature=route["temperature"],
                    response_format=response_format,
                )
                logger.debug("LLM success: provider=%s model=%s tier=%s", provider, model, tier)
                return content, f"{provider}/{model}"
            except Exception as exc:
                msg = f"{provider}/{model}: {exc}"
                logger.warning("LLM failed — %s", msg)
                failures.append(msg)
                last_exc = exc

        raise LLMError(
            f"All models in tier '{tier}' failed:\n" + "\n".join(f"  • {f}" for f in failures)
        ) from last_exc

    async def _call(
        self,
        http: httpx.AsyncClient,
        model: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        response_format: Optional[dict],
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format

        resp = await http.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def extract_json(
        self,
        prompt: str,
        system: str,
        tier: str = "medium",
    ) -> tuple[Any, str]:
        """Call LLM and parse response as JSON. Returns (parsed_obj, model_used)."""
        content, model = await self.chat(
            messages=[{"role": "user", "content": prompt}],
            system=system,
            tier=tier,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(content), model
        except json.JSONDecodeError:
            cleaned = content.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            return json.loads(cleaned), model


_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
