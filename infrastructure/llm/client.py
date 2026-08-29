"""
LLMClient — OpenRouter streaming client.

Supports:
- Text-only and vision models (base64 image in messages)
- Web search via OpenRouter's openrouter:web_search server tool — works for any
  model, OpenRouter runs the search server-side (the :online suffix and the
  plugins:[{id:"web"}] form are both deprecated as of 2026)
- SSE streaming: yields text chunks as they arrive
- Image generation via modalities: ["image", "text"] (non-streaming call)

Every call is recorded in full by ``call_log`` — see that module: it is a
corpus kept forever, not a log that rotates away.
"""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import aiohttp

from infrastructure.llm import call_log
from infrastructure.logging.logger import setup_logger

logger = setup_logger("LLMClient")

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


# Statuses worth trying again. The rest are the server telling us something
# about the request itself: a bad key, a model that does not exist, a payload it
# will not accept. Retrying those spends 4.5 seconds of someone's turn to arrive
# at the same answer — which is what `complete()` used to do to every 401.
RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

MAX_ATTEMPTS = 3


class OpenRouterError(RuntimeError):
    """A non-200 from OpenRouter, raised rather than yielded.

    ``stream()`` used to yield the text ``[OpenRouter error 429]`` as if the
    model had said it: the caller could not tell a failure from a reply, and
    the string ended up saved as Viktor's own words often enough that
    ``scripts/inspect_last_messages.py`` exists to find those rows. A failed
    request is an exception, and the SSE layer turns it into an error event.
    """

    def __init__(self, status: int, body: str = "", *, retry_after: float | None = None) -> None:
        self.status = status
        self.body = body
        self.retry_after = retry_after
        super().__init__(f"OpenRouter {status}: {body[:200]}" if body else f"OpenRouter {status}")

    @property
    def retryable(self) -> bool:
        return self.status in RETRYABLE_STATUSES


def _retry_delay(attempt: int, retry_after: float | None = None) -> float:
    """How long to wait before attempt *attempt* + 1.

    Honours the server's own ``Retry-After`` when it sent one — it knows when
    its rate limit resets and we do not.
    """
    if retry_after is not None:
        return max(0.0, min(retry_after, 30.0))
    return 1.5 * attempt


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # the HTTP-date form; the backoff below is good enough


def _sanitize_content(content) -> str | list:
    """Replace base64 image data with a placeholder.

    The one thing not kept verbatim: an inline image is megabytes of base64 that
    compresses badly and reads as noise. 230 rows in the existing corpus carry
    this placeholder. Text is never touched.
    """
    if isinstance(content, str):
        return content
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
    usage: Optional[dict] = None,
) -> None:
    call_log.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "call_type": call_type,
        "model": model,
        "system": system or None,
        "messages": _sanitize_messages(messages),
        "response": response,
        "web_search": web_search,
        "citations": citations or None,
        "error": error,
        # What the call actually cost, straight from the provider. Recorded
        # because nothing else knows it: the corpus had model and kind but no
        # money, so "what does a night of thinking cost" was unanswerable.
        "usage": _billing(usage),
    })


def _billing(usage: Optional[dict]) -> Optional[dict]:
    """The few fields worth keeping out of a large usage object."""
    if not usage:
        return None
    kept = {
        key: usage[key]
        for key in ("cost", "prompt_tokens", "completion_tokens", "total_tokens")
        if usage.get(key) is not None
    }
    return kept or None


