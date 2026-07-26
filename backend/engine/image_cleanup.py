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
Remove all visible text from this manga/comic page.

Remove dialogue and captions inside existing speech bubbles or boxes, borderless
text printed directly over artwork, sound effects, handwriting, and decorative
lettering. Text may be horizontal, vertical, diagonal, curved, small, large,
outlined, or stylized.

Where text is removed, reconstruct the hidden background naturally from the
surrounding artwork, colors, textures, tones, and line art. Do not replace an
illustrated or colored area with flat white.

Preserve all non-text artwork and every existing speech-bubble outline, caption
box, panel border, character, object, and page layout. Do not create any new
speech bubble, caption box, rectangle, border, text, symbol, or decoration.
Do not translate, crop, rotate, or resize. Return only the cleaned image.
""".strip()
ADVANCED_IMAGE_SELECTION_ERASE_PROMPT = """
Only edit the visible user-selected parts of this manga page; the white area
outside them is a selection mask and must stay white. Remove all text in the
visible parts, including borderless, diagonal, curved, handwritten, stylized,
and sound-effect text. Reconstruct the hidden background naturally. Preserve
all non-text artwork and every existing outline or border. Do not create a new
speech bubble, caption box, rectangle, border, text, symbol, or decoration.
Do not translate, crop, rotate, or resize. Return only the cleaned image.
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
            raise RuntimeError(self._format_http_error(exc.code, detail)) from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(
                "无法连接 Seedream 图像编辑服务。请检查网络和接口地址，稍后重试；"
                "如果持续失败，请确认火山方舟服务所在地域与接口地址一致。"
            ) from exc

        try:
            response_payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Seedream 图像编辑返回了无法解析的 JSON。") from exc

        image = self._extract_image(response_payload)
        if image is None:
            raise RuntimeError("Seedream 图像编辑没有返回可用图片。")
        return image

    def _format_http_error(self, status_code: int, detail: str) -> str:
        provider_code = ""
        provider_message = ""
        request_id = ""
        try:
            payload = json.loads(detail or "{}")
            error_payload = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error_payload, dict):
                provider_code = str(error_payload.get("code") or "").strip()
                provider_message = str(error_payload.get("message") or "").strip()
                request_id = str(
                    error_payload.get("request_id")
                    or error_payload.get("requestId")
                    or ""
                ).strip()
            if not request_id and isinstance(payload, dict):
                request_id = str(payload.get("request_id") or payload.get("requestId") or "").strip()
        except (TypeError, ValueError, json.JSONDecodeError):
            provider_message = ""

        if not request_id and provider_message:
            marker = "request id:"
            marker_index = provider_message.lower().find(marker)
            if marker_index >= 0:
                request_id = provider_message[marker_index + len(marker):].strip().split()[0]

        model_hint = f"当前模型名称为“{self.model}”。" if self.model else ""
        if status_code == 401:
            message = (
                "Seedream 认证失败（HTTP 401）。请检查高级擦除 API Key 是否正确、未过期，"
                "并确认接口地址、模型名称或接入点与这个 Key 属于同一火山方舟账号且已经开通权限。"
                f"{model_hint}"
            )
        elif status_code == 403:
            message = (
                "Seedream 拒绝了这次请求（HTTP 403）。API Key 已被识别，但当前账号可能没有该模型或接入点的调用权限。"
                f"请在火山方舟控制台检查授权和可用地域。{model_hint}"
            )
        elif status_code in {400, 404}:
            message = (
                f"Seedream 请求配置不正确（HTTP {status_code}）。请检查模型名称或推理接入点是否存在，"
                f"以及高级擦除接口地址是否填写正确。{model_hint}"
            )
        elif status_code == 429:
            message = (
                "Seedream 请求过于频繁或额度不足（HTTP 429）。请稍后重试，并在火山方舟控制台检查配额和余额。"
            )
        elif status_code >= 500:
            message = f"Seedream 服务暂时异常（HTTP {status_code}）。你的配置不一定有问题，请稍后重试。"
        else:
            message = (
                f"Seedream 图像编辑请求失败（HTTP {status_code}）。请检查接口地址、模型名称和账号权限后重试。"
                f"{model_hint}"
            )

        if provider_code and provider_code not in {"AuthenticationError", "Unauthorized"}:
            message += f" 服务错误码：{provider_code}。"
        if request_id:
            message += f" 请求 ID：{request_id}。"
        return message

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
