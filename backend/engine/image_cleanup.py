from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

import numpy as np
from PIL import Image


DEFAULT_IMAGE_CLEANUP_PROMPT = "去除覆盖在图片上的文字"


class GeminiImageCleanupClient:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def remove_text(
        self,
        source_rgb: np.ndarray,
        guide_rgb: np.ndarray,
        prompt: str = DEFAULT_IMAGE_CLEANUP_PROMPT,
    ) -> np.ndarray:
        return await asyncio.to_thread(self._remove_text_sync, source_rgb, guide_rgb, prompt)

    def _remove_text_sync(self, source_rgb: np.ndarray, guide_rgb: np.ndarray, prompt: str) -> np.ndarray:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("缺少 google-genai 依赖，请先重新安装后端依赖。") from exc

        client = genai.Client(api_key=self.api_key)
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
        )
        response = client.models.generate_content(
            model=self.model,
            contents=[
                prompt,
                Image.fromarray(source_rgb),
                Image.fromarray(guide_rgb),
            ],
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
    API_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def remove_text(
        self,
        source_rgb: np.ndarray,
        guide_rgb: np.ndarray,
        prompt: str = DEFAULT_IMAGE_CLEANUP_PROMPT,
    ) -> np.ndarray:
        return await asyncio.to_thread(self._remove_text_sync, source_rgb, guide_rgb, prompt)

    def _remove_text_sync(self, source_rgb: np.ndarray, guide_rgb: np.ndarray, prompt: str) -> np.ndarray:
        height, width = source_rgb.shape[:2]
        payload = {
            "model": self.model,
            "prompt": prompt,
            "image": [
                self._image_to_data_uri(source_rgb),
                self._image_to_data_uri(guide_rgb),
            ],
            "size": f"{width}x{height}",
            "watermark": False,
        }
        request = urllib_request.Request(
            self.API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib_request.urlopen(request, timeout=120) as response:
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

    def _decode_base64_image(self, encoded: str) -> np.ndarray:
        return np.array(Image.open(BytesIO(base64.b64decode(encoded))).convert("RGB"))

    def _load_image_from_url(self, url: str) -> np.ndarray:
        if url.startswith("data:image/"):
            _, encoded = url.split(",", 1)
            return self._decode_base64_image(encoded)

        with urllib_request.urlopen(url, timeout=120) as response:
            return np.array(Image.open(BytesIO(response.read())).convert("RGB"))


def create_image_cleanup_client(mode: str, api_key: str, model: str):
    if mode == "seedream-image":
        return SeedreamImageCleanupClient(api_key=api_key, model=model)
    return GeminiImageCleanupClient(api_key=api_key, model=model)
