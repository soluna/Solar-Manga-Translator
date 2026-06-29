from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import importlib
import json
import logging
import shutil
import sys
import os
import re
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from difflib import SequenceMatcher
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

import cv2
import numpy as np

from patch_pydensecrf import patch_mask_refinement
from runtime_paths import AppPaths, resolve_app_paths
from .image_cleanup import (
    ADVANCED_IMAGE_ERASE_PROMPT,
    ADVANCED_IMAGE_CONTAINER_MASK_PROMPT,
    ADVANCED_IMAGE_SELECTION_ERASE_PROMPT,
    DEFAULT_IMAGE_CLEANUP_PROMPT,
    SEEDREAM_IMAGE_API_URL,
    create_image_cleanup_client,
)


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]
logger = logging.getLogger("manga_translator.engine")


class TranslatorEngine:
    IMAGE_CLEANUP_TIMEOUT_SECONDS = 120
    IMAGE_CLEANUP_MAX_EDGE = 1280
    ADVANCED_ERASE_MAX_CHANGED_RATIO = 0.42
    ADVANCED_ERASE_MAX_REGION_REPLACE_RATIO = 0.62
    ADVANCED_ERASE_DEFAULT_PROVIDER = "volcengine-ark"
    ADVANCED_ERASE_DEFAULT_MODEL = "doubao-seedream-5-0-lite-260128"
    ADVANCED_ERASE_MIN_TIMEOUT_SECONDS = 30
    ADVANCED_ERASE_MAX_TIMEOUT_SECONDS = 300
    ADVANCED_ERASE_PROMPT_MAX_LENGTH = 4000
    LOCAL_MODEL_ERASE_INPAINTING_SIZE = 2048
    DOUBAO_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
    DOUBAO_DEFAULT_MODEL = "doubao-seed-translation-250915"
    STYLE_BUCKETS = ("gothic", "mincho", "rounded", "cartoon", "handwritten", "sfx")
    DEFAULT_FONT_KEY = "builtin:NotoSansCJKtc-Regular.otf"
    DEFAULT_STYLE_FONT_KEYS = {
        "gothic": DEFAULT_FONT_KEY,
        "mincho": DEFAULT_FONT_KEY,
        "rounded": DEFAULT_FONT_KEY,
        "cartoon": DEFAULT_FONT_KEY,
        "handwritten": DEFAULT_FONT_KEY,
        "sfx": DEFAULT_FONT_KEY,
    }
    TRANSLATED_OUTPUT_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
    REVIEW_MODES = ("classic", "canvas_beta")
    DEFAULT_REVIEW_MODE = "classic"
    PAGE_DOCUMENT_VERSION = 1
    IMAGE_PREVIEW_MIN_SIDE = 96
    IMAGE_PREVIEW_MAX_SIDE = 4096
    IMAGE_PREVIEW_FORMATS = (".webp", ".jpg", ".png")
    STYLE_ROTATION_MIN = -180.0
    STYLE_ROTATION_MAX = 180.0
    STYLE_STROKE_MIN = 0.0
    STYLE_STROKE_MAX = 5.0
    STYLE_LETTER_SPACING_MIN = 0.5
    STYLE_LETTER_SPACING_MAX = 2.5
    STYLE_LINE_SPACING_MIN = 0.5
    STYLE_LINE_SPACING_MAX = 2.5
    BRUSH_EDIT_MAX_OPERATIONS = 500
    BRUSH_EDIT_MAX_POINTS = 100_000
    BRUSH_EDIT_MAX_SIZE = 2048.0
    PROJECT_GLOSSARY_VERSION = 1
    PROJECT_GLOSSARY_CATEGORIES = {
        "人名",
        "组织/团体",
        "地点",
        "作品/道具/技能",
        "行业术语",
        "其他",
    }
    DOUBAO_CURATED_MODELS = {
        "doubao-seed-translation-250915",
        "doubao-seed-2-0-pro-260215",
        "doubao-seed-2-0-lite-260215",
        "doubao-seed-2-0-mini-260215",
    }

    def __init__(self, base_dir: Path, app_paths: AppPaths | None = None):
        self.base_dir = Path(base_dir)
        self.paths = app_paths or resolve_app_paths(self.base_dir)
        self.temp_dir = self.paths.cache_dir
        self.model_dir = self.paths.models_dir
        self.rerender_cache_root = self.temp_dir / "rerender_cache"
        self.projects_root = self.paths.projects_dir
        self.project_index_path = self.paths.project_index_path
        self.output_root = self.paths.output_dir
        self.logs_dir = self.paths.logs_dir
        self.config_dir = self.paths.config_dir
        self.user_font_dir = self.paths.user_fonts_dir
        self.project_font_custom_dir = self.base_dir.parent / "fonts" / "custom"
        self.project_font_dir = self.base_dir.parent / "fonts"
        self.project_font_legacy_dir = self.base_dir.parent / "font"
        self.open_builtin_font_dir = self.base_dir.parent / "fonts" / "system"
        self.open_builtin_font_legacy_dir = self.base_dir.parent / "fonts" / "builtin"
        self.builtin_font_dir = self.base_dir / "manga-image-translator" / "fonts"
        self.paths.ensure_directories()
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.rerender_cache_root.mkdir(parents=True, exist_ok=True)
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.active_sessions: dict[str, str] = {}
        self.active_sessions_lock = threading.Lock()
        self.validated_page_base_images: set[tuple[str, int, int]] = set()
        self.validated_page_base_images_lock = threading.Lock()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _project_dir(self, project_id: str) -> Path:
        return self.projects_root / project_id

    def _project_manifest_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "project.json"

    def _project_session_state_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "session.json"

    def _project_output_dir(self, project_id: str) -> Path:
        return self.output_root / project_id

    def _project_source_dir(self, project_id: str) -> Path:
        return self._project_output_dir(project_id) / "source"

    def _project_translated_dir(self, project_id: str) -> Path:
        return self._project_output_dir(project_id) / "translated"

    def _project_snapshots_dir(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "snapshots"

    def _project_pages_dir(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "pages"

    def _project_page_dir(self, project_id: str, page_id: str) -> Path:
        return self._project_pages_dir(project_id) / page_id

    def _project_page_document_path(self, project_id: str, page_id: str) -> Path:
        return self._project_page_dir(project_id, page_id) / "page_document.json"

    def _translation_request_debug_path(self, project_id: str) -> Path:
        return self.temp_dir / f"{project_id}_translation-request-debug.jsonl"

    def load_persisted_settings(self) -> dict[str, Any]:
        return self._normalize_config(self.paths.load_settings())

    def save_persisted_settings(self, raw_config: dict[str, Any] | None) -> dict[str, Any]:
        normalized = self._normalize_config(raw_config)
        self.paths.save_settings(normalized)
        return normalized

    def normalize_user_config(self, raw_config: dict[str, Any] | None) -> dict[str, Any]:
        return self._normalize_config(raw_config)

    async def validate_user_config(self, raw_config: dict[str, Any] | None) -> dict[str, Any]:
        config = self._normalize_config(raw_config)
        selected_translator = str(config.get("selected_translator") or "").strip()
        if selected_translator not in {"gemini", "doubao-ark", "openai-compatible"}:
            return {
                "ok": False,
                "message": f"当前只支持校验 Gemini / Doubao / OpenAI Compatible，暂不支持 {selected_translator or '当前引擎'}。",
                "translator": selected_translator,
            }

        if not str(config.get("api_key") or "").strip():
            return {
                "ok": False,
                "message": "缺少 API Key。",
                "translator": selected_translator,
            }

        try:
            if selected_translator == "openai-compatible":
                translated = [await self._validate_openai_compatible_connection(config)]
            elif selected_translator == "doubao-ark":
                translated = [await self._validate_doubao_connection(config)]
            else:
                translated = await self._translate_text_batch(
                    ["テスト"],
                    config,
                    session_id=f"settings-validation-{uuid.uuid4().hex[:8]}",
                )
        except Exception as exc:
            return {
                "ok": False,
                "message": str(exc),
                "translator": selected_translator,
            }

        preview = str(translated[0] or "").strip() if translated else ""
        return {
            "ok": bool(preview),
            "message": "连接正常。" if preview else "服务已响应，但没有返回翻译结果。",
            "translator": selected_translator,
            "preview": preview,
        }

    async def _validate_openai_compatible_connection(self, config: dict[str, Any]) -> str:
        base_url = str(config.get("openai_base_url") or "").strip()
        model = str(config.get("openai_model") or config.get("translator_model") or "").strip()
        api_key = str(config.get("api_key") or "").strip()
        if not base_url:
            raise ValueError("缺少 OpenAI Compatible API Base URL。")
        if not model:
            raise ValueError("缺少 OpenAI Compatible 模型名称。")

        return await asyncio.to_thread(
            self._request_chat_completions_validation_sync,
            provider_label="OpenAI Compatible",
            base_url=base_url,
            model=model,
            api_key=api_key,
        )

    async def _validate_doubao_connection(self, config: dict[str, Any]) -> str:
        model = str(config.get("translator_model") or self.DOUBAO_DEFAULT_MODEL).strip()
        api_key = str(config.get("api_key") or "").strip()
        if model.startswith("doubao-seed-translation"):
            return await asyncio.to_thread(
                self._request_responses_validation_sync,
                provider_label="Doubao Ark",
                base_url=self.DOUBAO_ARK_BASE_URL,
                model=model,
                api_key=api_key,
                target_lang=config.get("target_lang"),
            )

        return await asyncio.to_thread(
            self._request_chat_completions_validation_sync,
            provider_label="Doubao Ark",
            base_url=self.DOUBAO_ARK_BASE_URL,
            model=model,
            api_key=api_key,
        )

    def _request_chat_completions_validation_sync(
        self,
        *,
        provider_label: str,
        base_url: str,
        model: str,
        api_key: str,
    ) -> str:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a translation connectivity test. Return only the translated text.",
                },
                {
                    "role": "user",
                    "content": "Translate this Japanese text to Chinese: テスト",
                },
            ],
            "max_tokens": 64,
            "temperature": 0,
            "stream": False,
        }
        response = self._post_validation_json(
            provider_label=provider_label,
            url=self._chat_completions_url(base_url),
            api_key=api_key,
            payload=payload,
        )
        return self._extract_chat_completions_preview(response)

    def _request_chat_completions_text_sync(
        self,
        *,
        provider_label: str,
        base_url: str,
        model: str,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1600,
    ) -> str:
        if not str(base_url or "").strip():
            raise ValueError(f"缺少 {provider_label} API Base URL。")
        if not str(model or "").strip():
            raise ValueError(f"缺少 {provider_label} 模型名称。")
        if not str(api_key or "").strip():
            raise ValueError(f"缺少 {provider_label} API Key。")
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
        }
        response = self._post_validation_json(
            provider_label=provider_label,
            url=self._chat_completions_url(base_url),
            api_key=api_key,
            payload=payload,
        )
        return self._extract_chat_completions_preview(response)

    def _request_gemini_text_sync(
        self,
        *,
        model: str,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        if not str(api_key or "").strip():
            raise ValueError("缺少 Gemini API Key。")
        try:
            from google import genai
            from google.genai import types
        except Exception as exc:
            raise RuntimeError("当前环境缺少 Gemini SDK，无法提取专有名词。") from exc

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model or "gemini-3.1-pro-preview",
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0,
            ),
        )
        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
        raise RuntimeError("Gemini 已响应，但没有返回可读取的文本。")

    def _request_responses_validation_sync(
        self,
        *,
        provider_label: str,
        base_url: str,
        model: str,
        api_key: str,
        target_lang: Any,
    ) -> str:
        payload = {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "テスト",
                            "translation_options": {
                                "target_language": self._validation_language_code(target_lang) or "zh",
                            },
                        }
                    ],
                }
            ],
        }
        response = self._post_validation_json(
            provider_label=provider_label,
            url=self._responses_url(base_url),
            api_key=api_key,
            payload=payload,
        )
        return self._extract_responses_preview(response)

    def _post_validation_json(
        self,
        *,
        provider_label: str,
        url: str,
        api_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        request = urllib_request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(self._format_validation_http_error(provider_label, exc.code, body)) from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"{provider_label} 请求失败：{exc}") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{provider_label} 返回了无法解析的 JSON。") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(f"{provider_label} 返回格式异常。")
        return parsed

    def _chat_completions_url(self, base_url: str) -> str:
        normalized = str(base_url or "").strip().rstrip("/")
        if normalized.endswith("/chat/completions"):
            return normalized
        return f"{normalized}/chat/completions"

    def _responses_url(self, base_url: str) -> str:
        normalized = str(base_url or "").strip().rstrip("/")
        if normalized.endswith("/responses"):
            return normalized
        return f"{normalized}/responses"

    def _extract_chat_completions_preview(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                text = first.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        raise RuntimeError("服务已响应，但没有返回可读取的文本。")

    def _extract_responses_preview(self, payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        pieces: list[str] = []
        output = payload.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for content_item in content:
                    if isinstance(content_item, dict):
                        text = content_item.get("text")
                        if isinstance(text, str) and text.strip():
                            pieces.append(text.strip())

        preview = "\n".join(pieces).strip()
        if preview:
            return preview
        raise RuntimeError("服务已响应，但没有返回可读取的文本。")

    def _format_validation_http_error(self, provider_label: str, status_code: int, body: str) -> str:
        detail = ""
        try:
            payload = json.loads(body)
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("detail") or "").strip()
            elif isinstance(error, str):
                detail = error.strip()
            if not detail and isinstance(payload, dict):
                detail = str(payload.get("message") or payload.get("detail") or "").strip()
        except Exception:
            detail = ""

        if not detail:
            detail = str(body or "").strip()
        if len(detail) > 500:
            detail = f"{detail[:500]}..."
        return f"{provider_label} 请求失败：HTTP {status_code} {detail}".strip()

    def _validation_language_code(self, raw_value: Any) -> str | None:
        normalized = str(raw_value or "").strip().upper()
        return {
            "CHS": "zh",
            "CHT": "zh-Hant",
            "JPN": "ja",
            "ENG": "en",
            "KOR": "ko",
        }.get(normalized)

    def _font_directories_by_source(self) -> dict[str, list[Path]]:
        return {
            "project": [
                self.user_font_dir,
                self.project_font_custom_dir,
                self.project_font_dir,
                self.project_font_legacy_dir,
            ],
            "builtin": [
                self.open_builtin_font_dir,
                self.open_builtin_font_legacy_dir,
                self.builtin_font_dir,
            ],
        }

    def _read_json_file(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _read_jsonl_file(self, path: Path) -> list[Any]:
        if not path.exists():
            return []
        rows: list[Any] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        rows.append({"type": "unparsed_line", "raw": line})
        except Exception:
            return []
        return rows

    def _write_json_file(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=f"{path.stem}_", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(temp_path)

    def _sanitize_config_for_storage(self, config: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(config, dict):
            return {}
        sanitized = json.loads(json.dumps(config, ensure_ascii=False))
        selected_translator = str(sanitized.get("selected_translator") or "").strip()
        if selected_translator:
            sanitized["translator"] = selected_translator
        for secret_key in ("api_key", "image_cleanup_api_key", "advanced_erase_api_key"):
            if secret_key in sanitized:
                sanitized[secret_key] = ""
        return sanitized

    def _normalize_review_mode(self, raw_value: Any) -> str:
        value = str(raw_value or self.DEFAULT_REVIEW_MODE).strip().lower()
        if value not in self.REVIEW_MODES:
            return self.DEFAULT_REVIEW_MODE
        return value

    def _session_review_mode(self, session: dict[str, Any]) -> str:
        return self._normalize_review_mode(session.get("review_mode"))

    def _project_cover_url(self, project_id: str, session: dict[str, Any]) -> str:
        source_images = list(session.get("source_images") or [])
        if not source_images:
            return ""
        first_image = source_images[0]
        stored_name = str(first_image.get("stored_name") or "")
        if not stored_name:
            return ""

        output_dir = Path(session["translated_dir"])
        preferred_format = self._normalize_rerender_output_format(
            (session.get("last_config") or {}).get("rerender_output_format")
        )
        current_output = self._current_translated_output(session, output_dir, stored_name, preferred_format)
        if current_output is not None and current_output.exists():
            return f"/output/{project_id}/translated/{current_output.name}"
        return f"/output/{project_id}/source/{stored_name}"

    def _serialize_session_state(self, project_id: str, session: dict[str, Any]) -> dict[str, Any]:
        created_at = str(session.get("project_created_at") or self._now_iso())
        updated_at = self._now_iso()
        session["project_id"] = project_id
        session["project_created_at"] = created_at
        session["project_updated_at"] = updated_at

        return {
            "project_id": project_id,
            "project_title": str(session.get("project_title") or ""),
            "project_note": str(session.get("project_note") or ""),
            "review_mode": self._session_review_mode(session),
            "project_created_at": created_at,
            "project_updated_at": updated_at,
            "source_dir": str(session.get("source_dir") or ""),
            "translated_dir": str(session.get("translated_dir") or ""),
            "source_images": list(session.get("source_images") or []),
            "download_path": str(session.get("download_path") or ""),
            "translated_output_map": dict(session.get("translated_output_map") or {}),
            "rerender_generation": int(session.get("rerender_generation") or 0),
            "manual_regions": dict(session.get("manual_regions") or {}),
            "advanced_erase_pages": dict(session.get("advanced_erase_pages") or {}),
            "project_glossary": self._normalize_project_glossary(session.get("project_glossary")),
            "workflow_stage": str(session.get("workflow_stage") or "idle"),
            "mask_debug_dir": str(session.get("mask_debug_dir") or ""),
            "rerender_cache_dir": str(session.get("rerender_cache_dir") or ""),
            "last_config": self._sanitize_config_for_storage(session.get("last_config") or {}),
            "deferred_output_names": sorted(str(item) for item in (session.get("deferred_output_names") or [])),
            "translation_region_overrides": dict(session.get("translation_region_overrides") or {}),
            "translation_region_skip_overrides": dict(session.get("translation_region_skip_overrides") or {}),
            "translation_region_disabled_overrides": dict(session.get("translation_region_disabled_overrides") or {}),
            "translation_region_layout_overrides": dict(session.get("translation_region_layout_overrides") or {}),
            "style_region_overrides": dict(session.get("style_region_overrides") or {}),
        }

    def _read_snapshot_manifests(self, project_id: str) -> list[dict[str, Any]]:
        snapshots_dir = self._project_snapshots_dir(project_id)
        manifests: list[dict[str, Any]] = []
        if not snapshots_dir.exists():
            return manifests

        for path in sorted(snapshots_dir.glob("*.json")):
            payload = self._read_json_file(path, {})
            if isinstance(payload, dict):
                payload["_path"] = str(path)
                manifests.append(payload)

        manifests.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return manifests

    def _collect_referenced_output_names(self, project_id: str, session: dict[str, Any]) -> set[str]:
        referenced: set[str] = set()
        translated_output_map = session.get("translated_output_map") or {}
        referenced.update(str(name) for name in translated_output_map.values() if str(name))

        for snapshot in self._read_snapshot_manifests(project_id):
            output_map = snapshot.get("translated_output_map") or {}
            if isinstance(output_map, dict):
                referenced.update(str(name) for name in output_map.values() if str(name))

        return referenced

    def _infer_source_images_from_dir(self, source_dir: Path) -> list[dict[str, str]]:
        if not source_dir.exists() or not source_dir.is_dir():
            return []

        valid_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
        source_images: list[dict[str, str]] = []
        for path in sorted(source_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in valid_suffixes:
                continue
            source_images.append(
                {
                    "name": path.name,
                    "stored_name": path.name,
                }
            )
        return source_images

    def _merge_recovered_source_images(
        self,
        existing_images: list[dict[str, Any]],
        inferred_images: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        if not inferred_images:
            return []

        existing_by_stored_name = {
            str(item.get("stored_name") or ""): item
            for item in existing_images
            if isinstance(item, dict) and str(item.get("stored_name") or "").strip()
        }
        existing_by_name = {
            str(item.get("name") or ""): item
            for item in existing_images
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }

        recovered_images: list[dict[str, str]] = []
        for index, inferred in enumerate(inferred_images):
            inferred_stored_name = str(inferred.get("stored_name") or "").strip()
            inferred_display_name = str(inferred.get("name") or inferred_stored_name).strip() or inferred_stored_name

            matched_existing = existing_by_stored_name.get(inferred_stored_name)
            if matched_existing is None:
                matched_existing = existing_by_name.get(inferred_stored_name)
            if matched_existing is None and index < len(existing_images):
                candidate = existing_images[index]
                if isinstance(candidate, dict):
                    matched_existing = candidate

            display_name = inferred_display_name
            if isinstance(matched_existing, dict):
                display_name = (
                    str(matched_existing.get("name") or "").strip()
                    or str(matched_existing.get("stored_name") or "").strip()
                    or inferred_display_name
                )

            recovered_images.append(
                {
                    "name": display_name,
                    "stored_name": inferred_stored_name,
                }
            )

        return recovered_images

    def _source_images_need_recovery(
        self,
        source_dir: Path,
        existing_images: list[dict[str, Any]],
        inferred_images: list[dict[str, str]],
    ) -> bool:
        if not inferred_images:
            return False
        if not existing_images:
            return True

        existing_names = [
            str(item.get("stored_name") or "").strip()
            for item in existing_images
            if isinstance(item, dict) and str(item.get("stored_name") or "").strip()
        ]
        inferred_names = [
            str(item.get("stored_name") or "").strip()
            for item in inferred_images
            if str(item.get("stored_name") or "").strip()
        ]
        if not existing_names:
            return True

        existing_name_set = set(existing_names)
        inferred_name_set = set(inferred_names)
        if existing_name_set != inferred_name_set:
            return True

        return any(not (source_dir / name).exists() for name in existing_names)

    def _translated_output_map_needs_recovery(
        self,
        translated_dir: Path,
        source_images: list[dict[str, Any]],
        translated_output_map: dict[str, Any],
    ) -> bool:
        if not translated_dir.exists() or not translated_dir.is_dir():
            return bool(translated_output_map)

        source_image_names = {
            str(item.get("stored_name") or "").strip()
            for item in source_images
            if isinstance(item, dict) and str(item.get("stored_name") or "").strip()
        }
        if not translated_output_map:
            return True

        for stored_name, output_name in translated_output_map.items():
            normalized_stored_name = str(stored_name or "").strip()
            normalized_output_name = str(output_name or "").strip()
            if not normalized_stored_name or normalized_stored_name not in source_image_names:
                return True
            if not normalized_output_name or not (translated_dir / normalized_output_name).exists():
                return True

        return False

    def _infer_translated_output_map(
        self,
        translated_dir: Path,
        source_images: list[dict[str, Any]],
        preferred_format: str = "source",
    ) -> dict[str, str]:
        if not translated_dir.exists() or not translated_dir.is_dir():
            return {}

        translated_output_map: dict[str, str] = {}
        for image in source_images:
            stored_name = str(image.get("stored_name") or "")
            if not stored_name:
                continue
            current_output = self._find_existing_translated_output(translated_dir, stored_name, preferred_format)
            if current_output is not None and current_output.exists():
                translated_output_map[stored_name] = current_output.name
        return translated_output_map

    def _translated_outputs_cover_all_sources(
        self,
        session: dict[str, Any],
        translated_dir: Path,
        preferred_format: str,
    ) -> bool:
        source_images = [
            image
            for image in (session.get("source_images") or [])
            if isinstance(image, dict) and str(image.get("stored_name") or "").strip()
        ]
        if not source_images:
            return False

        for image in source_images:
            stored_name = str(image.get("stored_name") or "").strip()
            current_output = self._current_translated_output(session, translated_dir, stored_name, preferred_format)
            if current_output is None or not current_output.exists():
                return False
        return True

    def _has_persisted_page_regions(self, project_id: str, source_images: list[dict[str, Any]]) -> bool:
        for image in source_images:
            if not isinstance(image, dict):
                continue
            stored_name = str(image.get("stored_name") or "").strip()
            if not stored_name:
                continue
            payload = self._read_json_file(self._project_page_document_path(project_id, stored_name), {})
            regions = payload.get("regions") if isinstance(payload, dict) else None
            if isinstance(regions, list) and any(isinstance(region, dict) for region in regions):
                return True
        return False

    def _has_rerenderable_page_caches(self, project_id: str, session: dict[str, Any]) -> bool:
        cache_dir = self._session_rerender_cache_dir(session, project_id)
        for image in session.get("source_images") or []:
            if not isinstance(image, dict):
                continue
            stored_name = str(image.get("stored_name") or "").strip()
            if stored_name and self._has_rerenderable_page_cache(cache_dir / stored_name):
                return True
        return False

    def _latest_snapshot_workflow_stage(self, project_id: str) -> str:
        for snapshot in self._read_snapshot_manifests(project_id):
            stage = str(snapshot.get("workflow_stage") or "").strip().lower()
            if stage in {"translated", "detected"}:
                return stage
        return ""

    def _infer_restored_workflow_stage(
        self,
        project_id: str,
        session: dict[str, Any],
        manifest: dict[str, Any],
        translated_dir: Path,
        preferred_format: str,
    ) -> str:
        current_stage = str(session.get("workflow_stage") or "").strip().lower()
        if current_stage not in {"idle", "detecting", "detected", "translating", "translated"}:
            current_stage = "idle"

        if self._translated_outputs_cover_all_sources(session, translated_dir, preferred_format):
            return "translated"

        source_images = list(session.get("source_images") or [])
        has_page_regions = self._has_persisted_page_regions(project_id, source_images)
        has_page_cache = self._has_rerenderable_page_caches(project_id, session)
        has_editable_state = has_page_regions or has_page_cache

        if current_stage == "translated" and has_editable_state:
            return "translated"

        manifest_stage = str(manifest.get("workflow_stage") or "").strip().lower() if isinstance(manifest, dict) else ""
        snapshot_stage = self._latest_snapshot_workflow_stage(project_id)
        for persisted_stage in (snapshot_stage, manifest_stage):
            if persisted_stage == "translated" and has_editable_state:
                return "translated"
            if persisted_stage == "detected" and has_editable_state:
                return "detected"

        if current_stage == "detected":
            return "detected"
        if has_editable_state:
            return "detected"
        return "idle"

    def _recover_session_from_manifest(self, project_id: str, manifest: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(manifest, dict) or not manifest:
            return None

        source_dir = Path(str(manifest.get("source_dir") or "")) if str(manifest.get("source_dir") or "").strip() else self._project_source_dir(project_id)
        translated_dir = Path(str(manifest.get("translated_dir") or "")) if str(manifest.get("translated_dir") or "").strip() else self._project_translated_dir(project_id)
        source_images = self._infer_source_images_from_dir(source_dir)
        translated_output_map = self._infer_translated_output_map(translated_dir, source_images)
        workflow_stage = str(manifest.get("workflow_stage") or ("translated" if translated_output_map else "idle"))

        return {
            "source_dir": str(source_dir),
            "translated_dir": str(translated_dir),
            "source_images": source_images,
            "download_path": "",
            "translated_output_map": translated_output_map,
            "rerender_generation": 0,
            "manual_regions": {},
            "workflow_stage": workflow_stage,
            "mask_debug_dir": "",
            "rerender_cache_dir": str(self._rerender_cache_dir(project_id)),
            "last_config": {},
            "deferred_output_names": set(),
            "translation_region_overrides": {},
            "translation_region_skip_overrides": {},
            "translation_region_disabled_overrides": {},
            "translation_region_layout_overrides": {},
            "style_region_overrides": {},
            "project_id": project_id,
            "project_title": str(manifest.get("title") or manifest.get("project_title") or project_id),
            "project_note": str(manifest.get("note") or manifest.get("project_note") or ""),
            "review_mode": self._normalize_review_mode(manifest.get("review_mode")),
            "project_created_at": str(manifest.get("created_at") or manifest.get("project_created_at") or self._now_iso()),
            "project_updated_at": str(manifest.get("updated_at") or manifest.get("project_updated_at") or self._now_iso()),
        }

    def _garbage_collect_project_outputs(self, project_id: str, session: dict[str, Any]) -> None:
        output_dir = Path(session.get("translated_dir") or "")
        if not output_dir.exists():
            return

        referenced = self._collect_referenced_output_names(project_id, session)
        for path in output_dir.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() not in self.TRANSLATED_OUTPUT_SUFFIXES:
                continue
            if path.name in referenced:
                continue
            with contextlib.suppress(OSError):
                path.unlink()

    def _enforce_snapshot_retention(self, project_id: str, session: dict[str, Any]) -> None:
        manifests = self._read_snapshot_manifests(project_id)
        auto_snapshots = [item for item in manifests if not bool(item.get("pinned"))]

        victims: list[dict[str, Any]] = []
        while len(manifests) - len(victims) > 30:
            candidate = next((item for item in reversed(auto_snapshots) if item not in victims), None)
            if candidate is None:
                break
            victims.append(candidate)

        while len(auto_snapshots) - sum(1 for item in victims if item in auto_snapshots) > 20:
            candidate = next((item for item in reversed(auto_snapshots) if item not in victims), None)
            if candidate is None:
                break
            victims.append(candidate)

        for victim in victims:
            victim_path = Path(str(victim.get("_path") or ""))
            if victim_path.exists():
                with contextlib.suppress(OSError):
                    victim_path.unlink()

        self._garbage_collect_project_outputs(project_id, session)

    def _create_project_snapshot(
        self,
        project_id: str,
        session: dict[str, Any],
        kind: str,
        summary: str,
    ) -> dict[str, Any]:
        created_at = self._now_iso()
        snapshot_id = f"{created_at.replace(':', '').replace('-', '')}_{uuid.uuid4().hex[:8]}"
        snapshot = {
            "snapshot_id": snapshot_id,
            "project_id": project_id,
            "created_at": created_at,
            "kind": kind,
            "summary": summary,
            "translated_output_map": dict(session.get("translated_output_map") or {}),
            "workflow_stage": str(session.get("workflow_stage") or "idle"),
            "review_mode": self._session_review_mode(session),
            "last_config": self._sanitize_config_for_storage(session.get("last_config") or {}),
            "manual_regions": dict(session.get("manual_regions") or {}),
            "project_glossary": self._normalize_project_glossary(session.get("project_glossary")),
            "translation_region_overrides": dict(session.get("translation_region_overrides") or {}),
            "translation_region_skip_overrides": dict(session.get("translation_region_skip_overrides") or {}),
            "translation_region_disabled_overrides": dict(session.get("translation_region_disabled_overrides") or {}),
            "translation_region_layout_overrides": dict(session.get("translation_region_layout_overrides") or {}),
            "style_region_overrides": dict(session.get("style_region_overrides") or {}),
            "cover_image": self._project_cover_url(project_id, session),
            "pinned": False,
        }
        self._write_json_file(self._project_snapshots_dir(project_id) / f"{snapshot_id}.json", snapshot)
        return snapshot

    def _build_project_summary(self, project_id: str, session: dict[str, Any], latest_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        manifests = self._read_snapshot_manifests(project_id)
        latest = latest_snapshot or (manifests[0] if manifests else None)
        return {
            "project_id": project_id,
            "title": str(session.get("project_title") or project_id),
            "note": str(session.get("project_note") or ""),
            "review_mode": self._session_review_mode(session),
            "created_at": str(session.get("project_created_at") or self._now_iso()),
            "updated_at": str(session.get("project_updated_at") or self._now_iso()),
            "page_count": len(session.get("source_images") or []),
            "workflow_stage": str(session.get("workflow_stage") or "idle"),
            "cover_image": self._project_cover_url(project_id, session),
            "latest_snapshot_id": str((latest or {}).get("snapshot_id") or ""),
            "latest_snapshot_kind": str((latest or {}).get("kind") or ""),
            "latest_snapshot_summary": str((latest or {}).get("summary") or ""),
            "snapshot_count": len(manifests),
            "glossary_count": len(self._normalize_project_glossary(session.get("project_glossary")).get("entries") or []),
            "archived": bool(session.get("project_archived", False)),
            "is_busy": self.is_session_busy(project_id),
            "busy_action": self.get_session_busy_action(project_id),
        }

    def _write_project_index(self, summaries: list[dict[str, Any]]) -> None:
        summaries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        self._write_json_file(self.project_index_path, summaries)

    def _refresh_project_index_entry(self, project_summary: dict[str, Any]) -> None:
        existing = self._read_json_file(self.project_index_path, [])
        next_items = [item for item in existing if isinstance(item, dict) and str(item.get("project_id") or "") != project_summary["project_id"]]
        next_items.append(project_summary)
        self._write_project_index(next_items)

    def initialize_project(
        self,
        project_id: str,
        session: dict[str, Any],
        title: str,
        note: str = "",
        review_mode: str | None = None,
    ) -> None:
        now = self._now_iso()
        session["project_id"] = project_id
        session["project_title"] = title.strip() or project_id
        session["project_note"] = note.strip()
        session["review_mode"] = self._normalize_review_mode(review_mode or session.get("review_mode"))
        session["project_created_at"] = str(session.get("project_created_at") or now)
        session["project_updated_at"] = now
        self.persist_project_state(project_id, session, persist_page_documents=True)

    def attach_base_images(
        self,
        project_id: str,
        session: dict[str, Any],
        image_paths: list[str],
    ) -> dict[str, Any]:
        source_images = list(session.get("source_images") or [])
        if not source_images:
            raise ValueError("当前项目还没有原始页面，无法补充无字图。")
        if not image_paths:
            raise ValueError("没有收到可用的无字图文件。")

        exact_name_map: dict[str, dict[str, Any]] = {}
        stem_name_map: dict[str, list[dict[str, Any]]] = {}
        for image in source_images:
            display_name = str(image.get("name") or image.get("stored_name") or "").strip()
            stored_name = str(image.get("stored_name") or "").strip()
            if not display_name or not stored_name:
                continue
            exact_name_map[display_name.lower()] = image
            stem_name_map.setdefault(Path(display_name).stem.lower(), []).append(image)

        matched_pages: list[dict[str, str]] = []
        matched_page_ids: list[str] = []
        unmatched_files: list[str] = []
        invalid_files: list[str] = []

        for raw_image_path in image_paths:
            image_path = Path(raw_image_path)
            upload_name = image_path.name
            matched_image = exact_name_map.get(upload_name.lower())
            if matched_image is None:
                candidates = stem_name_map.get(image_path.stem.lower(), [])
                if len(candidates) == 1:
                    matched_image = candidates[0]
            if matched_image is None:
                unmatched_files.append(upload_name)
                continue

            image_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if image_bgr is None:
                invalid_files.append(upload_name)
                continue

            stored_name = str(matched_image.get("stored_name") or "").strip()
            if not stored_name:
                unmatched_files.append(upload_name)
                continue

            page_cache_dir = self._session_page_cache_dir(session, project_id, stored_name)
            page_cache_dir.mkdir(parents=True, exist_ok=True)
            inpainted_path = page_cache_dir / "inpainted.png"
            cv2.imwrite(str(inpainted_path), image_bgr)

            matched_page_ids.append(stored_name)
            matched_pages.append(
                {
                    "stored_name": stored_name,
                    "page_name": str(matched_image.get("name") or stored_name),
                    "uploaded_name": upload_name,
                }
            )

        if not matched_page_ids:
            raise ValueError("没有找到可匹配的页面文件名。请确保无字图与原图文件名一致。")

        unique_page_ids = list(dict.fromkeys(matched_page_ids))
        self.persist_project_state(
            project_id,
            session,
            snapshot_kind="base_images_uploaded",
            snapshot_summary=f"补充无字图 {len(unique_page_ids)} 页",
            persist_page_documents=True,
            page_ids=unique_page_ids,
        )

        return {
            "matched_count": len(matched_pages),
            "matched_pages": matched_pages,
            "updated_page_ids": unique_page_ids,
            "unmatched_count": len(unmatched_files),
            "unmatched_files": unmatched_files,
            "invalid_count": len(invalid_files),
            "invalid_files": invalid_files,
        }

    async def advanced_erase_page(
        self,
        project_id: str,
        session: dict[str, Any],
        page_id: str,
        raw_config: dict[str, Any] | None,
        action: str = "erase",
        selections: Any = None,
        local_mask_mode: Any = None,
    ) -> dict[str, Any]:
        if not any(str(image.get("stored_name") or "") == page_id for image in (session.get("source_images") or [])):
            raise FileNotFoundError("目标页面不存在，请刷新后重试。")

        normalized_action = str(action or "erase").strip().lower()
        if normalized_action in {"restore", "traditional", "rollback"}:
            result = self._restore_traditional_erase_page(project_id, session, page_id)
            return {
                **self.build_client_session_payload(project_id, session),
                "advanced_erase": result,
            }

        config = self.capture_page_command_config(session, raw_config)
        if normalized_action in {"local-selection", "local_model_selection", "local-model-selection", "model-selection"}:
            return await self._local_model_selection_erase_page(
                project_id=project_id,
                session=session,
                page_id=page_id,
                config=config,
                selections=selections,
                local_mask_mode=local_mask_mode,
            )

        if config.get("advanced_erase_provider") != self.ADVANCED_ERASE_DEFAULT_PROVIDER:
            raise ValueError("高级擦除第一版仅支持火山引擎 Ark / Seedream。")
        if not str(config.get("advanced_erase_api_key") or "").strip():
            raise ValueError("缺少高级擦除 API Key，请先在高级擦除 API 配置里填写。")
        if not str(config.get("advanced_erase_model") or "").strip():
            raise ValueError("缺少高级擦除模型名称。")
        if not str(config.get("advanced_erase_base_url") or "").strip():
            raise ValueError("缺少高级擦除接口地址。")

        if normalized_action in {"selection", "selection-erase", "selected"}:
            return await self._advanced_selection_erase_page(
                project_id=project_id,
                session=session,
                page_id=page_id,
                config=config,
                selections=selections,
            )

        source_path = self.get_page_source_image_path(project_id, session, page_id)
        page_cache_dir = self._session_page_cache_dir(session, project_id, page_id)
        if not self._ensure_page_base_image_cache(source_path, page_cache_dir):
            raise RuntimeError("无法准备当前页空页缓存。")
        backup_path = self._ensure_advanced_erase_traditional_backup(page_cache_dir)

        source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if source_bgr is None:
            raise RuntimeError(f"无法读取原图: {source_path}")
        source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)

        client = create_image_cleanup_client(
            mode="seedream-image",
            api_key=config["advanced_erase_api_key"],
            model=config["advanced_erase_model"],
            api_url=config["advanced_erase_base_url"],
            timeout_seconds=config["advanced_erase_timeout_seconds"],
        )
        print(
            "[DEBUG] Advanced erase request "
            f"file={source_path.name} provider={config['advanced_erase_provider']} "
            f"model={config['advanced_erase_model']} "
            f"size={source_rgb.shape[1]}x{source_rgb.shape[0]}"
        )
        attempt_id = self._advanced_erase_attempt_id()
        attempt_dir = self._advanced_erase_attempt_dir(page_cache_dir)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        input_path = attempt_dir / f"{attempt_id}.input.png"
        seedream_output_path = attempt_dir / f"{attempt_id}.seedream.png"
        output_path = attempt_dir / f"{attempt_id}.png"
        diff_mask_path = attempt_dir / f"{attempt_id}.diff.png"
        model_mask_raw_path = attempt_dir / f"{attempt_id}.model-mask-raw.png"
        model_mask_path = attempt_dir / f"{attempt_id}.model-mask.png"
        allowed_mask_path = attempt_dir / f"{attempt_id}.allowed.png"
        mask_path = attempt_dir / f"{attempt_id}.mask.png"
        metadata_path = attempt_dir / f"{attempt_id}.json"
        self._save_rgb_image_atomic(input_path, source_rgb)

        edited_rgb = await asyncio.wait_for(
            client.remove_text(source_rgb, None, ADVANCED_IMAGE_ERASE_PROMPT),
            timeout=int(config["advanced_erase_timeout_seconds"]) + 10,
        )
        edited_debug_rgb = self._normalize_advanced_erase_edited_image(source_rgb, edited_rgb)
        self._save_rgb_image_atomic(seedream_output_path, edited_debug_rgb)
        raw_diff_mask = self._build_advanced_erase_change_mask(source_rgb, edited_debug_rgb)
        raw_changed_ratio = float(cv2.countNonZero(raw_diff_mask)) / float(raw_diff_mask.size or 1)
        cv2.imwrite(str(diff_mask_path), raw_diff_mask)
        model_allowed_mask: np.ndarray | None = None
        model_container_count = 0
        model_mask_error = ""
        try:
            model_mask_rgb = await asyncio.wait_for(
                client.remove_text(source_rgb, None, ADVANCED_IMAGE_CONTAINER_MASK_PROMPT),
                timeout=int(config["advanced_erase_timeout_seconds"]) + 10,
            )
            model_mask_debug_rgb = self._normalize_advanced_erase_edited_image(source_rgb, model_mask_rgb)
            self._save_rgb_image_atomic(model_mask_raw_path, model_mask_debug_rgb)
            model_allowed_mask, model_container_count = self._build_advanced_erase_model_container_mask(
                source_rgb,
                model_mask_debug_rgb,
            )
            if model_allowed_mask is not None:
                cv2.imwrite(str(model_mask_path), model_allowed_mask)
        except Exception as exc:
            model_mask_error = str(exc)

        region_allowed_mask, allowed_region_count = self._build_advanced_erase_allowed_mask(
            source_rgb,
            page_cache_dir,
            config,
            page_id,
            session,
        )
        allowed_mask, mask_mode = self._select_advanced_erase_allowed_mask(
            model_allowed_mask,
            region_allowed_mask,
        )
        if allowed_mask is not None:
            cv2.imwrite(str(allowed_mask_path), allowed_mask)
        debug_mask = self._advanced_erase_final_mask(raw_diff_mask, allowed_mask)
        debug_changed_ratio = float(cv2.countNonZero(debug_mask)) / float(debug_mask.size or 1)
        cv2.imwrite(str(mask_path), debug_mask)
        metadata = {
            "attempt_id": attempt_id,
            "created_at": self._now_iso(),
            "page_id": page_id,
            "source_path": str(source_path),
            "provider": config["advanced_erase_provider"],
            "api_url": config["advanced_erase_base_url"],
            "model": config["advanced_erase_model"],
            "changed_ratio": debug_changed_ratio,
            "raw_changed_ratio": raw_changed_ratio,
            "traditional_backup": str(backup_path),
            "input_image": str(input_path),
            "seedream_output": str(seedream_output_path),
            "diff_mask": str(diff_mask_path),
            "model_container_mask_raw": str(model_mask_raw_path) if model_mask_raw_path.exists() else "",
            "model_container_mask": str(model_mask_path) if model_allowed_mask is not None else "",
            "model_container_count": model_container_count,
            "model_container_error": model_mask_error,
            "allowed_mask": str(allowed_mask_path) if allowed_mask is not None else "",
            "final_mask": str(mask_path),
            "allowed_region_count": allowed_region_count,
            "mask_mode": mask_mode,
        }
        try:
            composite_rgb, mask, changed_ratio = self._composite_advanced_erase_result(
                source_rgb,
                edited_debug_rgb,
                change_mask=debug_mask,
                max_changed_ratio=(
                    self.ADVANCED_ERASE_MAX_REGION_REPLACE_RATIO
                    if allowed_mask is not None
                    else self.ADVANCED_ERASE_MAX_CHANGED_RATIO
                ),
                clean_white_containers=allowed_mask is not None,
            )
        except RuntimeError as exc:
            self._write_json_file(metadata_path, {
                **metadata,
                "rejected": True,
                "error": str(exc),
            })
            raise RuntimeError(
                f"{exc} 调试文件已保存到: {attempt_dir}；"
                f"输入图: {input_path.name}；AI 返回图: {seedream_output_path.name}；"
                f"原始差异 mask: {diff_mask_path.name}；最终 mask: {mask_path.name}；"
                f"最终差异比例约 {debug_changed_ratio:.2%}，原始差异比例约 {raw_changed_ratio:.2%}。"
            ) from exc

        self._save_rgb_image_atomic(output_path, composite_rgb)
        cv2.imwrite(str(mask_path), mask)
        self._write_json_file(metadata_path, {
            **metadata,
            "changed_ratio": changed_ratio,
            "composited_output": str(output_path),
            "rejected": False,
        })

        inpainted_path = page_cache_dir / "inpainted.png"
        self._save_rgb_image_atomic(inpainted_path, composite_rgb)
        self._record_advanced_erase_page_state(
            session,
            page_id,
            {
                "mode": "advanced",
                "updated_at": self._now_iso(),
                "attempt_id": attempt_id,
                "provider": config["advanced_erase_provider"],
                "model": config["advanced_erase_model"],
                "changed_ratio": changed_ratio,
            },
        )
        self.persist_project_state(
            project_id,
            session,
            snapshot_kind="advanced_erase",
            snapshot_summary=f"高级擦除 {self._page_display_name(session, page_id)}",
            persist_page_documents=True,
            page_ids=[page_id],
        )

        return {
            **self.build_client_session_payload(project_id, session),
            "advanced_erase": {
                "action": "erase",
                "page_id": page_id,
                "attempt_id": attempt_id,
                "changed_ratio": changed_ratio,
            },
        }

    def brush_edit_page(
        self,
        project_id: str,
        session: dict[str, Any],
        page_id: str,
        operations: Any,
    ) -> dict[str, Any]:
        if not any(str(image.get("stored_name") or "") == page_id for image in (session.get("source_images") or [])):
            raise FileNotFoundError("目标页面不存在，请刷新后重试。")

        source_path = self.get_page_source_image_path(project_id, session, page_id)
        page_cache_dir = self._session_page_cache_dir(session, project_id, page_id)
        if not self._ensure_page_base_image_cache(source_path, page_cache_dir):
            raise RuntimeError("无法准备当前页空页缓存。")

        base_path = page_cache_dir / "inpainted.png"
        base_bgr = cv2.imread(str(base_path), cv2.IMREAD_COLOR)
        source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if base_bgr is None:
            raise RuntimeError(f"无法读取当前空页: {base_path}")
        if source_bgr is None:
            raise RuntimeError(f"无法读取原图: {source_path}")

        base_rgb = cv2.cvtColor(base_bgr, cv2.COLOR_BGR2RGB)
        source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)
        if source_rgb.shape[:2] != base_rgb.shape[:2]:
            source_rgb = cv2.resize(
                source_rgb,
                (base_rgb.shape[1], base_rgb.shape[0]),
                interpolation=cv2.INTER_AREA,
            )

        normalized_operations = self._normalize_brush_edit_operations(operations, base_rgb.shape)
        if not normalized_operations:
            raise ValueError("请至少绘制一笔后再保存。")

        self._ensure_advanced_erase_traditional_backup(page_cache_dir)
        edited_rgb = self._apply_brush_edit_operations(base_rgb, source_rgb, normalized_operations)
        self._save_rgb_image_atomic(base_path, edited_rgb)

        changed_pixels = np.any(edited_rgb != base_rgb, axis=2).astype(np.uint8) * 255
        changed_ratio = float(cv2.countNonZero(changed_pixels)) / float(changed_pixels.size or 1)
        self._record_advanced_erase_page_state(
            session,
            page_id,
            {
                "mode": "brush",
                "updated_at": self._now_iso(),
                "operation_count": len(normalized_operations),
                "changed_ratio": changed_ratio,
            },
        )
        self.persist_project_state(
            project_id,
            session,
            snapshot_kind="brush_edit",
            snapshot_summary=f"画笔编辑 {self._page_display_name(session, page_id)}",
            persist_page_documents=True,
            page_ids=[page_id],
        )

        return {
            **self.build_client_session_payload(project_id, session),
            "brush_edit": {
                "action": "brush",
                "page_id": page_id,
                "operation_count": len(normalized_operations),
                "changed_ratio": changed_ratio,
            },
        }

    def _normalize_brush_edit_operations(
        self,
        raw_operations: Any,
        image_shape: tuple[int, ...],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_operations, list):
            return []
        if len(raw_operations) > self.BRUSH_EDIT_MAX_OPERATIONS:
            raise ValueError(f"单次最多保存 {self.BRUSH_EDIT_MAX_OPERATIONS} 笔，请分次保存。")

        height, width = image_shape[:2]
        total_points = 0
        normalized: list[dict[str, Any]] = []
        for raw_operation in raw_operations:
            if not isinstance(raw_operation, dict):
                continue
            mode = str(raw_operation.get("mode") or "").strip().lower()
            if mode not in {"paint", "erase", "restore"}:
                continue

            raw_points = raw_operation.get("points")
            if not isinstance(raw_points, list):
                continue
            points: list[tuple[int, int]] = []
            coordinate_space = str(raw_operation.get("coordinate_space") or "normalized").strip().lower()
            for raw_point in raw_points:
                if isinstance(raw_point, dict):
                    raw_x = raw_point.get("x")
                    raw_y = raw_point.get("y")
                elif isinstance(raw_point, (list, tuple)) and len(raw_point) >= 2:
                    raw_x, raw_y = raw_point[:2]
                else:
                    continue
                try:
                    x = float(raw_x)
                    y = float(raw_y)
                except (TypeError, ValueError):
                    continue
                if not np.isfinite(x) or not np.isfinite(y):
                    continue
                if coordinate_space == "normalized":
                    x *= max(width - 1, 1)
                    y *= max(height - 1, 1)
                points.append((
                    int(round(np.clip(x, 0, max(width - 1, 0)))),
                    int(round(np.clip(y, 0, max(height - 1, 0)))),
                ))

            if not points:
                continue
            total_points += len(points)
            if total_points > self.BRUSH_EDIT_MAX_POINTS:
                raise ValueError("本次画笔轨迹过多，请先保存当前修改后再继续。")

            size = self._normalize_float_range(
                raw_operation.get("size"),
                1.0,
                min(self.BRUSH_EDIT_MAX_SIZE, float(max(width, height))),
                digits=2,
            ) or 20.0
            feather = 0.0
            if mode == "paint":
                feather = self._normalize_float_range(
                    raw_operation.get("feather"),
                    0.0,
                    size / 2.0,
                    digits=2,
                ) or 0.0
            normalized.append({
                "mode": mode,
                "color": self._rgb_color_payload(raw_operation.get("color"), (255, 255, 255)),
                "size": size,
                "feather": feather,
                "points": points,
            })
        return normalized

    def _build_brush_edit_mask(
        self,
        image_shape: tuple[int, ...],
        points: list[tuple[int, int]],
        size: float,
        feather: float,
    ) -> np.ndarray:
        height, width = image_shape[:2]
        radius = max(float(size) / 2.0, 0.5)
        feather = float(np.clip(feather, 0.0, radius))
        core_radius = max(radius - feather, 0.5)
        thickness = max(1, int(round(core_radius * 2.0)))
        mask = np.zeros((height, width), dtype=np.uint8)
        point_array = np.asarray(points, dtype=np.int32)
        if len(points) > 1:
            cv2.polylines(mask, [point_array], False, 255, thickness=thickness, lineType=cv2.LINE_AA)
        circle_radius = max(1, int(round(core_radius)))
        for point in (points if len(points) == 1 else (points[0], points[-1])):
            cv2.circle(mask, point, circle_radius, 255, thickness=-1, lineType=cv2.LINE_AA)
        if feather > 0:
            mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=max(feather / 3.0, 0.35))
        return mask

    def _apply_brush_edit_operations(
        self,
        base_rgb: np.ndarray,
        source_rgb: np.ndarray,
        operations: list[dict[str, Any]],
    ) -> np.ndarray:
        edited = base_rgb.copy()
        for operation in operations:
            mode = str(operation.get("mode") or "")
            mask = self._build_brush_edit_mask(
                edited.shape,
                list(operation.get("points") or []),
                float(operation.get("size") or 20.0),
                float(operation.get("feather") or 0.0),
            )
            if not np.any(mask):
                continue
            if mode == "erase":
                target = base_rgb
            elif mode == "restore":
                target = source_rgb
            else:
                target = np.empty_like(edited)
                target[:, :] = np.asarray(operation.get("color") or (255, 255, 255), dtype=np.uint8)
            alpha = mask[:, :, None].astype(np.float32) / 255.0
            edited = np.clip(
                edited.astype(np.float32) * (1.0 - alpha)
                + target.astype(np.float32) * alpha,
                0,
                255,
            ).astype(np.uint8)
        return edited

    async def _advanced_selection_erase_page(
        self,
        *,
        project_id: str,
        session: dict[str, Any],
        page_id: str,
        config: dict[str, Any],
        selections: Any,
    ) -> dict[str, Any]:
        source_path = self.get_page_source_image_path(project_id, session, page_id)
        page_cache_dir = self._session_page_cache_dir(session, project_id, page_id)
        if not self._ensure_page_base_image_cache(source_path, page_cache_dir):
            raise RuntimeError("无法准备当前页空页缓存。")
        backup_path = self._ensure_advanced_erase_traditional_backup(page_cache_dir)

        base_path = page_cache_dir / "inpainted.png"
        base_bgr = cv2.imread(str(base_path), cv2.IMREAD_COLOR)
        if base_bgr is None:
            raise RuntimeError(f"无法读取当前空页: {base_path}")
        base_rgb = cv2.cvtColor(base_bgr, cv2.COLOR_BGR2RGB)
        rects = self._normalize_selection_erase_rects(selections, base_rgb.shape)
        if not rects:
            raise ValueError("请至少框选一个要擦除的区域。")
        selection_mask = self._build_selection_erase_mask(rects, base_rgb.shape)
        selection_input_rgb = self._build_selection_erase_input_image(base_rgb, selection_mask)

        client = create_image_cleanup_client(
            mode="seedream-image",
            api_key=config["advanced_erase_api_key"],
            model=config["advanced_erase_model"],
            api_url=config["advanced_erase_base_url"],
            timeout_seconds=config["advanced_erase_timeout_seconds"],
        )
        print(
            "[DEBUG] Selection erase request "
            f"file={source_path.name} provider={config['advanced_erase_provider']} "
            f"model={config['advanced_erase_model']} selections={len(rects)} "
            f"size={base_rgb.shape[1]}x{base_rgb.shape[0]}"
        )

        attempt_id = self._advanced_erase_attempt_id()
        attempt_dir = self._advanced_erase_attempt_dir(page_cache_dir)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        input_path = attempt_dir / f"{attempt_id}.selection-input.png"
        seedream_output_path = attempt_dir / f"{attempt_id}.seedream.png"
        output_path = attempt_dir / f"{attempt_id}.png"
        selection_mask_path = attempt_dir / f"{attempt_id}.selection-mask.png"
        diff_mask_path = attempt_dir / f"{attempt_id}.selection-diff.png"
        text_mask_path = attempt_dir / f"{attempt_id}.selection-text-mask.png"
        precise_mask_path = attempt_dir / f"{attempt_id}.selection-precise-mask.png"
        residual_mask_path = attempt_dir / f"{attempt_id}.selection-residual-mask.png"
        metadata_path = attempt_dir / f"{attempt_id}.json"

        self._save_rgb_image_atomic(input_path, selection_input_rgb)
        cv2.imwrite(str(selection_mask_path), selection_mask)

        selection_prompt = self._normalize_advanced_erase_selection_prompt(
            config.get("advanced_erase_selection_prompt")
        )
        edited_rgb = await asyncio.wait_for(
            client.remove_text(selection_input_rgb, None, selection_prompt),
            timeout=int(config["advanced_erase_timeout_seconds"]) + 10,
        )
        edited_debug_rgb = self._normalize_advanced_erase_edited_image(base_rgb, edited_rgb)
        self._save_rgb_image_atomic(seedream_output_path, edited_debug_rgb)

        (
            composite_rgb,
            changed_ratio,
            precise_mask,
            model_change_mask,
            text_mask,
            residual_mask,
        ) = self._composite_selection_erase_result(
            base_rgb,
            edited_debug_rgb,
            selection_mask,
        )
        cv2.imwrite(str(diff_mask_path), model_change_mask)
        cv2.imwrite(str(text_mask_path), text_mask)
        cv2.imwrite(str(precise_mask_path), precise_mask)
        cv2.imwrite(str(residual_mask_path), residual_mask)
        self._save_rgb_image_atomic(output_path, composite_rgb)
        self._save_rgb_image_atomic(base_path, composite_rgb)

        metadata = {
            "attempt_id": attempt_id,
            "created_at": self._now_iso(),
            "page_id": page_id,
            "source_path": str(source_path),
            "base_path": str(base_path),
            "provider": config["advanced_erase_provider"],
            "api_url": config["advanced_erase_base_url"],
            "model": config["advanced_erase_model"],
            "mode": "selection",
            "prompt": selection_prompt,
            "selections": [
                {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
                for x1, y1, x2, y2 in rects
            ],
            "selection_count": len(rects),
            "selection_ratio": float(cv2.countNonZero(selection_mask)) / float(selection_mask.size or 1),
            "changed_ratio": changed_ratio,
            "traditional_backup": str(backup_path),
            "input_image": str(input_path),
            "seedream_output": str(seedream_output_path),
            "selection_mask": str(selection_mask_path),
            "diff_mask": str(diff_mask_path),
            "text_mask": str(text_mask_path),
            "precise_mask": str(precise_mask_path),
            "residual_mask": str(residual_mask_path),
            "precise_ratio": float(cv2.countNonZero(precise_mask)) / float(precise_mask.size or 1),
            "model_change_ratio": float(cv2.countNonZero(model_change_mask)) / float(model_change_mask.size or 1),
            "text_mask_ratio": float(cv2.countNonZero(text_mask)) / float(text_mask.size or 1),
            "residual_ratio": float(cv2.countNonZero(residual_mask)) / float(residual_mask.size or 1),
            "composited_output": str(output_path),
            "rejected": False,
        }
        self._write_json_file(metadata_path, metadata)

        self._record_advanced_erase_page_state(
            session,
            page_id,
            {
                "mode": "selection",
                "updated_at": self._now_iso(),
                "attempt_id": attempt_id,
                "provider": config["advanced_erase_provider"],
                "model": config["advanced_erase_model"],
                "changed_ratio": changed_ratio,
                "selection_count": len(rects),
            },
        )
        self.persist_project_state(
            project_id,
            session,
            snapshot_kind="advanced_erase_selection",
            snapshot_summary=f"选区擦除 {self._page_display_name(session, page_id)}",
            persist_page_documents=True,
            page_ids=[page_id],
        )

        return {
            **self.build_client_session_payload(project_id, session),
            "advanced_erase": {
                "action": "selection",
                "page_id": page_id,
                "attempt_id": attempt_id,
                "changed_ratio": changed_ratio,
                "selection_count": len(rects),
            },
        }

    async def _local_model_selection_erase_page(
        self,
        *,
        project_id: str,
        session: dict[str, Any],
        page_id: str,
        config: dict[str, Any],
        selections: Any,
        local_mask_mode: Any = None,
    ) -> dict[str, Any]:
        source_path = self.get_page_source_image_path(project_id, session, page_id)
        page_cache_dir = self._session_page_cache_dir(session, project_id, page_id)
        if not self._ensure_page_base_image_cache(source_path, page_cache_dir):
            raise RuntimeError("无法准备当前页空页缓存。")
        backup_path = self._ensure_advanced_erase_traditional_backup(page_cache_dir)

        base_path = page_cache_dir / "inpainted.png"
        base_bgr = cv2.imread(str(base_path), cv2.IMREAD_COLOR)
        if base_bgr is None:
            raise RuntimeError(f"无法读取当前空页: {base_path}")
        base_rgb = cv2.cvtColor(base_bgr, cv2.COLOR_BGR2RGB)
        rects = self._normalize_selection_erase_rects(selections, base_rgb.shape)
        if not rects:
            raise ValueError("请至少框选一个要擦除的区域。")
        selection_mask = self._build_selection_erase_mask(rects, base_rgb.shape)
        mask_mode = self._normalize_local_model_erase_mask_mode(local_mask_mode)
        erase_mask = selection_mask
        resolved_mask_mode = mask_mode
        if mask_mode == "text":
            text_mask = self._build_selection_erase_text_mask(base_rgb, selection_mask)
            if np.any(text_mask):
                erase_mask = text_mask
            else:
                resolved_mask_mode = "selection_fallback"

        attempt_id = self._advanced_erase_attempt_id()
        attempt_dir = self._advanced_erase_attempt_dir(page_cache_dir)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        input_path = attempt_dir / f"{attempt_id}.local-input.png"
        output_path = attempt_dir / f"{attempt_id}.local.png"
        selection_mask_path = attempt_dir / f"{attempt_id}.local-selection-mask.png"
        erase_mask_path = attempt_dir / f"{attempt_id}.local-erase-mask.png"
        model_output_path = attempt_dir / f"{attempt_id}.local-model.png"
        metadata_path = attempt_dir / f"{attempt_id}.json"

        self._save_rgb_image_atomic(input_path, base_rgb)
        cv2.imwrite(str(selection_mask_path), selection_mask)
        cv2.imwrite(str(erase_mask_path), erase_mask)
        device = self._select_local_inpainting_device(bool(config.get("use_gpu", True)))
        print(
            "[DEBUG] Local model erase request "
            f"file={source_path.name} model=lama_large device={device} selections={len(rects)} "
            f"mask_mode={resolved_mask_mode} size={base_rgb.shape[1]}x{base_rgb.shape[0]}"
        )

        try:
            model_rgb = await self._run_local_lama_inpaint(
                base_rgb,
                erase_mask,
                device=device,
            )
        except Exception as exc:
            model_path = self.model_dir / "inpainting" / "lama_large_512px.ckpt"
            self._write_json_file(metadata_path, {
                "attempt_id": attempt_id,
                "created_at": self._now_iso(),
                "page_id": page_id,
                "source_path": str(source_path),
                "base_path": str(base_path),
                "mode": "local_selection",
                "model": "lama_large",
                "device": device,
                "mask_mode": mask_mode,
                "resolved_mask_mode": resolved_mask_mode,
                "selection_count": len(rects),
                "selection_mask": str(selection_mask_path),
                "erase_mask": str(erase_mask_path),
                "traditional_backup": str(backup_path),
                "rejected": True,
                "error": str(exc),
            })
            raise RuntimeError(
                "本地模型擦除失败。首次使用会自动下载 LaMa 模型；"
                "如果下载很慢，也可以手动下载模型文件后放到 "
                f"{model_path}。详情：{exc}"
            ) from exc

        model_rgb = self._normalize_advanced_erase_edited_image(base_rgb, model_rgb)
        self._save_rgb_image_atomic(model_output_path, model_rgb)
        composite_rgb = self._composite_local_model_erase_result(base_rgb, model_rgb, erase_mask)
        changed_ratio = float(cv2.countNonZero(erase_mask)) / float(erase_mask.size or 1)
        self._save_rgb_image_atomic(output_path, composite_rgb)
        self._save_rgb_image_atomic(base_path, composite_rgb)

        metadata = {
            "attempt_id": attempt_id,
            "created_at": self._now_iso(),
            "page_id": page_id,
            "source_path": str(source_path),
            "base_path": str(base_path),
            "mode": "local_selection",
            "model": "lama_large",
            "device": device,
            "mask_mode": mask_mode,
            "resolved_mask_mode": resolved_mask_mode,
            "selections": [
                {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
                for x1, y1, x2, y2 in rects
            ],
            "selection_count": len(rects),
            "selection_ratio": float(cv2.countNonZero(selection_mask)) / float(selection_mask.size or 1),
            "erase_ratio": changed_ratio,
            "changed_ratio": changed_ratio,
            "traditional_backup": str(backup_path),
            "input_image": str(input_path),
            "selection_mask": str(selection_mask_path),
            "erase_mask": str(erase_mask_path),
            "model_output": str(model_output_path),
            "composited_output": str(output_path),
            "rejected": False,
        }
        self._write_json_file(metadata_path, metadata)

        self._record_advanced_erase_page_state(
            session,
            page_id,
            {
                "mode": "local_selection",
                "updated_at": self._now_iso(),
                "attempt_id": attempt_id,
                "provider": "local",
                "model": "lama_large",
                "device": device,
                "mask_mode": resolved_mask_mode,
                "changed_ratio": changed_ratio,
                "selection_count": len(rects),
            },
        )
        self.persist_project_state(
            project_id,
            session,
            snapshot_kind="local_model_erase_selection",
            snapshot_summary=f"本地模型擦除 {self._page_display_name(session, page_id)}",
            persist_page_documents=True,
            page_ids=[page_id],
        )

        return {
            **self.build_client_session_payload(project_id, session),
            "advanced_erase": {
                "action": "local-selection",
                "page_id": page_id,
                "attempt_id": attempt_id,
                "changed_ratio": changed_ratio,
                "selection_count": len(rects),
                "model": "lama_large",
                "device": device,
                "mask_mode": resolved_mask_mode,
            },
        }

    def _restore_traditional_erase_page(
        self,
        project_id: str,
        session: dict[str, Any],
        page_id: str,
    ) -> dict[str, Any]:
        source_path = self.get_page_source_image_path(project_id, session, page_id)
        page_cache_dir = self._session_page_cache_dir(session, project_id, page_id)
        if not self._ensure_page_base_image_cache(source_path, page_cache_dir):
            raise RuntimeError("无法准备当前页空页缓存。")

        backup_path = self._advanced_erase_traditional_backup_path(page_cache_dir)
        if not backup_path.exists():
            raise FileNotFoundError("当前页没有可恢复的传统空页备份。请先重新识别，或重新上传无字图。")

        inpainted_path = page_cache_dir / "inpainted.png"
        shutil.copy2(backup_path, inpainted_path)
        self._record_advanced_erase_page_state(
            session,
            page_id,
            {
                "mode": "traditional",
                "updated_at": self._now_iso(),
            },
        )
        self.persist_project_state(
            project_id,
            session,
            snapshot_kind="advanced_erase_restore",
            snapshot_summary=f"恢复传统空页 {self._page_display_name(session, page_id)}",
            persist_page_documents=True,
            page_ids=[page_id],
        )
        return {
            "action": "restore",
            "page_id": page_id,
        }

    def _ensure_advanced_erase_traditional_backup(self, page_cache_dir: Path) -> Path:
        inpainted_path = page_cache_dir / "inpainted.png"
        backup_path = self._advanced_erase_traditional_backup_path(page_cache_dir)
        if not backup_path.exists():
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(inpainted_path, backup_path)
        return backup_path

    def _advanced_erase_traditional_backup_path(self, page_cache_dir: Path) -> Path:
        return self._advanced_erase_attempt_dir(page_cache_dir) / "traditional_inpainted.png"

    def _advanced_erase_attempt_dir(self, page_cache_dir: Path) -> Path:
        return page_cache_dir / "advanced_erase"

    def _advanced_erase_attempt_id(self) -> str:
        timestamp = self._now_iso().replace(":", "").replace("-", "").replace("+", "z")
        return f"{timestamp}_{uuid.uuid4().hex[:8]}"

    def _record_advanced_erase_page_state(
        self,
        session: dict[str, Any],
        page_id: str,
        payload: dict[str, Any],
    ) -> None:
        pages = dict(session.get("advanced_erase_pages") or {})
        pages[page_id] = dict(payload)
        session["advanced_erase_pages"] = pages

    def _save_rgb_image_atomic(self, output_path: Path, image_rgb: np.ndarray) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=f"{output_path.stem}_", suffix=output_path.suffix, dir=str(output_path.parent))
        os.close(fd)
        try:
            image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
            if not cv2.imwrite(temp_path, image_bgr):
                raise RuntimeError(f"无法写入图片: {output_path}")
            os.replace(temp_path, output_path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(temp_path)

    def capture_session_config(self, session: dict[str, Any], raw_config: dict[str, Any] | None) -> dict[str, Any]:
        config = self._normalize_config(raw_config)
        session["last_config"] = config
        session["style_region_overrides"] = dict(config.get("style_region_overrides") or {})
        session["translation_region_overrides"] = dict(config.get("translation_region_overrides") or {})
        session["translation_region_skip_overrides"] = dict(config.get("translation_region_skip_overrides") or {})
        session["translation_region_disabled_overrides"] = dict(config.get("translation_region_disabled_overrides") or {})
        session["translation_region_layout_overrides"] = dict(config.get("translation_region_layout_overrides") or {})
        return config

    def capture_page_command_config(
        self,
        session: dict[str, Any],
        raw_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        config = self._normalize_config(raw_config)
        config["style_region_overrides"] = dict(session.get("style_region_overrides") or {})
        config["translation_region_overrides"] = dict(session.get("translation_region_overrides") or {})
        config["translation_region_skip_overrides"] = dict(session.get("translation_region_skip_overrides") or {})
        config["translation_region_disabled_overrides"] = dict(session.get("translation_region_disabled_overrides") or {})
        config["translation_region_layout_overrides"] = dict(session.get("translation_region_layout_overrides") or {})
        session["last_config"] = config
        return config

    def persist_project_state(
        self,
        project_id: str,
        session: dict[str, Any],
        snapshot_kind: str | None = None,
        snapshot_summary: str = "",
        persist_page_documents: bool = False,
        page_ids: list[str] | None = None,
    ) -> None:
        project_dir = self._project_dir(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        self._project_snapshots_dir(project_id).mkdir(parents=True, exist_ok=True)

        latest_snapshot = None
        if snapshot_kind:
            latest_snapshot = self._create_project_snapshot(project_id, session, snapshot_kind, snapshot_summary)

        session_state = self._serialize_session_state(project_id, session)
        self._write_json_file(self._project_session_state_path(project_id), session_state)

        project_summary = self._build_project_summary(project_id, session, latest_snapshot=latest_snapshot)
        project_manifest = {
            **project_summary,
            "source_dir": str(session.get("source_dir") or ""),
            "translated_dir": str(session.get("translated_dir") or ""),
        }
        self._write_json_file(self._project_manifest_path(project_id), project_manifest)
        self._refresh_project_index_entry(project_summary)

        if persist_page_documents:
            self._persist_page_documents(project_id, session, page_ids=page_ids)

        if snapshot_kind:
            self._enforce_snapshot_retention(project_id, session)
            refreshed_summary = self._build_project_summary(project_id, session)
            self._write_json_file(self._project_manifest_path(project_id), {**project_manifest, **refreshed_summary})
            self._refresh_project_index_entry(refreshed_summary)

    def list_projects(self) -> list[dict[str, Any]]:
        index_items = self._read_json_file(self.project_index_path, [])
        if isinstance(index_items, list) and index_items:
            valid_items = [item for item in index_items if isinstance(item, dict)]
            for item in valid_items:
                project_id = str(item.get("project_id") or "")
                item["is_busy"] = self.is_session_busy(project_id)
                item["busy_action"] = self.get_session_busy_action(project_id)
            valid_items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            return valid_items

        items: list[dict[str, Any]] = []
        for project_dir in sorted(self.projects_root.iterdir()) if self.projects_root.exists() else []:
            manifest_path = project_dir / "project.json"
            payload = self._read_json_file(manifest_path, {})
            if isinstance(payload, dict) and payload:
                project_id = str(payload.get("project_id") or "")
                payload["is_busy"] = self.is_session_busy(project_id)
                payload["busy_action"] = self.get_session_busy_action(project_id)
                items.append(payload)
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        if items:
            self._write_project_index(items)
        return items

    def try_mark_session_busy(self, project_id: str, action: str) -> bool:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return False
        normalized_action = str(action or "translate").strip().lower() or "translate"
        with self.active_sessions_lock:
            if normalized_project_id in self.active_sessions:
                return False
            self.active_sessions[normalized_project_id] = normalized_action
        return True

    def mark_session_busy(self, project_id: str, action: str) -> None:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return
        normalized_action = str(action or "translate").strip().lower() or "translate"
        with self.active_sessions_lock:
            self.active_sessions[normalized_project_id] = normalized_action

    def clear_session_busy(self, project_id: str) -> None:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return
        with self.active_sessions_lock:
            self.active_sessions.pop(normalized_project_id, None)

    def is_session_busy(self, project_id: str) -> bool:
        normalized_project_id = str(project_id or "").strip()
        with self.active_sessions_lock:
            return normalized_project_id in self.active_sessions

    def get_session_busy_action(self, project_id: str) -> str:
        normalized_project_id = str(project_id or "").strip()
        with self.active_sessions_lock:
            return self.active_sessions.get(normalized_project_id, "")

    def list_project_snapshots(self, project_id: str) -> list[dict[str, Any]]:
        snapshots = []
        for snapshot in self._read_snapshot_manifests(project_id):
            snapshots.append(
                {
                    "snapshot_id": str(snapshot.get("snapshot_id") or ""),
                    "project_id": project_id,
                    "created_at": str(snapshot.get("created_at") or ""),
                    "kind": str(snapshot.get("kind") or ""),
                    "summary": str(snapshot.get("summary") or ""),
                    "workflow_stage": str(snapshot.get("workflow_stage") or "idle"),
                    "cover_image": str(snapshot.get("cover_image") or ""),
                    "pinned": bool(snapshot.get("pinned")),
                }
            )
        return snapshots

    def set_snapshot_pinned(self, project_id: str, snapshot_id: str, pinned: bool) -> list[dict[str, Any]]:
        manifests = self._read_snapshot_manifests(project_id)
        target_snapshot = next(
            (item for item in manifests if str(item.get("snapshot_id") or "") == snapshot_id),
            None,
        )
        if not target_snapshot:
            raise FileNotFoundError("目标快照不存在，请刷新后重试。")

        if pinned:
            pinned_count = sum(1 for item in manifests if bool(item.get("pinned")))
            if not bool(target_snapshot.get("pinned")) and pinned_count >= 10:
                raise ValueError("固定快照最多保留 10 个，请先取消固定旧快照。")

        target_snapshot["pinned"] = bool(pinned)
        target_path = Path(str(target_snapshot.get("_path") or ""))
        payload = {key: value for key, value in target_snapshot.items() if key != "_path"}
        self._write_json_file(target_path, payload)

        try:
            session = self.restore_project_session(project_id)
            self._enforce_snapshot_retention(project_id, session)
            self._refresh_project_index_entry(self._build_project_summary(project_id, session))
        except FileNotFoundError:
            pass
        return self.list_project_snapshots(project_id)

    def restore_project_session(self, project_id: str) -> dict[str, Any]:
        state = self._read_json_file(self._project_session_state_path(project_id), {})
        manifest = self._read_json_file(self._project_manifest_path(project_id), {})
        restored_from_manifest = False
        if not isinstance(state, dict) or not state:
            recovered_session = self._recover_session_from_manifest(project_id, manifest)
            if not recovered_session:
                raise FileNotFoundError("项目状态不存在，请重新上传。")
            state = self._serialize_session_state(project_id, recovered_session)
            restored_from_manifest = True

        session = {
            "source_dir": str(state.get("source_dir") or ""),
            "translated_dir": str(state.get("translated_dir") or ""),
            "source_images": list(state.get("source_images") or []),
            "download_path": str(state.get("download_path") or ""),
            "translated_output_map": dict(state.get("translated_output_map") or {}),
            "rerender_generation": int(state.get("rerender_generation") or 0),
            "manual_regions": dict(state.get("manual_regions") or {}),
            "advanced_erase_pages": dict(state.get("advanced_erase_pages") or {}),
            "project_glossary": self._normalize_project_glossary(state.get("project_glossary")),
            "workflow_stage": str(state.get("workflow_stage") or "idle"),
            "mask_debug_dir": str(state.get("mask_debug_dir") or ""),
            "rerender_cache_dir": str(state.get("rerender_cache_dir") or ""),
            "last_config": dict(state.get("last_config") or {}),
            "deferred_output_names": set(state.get("deferred_output_names") or []),
            "translation_region_overrides": dict(state.get("translation_region_overrides") or {}),
            "translation_region_skip_overrides": dict(state.get("translation_region_skip_overrides") or {}),
            "translation_region_disabled_overrides": dict(state.get("translation_region_disabled_overrides") or {}),
            "translation_region_layout_overrides": dict(state.get("translation_region_layout_overrides") or {}),
            "style_region_overrides": dict(state.get("style_region_overrides") or {}),
            "project_id": project_id,
            "project_title": str(state.get("project_title") or project_id),
            "project_note": str(state.get("project_note") or ""),
            "review_mode": self._normalize_review_mode(state.get("review_mode")),
            "project_created_at": str(state.get("project_created_at") or self._now_iso()),
            "project_updated_at": str(state.get("project_updated_at") or self._now_iso()),
        }
        manifest_dict = manifest if isinstance(manifest, dict) else {}
        if not session["source_dir"]:
            session["source_dir"] = str(manifest_dict.get("source_dir") or "")
        if not session["translated_dir"]:
            session["translated_dir"] = str(manifest_dict.get("translated_dir") or "")
        source_dir_path = Path(session["source_dir"]) if session["source_dir"] else Path()
        translated_dir_path = Path(session["translated_dir"]) if session["translated_dir"] else Path()
        canonical_source_dir = self._project_source_dir(project_id)
        canonical_translated_dir = self._project_translated_dir(project_id)
        if (not session["source_dir"] or not source_dir_path.exists()) and canonical_source_dir.exists():
            session["source_dir"] = str(canonical_source_dir)
            source_dir_path = canonical_source_dir
            restored_from_manifest = True
        if (not session["translated_dir"] or not translated_dir_path.exists()) and canonical_translated_dir.exists():
            session["translated_dir"] = str(canonical_translated_dir)
            translated_dir_path = canonical_translated_dir
            restored_from_manifest = True
        if not session["project_title"]:
            session["project_title"] = str(manifest_dict.get("title") or manifest_dict.get("project_title") or project_id)
        if not session["project_note"]:
            session["project_note"] = str(manifest_dict.get("note") or manifest_dict.get("project_note") or "")
        inferred_source_images = self._infer_source_images_from_dir(source_dir_path)
        if self._source_images_need_recovery(source_dir_path, list(session.get("source_images") or []), inferred_source_images):
            session["source_images"] = self._merge_recovered_source_images(
                list(session.get("source_images") or []),
                inferred_source_images,
            )
            restored_from_manifest = True
        if self._translated_output_map_needs_recovery(
            translated_dir_path,
            list(session.get("source_images") or []),
            dict(session.get("translated_output_map") or {}),
        ):
            session["translated_output_map"] = self._infer_translated_output_map(
                translated_dir_path,
                session["source_images"],
                self._normalize_rerender_output_format((session.get("last_config") or {}).get("rerender_output_format")),
            )
            restored_from_manifest = True
        recovered_source_images, recovered_output_map, recovery_changed = self._validate_restored_page_assets(
            project_id,
            source_dir_path,
            translated_dir_path,
            list(session.get("source_images") or []),
            dict(session.get("translated_output_map") or {}),
            self._normalize_rerender_output_format((session.get("last_config") or {}).get("rerender_output_format")),
        )
        if recovery_changed:
            session["source_images"] = recovered_source_images
            session["translated_output_map"] = recovered_output_map
            restored_from_manifest = True
        if not session.get("source_images"):
            raise FileNotFoundError("项目原图文件缺失，无法恢复。请确认项目数据目录仍然完整。")
        inferred_workflow_stage = self._infer_restored_workflow_stage(
            project_id,
            session,
            manifest_dict,
            translated_dir_path,
            self._normalize_rerender_output_format((session.get("last_config") or {}).get("rerender_output_format")),
        )
        if session.get("workflow_stage") != inferred_workflow_stage:
            session["workflow_stage"] = inferred_workflow_stage
            restored_from_manifest = True
        if restored_from_manifest:
            self.persist_project_state(project_id, session, persist_page_documents=False)
        return session

    def _validate_restored_page_assets(
        self,
        project_id: str,
        source_dir: Path,
        translated_dir: Path,
        source_images: list[dict[str, Any]],
        translated_output_map: dict[str, Any],
        preferred_format: str,
    ) -> tuple[list[dict[str, Any]], dict[str, str], bool]:
        canonical_source_dir = self._project_source_dir(project_id)
        canonical_translated_dir = self._project_translated_dir(project_id)
        changed = False
        valid_source_images: list[dict[str, Any]] = []
        seen_source_names: set[str] = set()

        for image in source_images:
            if not isinstance(image, dict):
                changed = True
                continue
            stored_name = str(image.get("stored_name") or image.get("name") or "").strip()
            if not stored_name or stored_name in seen_source_names:
                changed = True
                continue
            if not (source_dir / stored_name).exists() and not (canonical_source_dir / stored_name).exists():
                changed = True
                continue
            seen_source_names.add(stored_name)
            valid_source_images.append({
                "name": str(image.get("name") or stored_name),
                "stored_name": stored_name,
            })

        if not valid_source_images:
            inferred = self._infer_source_images_from_dir(source_dir)
            if not inferred and canonical_source_dir != source_dir:
                inferred = self._infer_source_images_from_dir(canonical_source_dir)
            if inferred:
                valid_source_images = inferred
                changed = True

        valid_source_names = {str(image.get("stored_name") or "") for image in valid_source_images}
        valid_output_map: dict[str, str] = {}
        for stored_name, output_name in translated_output_map.items():
            normalized_stored_name = str(stored_name or "").strip()
            normalized_output_name = str(output_name or "").strip()
            if not normalized_stored_name or normalized_stored_name not in valid_source_names or not normalized_output_name:
                changed = True
                continue
            if (translated_dir / normalized_output_name).exists() or (canonical_translated_dir / normalized_output_name).exists():
                valid_output_map[normalized_stored_name] = normalized_output_name
            else:
                changed = True

        if valid_source_images and not valid_output_map and (translated_dir.exists() or canonical_translated_dir.exists()):
            recovered = self._infer_translated_output_map(translated_dir, valid_source_images, preferred_format)
            if not recovered and canonical_translated_dir != translated_dir:
                recovered = self._infer_translated_output_map(canonical_translated_dir, valid_source_images, preferred_format)
            if recovered:
                valid_output_map = recovered
                changed = True

        return valid_source_images, valid_output_map, changed

    def restore_snapshot_as_project(self, project_id: str, snapshot_id: str) -> tuple[str, dict[str, Any]]:
        source_session = self.restore_project_session(project_id)
        snapshot = next(
            (
                item
                for item in self._read_snapshot_manifests(project_id)
                if str(item.get("snapshot_id") or "") == snapshot_id
            ),
            None,
        )
        if not snapshot:
            raise FileNotFoundError("目标快照不存在，请刷新后重试。")

        source_output_dir = Path(source_session.get("translated_dir") or "")
        source_source_dir = Path(source_session.get("source_dir") or "")
        source_cache_dir = self._rerender_cache_dir(project_id)

        new_project_id = str(uuid.uuid4())
        new_output_root = self.output_root / new_project_id
        new_source_dir = new_output_root / "source"
        new_translated_dir = new_output_root / "translated"
        new_output_root.mkdir(parents=True, exist_ok=True)
        new_source_dir.mkdir(parents=True, exist_ok=True)
        new_translated_dir.mkdir(parents=True, exist_ok=True)

        if source_source_dir.exists():
            for source_file in source_source_dir.iterdir():
                if source_file.is_file():
                    shutil.copy2(source_file, new_source_dir / source_file.name)

        translated_output_map = dict(snapshot.get("translated_output_map") or {})
        copied_output_map: dict[str, str] = {}
        for stored_name, output_name in translated_output_map.items():
            source_file = source_output_dir / str(output_name)
            if not source_file.exists():
                continue
            target_file = new_translated_dir / source_file.name
            shutil.copy2(source_file, target_file)
            copied_output_map[str(stored_name)] = target_file.name

        new_cache_dir = self._rerender_cache_dir(new_project_id)
        if source_cache_dir.exists():
            if new_cache_dir.exists():
                shutil.rmtree(new_cache_dir)
            shutil.copytree(source_cache_dir, new_cache_dir)

        source_title = str(source_session.get("project_title") or project_id).strip() or project_id
        new_session = {
            **source_session,
            "source_dir": str(new_source_dir),
            "translated_dir": str(new_translated_dir),
            "download_path": "",
            "translated_output_map": copied_output_map,
            "manual_regions": dict(snapshot.get("manual_regions") or source_session.get("manual_regions") or {}),
            "advanced_erase_pages": dict(snapshot.get("advanced_erase_pages") or source_session.get("advanced_erase_pages") or {}),
            "project_glossary": self._normalize_project_glossary(snapshot.get("project_glossary") or source_session.get("project_glossary")),
            "workflow_stage": str(snapshot.get("workflow_stage") or source_session.get("workflow_stage") or "idle"),
            "mask_debug_dir": "",
            "rerender_cache_dir": str(new_cache_dir),
            "last_config": dict(snapshot.get("last_config") or source_session.get("last_config") or {}),
            "deferred_output_names": set(),
            "translation_region_overrides": dict(snapshot.get("translation_region_overrides") or {}),
            "translation_region_skip_overrides": dict(snapshot.get("translation_region_skip_overrides") or {}),
            "translation_region_disabled_overrides": dict(snapshot.get("translation_region_disabled_overrides") or {}),
            "translation_region_layout_overrides": dict(snapshot.get("translation_region_layout_overrides") or {}),
            "style_region_overrides": dict(snapshot.get("style_region_overrides") or {}),
            "project_id": new_project_id,
            "project_title": f"{source_title}（快照恢复）",
            "project_note": str(source_session.get("project_note") or ""),
            "review_mode": self._normalize_review_mode(snapshot.get("review_mode") or source_session.get("review_mode")),
            "project_created_at": self._now_iso(),
            "project_updated_at": self._now_iso(),
        }
        self.persist_project_state(
            new_project_id,
            new_session,
            snapshot_kind="snapshot_restored",
            snapshot_summary=f"从快照 {snapshot_id} 恢复继续编辑",
            persist_page_documents=True,
        )
        return new_project_id, new_session

    def delete_project(self, project_id: str) -> None:
        if self.is_session_busy(project_id):
            raise RuntimeError("该项目仍有任务在运行，请等待识别/翻译完成后再删除。")
        project_dir = self._project_dir(project_id)
        output_dir = self.output_root / project_id
        rerender_cache_dir = self._rerender_cache_dir(project_id)
        mask_debug_dir = self._mask_debug_dir(project_id)
        style_debug_dir = self._style_rerender_debug_dir(project_id)
        image_preview_cache_dir = self._image_preview_project_cache_dir(project_id)

        for path in (project_dir, output_dir, rerender_cache_dir, mask_debug_dir, style_debug_dir, image_preview_cache_dir):
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    with contextlib.suppress(OSError):
                        path.unlink()

        for extra_path in self.temp_dir.glob(f"{project_id}_*"):
            if extra_path in {project_dir, mask_debug_dir, style_debug_dir}:
                continue
            if extra_path.is_dir():
                shutil.rmtree(extra_path, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    extra_path.unlink()

        existing = self._read_json_file(self.project_index_path, [])
        next_items = [
            item for item in existing
            if isinstance(item, dict) and str(item.get("project_id") or "") != project_id
        ]
        self._write_project_index(next_items)

    def update_project_metadata(
        self,
        project_id: str,
        session: dict[str, Any],
        title: str | None = None,
        note: str | None = None,
        review_mode: str | None = None,
    ) -> dict[str, Any]:
        if title is not None:
            session["project_title"] = title.strip() or str(session.get("project_title") or project_id)
        if note is not None:
            session["project_note"] = note.strip()
        if review_mode is not None:
            session["review_mode"] = self._normalize_review_mode(review_mode)
        self.persist_project_state(
            project_id,
            session,
            persist_page_documents=review_mode is not None,
        )
        return self._build_project_summary(project_id, session)

    def build_client_session_payload(self, project_id: str, session: dict[str, Any]) -> dict[str, Any]:
        preferred_output_format = self._normalize_rerender_output_format(
            (session.get("last_config") or {}).get("rerender_output_format")
        )
        source_images = [
            {
                "name": str(image.get("name") or image.get("stored_name") or ""),
                "stored_name": str(image.get("stored_name") or ""),
                "url": f"/api/pages/{project_id}/{str(image.get('stored_name') or '')}/source-image",
            }
            for image in (session.get("source_images") or [])
            if str(image.get("stored_name") or "")
        ]
        translated_images: list[dict[str, Any]] = []
        output_dir = Path(session["translated_dir"])
        for index, image in enumerate(source_images):
            stored_name = str(image.get("stored_name") or "")
            current_output = self._current_translated_output(session, output_dir, stored_name, preferred_output_format)
            if current_output is None:
                continue
            translated_images.append(
                {
                    "id": f"{project_id}-translated-{stored_name or index}",
                    "name": str(image.get("name") or stored_name),
                    "url": f"/api/pages/{project_id}/{stored_name}/translated-image",
                    "stored_name": stored_name,
                }
            )

        return {
            "session_id": project_id,
            "review_mode": self._session_review_mode(session),
            "total_images": len(source_images),
            "images": source_images,
            "translated_images": translated_images,
            "workflow_stage": str(session.get("workflow_stage") or "idle"),
            "download_url": f"/api/download/{project_id}" if translated_images else "",
            "download_path": str(session.get("download_path") or ""),
            "translated_dir": str(Path(session.get("translated_dir") or "").resolve()) if session.get("translated_dir") else "",
            "mask_debug_dir": str(Path(session.get("mask_debug_dir") or "").resolve()) if session.get("mask_debug_dir") else "",
            "project": self._build_project_summary(project_id, session),
            "glossary": self.get_project_glossary(project_id, session),
            "config": self._sanitize_config_for_storage(session.get("last_config") or {}),
            "overrides": {
                "translation_region_overrides": dict(session.get("translation_region_overrides") or {}),
                "translation_region_skip_overrides": dict(session.get("translation_region_skip_overrides") or {}),
                "translation_region_disabled_overrides": dict(session.get("translation_region_disabled_overrides") or {}),
                "translation_region_layout_overrides": dict(session.get("translation_region_layout_overrides") or {}),
                "style_region_overrides": dict(session.get("style_region_overrides") or {}),
            },
        }

    def _normalize_glossary_category(self, raw_value: Any) -> str:
        value = str(raw_value or "").strip()
        normalized = value.lower().replace(" ", "").replace("-", "_")
        aliases = {
            "人物": "人名",
            "角色": "人名",
            "角色名": "人名",
            "姓名": "人名",
            "person": "人名",
            "people": "人名",
            "name": "人名",
            "character": "人名",
            "character_name": "人名",
            "组织": "组织/团体",
            "团体": "组织/团体",
            "机构": "组织/团体",
            "organization": "组织/团体",
            "organisation": "组织/团体",
            "org": "组织/团体",
            "地名": "地点",
            "地点名": "地点",
            "place": "地点",
            "location": "地点",
            "作品": "作品/道具/技能",
            "道具": "作品/道具/技能",
            "技能": "作品/道具/技能",
            "物品": "作品/道具/技能",
            "title": "作品/道具/技能",
            "work": "作品/道具/技能",
            "item": "作品/道具/技能",
            "skill": "作品/道具/技能",
            "术语": "行业术语",
            "行业词": "行业术语",
            "专业术语": "行业术语",
            "term": "行业术语",
            "domain_term": "行业术语",
            "industry_term": "行业术语",
            "misc": "其他",
            "other": "其他",
        }
        value = aliases.get(normalized, value)
        return value if value in self.PROJECT_GLOSSARY_CATEGORIES else "其他"

    def _first_glossary_entry_value(
        self,
        raw_entry: dict[str, Any],
        keys: tuple[str, ...],
        fallback: Any = "",
    ) -> str:
        for key in keys:
            if key not in raw_entry:
                continue
            value = raw_entry.get(key)
            if isinstance(value, (dict, list, tuple, set)):
                continue
            text = str(value or "").strip()
            if text:
                return text
        return str(fallback or "").strip()

    def _normalize_project_glossary_entry(self, raw_entry: Any, existing_entry: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not isinstance(raw_entry, dict):
            return None
        existing_entry = existing_entry or {}
        source = self._first_glossary_entry_value(
            raw_entry,
            (
                "source",
                "term",
                "original",
                "original_text",
                "source_text",
                "raw",
                "name",
                "原文",
                "源文",
                "词条",
                "术语",
                "名词",
                "名称",
                "日文",
                "原名",
            ),
            existing_entry.get("source"),
        )
        translation = self._first_glossary_entry_value(
            raw_entry,
            (
                "translation",
                "target",
                "translated",
                "target_text",
                "localized",
                "value",
                "译文",
                "翻译",
                "建议译文",
                "译名",
                "中文",
                "目标译文",
            ),
            existing_entry.get("translation"),
        )
        if not source or not translation:
            return None

        now = self._now_iso()
        entry_id = str(raw_entry.get("id") or existing_entry.get("id") or "").strip()
        if not entry_id:
            entry_id = f"term_{uuid.uuid4().hex[:12]}"
        source_kind = str(raw_entry.get("source_kind") or raw_entry.get("kind") or existing_entry.get("source_kind") or "user").strip()
        if source_kind not in {"system", "user"}:
            source_kind = "user"

        created_at = str(raw_entry.get("created_at") or existing_entry.get("created_at") or now)
        if "replacement" in raw_entry:
            replacement_raw = raw_entry.get("replacement")
        elif "replace_text" in raw_entry:
            replacement_raw = raw_entry.get("replace_text")
        elif "替换源" in raw_entry:
            replacement_raw = raw_entry.get("替换源")
        else:
            replacement_raw = existing_entry.get("replacement")
        note_raw = self._first_glossary_entry_value(
            raw_entry,
            ("note", "notes", "description", "reason", "备注", "说明", "注释"),
            existing_entry.get("note"),
        )
        category_raw = self._first_glossary_entry_value(
            raw_entry,
            ("category", "type", "kind", "分类", "类别", "类型"),
            existing_entry.get("category"),
        )
        return {
            "id": entry_id,
            "source": source,
            "translation": translation,
            "category": self._normalize_glossary_category(category_raw),
            "replacement": str(replacement_raw or "").strip(),
            "note": str(note_raw or "").strip(),
            "source_kind": source_kind,
            "created_at": created_at,
            "updated_at": now,
        }

    def _normalize_project_glossary(self, raw_value: Any) -> dict[str, Any]:
        raw_entries: list[Any]
        if isinstance(raw_value, dict):
            raw_entries = list(raw_value.get("entries") or [])
        elif isinstance(raw_value, list):
            raw_entries = raw_value
        else:
            raw_entries = []

        entries: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        seen_ids: set[str] = set()
        for raw_entry in raw_entries:
            entry = self._normalize_project_glossary_entry(raw_entry)
            if not entry:
                continue
            source_key = entry["source"]
            if source_key in seen_sources:
                continue
            while entry["id"] in seen_ids:
                entry["id"] = f"term_{uuid.uuid4().hex[:12]}"
            seen_sources.add(source_key)
            seen_ids.add(entry["id"])
            entries.append(entry)

        return {
            "version": self.PROJECT_GLOSSARY_VERSION,
            "entries": entries,
            "updated_at": str((raw_value or {}).get("updated_at") or self._now_iso()) if isinstance(raw_value, dict) else self._now_iso(),
            "auto_extract_completed": bool((raw_value or {}).get("auto_extract_completed")) if isinstance(raw_value, dict) else False,
            "auto_extracted_at": str((raw_value or {}).get("auto_extracted_at") or "") if isinstance(raw_value, dict) else "",
        }

    def _project_glossary_auto_extract_completed(self, session: dict[str, Any]) -> bool:
        glossary = self._normalize_project_glossary(session.get("project_glossary"))
        session["project_glossary"] = glossary
        return bool(glossary.get("auto_extract_completed"))

    def _mark_project_glossary_auto_extract_completed(
        self,
        project_id: str,
        session: dict[str, Any],
        *,
        persist: bool,
    ) -> None:
        glossary = self._normalize_project_glossary(session.get("project_glossary"))
        glossary["auto_extract_completed"] = True
        glossary["auto_extracted_at"] = self._now_iso()
        glossary["updated_at"] = glossary["auto_extracted_at"]
        session["project_glossary"] = glossary
        if persist:
            self.persist_project_state(project_id, session, persist_page_documents=False)

    def _project_glossary_occurrences(
        self,
        project_id: str,
        session: dict[str, Any],
        entries: list[dict[str, Any]],
        *,
        occurrence_limit_per_entry: int = 8,
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
        occurrences: dict[str, list[dict[str, Any]]] = {str(entry.get("id") or ""): [] for entry in entries}
        occurrence_counts: dict[str, int] = {str(entry.get("id") or ""): 0 for entry in entries}
        terms = [
            (str(entry.get("id") or ""), str(entry.get("source") or ""))
            for entry in sorted(entries, key=lambda item: len(str(item.get("source") or "")), reverse=True)
            if str(entry.get("id") or "") and str(entry.get("source") or "")
        ]
        if not terms:
            return occurrences, occurrence_counts

        for segment in self._iter_project_text_segments(project_id, session):
            source_text = str(segment.get("source_text") or "")
            if not source_text:
                continue
            for entry_id, source in terms:
                if source not in source_text:
                    continue
                occurrence_counts[entry_id] = occurrence_counts.get(entry_id, 0) + 1
                entry_occurrences = occurrences.setdefault(entry_id, [])
                if len(entry_occurrences) < occurrence_limit_per_entry:
                    entry_occurrences.append({
                        "page_id": str(segment.get("page_id") or ""),
                        "page_name": str(segment.get("page_name") or ""),
                        "region_id": str(segment.get("region_id") or ""),
                        "source_text": source_text,
                        "translation": str(segment.get("translation") or ""),
                    })

        return occurrences, occurrence_counts

    def get_project_glossary(
        self,
        project_id: str,
        session: dict[str, Any],
        *,
        include_occurrences: bool = False,
    ) -> dict[str, Any]:
        glossary = self._normalize_project_glossary(session.get("project_glossary"))
        session["project_glossary"] = glossary
        entries = list(glossary.get("entries") or [])
        occurrences: dict[str, list[dict[str, Any]]] = {}
        occurrence_counts: dict[str, int] = {}
        if include_occurrences:
            occurrences, occurrence_counts = self._project_glossary_occurrences(project_id, session, entries)
        return {
            **glossary,
            "occurrences_loaded": include_occurrences,
            "entries": [
                {
                    **entry,
                    "occurrence_count": (
                        occurrence_counts.get(str(entry.get("id") or ""), 0)
                        if include_occurrences
                        else None
                    ),
                    "occurrences": occurrences.get(str(entry.get("id") or ""), []) if include_occurrences else [],
                }
                for entry in entries
            ],
        }

    def save_project_glossary(
        self,
        project_id: str,
        session: dict[str, Any],
        raw_entries: list[Any],
        *,
        persist: bool = True,
    ) -> dict[str, Any]:
        existing_glossary = self._normalize_project_glossary(session.get("project_glossary"))
        existing_by_id = {str(entry.get("id") or ""): entry for entry in existing_glossary.get("entries") or []}
        existing_by_source = {str(entry.get("source") or ""): entry for entry in existing_glossary.get("entries") or []}
        entries: list[dict[str, Any]] = []
        for raw_entry in raw_entries or []:
            if not isinstance(raw_entry, dict):
                continue
            existing_entry = existing_by_id.get(str(raw_entry.get("id") or "")) or existing_by_source.get(str(raw_entry.get("source") or raw_entry.get("term") or ""))
            entry = self._normalize_project_glossary_entry(raw_entry, existing_entry=existing_entry)
            if entry:
                previous_translation = str((existing_entry or {}).get("translation") or "").strip()
                if previous_translation and previous_translation != entry["translation"] and not entry.get("replacement"):
                    entry["replacement"] = previous_translation
                entries.append(entry)

        glossary = self._normalize_project_glossary({
            "entries": entries,
            "updated_at": self._now_iso(),
            "auto_extract_completed": bool(existing_glossary.get("auto_extract_completed")),
            "auto_extracted_at": str(existing_glossary.get("auto_extracted_at") or ""),
        })
        session["project_glossary"] = glossary
        if persist:
            self.persist_project_state(project_id, session, persist_page_documents=False)
        return self.get_project_glossary(project_id, session)

    def _iter_project_text_segments(self, project_id: str, session: dict[str, Any]):
        for image in session.get("source_images") or []:
            stored_name = str(image.get("stored_name") or "")
            if not stored_name:
                continue
            try:
                document = self.get_page_document(project_id, session, stored_name)
            except Exception:
                continue
            page_name = str(image.get("name") or stored_name)
            for region in document.get("regions") or []:
                if not isinstance(region, dict):
                    continue
                region_id = str(region.get("region_id") or "").strip()
                source_text = str(region.get("source_text") or "").strip()
                translation_payload = region.get("translation") if isinstance(region.get("translation"), dict) else {}
                translation = str(
                    translation_payload.get("resolved")
                    or translation_payload.get("edited")
                    or translation_payload.get("machine")
                    or ""
                ).strip()
                if not region_id or not source_text:
                    continue
                yield {
                    "page_id": stored_name,
                    "page_name": page_name,
                    "region_id": region_id,
                    "source_text": source_text,
                    "translation": translation,
                }

    def _project_text_context_for_glossary(self, project_id: str, session: dict[str, Any], max_chars: int = 24000) -> str:
        lines: list[str] = []
        char_count = 0
        for index, segment in enumerate(self._iter_project_text_segments(project_id, session), start=1):
            source_text = str(segment.get("source_text") or "").replace("\n", " ").strip()
            if not source_text:
                continue
            line = f"{index}. [{segment.get('page_name')}] {source_text}"
            if char_count + len(line) + 1 > max_chars:
                lines.append("...")
                break
            lines.append(line)
            char_count += len(line) + 1
        return "\n".join(lines)

    def _format_project_glossary_context(self, session: dict[str, Any]) -> str:
        glossary = self._normalize_project_glossary(session.get("project_glossary"))
        entries = [
            entry for entry in glossary.get("entries") or []
            if str(entry.get("source") or "").strip() and str(entry.get("translation") or "").strip()
        ]
        if not entries:
            return ""
        lines = [
            "Use the following project terminology exactly when translating. Preserve these mappings consistently:",
        ]
        for entry in sorted(entries, key=lambda item: len(str(item.get("source") or "")), reverse=True):
            category = str(entry.get("category") or "其他")
            lines.append(f"- {entry['source']} => {entry['translation']} ({category})")
        return "\n".join(lines)

    def _attach_project_glossary_context(self, session: dict[str, Any], config: dict[str, Any]) -> None:
        glossary_context = self._format_project_glossary_context(session)
        if glossary_context:
            config["project_glossary_context"] = glossary_context
        else:
            config.pop("project_glossary_context", None)

    def _project_glossary_extraction_system_prompt(self) -> str:
        return (
            "You are a terminology editor for manga translation projects. "
            "Extract project-specific proper nouns and domain terms for consistent manga translation. "
            "Return only valid JSON. Include short character names when they are name-like."
        )

    def _build_glossary_extraction_prompt(self, project_context: str, target_lang: str, *, retry: bool = False) -> str:
        categories = "、".join(sorted(self.PROJECT_GLOSSARY_CATEGORIES))
        retry_instruction = ""
        if retry:
            retry_instruction = (
                "上一次没有得到可用词条。请重新检查，不要因为词条只有 2 到 4 个字就跳过；"
                "只要像角色名、昵称、称号、地点、组织、技能、道具、作品名或重复出现的关键行业词，就必须收录。"
            )
        return (
            "你是漫画翻译项目的专有名词编辑。请阅读全项目 OCR 原文，提取会影响翻译一致性的专有名词。"
            "包括角色名、组织/团体、地点、作品/道具/技能、行业术语和其他需要固定译法的词。"
            "短人名、昵称、称号即使只有 2 到 4 个字，也应该收录；只排除没有专名意义的普通短词。"
            f"目标语言是 {target_lang}。"
            "请只返回 JSON 数组，不要解释；每个元素格式为："
            "{\"source\":\"原文\",\"translation\":\"建议译文\",\"category\":\"分类\",\"note\":\"可选备注\"}。"
            f"分类只能从这些值选择：{categories}。"
            "不要收录普通助词、语气词、整句台词或没有专名意义的普通短词。"
            f"{retry_instruction}\n\n"
            f"项目 OCR 原文：\n{project_context}"
        )

    async def _request_project_glossary_extraction(
        self,
        config: dict[str, Any],
        prompt: str,
    ) -> str:
        selected_translator = str(config.get("selected_translator") or config.get("translator") or "").strip()
        api_key = str(config.get("api_key") or "").strip()
        system_prompt = self._project_glossary_extraction_system_prompt()

        if selected_translator == "openai-compatible":
            return await asyncio.to_thread(
                self._request_chat_completions_text_sync,
                provider_label="OpenAI Compatible",
                base_url=str(config.get("openai_base_url") or "").strip(),
                model=str(config.get("openai_model") or config.get("translator_model") or "").strip(),
                api_key=api_key,
                system_prompt=system_prompt,
                user_prompt=prompt,
                max_tokens=3200,
            )

        if selected_translator == "doubao-ark":
            model = str(config.get("translator_model") or self.DOUBAO_DEFAULT_MODEL).strip()
            if model.startswith("doubao-seed-translation"):
                return ""
            return await asyncio.to_thread(
                self._request_chat_completions_text_sync,
                provider_label="Doubao Ark",
                base_url=self.DOUBAO_ARK_BASE_URL,
                model=model,
                api_key=api_key,
                system_prompt=system_prompt,
                user_prompt=prompt,
                max_tokens=3200,
            )

        if selected_translator == "gemini":
            return await asyncio.to_thread(
                self._request_gemini_text_sync,
                model="gemini-3.1-pro-preview",
                api_key=api_key,
                system_prompt=system_prompt,
                user_prompt=prompt,
            )

        return ""

    def _parse_json_payload_from_model_text(self, text: str) -> Any:
        raw_text = str(text or "").strip()
        if not raw_text:
            return None
        fenced = re.search(r"```(?:json)?\s*(.*?)```", raw_text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            raw_text = fenced.group(1).strip()

        candidates = [raw_text]
        array_start = raw_text.find("[")
        array_end = raw_text.rfind("]")
        if array_start >= 0 and array_end > array_start:
            candidates.append(raw_text[array_start:array_end + 1])
        object_start = raw_text.find("{")
        object_end = raw_text.rfind("}")
        if object_start >= 0 and object_end > object_start:
            candidates.append(raw_text[object_start:object_end + 1])

        seen: set[str] = set()
        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None

    def _glossary_items_from_mapping(self, payload: dict[str, Any]) -> list[Any]:
        items: list[Any] = []
        for source, value in payload.items():
            if source in {
                "version",
                "updated_at",
                "created_at",
                "metadata",
                "meta",
                "language",
                "target_lang",
                "目标语言",
                "source_kind",
            }:
                continue
            if isinstance(value, str):
                items.append({"source": source, "translation": value})
            elif isinstance(value, dict):
                item = dict(value)
                item.setdefault("source", source)
                items.append(item)
        return items

    def _glossary_items_from_payload(self, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        source_keys = {
            "source",
            "term",
            "original",
            "original_text",
            "source_text",
            "raw",
            "name",
            "原文",
            "源文",
            "词条",
            "术语",
            "名词",
            "名称",
            "日文",
            "原名",
        }
        translation_keys = {
            "translation",
            "target",
            "translated",
            "target_text",
            "localized",
            "value",
            "译文",
            "翻译",
            "建议译文",
            "译名",
            "中文",
            "目标译文",
        }
        if source_keys.intersection(payload) and translation_keys.intersection(payload):
            return [payload]
        wrapper_keys = (
            "entries",
            "terms",
            "items",
            "glossary",
            "terminology",
            "data",
            "result",
            "results",
            "专有名词",
            "词条",
            "名词库",
        )
        for key in wrapper_keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                return self._glossary_items_from_mapping(value)
        return self._glossary_items_from_mapping(payload)

    def _parse_glossary_extraction_response(self, text: str) -> list[dict[str, Any]]:
        payload = self._parse_json_payload_from_model_text(text)
        items = self._glossary_items_from_payload(payload)
        if not items:
            return []
        entries: list[dict[str, Any]] = []
        for item in items:
            entry = self._normalize_project_glossary_entry({
                **item,
                "source_kind": "system",
            } if isinstance(item, dict) else item)
            if entry:
                entries.append(entry)
        return entries

    async def extract_project_glossary(
        self,
        project_id: str,
        session: dict[str, Any],
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        self._ensure_runtime_patches()
        if not force and self._project_glossary_auto_extract_completed(session):
            return self.get_project_glossary(project_id, session)
        project_context = self._project_text_context_for_glossary(project_id, session)
        if not project_context:
            return self.get_project_glossary(project_id, session)
        if config.get("translator") == "none":
            return self.get_project_glossary(project_id, session)

        if progress_callback is not None:
            await progress_callback({"event": "status", "message": "正在根据全项目原文提取专有名词库…"})
        prompt = self._build_glossary_extraction_prompt(project_context, str(config.get("target_lang") or ""))
        try:
            response_text = await self._request_project_glossary_extraction(config, prompt)
        except Exception as exc:
            print(f"[WARN] Project glossary extraction failed for {project_id}: {exc}")
            if progress_callback is not None:
                await progress_callback({"event": "status", "message": "专有名词库自动提取失败，已继续使用现有名词库翻译。"})
            return self.get_project_glossary(project_id, session)

        extracted_entries = self._parse_glossary_extraction_response(response_text)
        if not extracted_entries:
            if progress_callback is not None:
                await progress_callback({"event": "status", "message": "第一次未解析到专有名词，正在用更严格规则重试…"})
            retry_prompt = self._build_glossary_extraction_prompt(
                project_context,
                str(config.get("target_lang") or ""),
                retry=True,
            )
            try:
                retry_response_text = await self._request_project_glossary_extraction(config, retry_prompt)
                extracted_entries = self._parse_glossary_extraction_response(retry_response_text)
            except Exception as exc:
                print(f"[WARN] Project glossary extraction retry failed for {project_id}: {exc}")
        if not extracted_entries:
            if progress_callback is not None:
                await progress_callback({"event": "status", "message": "没有提取到可用专有名词，已继续使用现有名词库翻译。"})
            self._mark_project_glossary_auto_extract_completed(project_id, session, persist=True)
            return self.get_project_glossary(project_id, session)

        current = self._normalize_project_glossary(session.get("project_glossary"))
        existing_sources = {str(entry.get("source") or "") for entry in current.get("entries") or []}
        merged_entries = list(current.get("entries") or [])
        added_count = 0
        for entry in extracted_entries:
            if entry["source"] in existing_sources:
                continue
            merged_entries.append(entry)
            existing_sources.add(entry["source"])
            added_count += 1
        if added_count:
            self.save_project_glossary(project_id, session, merged_entries, persist=True)
            if progress_callback is not None:
                await progress_callback({"event": "status", "message": f"已补充 {added_count} 个项目专有名词，继续翻译。"})
        self._mark_project_glossary_auto_extract_completed(project_id, session, persist=True)
        return self.get_project_glossary(project_id, session)

    def _glossary_replacement_candidates(
        self,
        entry: dict[str, Any],
        previous_entries: dict[str, dict[str, Any]],
    ) -> list[str]:
        candidates: list[str] = []

        def add_candidate(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in candidates:
                candidates.append(text)

        explicit = str(entry.get("replacement") or "").strip()
        if explicit:
            add_candidate(explicit)
        previous = previous_entries.get(str(entry.get("id") or "")) or previous_entries.get(str(entry.get("source") or ""))
        previous_translation = str((previous or {}).get("translation") or "").strip()
        if previous_translation and previous_translation != str(entry.get("translation") or "").strip():
            add_candidate(previous_translation)
        add_candidate(entry.get("source"))
        return candidates

    def _glossary_replacement_source(self, entry: dict[str, Any], previous_entries: dict[str, dict[str, Any]]) -> str:
        candidates = self._glossary_replacement_candidates(entry, previous_entries)
        return candidates[0] if candidates else ""

    def preview_project_glossary_application(
        self,
        project_id: str,
        session: dict[str, Any],
        raw_entries: list[Any] | None = None,
    ) -> dict[str, Any]:
        current_glossary = self._normalize_project_glossary(session.get("project_glossary"))
        previous_entries: dict[str, dict[str, Any]] = {}
        for entry in current_glossary.get("entries") or []:
            previous_entries[str(entry.get("id") or "")] = entry
            previous_entries[str(entry.get("source") or "")] = entry

        if raw_entries is None:
            glossary = current_glossary
        else:
            normalized_entries = []
            for raw_entry in raw_entries or []:
                existing = previous_entries.get(str((raw_entry or {}).get("id") or "")) if isinstance(raw_entry, dict) else None
                entry = self._normalize_project_glossary_entry(raw_entry, existing_entry=existing)
                if entry:
                    normalized_entries.append(entry)
            glossary = self._normalize_project_glossary({"entries": normalized_entries})

        entries = [
            entry for entry in glossary.get("entries") or []
            if str(entry.get("source") or "").strip() and str(entry.get("translation") or "").strip()
        ]
        entries.sort(
            key=lambda item: (
                len(str(item.get("source") or "")),
                len(self._glossary_replacement_source(item, previous_entries)),
            ),
            reverse=True,
        )

        changes: list[dict[str, Any]] = []
        for segment in self._iter_project_text_segments(project_id, session):
            source_text = str(segment.get("source_text") or "")
            current_translation = str(segment.get("translation") or "")
            next_translation = current_translation
            applied_terms: list[dict[str, str]] = []
            if not source_text or not current_translation:
                continue
            for entry in entries:
                source = str(entry.get("source") or "")
                target = str(entry.get("translation") or "")
                if not source or not target or source not in source_text or target in next_translation:
                    continue
                replacement_source = ""
                for candidate in self._glossary_replacement_candidates(entry, previous_entries):
                    if candidate == target:
                        continue
                    if candidate in next_translation:
                        replacement_source = candidate
                        break
                if not replacement_source:
                    continue
                next_translation = next_translation.replace(replacement_source, target)
                applied_terms.append({
                    "entry_id": str(entry.get("id") or ""),
                    "source": source,
                    "from": replacement_source,
                    "to": target,
                })
            if next_translation == current_translation:
                continue
            changes.append({
                "page_id": str(segment.get("page_id") or ""),
                "page_name": str(segment.get("page_name") or ""),
                "region_id": str(segment.get("region_id") or ""),
                "source_text": source_text,
                "before": current_translation,
                "after": next_translation,
                "terms": applied_terms,
            })

        affected_pages = sorted({str(change.get("page_id") or "") for change in changes if str(change.get("page_id") or "")})
        return {
            "changes": changes,
            "change_count": len(changes),
            "affected_pages": affected_pages,
            "affected_page_count": len(affected_pages),
        }

    async def apply_project_glossary(
        self,
        project_id: str,
        session: dict[str, Any],
        raw_entries: list[Any],
    ) -> dict[str, Any]:
        preview = self.preview_project_glossary_application(project_id, session, raw_entries)
        self.save_project_glossary(project_id, session, raw_entries, persist=False)
        for change in preview.get("changes") or []:
            region_id = str(change.get("region_id") or "")
            after_text = str(change.get("after") or "")
            if region_id and after_text:
                self._set_region_translation_override_value(session, region_id, after_text)

        if preview.get("changes"):
            affected_pages = [
                str(page_id or "")
                for page_id in (preview.get("affected_pages") or [])
                if str(page_id or "")
            ]
            raw_config = {
                **dict(session.get("last_config") or {}),
                "translation_region_overrides": dict(session.get("translation_region_overrides") or {}),
                "translation_region_skip_overrides": dict(session.get("translation_region_skip_overrides") or {}),
                "translation_region_disabled_overrides": dict(session.get("translation_region_disabled_overrides") or {}),
                "translation_region_layout_overrides": dict(session.get("translation_region_layout_overrides") or {}),
                "style_region_overrides": dict(session.get("style_region_overrides") or {}),
            }

            async def ignore_progress(_event: dict[str, Any]) -> None:
                return None

            source_page_count = len(session.get("source_images") or [])
            if affected_pages and len(set(affected_pages)) < source_page_count:
                for page_id in dict.fromkeys(affected_pages):
                    await self.rerender_session(
                        session_id=project_id,
                        session=session,
                        raw_config=raw_config,
                        progress_callback=ignore_progress,
                        target_stored_name=page_id,
                    )
                config = self._normalize_config(raw_config)
                session["download_path"] = self.build_session_archive(
                    project_id,
                    session,
                    preferred_output_format=config["rerender_output_format"],
                )
                self.persist_project_state(
                    project_id,
                    session,
                    snapshot_kind="glossary_apply",
                    snapshot_summary=f"应用专有名词库并重嵌 {len(set(affected_pages))} 页",
                    persist_page_documents=True,
                    page_ids=list(dict.fromkeys(affected_pages)),
                )
            else:
                await self.rerender_session(
                    session_id=project_id,
                    session=session,
                    raw_config=raw_config,
                    progress_callback=ignore_progress,
                )
        else:
            self.persist_project_state(project_id, session, persist_page_documents=False)

        return {
            **preview,
            "glossary": self.get_project_glossary(project_id, session),
            **self.build_client_session_payload(project_id, session),
        }

    def _prepare_regions_for_page_document(
        self,
        source_rgb: np.ndarray,
        page_cache_dir: Path,
        config: dict[str, Any],
        stored_name: str,
        session: dict[str, Any] | None = None,
    ) -> list[Any]:
        regions = self._load_cached_regions(page_cache_dir)
        return self._prepare_regions_for_page_document_from_regions(
            source_rgb,
            regions,
            config,
            stored_name,
            session=session,
        )

    def _prepare_regions_for_page_document_from_regions(
        self,
        source_rgb: np.ndarray,
        regions: list[Any],
        config: dict[str, Any],
        stored_name: str,
        session: dict[str, Any] | None = None,
    ) -> list[Any]:
        regions = self._merge_manual_regions(session, stored_name, regions)
        self._assign_region_keys(regions, stored_name)
        self._apply_region_translation_overrides(regions, config, stored_name)
        self._apply_region_font_styles(source_rgb, regions, config, stored_name)

        disabled_overrides = config.get("translation_region_disabled_overrides") or {}
        layout_overrides = config.get("translation_region_layout_overrides") or {}
        for region in regions:
            region_key = str(getattr(region, "translation_region_key", "") or "")
            region.disabled_region = bool(disabled_overrides.get(region_key))
            region.letter_spacing_override_active = False
            region.line_spacing_override_active = False

            layout_override = layout_overrides.get(region_key) or {}
            region.preserve_background = bool(
                layout_override.get("preserve_background")
                or layout_override.get("skip_background_erase")
            )
            bbox = layout_override.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                self._set_region_bbox(region, bbox)

            font_size = layout_override.get("font_size")
            if font_size is not None:
                try:
                    region.font_size = max(8, int(round(float(font_size))))
                except (TypeError, ValueError):
                    pass

            rotation = self._normalize_rotation_degrees(layout_override.get("rotation", layout_override.get("angle")))
            if rotation is not None:
                region.angle = rotation
                self._invalidate_region_geometry_cache(region)

            stroke_width = self._normalize_stroke_strength(layout_override.get("stroke_width"))
            if stroke_width is not None:
                region.default_stroke_width = stroke_width

            letter_spacing = self._normalize_letter_spacing(layout_override.get("letter_spacing"))
            if letter_spacing is not None:
                region.letter_spacing = letter_spacing
                region.letter_spacing_override_active = True

            line_spacing = self._normalize_line_spacing(layout_override.get("line_spacing"))
            if line_spacing is not None:
                region.line_spacing = line_spacing
                region.line_spacing_override_active = True

            if "fg_color" in layout_override:
                region.fg_colors = np.array(self._rgb_color_payload(layout_override.get("fg_color"), (0, 0, 0)))

            if "bg_color" in layout_override:
                region.bg_colors = np.array(self._rgb_color_payload(layout_override.get("bg_color"), (255, 255, 255)))
                region.adjust_bg_color = False
            self._sanitize_auto_text_background_color(region, layout_override)

            self._assign_region_direction(
                region,
                self._resolve_region_direction(
                    self._region_bbox(region),
                    getattr(region, "direction", ""),
                    config.get("target_lang"),
                    layout_override=layout_override,
                ),
            )

        return self._dedupe_overlapping_regions(regions)

    def _serialize_page_document_region(
        self,
        region: Any,
        manual_payloads_by_id: dict[str, dict[str, Any]],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        region_id = str(
            getattr(region, "translation_region_key", "")
            or getattr(region, "style_region_key", "")
            or getattr(region, "manual_region_id", "")
        )
        bbox = [int(v) for v in self._region_bbox(region)]
        manual_payload = manual_payloads_by_id.get(region_id) or {}
        source_ids = [str(item) for item in (manual_payload.get("merged_from") or []) if str(item)]
        kind = "auto"
        if bool(getattr(region, "manual_region", False)):
            kind = "merged" if source_ids else "manual"

        font_family = str(getattr(region, "font_family", "") or "")
        font_size_override = None
        layout_override = (config.get("translation_region_layout_overrides") or {}).get(region_id) or {}
        resolved_direction = self._resolve_region_direction(
            bbox,
            getattr(region, "direction", ""),
            config.get("target_lang"),
            layout_override=layout_override,
        )
        if "font_size" in layout_override:
            try:
                font_size_override = max(8, int(round(float(layout_override["font_size"]))))
            except (TypeError, ValueError):
                font_size_override = None

        return {
            "region_id": region_id,
            "page_id": str(manual_payload.get("stored_name") or ""),
            "kind": kind,
            "source_ids": source_ids,
            "bbox": bbox,
            "polygon": None,
            "direction": resolved_direction,
            "source_text": self._region_source_text(region),
            "ocr_confidence": float(getattr(region, "prob", 1.0) or 0.0),
            "translation": {
                "machine": str(getattr(region, "machine_translation", "") or ""),
                "edited": str(getattr(region, "translation_override", "") or ""),
                "resolved": self._region_preview_text(region),
            },
            "style": {
                "auto_font_style": str(getattr(region, "auto_font_style", "") or ""),
                "font_style_override": str(getattr(region, "override_font_style", "") or ""),
                "font_style": str(getattr(region, "font_style", "") or ""),
                "font_family": os.path.basename(font_family) if font_family else "",
                "font_path": font_family,
                "font_key_override": str(layout_override.get("font_key") or ""),
                "font_size": int(max(float(getattr(region, "font_size", 0) or 0), 8)),
                "font_size_override": font_size_override,
                "letter_spacing": float(getattr(region, "letter_spacing", 1.0) or 1.0),
                "line_spacing": float(getattr(region, "line_spacing", 1.0) or 1.0),
                "alignment": str(getattr(region, "_alignment", getattr(region, "alignment", "auto")) or "auto"),
                "fg_color": self._rgb_color_payload(getattr(region, "fg_colors", None), (0, 0, 0)),
                "bg_color": self._rgb_color_payload(getattr(region, "bg_colors", None), (255, 255, 255)),
                "stroke_width": float(getattr(region, "default_stroke_width", 0.2) if getattr(region, "default_stroke_width", None) is not None else 0.2),
                "rotation": float(getattr(region, "angle", 0.0) or 0.0),
            },
            "flags": {
                "disabled": bool(getattr(region, "disabled_region", False)),
                "keep_original": bool(getattr(region, "skip_translation", False)),
                "translation_enabled": not bool(getattr(region, "disabled_region", False)) and not bool(getattr(region, "skip_translation", False)),
                "preserve_background": bool(getattr(region, "preserve_background", False)),
            },
            "audit": {
                "created_by": "user" if bool(getattr(region, "manual_region", False)) else "auto",
                "updated_at": self._now_iso(),
            },
        }

    def _build_page_document(
        self,
        project_id: str,
        session: dict[str, Any],
        stored_name: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        source_dir = Path(session.get("source_dir") or "")
        output_dir = Path(session.get("translated_dir") or "")
        source_path = source_dir / stored_name
        if not source_path.exists():
            raise FileNotFoundError(f"页面原图不存在：{stored_name}")

        source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if source_bgr is None:
            raise RuntimeError(f"无法读取页面原图：{source_path}")

        page_cache_dir = self._session_page_cache_dir(session, project_id, stored_name)
        self._ensure_page_base_image_cache(source_path, page_cache_dir)
        inpainted_path = page_cache_dir / "inpainted.png"
        preview_output = self._current_translated_output(
            session,
            output_dir,
            stored_name,
            self._normalize_rerender_output_format((session.get("last_config") or {}).get("rerender_output_format")),
        )

        previous_document = self._read_json_file(self._project_page_document_path(project_id, stored_name), {})
        previous_metadata = previous_document.get("metadata") if isinstance(previous_document, dict) else {}
        previous_revision = int((previous_metadata or {}).get("revision") or 0)
        previous_regions = previous_document.get("regions") if isinstance(previous_document, dict) else []

        regions: list[dict[str, Any]] = []
        if self._has_rerenderable_page_cache(page_cache_dir):
            source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)
            prepared_regions = self._prepare_regions_for_page_document(
                source_rgb,
                page_cache_dir,
                config,
                stored_name,
                session=session,
            )
            manual_payloads_by_id = {
                str(payload.get("id") or ""): payload
                for payload in self._manual_regions_for_page(session, stored_name)
                if isinstance(payload, dict)
            }
            regions = [
                self._serialize_page_document_region(region, manual_payloads_by_id, config)
                for region in prepared_regions
            ]
            for region_payload in regions:
                region_payload["page_id"] = stored_name
        else:
            source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)
            manual_only_regions = self._prepare_regions_for_page_document_from_regions(
                source_rgb,
                [],
                config,
                stored_name,
                session=session,
            )
            if manual_only_regions:
                manual_payloads_by_id = {
                    str(payload.get("id") or ""): payload
                    for payload in self._manual_regions_for_page(session, stored_name)
                    if isinstance(payload, dict)
                }
                regions = [
                    self._serialize_page_document_region(region, manual_payloads_by_id, config)
                    for region in manual_only_regions
                ]
                for region_payload in regions:
                    region_payload["page_id"] = stored_name
                print(
                    f"[WARN] Missing rerender cache for {project_id}/{stored_name}; "
                    "using manual regions only while keeping the page editable."
                )
            elif isinstance(previous_regions, list) and previous_regions:
                regions = json.loads(json.dumps(previous_regions, ensure_ascii=False))
                for region_payload in regions:
                    if isinstance(region_payload, dict):
                        region_payload["page_id"] = stored_name
                print(
                    f"[WARN] Missing rerender cache for {project_id}/{stored_name}; "
                    "falling back to the last persisted page document."
                )
            else:
                print(
                    f"[WARN] Missing rerender cache for {project_id}/{stored_name}; "
                    "no cached or persisted regions are available."
                )

        document = {
            "page_id": stored_name,
            "review_mode": self._session_review_mode(session),
            "source_image": f"/api/pages/{project_id}/{stored_name}/source-image",
            "base_image": (
                f"/api/pages/{project_id}/{stored_name}/base-image"
                if inpainted_path.exists()
                else f"/api/pages/{project_id}/{stored_name}/source-image"
            ),
            "preview_image": (
                f"/api/pages/{project_id}/{stored_name}/preview-image"
                if preview_output is not None
                else f"/api/pages/{project_id}/{stored_name}/source-image"
            ),
            "translated_image": (
                f"/api/pages/{project_id}/{stored_name}/translated-image"
                if preview_output is not None
                else ""
            ),
            "dimensions": {
                "width": int(source_bgr.shape[1]),
                "height": int(source_bgr.shape[0]),
            },
            "regions": regions,
            "erase_regions": [],
            "metadata": {
                "document_version": self.PAGE_DOCUMENT_VERSION,
                "updated_at": self._now_iso(),
                "revision": previous_revision + 1,
            },
        }
        return self._apply_session_overrides_to_page_document(session, document)

    def _persist_page_documents(
        self,
        project_id: str,
        session: dict[str, Any],
        page_ids: list[str] | None = None,
    ) -> None:
        source_images = list(session.get("source_images") or [])
        if not source_images:
            return

        target_ids = {str(page_id) for page_id in (page_ids or []) if str(page_id)}
        config = self._normalize_config(session.get("last_config") or {})
        for image in source_images:
            stored_name = str(image.get("stored_name") or "")
            if not stored_name:
                continue
            if target_ids and stored_name not in target_ids:
                continue
            try:
                document = self._build_page_document(project_id, session, stored_name, config)
                self._write_json_file(self._project_page_document_path(project_id, stored_name), document)
            except Exception as exc:
                print(f"[WARN] Failed to persist page document for {project_id}/{stored_name}: {exc}")

    def get_page_document(self, project_id: str, session: dict[str, Any], page_id: str) -> dict[str, Any]:
        document_path = self._project_page_document_path(project_id, page_id)
        payload = self._read_json_file(document_path, {})
        if isinstance(payload, dict) and payload:
            normalized_payload = self._normalize_page_document_image_urls(project_id, session, page_id, payload)
            normalized_payload = self._apply_session_overrides_to_page_document(session, normalized_payload)
            if normalized_payload != payload:
                self._write_json_file(document_path, normalized_payload)
            return normalized_payload

        self._persist_page_documents(project_id, session, page_ids=[page_id])
        payload = self._read_json_file(document_path, {})
        if isinstance(payload, dict) and payload:
            normalized_payload = self._normalize_page_document_image_urls(project_id, session, page_id, payload)
            normalized_payload = self._apply_session_overrides_to_page_document(session, normalized_payload)
            if normalized_payload != payload:
                self._write_json_file(document_path, normalized_payload)
            return normalized_payload
        raise FileNotFoundError("当前页面文档不存在，请先完成一次识别或翻译。")

    def _page_document_region_ids(self, project_id: str, session: dict[str, Any], page_id: str) -> set[str]:
        document = self.get_page_document(project_id, session, page_id)
        region_ids: set[str] = set()
        for region in document.get("regions") or []:
            if not isinstance(region, dict):
                continue
            region_id = str(region.get("id") or region.get("region_id") or "").strip()
            if region_id:
                region_ids.add(region_id)
        return region_ids

    def get_page_base_image_path(self, project_id: str, session: dict[str, Any], page_id: str) -> Path:
        source_path = self.get_page_source_image_path(project_id, session, page_id)
        page_cache_dir = self._session_page_cache_dir(session, project_id, page_id)
        self._ensure_page_base_image_cache(source_path, page_cache_dir)
        base_path = page_cache_dir / "inpainted.png"
        if base_path.exists():
            return base_path
        if source_path.exists():
            return source_path
        raise FileNotFoundError("当前页面底图不存在，请先完成一次识别或翻译。")

    def get_page_source_image_path(self, project_id: str, session: dict[str, Any], page_id: str) -> Path:
        candidate_paths = [
            Path(session.get("source_dir") or "") / page_id,
            self._project_source_dir(project_id) / page_id,
        ]
        for source_path in candidate_paths:
            if source_path.exists():
                return source_path
        raise FileNotFoundError("当前页面原图不存在，请先重新上传或恢复项目。")

    def get_page_translated_image_path(self, project_id: str, session: dict[str, Any], page_id: str) -> Path:
        preferred_format = self._normalize_rerender_output_format((session.get("last_config") or {}).get("rerender_output_format"))
        candidate_output_dirs = [
            Path(session.get("translated_dir") or ""),
            self._project_translated_dir(project_id),
        ]
        for output_dir in candidate_output_dirs:
            translated_path = self._current_translated_output(session, output_dir, page_id, preferred_format)
            if translated_path is not None and translated_path.exists():
                return translated_path
        raise FileNotFoundError("当前页面还没有可用的已嵌字结果。")

    def get_page_preview_image_path(self, project_id: str, session: dict[str, Any], page_id: str) -> Path:
        with contextlib.suppress(FileNotFoundError):
            return self.get_page_translated_image_path(project_id, session, page_id)
        return self.get_page_source_image_path(project_id, session, page_id)

    def get_page_image_response_path(
        self,
        image_path: Path,
        project_id: str,
        page_id: str,
        image_kind: str,
        max_side: int | None = None,
    ) -> Path:
        normalized_max_side = self._normalize_image_preview_max_side(max_side)
        if normalized_max_side is None:
            return image_path
        return self._get_resized_image_path(image_path, project_id, page_id, image_kind, normalized_max_side)

    def _normalize_image_preview_max_side(self, max_side: int | None) -> int | None:
        if max_side is None:
            return None
        try:
            parsed_max_side = int(max_side)
        except (TypeError, ValueError):
            return None
        if parsed_max_side <= 0:
            return None
        return max(self.IMAGE_PREVIEW_MIN_SIDE, min(self.IMAGE_PREVIEW_MAX_SIDE, parsed_max_side))

    def _get_resized_image_path(
        self,
        source_path: Path,
        project_id: str,
        page_id: str,
        image_kind: str,
        max_side: int,
    ) -> Path:
        source_path = Path(source_path)
        source_stat = source_path.stat()

        from PIL import Image, ImageOps

        with Image.open(source_path) as source_image:
            source_width, source_height = source_image.size
            source_long_side = max(source_width, source_height)
            if source_long_side <= max_side:
                return source_path

        preview_dir = self._image_preview_cache_dir(project_id, page_id)
        preview_dir.mkdir(parents=True, exist_ok=True)
        safe_kind = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(image_kind or "image")).strip("-") or "image"
        source_identity = "|".join([
            str(source_path.resolve()),
            str(source_stat.st_size),
            str(source_stat.st_mtime_ns),
            str(source_stat.st_ctime_ns),
            str(source_width),
            str(source_height),
            str(max_side),
        ])
        fingerprint = hashlib.sha1(source_identity.encode("utf-8")).hexdigest()[:16]
        output_base = preview_dir / f"{safe_kind}-{max_side}-{fingerprint}"

        for extension in self.IMAGE_PREVIEW_FORMATS:
            candidate_path = output_base.with_suffix(extension)
            if candidate_path.exists():
                return candidate_path

        with Image.open(source_path) as source_image:
            source_image.load()
            normalized_image = ImageOps.exif_transpose(source_image)
            normalized_width, normalized_height = normalized_image.size
            normalized_long_side = max(normalized_width, normalized_height)
            scale = max_side / normalized_long_side
            target_size = (
                max(1, round(normalized_width * scale)),
                max(1, round(normalized_height * scale)),
            )
            has_alpha = (
                normalized_image.mode in {"RGBA", "LA"}
                or (normalized_image.mode == "P" and "transparency" in normalized_image.info)
            )
            resampling_namespace = getattr(Image, "Resampling", Image)
            resampling_filter = getattr(resampling_namespace, "LANCZOS", getattr(Image, "LANCZOS", 1))
            resized_image = normalized_image.resize(target_size, resampling_filter)

        for stale_path in preview_dir.glob(f"{safe_kind}-{max_side}-*"):
            with contextlib.suppress(OSError):
                stale_path.unlink()

        return self._save_image_preview_atomic(resized_image, output_base, has_alpha)

    def _image_preview_project_cache_dir(self, project_id: str) -> Path:
        project_key = hashlib.sha1(str(project_id or "").encode("utf-8")).hexdigest()[:12]
        return self.temp_dir / "image_previews" / project_key

    def _image_preview_cache_dir(self, project_id: str, page_id: str) -> Path:
        page_key = hashlib.sha1(str(page_id or "").encode("utf-8")).hexdigest()[:12]
        return self._image_preview_project_cache_dir(project_id) / page_key

    def _save_image_preview_atomic(self, image: Any, output_base: Path, has_alpha: bool) -> Path:
        candidates: list[tuple[str, str, dict[str, Any], str]] = [
            ("WEBP", ".webp", {"quality": 88, "method": 4}, "RGBA" if has_alpha else "RGB"),
        ]
        if has_alpha:
            candidates.append(("PNG", ".png", {"optimize": True}, "RGBA"))
        else:
            candidates.append(("JPEG", ".jpg", {"quality": 90, "subsampling": 0, "optimize": True}, "RGB"))
            candidates.append(("PNG", ".png", {"optimize": True}, "RGB"))

        last_error: Exception | None = None
        for image_format, extension, save_kwargs, target_mode in candidates:
            output_path = output_base.with_suffix(extension)
            fd, temp_name = tempfile.mkstemp(
                dir=str(output_path.parent),
                suffix=extension,
            )
            os.close(fd)
            temp_path = Path(temp_name)
            try:
                image.convert(target_mode).save(temp_path, format=image_format, **save_kwargs)
                self._replace_file_with_retry(temp_path, output_path)
                return output_path
            except Exception as exc:
                last_error = exc
            finally:
                if temp_path.exists():
                    with contextlib.suppress(OSError):
                        temp_path.unlink()

        if last_error is not None:
            raise last_error
        raise RuntimeError("无法生成图片预览缓存。")

    def _normalize_page_document_image_urls(
        self,
        project_id: str,
        session: dict[str, Any],
        page_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(payload)
        resolved_page_id = str(normalized.get("page_id") or page_id or "").strip()
        if not resolved_page_id:
            return normalized

        source_image_url = f"/api/pages/{project_id}/{resolved_page_id}/source-image"
        base_image_url = f"/api/pages/{project_id}/{resolved_page_id}/base-image"
        preview_image_url = f"/api/pages/{project_id}/{resolved_page_id}/preview-image"
        translated_image_url = ""
        with contextlib.suppress(FileNotFoundError):
            self.get_page_translated_image_path(project_id, session, resolved_page_id)
            translated_image_url = f"/api/pages/{project_id}/{resolved_page_id}/translated-image"

        normalized["page_id"] = resolved_page_id
        normalized["source_image"] = source_image_url
        normalized["base_image"] = base_image_url
        normalized["preview_image"] = preview_image_url
        normalized["translated_image"] = translated_image_url
        return normalized

    def _apply_session_overrides_to_page_document(
        self,
        session: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(payload, dict) or not isinstance(payload.get("regions"), list):
            return payload

        normalized = copy.deepcopy(payload)
        translation_overrides = dict(session.get("translation_region_overrides") or {})
        skip_overrides = dict(session.get("translation_region_skip_overrides") or {})
        disabled_overrides = dict(session.get("translation_region_disabled_overrides") or {})
        layout_overrides = dict(session.get("translation_region_layout_overrides") or {})
        style_overrides = dict(session.get("style_region_overrides") or {})

        for region in normalized.get("regions") or []:
            if not isinstance(region, dict):
                continue

            region_id = str(region.get("region_id") or "").strip()
            if not region_id:
                continue

            translation_payload = region.setdefault("translation", {})
            style_payload = region.setdefault("style", {})
            flags_payload = region.setdefault("flags", {})

            if region_id in translation_overrides:
                edited_text = str(translation_overrides.get(region_id) or "")
                translation_payload["edited"] = edited_text
                translation_payload["resolved"] = edited_text or str(
                    translation_payload.get("machine") or translation_payload.get("resolved") or ""
                )

            if region_id in style_overrides:
                override_style = str(style_overrides.get(region_id) or "")
                style_payload["font_style_override"] = override_style
                if override_style:
                    style_payload["font_style"] = override_style

            if region_id in skip_overrides:
                keep_original = bool(skip_overrides.get(region_id))
                flags_payload["keep_original"] = keep_original
                flags_payload["translation_enabled"] = not keep_original and not bool(flags_payload.get("disabled"))
                if keep_original:
                    translation_payload["resolved"] = str(region.get("source_text") or "")

            if region_id in disabled_overrides:
                disabled = bool(disabled_overrides.get(region_id))
                flags_payload["disabled"] = disabled
                flags_payload["translation_enabled"] = not disabled and not bool(flags_payload.get("keep_original"))

            layout_override = layout_overrides.get(region_id) or {}
            if not isinstance(layout_override, dict) or not layout_override:
                continue

            if "preserve_background" in layout_override or "skip_background_erase" in layout_override:
                flags_payload["preserve_background"] = bool(
                    layout_override.get("preserve_background")
                    or layout_override.get("skip_background_erase")
                )

            bbox = layout_override.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                try:
                    region["bbox"] = [int(round(float(value))) for value in bbox]
                except (TypeError, ValueError):
                    pass

            font_size = layout_override.get("font_size")
            if font_size is not None:
                try:
                    normalized_font_size = max(8, int(round(float(font_size))))
                except (TypeError, ValueError):
                    normalized_font_size = None
                if normalized_font_size is not None:
                    style_payload["font_size_override"] = normalized_font_size
                    style_payload["font_size"] = normalized_font_size

            font_key = str(layout_override.get("font_key") or "").strip()
            if font_key:
                style_payload["font_key_override"] = font_key

            direction = self._normalize_direction_override(layout_override.get("direction"))
            if direction != "auto":
                region["direction"] = direction

            alignment = str(layout_override.get("alignment") or "").strip()
            if alignment:
                style_payload["alignment"] = alignment

            letter_spacing = layout_override.get("letter_spacing")
            if letter_spacing is not None:
                try:
                    style_payload["letter_spacing"] = float(letter_spacing)
                except (TypeError, ValueError):
                    pass

            line_spacing = layout_override.get("line_spacing")
            if line_spacing is not None:
                try:
                    style_payload["line_spacing"] = float(line_spacing)
                except (TypeError, ValueError):
                    pass

            stroke_width = self._normalize_stroke_strength(layout_override.get("stroke_width"))
            if stroke_width is not None:
                style_payload["stroke_width"] = stroke_width

            rotation = self._normalize_rotation_degrees(layout_override.get("rotation", layout_override.get("angle")))
            if rotation is not None:
                style_payload["rotation"] = rotation

            if "fg_color" in layout_override:
                style_payload["fg_color"] = self._rgb_color_payload(layout_override.get("fg_color"), (0, 0, 0))

            if "bg_color" in layout_override:
                style_payload["bg_color"] = self._rgb_color_payload(layout_override.get("bg_color"), (255, 255, 255))

        return normalized

    def get_page_ocr_debug(self, project_id: str, session: dict[str, Any], page_id: str) -> dict[str, Any]:
        page_document = self.get_page_document(project_id, session, page_id)
        page_cache_dir = self._session_page_cache_dir(session, project_id, page_id)
        raw_region_payloads = self._read_json_file(page_cache_dir / "regions.json", [])

        raw_regions: list[dict[str, Any]] = []
        if isinstance(raw_region_payloads, list):
            for index, payload in enumerate(raw_region_payloads):
                if not isinstance(payload, dict):
                    continue
                try:
                    region = self._deserialize_text_region(payload)
                except Exception:
                    region = None

                bbox = [0, 0, 0, 0]
                source_text = ""
                direction = "auto"
                confidence = 0.0
                if region is not None:
                    bbox = [int(value) for value in self._region_bbox(region)]
                    source_text = self._region_source_text(region)
                    direction = str(getattr(region, "direction", "") or "auto")
                    confidence = float(getattr(region, "prob", 0.0) or 0.0)

                raw_regions.append(
                    {
                        "index": index,
                        "bbox": bbox,
                        "source_text": source_text,
                        "text": str(payload.get("text") or ""),
                        "text_raw": str(payload.get("text_raw") or ""),
                        "source_text_field": str(payload.get("source_text") or ""),
                        "translation": str(payload.get("translation") or ""),
                        "direction": direction,
                        "ocr_confidence": confidence,
                        "raw_fields": {
                            key: self._to_json_compatible(value)
                            for key, value in payload.items()
                            if key in {
                                "text",
                                "text_raw",
                                "source_text",
                                "translation",
                                "direction",
                                "prob",
                                "fg_r",
                                "fg_g",
                                "fg_b",
                                "bg_r",
                                "bg_g",
                                "bg_b",
                                "font_size",
                                "vertical",
                                "src_is_vertical",
                            }
                        },
                    }
                )

        document_regions: list[dict[str, Any]] = []
        for index, region in enumerate(page_document.get("regions") or []):
            translation = region.get("translation") or {}
            style = region.get("style") or {}
            flags = region.get("flags") or {}
            document_regions.append(
                {
                    "index": index,
                    "region_id": str(region.get("region_id") or ""),
                    "kind": str(region.get("kind") or "auto"),
                    "bbox": [int(value) for value in (region.get("bbox") or [0, 0, 0, 0])],
                    "source_text": str(region.get("source_text") or ""),
                    "direction": str(region.get("direction") or "auto"),
                    "ocr_confidence": float(region.get("ocr_confidence") or 0.0),
                    "machine_translation": str(translation.get("machine") or ""),
                    "resolved_translation": str(translation.get("resolved") or translation.get("edited") or translation.get("machine") or ""),
                    "font_style": str(style.get("font_style") or ""),
                    "font_family": str(style.get("font_family") or ""),
                    "disabled": bool(flags.get("disabled")),
                    "keep_original": bool(flags.get("keep_original")),
                }
            )

        return {
            "project_id": project_id,
            "page_id": page_id,
            "page_name": self._page_display_name(session, page_id),
            "workflow_stage": str(session.get("workflow_stage") or "idle"),
            "has_rerender_cache": self._has_rerenderable_page_cache(page_cache_dir),
            "cache_dir": str(page_cache_dir.resolve()),
            "source_image": str(page_document.get("source_image") or ""),
            "base_image": str(page_document.get("base_image") or ""),
            "raw_region_count": len(raw_regions),
            "document_region_count": len(document_regions),
            "raw_regions": raw_regions,
            "document_regions": document_regions,
        }

    def get_page_translation_input_debug(self, project_id: str, session: dict[str, Any], page_id: str) -> dict[str, Any]:
        page_document = self.get_page_document(project_id, session, page_id)
        page_cache_dir = self._session_page_cache_dir(session, project_id, page_id)
        raw_region_payloads = self._read_json_file(page_cache_dir / "regions.json", [])
        if not isinstance(raw_region_payloads, list):
            raw_region_payloads = []

        queries: list[dict[str, Any]] = []
        for index, payload in enumerate(raw_region_payloads):
            if not isinstance(payload, dict):
                continue
            try:
                region = self._deserialize_text_region(payload)
            except Exception:
                region = None

            region_text = ""
            source_text = ""
            direction = "auto"
            confidence = 0.0
            bbox = [0, 0, 0, 0]
            if region is not None:
                region_text = str(getattr(region, "text", "") or "").strip()
                source_text = self._region_source_text(region)
                direction = str(getattr(region, "direction", "") or "auto")
                confidence = float(getattr(region, "prob", 0.0) or 0.0)
                bbox = [int(value) for value in self._region_bbox(region)]

            queries.append(
                {
                    "index": index,
                    "bbox": bbox,
                    "query_text": region_text,
                    "source_text": source_text,
                    "text": str(payload.get("text") or ""),
                    "text_raw": str(payload.get("text_raw") or ""),
                    "source_text_field": str(payload.get("source_text") or ""),
                    "direction": direction,
                    "ocr_confidence": confidence,
                    "raw_fields": {
                        key: self._to_json_compatible(value)
                        for key, value in payload.items()
                        if key in {
                            "text",
                            "text_raw",
                            "source_text",
                            "translation",
                            "direction",
                            "prob",
                            "fg_r",
                            "fg_g",
                            "fg_b",
                            "bg_r",
                            "bg_g",
                            "bg_b",
                            "font_size",
                            "vertical",
                            "src_is_vertical",
                        }
                    },
                }
            )

        config = session.get("config") or {}
        translator_config = config.get("translator", {}) if isinstance(config, dict) else {}
        return {
            "project_id": project_id,
            "page_id": page_id,
            "page_name": self._page_display_name(session, page_id),
            "workflow_stage": str(session.get("workflow_stage") or "idle"),
            "translator": str(translator_config.get("translator_gen") or translator_config.get("translator") or ""),
            "target_lang": str(translator_config.get("target_lang") or ""),
            "query_count": len(queries),
            "source_image": str(page_document.get("source_image") or ""),
            "queries": queries,
        }

    def get_translation_request_debug(self, project_id: str) -> dict[str, Any]:
        debug_path = self._translation_request_debug_path(project_id)
        events = self._read_jsonl_file(debug_path)
        if not events:
            raise FileNotFoundError("当前项目还没有翻译请求调试数据，请先实际执行一次翻译。")
        return {
            "project_id": project_id,
            "debug_file": str(debug_path.resolve()),
            "event_count": len(events),
            "events": events,
        }

    def _page_display_name(self, session: dict[str, Any], page_id: str) -> str:
        for image in session.get("source_images") or []:
            if str(image.get("stored_name") or "") == page_id:
                return str(image.get("name") or page_id)
        return page_id

    def _page_document_to_translation_page(
        self,
        page_document: dict[str, Any],
        page_name: str,
    ) -> dict[str, Any]:
        region_payloads: list[dict[str, Any]] = []
        for index, region in enumerate(page_document.get("regions") or []):
            translation = region.get("translation") or {}
            flags = region.get("flags") or {}
            style = region.get("style") or {}
            region_payloads.append(
                {
                    "id": str(region.get("region_id") or ""),
                    "index": index,
                    "bbox": list(region.get("bbox") or [0, 0, 0, 0]),
                    "manual": str(region.get("kind") or "") in {"manual", "merged"},
                    "source_text": str(region.get("source_text") or ""),
                    "ocr_confidence": float(region.get("ocr_confidence") or 0.0),
                    "direction": str(region.get("direction") or "auto"),
                    "machine_translation": str(translation.get("machine") or ""),
                    "override_translation": str(translation.get("edited") or ""),
                    "override_skip": bool(flags.get("keep_original")),
                    "preserve_background": bool(flags.get("preserve_background")),
                    "current_translation": str(translation.get("resolved") or translation.get("edited") or translation.get("machine") or ""),
                    "preview_text": str(translation.get("resolved") or translation.get("edited") or translation.get("machine") or region.get("source_text") or ""),
                    "font_size": int(style.get("font_size") or 12),
                    "font_size_override": int(style.get("font_size_override") or 0),
                    "font_key_override": str(style.get("font_key_override") or ""),
                    "alignment": str(style.get("alignment") or "auto"),
                    "letter_spacing": float(style.get("letter_spacing") or 1.0),
                    "line_spacing": float(style.get("line_spacing") or 1.0),
                    "fg_color": self._rgb_color_payload(style.get("fg_color"), (0, 0, 0)),
                    "bg_color": self._rgb_color_payload(style.get("bg_color"), (255, 255, 255)),
                    "stroke_width": float(style.get("stroke_width") if style.get("stroke_width") is not None else 0.2),
                    "rotation": float(style.get("rotation") if style.get("rotation") is not None else style.get("angle") or 0.0),
                }
            )

        dimensions = page_document.get("dimensions") or {}
        return {
            "stored_name": str(page_document.get("page_id") or ""),
            "name": page_name,
            "image_url": str(page_document.get("preview_image") or page_document.get("source_image") or ""),
            "source_image_url": str(page_document.get("source_image") or ""),
            "base_image_url": str(page_document.get("base_image") or page_document.get("source_image") or ""),
            "translated_image_url": str(page_document.get("translated_image") or page_document.get("preview_image") or page_document.get("source_image") or ""),
            "image_width": int(dimensions.get("width") or 0),
            "image_height": int(dimensions.get("height") or 0),
            "regions": region_payloads,
        }

    def _page_document_to_style_page(
        self,
        page_document: dict[str, Any],
        page_name: str,
    ) -> dict[str, Any]:
        region_payloads: list[dict[str, Any]] = []
        for index, region in enumerate(page_document.get("regions") or []):
            translation = region.get("translation") or {}
            style = region.get("style") or {}
            region_payloads.append(
                {
                    "id": str(region.get("region_id") or ""),
                    "index": index,
                    "bbox": list(region.get("bbox") or [0, 0, 0, 0]),
                    "manual": str(region.get("kind") or "") in {"manual", "merged"},
                    "ocr_confidence": float(region.get("ocr_confidence") or 0.0),
                    "auto_style": str(style.get("auto_font_style") or ""),
                    "override_style": str(style.get("font_style_override") or ""),
                    "resolved_style": str(style.get("font_style") or ""),
                    "font_family": str(style.get("font_family") or ""),
                    "font_key_override": str(style.get("font_key_override") or ""),
                    "preserve_background": bool((region.get("flags") or {}).get("preserve_background")),
                    "direction": str(region.get("direction") or "auto"),
                    "source_text": str(region.get("source_text") or ""),
                    "translation": str(translation.get("resolved") or translation.get("edited") or translation.get("machine") or ""),
                    "preview_text": str(translation.get("resolved") or translation.get("edited") or translation.get("machine") or region.get("source_text") or ""),
                    "font_size": int(style.get("font_size") or 12),
                    "font_size_override": int(style.get("font_size_override") or 0),
                    "alignment": str(style.get("alignment") or "auto"),
                    "letter_spacing": float(style.get("letter_spacing") or 1.0),
                    "line_spacing": float(style.get("line_spacing") or 1.0),
                    "fg_color": self._rgb_color_payload(style.get("fg_color"), (0, 0, 0)),
                    "bg_color": self._rgb_color_payload(style.get("bg_color"), (255, 255, 255)),
                    "stroke_width": float(style.get("stroke_width") if style.get("stroke_width") is not None else 0.2),
                    "rotation": float(style.get("rotation") if style.get("rotation") is not None else style.get("angle") or 0.0),
                }
            )

        dimensions = page_document.get("dimensions") or {}
        return {
            "stored_name": str(page_document.get("page_id") or ""),
            "name": page_name,
            "image_url": str(page_document.get("preview_image") or page_document.get("source_image") or ""),
            "source_image_url": str(page_document.get("source_image") or ""),
            "base_image_url": str(page_document.get("base_image") or page_document.get("source_image") or ""),
            "translated_image_url": str(page_document.get("translated_image") or page_document.get("preview_image") or page_document.get("source_image") or ""),
            "image_width": int(dimensions.get("width") or 0),
            "image_height": int(dimensions.get("height") or 0),
            "regions": region_payloads,
        }

    def _clear_region_overrides(self, session: dict[str, Any], region_id: str) -> None:
        for field_name in (
            "translation_region_overrides",
            "translation_region_skip_overrides",
            "translation_region_disabled_overrides",
            "translation_region_layout_overrides",
            "style_region_overrides",
        ):
            override_map = session.get(field_name)
            if isinstance(override_map, dict):
                override_map.pop(region_id, None)

    def _set_region_bbox_override(self, session: dict[str, Any], region_id: str, bbox: list[int]) -> None:
        overrides = dict(session.get("translation_region_layout_overrides") or {})
        current = dict(overrides.get(region_id) or {})
        current["bbox"] = [int(v) for v in bbox]
        overrides[region_id] = current
        session["translation_region_layout_overrides"] = overrides

    def _set_region_font_size_override(self, session: dict[str, Any], region_id: str, font_size: int | None) -> None:
        overrides = dict(session.get("translation_region_layout_overrides") or {})
        current = dict(overrides.get(region_id) or {})
        if font_size is None:
            current.pop("font_size", None)
        else:
            current["font_size"] = int(font_size)
        if current:
            overrides[region_id] = current
        else:
            overrides.pop(region_id, None)
        session["translation_region_layout_overrides"] = overrides

    def _normalize_direction_override(self, raw_value: Any) -> str:
        value = str(raw_value or "").strip().lower()
        if value in {"v", "vertical", "vertical-rl"}:
            return "vertical"
        if value in {"h", "horizontal", "horizontal-tb"}:
            return "horizontal"
        return "auto"

    def _assign_region_direction(self, region: Any, direction: str) -> None:
        # Vendor TextBlock exposes `direction` as a computed read-only property.
        # Persist the resolved direction through the backing field instead.
        try:
            setattr(region, "_direction", str(direction or "").strip().lower())
        except Exception:
            pass

    def _set_region_direction_override(self, session: dict[str, Any], region_id: str, direction: str | None) -> None:
        overrides = dict(session.get("translation_region_layout_overrides") or {})
        current = dict(overrides.get(region_id) or {})
        normalized_direction = self._normalize_direction_override(direction)
        if normalized_direction == "auto":
            current.pop("direction", None)
        else:
            current["direction"] = normalized_direction
        if current:
            overrides[region_id] = current
        else:
            overrides.pop(region_id, None)
        session["translation_region_layout_overrides"] = overrides

    def _set_region_font_key_override(self, session: dict[str, Any], region_id: str, font_key: str | None) -> None:
        overrides = dict(session.get("translation_region_layout_overrides") or {})
        current = dict(overrides.get(region_id) or {})
        normalized_font_key = str(font_key or "").strip()
        if normalized_font_key:
            current["font_key"] = normalized_font_key
        else:
            current.pop("font_key", None)
        if current:
            overrides[region_id] = current
        else:
            overrides.pop(region_id, None)
        session["translation_region_layout_overrides"] = overrides

    def _set_region_advanced_style_override(self, session: dict[str, Any], region_id: str, patch: dict[str, Any]) -> None:
        overrides = dict(session.get("translation_region_layout_overrides") or {})
        current = dict(overrides.get(region_id) or {})

        if "rotation" in patch or "angle" in patch:
            rotation = self._normalize_rotation_degrees(patch.get("rotation", patch.get("angle")))
            if rotation is not None:
                current["rotation"] = rotation

        if "stroke_width" in patch or "stroke" in patch:
            stroke_width = self._normalize_stroke_strength(patch.get("stroke_width", patch.get("stroke")))
            if stroke_width is not None:
                current["stroke_width"] = stroke_width

        if "letter_spacing" in patch:
            letter_spacing = self._normalize_letter_spacing(patch.get("letter_spacing"))
            if letter_spacing is not None:
                current["letter_spacing"] = letter_spacing

        if "line_spacing" in patch:
            line_spacing = self._normalize_line_spacing(patch.get("line_spacing"))
            if line_spacing is not None:
                current["line_spacing"] = line_spacing

        if "fg_color" in patch or "font_color" in patch:
            current["fg_color"] = self._rgb_color_payload(patch.get("fg_color", patch.get("font_color")), (0, 0, 0))

        if "bg_color" in patch or "stroke_color" in patch:
            current["bg_color"] = self._rgb_color_payload(patch.get("bg_color", patch.get("stroke_color")), (255, 255, 255))

        if "preserve_background" in patch or "skip_background_erase" in patch:
            preserve_background = bool(
                patch.get("preserve_background")
                if "preserve_background" in patch
                else patch.get("skip_background_erase")
            )
            if preserve_background:
                current["preserve_background"] = True
            else:
                current.pop("preserve_background", None)

        if current:
            overrides[region_id] = current
        else:
            overrides.pop(region_id, None)
        session["translation_region_layout_overrides"] = overrides

    def _set_region_translation_override_value(self, session: dict[str, Any], region_id: str, text: str) -> None:
        overrides = dict(session.get("translation_region_overrides") or {})
        normalized_text = self._normalize_translation_override_text(text)
        if normalized_text:
            overrides[region_id] = normalized_text
        else:
            overrides.pop(region_id, None)
        session["translation_region_overrides"] = overrides

    def _set_region_keep_original(self, session: dict[str, Any], region_id: str, enabled: bool) -> None:
        overrides = dict(session.get("translation_region_skip_overrides") or {})
        if enabled:
            overrides[region_id] = True
        else:
            overrides.pop(region_id, None)
        session["translation_region_skip_overrides"] = overrides

    def _set_region_disabled(self, session: dict[str, Any], region_id: str, enabled: bool) -> None:
        overrides = dict(session.get("translation_region_disabled_overrides") or {})
        if enabled:
            overrides[region_id] = True
            self._clear_region_overrides(session, region_id)
            overrides[region_id] = True
        else:
            overrides.pop(region_id, None)
        session["translation_region_disabled_overrides"] = overrides

    def _set_region_style_override_value(self, session: dict[str, Any], region_id: str, style_bucket: str) -> None:
        overrides = dict(session.get("style_region_overrides") or {})
        normalized_style = self._normalize_style_bucket(style_bucket)
        if normalized_style:
            overrides[region_id] = normalized_style
        else:
            overrides.pop(region_id, None)
        session["style_region_overrides"] = overrides

    async def apply_page_commands(
        self,
        project_id: str,
        session: dict[str, Any],
        page_id: str,
        raw_config: dict[str, Any] | None,
        commands: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not any(str(image.get("stored_name") or "") == page_id for image in (session.get("source_images") or [])):
            raise FileNotFoundError("目标页面不存在，请刷新后重试。")
        if not isinstance(commands, list) or not commands:
            raise ValueError("至少需要一条页面命令。")

        config = self.capture_page_command_config(session, raw_config)
        updated_region_ids: list[str] = []
        snapshot_hints: list[str] = []
        created_region_id = ""
        deleted_region_id = ""
        deleted_region_payload: dict[str, Any] | None = None
        page_region_ids: set[str] | None = None

        def require_existing_region_id(command: dict[str, Any]) -> str:
            nonlocal page_region_ids
            region_id = str(command.get("region_id") or "").strip()
            if not region_id:
                raise ValueError("缺少文本框标识。")
            if page_region_ids is None:
                page_region_ids = self._page_document_region_ids(project_id, session, page_id)
            if region_id not in page_region_ids:
                raise FileNotFoundError("目标文本框不存在，请刷新后重试。")
            return region_id

        for command in commands:
            if not isinstance(command, dict):
                continue
            command_type = str(command.get("type") or "").strip().lower()
            if not command_type:
                continue

            if command_type == "update_translation":
                region_id = require_existing_region_id(command)
                self._set_region_translation_override_value(session, region_id, str(command.get("text") or command.get("translation") or ""))
                updated_region_ids.append(region_id)
                snapshot_hints.append("translation_updated")
                continue

            if command_type == "set_keep_original":
                region_id = require_existing_region_id(command)
                self._set_region_keep_original(session, region_id, bool(command.get("enabled")))
                updated_region_ids.append(region_id)
                snapshot_hints.append("keep_original_updated")
                continue

            if command_type == "disable_region":
                region_id = require_existing_region_id(command)
                self._set_region_disabled(session, region_id, True)
                updated_region_ids.append(region_id)
                snapshot_hints.append("region_disabled")
                continue

            if command_type == "restore_region":
                region_id = require_existing_region_id(command)
                self._set_region_disabled(session, region_id, False)
                updated_region_ids.append(region_id)
                snapshot_hints.append("region_restored")
                continue

            if command_type == "update_region_bbox":
                region_id = require_existing_region_id(command)
                bbox = command.get("bbox")
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    raise ValueError("缺少有效的文本框坐标。")
                self._set_region_bbox_override(session, region_id, [int(round(float(v))) for v in bbox])
                updated_region_ids.append(region_id)
                snapshot_hints.append("layout_adjusted")
                continue

            if command_type == "update_font_size":
                region_id = require_existing_region_id(command)
                raw_font_size = command.get("font_size")
                if raw_font_size is None:
                    self._set_region_font_size_override(session, region_id, None)
                else:
                    self._set_region_font_size_override(session, region_id, max(8, int(round(float(raw_font_size)))))
                updated_region_ids.append(region_id)
                snapshot_hints.append("font_size_updated")
                continue

            if command_type == "update_text_direction":
                region_id = require_existing_region_id(command)
                self._set_region_direction_override(session, region_id, str(command.get("direction") or "auto"))
                updated_region_ids.append(region_id)
                snapshot_hints.append("text_direction_updated")
                continue

            if command_type == "update_region_font":
                region_id = require_existing_region_id(command)
                self._set_region_font_key_override(session, region_id, str(command.get("font_key") or command.get("font") or ""))
                updated_region_ids.append(region_id)
                snapshot_hints.append("font_family_updated")
                continue

            if command_type == "update_region_style":
                region_id = require_existing_region_id(command)
                self._set_region_advanced_style_override(session, region_id, command)
                updated_region_ids.append(region_id)
                snapshot_hints.append("advanced_style_updated")
                continue

            if command_type == "update_font_style":
                region_id = require_existing_region_id(command)
                self._set_region_style_override_value(session, region_id, str(command.get("style") or ""))
                updated_region_ids.append(region_id)
                snapshot_hints.append("font_style_updated")
                continue

            if command_type == "create_region":
                bbox = command.get("bbox")
                region = await self.create_manual_region(
                    session_id=project_id,
                    session=session,
                    raw_config=config,
                    stored_name=page_id,
                    bbox=bbox,
                )
                created_region_id = str(region.get("id") or "")
                updated_region_ids.append(created_region_id)
                snapshot_hints.append("manual_region_added")
                continue

            if command_type == "duplicate_region":
                region_id = require_existing_region_id(command)
                region = self.duplicate_region(
                    project_id=project_id,
                    session=session,
                    stored_name=page_id,
                    region_id=region_id,
                    raw_config=config,
                )
                created_region_id = str(region.get("id") or "")
                updated_region_ids.append(created_region_id)
                snapshot_hints.append("manual_region_duplicated")
                page_region_ids = None
                continue

            if command_type == "merge_regions":
                region_ids = command.get("region_ids") or []
                region = await self.merge_regions(
                    session_id=project_id,
                    session=session,
                    raw_config=config,
                    stored_name=page_id,
                    region_ids=region_ids,
                )
                merged_from = [str(item) for item in (region.get("merged_from") or []) if str(item)]
                created_region_id = str(region.get("id") or "")
                for source_region_id in merged_from:
                    self._set_region_disabled(session, source_region_id, True)
                updated_region_ids.extend(merged_from)
                updated_region_ids.append(created_region_id)
                snapshot_hints.append("regions_merged")
                continue

            if command_type == "delete_manual_region":
                region_id = str(command.get("region_id") or "").strip()
                removed_payload = self.pop_manual_region(session, region_id)
                if not removed_payload:
                    raise FileNotFoundError("没有找到对应的手动补框。")
                self._clear_region_overrides(session, region_id)
                deleted_region_id = region_id
                deleted_region_payload = removed_payload
                updated_region_ids.append(region_id)
                snapshot_hints.append("manual_region_deleted")
                continue

            if command_type == "restore_manual_region":
                raw_payload = command.get("payload")
                if not isinstance(raw_payload, dict):
                    raise ValueError("缺少可恢复的手动补框数据。")
                restored_payload = self.restore_manual_region(session, raw_payload)
                created_region_id = str(restored_payload.get("id") or "")
                updated_region_ids.append(created_region_id)
                snapshot_hints.append("manual_region_restored")
                continue

            raise ValueError(f"暂不支持的页面命令：{command_type}")

        self.persist_project_state(
            project_id,
            session,
            persist_page_documents=True,
            page_ids=[page_id],
        )
        document = self.get_page_document(project_id, session, page_id)
        page_name = self._page_display_name(session, page_id)
        return {
            "page_id": page_id,
            "document": document,
            "revision": int((document.get("metadata") or {}).get("revision") or 0),
            "updated_region_ids": sorted({region_id for region_id in updated_region_ids if region_id}),
            "created_region_id": created_region_id,
            "deleted_region_id": deleted_region_id,
            "deleted_region_payload": deleted_region_payload or {},
            "snapshot_hint": snapshot_hints[-1] if snapshot_hints else "",
            "executed_commands": [str(command.get("type") or "") for command in commands if isinstance(command, dict)],
            "translation_page": self._page_document_to_translation_page(document, page_name),
            "style_page": self._page_document_to_style_page(document, page_name),
            "overrides": {
                "translation_region_overrides": dict(session.get("translation_region_overrides") or {}),
                "translation_region_skip_overrides": dict(session.get("translation_region_skip_overrides") or {}),
                "translation_region_disabled_overrides": dict(session.get("translation_region_disabled_overrides") or {}),
                "translation_region_layout_overrides": dict(session.get("translation_region_layout_overrides") or {}),
                "style_region_overrides": dict(session.get("style_region_overrides") or {}),
            },
        }

    async def translate_session(
        self,
        session_id: str,
        session: dict[str, Any],
        raw_config: dict[str, Any] | None,
        progress_callback: ProgressCallback,
    ) -> dict[str, str]:
        await self.detect_session(
            session_id=session_id,
            session=session,
            raw_config=raw_config,
            progress_callback=progress_callback,
            auto_continue=True,
        )
        return await self.resume_translation_session(
            session_id=session_id,
            session=session,
            raw_config={
                **dict(raw_config or session.get("last_config") or {}),
                "translation_region_overrides": dict(session.get("translation_region_overrides") or {}),
                "translation_region_skip_overrides": dict(session.get("translation_region_skip_overrides") or {}),
                "translation_region_disabled_overrides": dict(session.get("translation_region_disabled_overrides") or {}),
                "translation_region_layout_overrides": dict(session.get("translation_region_layout_overrides") or {}),
                "style_region_overrides": dict(session.get("style_region_overrides") or {}),
            },
            progress_callback=progress_callback,
        )

    async def detect_session(
        self,
        session_id: str,
        session: dict[str, Any],
        raw_config: dict[str, Any] | None,
        progress_callback: ProgressCallback,
        auto_continue: bool = False,
    ) -> dict[str, str]:
        self._ensure_runtime_patches()
        config = self.capture_session_config(session, raw_config)
        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        cache_dir = self._prepare_rerender_cache_dir(session_id, reset=True)
        session["rerender_cache_dir"] = str(cache_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._clear_directory(output_dir)
        session["translated_output_map"] = {}
        session["download_path"] = None
        session["workflow_stage"] = "detecting"
        session["deferred_output_names"] = set()
        with contextlib.suppress(FileNotFoundError):
            self._translation_request_debug_path(session_id).unlink()

        config_path = self._write_config(session_id, config, profile="detect")
        log_path = self.temp_dir / f"{session_id}_detect.log"
        expected_outputs = [
            output_dir / Path(image["stored_name"])
            for image in session["source_images"]
        ]
        reported: set[Path] = set()

        await progress_callback({"event": "start", "total_pages": len(expected_outputs)})
        await progress_callback(
            {
                "event": "status",
                "message": (
                    "正在先识别全项目文本框，为专有名词库和翻译建立上下文。"
                    if auto_continue
                    else "正在先识别文本框并建立可校对缓存，翻译会在你确认后再继续。"
                ),
            }
        )

        command = self._build_command(
            source_dir,
            output_dir,
            config_path,
            config,
            prep_manual=True,
        )
        process_returncode = await self._run_translation_command(
            command=command,
            log_path=log_path,
            config=config,
            session_id=session_id,
            session=session,
            expected_outputs=expected_outputs,
            reported=reported,
            progress_callback=progress_callback,
        )
        if process_returncode != 0:
            raise RuntimeError(self._format_failure(log_path))

        rerenderable_pages = self._count_rerenderable_pages(session_id, session)
        if rerenderable_pages == 0:
            raise RuntimeError(
                self._format_missing_rerender_cache_failure(
                    log_path=log_path,
                    stage_label="文本框识别",
                    default_message="文本框识别已完成，但没有生成可校对缓存，请检查后端日志中的 rerender cache 写入情况。",
                )
            )
        if rerenderable_pages < len(expected_outputs):
            print(
                f"[WARN] Detection cache only generated for {rerenderable_pages}/{len(expected_outputs)} "
                f"page(s) in session {session_id}."
            )

        session["workflow_stage"] = "detected"
        self.persist_project_state(
            session_id,
            session,
            snapshot_kind="detect_only",
            snapshot_summary="文本框识别完成，等待逐框确认",
            persist_page_documents=True,
        )
        return {
            "translated_dir": str(output_dir.resolve()),
            "workflow_stage": session["workflow_stage"],
        }

    async def resume_translation_session(
        self,
        session_id: str,
        session: dict[str, Any],
        raw_config: dict[str, Any] | None,
        progress_callback: ProgressCallback,
        target_stored_name: str | None = None,
        skip_completed: bool = False,
    ) -> dict[str, str]:
        self._ensure_runtime_patches()
        config = self.capture_session_config(session, raw_config)
        if target_stored_name is None and not self._project_glossary_auto_extract_completed(session):
            await self.extract_project_glossary(session_id, session, config, progress_callback=progress_callback)
        self._attach_project_glossary_context(session, config)
        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        previous_stage = self._session_workflow_stage(session)
        session["download_path"] = None
        session["workflow_stage"] = "translating"
        session["deferred_output_names"] = set()
        with contextlib.suppress(FileNotFoundError):
            self._translation_request_debug_path(session_id).unlink()

        source_images = self._resolve_translation_images(session, target_stored_name)
        skipped_completed = 0
        if skip_completed and target_stored_name is None:
            source_images, skipped_completed = self._filter_completed_translation_images(
                session,
                output_dir,
                source_images,
                config["rerender_output_format"],
            )
        total = len(source_images)
        await progress_callback({"event": "start", "total_pages": total})
        if skipped_completed:
            await progress_callback({
                "event": "status",
                "message": f"已跳过 {skipped_completed} 张已有翻译结果的页面，继续处理剩余页面。",
            })
        await progress_callback(
            {
                "event": "status",
                "message": (
                    f"正在翻译当前页并回填到工作台：{self._page_display_name(session, target_stored_name)}…"
                    if target_stored_name
                    else "正在根据你确认后的文本框继续翻译并嵌字…"
                ),
            }
        )

        for index, image in enumerate(source_images, start=1):
            stored_name = str(image.get("stored_name") or "")
            source_path = source_dir / stored_name
            cache_page_dir = self._session_page_cache_dir(session, session_id, stored_name)
            if not self._ensure_editable_page_cache(
                session_id=session_id,
                session=session,
                stored_name=stored_name,
                config=config,
                source_path=source_path,
            ):
                raise RuntimeError(f"当前页面缺少可编辑缓存：{stored_name}")

            source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
            if source_bgr is None:
                raise RuntimeError(f"无法读取原图：{source_path}")
            source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)

            translated_regions = self._prepare_cached_regions_for_edit(
                source_rgb,
                cache_page_dir,
                config,
                stored_name,
                session=session,
            )
            await self._translate_cached_regions(
                translated_regions,
                config=config,
                session_id=session_id,
            )
            self._persist_translated_regions(
                page_cache_dir=cache_page_dir,
                session=session,
                stored_name=stored_name,
                regions=translated_regions,
            )

            output_path = output_dir / stored_name
            await self._render_cached_page(
                source_path=source_path,
                output_path=output_path,
                page_cache_dir=cache_page_dir,
                config=config,
                session=session,
            )
            self._update_translated_output_map(session, stored_name, output_path)
            self.persist_project_state(
                session_id,
                session,
                persist_page_documents=True,
                page_ids=[stored_name],
            )

            await progress_callback(
                {
                    "event": "progress",
                    "current": index,
                    "total": total,
                    "image_url": f"/api/pages/{session_id}/{stored_name}/translated-image",
                    "stored_name": stored_name,
                    "name": image["name"],
                }
            )

        complex_session = {**session, "source_images": source_images} if skip_completed and target_stored_name is None else session
        complex_images = self._select_complex_repair_images(complex_session, source_dir, config)
        if complex_images:
            if self._wants_ai_image_cleanup(config):
                if self._has_image_cleanup_key(config):
                    await self._ai_clean_complex_pages(
                        session_id=session_id,
                        session=session,
                        config=config,
                        complex_images=complex_images,
                        progress_callback=progress_callback,
                    )
                else:
                    await progress_callback(
                        {
                            "event": "status",
                            "message": "已启用 AI 去字，但没有可用 API Key，已保留稳定版输出。",
                        }
                    )
            else:
                await self._enhance_complex_pages(
                    session_id=session_id,
                    session=session,
                    config=config,
                    complex_images=complex_images,
                    progress_callback=progress_callback,
                )

        archive_path = ""
        if not target_stored_name or previous_stage == "translated":
            archive_path = self.build_session_archive(
                session_id=session_id,
                session=session,
                preferred_output_format=config["rerender_output_format"],
            )
            session["download_path"] = archive_path
            session["workflow_stage"] = "translated"
        else:
            session["workflow_stage"] = previous_stage if previous_stage in {"detected", "translated"} else "detected"
        self.persist_project_state(
            session_id,
            session,
            snapshot_kind="translate_page" if target_stored_name else "resume_translation",
            snapshot_summary=(
                f"{target_stored_name} 翻译本页并回填"
                if target_stored_name
                else "逐框确认后继续翻译并嵌字"
            ),
            persist_page_documents=True,
            page_ids=[target_stored_name] if target_stored_name else None,
        )

        return {
            "download_url": f"/api/download/{session_id}" if archive_path else "",
            "download_path": str(Path(archive_path).resolve()) if archive_path else "",
            "translated_dir": str(output_dir.resolve()),
            "mask_debug_dir": str(Path(session["mask_debug_dir"]).resolve()) if session.get("mask_debug_dir") else "",
            "workflow_stage": session["workflow_stage"],
        }

    async def rerender_session(
        self,
        session_id: str,
        session: dict[str, Any],
        raw_config: dict[str, Any] | None,
        progress_callback: ProgressCallback,
        target_stored_name: str | None = None,
    ) -> dict[str, str]:
        self._ensure_runtime_patches()
        config = self.capture_session_config(session, raw_config)
        self._attach_project_glossary_context(session, config)
        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        target_images = self._resolve_rerender_images(session, target_stored_name)
        rerenderable_pages = 0
        for image in target_images:
            stored_name = str(image.get("stored_name") or "")
            if not stored_name:
                continue
            source_path = source_dir / stored_name
            if self._ensure_editable_page_cache(
                session_id=session_id,
                session=session,
                stored_name=stored_name,
                config=config,
                source_path=source_path,
            ):
                rerenderable_pages += 1
        if rerenderable_pages == 0:
            raise RuntimeError("当前会话还没有可用的重嵌字缓存，请先用当前版本完整翻译一次。")

        total = len(target_images)
        await progress_callback({"event": "start", "total_pages": total})
        await progress_callback(
            {
                "event": "status",
                "message": (
                    f"正在复用缓存重新嵌字，当前页可重排。"
                    if target_stored_name
                    else f"正在复用缓存重新嵌字，可重排页面 {rerenderable_pages} 张。"
                ),
            }
        )

        style_debug_enabled = config.get("font_style_mode") == "auto-map"
        if style_debug_enabled:
            self._prepare_style_rerender_debug_dir(session_id, reset=True)

        rerender_variant = self._next_rerender_variant(session)

        for index, image in enumerate(target_images, start=1):
            source_path = source_dir / image["stored_name"]
            output_path = self._translated_output_path(
                output_dir,
                image["stored_name"],
                config["rerender_output_format"],
                rerender_variant,
            )
            cache_page_dir = self._session_page_cache_dir(session, session_id, image["stored_name"])

            if self._ensure_editable_page_cache(
                session_id=session_id,
                session=session,
                stored_name=image["stored_name"],
                config=config,
                source_path=source_path,
            ):
                prepared_regions = None
                debug_info = None
                if style_debug_enabled:
                    source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
                    if source_bgr is not None:
                        source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)
                        try:
                            prepared_regions, debug_info = self._prepare_cached_regions_for_edit_with_debug(
                                source_rgb,
                                cache_page_dir,
                                config,
                                image["stored_name"],
                                session=session,
                            )
                        except FileNotFoundError:
                            prepared_regions = None
                            debug_info = None
                await self._render_cached_page(
                    source_path,
                    output_path,
                    cache_page_dir,
                    config,
                    prepared_regions=prepared_regions,
                    debug_output_dir=self._prepare_style_rerender_debug_dir(session_id, reset=False) / Path(image["stored_name"]).stem if style_debug_enabled else None,
                    session=session,
                )
                if style_debug_enabled and debug_info is not None:
                    self._write_style_rerender_debug_report(
                        session_id=session_id,
                        stored_name=image["stored_name"],
                        output_path=output_path,
                        debug_info=debug_info,
                        regions=prepared_regions or [],
                    )
            elif not output_path.exists():
                self._copy_source_to_output(source_path, output_path)

            self._update_translated_output_map(session, image["stored_name"], output_path)

            await progress_callback(
                {
                    "event": "progress",
                    "current": index,
                    "total": total,
                    "image_url": f"/api/pages/{session_id}/{image['stored_name']}/translated-image",
                    "stored_name": image["stored_name"],
                    "name": image["name"],
                }
            )

        archive_path = ""
        if not target_stored_name:
            archive_path = self.build_session_archive(
                session_id=session_id,
                session=session,
                preferred_output_format=config["rerender_output_format"],
            )
            session["download_path"] = archive_path
        session["workflow_stage"] = "translated"
        rerender_scope = "当前页" if target_stored_name else "整组页面"
        self.persist_project_state(
            session_id,
            session,
            snapshot_kind="rerender",
            snapshot_summary=f"{rerender_scope}重新嵌字完成",
            persist_page_documents=True,
            page_ids=[target_stored_name] if target_stored_name else None,
        )

        return {
            "download_url": f"/api/download/{session_id}",
            "download_path": str(Path(archive_path or session.get("download_path") or "").resolve()) if (archive_path or session.get("download_path")) else "",
            "translated_dir": str(output_dir.resolve()),
            "mask_debug_dir": str(Path(session["mask_debug_dir"]).resolve()) if session.get("mask_debug_dir") else "",
            "workflow_stage": session["workflow_stage"],
        }

    def _resolve_rerender_images(
        self,
        session: dict[str, Any],
        target_stored_name: str | None,
    ) -> list[dict[str, Any]]:
        source_images = list(session.get("source_images") or [])
        if not target_stored_name:
            return source_images

        matched_images = [
            image for image in source_images
            if str(image.get("stored_name") or "") == target_stored_name
        ]
        if not matched_images:
            raise RuntimeError("找不到当前选中的页面，无法只重嵌这一页。请刷新逐框校对后再试。")
        return matched_images

    def _resolve_translation_images(
        self,
        session: dict[str, Any],
        target_stored_name: str | None,
    ) -> list[dict[str, Any]]:
        source_images = list(session.get("source_images") or [])
        if not target_stored_name:
            return source_images

        matched_images = [
            image for image in source_images
            if str(image.get("stored_name") or "") == target_stored_name
        ]
        if not matched_images:
            raise RuntimeError("找不到当前选中的页面，无法只翻译这一页。请刷新逐框校对后再试。")
        return matched_images

    def _filter_completed_translation_images(
        self,
        session: dict[str, Any],
        output_dir: Path,
        source_images: list[dict[str, Any]],
        preferred_format: str,
    ) -> tuple[list[dict[str, Any]], int]:
        remaining_images: list[dict[str, Any]] = []
        skipped_count = 0
        for image in source_images:
            stored_name = str(image.get("stored_name") or "").strip()
            if not stored_name:
                continue
            current_output = self._current_translated_output(session, output_dir, stored_name, preferred_format)
            if current_output is not None and current_output.exists():
                self._update_translated_output_map(session, stored_name, current_output)
                skipped_count += 1
                continue
            remaining_images.append(image)
        return remaining_images, skipped_count

    def _collect_session_archive_files(
        self,
        session: dict[str, Any],
        source_dir: Path,
        output_dir: Path,
        preferred_output_format: str,
    ) -> list[Path]:
        archive_files: list[Path] = []
        for image in session.get("source_images") or []:
            stored_name = str(image.get("stored_name") or "")
            if not stored_name:
                continue
            current_output = self._current_translated_output(
                session,
                output_dir,
                stored_name,
                preferred_output_format,
            )
            if current_output is not None and current_output.exists():
                archive_files.append(current_output)
                continue

            source_path = source_dir / stored_name
            fallback_output = self._translated_output_path(
                output_dir,
                stored_name,
                preferred_output_format,
            )
            self._copy_source_to_output(source_path, fallback_output)
            self._update_translated_output_map(session, stored_name, fallback_output)
            archive_files.append(fallback_output)
        return archive_files

    def build_session_archive(
        self,
        session_id: str,
        session: dict[str, Any],
        preferred_output_format: str | None = None,
    ) -> str:
        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        resolved_output_format = preferred_output_format or self._normalize_rerender_output_format(
            (session.get("last_config") or {}).get("rerender_output_format")
        )
        archive_files = self._collect_session_archive_files(
            session=session,
            source_dir=source_dir,
            output_dir=output_dir,
            preferred_output_format=resolved_output_format,
        )
        project_name = self._safe_export_name(session.get("project_title"), session_id)
        named_files = [
            (file_path, f"{project_name}_result_{index:04d}{file_path.suffix.lower()}")
            for index, file_path in enumerate(archive_files, start=1)
        ]
        archive_path = self._make_named_archive(
            self.temp_dir / f"{session_id}_result.zip",
            named_files,
        )
        session["download_path"] = archive_path
        return archive_path

    def build_blank_session_archive(
        self,
        session_id: str,
        session: dict[str, Any],
    ) -> str:
        project_name = self._safe_export_name(session.get("project_title"), session_id)
        named_files: list[tuple[Path, str]] = []
        for index, image in enumerate(session.get("source_images") or [], start=1):
            stored_name = str(image.get("stored_name") or "").strip()
            if not stored_name:
                continue
            base_path = self.get_page_base_image_path(session_id, session, stored_name)
            suffix = base_path.suffix.lower() if base_path.suffix else ".png"
            named_files.append((base_path, f"{project_name}_blank_{index:04d}{suffix}"))
        return self._make_named_archive(
            self.temp_dir / f"{session_id}_blank.zip",
            named_files,
        )

    def get_export_archive_filename(self, session_id: str, session: dict[str, Any], kind: str) -> str:
        normalized_kind = "blank" if str(kind or "").strip().lower() == "blank" else "result"
        project_name = self._safe_export_name(session.get("project_title"), session_id)
        return f"{project_name}_{normalized_kind}.zip"

    def _ensure_runtime_patches(self) -> None:
        try:
            if not patch_mask_refinement():
                print("[WARN] Runtime patch sync did not complete successfully.")
        except Exception as exc:
            print(f"[WARN] Failed to sync runtime patches: {exc}")

    async def _run_translation_command(
        self,
        command: list[str],
        log_path: Path,
        config: dict[str, Any],
        session_id: str,
        session: dict[str, Any],
        expected_outputs: list[Path] | None,
        reported: set[Path] | None,
        progress_callback: ProgressCallback | None,
    ) -> int:
        env = self._build_env(config, session_id)

        print(f"[DEBUG] Starting manga translator engine with command: {' '.join(command)}")
        print(f"[DEBUG] Log file: {log_path}")

        with log_path.open("wb") as log_file:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.base_dir / "manga-image-translator"),
                env=env,
                stdout=log_file,
                stderr=log_file,
            )
            wait_task = asyncio.create_task(process.wait())

            while not wait_task.done():
                if expected_outputs is not None and reported is not None and progress_callback is not None:
                    await self._emit_completed_images(
                        session_id,
                        session,
                        expected_outputs,
                        reported,
                        progress_callback,
                    )
                await asyncio.sleep(1)

            await wait_task

        print(f"[DEBUG] Engine finished with return code {process.returncode}")
        self._dump_log_output(log_path)

        if expected_outputs is not None and reported is not None and progress_callback is not None:
            await self._emit_completed_images(
                session_id,
                session,
                expected_outputs,
                reported,
                progress_callback,
            )

        return process.returncode

    async def _emit_completed_images(
        self,
        session_id: str,
        session: dict[str, Any],
        expected_outputs: list[Path],
        reported: set[Path],
        progress_callback: ProgressCallback,
    ) -> None:
        output_dir = Path(session["translated_dir"])
        total = len(session["source_images"])
        deferred_output_names = session.get("deferred_output_names", set())

        for index, source_meta in enumerate(session["source_images"], start=1):
            output_path = self._current_translated_output(
                session,
                output_dir,
                source_meta["stored_name"],
                "source",
            )
            if output_path is None or output_path in reported or not output_path.exists():
                continue

            if source_meta["stored_name"] in deferred_output_names:
                continue

            reported.add(output_path)
            self._update_translated_output_map(session, source_meta["stored_name"], output_path)
            await progress_callback(
                {
                    "event": "progress",
                    "current": len(reported),
                    "total": total,
                    "image_url": f"/api/pages/{session_id}/{source_meta['stored_name']}/translated-image",
                    "stored_name": source_meta["stored_name"],
                    "name": source_meta["name"],
                }
            )

    def _normalize_config(self, raw_config: dict[str, Any] | None) -> dict[str, Any]:
        raw_config = raw_config or {}
        selected_translator = str(raw_config.get("translator") or "gemini").strip() or "gemini"
        if selected_translator == "custom_openai":
            selected_translator = "doubao-ark"
        translator = selected_translator
        target_lang = str(raw_config.get("target_lang") or "CHS").strip().upper() or "CHS"
        translator_model = self._normalize_translator_model(
            selected_translator,
            raw_config.get("translator_model"),
            raw_config.get("translator_model_custom"),
        )

        # Sugoi doesn't support Chinese
        if translator == "sugoi" and target_lang in ["CHS", "CHT"]:
            print(f"[DEBUG] Sugoi translator does not support {target_lang}. Falling back to 'gemini'")
            translator = "gemini"
        elif selected_translator == "doubao-ark":
            translator = "custom_openai"
        elif selected_translator == "openai-compatible":
            translator = "custom_openai"

        use_gpu = bool(raw_config.get("use_gpu", True))
        api_key = str(raw_config.get("api_key", "")).strip()
        openai_base_url = str(raw_config.get("openai_base_url", "")).strip()
        openai_model = str(raw_config.get("openai_model", "")).strip()
        render_alignment = self._normalize_render_alignment(raw_config.get("render_alignment"))
        render_letter_spacing = self._normalize_render_letter_spacing(raw_config.get("render_letter_spacing"))
        font_style_mode = self._normalize_font_style_mode(raw_config.get("font_style_mode"))
        style_font_keys = self._normalize_style_font_keys(raw_config)
        font_key = str(
            raw_config.get("font_key")
            or style_font_keys.get("gothic")
            or self.DEFAULT_FONT_KEY
        ).strip()
        font_path = self._resolve_font_path(font_key)
        image_cleanup_mode = self._normalize_image_cleanup_mode(raw_config.get("image_cleanup_mode"))
        image_cleanup_model = self._normalize_image_cleanup_model(
            image_cleanup_mode,
            raw_config.get("image_cleanup_model"),
        )
        image_cleanup_api_key = str(raw_config.get("image_cleanup_api_key", "")).strip()
        advanced_erase_provider = self._normalize_advanced_erase_provider(
            raw_config.get("advanced_erase_provider")
        )
        advanced_erase_base_url = self._normalize_advanced_erase_base_url(
            raw_config.get("advanced_erase_base_url")
        )
        advanced_erase_model = self._normalize_advanced_erase_model(
            raw_config.get("advanced_erase_model")
        )
        advanced_erase_api_key = str(raw_config.get("advanced_erase_api_key", "")).strip()
        advanced_erase_timeout_seconds = self._normalize_advanced_erase_timeout_seconds(
            raw_config.get("advanced_erase_timeout_seconds")
        )
        advanced_erase_selection_prompt = self._normalize_advanced_erase_selection_prompt(
            raw_config.get("advanced_erase_selection_prompt")
        )
        mask_cleanup_strength = self._normalize_mask_cleanup_strength(raw_config.get("mask_cleanup_strength"))
        export_mask_debug = bool(raw_config.get("export_mask_debug", False))
        rerender_output_format = self._normalize_rerender_output_format(raw_config.get("rerender_output_format"))
        pause_after_detection = bool(raw_config.get("pause_after_detection", False))
        style_font_paths = {
            style: self._resolve_font_path(font_key)
            for style, font_key in style_font_keys.items()
        }
        style_region_overrides = self._normalize_style_region_overrides(raw_config.get("style_region_overrides"))
        translation_region_overrides = self._normalize_translation_region_overrides(
            raw_config.get("translation_region_overrides")
        )
        translation_region_skip_overrides = self._normalize_translation_region_skip_overrides(
            raw_config.get("translation_region_skip_overrides")
        )
        translation_region_disabled_overrides = self._normalize_translation_region_disabled_overrides(
            raw_config.get("translation_region_disabled_overrides")
        )
        translation_region_layout_overrides = self._normalize_translation_region_layout_overrides(
            raw_config.get("translation_region_layout_overrides")
        )

        return {
            "translator": translator,
            "selected_translator": selected_translator,
            "translator_model": translator_model,
            "target_lang": target_lang,
            "use_gpu": use_gpu,
            "api_key": api_key,
            "openai_base_url": openai_base_url,
            "openai_model": openai_model,
            "font_key": font_key,
            "font_path": font_path,
            "font_style_mode": font_style_mode,
            "style_font_keys": style_font_keys,
            "style_font_paths": style_font_paths,
            "style_region_overrides": style_region_overrides,
            "translation_region_overrides": translation_region_overrides,
            "translation_region_skip_overrides": translation_region_skip_overrides,
            "translation_region_disabled_overrides": translation_region_disabled_overrides,
            "translation_region_layout_overrides": translation_region_layout_overrides,
            "render_alignment": render_alignment,
            "render_letter_spacing": render_letter_spacing,
            "rerender_output_format": rerender_output_format,
            "mask_cleanup_strength": mask_cleanup_strength,
            "export_mask_debug": export_mask_debug,
            "pause_after_detection": pause_after_detection,
            "advanced_text_repair": self._normalize_advanced_text_repair(raw_config.get("advanced_text_repair")),
            "image_cleanup_mode": image_cleanup_mode,
            "image_cleanup_model": image_cleanup_model,
            "image_cleanup_api_key": image_cleanup_api_key,
            "advanced_erase_provider": advanced_erase_provider,
            "advanced_erase_base_url": advanced_erase_base_url,
            "advanced_erase_model": advanced_erase_model,
            "advanced_erase_api_key": advanced_erase_api_key,
            "advanced_erase_timeout_seconds": advanced_erase_timeout_seconds,
            "advanced_erase_selection_prompt": advanced_erase_selection_prompt,
        }

    def _normalize_advanced_text_repair(self, raw_value: Any) -> str:
        value = str(raw_value or "auto").strip().lower()
        if value not in {"auto", "off", "force"}:
            return "auto"
        return value

    def _normalize_advanced_erase_provider(self, raw_value: Any) -> str:
        value = str(raw_value or self.ADVANCED_ERASE_DEFAULT_PROVIDER).strip().lower()
        if value not in {self.ADVANCED_ERASE_DEFAULT_PROVIDER}:
            return self.ADVANCED_ERASE_DEFAULT_PROVIDER
        return value

    def _normalize_advanced_erase_base_url(self, raw_value: Any) -> str:
        value = str(raw_value or SEEDREAM_IMAGE_API_URL).strip()
        return value or SEEDREAM_IMAGE_API_URL

    def _normalize_advanced_erase_model(self, raw_value: Any) -> str:
        value = str(raw_value or self.ADVANCED_ERASE_DEFAULT_MODEL).strip()
        return value or self.ADVANCED_ERASE_DEFAULT_MODEL

    def _normalize_advanced_erase_timeout_seconds(self, raw_value: Any) -> int:
        try:
            value = int(round(float(raw_value)))
        except (TypeError, ValueError):
            value = self.IMAGE_CLEANUP_TIMEOUT_SECONDS
        return max(
            self.ADVANCED_ERASE_MIN_TIMEOUT_SECONDS,
            min(self.ADVANCED_ERASE_MAX_TIMEOUT_SECONDS, value),
        )

    def _normalize_advanced_erase_selection_prompt(self, raw_value: Any) -> str:
        value = str(raw_value or "").strip()
        if not value:
            return ADVANCED_IMAGE_SELECTION_ERASE_PROMPT
        return value[:self.ADVANCED_ERASE_PROMPT_MAX_LENGTH]

    def _normalize_translator_model(
        self,
        translator: str,
        raw_value: Any,
        raw_custom_value: Any = None,
    ) -> str:
        value = str(raw_value or "").strip()
        custom_value = str(raw_custom_value or "").strip()
        if translator in {"doubao-ark", "custom_openai"}:
            return custom_value or value or self.DOUBAO_DEFAULT_MODEL
        return ""

    def _normalize_image_cleanup_mode(self, raw_value: Any) -> str:
        value = str(raw_value or "off").strip().lower()
        if value not in {"off", "gemini-image", "seedream-image"}:
            return "off"
        return value

    def _normalize_render_alignment(self, raw_value: Any) -> str:
        value = str(raw_value or "center").strip().lower()
        if value not in {"auto", "left", "center", "right"}:
            return "center"
        if value == "auto":
            return "center"
        return value

    def _normalize_font_style_mode(self, raw_value: Any) -> str:
        return "auto-map"

    def _normalize_render_letter_spacing(self, raw_value: Any) -> float:
        try:
            value = float(raw_value if raw_value is not None else 1.08)
        except (TypeError, ValueError):
            value = 1.08
        return max(0.85, min(1.35, round(value, 2)))

    def _normalize_rerender_output_format(self, raw_value: Any) -> str:
        value = str(raw_value or "png").strip().lower()
        if value not in {"png", "source"}:
            return "png"
        return value

    def _normalize_style_bucket(self, raw_value: Any) -> str:
        value = str(raw_value or "").strip().lower()
        if value in self.STYLE_BUCKETS:
            return value
        return ""

    def _normalize_style_font_keys(self, raw_config: dict[str, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for style in self.STYLE_BUCKETS:
            raw_key = str(raw_config.get(f"style_font_{style}_key", "") or "").strip()
            normalized[style] = raw_key or self.DEFAULT_STYLE_FONT_KEYS.get(style, self.DEFAULT_FONT_KEY)
        return normalized

    def _normalize_style_region_overrides(self, raw_value: Any) -> dict[str, str]:
        if not isinstance(raw_value, dict):
            return {}

        normalized: dict[str, str] = {}
        for key, value in raw_value.items():
            if not isinstance(key, str):
                continue
            style = self._normalize_style_bucket(value)
            if style:
                normalized[key] = style
        return normalized

    def _normalize_translation_region_overrides(self, raw_value: Any) -> dict[str, str]:
        if not isinstance(raw_value, dict):
            return {}

        normalized: dict[str, str] = {}
        for key, value in raw_value.items():
            if not isinstance(key, str):
                continue
            text = self._normalize_translation_override_text(value)
            if text:
                normalized[key] = text
        return normalized

    def _normalize_translation_override_text(self, value: Any) -> str:
        return str(value or "").replace("\r\n", "\n").replace("\r", "\n")

    def _normalize_translation_region_skip_overrides(self, raw_value: Any) -> dict[str, bool]:
        if not isinstance(raw_value, dict):
            return {}

        normalized: dict[str, bool] = {}
        for key, value in raw_value.items():
            if not isinstance(key, str):
                continue
            if bool(value):
                normalized[key] = True
        return normalized

    def _normalize_translation_region_disabled_overrides(self, raw_value: Any) -> dict[str, bool]:
        if not isinstance(raw_value, dict):
            return {}

        normalized: dict[str, bool] = {}
        for key, value in raw_value.items():
            if not isinstance(key, str):
                continue
            if bool(value):
                normalized[key] = True
        return normalized

    def _normalize_translation_region_layout_overrides(self, raw_value: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(raw_value, dict):
            return {}

        normalized: dict[str, dict[str, Any]] = {}
        for key, value in raw_value.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue

            entry: dict[str, Any] = {}
            bbox = value.get("bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                try:
                    entry["bbox"] = [int(round(float(item))) for item in bbox]
                except (TypeError, ValueError):
                    pass

            font_size = value.get("font_size")
            if font_size is not None:
                try:
                    entry["font_size"] = max(8, int(round(float(font_size))))
                except (TypeError, ValueError):
                    pass

            font_key = str(value.get("font_key") or value.get("font") or "").strip()
            if font_key:
                entry["font_key"] = font_key

            direction = self._normalize_direction_override(
                value.get("direction", value.get("text_direction"))
            )
            if direction != "auto":
                entry["direction"] = direction

            rotation = self._normalize_rotation_degrees(value.get("rotation", value.get("angle")))
            if rotation is not None:
                entry["rotation"] = rotation

            stroke_width = self._normalize_stroke_strength(value.get("stroke_width", value.get("stroke")))
            if stroke_width is not None:
                entry["stroke_width"] = stroke_width

            letter_spacing = self._normalize_letter_spacing(value.get("letter_spacing"))
            if letter_spacing is not None:
                entry["letter_spacing"] = letter_spacing

            line_spacing = self._normalize_line_spacing(value.get("line_spacing"))
            if line_spacing is not None:
                entry["line_spacing"] = line_spacing

            if "fg_color" in value or "font_color" in value:
                entry["fg_color"] = self._rgb_color_payload(value.get("fg_color", value.get("font_color")), (0, 0, 0))

            if "bg_color" in value or "stroke_color" in value:
                entry["bg_color"] = self._rgb_color_payload(value.get("bg_color", value.get("stroke_color")), (255, 255, 255))

            if bool(value.get("preserve_background") or value.get("skip_background_erase")):
                entry["preserve_background"] = True

            if entry:
                normalized[key] = entry
        return normalized

    def _normalize_mask_cleanup_strength(self, raw_value: Any) -> str:
        value = str(raw_value or "standard").strip().lower()
        if value not in {"standard", "clean", "aggressive"}:
            return "standard"
        return value

    def _normalize_image_cleanup_model(self, mode: str, raw_value: Any) -> str:
        value = str(raw_value or "").strip()
        allowed_models = {
            "gemini-image": {
                "gemini-2.5-flash-image",
                "gemini-3-pro-image-preview",
                "gemini-3.1-flash-image-preview",
            },
            "seedream-image": {
                "doubao-seedream-5-0-lite-260128",
            },
        }
        default_models = {
            "gemini-image": "gemini-2.5-flash-image",
            "seedream-image": "doubao-seedream-5-0-lite-260128",
        }
        if mode in allowed_models and value in allowed_models[mode]:
            return value
        return default_models.get(mode, "gemini-2.5-flash-image")

    def _resolve_font_path(self, font_key: str) -> str:
        if not font_key:
            return ""

        candidate = Path(font_key)
        if candidate.exists():
            return str(candidate.resolve())

        font_dirs = self._font_directories_by_source()

        if ":" in font_key:
            source, font_name = font_key.split(":", 1)
            for font_dir in font_dirs.get(source, []):
                resolved = font_dir / font_name
                if resolved.exists():
                    return str(resolved.resolve())

        for font_dir_group in font_dirs.values():
            for font_dir in font_dir_group:
                resolved = font_dir / font_key
                if resolved.exists():
                    return str(resolved.resolve())

        print(f"[WARN] Requested font not found: {font_key}")
        return ""

    def _translated_output_path(
        self,
        output_dir: Path,
        stored_name: str,
        rerender_output_format: str,
        output_variant: str = "",
    ) -> Path:
        source_name = Path(stored_name)
        variant_suffix = f"__{output_variant}" if output_variant else ""
        if rerender_output_format == "png":
            return output_dir / f"{source_name.stem}{variant_suffix}.png"
        return output_dir / f"{source_name.stem}{variant_suffix}{source_name.suffix}"

    def _next_rerender_variant(self, session: dict[str, Any]) -> str:
        next_generation = int(session.get("rerender_generation") or 0) + 1
        session["rerender_generation"] = next_generation
        return f"rerender-{next_generation}"

    def _update_translated_output_map(
        self,
        session: dict[str, Any],
        stored_name: str,
        output_path: Path,
    ) -> None:
        translated_output_map = session.setdefault("translated_output_map", {})
        translated_output_map[stored_name] = output_path.name

    def _current_translated_output(
        self,
        session: dict[str, Any],
        output_dir: Path,
        stored_name: str,
        preferred_format: str = "source",
    ) -> Path | None:
        translated_output_map = session.get("translated_output_map") or {}
        mapped_name = translated_output_map.get(stored_name)
        if mapped_name:
            mapped_path = output_dir / mapped_name
            if mapped_path.exists():
                return mapped_path

        return self._find_existing_translated_output(output_dir, stored_name, preferred_format)

    def _find_existing_translated_output(
        self,
        output_dir: Path,
        stored_name: str,
        preferred_format: str = "source",
    ) -> Path | None:
        preferred_path = self._translated_output_path(output_dir, stored_name, preferred_format)
        candidates: list[Path] = [preferred_path]
        source_path = output_dir / stored_name
        if source_path not in candidates:
            candidates.append(source_path)

        stem = Path(stored_name).stem
        for suffix in self.TRANSLATED_OUTPUT_SUFFIXES:
            candidate = output_dir / f"{stem}{suffix}"
            if candidate not in candidates:
                candidates.append(candidate)

        for suffix in self.TRANSLATED_OUTPUT_SUFFIXES:
            try:
                variant_candidates = sorted(output_dir.glob(f"{stem}__*{suffix}"))
            except OSError:
                variant_candidates = []
            for candidate in variant_candidates:
                if candidate not in candidates:
                    candidates.append(candidate)

        existing_candidates = [candidate for candidate in candidates if candidate.exists()]
        if existing_candidates:
            def candidate_sort_key(path: Path) -> tuple[float, str]:
                try:
                    return (path.stat().st_mtime, path.name)
                except OSError:
                    return (0.0, path.name)

            return max(existing_candidates, key=candidate_sort_key)
        return None

    def _copy_source_to_output(self, source_path: Path, output_path: Path) -> None:
        if source_path.suffix.lower() == output_path.suffix.lower():
            shutil.copy2(source_path, output_path)
            return

        from PIL import Image

        with Image.open(source_path) as source_image:
            suffix = output_path.suffix.lower()
            if suffix in {".jpg", ".jpeg"}:
                source_image.convert("RGB").save(output_path, format="JPEG", quality=100, subsampling=0)
            elif suffix == ".png":
                source_image.save(output_path, format="PNG")
            elif suffix == ".webp":
                source_image.save(output_path, format="WEBP", quality=100, lossless=True)
            else:
                source_image.save(output_path)

    def _prune_translated_output_variants(self, output_dir: Path, stored_name: str, keep: Path) -> None:
        stem = Path(stored_name).stem
        keep_resolved = keep.resolve(strict=False)
        for suffix in self.TRANSLATED_OUTPUT_SUFFIXES:
            candidate = output_dir / f"{stem}{suffix}"
            if candidate.resolve(strict=False) == keep_resolved:
                continue
            if not candidate.exists():
                continue
            try:
                candidate.unlink()
            except OSError:
                continue

    def _make_selected_archive(self, archive_path: Path, files: list[Path], root_dir: Path) -> str:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        if archive_path.exists():
            archive_path.unlink()

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in files:
                if not file_path.exists():
                    continue
                archive.write(file_path, arcname=file_path.relative_to(root_dir))

        return str(archive_path.resolve())

    def _make_named_archive(self, archive_path: Path, files: list[tuple[Path, str]]) -> str:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        if archive_path.exists():
            archive_path.unlink()

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path, archive_name in files:
                if file_path.exists():
                    archive.write(file_path, arcname=archive_name)

        return str(archive_path.resolve())

    def _safe_export_name(self, value: Any, fallback: str) -> str:
        normalized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "").strip())
        normalized = re.sub(r"\s+", " ", normalized).strip(" .")
        return (normalized or str(fallback or "manga")).strip(" .")[:120]

    def _has_style_font_overrides(self, config: dict[str, Any]) -> bool:
        if config.get("font_style_mode") != "auto-map":
            return False
        default_font = str(config.get("font_path") or "")
        for path in (config.get("style_font_paths") or {}).values():
            if path and path != default_font:
                return True
        return False

    def _write_config(self, session_id: str, config: dict[str, Any], profile: str = "default") -> Path:
        config_path = self.temp_dir / f"{session_id}_{profile}_config.json"
        is_complex_profile = profile == "complex"
        strength = config.get("mask_cleanup_strength", "standard")
        strength_overrides = {
            "standard": {"dilation": 0, "kernel": 0, "unclip": 0.0},
            "clean": {"dilation": 4, "kernel": 2, "unclip": 0.15},
            "aggressive": {"dilation": 8, "kernel": 4, "unclip": 0.3},
        }
        strength_boost = strength_overrides.get(strength, strength_overrides["standard"])
        base_dilation = 28 if is_complex_profile else 20
        base_kernel = 9 if is_complex_profile else 7
        base_unclip = 3.0 if is_complex_profile else 2.5
        payload = {
            "translator": {
                "translator": config["translator"],
                "target_lang": config["target_lang"],
            },
            # Fix text artifacts (not clean):
            # The mask offset needs to be large enough to catch loose pixels.
            "mask_dilation_offset": base_dilation + strength_boost["dilation"],
            # Use larger convolution kernel to erase the text completely.
            "kernel_size": base_kernel + strength_boost["kernel"],
            "inpainter": {
                "inpainter": "sd" if is_complex_profile else "lama_large",
                # Keep the experimental path conservative enough to avoid
                # turning normal chapters into an OOM-prone workflow.
                "inpainting_size": 2048,
            },
            "render": {
                # Keep text fitting conservative, but preserve the original
                # detected orientation instead of forcing horizontal Chinese.
                "font_size_minimum": 8,
                "font_size_offset": -6,
                "alignment": config["render_alignment"],
                "direction": "auto"
            },
            "detector": {
                # Better bounding boxes logic:
                "unclip_ratio": round(base_unclip + strength_boost["unclip"], 2)  # Expand detected text bounding boxes
            },
            "ocr": {
                # Merge broken/split bboxes to form one solid bubble
                "use_mocr_merge": True
            }
        }
        config_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return config_path

    def _build_command(
        self,
        source_dir: Path,
        output_dir: Path,
        config_path: Path,
        config: dict[str, Any],
        prep_manual: bool = False,
    ) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "manga_translator",
            "--model-dir",
            str(self.model_dir),
            "local",
            "-i",
            str(source_dir),
            "-o",
            str(output_dir),
            "--overwrite",
            "--config-file",
            str(config_path),
            "--verbose",
        ]
        if config["font_path"]:
            command.extend(["--font-path", config["font_path"]])
        if prep_manual:
            command.append("--prep-manual")
        if config["use_gpu"]:
            command.insert(3, "--use-gpu")
        return command

    def _build_env(self, config: dict[str, Any], session_id: str | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["GEMINI_MODEL"] = "gemini-3.1-pro-preview"
        api_key = config.get("api_key")
        if api_key and config.get("translator") == "gemini":
            env["GEMINI_API_KEY"] = api_key
        if config.get("selected_translator") == "doubao-ark":
            env["CUSTOM_OPENAI_API_BASE"] = self.DOUBAO_ARK_BASE_URL
            model_name = config.get("translator_model") or self.DOUBAO_DEFAULT_MODEL
            env["CUSTOM_OPENAI_MODEL"] = model_name
            env["CUSTOM_OPENAI_MODEL_CONF"] = ""
            env["CUSTOM_OPENAI_USE_RESPONSES"] = "1" if str(model_name).startswith("doubao-seed-translation") else "0"
            if api_key:
                env["CUSTOM_OPENAI_API_KEY"] = api_key
        elif config.get("selected_translator") == "openai-compatible":
            base_url = str(config.get("openai_base_url") or "").strip()
            model = str(config.get("openai_model") or config.get("translator_model") or "").strip()
            if base_url:
                env["CUSTOM_OPENAI_API_BASE"] = base_url
            if model:
                env["CUSTOM_OPENAI_MODEL"] = model
            env["CUSTOM_OPENAI_MODEL_CONF"] = ""
            env["CUSTOM_OPENAI_USE_RESPONSES"] = "0"
            if api_key:
                env["CUSTOM_OPENAI_API_KEY"] = api_key
        glossary_context = str(config.get("project_glossary_context") or "").strip()
        if glossary_context:
            env["MT_PROJECT_GLOSSARY_TEXT"] = glossary_context
        if session_id and config.get("export_mask_debug"):
            env["MT_MASK_DEBUG_DIR"] = str(self._prepare_mask_debug_dir(session_id, reset=False))
        if session_id:
            env["MT_RERENDER_CACHE_DIR"] = str(self._prepare_rerender_cache_dir(session_id, reset=False))
            env["MT_TRANSLATION_DEBUG_FILE"] = str(self._translation_request_debug_path(session_id))
        return env

    def _dump_log_output(self, log_path: Path) -> None:
        try:
            with log_path.open("r", encoding="utf-8", errors="ignore") as file:
                log_content = file.read()
                print(f"[DEBUG] ENGINE LOG OUTPUT:\n{log_content}\n[DEBUG] END LOG OUTPUT")
        except Exception as exc:
            print(f"[DEBUG] Failed to read log file: {exc}")

    def _select_complex_repair_images(
        self,
        session: dict[str, Any],
        source_dir: Path,
        config: dict[str, Any],
    ) -> list[dict[str, str]]:
        repair_mode = config.get("advanced_text_repair", "auto")
        if repair_mode == "off":
            return []

        if not config.get("use_gpu") and not self._wants_ai_image_cleanup(config):
            return []

        selected_images: list[dict[str, str]] = []
        for image in session["source_images"]:
            if repair_mode == "force":
                selected_images.append(image)
                continue

            image_path = source_dir / image["stored_name"]
            analysis = self._analyze_embedded_text_risk(image_path)
            if analysis["should_enhance"]:
                print(
                    "[DEBUG] Complex page detected:",
                    image["stored_name"],
                    analysis,
                )
                selected_images.append(image)

        return selected_images

    async def _apply_font_style_rerender(
        self,
        session_id: str,
        session: dict[str, Any],
        config: dict[str, Any],
        progress_callback: ProgressCallback,
    ) -> None:
        await progress_callback(
            {
                "event": "status",
                "message": "正在根据文本框的字体样式切换中文字体并重新嵌字…",
            }
        )
        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        temp_output_dir = self.temp_dir / f"{session_id}_style_rerender"
        temp_output_dir.mkdir(parents=True, exist_ok=True)
        self._clear_directory(temp_output_dir)
        self._prepare_style_rerender_debug_dir(session_id, reset=True)
        rerender_variant = self._next_rerender_variant(session)

        for image in session["source_images"]:
            source_path = source_dir / image["stored_name"]
            output_path = self._translated_output_path(
                temp_output_dir,
                image["stored_name"],
                config["rerender_output_format"],
                rerender_variant,
            )
            cache_page_dir = self._session_page_cache_dir(session, session_id, image["stored_name"])
            if not self._ensure_editable_page_cache(
                session_id=session_id,
                session=session,
                stored_name=image["stored_name"],
                config=config,
                source_path=source_path,
            ):
                existing_output = self._current_translated_output(
                    session,
                    output_dir,
                    image["stored_name"],
                    "source",
                )
                if existing_output is not None:
                    shutil.copy2(existing_output, output_path)
                else:
                    self._copy_source_to_output(source_path, output_path)
                continue
            prepared_regions = None
            debug_info = None
            source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
            if source_bgr is not None:
                source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)
                prepared_regions, debug_info = self._prepare_cached_regions_for_edit_with_debug(
                    source_rgb,
                    cache_page_dir,
                    config,
                    image["stored_name"],
                    session=session,
                )
            await self._render_cached_page(
                source_path,
                output_path,
                cache_page_dir,
                config,
                prepared_regions=prepared_regions,
                debug_output_dir=self._prepare_style_rerender_debug_dir(session_id, reset=False) / Path(image["stored_name"]).stem,
                session=session,
            )
            if debug_info is not None:
                self._write_style_rerender_debug_report(
                    session_id=session_id,
                    stored_name=image["stored_name"],
                    output_path=output_path,
                    debug_info=debug_info,
                    regions=prepared_regions or [],
                )

        for generated_file in temp_output_dir.iterdir():
            final_output = output_dir / generated_file.name
            shutil.move(str(generated_file), str(final_output))
            for image in session["source_images"]:
                if Path(image["stored_name"]).stem == generated_file.stem.split("__", 1)[0]:
                    self._update_translated_output_map(session, image["stored_name"], final_output)
                    break
        shutil.rmtree(temp_output_dir, ignore_errors=True)

        await progress_callback(
            {
                "event": "status",
                "message": "字体风格映射已应用完成。",
            }
        )

    async def inspect_style_regions(
        self,
        session_id: str,
        session: dict[str, Any],
        raw_config: dict[str, Any] | None,
        target_stored_name: str | None = None,
    ) -> dict[str, Any]:
        config = self._normalize_config(raw_config)
        pages: list[dict[str, Any]] = []
        target = str(target_stored_name or "").strip()

        for image in session["source_images"]:
            if target and str(image.get("stored_name") or "") != target:
                continue
            try:
                page_document = self._get_inspection_page_document(
                    session_id,
                    session,
                    image["stored_name"],
                    config,
                )
            except Exception as exc:
                print(f"[WARN] Failed to build style inspection page for {session_id}/{image['stored_name']}: {exc}")
                continue
            pages.append(self._page_document_to_style_page(page_document, str(image.get("name") or image["stored_name"])))

        return {
            "styles": list(self.STYLE_BUCKETS),
            "pages": pages,
            "workflow_stage": self._session_workflow_stage(session),
            "overrides": {
                "style_region_overrides": dict(session.get("style_region_overrides") or {}),
            },
        }

    async def inspect_translation_regions(
        self,
        session_id: str,
        session: dict[str, Any],
        raw_config: dict[str, Any] | None,
        target_stored_name: str | None = None,
    ) -> dict[str, Any]:
        config = self._normalize_config(raw_config)
        pages: list[dict[str, Any]] = []
        target = str(target_stored_name or "").strip()

        for image in session["source_images"]:
            if target and str(image.get("stored_name") or "") != target:
                continue
            try:
                page_document = self._get_inspection_page_document(
                    session_id,
                    session,
                    image["stored_name"],
                    config,
                )
            except Exception as exc:
                print(f"[WARN] Failed to build review inspection page for {session_id}/{image['stored_name']}: {exc}")
                continue
            pages.append(self._page_document_to_translation_page(page_document, str(image.get("name") or image["stored_name"])))

        return {
            "pages": pages,
            "workflow_stage": self._session_workflow_stage(session),
            "overrides": {
                "translation_region_overrides": dict(session.get("translation_region_overrides") or {}),
                "translation_region_skip_overrides": dict(session.get("translation_region_skip_overrides") or {}),
                "translation_region_disabled_overrides": dict(session.get("translation_region_disabled_overrides") or {}),
                "translation_region_layout_overrides": dict(session.get("translation_region_layout_overrides") or {}),
            },
        }

    def _get_inspection_page_document(
        self,
        session_id: str,
        session: dict[str, Any],
        stored_name: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        override_keys = (
            "translation_region_overrides",
            "translation_region_skip_overrides",
            "translation_region_disabled_overrides",
            "translation_region_layout_overrides",
            "style_region_overrides",
        )
        has_pending_override_changes = any(
            dict(config.get(key) or {}) != dict(session.get(key) or {})
            for key in override_keys
        )
        if has_pending_override_changes:
            return self._build_page_document(session_id, session, stored_name, config)
        return self.get_page_document(session_id, session, stored_name)

    def _session_workflow_stage(self, session: dict[str, Any]) -> str:
        stage = str(session.get("workflow_stage") or "").strip().lower()
        if stage in {"idle", "detecting", "detected", "translating", "translated"}:
            return stage
        return "translated" if session.get("download_path") else "idle"

    def _wants_ai_image_cleanup(self, config: dict[str, Any]) -> bool:
        return config.get("image_cleanup_mode") in {"gemini-image", "seedream-image"}

    def _has_image_cleanup_key(self, config: dict[str, Any]) -> bool:
        if config.get("image_cleanup_mode") == "seedream-image":
            return bool(config.get("image_cleanup_api_key"))
        return bool(config.get("image_cleanup_api_key") or config.get("api_key"))

    def _analyze_embedded_text_risk(self, image_path: Path) -> dict[str, Any]:
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            return {"should_enhance": False, "reason": "unreadable"}

        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        saturation = hsv[:, :, 1]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        mean_saturation = float(saturation.mean())
        colorful_ratio = float(np.mean(saturation > 32))
        dark_ratio = float(np.mean(gray < 180))
        is_colorful = mean_saturation >= 18 and colorful_ratio >= 0.08

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
        _, stroke_mask = cv2.threshold(
            blackhat,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )
        stroke_mask = cv2.morphologyEx(
            stroke_mask,
            cv2.MORPH_OPEN,
            np.ones((2, 2), dtype=np.uint8),
        )
        stroke_mask = cv2.dilate(stroke_mask, np.ones((2, 2), dtype=np.uint8), iterations=1)

        component_total = 0
        risky_components = 0
        image_area = image.shape[0] * image.shape[1]
        num_labels, _, stats, _ = cv2.connectedComponentsWithStats(stroke_mask, 8, cv2.CV_32S)
        for index in range(1, num_labels):
            x, y, w, h, area = stats[index]
            if area < 10 or area > max(4000, int(image_area * 0.02)):
                continue

            aspect_ratio = w / max(h, 1)
            if aspect_ratio < 0.08 or aspect_ratio > 20:
                continue

            component_total += 1
            pad = max(4, int(max(w, h) * 0.2))
            x1 = max(0, x - pad)
            y1 = max(0, y - pad)
            x2 = min(image.shape[1], x + w + pad)
            y2 = min(image.shape[0], y + h + pad)
            region_gray = gray[y1:y2, x1:x2]
            region_saturation = saturation[y1:y2, x1:x2]
            if region_gray.size == 0:
                continue

            region_nonwhite_ratio = float(np.mean(region_gray < 232))
            if (
                float(region_saturation.mean()) > 24
                or region_nonwhite_ratio > 0.28
                or (
                    region_nonwhite_ratio > 0.18
                    and float(region_gray.mean()) < 208
                )
            ):
                risky_components += 1

        risky_ratio = risky_components / component_total if component_total else 0.0
        should_enhance = False
        if is_colorful and component_total >= 8 and risky_components >= 4 and risky_ratio >= 0.45:
            should_enhance = True
        elif component_total >= 18 and risky_components >= 10 and risky_ratio >= 0.72 and dark_ratio >= 0.24:
            should_enhance = True
        elif component_total >= 26 and risky_components >= 18 and risky_ratio >= 0.78:
            should_enhance = True

        return {
            "should_enhance": should_enhance,
            "is_colorful": is_colorful,
            "mean_saturation": round(mean_saturation, 2),
            "colorful_ratio": round(colorful_ratio, 3),
            "dark_ratio": round(dark_ratio, 3),
            "component_total": component_total,
            "risky_components": risky_components,
            "risky_ratio": round(risky_ratio, 3),
        }

    async def _enhance_complex_pages(
        self,
        session_id: str,
        session: dict[str, Any],
        config: dict[str, Any],
        complex_images: list[dict[str, str]],
        progress_callback: ProgressCallback,
    ) -> int:
        await progress_callback(
            {
                "event": "status",
                "message": f"检测到 {len(complex_images)} 张复杂嵌字页，正在进行增强修复…",
            }
        )

        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        enhanced_source_dir = self.temp_dir / f"{session_id}_complex_source"
        enhanced_output_dir = self.temp_dir / f"{session_id}_complex_output"
        shutil.rmtree(enhanced_source_dir, ignore_errors=True)
        shutil.rmtree(enhanced_output_dir, ignore_errors=True)
        enhanced_source_dir.mkdir(parents=True, exist_ok=True)
        enhanced_output_dir.mkdir(parents=True, exist_ok=True)

        for image in complex_images:
            shutil.copy2(source_dir / image["stored_name"], enhanced_source_dir / image["stored_name"])

        complex_config_path = self._write_config(session_id, config, profile="complex")
        complex_log_path = self.temp_dir / f"{session_id}_complex_translation.log"
        complex_command = self._build_command(
            enhanced_source_dir,
            enhanced_output_dir,
            complex_config_path,
            config,
        )

        try:
            returncode = await self._run_translation_command(
                command=complex_command,
                log_path=complex_log_path,
                config=config,
                session_id=session_id,
                session=session,
                expected_outputs=None,
                reported=None,
                progress_callback=None,
            )
        except Exception as exc:
            print(f"[WARN] Complex page enhancement failed before completion: {exc}")
            await progress_callback(
                {
                    "event": "status",
                    "message": "复杂页增强修复失败，已保留稳定版输出。",
                }
            )
            shutil.rmtree(enhanced_source_dir, ignore_errors=True)
            shutil.rmtree(enhanced_output_dir, ignore_errors=True)
            return 0

        if returncode != 0:
            print(f"[WARN] Complex page enhancement exited with {returncode}. Keeping stable outputs.")
            await progress_callback(
                {
                    "event": "status",
                    "message": "复杂页增强修复失败，已保留稳定版输出。",
                }
            )
            shutil.rmtree(enhanced_source_dir, ignore_errors=True)
            shutil.rmtree(enhanced_output_dir, ignore_errors=True)
            return 0

        enhanced_count = 0
        for image in complex_images:
            enhanced_file = enhanced_output_dir / image["stored_name"]
            final_file = output_dir / image["stored_name"]
            if not enhanced_file.exists():
                continue
            shutil.copy2(enhanced_file, final_file)
            enhanced_count += 1

        await progress_callback(
            {
                "event": "status",
                "message": f"复杂页增强修复完成，共 {enhanced_count} 张。",
            }
        )
        shutil.rmtree(enhanced_source_dir, ignore_errors=True)
        shutil.rmtree(enhanced_output_dir, ignore_errors=True)
        return enhanced_count

    async def _ai_clean_complex_pages(
        self,
        session_id: str,
        session: dict[str, Any],
        config: dict[str, Any],
        complex_images: list[dict[str, str]],
        progress_callback: ProgressCallback,
    ) -> int:
        await progress_callback(
            {
                "event": "status",
                "message": f"检测到 {len(complex_images)} 张复杂嵌字页，正在使用 {config['image_cleanup_model']} 做 AI 去字…",
            }
        )

        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        enhanced_count = 0

        for index, image in enumerate(complex_images, start=1):
            source_path = source_dir / image["stored_name"]
            output_path = output_dir / image["stored_name"]
            cache_page_dir = self._session_page_cache_dir(session, session_id, image["stored_name"])

            await progress_callback(
                {
                    "event": "status",
                    "message": (
                        f"AI 去字第 {index}/{len(complex_images)} 张："
                        f"{image['name']}，正在等待 {config['image_cleanup_model']} 返回结果…"
                    ),
                }
            )

            if not self._has_rerenderable_page_cache(cache_page_dir):
                print(f"[WARN] Missing rerender cache for AI cleanup page: {image['stored_name']}")
                await progress_callback(
                    {
                        "event": "status",
                        "message": f"AI 去字跳过 {image['name']}：缺少可复用缓存，已保留稳定版输出。",
                    }
                )
                continue

            try:
                ai_base_rgb = await self._build_ai_clean_base_image(
                    source_path=source_path,
                    page_cache_dir=cache_page_dir,
                    config=config,
                )
                if ai_base_rgb is None:
                    continue

                cv2.imwrite(
                    str(cache_page_dir / "inpainted.png"),
                    cv2.cvtColor(ai_base_rgb, cv2.COLOR_RGB2BGR),
                )

                await self._render_cached_page(
                    source_path=source_path,
                    output_path=output_path,
                    page_cache_dir=cache_page_dir,
                    config=config,
                    base_image_rgb=ai_base_rgb,
                    session=session,
                )
                enhanced_count += 1
                await progress_callback(
                    {
                        "event": "status",
                        "message": f"AI 去字已完成 {image['name']}，正在继续后续页面…",
                    }
                )
            except asyncio.TimeoutError:
                print(
                    f"[WARN] AI cleanup timed out after {self.IMAGE_CLEANUP_TIMEOUT_SECONDS}s "
                    f"for {image['stored_name']}"
                )
                await progress_callback(
                    {
                        "event": "status",
                        "message": (
                            f"AI 去字在 {image['name']} 上等待超时，"
                            "已自动回退到稳定版输出。"
                        ),
                    }
                )
            except Exception as exc:
                print(f"[WARN] AI cleanup failed for {image['stored_name']}: {exc}")
                await progress_callback(
                    {
                        "event": "status",
                        "message": f"AI 去字在 {image['name']} 上失败，已回退到稳定版输出。",
                    }
                )

        if enhanced_count:
            await progress_callback(
                {
                    "event": "status",
                    "message": f"AI 去字完成，共优化 {enhanced_count} 张复杂页。",
                }
            )
        else:
            await progress_callback(
                {
                    "event": "status",
                    "message": "AI 去字没有成功产出可替换结果，已保留稳定版输出。",
                }
            )

        return enhanced_count

    async def _build_ai_clean_base_image(
        self,
        source_path: Path,
        page_cache_dir: Path,
        config: dict[str, Any],
    ) -> np.ndarray | None:
        source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if source_bgr is None:
            raise RuntimeError(f"无法读取原图: {source_path}")

        inpainted_bgr = cv2.imread(str(page_cache_dir / "inpainted.png"), cv2.IMREAD_COLOR)
        if inpainted_bgr is None and self._ensure_page_base_image_cache(source_path, page_cache_dir):
            inpainted_bgr = cv2.imread(str(page_cache_dir / "inpainted.png"), cv2.IMREAD_COLOR)
        if inpainted_bgr is None:
            raise RuntimeError(f"无法读取缓存底图: {page_cache_dir / 'inpainted.png'}")

        source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)
        base_rgb = cv2.cvtColor(inpainted_bgr, cv2.COLOR_BGR2RGB)
        regions = self._load_cached_regions(page_cache_dir)
        target_regions = self._select_ai_cleanup_regions(source_rgb, regions)
        if not target_regions:
            return None

        if config["image_cleanup_mode"] == "seedream-image":
            return await self._build_seedream_full_page_base_image(
                source_path=source_path,
                source_rgb=source_rgb,
                target_regions=target_regions,
                config=config,
            )

        x1, y1, x2, y2 = self._merge_region_bounds(target_regions, source_rgb.shape)
        source_crop = source_rgb[y1:y2, x1:x2].copy()
        guide_crop = self._build_ai_cleanup_guide(source_crop, target_regions, x1, y1)
        prepared_source_crop, prepared_guide_crop = self._prepare_ai_cleanup_inputs(
            source_crop,
            guide_crop,
            config["image_cleanup_mode"],
        )

        api_key = config.get("image_cleanup_api_key")
        if config.get("image_cleanup_mode") != "seedream-image":
            api_key = api_key or config.get("api_key")
        client = create_image_cleanup_client(
            mode=config["image_cleanup_mode"],
            api_key=api_key,
            model=config["image_cleanup_model"],
        )
        print(
            "[DEBUG] AI cleanup request "
            f"file={source_path.name} mode={config['image_cleanup_mode']} model={config['image_cleanup_model']} "
            f"regions={len(target_regions)} crop={source_crop.shape[1]}x{source_crop.shape[0]} "
            f"request={prepared_source_crop.shape[1]}x{prepared_source_crop.shape[0]}"
        )
        edited_crop_rgb = await asyncio.wait_for(
            client.remove_text(
                prepared_source_crop,
                prepared_guide_crop,
                DEFAULT_IMAGE_CLEANUP_PROMPT,
            ),
            timeout=self.IMAGE_CLEANUP_TIMEOUT_SECONDS,
        )
        if edited_crop_rgb.shape[:2] != source_crop.shape[:2]:
            edited_crop_rgb = cv2.resize(
                edited_crop_rgb,
                (source_crop.shape[1], source_crop.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )

        blend_mask = self._build_ai_blend_mask(target_regions, source_rgb.shape, x1, y1, x2, y2)
        base_region = base_rgb[y1:y2, x1:x2].astype(np.float32)
        edited_region = edited_crop_rgb.astype(np.float32)
        alpha = blend_mask[:, :, None].astype(np.float32) / 255.0
        base_rgb[y1:y2, x1:x2] = np.clip(
            base_region * (1 - alpha) + edited_region * alpha,
            0,
            255,
        ).astype(np.uint8)
        return base_rgb

    async def _build_seedream_full_page_base_image(
        self,
        source_path: Path,
        source_rgb: np.ndarray,
        target_regions: list[Any],
        config: dict[str, Any],
    ) -> np.ndarray:
        guide_rgb = self._build_ai_cleanup_guide(source_rgb.copy(), target_regions, 0, 0)
        client = create_image_cleanup_client(
            mode=config["image_cleanup_mode"],
            api_key=config["image_cleanup_api_key"],
            model=config["image_cleanup_model"],
        )
        print(
            "[DEBUG] AI cleanup request "
            f"file={source_path.name} mode={config['image_cleanup_mode']} model={config['image_cleanup_model']} "
            f"regions={len(target_regions)} crop={source_rgb.shape[1]}x{source_rgb.shape[0]} "
            f"request={source_rgb.shape[1]}x{source_rgb.shape[0]}"
        )
        edited_rgb = await asyncio.wait_for(
            client.remove_text(
                source_rgb,
                guide_rgb,
                DEFAULT_IMAGE_CLEANUP_PROMPT,
            ),
            timeout=self.IMAGE_CLEANUP_TIMEOUT_SECONDS,
        )
        if edited_rgb.shape[:2] != source_rgb.shape[:2]:
            edited_rgb = cv2.resize(
                edited_rgb,
                (source_rgb.shape[1], source_rgb.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )
        return edited_rgb

    def _composite_advanced_erase_result(
        self,
        source_rgb: np.ndarray,
        edited_rgb: np.ndarray,
        change_mask: np.ndarray | None = None,
        max_changed_ratio: float | None = None,
        clean_white_containers: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, float]:
        edited_rgb = self._normalize_advanced_erase_edited_image(source_rgb, edited_rgb)

        mask = (
            self._normalize_advanced_erase_mask(change_mask, source_rgb.shape)
            if change_mask is not None
            else self._build_advanced_erase_change_mask(source_rgb, edited_rgb)
        )
        ratio_limit = (
            float(max_changed_ratio)
            if max_changed_ratio is not None
            else self.ADVANCED_ERASE_MAX_CHANGED_RATIO
        )
        changed_ratio = float(cv2.countNonZero(mask)) / float(mask.size or 1)
        if changed_ratio <= 0:
            raise RuntimeError("高级擦除没有检测到可替换的文字区域。")
        if changed_ratio > ratio_limit:
            raise RuntimeError(
                "高级擦除返回图与原图差异过大，已拒绝覆盖空页。"
                "请重试，或换一张图/更保守的提示后再试。"
            )

        alpha = self._advanced_erase_alpha_from_mask(mask)
        alpha_f = alpha[:, :, None].astype(np.float32) / 255.0
        composite = (
            source_rgb.astype(np.float32) * (1.0 - alpha_f)
            + edited_rgb.astype(np.float32) * alpha_f
        )
        composite_rgb = np.clip(composite, 0, 255).astype(np.uint8)
        if clean_white_containers:
            composite_rgb = self._clean_advanced_erase_white_container_residue(source_rgb, composite_rgb, mask)
        return composite_rgb, mask, changed_ratio

    def _normalize_selection_erase_rects(
        self,
        raw_rects: Any,
        image_shape: tuple[int, ...],
    ) -> list[tuple[int, int, int, int]]:
        if not isinstance(raw_rects, list):
            return []
        height, width = image_shape[:2]
        rects: list[tuple[int, int, int, int]] = []
        for raw_rect in raw_rects:
            if not isinstance(raw_rect, dict):
                continue
            try:
                if {"x1", "y1", "x2", "y2"}.issubset(raw_rect.keys()):
                    x1 = float(raw_rect.get("x1"))
                    y1 = float(raw_rect.get("y1"))
                    x2 = float(raw_rect.get("x2"))
                    y2 = float(raw_rect.get("y2"))
                else:
                    x1 = float(raw_rect.get("x"))
                    y1 = float(raw_rect.get("y"))
                    rect_width = float(raw_rect.get("width"))
                    rect_height = float(raw_rect.get("height"))
                    x2 = x1 + rect_width
                    y2 = y1 + rect_height
            except (TypeError, ValueError):
                continue

            values = (x1, y1, x2, y2)
            if all(-0.05 <= value <= 1.05 for value in values):
                x1 *= width
                x2 *= width
                y1 *= height
                y2 *= height

            left = int(round(min(x1, x2)))
            right = int(round(max(x1, x2)))
            top = int(round(min(y1, y2)))
            bottom = int(round(max(y1, y2)))
            left = max(0, min(width, left))
            right = max(0, min(width, right))
            top = max(0, min(height, top))
            bottom = max(0, min(height, bottom))
            if right - left < 4 or bottom - top < 4:
                continue
            rects.append((left, top, right, bottom))
        return rects

    def _build_selection_erase_mask(
        self,
        rects: list[tuple[int, int, int, int]],
        image_shape: tuple[int, ...],
    ) -> np.ndarray:
        height, width = image_shape[:2]
        mask = np.zeros((height, width), dtype=np.uint8)
        for x1, y1, x2, y2 in rects:
            mask[y1:y2, x1:x2] = 255
        return mask

    def _build_selection_erase_input_image(
        self,
        base_rgb: np.ndarray,
        selection_mask: np.ndarray,
    ) -> np.ndarray:
        mask = self._normalize_advanced_erase_mask(selection_mask, base_rgb.shape)
        selected_input = np.full_like(base_rgb, 255)
        selected_input[mask > 0] = base_rgb[mask > 0]
        return selected_input

    def _normalize_local_model_erase_mask_mode(self, raw_value: Any) -> str:
        value = str(raw_value or "text").strip().lower().replace("_", "-")
        if value in {"selection", "selected", "area", "whole", "whole-selection", "full"}:
            return "selection"
        return "text"

    def _select_local_inpainting_device(self, use_gpu: bool) -> str:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("本地模型擦除需要 PyTorch，请先安装完整后端依赖。") from exc

        if use_gpu:
            if torch.cuda.is_available():
                return "cuda"
            mps_backend = getattr(torch.backends, "mps", None)
            if mps_backend is not None and mps_backend.is_available():
                return "mps"
        return "cpu"

    async def _run_local_lama_inpaint(
        self,
        base_rgb: np.ndarray,
        selection_mask: np.ndarray,
        *,
        device: str,
    ) -> np.ndarray:
        self._ensure_vendor_import_path()
        patch_mask_refinement()
        from manga_translator.config import Inpainter, InpainterConfig, InpaintPrecision
        from manga_translator.inpainting import dispatch as dispatch_inpainting
        from manga_translator.utils import ModelWrapper

        ModelWrapper._MODEL_DIR = str(self.model_dir)
        config = InpainterConfig(
            inpainter=Inpainter.lama_large,
            inpainting_size=self.LOCAL_MODEL_ERASE_INPAINTING_SIZE,
            inpainting_precision=InpaintPrecision.bf16,
        )
        return await dispatch_inpainting(
            Inpainter.lama_large,
            base_rgb,
            selection_mask,
            config,
            self.LOCAL_MODEL_ERASE_INPAINTING_SIZE,
            device,
            False,
        )

    def _composite_local_model_erase_result(
        self,
        base_rgb: np.ndarray,
        model_rgb: np.ndarray,
        selection_mask: np.ndarray,
    ) -> np.ndarray:
        model_rgb = self._normalize_advanced_erase_edited_image(base_rgb, model_rgb)
        mask = self._normalize_advanced_erase_mask(selection_mask, base_rgb.shape)
        if not np.any(mask):
            return base_rgb.copy()

        min_side = max(1, min(base_rgb.shape[:2]))
        blur_size = max(3, min(15, int(round(min_side * 0.01)) | 1))
        alpha = cv2.GaussianBlur(mask, (blur_size, blur_size), 0)
        alpha = cv2.bitwise_and(alpha, mask)
        alpha[mask > 0] = np.maximum(alpha[mask > 0], 224)
        alpha_f = alpha[:, :, None].astype(np.float32) / 255.0
        composite = (
            base_rgb.astype(np.float32) * (1.0 - alpha_f)
            + model_rgb.astype(np.float32) * alpha_f
        )
        return np.clip(composite, 0, 255).astype(np.uint8)

    def _build_selection_erase_text_mask(
        self,
        base_rgb: np.ndarray,
        selection_mask: np.ndarray,
    ) -> np.ndarray:
        selection = self._normalize_advanced_erase_mask(selection_mask, base_rgb.shape)
        if not np.any(selection):
            return np.zeros(base_rgb.shape[:2], dtype=np.uint8)

        gray = cv2.cvtColor(base_rgb[:, :, :3], cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        local_delta = cv2.absdiff(gray, blurred)
        gradient_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        gradient = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, gradient_kernel)
        edges = cv2.Canny(gray, 40, 120)

        high_contrast = (local_delta >= 14) | (gradient >= 18) | (edges > 0)
        selected = selection > 0
        candidate = (
            selected
            & (
                (gray <= 76)
                | ((gray <= 162) & high_contrast)
                | ((gray >= 210) & high_contrast)
            )
        ).astype(np.uint8) * 255

        filtered = self._filter_selection_erase_text_components(
            candidate,
            selection,
            local_delta=local_delta,
            before_dilation=True,
        )
        if not np.any(filtered):
            return filtered

        close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        closed = cv2.morphologyEx(filtered, cv2.MORPH_CLOSE, close_kernel, iterations=1)
        filled = self._fill_selection_erase_small_enclosures(closed, selection)
        text_mask = cv2.bitwise_or(closed, filled)

        min_side = max(1, min(base_rgb.shape[:2]))
        dilate_size = max(5, min(31, int(round(min_side * 0.006)) | 1))
        dilate_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_size, dilate_size))
        text_mask = cv2.dilate(text_mask, dilate_kernel, iterations=1)
        text_mask = cv2.bitwise_and(text_mask, selection)
        text_mask = self._filter_selection_erase_text_components(
            text_mask,
            selection,
            before_dilation=False,
        )
        return cv2.bitwise_and(text_mask, selection)

    def _fill_selection_erase_small_enclosures(
        self,
        mask: np.ndarray,
        selection_mask: np.ndarray,
    ) -> np.ndarray:
        selection_area = max(int(cv2.countNonZero(selection_mask)), 1)
        fill_limit = max(256, int(selection_area * 0.28))
        filled = np.zeros(mask.shape, dtype=np.uint8)
        contours, _hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            if cv2.contourArea(contour) < 6:
                continue
            x, y, width, height = cv2.boundingRect(contour)
            bbox_area = width * height
            if bbox_area <= fill_limit:
                cv2.drawContours(filled, [contour], -1, 255, thickness=cv2.FILLED)
        return cv2.bitwise_and(filled, selection_mask)

    def _filter_selection_erase_text_components(
        self,
        mask: np.ndarray,
        selection_mask: np.ndarray,
        *,
        local_delta: np.ndarray | None = None,
        before_dilation: bool,
    ) -> np.ndarray:
        mask = self._normalize_advanced_erase_mask(mask, mask.shape)
        selection = self._normalize_advanced_erase_mask(selection_mask, mask.shape)
        component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if component_count <= 1:
            return cv2.bitwise_and(mask, selection)

        selection_area = max(int(cv2.countNonZero(selection)), 1)
        page_area = max(mask.shape[0] * mask.shape[1], 1)
        min_area = max(3, int(page_area * 0.0000008)) if before_dilation else max(6, int(page_area * 0.0000012))
        filtered = np.zeros(mask.shape, dtype=np.uint8)

        for label in range(1, component_count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area < min_area:
                continue
            x = int(stats[label, cv2.CC_STAT_LEFT])
            y = int(stats[label, cv2.CC_STAT_TOP])
            width = int(stats[label, cv2.CC_STAT_WIDTH])
            height = int(stats[label, cv2.CC_STAT_HEIGHT])
            bbox_area = max(width * height, 1)
            fill_ratio = float(area) / float(bbox_area)

            # Large sparse contours are usually speech/SFX frames. They should be
            # preserved unless the model itself changes only nearby text pixels.
            if bbox_area > max(512, int(selection_area * 0.36)) and fill_ratio < 0.16:
                continue
            if area > int(selection_area * 0.68) and fill_ratio > 0.18:
                continue
            if bbox_area > int(selection_area * 0.86) and fill_ratio > 0.14:
                continue

            if before_dilation and local_delta is not None and area < 12:
                component = labels == label
                component_delta = local_delta[component]
                if component_delta.size and float(np.median(component_delta)) < 7.0:
                    continue

            filtered[labels == label] = 255
        return cv2.bitwise_and(filtered, selection)

    def _clip_advanced_erase_mask(
        self,
        mask: np.ndarray,
        allowed_mask: np.ndarray,
    ) -> np.ndarray:
        normalized = self._normalize_advanced_erase_mask(mask, mask.shape)
        allowed = self._normalize_advanced_erase_mask(allowed_mask, normalized.shape)
        return cv2.bitwise_and(normalized, allowed)

    def _filter_selection_erase_residual_mask(
        self,
        residual_mask: np.ndarray,
        selection_mask: np.ndarray,
    ) -> np.ndarray:
        residual = self._normalize_advanced_erase_mask(residual_mask, residual_mask.shape)
        selection = self._normalize_advanced_erase_mask(selection_mask, residual.shape)
        component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(residual, connectivity=8)
        if component_count <= 1:
            return cv2.bitwise_and(residual, selection)

        selection_area = max(int(cv2.countNonZero(selection)), 1)
        filtered = np.zeros(residual.shape, dtype=np.uint8)
        for label in range(1, component_count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            x = int(stats[label, cv2.CC_STAT_LEFT])
            y = int(stats[label, cv2.CC_STAT_TOP])
            width = int(stats[label, cv2.CC_STAT_WIDTH])
            height = int(stats[label, cv2.CC_STAT_HEIGHT])
            bbox_area = max(width * height, 1)
            fill_ratio = float(area) / float(bbox_area)
            if bbox_area > max(512, int(selection_area * 0.32)) and fill_ratio < 0.20:
                continue
            if area > int(selection_area * 0.48):
                continue
            filtered[labels == label] = 255
        return cv2.bitwise_and(filtered, selection)

    def _composite_selection_erase_result(
        self,
        base_rgb: np.ndarray,
        edited_rgb: np.ndarray,
        selection_mask: np.ndarray,
    ) -> tuple[np.ndarray, float, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        edited_rgb = self._normalize_advanced_erase_edited_image(base_rgb, edited_rgb)
        selection = self._normalize_advanced_erase_mask(selection_mask, base_rgb.shape)
        if not np.any(selection):
            empty = np.zeros(base_rgb.shape[:2], dtype=np.uint8)
            return base_rgb.copy(), 0.0, empty, empty, empty, empty

        composite_rgb = base_rgb.copy()
        precise_mask = np.zeros(base_rgb.shape[:2], dtype=np.uint8)
        model_change_mask = np.zeros(base_rgb.shape[:2], dtype=np.uint8)
        text_mask = np.zeros(base_rgb.shape[:2], dtype=np.uint8)
        residual_mask = np.zeros(base_rgb.shape[:2], dtype=np.uint8)

        for x1, y1, x2, y2, component_mask in self._selection_erase_component_rois(selection):
            base_roi = base_rgb[y1:y2, x1:x2]
            edited_roi = edited_rgb[y1:y2, x1:x2]
            (
                local_composite,
                _local_ratio,
                local_precise,
                local_model_change,
                local_text,
                local_residual,
            ) = self._composite_selection_erase_roi_result(base_roi, edited_roi, component_mask)

            target_roi = composite_rgb[y1:y2, x1:x2]
            target_roi[local_precise > 0] = local_composite[local_precise > 0]
            precise_mask[y1:y2, x1:x2] = cv2.bitwise_or(precise_mask[y1:y2, x1:x2], local_precise)
            model_change_mask[y1:y2, x1:x2] = cv2.bitwise_or(
                model_change_mask[y1:y2, x1:x2],
                local_model_change,
            )
            text_mask[y1:y2, x1:x2] = cv2.bitwise_or(text_mask[y1:y2, x1:x2], local_text)
            residual_mask[y1:y2, x1:x2] = cv2.bitwise_or(
                residual_mask[y1:y2, x1:x2],
                local_residual,
            )

        changed_ratio = float(cv2.countNonZero(precise_mask)) / float(precise_mask.size or 1)
        return composite_rgb, changed_ratio, precise_mask, model_change_mask, text_mask, residual_mask

    def _selection_erase_component_rois(
        self,
        selection_mask: np.ndarray,
    ) -> list[tuple[int, int, int, int, np.ndarray]]:
        selection = self._normalize_advanced_erase_mask(selection_mask, selection_mask.shape)
        component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(selection, connectivity=8)
        if component_count <= 1:
            return []

        height, width = selection.shape[:2]
        min_side = max(1, min(height, width))
        pad = max(16, min(96, int(round(min_side * 0.012))))
        rois: list[tuple[int, int, int, int, np.ndarray]] = []
        for label in range(1, component_count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area <= 0:
                continue
            left = int(stats[label, cv2.CC_STAT_LEFT])
            top = int(stats[label, cv2.CC_STAT_TOP])
            comp_width = int(stats[label, cv2.CC_STAT_WIDTH])
            comp_height = int(stats[label, cv2.CC_STAT_HEIGHT])
            x1 = max(0, left - pad)
            y1 = max(0, top - pad)
            x2 = min(width, left + comp_width + pad)
            y2 = min(height, top + comp_height + pad)
            if x2 <= x1 or y2 <= y1:
                continue
            component_mask = np.where(labels[y1:y2, x1:x2] == label, 255, 0).astype(np.uint8)
            rois.append((x1, y1, x2, y2, component_mask))
        return rois

    def _composite_selection_erase_roi_result(
        self,
        base_rgb: np.ndarray,
        edited_rgb: np.ndarray,
        selection_mask: np.ndarray,
    ) -> tuple[np.ndarray, float, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        edited_rgb = self._normalize_advanced_erase_edited_image(base_rgb, edited_rgb)
        selection = self._normalize_advanced_erase_mask(selection_mask, base_rgb.shape)
        model_change_mask = self._clip_advanced_erase_mask(
            self._build_advanced_erase_change_mask(base_rgb, edited_rgb),
            selection,
        )
        text_mask = self._build_selection_erase_text_mask(base_rgb, selection)
        replace_mask = selection

        if not np.any(replace_mask):
            empty = np.zeros(base_rgb.shape[:2], dtype=np.uint8)
            return base_rgb.copy(), 0.0, empty, model_change_mask, text_mask, empty

        min_side = max(1, min(base_rgb.shape[:2]))
        blur_size = max(3, min(15, int(round(min_side * 0.01)) | 1))
        alpha = cv2.GaussianBlur(replace_mask, (blur_size, blur_size), 0)
        alpha = cv2.bitwise_and(alpha, replace_mask)
        alpha[replace_mask > 0] = np.maximum(alpha[replace_mask > 0], 192)
        alpha_f = alpha[:, :, None].astype(np.float32) / 255.0
        composite = (
            base_rgb.astype(np.float32) * (1.0 - alpha_f)
            + edited_rgb.astype(np.float32) * alpha_f
        )
        composite_rgb = np.clip(composite, 0, 255).astype(np.uint8)

        residual_mask = np.zeros(base_rgb.shape[:2], dtype=np.uint8)
        if np.any(text_mask):
            diff_rgb = cv2.absdiff(base_rgb[:, :, :3], edited_rgb[:, :, :3])
            diff_gray = cv2.cvtColor(diff_rgb, cv2.COLOR_RGB2GRAY)
            base_gray = cv2.cvtColor(base_rgb[:, :, :3], cv2.COLOR_RGB2GRAY)
            edited_gray = cv2.cvtColor(edited_rgb[:, :, :3], cv2.COLOR_RGB2GRAY)
            unchanged = (diff_gray <= 10) & (text_mask > 0)
            stubborn_dark = (
                (diff_gray <= 28)
                & (base_gray <= 170)
                & (edited_gray <= 128)
                & (text_mask > 0)
            )
            remaining_dark_text = (
                (base_gray <= 190)
                & (edited_gray <= 168)
                & (text_mask > 0)
            )
            residual_mask = np.where(unchanged | stubborn_dark | remaining_dark_text, 255, 0).astype(np.uint8)
            residual_mask = self._filter_selection_erase_residual_mask(residual_mask, selection)
            if np.any(residual_mask):
                min_side = max(1, min(base_rgb.shape[:2]))
                close_size = max(3, min(13, int(round(min_side * 0.004)) | 1))
                close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size))
                residual_mask = cv2.morphologyEx(residual_mask, cv2.MORPH_CLOSE, close_kernel, iterations=1)
                residual_mask = cv2.bitwise_and(residual_mask, selection)
                radius = int(max(3, min(11, round(min_side * 0.006))))
                inpainted_rgb = cv2.inpaint(base_rgb, residual_mask, radius, cv2.INPAINT_TELEA)
                residual_alpha = self._advanced_erase_alpha_from_mask(residual_mask)
                residual_alpha_f = residual_alpha[:, :, None].astype(np.float32) / 255.0
                composite_rgb = np.clip(
                    composite_rgb.astype(np.float32) * (1.0 - residual_alpha_f)
                    + inpainted_rgb.astype(np.float32) * residual_alpha_f,
                    0,
                    255,
                ).astype(np.uint8)

        changed_ratio = float(cv2.countNonZero(replace_mask)) / float(replace_mask.size or 1)
        return composite_rgb, changed_ratio, replace_mask, model_change_mask, text_mask, residual_mask

    def _normalize_advanced_erase_mask(
        self,
        mask: np.ndarray,
        image_shape: tuple[int, ...],
    ) -> np.ndarray:
        normalized = np.asarray(mask)
        if normalized.ndim == 3:
            normalized = cv2.cvtColor(normalized[:, :, :3], cv2.COLOR_RGB2GRAY)
        if normalized.ndim != 2:
            raise RuntimeError("高级擦除差异 mask 格式异常。")
        target_shape = image_shape[:2]
        if normalized.shape[:2] != target_shape:
            normalized = cv2.resize(
                normalized,
                (target_shape[1], target_shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )
        normalized = normalized.astype(np.uint8, copy=False)
        _, normalized = cv2.threshold(normalized, 0, 255, cv2.THRESH_BINARY)
        return normalized.astype(np.uint8)

    def _normalize_advanced_erase_edited_image(
        self,
        source_rgb: np.ndarray,
        edited_rgb: np.ndarray,
    ) -> np.ndarray:
        normalized = np.asarray(edited_rgb)
        if normalized.ndim == 2:
            normalized = cv2.cvtColor(normalized, cv2.COLOR_GRAY2RGB)
        if normalized.ndim != 3:
            raise RuntimeError("高级擦除返回图片格式异常。")
        if normalized.shape[2] > 3:
            normalized = normalized[:, :, :3]
        if normalized.dtype != np.uint8:
            normalized = np.clip(normalized, 0, 255).astype(np.uint8)
        if normalized.shape[:2] != source_rgb.shape[:2]:
            normalized = cv2.resize(
                normalized,
                (source_rgb.shape[1], source_rgb.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )
        return normalized.copy()

    def _build_advanced_erase_change_mask(self, source_rgb: np.ndarray, edited_rgb: np.ndarray) -> np.ndarray:
        source_rgb = np.asarray(source_rgb)
        edited_rgb = np.asarray(edited_rgb)
        if source_rgb.shape[:2] != edited_rgb.shape[:2]:
            edited_rgb = cv2.resize(
                edited_rgb,
                (source_rgb.shape[1], source_rgb.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )
        if source_rgb.ndim != 3 or edited_rgb.ndim != 3:
            raise RuntimeError("高级擦除输入图片格式异常。")

        diff_rgb = cv2.absdiff(source_rgb[:, :, :3], edited_rgb[:, :, :3])
        gray_diff = cv2.cvtColor(diff_rgb, cv2.COLOR_RGB2GRAY)
        max_diff = np.max(diff_rgb, axis=2).astype(np.uint8)
        diff = np.maximum(gray_diff, max_diff)
        diff = cv2.GaussianBlur(diff, (5, 5), 0)

        mask = self._threshold_advanced_erase_diff(diff, 12)
        changed_ratio = float(cv2.countNonZero(mask)) / float(mask.size or 1)
        if changed_ratio > self.ADVANCED_ERASE_MAX_CHANGED_RATIO:
            for threshold in (20, 28, 36, 48):
                stricter_mask = self._threshold_advanced_erase_diff(diff, threshold)
                stricter_ratio = float(cv2.countNonZero(stricter_mask)) / float(stricter_mask.size or 1)
                mask = stricter_mask
                changed_ratio = stricter_ratio
                if changed_ratio <= self.ADVANCED_ERASE_MAX_CHANGED_RATIO:
                    break

        return mask

    def _build_advanced_erase_allowed_mask(
        self,
        source_rgb: np.ndarray,
        page_cache_dir: Path,
        config: dict[str, Any],
        stored_name: str,
        session: dict[str, Any] | None,
    ) -> tuple[np.ndarray | None, int]:
        regions: list[Any] = []
        with contextlib.suppress(Exception):
            regions = self._prepare_cached_regions_for_edit(
                source_rgb,
                page_cache_dir,
                config,
                stored_name,
                session=session,
            )
        if not regions:
            with contextlib.suppress(Exception):
                regions = self._load_cached_regions(page_cache_dir)
                self._assign_region_keys(regions, stored_name)

        if not regions:
            return None, 0

        allowed = np.zeros(source_rgb.shape[:2], dtype=np.uint8)
        conservative_allowed = np.zeros(source_rgb.shape[:2], dtype=np.uint8)
        fallback_allowed = np.zeros(source_rgb.shape[:2], dtype=np.uint8)
        allowed_region_count = 0
        for region in regions:
            if bool(getattr(region, "skip_translation", False)) or bool(getattr(region, "disabled_region", False)):
                continue
            region_mask, conservative_mask, fallback_mask = self._build_advanced_erase_region_container_masks(
                source_rgb,
                region,
            )
            if np.any(region_mask):
                allowed = cv2.bitwise_or(allowed, region_mask)
                conservative_allowed = cv2.bitwise_or(conservative_allowed, conservative_mask)
                fallback_allowed = cv2.bitwise_or(fallback_allowed, fallback_mask)
                allowed_region_count += 1

        if not np.any(allowed):
            return allowed, 0

        close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        allowed = cv2.morphologyEx(allowed, cv2.MORPH_CLOSE, close_kernel, iterations=1)
        conservative_allowed = cv2.morphologyEx(conservative_allowed, cv2.MORPH_CLOSE, close_kernel, iterations=1)
        fallback_allowed = cv2.morphologyEx(fallback_allowed, cv2.MORPH_CLOSE, close_kernel, iterations=1)
        if self._advanced_erase_allowed_mask_is_overbroad(allowed):
            allowed = conservative_allowed
        if self._advanced_erase_allowed_mask_is_overbroad(allowed):
            allowed = fallback_allowed
        return allowed, allowed_region_count

    def _build_advanced_erase_model_container_mask(
        self,
        source_rgb: np.ndarray,
        marker_rgb: np.ndarray,
    ) -> tuple[np.ndarray | None, int]:
        marker_rgb = self._normalize_advanced_erase_edited_image(source_rgb, marker_rgb)
        gray = cv2.cvtColor(marker_rgb, cv2.COLOR_RGB2GRAY)
        hsv = cv2.cvtColor(marker_rgb, cv2.COLOR_RGB2HSV)
        red = marker_rgb[:, :, 0]
        green = marker_rgb[:, :, 1]
        blue = marker_rgb[:, :, 2]
        chroma_green = (green >= 170) & (red <= 100) & (blue <= 110) & (hsv[:, :, 1] >= 90)
        if int(np.count_nonzero(chroma_green)) >= max(64, int(chroma_green.size * 0.00002)):
            mask = np.where(chroma_green, 255, 0).astype(np.uint8)
        else:
            bright_neutral = (gray >= 245) & (hsv[:, :, 1] <= 42)
            mask = np.where(bright_neutral, 255, 0).astype(np.uint8)

        scale = max(source_rgb.shape[:2]) / 3400.0
        close_size = max(5, int(round(11 * scale)) | 1)
        close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=2)
        mask = self._filter_advanced_erase_container_components(mask)
        if not np.any(mask):
            return None, 0

        dilate_size = max(3, int(round(7 * scale)) | 1)
        dilate_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_size, dilate_size))
        mask = cv2.dilate(mask, dilate_kernel, iterations=1)
        if self._advanced_erase_allowed_mask_is_overbroad(mask):
            return None, 0
        component_count, _labels, _stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        return mask, max(component_count - 1, 0)

    def _filter_advanced_erase_container_components(self, mask: np.ndarray) -> np.ndarray:
        mask = self._normalize_advanced_erase_mask(mask, mask.shape)
        component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if component_count <= 1:
            return mask
        page_area = max(mask.shape[0] * mask.shape[1], 1)
        min_area = max(500, int(page_area * 0.00005))
        filtered = np.zeros(mask.shape, dtype=np.uint8)
        for label in range(1, component_count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            width = int(stats[label, cv2.CC_STAT_WIDTH])
            height = int(stats[label, cv2.CC_STAT_HEIGHT])
            if area < min_area:
                continue
            if area > int(page_area * 0.22):
                continue
            if width > int(mask.shape[1] * 0.85) or height > int(mask.shape[0] * 0.85):
                continue
            component = np.where(labels == label, 255, 0).astype(np.uint8)
            component = self._fill_binary_mask_holes(component)
            filtered = cv2.bitwise_or(filtered, component)
        return filtered

    def _select_advanced_erase_allowed_mask(
        self,
        model_mask: np.ndarray | None,
        region_mask: np.ndarray | None,
    ) -> tuple[np.ndarray | None, str]:
        if model_mask is not None and region_mask is not None:
            combined = cv2.bitwise_or(
                self._normalize_advanced_erase_mask(model_mask, model_mask.shape),
                self._normalize_advanced_erase_mask(region_mask, model_mask.shape),
            )
            if not self._advanced_erase_allowed_mask_is_overbroad(combined):
                return combined, "model_container_region"
        if model_mask is not None and not self._advanced_erase_allowed_mask_is_overbroad(model_mask):
            return model_mask, "model_container"
        if region_mask is not None and not self._advanced_erase_allowed_mask_is_overbroad(region_mask):
            return region_mask, "region_replace"
        return None, "diff_only"

    def _clean_advanced_erase_white_container_residue(
        self,
        source_rgb: np.ndarray,
        composite_rgb: np.ndarray,
        mask: np.ndarray,
    ) -> np.ndarray:
        mask = self._normalize_advanced_erase_mask(mask, source_rgb.shape)
        if not np.any(mask):
            return composite_rgb

        gray = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2GRAY)
        hsv = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2HSV)
        component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if component_count <= 1:
            return composite_rgb

        cleaned = composite_rgb.copy()
        page_area = max(mask.shape[0] * mask.shape[1], 1)
        for label in range(1, component_count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area < max(96, int(page_area * 0.00001)):
                continue
            component = labels == label
            component_gray = gray[component]
            component_sat = hsv[:, :, 1][component]
            if component_gray.size == 0:
                continue
            bright_ratio = float(np.mean((component_gray >= 225) & (component_sat <= 64)))
            median_gray = float(np.median(component_gray))
            if bright_ratio < 0.58 or median_gray < 218:
                continue

            x = int(stats[label, cv2.CC_STAT_LEFT])
            y = int(stats[label, cv2.CC_STAT_TOP])
            width = int(stats[label, cv2.CC_STAT_WIDTH])
            height = int(stats[label, cv2.CC_STAT_HEIGHT])
            local = np.where(component[y:y + height, x:x + width], 255, 0).astype(np.uint8)
            padded_local = cv2.copyMakeBorder(local, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
            distance = cv2.distanceTransform(padded_local, cv2.DIST_L2, 5)[1:-1, 1:-1]
            distance_threshold = max(3.0, min(18.0, float(min(width, height)) * 0.055))
            core = np.where(distance >= distance_threshold, 255, 0).astype(np.uint8)
            if not np.any(core):
                core = local

            source_roi = source_rgb[y:y + height, x:x + width]
            gray_roi = gray[y:y + height, x:x + width]
            sat_roi = hsv[y:y + height, x:x + width, 1]
            sample_pixels = source_roi[(local > 0) & (gray_roi >= 235) & (sat_roi <= 56)]
            if sample_pixels.size:
                fill_color = np.percentile(sample_pixels.reshape(-1, 3), 85, axis=0)
            else:
                fill_color = np.array([255, 255, 255], dtype=np.float32)
            fill_color = np.clip(fill_color, 242, 255).astype(np.uint8)

            target = cleaned[y:y + height, x:x + width]
            target[core > 0] = fill_color
            cleaned[y:y + height, x:x + width] = target
        return cleaned

    def _build_advanced_erase_region_container_mask(self, source_rgb: np.ndarray, region: Any) -> np.ndarray:
        region_mask, _conservative_mask, _fallback_mask = self._build_advanced_erase_region_container_masks(
            source_rgb,
            region,
        )
        return region_mask

    def _build_advanced_erase_region_container_masks(
        self,
        source_rgb: np.ndarray,
        region: Any,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        fallback_mask = self._build_region_mask(
            region,
            source_rgb.shape,
            dilation_scale=0.42,
            dilation_min=8,
            dilation_max=64,
        )
        bright_container = self._detect_bright_text_container_mask(source_rgb, region)

        region_mask = fallback_mask
        if bright_container is not None and np.any(bright_container):
            region_mask = cv2.bitwise_or(region_mask, bright_container)
        conservative_mask = region_mask.copy()
        fallback_area = max(int(cv2.countNonZero(fallback_mask)), 1)
        bright_area = int(cv2.countNonZero(bright_container)) if bright_container is not None else 0
        line_container = None
        if bright_area < fallback_area * 2 and self._region_allows_line_art_container(region):
            line_container = self._detect_line_art_text_container_mask(source_rgb, region)
        if line_container is not None and np.any(line_container):
            region_mask = cv2.bitwise_or(region_mask, line_container)
        if not self._advanced_erase_region_mask_is_plausible(region_mask, fallback_mask, source_rgb.shape):
            region_mask = conservative_mask
        if not self._advanced_erase_region_mask_is_plausible(region_mask, fallback_mask, source_rgb.shape):
            region_mask = fallback_mask
        return region_mask, conservative_mask, fallback_mask

    def _region_allows_line_art_container(self, region: Any) -> bool:
        style_values = {
            str(getattr(region, "font_style", "") or "").strip().lower(),
            str(getattr(region, "auto_font_style", "") or "").strip().lower(),
            str(getattr(region, "override_font_style", "") or "").strip().lower(),
        }
        return bool(style_values.intersection({"sfx", "handwritten", "cartoon"}))

    def _advanced_erase_region_mask_is_plausible(
        self,
        mask: np.ndarray,
        fallback_mask: np.ndarray,
        image_shape: tuple[int, ...],
    ) -> bool:
        mask = self._normalize_advanced_erase_mask(mask, image_shape)
        area = int(cv2.countNonZero(mask))
        if area <= 0:
            return False
        page_area = max(mask.shape[0] * mask.shape[1], 1)
        if area > int(page_area * 0.20):
            return False
        fallback_area = max(int(cv2.countNonZero(fallback_mask)), 1)
        if area > max(fallback_area * 80, int(page_area * 0.04)):
            return False
        component_count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        for label in range(1, component_count):
            comp_area = int(stats[label, cv2.CC_STAT_AREA])
            comp_width = int(stats[label, cv2.CC_STAT_WIDTH])
            comp_height = int(stats[label, cv2.CC_STAT_HEIGHT])
            if comp_area > int(page_area * 0.18):
                return False
            if comp_width > int(mask.shape[1] * 0.72) or comp_height > int(mask.shape[0] * 0.72):
                return False
        return True

    def _advanced_erase_allowed_mask_is_overbroad(self, mask: np.ndarray) -> bool:
        mask = self._normalize_advanced_erase_mask(mask, mask.shape)
        area = int(cv2.countNonZero(mask))
        page_area = max(mask.shape[0] * mask.shape[1], 1)
        if area > int(page_area * self.ADVANCED_ERASE_MAX_REGION_REPLACE_RATIO):
            return True
        component_count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        for label in range(1, component_count):
            comp_area = int(stats[label, cv2.CC_STAT_AREA])
            comp_width = int(stats[label, cv2.CC_STAT_WIDTH])
            comp_height = int(stats[label, cv2.CC_STAT_HEIGHT])
            if comp_area > int(page_area * 0.45):
                return True
            if (
                comp_area > int(page_area * 0.30)
                and comp_width > int(mask.shape[1] * 0.82)
                and comp_height > int(mask.shape[0] * 0.82)
            ):
                return True
        return False

    def _detect_bright_text_container_mask(self, source_rgb: np.ndarray, region: Any) -> np.ndarray | None:
        gray = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2GRAY)
        roi_info = self._advanced_erase_region_roi(source_rgb.shape, region, pad_scale=2.2, min_pad=28)
        if roi_info is None:
            return None
        roi_x1, roi_y1, roi_x2, roi_y2, local_bbox = roi_info
        roi = gray[roi_y1:roi_y2, roi_x1:roi_x2]
        if roi.size == 0:
            return None

        bright = cv2.inRange(roi, 214, 255)
        kernel_size = max(5, min(23, int(round(min(roi.shape[:2]) * 0.035)) | 1))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
        bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, kernel, iterations=2)

        local_x1, local_y1, local_x2, local_y2 = local_bbox
        core = np.zeros(roi.shape[:2], dtype=np.uint8)
        cv2.rectangle(core, (local_x1, local_y1), (local_x2, local_y2), 255, -1)
        core_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        core = cv2.dilate(core, core_kernel, iterations=1)
        core_area = max(int(cv2.countNonZero(core)), 1)
        text_area = max((local_x2 - local_x1) * (local_y2 - local_y1), 1)

        component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(bright, connectivity=8)
        best_label = 0
        best_score = 0.0
        roi_area = max(roi.shape[0] * roi.shape[1], 1)
        for label in range(1, component_count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area < max(64, int(text_area * 1.15)):
                continue
            if area > int(roi_area * 0.94):
                continue
            component = (labels == label).astype(np.uint8) * 255
            overlap = int(cv2.countNonZero(cv2.bitwise_and(component, core)))
            if overlap < max(8, int(core_area * 0.025)):
                continue
            x = int(stats[label, cv2.CC_STAT_LEFT])
            y = int(stats[label, cv2.CC_STAT_TOP])
            w = int(stats[label, cv2.CC_STAT_WIDTH])
            h = int(stats[label, cv2.CC_STAT_HEIGHT])
            if w < max(4, int((local_x2 - local_x1) * 0.7)) or h < max(4, int((local_y2 - local_y1) * 0.7)):
                continue
            score = float(overlap) * 4.0 + float(area)
            if score > best_score:
                best_label = label
                best_score = score

        if best_label <= 0:
            return None

        component = (labels == best_label).astype(np.uint8) * 255
        component = self._fill_binary_mask_holes(component)
        outline_pad = max(3, min(13, int(round(min(roi.shape[:2]) * 0.015)) | 1))
        outline_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (outline_pad, outline_pad))
        component = cv2.dilate(component, outline_kernel, iterations=1)
        mask = np.zeros(source_rgb.shape[:2], dtype=np.uint8)
        mask[roi_y1:roi_y2, roi_x1:roi_x2] = component
        return mask

    def _detect_line_art_text_container_mask(self, source_rgb: np.ndarray, region: Any) -> np.ndarray | None:
        gray = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2GRAY)
        roi_info = self._advanced_erase_region_roi(source_rgb.shape, region, pad_scale=1.8, min_pad=36)
        if roi_info is None:
            return None
        roi_x1, roi_y1, roi_x2, roi_y2, local_bbox = roi_info
        roi = gray[roi_y1:roi_y2, roi_x1:roi_x2]
        if roi.size == 0:
            return None

        blurred = cv2.GaussianBlur(roi, (3, 3), 0)
        edges = cv2.Canny(blurred, 42, 138)
        bright_strokes = cv2.inRange(roi, 226, 255)
        dark_strokes = cv2.inRange(roi, 0, 36)
        line_mask = cv2.bitwise_or(edges, cv2.bitwise_or(bright_strokes, dark_strokes))
        local_x1, local_y1, local_x2, local_y2 = local_bbox
        text_w = max(local_x2 - local_x1, 1)
        text_h = max(local_y2 - local_y1, 1)
        text_area = text_w * text_h
        close_size = max(7, min(37, int(round(max(text_w, text_h) * 0.28)) | 1))
        close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size))
        line_mask = cv2.morphologyEx(line_mask, cv2.MORPH_CLOSE, close_kernel, iterations=2)
        line_mask = cv2.dilate(line_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1)

        contours, _hierarchy = cv2.findContours(line_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        core = np.zeros(roi.shape[:2], dtype=np.uint8)
        cv2.rectangle(core, (local_x1, local_y1), (local_x2, local_y2), 255, -1)
        text_center_x = (local_x1 + local_x2) // 2
        text_center_y = (local_y1 + local_y2) // 2
        best_contour = None
        best_rect: tuple[int, int, int, int] | None = None
        best_score = 0.0
        roi_area = max(roi.shape[0] * roi.shape[1], 1)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = max(int(w * h), 1)
            if area < max(96, int(text_area * 1.35)):
                continue
            if area > int(roi_area * 0.96):
                continue
            if w < int(text_w * 0.85) or h < int(text_h * 0.85):
                continue
            contour_mask = np.zeros(roi.shape[:2], dtype=np.uint8)
            cv2.drawContours(contour_mask, [contour], -1, 255, thickness=-1)
            overlap = int(cv2.countNonZero(cv2.bitwise_and(contour_mask, core)))
            bbox_overlap = max(0, min(x + w, local_x2) - max(x, local_x1)) * max(
                0,
                min(y + h, local_y2) - max(y, local_y1),
            )
            contains_text_center = x <= text_center_x <= x + w and y <= text_center_y <= y + h
            if overlap <= 0 and bbox_overlap <= 0 and not contains_text_center:
                continue
            contour_area = max(float(cv2.contourArea(contour)), 1.0)
            score = float(overlap) * 5.0 + float(bbox_overlap) * 2.0 + contour_area + float(area) * 0.15
            if contains_text_center:
                score += float(text_area)
            if score > best_score:
                best_contour = contour
                best_rect = (x, y, w, h)
                best_score = score

        if best_contour is None or best_rect is None:
            return None

        component = np.zeros(roi.shape[:2], dtype=np.uint8)
        cv2.drawContours(component, [best_contour], -1, 255, thickness=-1)
        x, y, w, h = best_rect
        cv2.rectangle(component, (x, y), (x + w, y + h), 255, -1)
        component = self._fill_binary_mask_holes(component)
        component = cv2.dilate(component, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1)
        mask = np.zeros(source_rgb.shape[:2], dtype=np.uint8)
        mask[roi_y1:roi_y2, roi_x1:roi_x2] = component
        return mask

    def _advanced_erase_region_roi(
        self,
        image_shape: tuple[int, ...],
        region: Any,
        pad_scale: float,
        min_pad: int,
    ) -> tuple[int, int, int, int, tuple[int, int, int, int]] | None:
        height, width = image_shape[:2]
        x1, y1, x2, y2 = self._region_bbox(region)
        x1 = max(0, min(width - 1, x1))
        x2 = max(0, min(width, x2))
        y1 = max(0, min(height - 1, y1))
        y2 = max(0, min(height, y2))
        if x2 <= x1 or y2 <= y1:
            return None

        box_w = max(x2 - x1, 1)
        box_h = max(y2 - y1, 1)
        font_size = max(float(getattr(region, "font_size", 0) or 0), 12.0)
        pad_x = int(max(min_pad, box_w * pad_scale, font_size * 2.5))
        pad_y = int(max(min_pad, box_h * pad_scale, font_size * 2.5))
        roi_x1 = max(0, x1 - pad_x)
        roi_y1 = max(0, y1 - pad_y)
        roi_x2 = min(width, x2 + pad_x)
        roi_y2 = min(height, y2 + pad_y)
        return roi_x1, roi_y1, roi_x2, roi_y2, (x1 - roi_x1, y1 - roi_y1, x2 - roi_x1, y2 - roi_y1)

    def _fill_binary_mask_holes(self, mask: np.ndarray) -> np.ndarray:
        mask = self._normalize_advanced_erase_mask(mask, mask.shape)
        padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
        flood = padded.copy()
        flood_mask = np.zeros((flood.shape[0] + 2, flood.shape[1] + 2), dtype=np.uint8)
        cv2.floodFill(flood, flood_mask, (0, 0), 255)
        holes = cv2.bitwise_not(flood)
        filled = cv2.bitwise_or(padded, holes)
        return filled[1:-1, 1:-1].astype(np.uint8)

    def _advanced_erase_final_mask(
        self,
        raw_diff_mask: np.ndarray,
        allowed_mask: np.ndarray | None,
    ) -> np.ndarray:
        raw_diff_mask = self._normalize_advanced_erase_mask(raw_diff_mask, raw_diff_mask.shape)
        if allowed_mask is None:
            return raw_diff_mask
        allowed_mask = self._normalize_advanced_erase_mask(allowed_mask, raw_diff_mask.shape)
        return allowed_mask

    def _threshold_advanced_erase_diff(self, diff: np.ndarray, threshold: int) -> np.ndarray:
        _, mask = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
        mask = mask.astype(np.uint8)
        close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=1)
        mask = self._filter_advanced_erase_mask_components(mask)

        min_side = max(1, min(mask.shape[:2]))
        dilate_size = max(3, min(21, int(round(min_side * 0.008)) | 1))
        dilate_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_size, dilate_size))
        return cv2.dilate(mask, dilate_kernel, iterations=1)

    def _filter_advanced_erase_mask_components(self, mask: np.ndarray) -> np.ndarray:
        component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if component_count <= 1:
            return mask
        min_area = max(8, int(mask.size * 0.000004))
        filtered = np.zeros(mask.shape, dtype=np.uint8)
        for label in range(1, component_count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area >= min_area:
                filtered[labels == label] = 255
        return filtered

    def _advanced_erase_alpha_from_mask(self, mask: np.ndarray) -> np.ndarray:
        min_side = max(1, min(mask.shape[:2]))
        blur_size = max(3, min(17, int(round(min_side * 0.006)) | 1))
        mask = self._normalize_advanced_erase_mask(mask, mask.shape)
        alpha = cv2.GaussianBlur(mask, (blur_size, blur_size), 0)
        alpha[mask > 0] = 255
        alpha[alpha < 4] = 0
        return alpha.astype(np.uint8)

    def _prepare_ai_cleanup_inputs(
        self,
        source_crop: np.ndarray,
        guide_crop: np.ndarray,
        mode: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        if mode == "seedream-image":
            return source_crop, guide_crop

        height, width = source_crop.shape[:2]
        longest_edge = max(height, width)
        if longest_edge <= self.IMAGE_CLEANUP_MAX_EDGE:
            return source_crop, guide_crop

        scale = self.IMAGE_CLEANUP_MAX_EDGE / float(longest_edge)
        resized_width = max(256, int(round(width * scale)))
        resized_height = max(256, int(round(height * scale)))
        resized_size = (resized_width, resized_height)
        return (
            cv2.resize(source_crop, resized_size, interpolation=cv2.INTER_AREA),
            cv2.resize(guide_crop, resized_size, interpolation=cv2.INTER_LINEAR),
        )

    def _select_ai_cleanup_regions(self, image_rgb: np.ndarray, regions: list[Any]) -> list[Any]:
        if not regions:
            return []

        hsv = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2HSV)
        saturation = hsv[:, :, 1]
        gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        scored_regions: list[tuple[float, Any]] = []

        for region in regions:
            x1, y1, x2, y2 = map(int, getattr(region, "xyxy", [0, 0, 0, 0]))
            pad = max(8, int(max(x2 - x1, y2 - y1) * 0.25))
            x1 = max(0, x1 - pad)
            y1 = max(0, y1 - pad)
            x2 = min(image_rgb.shape[1], x2 + pad)
            y2 = min(image_rgb.shape[0], y2 + pad)
            if x2 <= x1 or y2 <= y1:
                continue

            region_gray = gray[y1:y2, x1:x2]
            region_sat = saturation[y1:y2, x1:x2]
            if region_gray.size == 0:
                continue

            nonwhite_ratio = float(np.mean(region_gray < 232))
            sat_score = float(region_sat.mean()) / 255.0
            dark_ratio = float(np.mean(region_gray < 210))
            score = nonwhite_ratio * 0.7 + dark_ratio * 0.2 + sat_score * 0.4
            scored_regions.append((score, region))

        if not scored_regions:
            return []

        scored_regions.sort(key=lambda item: item[0], reverse=True)
        thresholded = [region for score, region in scored_regions if score >= 0.22]
        if thresholded:
            return thresholded
        return [region for _, region in scored_regions[: min(3, len(scored_regions))]]

    def _merge_region_bounds(self, regions: list[Any], image_shape: tuple[int, ...]) -> tuple[int, int, int, int]:
        x1 = min(int(region.xyxy[0]) for region in regions)
        y1 = min(int(region.xyxy[1]) for region in regions)
        x2 = max(int(region.xyxy[2]) for region in regions)
        y2 = max(int(region.xyxy[3]) for region in regions)
        pad = max(24, int(max(x2 - x1, y2 - y1) * 0.2))
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(image_shape[1], x2 + pad)
        y2 = min(image_shape[0], y2 + pad)
        return x1, y1, x2, y2

    def _build_ai_cleanup_guide(
        self,
        source_crop: np.ndarray,
        regions: list[Any],
        crop_x1: int,
        crop_y1: int,
    ) -> np.ndarray:
        guide = source_crop.copy()
        overlay = guide.copy()
        for region in regions:
            polygon = np.array(region.min_rect[0], dtype=np.int32)
            polygon[:, 0] -= crop_x1
            polygon[:, 1] -= crop_y1
            cv2.fillConvexPoly(overlay, polygon, color=(255, 48, 48))
            cv2.polylines(overlay, [polygon], True, color=(255, 255, 255), thickness=4)
        return cv2.addWeighted(overlay, 0.48, guide, 0.52, 0)

    def _build_ai_blend_mask(
        self,
        regions: list[Any],
        image_shape: tuple[int, ...],
        crop_x1: int,
        crop_y1: int,
        crop_x2: int,
        crop_y2: int,
    ) -> np.ndarray:
        mask = np.zeros(image_shape[:2], dtype=np.uint8)
        for region in regions:
            polygon = np.array(region.min_rect[0], dtype=np.int32)
            cv2.fillConvexPoly(mask, polygon, 255)
        mask = cv2.dilate(mask, np.ones((21, 21), dtype=np.uint8), iterations=1)
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=9, sigmaY=9)
        return mask[crop_y1:crop_y2, crop_x1:crop_x2]

    def _rerender_cache_dir(self, session_id: str) -> Path:
        return self.rerender_cache_root / session_id

    def _mask_debug_dir(self, session_id: str) -> Path:
        return self.temp_dir / f"{session_id}_mask_debug"

    def _prepare_rerender_cache_dir(self, session_id: str, reset: bool) -> Path:
        cache_dir = self._rerender_cache_dir(session_id)
        if reset:
            shutil.rmtree(cache_dir, ignore_errors=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _prepare_mask_debug_dir(self, session_id: str, reset: bool) -> Path:
        debug_dir = self._mask_debug_dir(session_id)
        if reset:
            shutil.rmtree(debug_dir, ignore_errors=True)
        debug_dir.mkdir(parents=True, exist_ok=True)
        return debug_dir

    def _style_rerender_debug_dir(self, session_id: str) -> Path:
        return self.temp_dir / f"{session_id}_style_rerender_debug"

    def _prepare_style_rerender_debug_dir(self, session_id: str, reset: bool) -> Path:
        debug_dir = self._style_rerender_debug_dir(session_id)
        if reset:
            shutil.rmtree(debug_dir, ignore_errors=True)
        debug_dir.mkdir(parents=True, exist_ok=True)
        return debug_dir

    def _count_rerenderable_pages(self, session_id: str, session: dict[str, Any]) -> int:
        cache_dir = self._session_rerender_cache_dir(session, session_id)
        return sum(
            1
            for image in session["source_images"]
            if self._has_rerenderable_page_cache(cache_dir / image["stored_name"])
        )

    def _session_rerender_cache_dir(self, session: dict[str, Any] | None, project_id: str) -> Path:
        configured = str((session or {}).get("rerender_cache_dir") or "").strip()
        if configured:
            return Path(configured)
        return self._rerender_cache_dir(project_id)

    def _session_page_cache_dir(
        self,
        session: dict[str, Any] | None,
        project_id: str,
        page_id: str,
    ) -> Path:
        return self._session_rerender_cache_dir(session, project_id) / page_id

    def _has_rerenderable_page_cache(self, page_cache_dir: Path) -> bool:
        return (
            page_cache_dir.exists()
            and (page_cache_dir / "inpainted.png").exists()
            and (page_cache_dir / "regions.json").exists()
            and self._cached_regions_json_is_readable(page_cache_dir)
        )

    def _cached_regions_json_is_readable(self, page_cache_dir: Path) -> bool:
        regions_path = page_cache_dir / "regions.json"
        if not regions_path.exists():
            return False
        try:
            payload = json.loads(regions_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Unreadable rerender regions cache. path=%s error=%s", regions_path, exc)
            return False
        return isinstance(payload, list)

    def _page_document_region_to_text_region(
        self,
        region_payload: dict[str, Any],
        config: dict[str, Any],
        stored_name: str,
    ) -> Any | None:
        region_id = str(region_payload.get("region_id") or "").strip()
        bbox = region_payload.get("bbox")
        if not region_id or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return None

        try:
            normalized_bbox = [int(round(float(value))) for value in bbox]
        except (TypeError, ValueError):
            return None

        translation_payload = region_payload.get("translation") or {}
        style_payload = region_payload.get("style") or {}
        source_text = str(region_payload.get("source_text") or "").strip()
        machine_translation = str(translation_payload.get("machine") or "").strip()
        edited_translation = str(translation_payload.get("edited") or "").strip()
        resolved_translation = str(
            translation_payload.get("resolved")
            or edited_translation
            or machine_translation
            or ""
        ).strip()

        try:
            font_size = int(round(float(style_payload.get("font_size") or 14)))
        except (TypeError, ValueError):
            font_size = 14

        payload = {
            "texts": [source_text],
            "source_text": source_text,
            "text_raw": source_text,
            "translation": resolved_translation,
            "font_size": max(8, font_size),
            "font_family": str(style_payload.get("font_path") or style_payload.get("font_family") or ""),
            "line_spacing": float(style_payload.get("line_spacing") or 1.0),
            "letter_spacing": float(style_payload.get("letter_spacing") or 1.0),
            "direction": str(region_payload.get("direction") or "auto"),
            "_direction": str(region_payload.get("direction") or "auto"),
            "alignment": str(style_payload.get("alignment") or "auto"),
            "_alignment": str(style_payload.get("alignment") or "auto"),
            "target_lang": str(config.get("target_lang") or ""),
            "language": "unknown",
            "fg_color": (0, 0, 0),
            "bg_color": (255, 255, 255),
            "_bounding_rect": normalized_bbox,
            "lines": self._manual_region_lines(normalized_bbox),
            "translation_region_key": region_id,
            "style_region_key": region_id,
        }

        region = self._deserialize_text_region(payload)
        region.translation_region_key = region_id
        region.style_region_key = region_id
        region.source_text = source_text
        region.text_raw = source_text
        region.machine_translation = machine_translation or resolved_translation
        region.translation_override = edited_translation
        region.translation = resolved_translation
        region.skip_translation = bool((region_payload.get("flags") or {}).get("keep_original"))
        region.disabled_region = bool((region_payload.get("flags") or {}).get("disabled"))
        region.preserve_background = bool((region_payload.get("flags") or {}).get("preserve_background"))
        return region

    def _ensure_editable_page_cache(
        self,
        session_id: str,
        session: dict[str, Any],
        stored_name: str,
        config: dict[str, Any],
        source_path: Path | None = None,
    ) -> bool:
        page_cache_dir = self._session_page_cache_dir(session, session_id, stored_name)
        resolved_source_path = source_path or (Path(session.get("source_dir") or "") / stored_name)
        if self._has_rerenderable_page_cache(page_cache_dir):
            return self._ensure_page_base_image_cache(resolved_source_path, page_cache_dir)

        if not resolved_source_path.exists():
            return False
        if not self._ensure_page_base_image_cache(resolved_source_path, page_cache_dir):
            return False

        document: dict[str, Any] = {}
        try:
            document = self.get_page_document(session_id, session, stored_name)
        except Exception as exc:
            logger.warning(
                "Could not load page document while restoring editable cache. session_id=%s page=%s error=%s",
                session_id,
                stored_name,
                exc,
            )

        raw_regions = document.get("regions") if isinstance(document, dict) else []
        if not isinstance(raw_regions, list):
            raw_regions = []
        restored_regions: list[Any] = []
        for region_payload in raw_regions or []:
            if not isinstance(region_payload, dict):
                continue
            if str(region_payload.get("kind") or "auto") in {"manual", "merged"}:
                continue
            region = self._page_document_region_to_text_region(region_payload, config, stored_name)
            if region is not None:
                restored_regions.append(region)

        has_manual_regions = bool(self._manual_regions_for_page(session, stored_name))
        if not restored_regions and not has_manual_regions:
            logger.warning(
                "No editable regions found while restoring page cache; creating empty cache so batch translation can continue. session_id=%s page=%s",
                session_id,
                stored_name,
            )

        page_cache_dir.mkdir(parents=True, exist_ok=True)
        self._save_cached_regions(page_cache_dir, restored_regions)
        restored = self._has_rerenderable_page_cache(page_cache_dir)
        if restored:
            logger.warning(
                "Restored editable cache from persisted page document. session_id=%s page=%s regions=%s",
                session_id,
                stored_name,
                len(restored_regions),
            )
        return restored

    def _ensure_page_base_image_cache(self, source_path: Path, page_cache_dir: Path) -> bool:
        page_cache_dir.mkdir(parents=True, exist_ok=True)
        inpainted_path = page_cache_dir / "inpainted.png"
        if inpainted_path.exists():
            stat = inpainted_path.stat()
            signature = (str(inpainted_path.resolve()), stat.st_size, stat.st_mtime_ns)
            with self.validated_page_base_images_lock:
                if signature in self.validated_page_base_images:
                    return True
            if cv2.imread(str(inpainted_path), cv2.IMREAD_COLOR) is not None:
                with self.validated_page_base_images_lock:
                    self.validated_page_base_images.add(signature)
                return True

        if inpainted_path.exists():
            corrupt_path = page_cache_dir / f"inpainted.corrupt-{uuid.uuid4().hex[:8]}.png"
            try:
                inpainted_path.rename(corrupt_path)
                logger.warning("Moved unreadable page base cache aside. source=%s target=%s", inpainted_path, corrupt_path)
            except OSError:
                pass

        source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if source_bgr is None:
            return False

        backup_path = self._advanced_erase_traditional_backup_path(page_cache_dir)
        backup_bgr = cv2.imread(str(backup_path), cv2.IMREAD_COLOR) if backup_path.exists() else None
        if backup_bgr is not None:
            self._save_rgb_image_atomic(inpainted_path, cv2.cvtColor(backup_bgr, cv2.COLOR_BGR2RGB))
            restored = cv2.imread(str(inpainted_path), cv2.IMREAD_COLOR) is not None
            if restored:
                stat = inpainted_path.stat()
                with self.validated_page_base_images_lock:
                    self.validated_page_base_images.add((str(inpainted_path.resolve()), stat.st_size, stat.st_mtime_ns))
            return restored

        self._save_rgb_image_atomic(inpainted_path, cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB))
        restored = cv2.imread(str(inpainted_path), cv2.IMREAD_COLOR) is not None
        if restored:
            stat = inpainted_path.stat()
            with self.validated_page_base_images_lock:
                self.validated_page_base_images.add((str(inpainted_path.resolve()), stat.st_size, stat.st_mtime_ns))
        return restored

    def _ensure_vendor_import_path(self) -> None:
        vendor_root = str(self.base_dir / "manga-image-translator")
        if vendor_root not in sys.path:
            sys.path.insert(0, vendor_root)

    def _reload_vendor_translator_modules(self) -> None:
        self._ensure_vendor_import_path()
        importlib.invalidate_caches()
        for module_name in (
            "manga_translator.translators.custom_openai",
            "manga_translator.translators",
        ):
            module = sys.modules.get(module_name)
            if module is not None:
                importlib.reload(module)

    def _load_cached_regions(self, page_cache_dir: Path) -> list[Any]:
        regions_path = page_cache_dir / "regions.json"
        try:
            region_payloads = json.loads(regions_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load cached regions. path=%s error=%s", regions_path, exc)
            return []
        if not isinstance(region_payloads, list):
            logger.warning("Cached regions payload is not a list. path=%s", regions_path)
            return []
        regions: list[Any] = []
        for index, payload in enumerate(region_payloads):
            if not isinstance(payload, dict):
                print(f"[WARN] Skipping non-dict cached region payload at {regions_path}#{index}")
                continue
            try:
                regions.append(self._deserialize_text_region(payload))
            except Exception as exc:
                print(f"[WARN] Failed to deserialize cached region at {regions_path}#{index}: {exc}")
        return regions

    def _to_json_compatible(self, value: Any) -> Any:
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {str(key): self._to_json_compatible(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._to_json_compatible(item) for item in value]
        return value

    def _rgb_color_payload(self, value: Any, fallback: tuple[int, int, int]) -> list[int]:
        compatible = self._to_json_compatible(value)
        if compatible is None:
            compatible = fallback
        if isinstance(compatible, str):
            hex_value = compatible.strip().lstrip("#")
            if len(hex_value) == 3:
                hex_value = "".join(char * 2 for char in hex_value)
            if len(hex_value) == 6:
                try:
                    compatible = [
                        int(hex_value[0:2], 16),
                        int(hex_value[2:4], 16),
                        int(hex_value[4:6], 16),
                    ]
                except ValueError:
                    compatible = fallback
        if isinstance(compatible, np.ndarray):
            compatible = compatible.tolist()
        if not isinstance(compatible, (list, tuple)) or len(compatible) < 3:
            compatible = fallback

        channel_values: list[int] = []
        for channel in list(compatible)[:3]:
            try:
                normalized = int(round(float(channel)))
            except (TypeError, ValueError):
                normalized = 0
            channel_values.append(max(0, min(255, normalized)))
        while len(channel_values) < 3:
            channel_values.append(0)
        return channel_values

    def _sanitize_auto_text_background_color(
        self,
        region: Any,
        layout_override: dict[str, Any] | None = None,
    ) -> None:
        layout_override = layout_override or {}
        if "bg_color" in layout_override or "stroke_color" in layout_override:
            return

        fg_color = self._rgb_color_payload(getattr(region, "fg_colors", None), (0, 0, 0))
        bg_color = self._rgb_color_payload(getattr(region, "bg_colors", None), (255, 255, 255))
        if max(fg_color) <= 88 and max(bg_color) <= 64:
            region.bg_colors = np.array((255, 255, 255), dtype=np.uint8)
            region.adjust_bg_color = False

    def _normalize_float_range(
        self,
        raw_value: Any,
        minimum: float,
        maximum: float,
        *,
        digits: int = 3,
    ) -> float | None:
        if raw_value is None:
            return None
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(value):
            return None
        return round(float(np.clip(value, minimum, maximum)), digits)

    def _normalize_rotation_degrees(self, raw_value: Any) -> float | None:
        return self._normalize_float_range(
            raw_value,
            self.STYLE_ROTATION_MIN,
            self.STYLE_ROTATION_MAX,
            digits=2,
        )

    def _normalize_stroke_strength(self, raw_value: Any) -> float | None:
        return self._normalize_float_range(
            raw_value,
            self.STYLE_STROKE_MIN,
            self.STYLE_STROKE_MAX,
            digits=3,
        )

    def _normalize_letter_spacing(self, raw_value: Any) -> float | None:
        return self._normalize_float_range(
            raw_value,
            self.STYLE_LETTER_SPACING_MIN,
            self.STYLE_LETTER_SPACING_MAX,
            digits=3,
        )

    def _normalize_line_spacing(self, raw_value: Any) -> float | None:
        return self._normalize_float_range(
            raw_value,
            self.STYLE_LINE_SPACING_MIN,
            self.STYLE_LINE_SPACING_MAX,
            digits=3,
        )

    def _save_cached_regions(self, page_cache_dir: Path, regions: list[Any]) -> None:
        serialized_regions: list[dict[str, Any]] = []
        for region in regions:
            if not hasattr(region, "to_dict"):
                continue
            serialized_regions.append(self._to_json_compatible(region.to_dict()))

        regions_path = page_cache_dir / "regions.json"
        regions_path.write_text(
            json.dumps(serialized_regions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _ensure_manual_regions_store(self, session: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        manual_regions = session.get("manual_regions")
        if not isinstance(manual_regions, dict):
            manual_regions = {}
            session["manual_regions"] = manual_regions
        return manual_regions

    def _manual_regions_for_page(self, session: dict[str, Any] | None, stored_name: str) -> list[dict[str, Any]]:
        if not session:
            return []
        manual_regions = self._ensure_manual_regions_store(session)
        page_regions = manual_regions.get(stored_name)
        if not isinstance(page_regions, list):
            return []
        return [payload for payload in page_regions if isinstance(payload, dict)]

    def _normalize_manual_bbox(self, bbox: Any, image_width: int, image_height: int) -> list[int]:
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise ValueError("补漏框坐标无效。")
        try:
            x1, y1, x2, y2 = [int(round(float(value))) for value in bbox]
        except (TypeError, ValueError) as exc:
            raise ValueError("补漏框坐标无效。") from exc

        left = max(0, min(x1, x2))
        top = max(0, min(y1, y2))
        right = min(image_width, max(x1, x2))
        bottom = min(image_height, max(y1, y2))
        if right - left < 8 or bottom - top < 8:
            raise ValueError("补漏框太小了，至少需要大于 8 像素。")
        return [left, top, right, bottom]

    def _direction_from_bbox(self, bbox: list[int]) -> str:
        x1, y1, x2, y2 = bbox
        width = max(x2 - x1, 1)
        height = max(y2 - y1, 1)
        return "v" if height > width * 1.15 else "h"

    def _default_direction_for_target_lang(self, target_lang: str | None) -> str:
        normalized = str(target_lang or "").strip().upper()
        if normalized in {"CHS", "CHT"}:
            return "v"
        return "auto"

    def _resolve_region_direction(
        self,
        bbox: list[int],
        region_direction: Any,
        target_lang: str | None,
        layout_override: dict[str, Any] | None = None,
    ) -> str:
        direction_override = self._normalize_direction_override((layout_override or {}).get("direction"))
        if direction_override == "vertical":
            return "v"
        if direction_override == "horizontal":
            return "h"

        default_direction = self._default_direction_for_target_lang(target_lang)
        if default_direction != "auto":
            return default_direction

        resolved = str(region_direction or "").strip().lower()
        if resolved.startswith("v"):
            return "v"
        if resolved.startswith("h"):
            return "h"
        return self._direction_from_bbox(bbox)

    def _manual_region_lines(self, bbox: list[int]) -> list[list[list[int]]]:
        x1, y1, x2, y2 = bbox
        return [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]]

    def _build_manual_region_payload(
        self,
        stored_name: str,
        bbox: list[int],
        source_text: str,
        translation: str,
        target_lang: str,
        direction: str | None = None,
        font_size: float | None = None,
        fg_color: tuple[int, int, int] | list[int] | None = None,
        bg_color: tuple[int, int, int] | list[int] | None = None,
    ) -> dict[str, Any]:
        x1, y1, x2, y2 = bbox
        width = max(x2 - x1, 1)
        height = max(y2 - y1, 1)
        resolved_direction = self._resolve_region_direction(
            bbox,
            direction,
            target_lang,
        )
        resolved_font_size = int(round(font_size if font_size is not None else max(min(width, height), 14)))
        return {
            "id": f"manual::{stored_name}::{uuid.uuid4().hex}",
            "stored_name": stored_name,
            "bbox": [int(x1), int(y1), int(x2), int(y2)],
            "lines": self._manual_region_lines(bbox),
            "source_text": str(source_text or "").strip(),
            "machine_translation": str(translation or "").strip(),
            "translation": str(translation or "").strip(),
            "direction": resolved_direction,
            "alignment": "auto",
            "font_size": max(resolved_font_size, 8),
            "angle": 0.0,
            "letter_spacing": 1.0,
            "line_spacing": 1.0,
            "fg_color": self._rgb_color_payload(fg_color, (0, 0, 0)),
            "bg_color": self._rgb_color_payload(bg_color, (255, 255, 255)),
            "stroke_width": 0.2,
            "target_lang": target_lang,
            "manual": True,
            "created_at": time.time(),
        }

    def _manual_region_to_text_region(self, payload: dict[str, Any]) -> Any:
        self._ensure_vendor_import_path()
        from manga_translator.utils.textblock import TextBlock

        bbox = payload.get("bbox") or [0, 0, 0, 0]
        direction = self._resolve_region_direction(
            bbox,
            payload.get("direction"),
            payload.get("target_lang"),
        )
        translation = str(payload.get("translation") or payload.get("machine_translation") or "").strip()
        source_text = str(payload.get("source_text") or "").strip()
        region = TextBlock(
            lines=payload.get("lines") or self._manual_region_lines(bbox),
            texts=[source_text],
            language=payload.get("source_lang", "unknown"),
            font_size=payload.get("font_size", max(min(bbox[2] - bbox[0], bbox[3] - bbox[1]), 14)),
            angle=payload.get("angle", 0),
            translation=translation,
            fg_color=tuple(self._rgb_color_payload(payload.get("fg_color"), (0, 0, 0))),
            bg_color=tuple(self._rgb_color_payload(payload.get("bg_color"), (255, 255, 255))),
            line_spacing=payload.get("line_spacing", 1.0),
            letter_spacing=payload.get("letter_spacing", 1.0),
            font_family=payload.get("font_family", ""),
            bold=payload.get("bold", False),
            underline=payload.get("underline", False),
            italic=payload.get("italic", False),
            direction=direction,
            alignment=payload.get("alignment", "auto"),
            default_stroke_width=payload.get("stroke_width", payload.get("default_stroke_width", 0.2)),
            source_lang=payload.get("source_lang", ""),
            target_lang=payload.get("target_lang", ""),
            prob=payload.get("prob", 1.0),
        )
        region.manual_region = True
        region.manual_region_id = str(payload.get("id") or "")
        region.style_region_key = region.manual_region_id
        region.translation_region_key = region.manual_region_id
        region.source_text = source_text
        region.text_raw = source_text
        region.machine_translation = str(payload.get("machine_translation") or translation or "")
        region.translation_override = ""
        region.preserve_background = bool(payload.get("preserve_background"))
        region.allow_overlap = bool(payload.get("allow_overlap"))
        return region

    def _merge_manual_regions(
        self,
        session: dict[str, Any] | None,
        stored_name: str,
        regions: list[Any],
    ) -> list[Any]:
        manual_payloads = self._manual_regions_for_page(session, stored_name)
        if not manual_payloads:
            return regions
        merged_regions = list(regions)
        for payload in manual_payloads:
            try:
                merged_regions.append(self._manual_region_to_text_region(payload))
            except Exception as exc:
                print(f"[WARN] Failed to restore manual region {payload.get('id')}: {exc}")
        return merged_regions

    def _normalize_manual_region_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        stored_name = str(payload.get("stored_name") or "").strip()
        bbox = payload.get("bbox") or [0, 0, 0, 0]
        if not stored_name:
            raise ValueError("手动框缺少页面标识。")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise ValueError("手动框缺少有效坐标。")

        normalized = dict(payload)
        normalized["id"] = str(payload.get("id") or f"manual::{stored_name}::{uuid.uuid4().hex}")
        normalized["stored_name"] = stored_name
        normalized["bbox"] = [int(round(float(value))) for value in bbox]
        normalized["lines"] = payload.get("lines") or self._manual_region_lines(normalized["bbox"])
        normalized["source_text"] = str(payload.get("source_text") or "").strip()
        normalized["machine_translation"] = str(payload.get("machine_translation") or "").strip()
        normalized["translation"] = str(payload.get("translation") or normalized["machine_translation"]).strip()
        normalized["direction"] = self._resolve_region_direction(
            normalized["bbox"],
            payload.get("direction"),
            payload.get("target_lang"),
        )
        normalized["alignment"] = str(payload.get("alignment") or "auto")
        normalized["font_size"] = max(8, int(round(float(payload.get("font_size") or 14))))
        normalized["angle"] = self._normalize_rotation_degrees(payload.get("angle", payload.get("rotation"))) or 0.0
        normalized["letter_spacing"] = self._normalize_letter_spacing(payload.get("letter_spacing")) or 1.0
        normalized["line_spacing"] = self._normalize_line_spacing(payload.get("line_spacing")) or 1.0
        normalized["fg_color"] = self._rgb_color_payload(payload.get("fg_color"), (0, 0, 0))
        normalized["bg_color"] = self._rgb_color_payload(payload.get("bg_color"), (255, 255, 255))
        normalized["stroke_width"] = self._normalize_stroke_strength(payload.get("stroke_width")) if payload.get("stroke_width") is not None else 0.2
        normalized["target_lang"] = str(payload.get("target_lang") or "")
        normalized["manual"] = True
        normalized["created_at"] = float(payload.get("created_at") or time.time())
        if bool(payload.get("allow_overlap")):
            normalized["allow_overlap"] = True
        if bool(payload.get("preserve_background")):
            normalized["preserve_background"] = True
        if "merged_from" in payload:
            normalized["merged_from"] = [str(item) for item in (payload.get("merged_from") or []) if str(item)]
        return normalized

    def restore_manual_region(self, session: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_manual_region_payload(payload)
        manual_regions = self._ensure_manual_regions_store(session)
        stored_name = normalized["stored_name"]
        page_regions = [
            item for item in (manual_regions.get(stored_name) or [])
            if str(item.get("id") or "") != normalized["id"]
        ]
        page_regions.append(normalized)
        manual_regions[stored_name] = page_regions
        return normalized

    @contextlib.contextmanager
    def _temporary_environment(self, updates: dict[str, str]):
        sentinel = object()
        previous: dict[str, Any] = {}
        try:
            for key, value in updates.items():
                previous[key] = os.environ.get(key, sentinel)
                if value is None or value == "":
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = str(value)
            yield
        finally:
            for key, value in previous.items():
                if value is sentinel:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = str(value)

    async def _ocr_manual_region(
        self,
        source_rgb: np.ndarray,
        bbox: list[int],
        use_gpu: bool,
    ) -> dict[str, Any]:
        self._ensure_vendor_import_path()
        from manga_translator.config import Ocr, OcrConfig
        from manga_translator.ocr import dispatch as dispatch_ocr
        from manga_translator.utils import Quadrilateral

        pts = np.array(
            [[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]]],
            dtype=np.int32,
        )
        quad = Quadrilateral(pts, "", 1.0)
        recognized = await dispatch_ocr(
            Ocr.ocr48px,
            source_rgb,
            [quad],
            OcrConfig(use_mocr_merge=True, ocr=Ocr.ocr48px),
            "cuda" if use_gpu else "cpu",
            False,
        )
        if not recognized:
            return {
                "source_text": "",
                "direction": self._direction_from_bbox(bbox),
                "font_size": max(min(bbox[2] - bbox[0], bbox[3] - bbox[1]), 14),
                "fg_color": (0, 0, 0),
                "bg_color": (255, 255, 255),
            }

        region = recognized[0]
        return {
            "source_text": str(getattr(region, "text", "") or "").strip(),
            "direction": str(getattr(region, "direction", "") or self._direction_from_bbox(bbox)),
            "font_size": float(getattr(region, "font_size", 0) or max(min(bbox[2] - bbox[0], bbox[3] - bbox[1]), 14)),
            "fg_color": tuple(int(v) for v in getattr(region, "fg_colors", (0, 0, 0))),
            "bg_color": tuple(int(v) for v in getattr(region, "bg_colors", (255, 255, 255))),
        }

    async def _translate_manual_text(
        self,
        text: str,
        config: dict[str, Any],
        session_id: str,
    ) -> str:
        translations = await self._translate_text_batch([text], config, session_id)
        return translations[0] if translations else ""

    async def _translate_text_batch(
        self,
        texts: list[str],
        config: dict[str, Any],
        session_id: str,
    ) -> list[str]:
        cleaned_texts = [str(text or "").strip() for text in texts]
        if not cleaned_texts:
            return []
        if not any(cleaned_texts):
            return ["" for _ in cleaned_texts]

        if config.get("translator") == "none":
            return ["" for _ in cleaned_texts]

        self._ensure_vendor_import_path()
        self._reload_vendor_translator_modules()
        from manga_translator.config import Translator, TranslatorConfig
        from manga_translator.translators import dispatch as dispatch_translation, unload as unload_translator

        translator_key = Translator[config["translator"]]
        translator_config = TranslatorConfig(
            translator=translator_key,
            target_lang=config["target_lang"],
        )
        env_updates = self._build_env(config, session_id)

        with self._temporary_environment(env_updates):
            try:
                await unload_translator(translator_key)
            except Exception:
                pass
            try:
                translated = await dispatch_translation(
                    translator_config.translator_gen,
                    cleaned_texts,
                    translator_config=translator_config,
                    use_mtpe=False,
                    args=None,
                    device="cuda" if config.get("use_gpu") else "cpu",
                )
            finally:
                try:
                    await unload_translator(translator_key)
                except Exception:
                    pass

        normalized = [str(item or "").strip() for item in (translated or [])]
        if len(normalized) < len(cleaned_texts):
            normalized.extend([""] * (len(cleaned_texts) - len(normalized)))
        return normalized[:len(cleaned_texts)]

    async def _translate_cached_regions(
        self,
        regions: list[Any],
        config: dict[str, Any],
        session_id: str,
    ) -> None:
        pending_regions: list[Any] = []
        pending_texts: list[str] = []

        for region in regions:
            region.target_lang = config["target_lang"]
            if bool(getattr(region, "skip_translation", False)):
                continue

            override_translation = str(getattr(region, "translation_override", "") or "").strip()
            if override_translation:
                region.translation = override_translation
                continue

            source_text = self._region_source_text(region)
            if not source_text:
                region.translation = ""
                region.machine_translation = ""
                continue

            pending_regions.append(region)
            pending_texts.append(source_text)

        if not pending_texts:
            return

        translated_texts = await self._translate_text_batch(pending_texts, config, session_id)
        for region, translated_text in zip(pending_regions, translated_texts):
            resolved_translation = str(translated_text or "").strip()
            region.machine_translation = resolved_translation
            region.translation = resolved_translation

    def _sync_manual_region_translations(
        self,
        session: dict[str, Any],
        stored_name: str,
        regions: list[Any],
    ) -> None:
        manual_regions = self._ensure_manual_regions_store(session)
        page_payloads = manual_regions.get(stored_name)
        if not isinstance(page_payloads, list):
            return

        payload_by_id = {
            str(payload.get("id") or ""): payload
            for payload in page_payloads
            if isinstance(payload, dict)
        }
        for region in regions:
            if not bool(getattr(region, "manual_region", False)):
                continue
            region_id = str(getattr(region, "manual_region_id", "") or getattr(region, "translation_region_key", "") or "")
            payload = payload_by_id.get(region_id)
            if not payload:
                continue
            payload["source_text"] = self._region_source_text(region)
            payload["machine_translation"] = str(getattr(region, "machine_translation", "") or "")
            payload["translation"] = str(getattr(region, "translation", "") or "")

    def _persist_translated_regions(
        self,
        page_cache_dir: Path,
        session: dict[str, Any],
        stored_name: str,
        regions: list[Any],
    ) -> None:
        auto_regions = [region for region in regions if not bool(getattr(region, "manual_region", False))]
        self._save_cached_regions(page_cache_dir, auto_regions)
        self._sync_manual_region_translations(session, stored_name, regions)

    async def create_manual_region(
        self,
        session_id: str,
        session: dict[str, Any],
        raw_config: dict[str, Any] | None,
        stored_name: str,
        bbox: Any,
    ) -> dict[str, Any]:
        self._ensure_runtime_patches()
        config = self._normalize_config(raw_config)
        source_dir = Path(session["source_dir"])
        source_path = source_dir / stored_name
        if not source_path.exists():
            raise RuntimeError("目标页面不存在，请刷新后重试。")

        source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if source_bgr is None:
            raise RuntimeError("无法读取当前页面原图。")
        source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)
        normalized_bbox = self._normalize_manual_bbox(bbox, source_rgb.shape[1], source_rgb.shape[0])

        cache_page_dir = self._session_page_cache_dir(session, session_id, stored_name)
        if not self._ensure_page_base_image_cache(source_path, cache_page_dir):
            raise RuntimeError("无法为当前页面准备底图，请刷新后重试。")

        ocr_result = await self._ocr_manual_region(source_rgb, normalized_bbox, config.get("use_gpu", True))
        translation = ""
        if self._session_workflow_stage(session) != "detected":
            try:
                translation = await self._translate_manual_text(ocr_result.get("source_text", ""), config, session_id)
            except Exception as exc:
                print(f"[WARN] Manual region translation failed for {stored_name}: {exc}")

        payload = self._build_manual_region_payload(
            stored_name=stored_name,
            bbox=normalized_bbox,
            source_text=ocr_result.get("source_text", ""),
            translation=translation,
            target_lang=config["target_lang"],
            direction=ocr_result.get("direction"),
            font_size=ocr_result.get("font_size"),
            fg_color=ocr_result.get("fg_color"),
            bg_color=ocr_result.get("bg_color"),
        )
        manual_regions = self._ensure_manual_regions_store(session)
        manual_regions.setdefault(stored_name, []).append(payload)
        return payload

    def duplicate_region(
        self,
        project_id: str,
        session: dict[str, Any],
        stored_name: str,
        region_id: str,
        raw_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        document = self.get_page_document(project_id, session, stored_name)
        source_region = next(
            (
                region for region in (document.get("regions") or [])
                if str(region.get("region_id") or region.get("id") or "") == region_id
            ),
            None,
        )
        if not isinstance(source_region, dict):
            raise FileNotFoundError("目标文本框不存在，请刷新后重试。")

        dimensions = document.get("dimensions") or {}
        image_width = max(1, int(dimensions.get("width") or 1))
        image_height = max(1, int(dimensions.get("height") or 1))
        source_bbox = self._normalize_manual_bbox(source_region.get("bbox"), image_width, image_height)
        x1, y1, x2, y2 = source_bbox
        offset = max(8, min(24, int(round(min(image_width, image_height) * 0.012))))
        dx = offset if x2 + offset <= image_width else (-offset if x1 - offset >= 0 else 0)
        dy = offset if y2 + offset <= image_height else (-offset if y1 - offset >= 0 else 0)
        duplicated_bbox = [x1 + dx, y1 + dy, x2 + dx, y2 + dy]

        style = source_region.get("style") or {}
        flags = source_region.get("flags") or {}
        translation = source_region.get("translation") or {}
        config = self._normalize_config(raw_config)
        resolved_translation = str(
            translation.get("resolved")
            if translation.get("resolved") is not None
            else translation.get("edited")
            if translation.get("edited") is not None
            else translation.get("machine")
            or ""
        )
        payload = self._build_manual_region_payload(
            stored_name=stored_name,
            bbox=duplicated_bbox,
            source_text=str(source_region.get("source_text") or ""),
            translation=resolved_translation,
            target_lang=config["target_lang"],
            direction=str(source_region.get("direction") or "auto"),
            font_size=float(style.get("font_size") or 12),
            fg_color=style.get("fg_color"),
            bg_color=style.get("bg_color"),
        )
        payload.update({
            "alignment": str(style.get("alignment") or "auto"),
            "angle": float(style.get("rotation") or 0.0),
            "letter_spacing": float(style.get("letter_spacing") or 1.0),
            "line_spacing": float(style.get("line_spacing") or 1.0),
            "font_family": str(style.get("font_path") or ""),
            "stroke_width": float(style.get("stroke_width") if style.get("stroke_width") is not None else 0.2),
            "preserve_background": bool(flags.get("preserve_background")),
            "allow_overlap": True,
        })
        manual_regions = self._ensure_manual_regions_store(session)
        manual_regions.setdefault(stored_name, []).append(payload)

        duplicated_region_id = str(payload["id"])
        translation_overrides = dict(session.get("translation_region_overrides") or {})
        translation_overrides[duplicated_region_id] = resolved_translation
        session["translation_region_overrides"] = translation_overrides

        if bool(flags.get("keep_original")):
            skip_overrides = dict(session.get("translation_region_skip_overrides") or {})
            skip_overrides[duplicated_region_id] = True
            session["translation_region_skip_overrides"] = skip_overrides

        direction = str(source_region.get("direction") or "").strip().lower()
        layout_override: dict[str, Any] = {
            "font_size": max(8, int(round(float(style.get("font_size") or 12)))),
            "direction": "vertical" if direction.startswith("v") else "horizontal",
            "rotation": float(style.get("rotation") or 0.0),
            "stroke_width": float(style.get("stroke_width") if style.get("stroke_width") is not None else 0.2),
            "letter_spacing": float(style.get("letter_spacing") or 1.0),
            "line_spacing": float(style.get("line_spacing") or 1.0),
            "fg_color": self._rgb_color_payload(style.get("fg_color"), (0, 0, 0)),
            "bg_color": self._rgb_color_payload(style.get("bg_color"), (255, 255, 255)),
            "preserve_background": bool(flags.get("preserve_background")),
        }
        font_key = str(style.get("font_key_override") or "").strip()
        if font_key:
            layout_override["font_key"] = font_key
        layout_overrides = dict(session.get("translation_region_layout_overrides") or {})
        layout_overrides[duplicated_region_id] = layout_override
        session["translation_region_layout_overrides"] = layout_overrides

        resolved_font_style = str(
            style.get("font_style_override")
            or style.get("font_style")
            or style.get("auto_font_style")
            or ""
        ).strip()
        if resolved_font_style:
            style_overrides = dict(session.get("style_region_overrides") or {})
            style_overrides[duplicated_region_id] = resolved_font_style
            session["style_region_overrides"] = style_overrides

        if bool(flags.get("disabled")):
            disabled_overrides = dict(session.get("translation_region_disabled_overrides") or {})
            disabled_overrides[duplicated_region_id] = True
            session["translation_region_disabled_overrides"] = disabled_overrides

        return payload

    def _sort_regions_for_merge(self, regions: list[Any]) -> list[Any]:
        if not regions:
            return []

        vertical_votes = sum(1 for region in regions if str(getattr(region, "direction", "") or "").startswith("v"))
        if vertical_votes >= max(1, len(regions) // 2 + (len(regions) % 2)):
            return sorted(
                regions,
                key=lambda region: (self._region_bbox(region)[0], self._region_bbox(region)[1]),
                reverse=True,
            )

        x1 = min(self._region_bbox(region)[0] for region in regions)
        y1 = min(self._region_bbox(region)[1] for region in regions)
        x2 = max(self._region_bbox(region)[2] for region in regions)
        y2 = max(self._region_bbox(region)[3] for region in regions)
        if (y2 - y1) > (x2 - x1) * 1.15:
            return sorted(regions, key=lambda region: (self._region_bbox(region)[0], self._region_bbox(region)[1]), reverse=True)

        return sorted(regions, key=lambda region: (self._region_bbox(region)[1], self._region_bbox(region)[0]))

    async def merge_regions(
        self,
        session_id: str,
        session: dict[str, Any],
        raw_config: dict[str, Any] | None,
        stored_name: str,
        region_ids: list[str],
    ) -> dict[str, Any]:
        self._ensure_runtime_patches()
        config = self._normalize_config(raw_config)
        source_dir = Path(session["source_dir"])
        source_path = source_dir / stored_name
        if not source_path.exists():
            raise RuntimeError("目标页面不存在，请刷新后重试。")

        source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if source_bgr is None:
            raise RuntimeError("无法读取当前页面原图。")
        source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)
        cache_page_dir = self._session_page_cache_dir(session, session_id, stored_name)
        if not self._has_rerenderable_page_cache(cache_page_dir):
            raise RuntimeError("无法为当前页面建立可编辑缓存，请刷新后重试。")

        candidate_regions = self._prepare_cached_regions_for_edit(
            source_rgb,
            cache_page_dir,
            config,
            stored_name,
            session=session,
        )
        region_id_set = {str(region_id or "").strip() for region_id in region_ids if str(region_id or "").strip()}
        selected_regions = [
            region for region in candidate_regions
            if str(getattr(region, "translation_region_key", "") or getattr(region, "style_region_key", "") or "") in region_id_set
        ]
        if len(selected_regions) < 2:
            raise ValueError("至少需要选择两个文本框才能合并。")

        ordered_regions = self._sort_regions_for_merge(selected_regions)
        merged_bbox = [
            min(self._region_bbox(region)[0] for region in ordered_regions),
            min(self._region_bbox(region)[1] for region in ordered_regions),
            max(self._region_bbox(region)[2] for region in ordered_regions),
            max(self._region_bbox(region)[3] for region in ordered_regions),
        ]
        merged_source_text = "\n".join(
            text for text in [self._region_source_text(region) for region in ordered_regions] if text
        ).strip()
        merged_font_size = float(np.median([max(float(getattr(region, "font_size", 0) or 0), 8.0) for region in ordered_regions]))
        sample_region = ordered_regions[0]
        translation = ""
        if self._session_workflow_stage(session) != "detected" and merged_source_text:
            try:
                translation = await self._translate_manual_text(merged_source_text, config, session_id)
            except Exception as exc:
                print(f"[WARN] Merged region translation failed for {stored_name}: {exc}")

        payload = self._build_manual_region_payload(
            stored_name=stored_name,
            bbox=merged_bbox,
            source_text=merged_source_text,
            translation=translation,
            target_lang=config["target_lang"],
            direction=str(getattr(sample_region, "direction", "") or self._direction_from_bbox(merged_bbox)),
            font_size=merged_font_size,
            fg_color=tuple(self._rgb_color_payload(getattr(sample_region, "fg_colors", getattr(sample_region, "fg_color", None)), (0, 0, 0))),
            bg_color=tuple(self._rgb_color_payload(getattr(sample_region, "bg_colors", getattr(sample_region, "bg_color", None)), (255, 255, 255))),
        )
        payload["merged_from"] = [str(getattr(region, "translation_region_key", "") or "") for region in ordered_regions]
        manual_regions = self._ensure_manual_regions_store(session)
        manual_regions.setdefault(stored_name, []).append(payload)
        return payload

    def pop_manual_region(self, session: dict[str, Any], region_id: str) -> dict[str, Any] | None:
        manual_regions = self._ensure_manual_regions_store(session)
        for stored_name, page_regions in list(manual_regions.items()):
            if not isinstance(page_regions, list):
                continue
            next_page_regions: list[dict[str, Any]] = []
            removed_payload: dict[str, Any] | None = None
            for payload in page_regions:
                if removed_payload is None and str(payload.get("id") or "") == region_id:
                    removed_payload = dict(payload)
                    continue
                next_page_regions.append(payload)
            if removed_payload is not None:
                if next_page_regions:
                    manual_regions[stored_name] = next_page_regions
                else:
                    manual_regions.pop(stored_name, None)
                return removed_payload
        return None

    def delete_manual_region(self, session: dict[str, Any], region_id: str) -> bool:
        return self.pop_manual_region(session, region_id) is not None

    def _prepare_cached_regions_for_edit(
        self,
        source_rgb: np.ndarray,
        page_cache_dir: Path,
        config: dict[str, Any],
        stored_name: str,
        session: dict[str, Any] | None = None,
    ) -> list[Any]:
        regions = self._load_cached_regions(page_cache_dir)
        regions = self._merge_manual_regions(session, stored_name, regions)
        regions = self._apply_region_layout_overrides(regions, config, stored_name)
        self._apply_region_translation_overrides(regions, config, stored_name)
        self._apply_region_font_styles(source_rgb, regions, config, stored_name)
        return self._dedupe_overlapping_regions(regions)

    def _prepare_cached_regions_for_edit_with_debug(
        self,
        source_rgb: np.ndarray,
        page_cache_dir: Path,
        config: dict[str, Any],
        stored_name: str,
        session: dict[str, Any] | None = None,
    ) -> tuple[list[Any], dict[str, Any]]:
        regions = self._load_cached_regions(page_cache_dir)
        regions = self._merge_manual_regions(session, stored_name, regions)
        regions = self._apply_region_layout_overrides(regions, config, stored_name)
        raw_count = len(regions)
        self._apply_region_translation_overrides(regions, config, stored_name)
        self._apply_region_font_styles(source_rgb, regions, config, stored_name)
        deduped_regions, suppressed = self._dedupe_overlapping_regions(regions, capture_debug=True)
        surviving_suspects = self._find_remaining_overlap_suspects(deduped_regions)
        return deduped_regions, {
            "raw_count": raw_count,
            "deduped_count": len(deduped_regions),
            "suppressed": suppressed,
            "surviving_suspects": surviving_suspects,
        }

    def _make_style_region_key(self, stored_name: str, index: int) -> str:
        return f"{stored_name}::{index}"

    def _assign_region_keys(self, regions: list[Any], stored_name: str) -> None:
        for index, region in enumerate(regions):
            region_key = str(
                getattr(region, "translation_region_key", "")
                or getattr(region, "style_region_key", "")
                or getattr(region, "manual_region_id", "")
                or self._make_style_region_key(stored_name, index)
            )
            region.translation_region_key = region_key
            region.style_region_key = region_key

    def _region_bbox(self, region: Any) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = [int(v) for v in getattr(region, "xyxy", [0, 0, 0, 0])]
        return x1, y1, x2, y2

    def _region_area(self, region: Any) -> int:
        x1, y1, x2, y2 = self._region_bbox(region)
        return max(1, (x2 - x1) * (y2 - y1))

    def _region_display_text(self, region: Any) -> str:
        return str(getattr(region, "translation", "") or self._region_source_text(region) or "").strip()

    def _region_text_similarity(self, left: Any, right: Any) -> float:
        left_text = re.sub(r"\s+", "", self._region_display_text(left))
        right_text = re.sub(r"\s+", "", self._region_display_text(right))
        if not left_text or not right_text:
            return 0.0
        if left_text in right_text or right_text in left_text:
            return 1.0
        return SequenceMatcher(None, left_text, right_text).ratio()

    def _region_overlap_metrics(self, left: Any, right: Any) -> tuple[float, float, float]:
        ax1, ay1, ax2, ay2 = self._region_bbox(left)
        bx1, by1, bx2, by2 = self._region_bbox(right)
        intersection_w = max(0, min(ax2, bx2) - max(ax1, bx1))
        intersection_h = max(0, min(ay2, by2) - max(ay1, by1))
        intersection = intersection_w * intersection_h
        if intersection <= 0:
            return 0.0, 0.0, 1.0

        left_area = self._region_area(left)
        right_area = self._region_area(right)
        smaller_cover = intersection / float(max(1, min(left_area, right_area)))
        union = max(left_area + right_area - intersection, 1)
        iou = intersection / float(union)

        left_cx = (ax1 + ax2) / 2.0
        left_cy = (ay1 + ay2) / 2.0
        right_cx = (bx1 + bx2) / 2.0
        right_cy = (by1 + by2) / 2.0
        diag = max((((max(ax2 - ax1, 1) ** 2) + (max(ay2 - ay1, 1) ** 2)) ** 0.5), 1.0)
        center_distance = ((left_cx - right_cx) ** 2 + (left_cy - right_cy) ** 2) ** 0.5
        center_ratio = center_distance / diag
        return smaller_cover, iou, center_ratio

    def _region_render_priority(self, region: Any) -> float:
        translation_override = str(getattr(region, "translation_override", "") or "").strip()
        text_value = self._region_display_text(region)
        font_size = float(getattr(region, "font_size", 0) or 0)
        return (
            (2500.0 if bool(getattr(region, "manual_region", False)) else 0.0)
            +
            (1000.0 if translation_override else 0.0)
            + len(text_value) * 12.0
            + self._region_area(region) / 1200.0
            + font_size
        )

    def _build_region_mask(
        self,
        region: Any,
        image_shape: tuple[int, int],
        dilation_scale: float = 0.08,
        dilation_min: int = 1,
        dilation_max: int = 8,
    ) -> np.ndarray:
        mask = np.zeros(image_shape[:2], dtype=np.uint8)
        drew_mask = False
        raw_lines = getattr(region, "lines", None)
        if isinstance(raw_lines, list):
            for line in raw_lines:
                try:
                    points = np.asarray(line, dtype=np.int32).reshape(-1, 2)
                except Exception:
                    continue
                if points.shape[0] >= 3:
                    cv2.fillPoly(mask, [points], 255)
                    drew_mask = True
                elif points.shape[0] == 2:
                    cv2.rectangle(mask, tuple(points[0]), tuple(points[1]), 255, -1)
                    drew_mask = True

        if not drew_mask:
            x1, y1, x2, y2 = self._region_bbox(region)
            x1 = max(0, min(mask.shape[1], x1))
            x2 = max(0, min(mask.shape[1], x2))
            y1 = max(0, min(mask.shape[0], y1))
            y2 = max(0, min(mask.shape[0], y2))
            if x2 <= x1 or y2 <= y1:
                return mask
            cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)

        font_size = max(float(getattr(region, "font_size", 0) or 0), 10.0)
        dilation = int(max(dilation_min, min(dilation_max, round(font_size * dilation_scale))))
        kernel = np.ones((dilation, dilation), dtype=np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=1)
        return mask

    def _restore_original_region_pixels(
        self,
        source_rgb: np.ndarray,
        base_rgb: np.ndarray,
        region: Any,
    ) -> None:
        if source_rgb.shape[:2] != base_rgb.shape[:2]:
            return

        mask = self._build_region_mask(region, source_rgb.shape)
        selector = mask > 0
        if np.any(selector):
            base_rgb[selector] = source_rgb[selector]

    def _erase_manual_region_pixels(
        self,
        base_rgb: np.ndarray,
        region: Any,
    ) -> None:
        mask = self._build_region_mask(
            region,
            base_rgb.shape,
            dilation_scale=0.14,
            dilation_min=2,
            dilation_max=12,
        )
        if not np.any(mask):
            return

        font_size = max(float(getattr(region, "font_size", 0) or 0), 12.0)
        radius = int(max(3, min(9, round(font_size * 0.14))))
        inpainted = cv2.inpaint(base_rgb, mask, radius, cv2.INPAINT_TELEA)
        base_rgb[:, :] = inpainted

    def _dedupe_overlapping_regions(
        self,
        regions: list[Any],
        capture_debug: bool = False,
    ) -> list[Any] | tuple[list[Any], list[dict[str, Any]]]:
        if len(regions) < 2:
            return (regions, []) if capture_debug else regions

        indexed_regions = list(enumerate(regions))
        indexed_regions.sort(
            key=lambda item: (self._region_render_priority(item[1]), -item[0]),
            reverse=True,
        )
        suppressed: set[int] = set()
        suppressed_debug: list[dict[str, Any]] = []

        for position, (index, region) in enumerate(indexed_regions):
            if index in suppressed:
                continue
            for other_index, other_region in indexed_regions[position + 1:]:
                if other_index in suppressed:
                    continue
                if bool(getattr(region, "allow_overlap", False)) or bool(getattr(other_region, "allow_overlap", False)):
                    continue

                smaller_cover, iou, center_ratio = self._region_overlap_metrics(region, other_region)
                text_similarity = self._region_text_similarity(region, other_region)
                if smaller_cover < 0.72 and not (iou >= 0.5 and center_ratio <= 0.34):
                    continue

                if text_similarity >= 0.72 and center_ratio <= 0.36:
                    suppressed.add(other_index)
                    if capture_debug:
                        suppressed_debug.append(
                            self._build_overlap_debug_entry(
                                kept_index=index,
                                kept_region=region,
                                other_index=other_index,
                                other_region=other_region,
                                smaller_cover=smaller_cover,
                                iou=iou,
                                center_ratio=center_ratio,
                                text_similarity=text_similarity,
                                reason="high_similarity_center_overlap",
                            )
                        )
                    continue

                if text_similarity >= 0.55 and smaller_cover >= 0.78:
                    suppressed.add(other_index)
                    if capture_debug:
                        suppressed_debug.append(
                            self._build_overlap_debug_entry(
                                kept_index=index,
                                kept_region=region,
                                other_index=other_index,
                                other_region=other_region,
                                smaller_cover=smaller_cover,
                                iou=iou,
                                center_ratio=center_ratio,
                                text_similarity=text_similarity,
                                reason="high_similarity_large_cover",
                            )
                        )
                    continue

                if text_similarity < 0.38 and smaller_cover < 0.9:
                    continue

                suppressed.add(other_index)
                if capture_debug:
                    suppressed_debug.append(
                        self._build_overlap_debug_entry(
                            kept_index=index,
                            kept_region=region,
                            other_index=other_index,
                            other_region=other_region,
                            smaller_cover=smaller_cover,
                            iou=iou,
                            center_ratio=center_ratio,
                            text_similarity=text_similarity,
                            reason="fallback_overlap_suppression",
                        )
                    )

        if not suppressed:
            return (regions, suppressed_debug) if capture_debug else regions

        deduped_regions = [region for index, region in enumerate(regions) if index not in suppressed]
        if capture_debug:
            return deduped_regions, suppressed_debug
        return deduped_regions

    def _build_overlap_debug_entry(
        self,
        kept_index: int,
        kept_region: Any,
        other_index: int,
        other_region: Any,
        smaller_cover: float,
        iou: float,
        center_ratio: float,
        text_similarity: float,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "reason": reason,
            "kept_index": kept_index,
            "other_index": other_index,
            "smaller_cover": round(smaller_cover, 4),
            "iou": round(iou, 4),
            "center_ratio": round(center_ratio, 4),
            "text_similarity": round(text_similarity, 4),
            "kept_bbox": list(self._region_bbox(kept_region)),
            "other_bbox": list(self._region_bbox(other_region)),
            "kept_text": self._region_display_text(kept_region),
            "other_text": self._region_display_text(other_region),
            "kept_style": str(getattr(kept_region, "font_style", "") or ""),
            "other_style": str(getattr(other_region, "font_style", "") or ""),
            "kept_font": os.path.basename(str(getattr(kept_region, "font_family", "") or "")),
            "other_font": os.path.basename(str(getattr(other_region, "font_family", "") or "")),
        }

    def _find_remaining_overlap_suspects(self, regions: list[Any]) -> list[dict[str, Any]]:
        suspects: list[dict[str, Any]] = []
        for left_index, left_region in enumerate(regions):
            for right_index in range(left_index + 1, len(regions)):
                right_region = regions[right_index]
                smaller_cover, iou, center_ratio = self._region_overlap_metrics(left_region, right_region)
                if smaller_cover < 0.55 and not (iou >= 0.33 and center_ratio <= 0.45):
                    continue
                text_similarity = self._region_text_similarity(left_region, right_region)
                suspects.append(
                    self._build_overlap_debug_entry(
                        kept_index=left_index,
                        kept_region=left_region,
                        other_index=right_index,
                        other_region=right_region,
                        smaller_cover=smaller_cover,
                        iou=iou,
                        center_ratio=center_ratio,
                        text_similarity=text_similarity,
                        reason="surviving_overlap_candidate",
                    )
                )

        suspects.sort(
            key=lambda item: (
                item["smaller_cover"],
                item["iou"],
                item["text_similarity"],
            ),
            reverse=True,
        )
        return suspects[:12]

    def _region_debug_entry(self, index: int, region: Any) -> dict[str, Any]:
        return {
            "index": index,
            "bbox": list(self._region_bbox(region)),
            "style_region_key": str(getattr(region, "style_region_key", "") or ""),
            "translation_region_key": str(getattr(region, "translation_region_key", "") or ""),
            "font_style": str(getattr(region, "font_style", "") or ""),
            "auto_font_style": str(getattr(region, "auto_font_style", "") or ""),
            "override_font_style": str(getattr(region, "override_font_style", "") or ""),
            "font_family": os.path.basename(str(getattr(region, "font_family", "") or "")),
            "translation": self._region_display_text(region),
            "manual": bool(getattr(region, "manual_region", False)),
        }

    def _write_style_rerender_debug_report(
        self,
        session_id: str,
        stored_name: str,
        output_path: Path,
        debug_info: dict[str, Any],
        regions: list[Any],
    ) -> None:
        debug_dir = self._prepare_style_rerender_debug_dir(session_id, reset=False)
        report_path = debug_dir / f"{Path(stored_name).stem}.json"
        report = {
            "stored_name": stored_name,
            "output_name": output_path.name,
            "raw_count": int(debug_info.get("raw_count", 0)),
            "deduped_count": int(debug_info.get("deduped_count", len(regions))),
            "suppressed_count": len(debug_info.get("suppressed", [])),
            "surviving_overlap_count": len(debug_info.get("surviving_suspects", [])),
            "regions": [self._region_debug_entry(index, region) for index, region in enumerate(regions)],
            "suppressed": debug_info.get("suppressed", []),
            "surviving_suspects": debug_info.get("surviving_suspects", []),
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            "[DEBUG] Style rerender report",
            f"page={stored_name}",
            f"raw={report['raw_count']}",
            f"deduped={report['deduped_count']}",
            f"suppressed={report['suppressed_count']}",
            f"surviving={report['surviving_overlap_count']}",
            f"output={output_path.name}",
            f"report={report_path}",
        )
        for suspect in report["surviving_suspects"][:5]:
            print(
                "[DEBUG] Surviving overlap suspect",
                f"page={stored_name}",
                f"kept={suspect['kept_index']}",
                f"other={suspect['other_index']}",
                f"cover={suspect['smaller_cover']}",
                f"iou={suspect['iou']}",
                f"center={suspect['center_ratio']}",
                f"sim={suspect['text_similarity']}",
                f"kept_font={suspect['kept_font']}",
                f"other_font={suspect['other_font']}",
            )

    def _region_source_text(self, region: Any) -> str:
        for field_name in ("text_raw", "text", "source_text", "original_text", "manual_source_text"):
            text_value = str(getattr(region, field_name, "") or "").strip()
            if text_value:
                return text_value
        texts = getattr(region, "texts", None)
        if isinstance(texts, list):
            joined = "".join(str(item or "") for item in texts).strip()
            if joined:
                return joined
        return ""

    def _region_preview_text(self, region: Any) -> str:
        if bool(getattr(region, "skip_translation", False)):
            return self._region_source_text(region)
        preview = str(getattr(region, "translation", "") or "").strip()
        if preview:
            return preview
        return self._region_source_text(region)

    def _invalidate_region_geometry_cache(self, region: Any) -> None:
        for field_name in (
            "xyxy",
            "xywh",
            "center",
            "unrotated_polygons",
            "unrotated_min_rect",
            "min_rect",
            "polygon_aspect_ratio",
            "unrotated_size",
            "aspect_ratio",
        ):
            region.__dict__.pop(field_name, None)

    def _set_region_bbox(self, region: Any, bbox: list[int]) -> None:
        x1, y1, x2, y2 = [int(v) for v in bbox]
        region.lines = np.array(self._manual_region_lines([x1, y1, x2, y2]), dtype=np.int32)
        region._bounding_rect = [x1, y1, x2, y2]
        self._invalidate_region_geometry_cache(region)

    def _apply_region_layout_overrides(
        self,
        regions: list[Any],
        config: dict[str, Any],
        stored_name: str,
    ) -> list[Any]:
        self._assign_region_keys(regions, stored_name)
        disabled_overrides = config.get("translation_region_disabled_overrides") or {}
        layout_overrides = config.get("translation_region_layout_overrides") or {}

        prepared_regions: list[Any] = []
        for region in regions:
            region_key = str(getattr(region, "translation_region_key", "") or "")
            region.disabled_region = bool(disabled_overrides.get(region_key))

            layout_override = layout_overrides.get(region_key) or {}
            region.preserve_background = bool(
                layout_override.get("preserve_background")
                or layout_override.get("skip_background_erase")
            )
            region.font_size_override_active = False
            region.direction_override_active = False
            region.letter_spacing_override_active = False
            region.line_spacing_override_active = False
            bbox = layout_override.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                self._set_region_bbox(region, bbox)

            font_size = layout_override.get("font_size")
            if font_size is not None:
                try:
                    region.font_size = max(8, int(round(float(font_size))))
                    region.font_size_override_active = True
                except (TypeError, ValueError):
                    pass

            rotation = self._normalize_rotation_degrees(layout_override.get("rotation", layout_override.get("angle")))
            if rotation is not None:
                region.angle = rotation
                self._invalidate_region_geometry_cache(region)

            stroke_width = self._normalize_stroke_strength(layout_override.get("stroke_width"))
            if stroke_width is not None:
                region.default_stroke_width = stroke_width

            letter_spacing = self._normalize_letter_spacing(layout_override.get("letter_spacing"))
            if letter_spacing is not None:
                region.letter_spacing = letter_spacing
                region.letter_spacing_override_active = True

            line_spacing = self._normalize_line_spacing(layout_override.get("line_spacing"))
            if line_spacing is not None:
                region.line_spacing = line_spacing
                region.line_spacing_override_active = True

            if "fg_color" in layout_override:
                region.fg_colors = np.array(self._rgb_color_payload(layout_override.get("fg_color"), (0, 0, 0)))

            if "bg_color" in layout_override:
                region.bg_colors = np.array(self._rgb_color_payload(layout_override.get("bg_color"), (255, 255, 255)))
                region.adjust_bg_color = False
            self._sanitize_auto_text_background_color(region, layout_override)

            resolved_direction = self._resolve_region_direction(
                self._region_bbox(region),
                getattr(region, "direction", ""),
                config.get("target_lang"),
                layout_override=layout_override,
            )
            if self._normalize_direction_override(layout_override.get("direction")) != "auto":
                region.direction_override_active = True
            self._assign_region_direction(
                region,
                resolved_direction,
            )

            prepared_regions.append(region)

        return prepared_regions

    def _deserialize_text_region(self, payload: dict[str, Any]) -> Any:
        self._ensure_vendor_import_path()
        from manga_translator.utils.textblock import TextBlock

        texts = payload.get("texts")
        if not isinstance(texts, list) or not texts:
            texts = [
                str(
                    payload.get("text")
                    or payload.get("text_raw")
                    or payload.get("source_text")
                    or ""
                )
            ]

        lines = payload.get("lines") or []
        if not lines:
            bounding_rect = payload.get("_bounding_rect")
            if isinstance(bounding_rect, (list, tuple)) and len(bounding_rect) == 4:
                try:
                    bbox = [int(round(float(value))) for value in bounding_rect]
                    lines = self._manual_region_lines(bbox)
                except (TypeError, ValueError):
                    lines = []

        fg_payload = payload.get("fg_colors") if payload.get("fg_colors") is not None else payload.get("fg_color")
        bg_payload = payload.get("bg_colors") if payload.get("bg_colors") is not None else payload.get("bg_color")

        region = TextBlock(
            lines=lines,
            texts=texts,
            language=payload.get("language", "unknown"),
            font_size=payload.get("font_size", -1),
            angle=payload.get("angle", 0),
            translation=payload.get("translation", ""),
            fg_color=tuple(self._rgb_color_payload(fg_payload, (0, 0, 0))),
            bg_color=tuple(self._rgb_color_payload(bg_payload, (0, 0, 0))),
            line_spacing=payload.get("line_spacing", 1.0),
            letter_spacing=payload.get("letter_spacing", 1.0),
            font_family=payload.get("font_family", ""),
            bold=payload.get("bold", False),
            underline=payload.get("underline", False),
            italic=payload.get("italic", False),
            direction=payload.get("_direction", payload.get("direction", "auto")),
            alignment=payload.get("_alignment", payload.get("alignment", "auto")),
            rich_text=payload.get("rich_text", ""),
            _bounding_rect=payload.get("_bounding_rect"),
            default_stroke_width=payload.get("default_stroke_width", 0.2),
            font_weight=payload.get("font_weight", 50),
            source_lang=payload.get("_source_lang", payload.get("source_lang", "")),
            target_lang=payload.get("target_lang", ""),
            opacity=payload.get("opacity", 1.0),
            shadow_radius=payload.get("shadow_radius", 0.0),
            shadow_strength=payload.get("shadow_strength", 1.0),
            shadow_color=tuple(payload.get("shadow_color") or (0, 0, 0)),
            shadow_offset=payload.get("shadow_offset") or [0, 0],
            prob=payload.get("prob", 1),
        )
        if "adjust_bg_color" in payload:
            region.adjust_bg_color = bool(payload["adjust_bg_color"])
        if "font_style" in payload:
            region.font_style = payload["font_style"]
        if "preserve_background" in payload:
            region.preserve_background = bool(payload["preserve_background"])
        source_text = str(payload.get("source_text") or payload.get("text_raw") or payload.get("text") or "").strip()
        if source_text:
            region.source_text = source_text
            region.text_raw = source_text
        return region

    def _classify_region_font_style(
        self,
        source_rgb: np.ndarray,
        region: Any,
        median_font_size: float,
    ) -> str:
        features = self._extract_region_style_features(source_rgb, region, median_font_size)
        if self._looks_like_sfx(region, median_font_size, features):
            return "sfx"
        if self._looks_like_handwritten(region, median_font_size, features):
            return "handwritten"
        if self._looks_like_cartoon(region, median_font_size, features):
            return "cartoon"
        if self._looks_like_rounded(region, median_font_size, features):
            return "rounded"
        if self._looks_like_mincho(region, median_font_size, features):
            return "mincho"
        return "gothic"

    def _extract_region_style_features(
        self,
        source_rgb: np.ndarray,
        region: Any,
        median_font_size: float,
    ) -> dict[str, float]:
        x1, y1, x2, y2 = map(int, getattr(region, "xyxy", [0, 0, 0, 0]))
        w = max(x2 - x1, 1)
        h = max(y2 - y1, 1)
        pad = max(4, int(max(w, h) * 0.12))
        roi_x1 = max(0, x1 - pad)
        roi_y1 = max(0, y1 - pad)
        roi_x2 = min(source_rgb.shape[1], x2 + pad)
        roi_y2 = min(source_rgb.shape[0], y2 + pad)
        roi = source_rgb[roi_y1:roi_y2, roi_x1:roi_x2]
        if roi.size == 0:
            return {
                "char_count": 0.0,
                "font_ratio": 1.0,
                "aspect_ratio": 1.0,
                "fill_ratio": 0.0,
                "component_count": 0.0,
                "mean_circularity": 0.0,
                "corner_density": 0.0,
                "stroke_width_mean": 0.0,
                "stroke_width_var": 0.0,
                "ink_darkness": 0.0,
                "boxed": 0.0,
            }

        gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
        polygon_mask = np.zeros(gray.shape, dtype=np.uint8)
        try:
            polygon = np.array(region.min_rect[0], dtype=np.int32)
            polygon[:, 0] -= roi_x1
            polygon[:, 1] -= roi_y1
            cv2.fillConvexPoly(polygon_mask, polygon, 255)
        except Exception:
            polygon_mask[:, :] = 255

        fg_color = tuple(self._rgb_color_payload(getattr(region, "fg_colors", getattr(region, "fg_color", None)), (0, 0, 0)))
        bg_color = tuple(self._rgb_color_payload(getattr(region, "bg_colors", getattr(region, "bg_color", None)), (255, 255, 255)))
        fg_brightness = float(np.mean(fg_color))
        bg_brightness = float(np.mean(bg_color))

        _, otsu_dark = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        _, otsu_light = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        text_mask = otsu_dark if fg_brightness <= bg_brightness else otsu_light
        text_mask = cv2.bitwise_and(text_mask, polygon_mask)

        if cv2.countNonZero(text_mask) < max(8, int(w * h * 0.01)):
            kernel_size = max(3, int(round(max(w, h) / max(median_font_size, 1.0))))
            if kernel_size % 2 == 0:
                kernel_size += 1
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
            response = cv2.max(
                cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel),
                cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel),
            )
            _, text_mask = cv2.threshold(response, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            text_mask = cv2.bitwise_and(text_mask, polygon_mask)

        text_mask = cv2.morphologyEx(text_mask, cv2.MORPH_OPEN, np.ones((2, 2), dtype=np.uint8))
        text_mask = cv2.morphologyEx(text_mask, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8))

        ink_pixels = text_mask > 0
        ink_count = int(np.count_nonzero(ink_pixels))
        bbox_area = max(w * h, 1)
        fill_ratio = ink_count / float(bbox_area)

        contours, _ = cv2.findContours(text_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        component_count = 0
        circularities: list[float] = []
        corner_count = 0
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 4:
                continue
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            component_count += 1
            circularities.append(float((4.0 * np.pi * area) / (perimeter * perimeter)))
            approximation = cv2.approxPolyDP(contour, 0.06 * perimeter, True)
            corner_count += max(len(approximation) - 2, 0)

        mean_circularity = float(np.clip(np.mean(circularities), 0.0, 1.0)) if circularities else 0.0
        corner_density = corner_count / float(max(ink_count, 1))

        distance = cv2.distanceTransform((text_mask > 0).astype(np.uint8), cv2.DIST_L2, 5)
        stroke_values = distance[ink_pixels]
        if stroke_values.size:
            stroke_width_mean = float(np.mean(stroke_values) * 2.0)
            stroke_width_var = float(np.std(stroke_values) / max(np.mean(stroke_values), 1e-6))
        else:
            stroke_width_mean = 0.0
            stroke_width_var = 0.0

        char_count = len(re.sub(r"\s+", "", str(getattr(region, "text", "") or getattr(region, "translation", "") or "")))
        font_size = max(float(getattr(region, "font_size", 0) or 0), 1.0)
        long_side = max(float(w), float(h), 1.0)
        short_side = max(min(float(w), float(h)), 1.0)
        aspect_ratio = long_side / short_side
        ink_darkness = float(255.0 - np.mean(gray[ink_pixels])) / 255.0 if ink_count else 0.0

        return {
            "char_count": float(char_count),
            "font_ratio": font_size / max(median_font_size, 1.0),
            "aspect_ratio": aspect_ratio,
            "fill_ratio": fill_ratio,
            "component_count": float(component_count),
            "mean_circularity": mean_circularity,
            "corner_density": corner_density,
            "stroke_width_mean": stroke_width_mean,
            "stroke_width_var": stroke_width_var,
            "ink_darkness": ink_darkness,
            "boxed": 1.0 if self._looks_like_caption_box(source_rgb, region) else 0.0,
        }

    def _looks_like_sfx(self, region: Any, median_font_size: float, features: dict[str, float]) -> bool:
        char_count = features["char_count"]
        font_ratio = features["font_ratio"]
        fill_ratio = features["fill_ratio"]
        stroke_width_mean = features["stroke_width_mean"]
        aspect_ratio = features["aspect_ratio"]
        boxed = features["boxed"] > 0.5
        font_size = max(float(getattr(region, "font_size", 0) or 0), 1.0)
        if boxed:
            return False
        if char_count <= 6 and (font_ratio >= 1.18 or font_size >= median_font_size * 1.2):
            return True
        if char_count <= 8 and fill_ratio >= 0.24 and stroke_width_mean >= max(2.2, median_font_size * 0.1):
            return True
        if char_count <= 5 and aspect_ratio >= 4.2 and font_ratio >= 1.05:
            return True
        return False

    def _looks_like_handwritten(self, region: Any, median_font_size: float, features: dict[str, float]) -> bool:
        char_count = features["char_count"]
        font_ratio = features["font_ratio"]
        component_count = features["component_count"]
        stroke_width_var = features["stroke_width_var"]
        fill_ratio = features["fill_ratio"]
        mean_circularity = features["mean_circularity"]
        boxed = features["boxed"] > 0.5
        if boxed:
            return False
        if font_ratio <= 0.72 and char_count <= 8:
            return True
        if stroke_width_var >= 0.68 and component_count >= max(char_count * 1.2, 3.0) and char_count <= 8:
            return True
        if fill_ratio <= 0.1 and mean_circularity <= 0.18 and char_count <= 6 and font_ratio <= 0.92:
            return True
        return False

    def _looks_like_cartoon(self, region: Any, median_font_size: float, features: dict[str, float]) -> bool:
        char_count = features["char_count"]
        font_ratio = features["font_ratio"]
        mean_circularity = features["mean_circularity"]
        stroke_width_mean = features["stroke_width_mean"]
        fill_ratio = features["fill_ratio"]
        if char_count == 0:
            return False
        if font_ratio >= 1.14 and mean_circularity >= 0.4 and stroke_width_mean >= max(2.25, median_font_size * 0.095):
            return True
        if fill_ratio >= 0.26 and mean_circularity >= 0.44 and char_count <= 6:
            return True
        if char_count <= 3 and mean_circularity >= 0.48:
            return True
        return False

    def _looks_like_rounded(self, region: Any, median_font_size: float, features: dict[str, float]) -> bool:
        mean_circularity = features["mean_circularity"]
        corner_density = features["corner_density"]
        stroke_width_var = features["stroke_width_var"]
        fill_ratio = features["fill_ratio"]
        if mean_circularity >= 0.46 and corner_density <= 0.022:
            return True
        if mean_circularity >= 0.4 and stroke_width_var <= 0.22 and fill_ratio >= 0.14:
            return True
        return False

    def _looks_like_mincho(self, region: Any, median_font_size: float, features: dict[str, float]) -> bool:
        stroke_width_var = features["stroke_width_var"]
        corner_density = features["corner_density"]
        mean_circularity = features["mean_circularity"]
        stroke_width_mean = features["stroke_width_mean"]
        if stroke_width_var >= 0.56 and corner_density >= 0.034:
            return True
        if stroke_width_var >= 0.46 and corner_density >= 0.028 and mean_circularity <= 0.28:
            return True
        if stroke_width_mean <= max(1.55, median_font_size * 0.055) and corner_density >= 0.035:
            return True
        return False

    def _looks_like_caption_box(self, source_rgb: np.ndarray, region: Any) -> bool:
        gray = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2GRAY)
        x1, y1, x2, y2 = map(int, getattr(region, "xyxy"))
        w = max(x2 - x1, 1)
        h = max(y2 - y1, 1)
        pad_x = max(int(w * 0.45), 8)
        pad_y = max(int(h * 0.35), 8)
        roi_x1 = max(0, x1 - pad_x)
        roi_y1 = max(0, y1 - pad_y)
        roi_x2 = min(gray.shape[1], x2 + pad_x)
        roi_y2 = min(gray.shape[0], y2 + pad_y)
        roi = gray[roi_y1:roi_y2, roi_x1:roi_x2]
        if roi.size == 0:
            return False

        bright = cv2.inRange(roi, 220, 255)
        bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8))
        bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8))
        local_x1 = x1 - roi_x1
        local_y1 = y1 - roi_y1
        local_w = w
        local_h = h
        center_x = local_x1 + local_w // 2
        center_y = local_y1 + local_h // 2
        bbox_area = max(local_w * local_h, 1)
        roi_area = roi.shape[0] * roi.shape[1]

        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(bright, 8, cv2.CV_32S)
        for label in range(1, num_labels):
            cx = int(stats[label, cv2.CC_STAT_LEFT])
            cy = int(stats[label, cv2.CC_STAT_TOP])
            cw = int(stats[label, cv2.CC_STAT_WIDTH])
            ch = int(stats[label, cv2.CC_STAT_HEIGHT])
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area < int(bbox_area * 1.1) or area > int(roi_area * 0.85):
                continue
            if area / max(cw * ch, 1) < 0.72:
                continue
            if not (0 <= center_x < roi.shape[1] and 0 <= center_y < roi.shape[0] and labels[center_y, center_x] == label):
                overlap = max(0, min(cx + cw, local_x1 + local_w) - max(cx, local_x1)) * max(
                    0,
                    min(cy + ch, local_y1 + local_h) - max(cy, local_y1),
                )
                if overlap <= 0:
                    continue

            component_pixels = roi[labels == label]
            if component_pixels.size == 0:
                continue
            if float(component_pixels.mean()) < 229 or float(component_pixels.std()) > 24:
                continue
            return True
        return False

    def _apply_region_font_styles(
        self,
        source_rgb: np.ndarray,
        regions: list[Any],
        config: dict[str, Any],
        stored_name: str = "",
    ) -> None:
        if not regions:
            return

        default_font_path = config.get("font_path", "")
        style_paths = config.get("style_font_paths") or {}
        style_overrides = config.get("style_region_overrides") or {}
        layout_overrides = config.get("translation_region_layout_overrides") or {}
        if config.get("font_style_mode") != "auto-map":
            for index, region in enumerate(regions):
                region_key = str(
                    getattr(region, "style_region_key", "")
                    or (self._make_style_region_key(stored_name, index) if stored_name else str(index))
                )
                layout_override = layout_overrides.get(region_key) or {}
                explicit_font_key = str(layout_override.get("font_key") or "")
                explicit_font_path = self._resolve_font_path(explicit_font_key) if explicit_font_key else ""
                region.font_family = explicit_font_path or default_font_path
                region.font_style = "single"
                region.auto_font_style = "gothic"
                region.override_font_style = ""
                region.style_region_key = region_key
            return

        font_sizes = [max(float(getattr(region, "font_size", 0) or 0), 1.0) for region in regions]
        median_font_size = float(np.median(font_sizes)) if font_sizes else 12.0

        for index, region in enumerate(regions):
            style_key = str(
                getattr(region, "style_region_key", "")
                or (self._make_style_region_key(stored_name, index) if stored_name else str(index))
            )
            auto_style = self._classify_region_font_style(source_rgb, region, median_font_size)
            override_style = self._normalize_style_bucket(style_overrides.get(style_key, ""))
            resolved_style = override_style or auto_style
            layout_override = layout_overrides.get(style_key) or {}
            explicit_font_key = str(layout_override.get("font_key") or "")
            explicit_font_path = self._resolve_font_path(explicit_font_key) if explicit_font_key else ""
            region.style_region_key = style_key
            region.auto_font_style = auto_style
            region.override_font_style = override_style
            region.font_style = resolved_style
            if explicit_font_path:
                region.font_family = explicit_font_path
            else:
                region.font_family = style_paths.get(resolved_style) or default_font_path

    def _apply_region_translation_overrides(
        self,
        regions: list[Any],
        config: dict[str, Any],
        stored_name: str = "",
    ) -> None:
        overrides = config.get("translation_region_overrides") or {}
        skip_overrides = config.get("translation_region_skip_overrides") or {}
        for index, region in enumerate(regions):
            region_key = str(
                getattr(region, "translation_region_key", "")
                or getattr(region, "style_region_key", "")
                or (self._make_style_region_key(stored_name, index) if stored_name else str(index))
            )
            machine_translation = str(getattr(region, "translation", "") or "")
            override_translation = str(overrides.get(region_key, "") or "").strip()
            skip_translation = bool(skip_overrides.get(region_key))
            region.translation_region_key = region_key
            region.machine_translation = machine_translation
            region.translation_override = override_translation
            region.skip_translation = skip_translation
            if override_translation:
                region.translation = override_translation

    async def _render_cached_page(
        self,
        source_path: Path,
        output_path: Path,
        page_cache_dir: Path,
        config: dict[str, Any],
        base_image_rgb: np.ndarray | None = None,
        prepared_regions: list[Any] | None = None,
        debug_output_dir: Path | None = None,
        session: dict[str, Any] | None = None,
    ) -> None:
        self._ensure_vendor_import_path()
        from manga_translator.rendering import (
            dispatch as dispatch_rendering,
            render as render_region,
            resize_regions_to_font_size,
        )

        source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if source_bgr is None:
            raise RuntimeError(f"无法读取原图: {source_path}")
        source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)
        inpainted_bgr = cv2.imread(str(page_cache_dir / "inpainted.png"), cv2.IMREAD_COLOR)
        if inpainted_bgr is None and self._ensure_page_base_image_cache(source_path, page_cache_dir):
            inpainted_bgr = cv2.imread(str(page_cache_dir / "inpainted.png"), cv2.IMREAD_COLOR)
        if inpainted_bgr is None:
            raise RuntimeError(f"重嵌字缓存损坏，无法读取底图: {page_cache_dir / 'inpainted.png'}")

        inpainted_rgb = (
            base_image_rgb.copy()
            if base_image_rgb is not None
            else cv2.cvtColor(inpainted_bgr, cv2.COLOR_BGR2RGB)
        )
        regions = (
            prepared_regions
            if prepared_regions is not None
            else self._prepare_cached_regions_for_edit(
                source_rgb,
                page_cache_dir,
                config,
                source_path.name,
                session=session,
            )
        )
        skipped_regions = [region for region in regions if bool(getattr(region, "skip_translation", False))]
        disabled_regions = [region for region in regions if bool(getattr(region, "disabled_region", False))]
        manual_render_regions = [
            region for region in regions
            if (
                bool(getattr(region, "manual_region", False))
                and not bool(getattr(region, "skip_translation", False))
                and not bool(getattr(region, "disabled_region", False))
                and not bool(getattr(region, "preserve_background", False))
            )
        ]
        render_regions = [
            region for region in regions
            if not bool(getattr(region, "skip_translation", False))
            and not bool(getattr(region, "disabled_region", False))
        ]
        for region in [*skipped_regions, *disabled_regions]:
            self._restore_original_region_pixels(source_rgb, inpainted_rgb, region)
        for region in manual_render_regions:
            self._erase_manual_region_pixels(inpainted_rgb, region)

        for region in render_regions:
            region._alignment = config["render_alignment"]
            if not getattr(region, "letter_spacing_override_active", False):
                region.letter_spacing = config["render_letter_spacing"]

        if debug_output_dir is None:
            rendered_rgb = await dispatch_rendering(
                inpainted_rgb.copy(),
                render_regions,
                font_path=config["font_path"],
                font_size_fixed=None,
                font_size_offset=-6,
                font_size_minimum=8,
                hyphenate=True,
                render_mask=None,
                line_spacing=None,
                disable_font_border=False,
            )
        else:
            debug_output_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(
                str(debug_output_dir / "00_base.png"),
                cv2.cvtColor(inpainted_rgb, cv2.COLOR_RGB2BGR),
            )

            rendered_rgb = inpainted_rgb.copy()
            dst_points_list = resize_regions_to_font_size(
                rendered_rgb,
                render_regions,
                None,
                -6,
                8,
                True,
                None,
                config["font_path"],
            )

            render_steps: list[dict[str, Any]] = []
            for index, (region, dst_points) in enumerate(zip(render_regions, dst_points_list), start=1):
                rendered_rgb = render_region(
                    rendered_rgb,
                    region,
                    dst_points,
                    True,
                    None,
                    False,
                    config["font_path"],
                )
                step_name = f"step_{index:02d}.png"
                cv2.imwrite(
                    str(debug_output_dir / step_name),
                    cv2.cvtColor(rendered_rgb, cv2.COLOR_RGB2BGR),
                )
                render_steps.append(
                    {
                        "index": index - 1,
                        "step_image": step_name,
                        "bbox": list(self._region_bbox(region)),
                        "font_style": str(getattr(region, "font_style", "") or ""),
                        "font_family": os.path.basename(str(getattr(region, "font_family", "") or "")),
                        "translation": self._region_display_text(region),
                    }
                )

            diff_rgb = cv2.absdiff(rendered_rgb, inpainted_rgb)
            cv2.imwrite(
                str(debug_output_dir / "99_text_diff.png"),
                cv2.cvtColor(diff_rgb, cv2.COLOR_RGB2BGR),
            )
            (debug_output_dir / "render_steps.json").write_text(
                json.dumps(render_steps, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        result_image = self._rendered_rgb_to_pil_image(source_path, rendered_rgb)

        self._save_result_atomic(result_image, output_path, save_quality=100)

    def _rendered_rgb_to_pil_image(self, source_path: Path, rendered_rgb: np.ndarray) -> Any:
        from PIL import Image

        result_rgb = Image.fromarray(np.asarray(rendered_rgb, dtype=np.uint8), "RGB")
        result_image = result_rgb.convert("RGBA")

        try:
            with Image.open(source_path) as source_image:
                has_alpha = (
                    source_image.mode in {"RGBA", "LA"}
                    or (source_image.mode == "P" and "transparency" in source_image.info)
                )
                if not has_alpha:
                    return result_image
                alpha_channel = source_image.convert("RGBA").getchannel("A")
        except Exception:
            return result_image

        if alpha_channel.size != result_image.size:
            alpha_channel = alpha_channel.resize(result_image.size)
        result_image.putalpha(alpha_channel)
        return result_image

    def _save_result_atomic(self, result_image: Any, output_path: Path, save_quality: int = 100) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        suffix = output_path.suffix.lower()
        quality = max(1, min(100, int(save_quality or 100)))

        fd, temp_name = tempfile.mkstemp(
            dir=str(output_path.parent),
            suffix=suffix or ".tmp",
        )
        os.close(fd)
        temp_path = Path(temp_name)

        try:
            if suffix in {".jpg", ".jpeg"}:
                rgb_image = result_image.convert("RGB")
                rgb_image.save(
                    temp_path,
                    quality=quality,
                    format="JPEG",
                    subsampling=0,
                )
            elif suffix == ".png":
                result_image.save(temp_path, format="PNG")
            elif suffix == ".webp":
                result_image.save(temp_path, format="WEBP", quality=quality, lossless=True)
            else:
                result_image.save(temp_path)

            self._replace_file_with_retry(temp_path, output_path)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def _replace_file_with_retry(self, source_path: Path, target_path: Path) -> None:
        retry_delays = (0.08, 0.12, 0.2, 0.35, 0.5, 0.8, 1.2)
        last_error: Exception | None = None

        for delay in (*retry_delays, None):
            try:
                os.replace(source_path, target_path)
                return
            except PermissionError as exc:
                last_error = exc
                if delay is None:
                    break
                time.sleep(delay)
            except OSError as exc:
                last_error = exc
                if delay is None:
                    break
                time.sleep(delay)

        raise RuntimeError(
            f"无法替换输出文件，目标文件可能仍被占用：{target_path}"
        ) from last_error

    def _clear_directory(self, directory: Path) -> None:
        for child in directory.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    def _format_failure(self, log_path: Path) -> str:
        if not log_path.exists():
            return "manga-image-translator 执行失败，且没有生成日志。"

        pre_render_failure = self._detect_pre_render_failure(log_path, "manga-image-translator 执行")
        if pre_render_failure:
            return pre_render_failure

        lines = deque(log_path.read_text(encoding="utf-8", errors="ignore").splitlines(), maxlen=24)
        if not lines:
            return "manga-image-translator 执行失败，请检查依赖是否安装完整。"

        return "manga-image-translator 执行失败:\n" + "\n".join(lines)

    def _format_missing_rerender_cache_failure(
        self,
        log_path: Path,
        stage_label: str,
        default_message: str,
    ) -> str:
        pre_render_failure = self._detect_pre_render_failure(log_path, stage_label)
        if pre_render_failure:
            return pre_render_failure
        return default_message

    def _detect_pre_render_failure(self, log_path: Path, stage_label: str) -> str:
        if not log_path.exists():
            return ""

        content = log_path.read_text(encoding="utf-8", errors="ignore")
        if not content.strip():
            return ""

        tail_lines = deque(content.splitlines(), maxlen=18)
        never_reached_render = "Running rendering" not in content and 'Saving "' not in content

        if "ChunkedEncodingError" in content or "IncompleteRead(" in content:
            download_dir = self._extract_log_match(content, r"Downloading models into\s+([^\n\r]+)")
            download_url = self._extract_log_match(content, r'-- Downloading:\s+"([^"]+)"')
            detail_lines = [
                f"{stage_label}未真正完成：模型下载过程中网络连接中断，因此没有生成任何可校对缓存。"
            ]
            if download_url:
                detail_lines.append(f"下载地址：{download_url}")
            if download_dir:
                detail_lines.append(f"本地模型目录：{download_dir}")
            detail_lines.append("建议重试；如果问题反复出现，可先手动下载对应模型文件到本地后再继续。")
            detail_lines.append("日志摘要：")
            detail_lines.extend(tail_lines)
            return "\n".join(detail_lines)

        if "Downloading models into" in content and "Traceback" in content:
            download_dir = self._extract_log_match(content, r"Downloading models into\s+([^\n\r]+)")
            detail_lines = [
                f"{stage_label}未真正完成：模型准备阶段发生异常，因此没有生成任何可校对缓存。"
            ]
            if download_dir:
                detail_lines.append(f"本地模型目录：{download_dir}")
            detail_lines.append("日志摘要：")
            detail_lines.extend(tail_lines)
            return "\n".join(detail_lines)

        if "Traceback" in content and never_reached_render:
            return (
                f"{stage_label}未真正完成：引擎在渲染前发生异常，因此没有生成任何可校对缓存。\n"
                "日志摘要：\n"
                + "\n".join(tail_lines)
            )

        return ""

    def _extract_log_match(self, content: str, pattern: str) -> str:
        if not content:
            return ""
        match = re.search(pattern, content)
        if not match:
            return ""
        return str(match.group(1) or "").strip()

    def _format_quality_failure(self, log_path: Path, target_lang: str | None) -> str:
        if not log_path.exists():
            return ""

        content = log_path.read_text(encoding="utf-8", errors="ignore")
        failure_markers = (
            "Page-level target language check failed after all",
            "Batch-level target language check failed after all",
            "Single image target language check failed after all",
        )
        if not any(marker in content for marker in failure_markers):
            return ""

        model_name = self._extract_model_name_from_log(content)
        lang_label = {
            "CHS": "简体中文",
            "CHT": "繁體中文",
            "ENG": "英文",
            "JPN": "日文",
        }.get(str(target_lang or "").strip().upper(), "目标语言")
        lines = deque(content.splitlines(), maxlen=18)
        model_hint = self._format_quality_failure_model_hint(model_name)
        return (
            f"翻译模型连续未能产出有效的{lang_label}结果，本次输出已中止，避免生成乱码嵌字图。\n"
            "这通常是模型不适配当前 OCR 文本，或 OCR 结果本身质量过差导致的。\n"
            f"{model_hint}\n"
            "日志摘要：\n"
            + "\n".join(lines)
        )

    def _extract_model_name_from_log(self, content: str) -> str:
        if not content:
            return ""
        match = re.search(r"Using Responses API for model:\s*([^\s]+)", content)
        if match:
            return str(match.group(1) or "").strip()
        return ""

    def _format_quality_failure_model_hint(self, model_name: str) -> str:
        normalized = str(model_name or "").strip().lower()
        if normalized.startswith("doubao-seed-translation"):
            return "建议先检查 OCR 结果是否过脏，或改用“识别后先校对再翻译”的流程。"
        if normalized.startswith("doubao-seed-2-0-lite"):
            return (
                f"当前使用的是 {model_name}，它更偏轻量通用文本，不太适合 OCR 噪声较重的漫画翻译。\n"
                "建议切换到 doubao-seed-translation-250915，或先手动校对识别框后再翻译。"
            )
        if normalized.startswith("doubao-seed-2-0-"):
            return (
                f"当前使用的是 {model_name}，它属于通用文本模型，在 OCR 噪声较重的漫画翻译场景下可能不稳定。\n"
                "建议优先改用 doubao-seed-translation-250915。"
            )
        return "建议先切换更稳的翻译模型，或改用“识别后先校对再翻译”的流程。"
