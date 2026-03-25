from __future__ import annotations

import asyncio
import json
import shutil
import sys
import os
import re
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

import cv2
import numpy as np

from patch_pydensecrf import patch_mask_refinement
from .image_cleanup import DEFAULT_IMAGE_CLEANUP_PROMPT, create_image_cleanup_client


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


class TranslatorEngine:
    IMAGE_CLEANUP_TIMEOUT_SECONDS = 120
    IMAGE_CLEANUP_MAX_EDGE = 1280
    DOUBAO_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
    DOUBAO_DEFAULT_MODEL = "doubao-seed-translation-250915"
    STYLE_BUCKETS = ("gothic", "mincho", "rounded", "cartoon", "handwritten", "sfx")
    DOUBAO_ALLOWED_MODELS = {
        "doubao-seed-translation-250915",
        "doubao-seed-2-0-mini-260215",
    }

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.temp_dir = self.base_dir / "temp_uploads"
        self.model_dir = self.base_dir / "models"
        self.rerender_cache_root = self.temp_dir / "rerender_cache"
        self.project_font_dir = self.base_dir.parent / "fonts"
        self.builtin_font_dir = self.base_dir / "manga-image-translator" / "fonts"
        self.model_dir.mkdir(exist_ok=True)
        self.rerender_cache_root.mkdir(parents=True, exist_ok=True)

    async def translate_session(
        self,
        session_id: str,
        session: dict[str, Any],
        raw_config: dict[str, Any] | None,
        progress_callback: ProgressCallback,
    ) -> dict[str, str]:
        self._ensure_runtime_patches()
        config = self._normalize_config(raw_config)
        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        cache_dir = self._prepare_rerender_cache_dir(session_id, reset=True)
        session["rerender_cache_dir"] = str(cache_dir)
        mask_debug_dir = self._prepare_mask_debug_dir(session_id, reset=True) if config["export_mask_debug"] else None
        session["mask_debug_dir"] = str(mask_debug_dir) if mask_debug_dir is not None else None
        output_dir.mkdir(parents=True, exist_ok=True)
        self._clear_directory(output_dir)

        config_path = self._write_config(session_id, config)
        log_path = self.temp_dir / f"{session_id}_translation.log"

        expected_outputs = [
            output_dir / Path(image["stored_name"])
            for image in session["source_images"]
        ]
        complex_images = self._select_complex_repair_images(session, source_dir, config)
        deferred_output_names = {image["stored_name"] for image in complex_images}
        should_apply_font_style_map = self._has_style_font_overrides(config)
        if should_apply_font_style_map:
            deferred_output_names = {image["stored_name"] for image in session["source_images"]}
            await progress_callback(
                {
                    "event": "status",
                    "message": "已启用字体样式映射，翻译完成后会自动按黑体 / 宋体 / 圆体 / 卡通 / 手写 / 拟声重新嵌字。",
                }
            )
        session["deferred_output_names"] = deferred_output_names

        command = self._build_command(source_dir, output_dir, config_path, config)

        reported: set[Path] = set()
        total = len(expected_outputs)
        await progress_callback({"event": "start", "total_pages": total})

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

        if should_apply_font_style_map:
            await self._apply_font_style_rerender(
                session_id=session_id,
                session=session,
                config=config,
                progress_callback=progress_callback,
            )

        if complex_images:
            if self._wants_ai_image_cleanup(config):
                if self._has_image_cleanup_key(config):
                    enhanced_count = await self._ai_clean_complex_pages(
                        session_id=session_id,
                        session=session,
                        config=config,
                        complex_images=complex_images,
                        progress_callback=progress_callback,
                    )
                else:
                    enhanced_count = 0
                    await progress_callback(
                        {
                            "event": "status",
                            "message": "已启用 Gemini 图像去字，但没有可用 API Key，已保留稳定版输出。",
                        }
                    )
            else:
                enhanced_count = await self._enhance_complex_pages(
                    session_id=session_id,
                    session=session,
                    config=config,
                    complex_images=complex_images,
                    progress_callback=progress_callback,
                )
            if enhanced_count:
                print(f"[DEBUG] Enhanced repair finished for {enhanced_count} complex page(s).")
        if session.get("deferred_output_names"):
            session["deferred_output_names"] = set()
            await self._emit_completed_images(
                session_id,
                session,
                expected_outputs,
                reported,
                progress_callback,
            )

        archive_base = self.temp_dir / f"{session_id}_translated"
        archive_path = shutil.make_archive(
            str(archive_base),
            "zip",
            root_dir=str(output_dir),
        )
        session["download_path"] = archive_path
        session["last_config"] = config

        return {
            "download_url": f"/api/download/{session_id}",
            "download_path": str(Path(archive_path).resolve()),
            "translated_dir": str(output_dir.resolve()),
            "mask_debug_dir": str(mask_debug_dir.resolve()) if mask_debug_dir is not None else "",
        }

    async def rerender_session(
        self,
        session_id: str,
        session: dict[str, Any],
        raw_config: dict[str, Any] | None,
        progress_callback: ProgressCallback,
    ) -> dict[str, str]:
        self._ensure_runtime_patches()
        config = self._normalize_config(raw_config)
        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        rerenderable_pages = self._count_rerenderable_pages(session_id, session)
        if rerenderable_pages == 0:
            raise RuntimeError("当前会话还没有可用的重嵌字缓存，请先用当前版本完整翻译一次。")

        total = len(session["source_images"])
        await progress_callback({"event": "start", "total_pages": total})
        await progress_callback(
            {
                "event": "status",
                "message": f"正在复用缓存重新嵌字，可重排页面 {rerenderable_pages} 张。",
            }
        )

        for index, image in enumerate(session["source_images"], start=1):
            source_path = source_dir / image["stored_name"]
            output_path = output_dir / image["stored_name"]
            cache_page_dir = self._rerender_cache_dir(session_id) / image["stored_name"]

            if self._has_rerenderable_page_cache(cache_page_dir):
                await self._render_cached_page(source_path, output_path, cache_page_dir, config)
            elif not output_path.exists():
                shutil.copy2(source_path, output_path)

            await progress_callback(
                {
                    "event": "progress",
                    "current": index,
                    "total": total,
                    "image_url": f"/output/{session_id}/translated/{output_path.name}",
                    "name": image["name"],
                }
            )

        archive_base = self.temp_dir / f"{session_id}_translated"
        archive_path = shutil.make_archive(
            str(archive_base),
            "zip",
            root_dir=str(output_dir),
        )
        session["download_path"] = archive_path
        session["last_config"] = config

        return {
            "download_url": f"/api/download/{session_id}",
            "download_path": str(Path(archive_path).resolve()),
            "translated_dir": str(output_dir.resolve()),
            "mask_debug_dir": str(Path(session["mask_debug_dir"]).resolve()) if session.get("mask_debug_dir") else "",
        }

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
        total = len(expected_outputs)
        deferred_output_names = session.get("deferred_output_names", set())

        for index, output_path in enumerate(expected_outputs, start=1):
            if output_path in reported or not output_path.exists():
                continue

            if output_path.name in deferred_output_names:
                continue

            reported.add(output_path)
            source_meta = session["source_images"][index - 1]
            await progress_callback(
                {
                    "event": "progress",
                    "current": len(reported),
                    "total": total,
                    "image_url": f"/output/{session_id}/translated/{output_path.name}",
                    "name": source_meta["name"],
                }
            )

    def _normalize_config(self, raw_config: dict[str, Any] | None) -> dict[str, Any]:
        raw_config = raw_config or {}
        selected_translator = str(raw_config.get("translator") or "gemini").strip() or "gemini"
        translator = selected_translator
        target_lang = str(raw_config.get("target_lang") or "CHS").strip().upper() or "CHS"
        translator_model = self._normalize_translator_model(
            selected_translator,
            raw_config.get("translator_model"),
        )

        # Bug fix: Some translators use different language codes for Chinese
        # For example, sugoi might not support CHS, but only JPN/ENG
        # If it's CHS/CHT, the backend needs to convert it appropriately based on the translator
        # But looking at the logs: Language unsupported exception for SugoiTranslator: "CHS"
        # Sugoi only supports Japanese to English translations!
        if translator == "sugoi" and target_lang in ["CHS", "CHT"]:
            # Fall back to gemini for Chinese if user selected Sugoi but wants Chinese
            print(f"[DEBUG] Sugoi translator does not support {target_lang}. Falling back to 'gemini'")
            translator = "gemini"
        elif selected_translator == "doubao-ark":
            translator = "custom_openai"

        use_gpu = bool(raw_config.get("use_gpu", True))
        api_key = str(raw_config.get("api_key", "")).strip()
        font_key = str(raw_config.get("font_key", "")).strip()
        font_path = self._resolve_font_path(font_key)
        render_alignment = self._normalize_render_alignment(raw_config.get("render_alignment"))
        render_letter_spacing = self._normalize_render_letter_spacing(raw_config.get("render_letter_spacing"))
        font_style_mode = self._normalize_font_style_mode(raw_config.get("font_style_mode"))
        image_cleanup_mode = self._normalize_image_cleanup_mode(raw_config.get("image_cleanup_mode"))
        image_cleanup_model = self._normalize_image_cleanup_model(
            image_cleanup_mode,
            raw_config.get("image_cleanup_model"),
        )
        image_cleanup_api_key = str(raw_config.get("image_cleanup_api_key", "")).strip()
        mask_cleanup_strength = self._normalize_mask_cleanup_strength(raw_config.get("mask_cleanup_strength"))
        export_mask_debug = bool(raw_config.get("export_mask_debug", False))
        style_font_keys = self._normalize_style_font_keys(raw_config)
        style_font_paths = {
            style: self._resolve_font_path(font_key)
            for style, font_key in style_font_keys.items()
        }
        style_region_overrides = self._normalize_style_region_overrides(raw_config.get("style_region_overrides"))
        translation_region_overrides = self._normalize_translation_region_overrides(
            raw_config.get("translation_region_overrides")
        )

        return {
            "translator": translator,
            "selected_translator": selected_translator,
            "translator_model": translator_model,
            "target_lang": target_lang,
            "use_gpu": use_gpu,
            "api_key": api_key,
            "font_key": font_key,
            "font_path": font_path,
            "font_style_mode": font_style_mode,
            "style_font_keys": style_font_keys,
            "style_font_paths": style_font_paths,
            "style_region_overrides": style_region_overrides,
            "translation_region_overrides": translation_region_overrides,
            "render_alignment": render_alignment,
            "render_letter_spacing": render_letter_spacing,
            "mask_cleanup_strength": mask_cleanup_strength,
            "export_mask_debug": export_mask_debug,
            "advanced_text_repair": self._normalize_advanced_text_repair(raw_config.get("advanced_text_repair")),
            "image_cleanup_mode": image_cleanup_mode,
            "image_cleanup_model": image_cleanup_model,
            "image_cleanup_api_key": image_cleanup_api_key,
        }

    def _normalize_advanced_text_repair(self, raw_value: Any) -> str:
        value = str(raw_value or "auto").strip().lower()
        if value not in {"auto", "off", "force"}:
            return "auto"
        return value

    def _normalize_translator_model(self, translator: str, raw_value: Any) -> str:
        value = str(raw_value or "").strip()
        if translator == "doubao-ark" and value in self.DOUBAO_ALLOWED_MODELS:
            return value
        if translator == "doubao-ark":
            return self.DOUBAO_DEFAULT_MODEL
        return ""

    def _normalize_image_cleanup_mode(self, raw_value: Any) -> str:
        value = str(raw_value or "off").strip().lower()
        if value not in {"off", "gemini-image", "seedream-image"}:
            return "off"
        return value

    def _normalize_render_alignment(self, raw_value: Any) -> str:
        value = str(raw_value or "left").strip().lower()
        if value not in {"auto", "left", "center", "right"}:
            return "left"
        return value

    def _normalize_font_style_mode(self, raw_value: Any) -> str:
        value = str(raw_value or "single").strip().lower()
        if value not in {"single", "auto-map"}:
            return "single"
        return value

    def _normalize_render_letter_spacing(self, raw_value: Any) -> float:
        try:
            value = float(raw_value if raw_value is not None else 1.08)
        except (TypeError, ValueError):
            value = 1.08
        return max(0.85, min(1.35, round(value, 2)))

    def _normalize_style_bucket(self, raw_value: Any) -> str:
        value = str(raw_value or "").strip().lower()
        if value in self.STYLE_BUCKETS:
            return value
        return ""

    def _normalize_style_font_keys(self, raw_config: dict[str, Any]) -> dict[str, str]:
        return {
            "gothic": str(raw_config.get("style_font_gothic_key", "")).strip(),
            "mincho": str(raw_config.get("style_font_mincho_key", "")).strip(),
            "rounded": str(raw_config.get("style_font_rounded_key", "")).strip(),
            "cartoon": str(raw_config.get("style_font_cartoon_key", "")).strip(),
            "handwritten": str(raw_config.get("style_font_handwritten_key", "")).strip(),
            "sfx": str(raw_config.get("style_font_sfx_key", "")).strip(),
        }

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
            text = str(value or "").strip()
            if text:
                normalized[key] = text
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

        font_dirs = {
            "project": self.project_font_dir,
            "builtin": self.builtin_font_dir,
        }

        if ":" in font_key:
            source, font_name = font_key.split(":", 1)
            font_dir = font_dirs.get(source)
            if font_dir:
                resolved = font_dir / font_name
                if resolved.exists():
                    return str(resolved.resolve())

        for font_dir in font_dirs.values():
            resolved = font_dir / font_key
            if resolved.exists():
                return str(resolved.resolve())

        print(f"[WARN] Requested font not found: {font_key}")
        return ""

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
        if session_id and config.get("export_mask_debug"):
            env["MT_MASK_DEBUG_DIR"] = str(self._prepare_mask_debug_dir(session_id, reset=False))
        if session_id:
            env["MT_RERENDER_CACHE_DIR"] = str(self._prepare_rerender_cache_dir(session_id, reset=False))
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
        for image in session["source_images"]:
            source_path = source_dir / image["stored_name"]
            output_path = output_dir / image["stored_name"]
            cache_page_dir = self._rerender_cache_dir(session_id) / image["stored_name"]
            if not self._has_rerenderable_page_cache(cache_page_dir):
                continue
            await self._render_cached_page(source_path, output_path, cache_page_dir, config)

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
    ) -> dict[str, Any]:
        config = self._normalize_config(raw_config)
        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        pages: list[dict[str, Any]] = []

        for image in session["source_images"]:
            cache_page_dir = self._rerender_cache_dir(session_id) / image["stored_name"]
            if not self._has_rerenderable_page_cache(cache_page_dir):
                continue

            source_path = source_dir / image["stored_name"]
            source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
            if source_bgr is None:
                continue
            source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)

            regions = self._load_cached_regions(cache_page_dir)
            self._apply_region_translation_overrides(regions, config, image["stored_name"])
            self._apply_region_font_styles(source_rgb, regions, config, image["stored_name"])

            preview_path = output_dir / image["stored_name"]
            preview_url = (
                f"/output/{session_id}/translated/{image['stored_name']}"
                if preview_path.exists()
                else f"/output/{session_id}/source/{image['stored_name']}"
            )

            region_payloads: list[dict[str, Any]] = []
            for index, region in enumerate(regions):
                x1, y1, x2, y2 = [int(v) for v in getattr(region, "xyxy", [0, 0, 0, 0])]
                region_payloads.append(
                    {
                        "id": getattr(region, "style_region_key", self._make_style_region_key(image["stored_name"], index)),
                        "index": index,
                        "bbox": [x1, y1, x2, y2],
                        "auto_style": getattr(region, "auto_font_style", ""),
                        "override_style": getattr(region, "override_font_style", ""),
                        "resolved_style": getattr(region, "font_style", ""),
                        "font_family": str(getattr(region, "font_family", "") or ""),
                        "source_text": self._region_source_text(region),
                        "translation": str(getattr(region, "translation", "") or ""),
                        "preview_text": self._region_preview_text(region),
                    }
                )

            pages.append(
                {
                    "stored_name": image["stored_name"],
                    "name": image["name"],
                    "image_url": preview_url,
                    "image_width": int(source_rgb.shape[1]),
                    "image_height": int(source_rgb.shape[0]),
                    "regions": region_payloads,
                }
            )

        return {
            "styles": list(self.STYLE_BUCKETS),
            "pages": pages,
        }

    async def inspect_translation_regions(
        self,
        session_id: str,
        session: dict[str, Any],
        raw_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        config = self._normalize_config(raw_config)
        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        pages: list[dict[str, Any]] = []

        for image in session["source_images"]:
            cache_page_dir = self._rerender_cache_dir(session_id) / image["stored_name"]
            if not self._has_rerenderable_page_cache(cache_page_dir):
                continue

            source_path = source_dir / image["stored_name"]
            source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
            if source_bgr is None:
                continue
            source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)

            regions = self._load_cached_regions(cache_page_dir)
            self._apply_region_translation_overrides(regions, config, image["stored_name"])

            preview_path = output_dir / image["stored_name"]
            preview_url = (
                f"/output/{session_id}/translated/{image['stored_name']}"
                if preview_path.exists()
                else f"/output/{session_id}/source/{image['stored_name']}"
            )

            region_payloads: list[dict[str, Any]] = []
            for index, region in enumerate(regions):
                x1, y1, x2, y2 = [int(v) for v in getattr(region, "xyxy", [0, 0, 0, 0])]
                region_payloads.append(
                    {
                        "id": getattr(region, "translation_region_key", self._make_style_region_key(image["stored_name"], index)),
                        "index": index,
                        "bbox": [x1, y1, x2, y2],
                        "source_text": self._region_source_text(region),
                        "machine_translation": str(getattr(region, "machine_translation", "") or ""),
                        "override_translation": str(getattr(region, "translation_override", "") or ""),
                        "current_translation": str(getattr(region, "translation", "") or ""),
                        "preview_text": self._region_preview_text(region),
                    }
                )

            pages.append(
                {
                    "stored_name": image["stored_name"],
                    "name": image["name"],
                    "image_url": preview_url,
                    "image_width": int(source_rgb.shape[1]),
                    "image_height": int(source_rgb.shape[0]),
                    "regions": region_payloads,
                }
            )

        return {"pages": pages}

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
            cache_page_dir = self._rerender_cache_dir(session_id) / image["stored_name"]

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

    def _count_rerenderable_pages(self, session_id: str, session: dict[str, Any]) -> int:
        cache_dir = Path(session.get("rerender_cache_dir") or self._rerender_cache_dir(session_id))
        return sum(
            1
            for image in session["source_images"]
            if self._has_rerenderable_page_cache(cache_dir / image["stored_name"])
        )

    def _has_rerenderable_page_cache(self, page_cache_dir: Path) -> bool:
        return (
            page_cache_dir.exists()
            and (page_cache_dir / "inpainted.png").exists()
            and (page_cache_dir / "regions.json").exists()
        )

    def _ensure_vendor_import_path(self) -> None:
        vendor_root = str(self.base_dir / "manga-image-translator")
        if vendor_root not in sys.path:
            sys.path.insert(0, vendor_root)

    def _load_cached_regions(self, page_cache_dir: Path) -> list[Any]:
        regions_path = page_cache_dir / "regions.json"
        region_payloads = json.loads(regions_path.read_text(encoding="utf-8"))
        return [self._deserialize_text_region(payload) for payload in region_payloads]

    def _make_style_region_key(self, stored_name: str, index: int) -> str:
        return f"{stored_name}::{index}"

    def _region_source_text(self, region: Any) -> str:
        text_value = str(getattr(region, "text", "") or "").strip()
        if text_value:
            return text_value
        texts = getattr(region, "texts", None)
        if isinstance(texts, list):
            joined = "".join(str(item or "") for item in texts).strip()
            if joined:
                return joined
        return ""

    def _region_preview_text(self, region: Any) -> str:
        preview = str(getattr(region, "translation", "") or "").strip()
        if preview:
            return preview
        return self._region_source_text(region)

    def _deserialize_text_region(self, payload: dict[str, Any]) -> Any:
        self._ensure_vendor_import_path()
        from manga_translator.utils.textblock import TextBlock

        texts = payload.get("texts") or ([payload.get("text", "")] if payload.get("text") is not None else [])
        region = TextBlock(
            lines=payload.get("lines", []),
            texts=texts,
            language=payload.get("language", "unknown"),
            font_size=payload.get("font_size", -1),
            angle=payload.get("angle", 0),
            translation=payload.get("translation", ""),
            fg_color=tuple(payload.get("fg_colors") or payload.get("fg_color") or (0, 0, 0)),
            bg_color=tuple(payload.get("bg_colors") or payload.get("bg_color") or (0, 0, 0)),
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

        fg_color = tuple(getattr(region, "fg_colors", getattr(region, "fg_color", (0, 0, 0))) or (0, 0, 0))
        bg_color = tuple(getattr(region, "bg_colors", getattr(region, "bg_color", (255, 255, 255))) or (255, 255, 255))
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
        if font_ratio <= 0.84 and char_count <= 12:
            return True
        if stroke_width_var >= 0.52 and component_count >= max(char_count, 2.0):
            return True
        if fill_ratio <= 0.12 and mean_circularity <= 0.22 and char_count <= 10:
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
        if font_ratio >= 1.08 and mean_circularity >= 0.34 and stroke_width_mean >= max(2.0, median_font_size * 0.085):
            return True
        if fill_ratio >= 0.22 and mean_circularity >= 0.38:
            return True
        if char_count <= 4 and mean_circularity >= 0.42:
            return True
        return False

    def _looks_like_rounded(self, region: Any, median_font_size: float, features: dict[str, float]) -> bool:
        mean_circularity = features["mean_circularity"]
        corner_density = features["corner_density"]
        stroke_width_var = features["stroke_width_var"]
        fill_ratio = features["fill_ratio"]
        if mean_circularity >= 0.4 and corner_density <= 0.03:
            return True
        if mean_circularity >= 0.34 and stroke_width_var <= 0.3 and fill_ratio >= 0.1:
            return True
        return False

    def _looks_like_mincho(self, region: Any, median_font_size: float, features: dict[str, float]) -> bool:
        stroke_width_var = features["stroke_width_var"]
        corner_density = features["corner_density"]
        mean_circularity = features["mean_circularity"]
        stroke_width_mean = features["stroke_width_mean"]
        if stroke_width_var >= 0.46 and corner_density >= 0.028:
            return True
        if stroke_width_var >= 0.38 and corner_density >= 0.022 and mean_circularity <= 0.32:
            return True
        if stroke_width_mean <= max(1.8, median_font_size * 0.06) and corner_density >= 0.03:
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
        if config.get("font_style_mode") != "auto-map":
            for index, region in enumerate(regions):
                region.font_family = default_font_path
                region.font_style = "single"
                region.auto_font_style = "gothic"
                region.override_font_style = ""
                region.style_region_key = self._make_style_region_key(stored_name, index) if stored_name else str(index)
            return

        font_sizes = [max(float(getattr(region, "font_size", 0) or 0), 1.0) for region in regions]
        median_font_size = float(np.median(font_sizes)) if font_sizes else 12.0

        for index, region in enumerate(regions):
            style_key = self._make_style_region_key(stored_name, index) if stored_name else str(index)
            auto_style = self._classify_region_font_style(source_rgb, region, median_font_size)
            override_style = self._normalize_style_bucket(style_overrides.get(style_key, ""))
            resolved_style = override_style or auto_style
            region.style_region_key = style_key
            region.auto_font_style = auto_style
            region.override_font_style = override_style
            region.font_style = resolved_style
            region.font_family = style_paths.get(resolved_style) or default_font_path

    def _apply_region_translation_overrides(
        self,
        regions: list[Any],
        config: dict[str, Any],
        stored_name: str = "",
    ) -> None:
        overrides = config.get("translation_region_overrides") or {}
        for index, region in enumerate(regions):
            region_key = self._make_style_region_key(stored_name, index) if stored_name else str(index)
            machine_translation = str(getattr(region, "translation", "") or "")
            override_translation = str(overrides.get(region_key, "") or "").strip()
            region.translation_region_key = region_key
            region.machine_translation = machine_translation
            region.translation_override = override_translation
            if override_translation:
                region.translation = override_translation

    async def _render_cached_page(
        self,
        source_path: Path,
        output_path: Path,
        page_cache_dir: Path,
        config: dict[str, Any],
        base_image_rgb: np.ndarray | None = None,
    ) -> None:
        self._ensure_vendor_import_path()
        from PIL import Image
        from manga_translator.rendering import dispatch as dispatch_rendering
        from manga_translator.save import save_result
        from manga_translator.utils import Context, dump_image, load_image

        source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if source_bgr is None:
            raise RuntimeError(f"无法读取原图: {source_path}")
        source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)
        inpainted_bgr = cv2.imread(str(page_cache_dir / "inpainted.png"), cv2.IMREAD_COLOR)
        if inpainted_bgr is None:
            raise RuntimeError(f"重嵌字缓存损坏，无法读取底图: {page_cache_dir / 'inpainted.png'}")

        source_image = Image.open(source_path)
        _, alpha_ch = load_image(source_image)
        inpainted_rgb = (
            base_image_rgb.copy()
            if base_image_rgb is not None
            else cv2.cvtColor(inpainted_bgr, cv2.COLOR_BGR2RGB)
        )
        regions = self._load_cached_regions(page_cache_dir)
        self._apply_region_translation_overrides(regions, config, source_path.name)
        self._apply_region_font_styles(source_rgb, regions, config, source_path.name)
        for region in regions:
            region._alignment = config["render_alignment"]
            region.letter_spacing = config["render_letter_spacing"]

        rendered_rgb = await dispatch_rendering(
            inpainted_rgb.copy(),
            regions,
            font_path=config["font_path"],
            font_size_fixed=None,
            font_size_offset=-6,
            font_size_minimum=8,
            hyphenate=True,
            render_mask=None,
            line_spacing=None,
            disable_font_border=False,
        )
        result_image = dump_image(source_image, rendered_rgb, alpha_ch)

        save_ctx = Context(save_quality=100, text_regions=regions, result=result_image)
        self._save_result_atomic(result_image, output_path, save_ctx)

    def _save_result_atomic(self, result_image: Any, output_path: Path, save_ctx: Any) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        suffix = output_path.suffix.lower()

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
                    quality=getattr(save_ctx, "save_quality", 100),
                    format="JPEG",
                    subsampling=0,
                )
            elif suffix == ".png":
                result_image.save(temp_path, format="PNG")
            elif suffix == ".webp":
                result_image.save(temp_path, format="WEBP", quality=100, lossless=True)
            else:
                save_result(result_image, str(temp_path), save_ctx)

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

        lines = deque(log_path.read_text(encoding="utf-8", errors="ignore").splitlines(), maxlen=24)
        if not lines:
            return "manga-image-translator 执行失败，请检查依赖是否安装完整。"

        return "manga-image-translator 执行失败:\n" + "\n".join(lines)
