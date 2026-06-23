from __future__ import annotations

import asyncio
import base64
import json
import math
from io import BytesIO
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

import numpy as np
from PIL import Image


SEEDREAM_IMAGE_API_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
DEFAULT_IMAGE_CLEANUP_PROMPT = "去除覆盖在图片上的文字"
ADVANCED_IMAGE_ERASE_PROMPT = """
You are editing a manga/comic page.
Remove every visible text mark from the image, including speech-bubble text,
captions, sound effects, decorative lettering, handwriting, stylized text, and
text embedded in backgrounds. Reconstruct the background naturally where text
was removed. Preserve all non-text artwork, line art, panels, characters,
objects, tones, colors, and composition unchanged. If a sound effect or
decorative lettering sits inside a frame, speech balloon, caption box, border,
outline, tail, or any hand-drawn container, remove only the lettering and keep
that surrounding container exactly. Inside white speech balloons or caption
areas, leave a clean continuous white fill with no faint ghost text, smudges, or
partial strokes. Inside black, dark, colored, transparent, or patterned text
containers, leave a clean continuous matching fill with no faint ghost text,
smudges, or partial strokes. Do not translate, add text, redraw non-text
content, crop, rotate, or change the page layout. Return only the cleaned image.
""".strip()
ADVANCED_IMAGE_SELECTION_ERASE_PROMPT = """
Edit this manga page. The white blank area is outside the user's selection and
should stay blank. In the visible selected areas, remove all text, letters,
handwriting, and sound-effect characters. Fill the removed text with the
surrounding background. Keep non-text artwork, character lines, speech bubbles,
caption boxes, sound-effect borders, panels, tones, and layout unchanged. Do
not add or translate text. Do not crop, rotate, or resize. Return only the
cleaned image.
""".strip()
ADVANCED_IMAGE_CONTAINER_MASK_PROMPT = """
Create a segmentation mask image for this manga/comic page. Do not edit the
manga artwork. Keep exactly the same canvas, orientation, and aspect ratio as
the input. Use pure black (#000000) for every pixel outside text containers.
Use pure chroma green (#00FF00) to fill the complete interior of every speech
balloon, thought bubble, caption box, narration box, rectangular dialogue box,
and decorative sound-effect text container that contains text. This includes
white, black, dark, colored, transparent, textured, and patterned containers.
For irregular hand-drawn sound-effect containers, fill the whole irregular
container interior, including jagged edges, tails, spikes, and border-adjacent
interior. If uncertain, include a small margin just inside and immediately
around the container border. Do not include characters, faces, hair, hands,
clothing, bodies, panel borders, background texture, blank page margins, or
non-text artwork. Do not draw outlines, labels, numbers, gradients, gray
shading, anti-aliased artwork, or the original manga image. Return only a
binary-looking mask: black background with solid chroma-green filled blobs for
text containers.
""".strip()


class GeminiImageCleanupClient:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def remove_text(
        self,
        source_rgb: np.ndarray,
        guide_rgb: np.ndarray | None = None,
        prompt: str = DEFAULT_IMAGE_CLEANUP_PROMPT,
    ) -> np.ndarray:
        return await asyncio.to_thread(self._remove_text_sync, source_rgb, guide_rgb, prompt)

    def _remove_text_sync(self, source_rgb: np.ndarray, guide_rgb: np.ndarray | None, prompt: str) -> np.ndarray:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("缺少 google-genai 依赖，请先重新安装后端依赖。") from exc

        client = genai.Client(api_key=self.api_key)
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
        )
        contents: list[Any] = [prompt, Image.fromarray(source_rgb)]
        if guide_rgb is not None:
            contents.append(Image.fromarray(guide_rgb))

        response = client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

        image = self._extract_image(response)
        if image is None:
            raise RuntimeError("Gemini 图像编辑没有返回可用图片。")
        return image

    def _extract_image(self, response: Any) -> np.ndarray | None:
        direct_parts = getattr(response, "parts", None) or []
        for part in direct_parts:
            image = self._part_to_image(part)
            if image is not None:
                return image

        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                image = self._part_to_image(part)
                if image is not None:
                    return image

        return None

    def _part_to_image(self, part: Any) -> np.ndarray | None:
        if hasattr(part, "as_image"):
            try:
                pil_image = part.as_image()
                if hasattr(pil_image, "convert"):
                    return np.array(pil_image.convert("RGB"))
            except Exception:
                pass

        inline_data = getattr(part, "inline_data", None)
        if inline_data is None:
            return None

        data = getattr(inline_data, "data", None)
        if data is None:
            return None

        return np.array(Image.open(BytesIO(data)).convert("RGB"))


