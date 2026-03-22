from __future__ import annotations

import asyncio
import json
import shutil
import sys
import os
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
        output_dir.mkdir(parents=True, exist_ok=True)
        self._clear_directory(output_dir)

        config_path = self._write_config(session_id, config)
        log_path = self.temp_dir / f"{session_id}_translation.log"

        expected_outputs = [
            output_dir / Path(image["stored_name"])
            for image in session["source_images"]
        ]
        complex_images = self._select_complex_repair_images(session, source_dir, config)
        session["deferred_output_names"] = {image["stored_name"] for image in complex_images}

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
        translator = str(raw_config.get("translator") or "gemini").strip() or "gemini"
        target_lang = str(raw_config.get("target_lang") or "CHS").strip().upper() or "CHS"

        # Bug fix: Some translators use different language codes for Chinese
        # For example, sugoi might not support CHS, but only JPN/ENG
        # If it's CHS/CHT, the backend needs to convert it appropriately based on the translator
        # But looking at the logs: Language unsupported exception for SugoiTranslator: "CHS"
        # Sugoi only supports Japanese to English translations!
        if translator == "sugoi" and target_lang in ["CHS", "CHT"]:
            # Fall back to gemini for Chinese if user selected Sugoi but wants Chinese
            print(f"[DEBUG] Sugoi translator does not support {target_lang}. Falling back to 'gemini'")
            translator = "gemini"

        use_gpu = bool(raw_config.get("use_gpu", True))
        api_key = str(raw_config.get("api_key", "")).strip()
        font_key = str(raw_config.get("font_key", "")).strip()
        font_path = self._resolve_font_path(font_key)
        image_cleanup_mode = self._normalize_image_cleanup_mode(raw_config.get("image_cleanup_mode"))
        image_cleanup_model = self._normalize_image_cleanup_model(
            image_cleanup_mode,
            raw_config.get("image_cleanup_model"),
        )
        image_cleanup_api_key = str(raw_config.get("image_cleanup_api_key", "")).strip()

        return {
            "translator": translator,
            "target_lang": target_lang,
            "use_gpu": use_gpu,
            "api_key": api_key,
            "font_key": font_key,
            "font_path": font_path,
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

    def _normalize_image_cleanup_mode(self, raw_value: Any) -> str:
        value = str(raw_value or "off").strip().lower()
        if value not in {"off", "gemini-image", "seedream-image"}:
            return "off"
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

    def _write_config(self, session_id: str, config: dict[str, Any], profile: str = "default") -> Path:
        config_path = self.temp_dir / f"{session_id}_{profile}_config.json"
        is_complex_profile = profile == "complex"
        payload = {
            "translator": {
                "translator": config["translator"],
                "target_lang": config["target_lang"],
            },
            # Fix text artifacts (not clean):
            # The mask offset needs to be large enough to catch loose pixels.
            "mask_dilation_offset": 28 if is_complex_profile else 20,
            # Use larger convolution kernel to erase the text completely.
            "kernel_size": 9 if is_complex_profile else 7,
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
                "alignment": "center",
                "direction": "auto"
            },
            "detector": {
                # Better bounding boxes logic:
                "unclip_ratio": 3.0 if is_complex_profile else 2.5  # Expand detected text bounding boxes
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

    def _prepare_rerender_cache_dir(self, session_id: str, reset: bool) -> Path:
        cache_dir = self._rerender_cache_dir(session_id)
        if reset:
            shutil.rmtree(cache_dir, ignore_errors=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

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
        return region

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
        save_result(result_image, str(output_path), save_ctx)

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
