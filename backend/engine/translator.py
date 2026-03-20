from __future__ import annotations

import asyncio
import json
import shutil
import sys
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


class TranslatorEngine:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.temp_dir = self.base_dir / "temp_uploads"
        self.model_dir = self.base_dir / "models"
        self.model_dir.mkdir(exist_ok=True)

    async def translate_session(
        self,
        session_id: str,
        session: dict[str, Any],
        raw_config: dict[str, Any] | None,
        progress_callback: ProgressCallback,
    ) -> dict[str, str]:
        config = self._normalize_config(raw_config)
        source_dir = Path(session["source_dir"])
        output_dir = Path(session["translated_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        self._clear_directory(output_dir)

        config_path = self._write_config(session_id, config)
        log_path = self.temp_dir / f"{session_id}_translation.log"

        expected_outputs = [
            output_dir / Path(image["stored_name"])
            for image in session["source_images"]
        ]

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
        ]
        if config["use_gpu"]:
            command.insert(3, "--use-gpu")

        reported: set[Path] = set()
        total = len(expected_outputs)
        await progress_callback({"event": "start", "total_pages": total})

        with log_path.open("wb") as log_file:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.base_dir / "manga-image-translator"),
                stdout=log_file,
                stderr=log_file,
            )
            wait_task = asyncio.create_task(process.wait())

            while not wait_task.done():
                await self._emit_completed_images(
                    session_id,
                    session,
                    expected_outputs,
                    reported,
                    progress_callback,
                )
                await asyncio.sleep(1)

            await wait_task

        await self._emit_completed_images(
            session_id,
            session,
            expected_outputs,
            reported,
            progress_callback,
        )

        if process.returncode != 0:
            raise RuntimeError(self._format_failure(log_path))

        archive_base = self.temp_dir / f"{session_id}_translated"
        archive_path = shutil.make_archive(
            str(archive_base),
            "zip",
            root_dir=str(output_dir),
        )
        session["download_path"] = archive_path

        return {
            "download_url": f"/api/download/{session_id}",
        }

    async def _emit_completed_images(
        self,
        session_id: str,
        session: dict[str, Any],
        expected_outputs: list[Path],
        reported: set[Path],
        progress_callback: ProgressCallback,
    ) -> None:
        total = len(expected_outputs)

        for index, output_path in enumerate(expected_outputs, start=1):
            if output_path in reported or not output_path.exists():
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
        translator = str(raw_config.get("translator") or "sugoi").strip() or "sugoi"
        target_lang = str(raw_config.get("target_lang") or "CHS").strip().upper() or "CHS"
        use_gpu = bool(raw_config.get("use_gpu", True))

        return {
            "translator": translator,
            "target_lang": target_lang,
            "use_gpu": use_gpu,
        }

    def _write_config(self, session_id: str, config: dict[str, Any]) -> Path:
        config_path = self.temp_dir / f"{session_id}_config.json"
        payload = {
            "translator": {
                "translator": config["translator"],
                "target_lang": config["target_lang"],
            }
        }
        config_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return config_path

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
