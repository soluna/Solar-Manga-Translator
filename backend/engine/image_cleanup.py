from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
from PIL import Image


class GeminiImageCleanupClient:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def remove_text(self, source_rgb: np.ndarray, guide_rgb: np.ndarray, prompt: str) -> np.ndarray:
        return await asyncio.to_thread(self._remove_text_sync, source_rgb, guide_rgb, prompt)

    def _remove_text_sync(self, source_rgb: np.ndarray, guide_rgb: np.ndarray, prompt: str) -> np.ndarray:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("缺少 google-genai 依赖，请先重新安装后端依赖。") from exc

        client = genai.Client(api_key=self.api_key)
        response = client.models.generate_content(
            model=self.model,
            contents=[
                prompt,
                Image.fromarray(source_rgb),
                Image.fromarray(guide_rgb),
            ],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
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
        inline_data = getattr(part, "inline_data", None)
        if inline_data is None:
            return None

        if hasattr(part, "as_image"):
            return np.array(part.as_image().convert("RGB"))

        data = getattr(inline_data, "data", None)
        if data is None:
            return None

        from io import BytesIO

        return np.array(Image.open(BytesIO(data)).convert("RGB"))
