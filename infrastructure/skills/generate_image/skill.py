from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path

from infrastructure.skills.base import SkillBase, SkillContext, SkillResult

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_GENERATED_IMAGES_DIR = _PROJECT_ROOT / "generated_images"
_GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

_MODEL_MAP = {
    "gpt5": "openai/gpt-5-image",
    "gemini": "google/gemini-3-pro-image-preview",
    "flux": "black-forest-labs/flux.2-pro",
    "grok": "x-ai/grok-imagine-image-quality",
}

# Any image-generation failure (moderation, provider error, empty result)
# falls back to Grok, which is the most permissive on romance/intimacy.
_FALLBACK_MODEL = "x-ai/grok-imagine-image-quality"


class GenerateImageSkill(SkillBase):
    id = "generate_image"
    cmd_name = "GENERATE_IMAGE"
    display = {"en": "Image Generation", "ru": "Генерация изображений"}
    description = {
        "en": "AI creates images using GPT-5 Image, Gemini 3 Pro, FLUX, or Grok.",
        "ru": "AI создаёт изображения через GPT-5 Image, Gemini 3 Pro, FLUX или Grok.",
    }
    example = "[GENERATE_IMAGE: gpt5 | night sky over Yerevan rooftops]"
    action_type = "inline"
    allow_mid_reply = True
    stream_command_text = False
    persist_in_db = False
    parse_re = re.compile(r"\[GENERATE[_ ]IMAGE:\s*(.*?)\]", re.DOTALL | re.IGNORECASE)
    _prompt_dir = Path(__file__).resolve().parent

    def pre_sse_events(self, match: re.Match) -> list[tuple[str, dict]]:
        return [("image_start", {"prompt": match.group(1).strip()})]

    async def _try_generate(self, ctx: SkillContext, prompt: str, model_id: str) -> str | None:
        """Call the image model, swallowing exceptions so the caller can fall back."""
        try:
            return await ctx.client.generate_image(prompt=prompt, model=model_id)
        except Exception as exc:
            ctx.dbg(f"GENERATE_IMAGE EXCEPTION ({model_id}): {type(exc).__name__}: {exc}")
            ctx.logger.error("[generate_image] exception on %s: %s", model_id, exc)
            return None

    async def execute(self, match: re.Match, ctx: SkillContext) -> SkillResult:
        raw = match.group(1).strip()
        parts = [p.strip() for p in raw.split("|", 1)]
        if len(parts) == 2:
            model_alias = parts[0].lower()
            prompt = parts[1]
        else:
            model_alias = "gpt5"
            prompt = parts[0]

        model_id = _MODEL_MAP.get(model_alias, "openai/gpt-5-image")
        ctx.logger.info("[generate_image] model=%s prompt=%s", model_id, prompt[:120])
        ctx.dbg(f"GENERATE_IMAGE model={model_id} prompt={prompt[:80]}")

        data_url = await self._try_generate(ctx, prompt, model_id)
        used_model = model_id

        # Fallback: on ANY failure (moderation, provider error, empty result),
        # retry with Grok — unless Grok was already the primary model.
        if not data_url and model_id != _FALLBACK_MODEL:
            ctx.logger.warning(
                "[generate_image] model=%s failed, falling back to %s", model_id, _FALLBACK_MODEL
            )
            ctx.dbg(f"GENERATE_IMAGE fallback {model_id} -> {_FALLBACK_MODEL}")
            data_url = await self._try_generate(ctx, prompt, _FALLBACK_MODEL)
            if data_url:
                used_model = _FALLBACK_MODEL

        ctx.dbg(f"GENERATE_IMAGE result={'OK len=' + str(len(data_url)) if data_url else 'None'}")
        if not data_url:
            ctx.logger.warning("[generate_image] returned no data (incl. fallback)")
            return SkillResult(sse_events=[("image_cancel", {})])

        model_id = used_model

        try:
            if data_url.startswith("data:"):
                _, b64_data = data_url.split(",", 1)
            else:
                b64_data = data_url
            img_bytes = base64.b64decode(b64_data)
            filename = f"{uuid.uuid4().hex}.png"
            filepath = _GENERATED_IMAGES_DIR / filename

            # Re-encode through Pillow to strip non-standard metadata chunks
            try:
                import io
                from PIL import Image as _PILImage
                img_obj = _PILImage.open(io.BytesIO(img_bytes))
                buf = io.BytesIO()
                img_obj.save(buf, format="PNG", optimize=False)
                filepath.write_bytes(buf.getvalue())
            except Exception:
                filepath.write_bytes(img_bytes)

            relative_path = f"/api/generated_images/{filename}"
            ctx.logger.info("[generate_image] saved to %s", filepath)
            ctx.dbg(f"GENERATE_IMAGE saved {relative_path} ({len(img_bytes)} bytes)")

            img_result = {"path": relative_path, "model": model_id, "prompt": prompt}
            marker = f"[GENERATED_IMAGE: {relative_path} | {model_id} | {prompt}]"
            return SkillResult(
                sse_events=[("image_ready", img_result)],
                db_markers=[marker],
            )
        except Exception as exc:
            ctx.dbg(f"GENERATE_IMAGE save failed: {exc}")
            ctx.logger.error("[generate_image] save failed: %s", exc)
            return SkillResult(sse_events=[("image_cancel", {})])


skill = GenerateImageSkill()
