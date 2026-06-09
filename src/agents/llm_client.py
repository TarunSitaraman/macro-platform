"""Multi-provider LLM client with per-provider cooldown and fresh connections per call."""

import asyncio
import json
import logging
import time
from typing import Any, Optional

import httpx

from src.config import MODEL_ROUTES, get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Per-provider cooldown: maps provider name → monotonic time when it becomes available again.
# Module-level so it persists across calls within the same Streamlit session process.
_provider_cooldown: dict[str, float] = {}
_COOLDOWN_SECS = 90  # how long to skip a provider after a 429


class LLMError(Exception):
    """Raised when all candidates in a tier's fallback chain are exhausted."""


def _make_headers(provider: str) -> dict[str, str]:
    keys = {
        "groq": settings.groq_api_key,
        "gemini": settings.gemini_api_key,
        "openrouter": settings.openrouter_api_key,
    }
    key = keys.get(provider, "")
    if not key:
        return {}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://macro-platform.io"
        headers["X-Title"] = "Macro Intelligence Platform"
    return headers


def _base_url(provider: str) -> str:
    return {
        "groq": settings.groq_base_url,
        "gemini": settings.gemini_base_url,
        "openrouter": settings.openrouter_base_url,
    }[provider]


def _has_key(provider: str) -> bool:
    return bool({
        "groq": settings.groq_api_key,
        "gemini": settings.gemini_api_key,
        "openrouter": settings.openrouter_api_key,
    }.get(provider, ""))


class LLMClient:
    """
    Sends chat completion requests with per-tier provider fallback chains.

    Creates a fresh httpx.AsyncClient per provider call to avoid stale TLS connections
    (the original singleton pattern caused ConnectError after idle periods).
    Per-provider 429 cooldown state is tracked at module level across calls.
    """

    async def chat(
        self,
        messages: list[dict],
        tier: str = "medium",
        response_format: Optional[dict] = None,
        system: Optional[str] = None,
    ) -> tuple[str, str]:
        """Try each candidate in order, skipping providers in cooldown. Returns (content, model_used)."""
        route = MODEL_ROUTES[tier]

        all_messages: list[dict] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        now = time.monotonic()
        failures: list[str] = []
        last_exc: Optional[Exception] = None

        for candidate in route["candidates"]:
            provider = candidate["provider"]
            model = candidate["model"]

            if not _has_key(provider):
                failures.append(f"{provider}/{model}: skipped (API key not set)")
                continue

            cooldown_until = _provider_cooldown.get(provider, 0)
            if cooldown_until > now:
                remaining = int(cooldown_until - now)
                failures.append(f"{provider}/{model}: skipped (rate-limited, {remaining}s cooldown remaining)")
                continue

            try:
                content = await self._call(
                    provider=provider,
                    model=model,
                    messages=all_messages,
                    max_tokens=route["max_tokens"],
                    temperature=route["temperature"],
                    response_format=response_format,
                )
                logger.debug("LLM success: provider=%s model=%s tier=%s", provider, model, tier)
                return content, f"{provider}/{model}"
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    _provider_cooldown[provider] = time.monotonic() + _COOLDOWN_SECS
                    msg = f"{provider}/{model}: 429 — cooling down for {_COOLDOWN_SECS}s"
                else:
                    msg = f"{provider}/{model}: HTTP {exc.response.status_code}"
                logger.warning("LLM failed — %s", msg)
                failures.append(msg)
                last_exc = exc
            except Exception as exc:
                error_str = str(exc) or type(exc).__name__
                msg = f"{provider}/{model}: {error_str}"
                logger.warning("LLM failed — %s", msg)
                failures.append(msg)
                last_exc = exc

        raise LLMError(
            f"All models in tier '{tier}' failed:\n" + "\n".join(f"  • {f}" for f in failures)
        ) from last_exc

    async def _call(
        self,
        provider: str,
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

        # Fresh client per call — avoids stale TLS connections from the old singleton pattern
        async with httpx.AsyncClient(
            base_url=_base_url(provider),
            headers=_make_headers(provider),
            timeout=60.0,
        ) as http:
            for attempt in range(2):
                resp = await http.post("/chat/completions", json=payload)

                if resp.status_code == 429:
                    # Raise immediately so the caller can set cooldown and move to next provider
                    resp.raise_for_status()

                if resp.status_code == 413 and attempt == 0:
                    payload["messages"] = _truncate_messages(payload["messages"])
                    continue

                resp.raise_for_status()
                break

        data = resp.json()
        # Some providers (OpenRouter) return HTTP 200 with {"error": {...}} on quota/model errors
        if "choices" not in data:
            error_info = data.get("error", data)
            msg = error_info.get("message", str(error_info)) if isinstance(error_info, dict) else str(error_info)
            raise ValueError(f"Provider error: {msg}")
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


def _truncate_messages(messages: list[dict], keep_ratio: float = 0.5) -> list[dict]:
    """Shorten the last user message to fit within a smaller model's context window."""
    result = list(messages)
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") == "user" and isinstance(result[i].get("content"), str):
            original = result[i]["content"]
            result[i] = {**result[i], "content": original[: int(len(original) * keep_ratio)]}
            break
    return result


def get_llm_client() -> LLMClient:
    """Return an LLMClient instance. Stateless — safe to call per request."""
    return LLMClient()
