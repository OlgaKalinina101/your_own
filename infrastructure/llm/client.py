"""
LLMClient — OpenRouter streaming client.

Supports:
- Text-only and vision models (base64 image in messages)
- Web search via OpenRouter's openrouter:web_search server tool — works for any
  model, OpenRouter runs the search server-side (the :online suffix and the
  plugins:[{id:"web"}] form are both deprecated as of 2026)
- SSE streaming: yields text chunks as they arrive
- Image generation via modalities: ["image", "text"] (non-streaming call)

All calls log to logs/debug_dataset.jsonl (system, messages, response) for debugging.
"""

import asyncio
import base64
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

import aiohttp
from infrastructure.logging.logger import setup_logger

logger = setup_logger("LLMClient")

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_DEBUG_DATASET_PATH = Path(__file__).resolve().parent.parent.parent / "logs" / "debug_dataset.jsonl"
_DEBUG_LOCK = threading.Lock()
_MAX_DEBUG_FIELD_CHARS = 100_000


def _truncate(s: str, max_len: int = _MAX_DEBUG_FIELD_CHARS) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"... [truncated, total {len(s)} chars]"


def _sanitize_content(content) -> str | list:
    """Replace base64 image data with placeholder for debug log."""
    if isinstance(content, str):
        return _truncate(content)
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                t = part.get("type", "")
                if t == "image_url":
                    url = (part.get("image_url") or {}).get("url", "")
                    out.append({"type": "image_url", "image_url": {"url": "[base64 image]" if "base64" in url else url[:80]}})
                elif t == "image":
                    out.append({"type": "image", "data": "[base64]"})
                else:
                    out.append(part)
            else:
                out.append(part)
        return out
    return content


def _sanitize_messages(msgs: list) -> list:
    out = []
    for m in msgs:
        role = m.get("role", "")
        content = m.get("content", "")
        out.append({"role": role, "content": _sanitize_content(content)})
    return out


def _append_debug_row(
    *,
    call_type: str,
    model: str,
    system: Optional[str] = None,
    messages: list,
    response: str,
    web_search: bool = False,
    citations: Optional[list] = None,
    error: Optional[str] = None,
) -> None:
    try:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "call_type": call_type,
            "model": model,
            "system": _truncate(system) if system else None,
            "messages": _sanitize_messages(messages),
            "response": _truncate(response),
            "web_search": web_search,
            "citations": citations or None,
            "error": error,
        }
        line = json.dumps(row, ensure_ascii=False) + "\n"
        _DEBUG_DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOCK:
            with _DEBUG_DATASET_PATH.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception as exc:
        logger.debug("[LLMClient] debug_dataset write failed: %s", exc)


# Models that support vision (image input)
VISION_MODELS = {
    "anthropic/claude-fable-5",
    "anthropic/claude-opus-4.6",
    "anthropic/claude-sonnet-4.6",
    "openai/gpt-5.1",
    "openai/gpt-5.4",
    "openai/gpt-5.5",
    "xiaomi/mimo-v2-omni",
}


