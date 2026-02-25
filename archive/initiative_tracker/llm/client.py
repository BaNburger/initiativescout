from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)


class LLMClient:
    """Abstract base for LLM API clients."""

    def score_dossier(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        raise NotImplementedError


class AnthropicClient(LLMClient):
    def __init__(self, model: str = "claude-haiku-4-5-20241022", api_key: str | None = None):
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError("Install the 'anthropic' package: pip install anthropic") from exc
        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def score_dossier(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        # Extract JSON from response (handle markdown code blocks)
        if text.startswith("```"):
            lines = text.split("\n")
            start = 1 if lines[0].startswith("```") else 0
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            text = "\n".join(lines[start:end])
        return json.loads(text)


class OpenAIClient(LLMClient):
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        try:
            import openai
        except ImportError as exc:
            raise ImportError("Install the 'openai' package: pip install openai") from exc
        self.model = model
        kwargs: dict[str, Any] = {}
        if api_key or os.environ.get("OPENAI_API_KEY"):
            kwargs["api_key"] = api_key or os.environ.get("OPENAI_API_KEY")
        if base_url or os.environ.get("OPENAI_BASE_URL"):
            kwargs["base_url"] = base_url or os.environ.get("OPENAI_BASE_URL")
        self._client = openai.OpenAI(**kwargs)

    def score_dossier(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=2048,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = response.choices[0].message.content or "{}"
        return json.loads(text)


def get_llm_client(config: dict[str, Any] | None = None) -> LLMClient:
    config = config or {}
    provider = config.get("provider") or os.environ.get("LLM_PROVIDER", "anthropic")
    model = config.get("model") or os.environ.get("LLM_MODEL", "")
    api_key = config.get("api_key") or None
    base_url = config.get("base_url") or None

    if provider == "anthropic":
        return AnthropicClient(
            model=model or "claude-haiku-4-5-20241022",
            api_key=api_key,
        )
    elif provider in ("openai", "openai_compatible"):
        return OpenAIClient(
            model=model or "gpt-4o-mini",
            api_key=api_key,
            base_url=base_url,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider!r}. Use 'anthropic' or 'openai'.")
