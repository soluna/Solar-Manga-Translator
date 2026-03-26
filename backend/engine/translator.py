from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sys
import os
import re
import tempfile
import time
import uuid
import zipfile
from difflib import SequenceMatcher
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
    TRANSLATED_OUTPUT_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
    DOUBAO_CURATED_MODELS = {
        "doubao-seed-translation-250915",
        "doubao-seed-2-0-pro-260215",
        "doubao-seed-2-0-lite-260215",
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
        session["translated_output_map"] = {}
        session["download_path"] = None
        session["workflow_stage"] = "translating"
        session["deferred_output_names"] = set()

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

        archive_path = self.build_session_archive(
            session_id=session_id,
            session=session,
            preferred_output_format=config["rerender_output_format"],
        )
        session["download_path"] = archive_path
        session["last_config"] = config
        session["workflow_stage"] = "translated"

        return {
            "download_url": f"/api/download/{session_id}",
            "download_path": str(Path(archive_path).resolve()),
            "translated_dir": str(output_dir.resolve()),
            "mask_debug_dir": str(mask_debug_dir.resolve()) if mask_debug_dir is not None else "",
            "workflow_stage": session["workflow_stage"],
        }

    async def detect_session(
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
        output_dir.mkdir(parents=True, exist_ok=True)
        self._clear_directory(output_dir)
        session["translated_output_map"] = {}
        session["download_path"] = None
        session["workflow_stage"] = "detecting"
        session["deferred_output_names"] = set()

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
                "message": "正在先识别文本框并建立可校对缓存，翻译会在你确认后再继续。",
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

        session["last_config"] = config
        session["workflow_stage"] = "detected"
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
    ) -> dict[str, str]:
        self._ensure_runtime_patches()
        config = self._normalize_config(raw_config)
        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        session["download_path"] = None
        session["workflow_stage"] = "translating"
        session["deferred_output_names"] = set()

        source_images = list(session.get("source_images") or [])
        total = len(source_images)
        await progress_callback({"event": "start", "total_pages": total})
        await progress_callback(
            {
                "event": "status",
                "message": "正在根据你确认后的文本框继续翻译并嵌字…",
            }
        )

        for index, image in enumerate(source_images, start=1):
            stored_name = str(image.get("stored_name") or "")
            source_path = source_dir / stored_name
            cache_page_dir = self._rerender_cache_dir(session_id) / stored_name
            if not self._ensure_rerenderable_page_cache(source_path, cache_page_dir):
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

            await progress_callback(
                {
                    "event": "progress",
                    "current": index,
                    "total": total,
                    "image_url": f"/output/{session_id}/translated/{output_path.name}",
                    "stored_name": stored_name,
                    "name": image["name"],
                }
            )

        complex_images = self._select_complex_repair_images(session, source_dir, config)
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

        archive_path = self.build_session_archive(
            session_id=session_id,
            session=session,
            preferred_output_format=config["rerender_output_format"],
        )
        session["download_path"] = archive_path
        session["last_config"] = config
        session["workflow_stage"] = "translated"

        return {
            "download_url": f"/api/download/{session_id}",
            "download_path": str(Path(archive_path).resolve()),
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
        config = self._normalize_config(raw_config)
        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        target_images = self._resolve_rerender_images(session, target_stored_name)
        rerenderable_pages = sum(
            1
            for image in target_images
            if self._has_rerenderable_page_cache(self._rerender_cache_dir(session_id) / image["stored_name"])
        )
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
            cache_page_dir = self._rerender_cache_dir(session_id) / image["stored_name"]

            if self._has_rerenderable_page_cache(cache_page_dir):
                prepared_regions = None
                debug_info = None
                if style_debug_enabled:
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
                    "image_url": f"/output/{session_id}/translated/{output_path.name}",
                    "stored_name": image["stored_name"],
                    "name": image["name"],
                }
            )

        archive_path = self.build_session_archive(
            session_id=session_id,
            session=session,
            preferred_output_format=config["rerender_output_format"],
        )
        session["download_path"] = archive_path
        session["last_config"] = config
        session["workflow_stage"] = "translated"

        return {
            "download_url": f"/api/download/{session_id}",
            "download_path": str(Path(archive_path).resolve()),
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
        archive_base = self.temp_dir / f"{session_id}_translated"
        archive_path = self._make_selected_archive(Path(f"{archive_base}.zip"), archive_files, output_dir)
        session["download_path"] = archive_path
        return archive_path

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
                    "image_url": f"/output/{session_id}/translated/{output_path.name}",
                    "stored_name": source_meta["stored_name"],
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
            raw_config.get("translator_model_custom"),
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
        rerender_output_format = self._normalize_rerender_output_format(raw_config.get("rerender_output_format"))
        pause_after_detection = bool(raw_config.get("pause_after_detection", False))
        style_font_keys = self._normalize_style_font_keys(raw_config)
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
        }

    def _normalize_advanced_text_repair(self, raw_value: Any) -> str:
        value = str(raw_value or "auto").strip().lower()
        if value not in {"auto", "off", "force"}:
            return "auto"
        return value

    def _normalize_translator_model(
        self,
        translator: str,
        raw_value: Any,
        raw_custom_value: Any = None,
    ) -> str:
        value = str(raw_value or "").strip()
        custom_value = str(raw_custom_value or "").strip()
        if translator == "doubao-ark":
            return custom_value or value or self.DOUBAO_DEFAULT_MODEL
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

        for candidate in candidates:
            if candidate.exists():
                return candidate
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
            cache_page_dir = self._rerender_cache_dir(session_id) / image["stored_name"]
            if not self._has_rerenderable_page_cache(cache_page_dir):
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
    ) -> dict[str, Any]:
        config = self._normalize_config(raw_config)
        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        preferred_output_format = self._normalize_rerender_output_format(
            (session.get("last_config") or {}).get("rerender_output_format")
        )
        pages: list[dict[str, Any]] = []

        for image in session["source_images"]:
            cache_page_dir = self._rerender_cache_dir(session_id) / image["stored_name"]
            source_path = source_dir / image["stored_name"]
            self._ensure_rerenderable_page_cache(source_path, cache_page_dir)
            if not self._has_rerenderable_page_cache(cache_page_dir):
                continue

            source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
            if source_bgr is None:
                continue
            source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)

            regions = self._prepare_cached_regions_for_edit(
                source_rgb,
                cache_page_dir,
                config,
                image["stored_name"],
                session=session,
            )

            preview_path = self._current_translated_output(
                session,
                output_dir,
                image["stored_name"],
                preferred_output_format,
            )
            preview_url = (
                f"/output/{session_id}/translated/{preview_path.name}"
                if preview_path is not None
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
                        "manual": bool(getattr(region, "manual_region", False)),
                        "auto_style": getattr(region, "auto_font_style", ""),
                        "override_style": getattr(region, "override_font_style", ""),
                        "resolved_style": getattr(region, "font_style", ""),
                        "font_family": str(getattr(region, "font_family", "") or ""),
                        "source_text": self._region_source_text(region),
                        "translation": str(getattr(region, "translation", "") or ""),
                        "preview_text": self._region_preview_text(region),
                        "font_size": int(max(float(getattr(region, "font_size", 0) or 0), 8)),
                    }
                )

            pages.append(
                {
                    "stored_name": image["stored_name"],
                    "name": image["name"],
                    "image_url": preview_url,
                    "source_image_url": f"/output/{session_id}/source/{image['stored_name']}",
                    "translated_image_url": preview_url,
                    "image_width": int(source_rgb.shape[1]),
                    "image_height": int(source_rgb.shape[0]),
                    "regions": region_payloads,
                }
            )

        return {
            "styles": list(self.STYLE_BUCKETS),
            "pages": pages,
            "workflow_stage": self._session_workflow_stage(session),
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
        preferred_output_format = self._normalize_rerender_output_format(
            (session.get("last_config") or {}).get("rerender_output_format")
        )
        pages: list[dict[str, Any]] = []

        for image in session["source_images"]:
            cache_page_dir = self._rerender_cache_dir(session_id) / image["stored_name"]
            source_path = source_dir / image["stored_name"]
            self._ensure_rerenderable_page_cache(source_path, cache_page_dir)
            if not self._has_rerenderable_page_cache(cache_page_dir):
                continue

            source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
            if source_bgr is None:
                continue
            source_rgb = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2RGB)

            regions = self._prepare_cached_regions_for_edit(
                source_rgb,
                cache_page_dir,
                config,
                image["stored_name"],
                session=session,
            )

            preview_path = self._current_translated_output(
                session,
                output_dir,
                image["stored_name"],
                preferred_output_format,
            )
            preview_url = (
                f"/output/{session_id}/translated/{preview_path.name}"
                if preview_path is not None
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
                        "manual": bool(getattr(region, "manual_region", False)),
                        "source_text": self._region_source_text(region),
                        "machine_translation": str(getattr(region, "machine_translation", "") or ""),
                        "override_translation": str(getattr(region, "translation_override", "") or ""),
                        "override_skip": bool(getattr(region, "skip_translation", False)),
                        "current_translation": str(getattr(region, "translation", "") or ""),
                        "preview_text": self._region_preview_text(region),
                        "font_size": int(max(float(getattr(region, "font_size", 0) or 0), 8)),
                    }
                )

            pages.append(
                {
                    "stored_name": image["stored_name"],
                    "name": image["name"],
                    "image_url": preview_url,
                    "source_image_url": f"/output/{session_id}/source/{image['stored_name']}",
                    "translated_image_url": preview_url,
                    "image_width": int(source_rgb.shape[1]),
                    "image_height": int(source_rgb.shape[0]),
                    "regions": region_payloads,
                }
            )

        return {
            "pages": pages,
            "workflow_stage": self._session_workflow_stage(session),
        }

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

    def _style_rerender_debug_dir(self, session_id: str) -> Path:
        return self.temp_dir / f"{session_id}_style_rerender_debug"

    def _prepare_style_rerender_debug_dir(self, session_id: str, reset: bool) -> Path:
        debug_dir = self._style_rerender_debug_dir(session_id)
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

    def _ensure_rerenderable_page_cache(self, source_path: Path, page_cache_dir: Path) -> bool:
        if self._has_rerenderable_page_cache(page_cache_dir):
            return True

        source_bgr = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if source_bgr is None:
            return False

        page_cache_dir.mkdir(parents=True, exist_ok=True)

        inpainted_path = page_cache_dir / "inpainted.png"
        if not inpainted_path.exists():
            cv2.imwrite(str(inpainted_path), source_bgr)

        regions_path = page_cache_dir / "regions.json"
        if not regions_path.exists():
            regions_path.write_text("[]", encoding="utf-8")

        return self._has_rerenderable_page_cache(page_cache_dir)

    def _ensure_vendor_import_path(self) -> None:
        vendor_root = str(self.base_dir / "manga-image-translator")
        if vendor_root not in sys.path:
            sys.path.insert(0, vendor_root)

    def _load_cached_regions(self, page_cache_dir: Path) -> list[Any]:
        regions_path = page_cache_dir / "regions.json"
        region_payloads = json.loads(regions_path.read_text(encoding="utf-8"))
        return [self._deserialize_text_region(payload) for payload in region_payloads]

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
        resolved_direction = direction or self._direction_from_bbox(bbox)
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
            "fg_color": [int(v) for v in (fg_color or (0, 0, 0))],
            "bg_color": [int(v) for v in (bg_color or (255, 255, 255))],
            "target_lang": target_lang,
            "manual": True,
            "created_at": time.time(),
        }

    def _manual_region_to_text_region(self, payload: dict[str, Any]) -> Any:
        self._ensure_vendor_import_path()
        from manga_translator.utils.textblock import TextBlock

        bbox = payload.get("bbox") or [0, 0, 0, 0]
        direction = str(payload.get("direction") or self._direction_from_bbox(bbox))
        translation = str(payload.get("translation") or payload.get("machine_translation") or "").strip()
        source_text = str(payload.get("source_text") or "").strip()
        region = TextBlock(
            lines=payload.get("lines") or self._manual_region_lines(bbox),
            texts=[source_text],
            language=payload.get("source_lang", "unknown"),
            font_size=payload.get("font_size", max(min(bbox[2] - bbox[0], bbox[3] - bbox[1]), 14)),
            angle=payload.get("angle", 0),
            translation=translation,
            fg_color=tuple(payload.get("fg_color") or (0, 0, 0)),
            bg_color=tuple(payload.get("bg_color") or (255, 255, 255)),
            line_spacing=payload.get("line_spacing", 1.0),
            letter_spacing=payload.get("letter_spacing", 1.0),
            font_family=payload.get("font_family", ""),
            bold=payload.get("bold", False),
            underline=payload.get("underline", False),
            italic=payload.get("italic", False),
            direction=direction,
            alignment=payload.get("alignment", "auto"),
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

        cache_page_dir = self._rerender_cache_dir(session_id) / stored_name
        if not self._ensure_rerenderable_page_cache(source_path, cache_page_dir):
            raise RuntimeError("无法为当前页面建立可编辑缓存，请刷新后重试。")

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
        cache_page_dir = self._rerender_cache_dir(session_id) / stored_name
        if not self._ensure_rerenderable_page_cache(source_path, cache_page_dir):
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
            fg_color=tuple(getattr(sample_region, "fg_colors", getattr(sample_region, "fg_color", (0, 0, 0))) or (0, 0, 0)),
            bg_color=tuple(getattr(sample_region, "bg_colors", getattr(sample_region, "bg_color", (255, 255, 255))) or (255, 255, 255)),
        )
        payload["merged_from"] = [str(getattr(region, "translation_region_key", "") or "") for region in ordered_regions]
        manual_regions = self._ensure_manual_regions_store(session)
        manual_regions.setdefault(stored_name, []).append(payload)
        return payload

    def delete_manual_region(self, session: dict[str, Any], region_id: str) -> bool:
        manual_regions = self._ensure_manual_regions_store(session)
        removed = False
        for stored_name, page_regions in list(manual_regions.items()):
            if not isinstance(page_regions, list):
                continue
            next_page_regions = [
                payload for payload in page_regions
                if str(payload.get("id") or "") != region_id
            ]
            if len(next_page_regions) != len(page_regions):
                removed = True
                if next_page_regions:
                    manual_regions[stored_name] = next_page_regions
                else:
                    manual_regions.pop(stored_name, None)
        return removed

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

        visible_regions: list[Any] = []
        for region in regions:
            region_key = str(getattr(region, "translation_region_key", "") or "")
            region.disabled_region = bool(disabled_overrides.get(region_key))
            if region.disabled_region:
                continue

            layout_override = layout_overrides.get(region_key) or {}
            bbox = layout_override.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                self._set_region_bbox(region, bbox)

            font_size = layout_override.get("font_size")
            if font_size is not None:
                try:
                    region.font_size = max(8, int(round(float(font_size))))
                except (TypeError, ValueError):
                    pass

            visible_regions.append(region)

        return visible_regions

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
        if config.get("font_style_mode") != "auto-map":
            for index, region in enumerate(regions):
                region.font_family = default_font_path
                region.font_style = "single"
                region.auto_font_style = "gothic"
                region.override_font_style = ""
                region.style_region_key = str(
                    getattr(region, "style_region_key", "")
                    or (self._make_style_region_key(stored_name, index) if stored_name else str(index))
                )
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
        from PIL import Image
        from manga_translator.rendering import (
            dispatch as dispatch_rendering,
            render as render_region,
            resize_regions_to_font_size,
        )
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
        manual_render_regions = [
            region for region in regions
            if bool(getattr(region, "manual_region", False)) and not bool(getattr(region, "skip_translation", False))
        ]
        render_regions = [region for region in regions if not bool(getattr(region, "skip_translation", False))]
        for region in skipped_regions:
            self._restore_original_region_pixels(source_rgb, inpainted_rgb, region)
        for region in manual_render_regions:
            self._erase_manual_region_pixels(inpainted_rgb, region)

        for region in render_regions:
            region._alignment = config["render_alignment"]
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
        result_image = dump_image(source_image, rendered_rgb, alpha_ch)

        save_ctx = Context(save_quality=100, text_regions=render_regions, result=result_image)
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