class LLMClient:
    def __init__(
        self,
        api_key: str,
        model: str = "",
        temperature: float = 0.7,
        top_p: float = 0.9,
    ):
        from infrastructure.settings_store import DEFAULT_MODEL
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.temperature = temperature
        self.top_p = top_p

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/your-own-app",
            "X-Title": "Your Own",
        }

    # OpenRouter server tool that runs web search server-side and feeds the
    # results back to the model, which cites them via url_citation annotations.
    # Replaces the deprecated :online suffix / plugins:[{id:"web"}] form.
    _WEB_SEARCH_TOOL = {
        "type": "openrouter:web_search",
        "parameters": {"max_results": 5},
    }

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
        When web_search=True, attaches the openrouter:web_search server tool —
        OpenRouter runs the search server-side and the model grounds its reply
        in the results (citations arrive as url_citation annotations).
        """
        model = self.model
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
        if web_search:
            payload["tools"] = [self._WEB_SEARCH_TOOL]

        chunks: list[str] = []
        citations: list[dict] = []
        _seen_urls: set[str] = set()
        web_search_requests = 0
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
                    _log_msgs_err = built_messages[1:] if (built_messages and built_messages[0].get("role") == "system") else built_messages
                    _append_debug_row(
                        call_type="stream",
                        model=model,
                        system=system_prompt,
                        messages=_log_msgs_err,
                        response="",
                        web_search=web_search,
                        error=f"HTTP {resp.status}: {error_body[:500]}",
                    )
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

                    # web_search server tool reports its search count in usage
                    usage = obj.get("usage") or {}
                    stu = usage.get("server_tool_use_details") or {}
                    if stu.get("web_search_requests"):
                        web_search_requests = stu["web_search_requests"]

                    choices = obj.get("choices") or []
                    if not choices:
                        # OpenRouter may emit non-token SSE payloads without choices.
                        continue

                    delta = choices[0].get("delta") or {}

                    # Collect url_citation annotations (sources the model grounded on).
                    for ann in delta.get("annotations") or []:
                        if ann.get("type") != "url_citation":
                            continue
                        uc = ann.get("url_citation") or {}
                        url = uc.get("url")
                        if url and url not in _seen_urls:
                            _seen_urls.add(url)
                            citations.append({"url": url, "title": uc.get("title") or url})

                    chunk = delta.get("content")
                    if chunk:
                        chunks.append(chunk)
                        yield chunk

        if web_search:
            logger.info(
                "[LLMClient] web_search done requests=%d citations=%d",
                web_search_requests,
                len(citations),
            )

        full_response = "".join(chunks)
        # Separate system from the rest for readability: built_messages[0] is system if present
        _log_msgs = built_messages[1:] if (built_messages and built_messages[0].get("role") == "system") else built_messages
        _append_debug_row(
            call_type="stream",
            model=model,
            system=system_prompt,
            messages=_log_msgs,
            response=full_response,
            web_search=web_search,
            citations=citations,
        )

    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 650,
        temperature: float | None = None,
        return_meta: bool = False,
    ) -> str | tuple[str, str | None]:
        """Non-streaming single completion. Returns assistant text or '' on failure.

        When ``return_meta=True`` returns ``(text, finish_reason)`` so callers can
        detect truncation (``finish_reason == "length"`` means the model hit
        ``max_tokens`` and the text is cut off mid-thought — do not trust it as-is).
        """
        model = self.model
        temp = temperature if temperature is not None else self.temperature
        system = None
        if messages and messages[0].get("role") == "system":
            system = messages[0].get("content", "")
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temp,
            "top_p": self.top_p,
            "max_tokens": max_tokens,
            "stream": False,
        }
        timeout = aiohttp.ClientTimeout(total=60)
        for attempt in range(1, 4):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        f"{OPENROUTER_BASE}/chat/completions",
                        headers=self._headers(),
                        json=payload,
                    ) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            logger.warning(
                                "[LLMClient.complete] %d on attempt %d/3: %s",
                                resp.status, attempt, body[:200],
                            )
                            _append_debug_row(
                                call_type="complete",
                                model=model,
                                system=system,
                                messages=messages,
                                response="",
                                error=f"HTTP {resp.status}: {body[:500]}",
                            )
                        else:
                            data = await resp.json()
                            choices = data.get("choices") or []
                            if choices:
                                choice = choices[0]
                                # Provider may return an error inside choices
                                # with message=null — treat as a retryable failure.
                                if choice.get("error"):
                                    err = choice["error"]
                                    logger.warning(
                                        "[LLMClient.complete] provider error in choices on attempt %d/3: %s",
                                        attempt, err,
                                    )
                                    _append_debug_row(
                                        call_type="complete",
                                        model=model,
                                        system=system,
                                        messages=messages,
                                        response="",
                                        error=str(err),
                                    )
                                else:
                                    message = choice.get("message") or {}
                                    response = (message.get("content") or "").strip()
                                    finish_reason = choice.get("finish_reason")
                                    if finish_reason == "length":
                                        logger.warning(
                                            "[LLMClient.complete] response TRUNCATED "
                                            "(finish_reason=length, max_tokens=%d) — tail=%r",
                                            max_tokens, response[-60:],
                                        )
                                    _append_debug_row(
                                        call_type="complete",
                                        model=model,
                                        system=system,
                                        messages=messages,
                                        response=response,
                                        error=("finish_reason=length" if finish_reason == "length" else None),
                                    )
                                    return (response, finish_reason) if return_meta else response
            except Exception as exc:
                logger.warning("[LLMClient.complete] error on attempt %d/3: %s", attempt, exc)
                _append_debug_row(
                    call_type="complete",
                    model=model,
                    system=system,
                    messages=messages,
                    response="",
                    error=str(exc),
                )

            if attempt < 3:
                await asyncio.sleep(1.5 * attempt)

        return ("", None) if return_meta else ""

    # Models that output only images (no text) — require modalities: ["image"]
    _IMAGE_ONLY_PREFIXES = (
        "black-forest-labs/",
        "sourceful/",
        "bytedance-seed/",
        "x-ai/",
    )

    async def generate_image(self, prompt: str, model: str) -> str | None:
        """
        Non-streaming image generation via OpenRouter.
        Returns a base64 data URL string (data:image/png;base64,...) or None on failure.

        FLUX / Sourceful / ByteDance are image-only models → modalities: ["image"].
        GPT / Gemini output both text and image → modalities: ["image", "text"].
        """
        messages = [{"role": "user", "content": prompt}]
        image_only = any(model.startswith(p) for p in self._IMAGE_ONLY_PREFIXES)
        modalities = ["image"] if image_only else ["image", "text"]
        payload = {
            "model": model,
            "messages": messages,
            "modalities": modalities,
            "stream": False,
        }
        logger.info("[LLMClient] generate_image model=%s prompt=%s", model, prompt[:120])

        # Image responses contain large base64 payloads (~2MB). Use a generous
        # timeout and read the raw bytes first to avoid TransferEncodingError.
        timeout = aiohttp.ClientTimeout(total=300)
        connector = aiohttp.TCPConnector(force_close=True)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.post(
                f"{OPENROUTER_BASE}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as resp:
                logger.info("[LLMClient] generate_image status=%d model=%s", resp.status, model)
                if resp.status != 200:
                    error_body = await resp.text()
                    logger.error("[LLMClient] generate_image error %d: %s", resp.status, error_body)
                    _append_debug_row(
                        call_type="generate_image",
                        model=model,
                        messages=messages,
                        response="",
                        error=f"HTTP {resp.status}: {error_body[:500]}",
                    )
                    return None

                try:
                    # Read full bytes before JSON-parsing to avoid chunked-encoding truncation
                    raw = await resp.read()
                    body = json.loads(raw)
                except Exception as exc:
                    logger.error("[LLMClient] generate_image JSON parse error: %s", exc)
                    _append_debug_row(
                        call_type="generate_image",
                        model=model,
                        messages=messages,
                        response="",
                        error=str(exc),
                    )
                    return None

        # Log full body at DEBUG level so we can diagnose unexpected shapes
        import json as _json
        _body_preview = _json.dumps(body)[:1200]
        logger.info("[LLMClient] generate_image response body (truncated): %s", _body_preview)

        choices = body.get("choices") or []
        if not choices:
            logger.warning("[LLMClient] generate_image: no choices in response body=%s", _body_preview)
            _append_debug_row(
                call_type="generate_image",
                model=model,
                messages=messages,
                response="",
                error="no choices in response",
            )
            return None

        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content")

        # ── Shape 1: content is a list of typed parts ──────────────────────────
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                t = part.get("type", "")
                # {"type": "image_url", "image_url": {"url": "data:..."}}
                if t == "image_url":
                    url = (part.get("image_url") or {}).get("url", "")
                    if url:
                        logger.info("[LLMClient] generate_image: found image_url part")
                        _append_debug_row(
                            call_type="generate_image",
                            model=model,
                            messages=messages,
                            response=url,
                        )
                        return url
                # {"type": "image", "data": "base64...", "media_type": "image/png"}
                if t == "image":
                    data = part.get("data") or part.get("source", {}).get("data", "")
                    if data:
                        logger.info("[LLMClient] generate_image: found image part")
                        url = f"data:image/png;base64,{data}"
                        _append_debug_row(
                            call_type="generate_image",
                            model=model,
                            messages=messages,
                            response=url,
                        )
                        return url

        # ── Shape 2: content is a plain string (data URL or https URL) ─────────
        if isinstance(content, str) and content.strip():
            stripped = content.strip()
            if stripped.startswith("data:") or stripped.startswith("http"):
                logger.info("[LLMClient] generate_image: found image in string content")
                _append_debug_row(
                    call_type="generate_image",
                    model=model,
                    messages=messages,
                    response=stripped,
                )
                return stripped

        # ── Shape 3: message-level "images" array (OpenRouter docs format) ─────
        # choices[0].message.images[0]['image_url']['url']
        images = message.get("images") or []
        if images:
            first = images[0]
            if isinstance(first, dict):
                url = (first.get("image_url") or {}).get("url") or first.get("url", "")
                if url:
                    logger.info("[LLMClient] generate_image: found image in message.images")
                    _append_debug_row(
                        call_type="generate_image",
                        model=model,
                        messages=messages,
                        response=url,
                    )
                    return url
            if isinstance(first, str) and first.strip():
                _append_debug_row(
                    call_type="generate_image",
                    model=model,
                    messages=messages,
                    response=first.strip(),
                )
                return first.strip()

        # ── Shape 4: top-level "data" array (DALL-E style) ────────────────────
        data_list = body.get("data") or []
        if data_list:
            first = data_list[0]
            if isinstance(first, dict):
                url = first.get("url") or first.get("b64_json")
                if url:
                    logger.info("[LLMClient] generate_image: found image in top-level data[]")
                    if not url.startswith("http") and not url.startswith("data:"):
                        url = f"data:image/png;base64,{url}"
                    _append_debug_row(
                        call_type="generate_image",
                        model=model,
                        messages=messages,
                        response=url,
                    )
                    return url

        logger.warning(
            "[LLMClient] generate_image: could not find image in response. "
            "Full body (truncated): %s",
            _body_preview,
        )
        _append_debug_row(
            call_type="generate_image",
            model=model,
            messages=messages,
            response="",
            error="could not find image in response",
        )
        return None