async def fetch_account_state(api_key: str) -> dict:
    """What OpenRouter says about the key itself: credit and recent spend.

    Not a completion, so it deliberately does not go through ``_open`` and its
    retry policy — this is read while he is mid-thought, and a slow answer is
    worse than no answer. It lives here anyway because this module is the one
    place that knows how to talk to OpenRouter, and a sixth hand-rolled path is
    exactly what ``tests/test_llm_contract.py`` refuses to allow.

    Raises on any failure. The caller decides what to say about that.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(f"{OPENROUTER_BASE}/credits") as resp:
            credits = (await resp.json())["data"]
        async with session.get(f"{OPENROUTER_BASE}/key") as resp:
            usage = (await resp.json())["data"]

    return {
        "remaining": float(credits.get("total_credits", 0))
        - float(credits.get("total_usage", 0)),
        "daily": float(usage.get("usage_daily", 0)),
        "weekly": float(usage.get("usage_weekly", 0)),
        "monthly": float(usage.get("usage_monthly", 0)),
    }


MIN_COMPLETE_TIMEOUT_S = 60
MAX_COMPLETE_TIMEOUT_S = 420

# Longest a stream may go without a byte before we treat it as hung. Generous:
# a reasoning model thinks before the first token, and OpenRouter sends nothing
# while it does.
STREAM_SOCK_READ_S = 180


def _timeout_for(max_tokens: int) -> int:
    """Scale the request timeout with the output budget.

    Small extraction calls keep the old 60s; a 16k-token reflection step on a
    reasoning model gets the minutes it actually needs.
    """
    return max(MIN_COMPLETE_TIMEOUT_S, min(MAX_COMPLETE_TIMEOUT_S, max_tokens // 25))


# Models that output only images (no text) — they need modalities: ["image"].
# One list, because there were two: this module's and main.py's, and they had
# drifted apart. main.py's was missing "bytedance-seed/" and "x-ai/", so body
# generation asked those models for text alongside the image and got neither.
IMAGE_ONLY_PREFIXES = (
    "black-forest-labs/",
    "bytedance-seed/",
    "bytedance/",
    "sourceful/",
    "x-ai/",
)


def modalities_for(model: str) -> list[str]:
    """What to ask OpenRouter for, given the model."""
    return ["image"] if model.startswith(IMAGE_ONLY_PREFIXES) else ["image", "text"]


def parse_image_response(body: dict) -> str | None:
    """Pull a data URL or https URL out of an OpenRouter image response.

    Four shapes, because different providers answer differently and OpenRouter
    passes the shape through. This used to exist twice — here and in main.py —
    with roughly 120 duplicated lines; the copies were close enough that a fix
    to one was easy not to notice missing from the other.

    Returns None when no image can be found; the caller decides how loud that is.
    """
    choices = body.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")

        # 1. content is a list of typed parts
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                kind = part.get("type", "")
                if kind == "image_url":
                    url = (part.get("image_url") or {}).get("url", "")
                    if url:
                        return url
                if kind == "image":
                    data = part.get("data") or (part.get("source") or {}).get("data", "")
                    if data:
                        return f"data:image/png;base64,{data}"

        # 2. content is a plain string holding a data: or https: URL
        if isinstance(content, str) and content.strip():
            stripped = content.strip()
            if stripped.startswith(("data:", "http")):
                return stripped

        # 3. message-level "images" array (OpenRouter's documented shape)
        images = message.get("images") or []
        if images:
            first = images[0]
            if isinstance(first, dict):
                url = (first.get("image_url") or {}).get("url") or first.get("url", "")
                if url:
                    return url
            if isinstance(first, str) and first.strip():
                return first.strip()

    # 4. top-level "data" array (DALL-E style)
    data_list = body.get("data") or []
    if data_list:
        first = data_list[0]
        if isinstance(first, dict):
            url = first.get("url") or first.get("b64_json")
            if url:
                if not url.startswith(("http", "data:")):
                    url = f"data:image/png;base64,{url}"
                return url

    return None


# Which models can be shown a photograph. A model that is not here gets the
# text and never learns there was a picture — silently, which is why the same
# set has to hold on every client too (frontend and mobile keep their own
# copies; they had already drifted apart before this list was cut to four).
VISION_MODELS = {
    "~anthropic/claude-fable-latest",
    "~moonshotai/kimi-latest",
    "~google/gemini-pro-latest",
    "openai/gpt-chat-latest",
    # ~z-ai/glm-latest is text-only — confirmed against the OpenRouter
    # catalogue, its input modalities are ["text"] alone.
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

    # ── The one way this class talks to OpenRouter ───────────────────────────
    #
    # There were four: a streaming path, two completion paths and an image path,
    # each with its own session, its own timeout, its own idea of what counts as
    # a failure — and retries in three of them but not in the fourth, which was
    # the one every chat message goes through. A 429 at the wrong moment cost
    # the whole reply while a reflection step in the same second got three tries.

    @asynccontextmanager
    async def _open(self, payload: dict, *, timeout, connector_factory=None):
        """One POST to /chat/completions. Raises OpenRouterError on non-200."""
        connector = connector_factory() if connector_factory else None
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.post(
                f"{OPENROUTER_BASE}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    raise OpenRouterError(
                        response.status,
                        body,
                        retry_after=_parse_retry_after(response.headers.get("Retry-After")),
                    )
                yield response

    async def _post_json(
        self,
        payload: dict,
        *,
        timeout,
        connector_factory=None,
        attempts: int = MAX_ATTEMPTS,
        what: str = "request",
    ) -> dict:
        """A non-streaming call, retried while retrying can still help.

        Raises the last :class:`OpenRouterError` — or the last transport error —
        once the attempts are spent.
        """
        last: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                async with self._open(
                    payload, timeout=timeout, connector_factory=connector_factory
                ) as response:
                    # Read the bytes before parsing: image replies are megabytes
                    # of base64 and chunked-encoding truncation shows up here.
                    return json.loads(await response.read())
            except OpenRouterError as exc:
                last = exc
                if not exc.retryable:
                    logger.error("[LLMClient.%s] %d, not retryable: %s", what, exc.status, exc.body[:200])
                    raise
                logger.warning(
                    "[LLMClient.%s] %d on attempt %d/%d: %s",
                    what, exc.status, attempt, attempts, exc.body[:200],
                )
            except Exception as exc:
                last = exc
                logger.warning(
                    "[LLMClient.%s] error on attempt %d/%d: %s", what, attempt, attempts, exc
                )
            if attempt < attempts:
                await asyncio.sleep(
                    _retry_delay(attempt, getattr(last, "retry_after", None))
                )
        assert last is not None
        raise last

    async def stream(
        self,
        messages: list[dict],
        image_items: Optional[list[tuple[bytes, str]]] = None,
        geo: Optional[dict] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream text chunks from OpenRouter.

        Plain generation only. Web research goes through
        :class:`~infrastructure.agents.research.ResearchAgent`, which calls
        :meth:`complete_with_tools` — no search path runs through the reply
        stream any more.
        """
        model = self.model
        built_messages = self._build_messages(messages, image_items, geo, system_prompt)
        logger.info(
            "[LLMClient] stream start model=%s messages=%d has_system=%s images=%d",
            model,
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
            # Without this the response carries no cost, and the corpus cannot
            # answer what any of this costs.
            "usage": {"include": True},
        }

        # A long answer legitimately takes minutes, so there is no total budget;
        # what must not happen is a socket that has gone quiet holding the
        # request open. aiohttp's default here is total=5min and no read
        # deadline at all — the wrong way round for a stream.
        timeout = aiohttp.ClientTimeout(
            total=None, connect=30, sock_connect=30, sock_read=STREAM_SOCK_READ_S
        )
        # Separate system from the rest for the record: built_messages[0] is it.
        log_messages = (
            built_messages[1:]
            if built_messages and built_messages[0].get("role") == "system"
            else built_messages
        )

        chunks: list[str] = []
        billing: Optional[dict] = None
        started = False   # once a chunk has reached the caller there is no going back

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with self._open(payload, timeout=timeout) as response:
                    logger.info(
                        "[LLMClient] response status=%d model=%s attempt=%d",
                        response.status, model, attempt,
                    )
                    async for raw_line in response.content:
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
                            # OpenRouter emits non-token SSE payloads too — and
                            # the last of them carries the cost of the call.
                            # It is the only place a streamed call reports it.
                            if obj.get("usage"):
                                billing = obj["usage"]
                            continue
                        chunk = (choices[0].get("delta") or {}).get("content")
                        if chunk:
                            started = True
                            chunks.append(chunk)
                            yield chunk
                break

            except Exception as exc:
                # Retrying is only honest before the first token. After that the
                # caller has already shown the user text, and a second attempt
                # would append a second answer to the first half of one.
                retryable = (
                    not started
                    and attempt < MAX_ATTEMPTS
                    and (not isinstance(exc, OpenRouterError) or exc.retryable)
                )
                if not retryable:
                    logger.error(
                        "[LLMClient] stream failed%s: %s",
                        " mid-answer" if started else f" on attempt {attempt}/{MAX_ATTEMPTS}",
                        exc,
                    )
                    _append_debug_row(
                        call_type="stream",
                        model=model,
                        system=system_prompt,
                        messages=log_messages,
                        response="".join(chunks),
                        error=str(exc),
                    )
                    raise
                logger.warning(
                    "[LLMClient] stream attempt %d/%d failed before any output: %s",
                    attempt, MAX_ATTEMPTS, exc,
                )
                await asyncio.sleep(_retry_delay(attempt, getattr(exc, "retry_after", None)))

        _append_debug_row(
            usage=billing,
            call_type="stream",
            model=model,
            system=system_prompt,
            messages=log_messages,
            response="".join(chunks),
        )
    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 650,
        temperature: float | None = None,
        return_meta: bool = False,
    ) -> str | tuple[str, str | None]:
        """Non-streaming single completion. Returns assistant text or '' on failure.

        Never raises: every caller treats an empty string as "he had nothing to
        say this step", and a reflection that aborts on a transient 502 is worse
        than one that skips a step.

        With ``return_meta=True`` returns ``(text, finish_reason)`` so callers can
        detect truncation (``finish_reason == "length"`` means the model hit
        ``max_tokens`` and the text is cut off mid-thought — do not trust it as-is).

        The request timeout scales with ``max_tokens``: a reasoning model thinks
        before it writes, and a generous budget means a genuinely long request.
        A fixed 60s would just trade truncation for timeouts.
        """
        system = messages[0].get("content", "") if messages and messages[0].get("role") == "system" else None
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "top_p": self.top_p,
            "max_tokens": max_tokens,
            "stream": False,
            "usage": {"include": True},
        }

        def _failed(detail: str):
            _append_debug_row(
                call_type="complete", model=self.model, system=system,
                messages=messages, response="", error=detail,
            )
            return ("", None) if return_meta else ""

        try:
            body = await self._post_json(
                payload,
                timeout=aiohttp.ClientTimeout(total=_timeout_for(max_tokens)),
                what="complete",
            )
        except Exception as exc:
            return _failed(str(exc))

        choices = body.get("choices") or []
        if not choices:
            return _failed("no choices in response")

        choice = choices[0]
        if choice.get("error"):
            # A provider error can arrive inside a 200 with message=null.
            logger.warning("[LLMClient.complete] provider error in choices: %s", choice["error"])
            return _failed(str(choice["error"]))

        response = ((choice.get("message") or {}).get("content") or "").strip()
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            logger.warning(
                "[LLMClient.complete] response TRUNCATED (finish_reason=length, "
                "max_tokens=%d) — tail=%r", max_tokens, response[-60:],
            )
        _append_debug_row(
            call_type="complete", model=self.model, system=system,
            messages=messages, response=response,
            error=("finish_reason=length" if finish_reason == "length" else None),
            usage=body.get("usage"),
        )
        return (response, finish_reason) if return_meta else response

    async def complete_with_tools(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        max_tokens: int = 1200,
        temperature: float | None = None,
        timeout_s: int = 120,
    ) -> tuple[str, list[dict]]:
        """Non-streaming completion with OpenRouter server tools attached.

        Server tools (``openrouter:web_search``, ``openrouter:web_fetch``) run
        on OpenRouter's side: the model decides when and how often to call them
        and the whole search loop happens inside this single request. The reply
        text arrives with ``url_citation`` annotations for the sources it used.

        Returns ``(text, citations)`` where each citation is
        ``{"url": ..., "title": ...}``. Returns ``("", [])`` on failure.

        The timeout is generous by default — several searches plus a page fetch
        can take well over the 60s used by :meth:`complete`.
        """
        system = messages[0].get("content", "") if messages and messages[0].get("role") == "system" else None
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "top_p": self.top_p,
            "max_tokens": max_tokens,
            "tools": tools,
            "stream": False,
            "usage": {"include": True},
        }

        def _failed(detail: str) -> tuple[str, list[dict]]:
            _append_debug_row(
                call_type="research", model=self.model, system=system,
                messages=messages, response="", web_search=True, error=detail,
            )
            return "", []

        try:
            body = await self._post_json(
                payload,
                timeout=aiohttp.ClientTimeout(total=timeout_s),
                what="complete_with_tools",
            )
        except Exception as exc:
            return _failed(str(exc))

        choices = body.get("choices") or []
        if not choices:
            return _failed("no choices in response")
        if choices[0].get("error"):
            logger.warning(
                "[LLMClient.complete_with_tools] provider error: %s", choices[0]["error"]
            )
            return _failed(str(choices[0]["error"]))

        message = choices[0].get("message") or {}
        text_out = (message.get("content") or "").strip()

        citations: list[dict] = []
        seen: set[str] = set()
        for annotation in message.get("annotations") or []:
            if annotation.get("type") != "url_citation":
                continue
            citation = annotation.get("url_citation") or {}
            url = citation.get("url")
            if url and url not in seen:
                seen.add(url)
                citations.append({"url": url, "title": citation.get("title") or url})

        usage = (body.get("usage") or {}).get("server_tool_use_details") or {}
        logger.info(
            "[LLMClient.complete_with_tools] model=%s searches=%s citations=%d len=%d",
            self.model, usage.get("web_search_requests", 0), len(citations), len(text_out),
        )
        _append_debug_row(
            call_type="research", model=self.model, system=system, messages=messages,
            response=text_out, web_search=True, citations=citations,
            usage=body.get("usage"),
        )
        return text_out, citations

    async def generate_image(
        self,
        prompt: str,
        model: str,
        *,
        reference_png_b64: str | None = None,
        sock_read_s: int | None = None,
    ) -> str | None:
        """
        Non-streaming image generation via OpenRouter.
        Returns a base64 data URL string (data:image/png;base64,...) or None on failure.

        FLUX / Sourceful / ByteDance are image-only models → modalities: ["image"].
        GPT / Gemini output both text and image → modalities: ["image", "text"].

        ``reference_png_b64`` turns this into image-to-image, which is what the
        body-expression pipeline needs. That pipeline used to post to OpenRouter
        itself — a fifth hand-rolled path, with its own headers, its own timeout
        and no retries at all.
        """
        content: list | str = prompt
        if reference_png_b64:
            content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{reference_png_b64}"},
                },
            ]
        messages = [{"role": "user", "content": content}]
        payload = {
            "model": model,
            "messages": messages,
            "modalities": modalities_for(model),
            "stream": False,
            "usage": {"include": True},
        }
        logger.info("[LLMClient] generate_image model=%s prompt=%s", model, prompt[:120])

        # Image replies are megabytes of base64, and a slow generation can sit
        # quiet for minutes: a read deadline rather than a total budget, and a
        # fresh connection each time, which is what stopped the
        # TransferEncodingErrors on this path.
        timeout = (
            aiohttp.ClientTimeout(
                total=None, connect=30, sock_connect=30, sock_read=sock_read_s
            )
            if sock_read_s
            else aiohttp.ClientTimeout(total=300)
        )

        try:
            body = await self._post_json(
                payload,
                timeout=timeout,
                connector_factory=lambda: aiohttp.TCPConnector(force_close=True),
                what="generate_image",
            )
        except Exception as exc:
            logger.error("[LLMClient] generate_image failed: %s", exc)
            _append_debug_row(
                call_type="generate_image", model=model, messages=messages,
                response="", error=str(exc),
            )
            return None

        url = parse_image_response(body)
        if url:
            logger.info("[LLMClient] generate_image: found image (%d chars)", len(url))
            _append_debug_row(
                call_type="generate_image", model=model, messages=messages, response=url,
            )
            return url

        # The shape is the diagnostic: log enough of it to recognise a new one.
        preview = json.dumps(body)[:1200]
        logger.warning(
            "[LLMClient] generate_image: could not find image in response. "
            "Full body (truncated): %s",
            preview,
        )
        _append_debug_row(
            call_type="generate_image",
            model=model,
            messages=messages,
            response="",
            error="could not find image in response",
        )
        return None

