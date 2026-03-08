"""
LLMClient — OpenRouter streaming client.

Supports:
- Text-only and vision models (base64 image in messages)
- Web search via OpenRouter native :online suffix — works for any model,
  no tool calls or third-party search needed
- SSE streaming: yields text chunks as they arrive
"""

import base64
import json
import logging
from typing import AsyncIterator, Optional

import aiohttp
from infrastructure.logging.logger import setup_logger

logger = setup_logger("LLMClient")

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Models that support vision (image input)
VISION_MODELS = {
    "anthropic/claude-opus-4.6",
    "openai/gpt-5.1",
    "openai/gpt-5.4",
}


class LLMClient:
    def __init__(
        self,
        api_key: str,
        model: str = "anthropic/claude-opus-4.6",
        temperature: float = 0.7,
        top_p: float = 0.9,
    ):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.top_p = top_p

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/your-own-app",
            "X-Title": "Your Own",
        }

    def _resolve_model(self, web_search: bool) -> str:
        """
        Appends :online to the model slug when web search is requested.
        OpenRouter handles the rest — native search for Anthropic/OpenAI/xAI,
        Exa-powered search for all other models.
        """
        if not web_search:
            return self.model
        # Strip any existing :online to avoid duplication
        base = self.model.rstrip(":online").removesuffix(":online")
        return f"{base}:online"

    def _build_messages(
        self,
        messages: list[dict],
        image_items: Optional[list[tuple[bytes, str]]] = None,
        geo: Optional[dict] = None,
        system_prompt: Optional[str] = None,
    ) -> list[dict]:
        """
        Converts message list to OpenRouter format.
        - Injects geo context as text into the last user message
        - Attaches one or more images (base64) to the last user message for vision models
        """
        result = []

        if system_prompt:
            result.append({"role": "system", "content": system_prompt})

        for i, msg in enumerate(messages):
            is_last_user = msg["role"] == "user" and i == len(messages) - 1

            if is_last_user and (image_items or geo):
                content: list = []

                text = msg.get("content", "")
                if geo:
                    text += f"\n\n[User location: lat={geo.get('lat')}, lon={geo.get('lon')}]"
                if text:
                    content.append({"type": "text", "text": text})

                if image_items and self.model in VISION_MODELS:
                    for image_bytes, image_mime in image_items:
                        b64 = base64.b64encode(image_bytes).decode()
                        content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{image_mime};base64,{b64}"},
                        })

                result.append({"role": "user", "content": content})
            else:
                result.append({"role": msg["role"], "content": msg.get("content", "")})

        return result

    async def stream(
        self,
        messages: list[dict],
        web_search: bool = False,
        image_items: Optional[list[tuple[bytes, str]]] = None,
        geo: Optional[dict] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """
        Streams text chunks from OpenRouter.
        When web_search=True, appends :online to the model slug —
        OpenRouter automatically injects real-time web results.
        """
        model = self._resolve_model(web_search)
        built_messages = self._build_messages(messages, image_items, geo, system_prompt)
        logger.info(
            "[LLMClient] stream start model=%s web_search=%s messages=%d has_system=%s images=%d",
            model,
            web_search,
            len(built_messages),
            bool(system_prompt),
            len(image_items or []),
        )

        payload = {
            "model": model,
            "messages": built_messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "stream": True,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OPENROUTER_BASE}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as resp:
                logger.info("[LLMClient] response status=%d model=%s", resp.status, model)
                if resp.status != 200:
                    error_body = await resp.text()
                    logger.error("[LLMClient] OpenRouter %d: %s", resp.status, error_body)
                    yield f"[OpenRouter error {resp.status}]"
                    return

                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break

                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choices = obj.get("choices") or []
                    if not choices:
                        # OpenRouter may emit non-token SSE payloads without choices.
                        continue

                    delta = choices[0].get("delta") or {}
                    chunk = delta.get("content")
                    if chunk:
                        yield chunk