class SeedreamImageCleanupClient:
    API_URL = SEEDREAM_IMAGE_API_URL
    MIN_PIXELS = 2560 * 1440
    MAX_PIXELS = int(3072 * 3072 * 1.1025)

    def __init__(
        self,
        api_key: str,
        model: str,
        api_url: str | None = None,
        timeout_seconds: int = 120,
    ):
        self.api_key = api_key
        self.model = model
        self.api_url = self._normalize_api_url(api_url)
        self.timeout_seconds = max(30, min(300, int(timeout_seconds or 120)))

    async def remove_text(
        self,
        source_rgb: np.ndarray,
        guide_rgb: np.ndarray | None = None,
        prompt: str = DEFAULT_IMAGE_CLEANUP_PROMPT,
    ) -> np.ndarray:
        return await asyncio.to_thread(self._remove_text_sync, source_rgb, guide_rgb, prompt)

    def _remove_text_sync(self, source_rgb: np.ndarray, guide_rgb: np.ndarray | None, prompt: str) -> np.ndarray:
        source_rgb, guide_rgb, size_value = self._prepare_request_images(source_rgb, guide_rgb)
        images = [self._image_to_data_uri(source_rgb)]
        if guide_rgb is not None:
            images.append(self._image_to_data_uri(guide_rgb))

        payload = {
            "model": self.model,
            "prompt": prompt,
            "image": images,
            "size": size_value,
            "response_format": "b64_json",
            "output_format": "png",
            "watermark": False,
        }
        request = urllib_request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib_request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Seedream 图像编辑请求失败: HTTP {exc.code} {detail}") from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"Seedream 图像编辑请求失败: {exc.reason}") from exc

        try:
            response_payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Seedream 图像编辑返回了无法解析的 JSON。") from exc

        image = self._extract_image(response_payload)
        if image is None:
            raise RuntimeError("Seedream 图像编辑没有返回可用图片。")
        return image

    def _extract_image(self, payload: dict[str, Any]) -> np.ndarray | None:
        for item in payload.get("data") or []:
            encoded = item.get("b64_json")
            if encoded:
                return self._decode_base64_image(encoded)

            url = item.get("url")
            if url:
                return self._load_image_from_url(url)

        return None

    def _image_to_data_uri(self, image_rgb: np.ndarray) -> str:
        image = Image.fromarray(image_rgb)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def _prepare_request_images(
        self,
        source_rgb: np.ndarray,
        guide_rgb: np.ndarray | None,
    ) -> tuple[np.ndarray, np.ndarray | None, str]:
        height, width = source_rgb.shape[:2]
        current_pixels = height * width
        target_pixels = min(max(current_pixels, self.MIN_PIXELS), self.MAX_PIXELS)

        if current_pixels == 0:
            raise RuntimeError("Seedream 图像编辑输入为空。")

        scale = max(1.0, math.sqrt(target_pixels / float(current_pixels)))
        target_width = max(width, int(math.ceil(width * scale)))
        target_height = max(height, int(math.ceil(height * scale)))
        while target_width * target_height < self.MIN_PIXELS:
            if target_width <= target_height:
                target_width += 1
            else:
                target_height += 1

        if target_width != width or target_height != height:
            target_size = (target_width, target_height)
            source_rgb = np.array(
                Image.fromarray(source_rgb).resize(target_size, resample=Image.Resampling.LANCZOS)
            )
            if guide_rgb is not None:
                guide_rgb = np.array(
                    Image.fromarray(guide_rgb).resize(target_size, resample=Image.Resampling.BILINEAR)
                )

        return source_rgb, guide_rgb, f"{target_width}x{target_height}"

    def _decode_base64_image(self, encoded: str) -> np.ndarray:
        return np.array(Image.open(BytesIO(base64.b64decode(encoded))).convert("RGB"))

    def _load_image_from_url(self, url: str) -> np.ndarray:
        if url.startswith("data:image/"):
            _, encoded = url.split(",", 1)
            return self._decode_base64_image(encoded)

        with urllib_request.urlopen(url, timeout=self.timeout_seconds) as response:
            return np.array(Image.open(BytesIO(response.read())).convert("RGB"))

    def _normalize_api_url(self, raw_url: str | None) -> str:
        normalized = str(raw_url or self.API_URL).strip().rstrip("/")
        if not normalized:
            return self.API_URL
        if normalized.endswith("/images/generations"):
            return normalized
        return f"{normalized}/images/generations"


def create_image_cleanup_client(
    mode: str,
    api_key: str,
    model: str,
    api_url: str | None = None,
    timeout_seconds: int = 120,
):
    if mode == "seedream-image":
        return SeedreamImageCleanupClient(
            api_key=api_key,
            model=model,
            api_url=api_url,
            timeout_seconds=timeout_seconds,
        )
    return GeminiImageCleanupClient(api_key=api_key, model=model)
