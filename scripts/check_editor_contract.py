"""Exercise the actual Vue editor module against the actual FastAPI HTTP handlers.

Uses generated geometric media and temporary storage. Recognition state is seeded;
the upstream TextBlock container, OCR, provider validation, text-mask detection and
inpainting are substituted. Runtime patch installation is disabled. No browser,
real model or provider request is used. JSON lines transport requests between
Node and FastAPI TestClient; decoded pixels verify local brush/erase effects.
"""
from __future__ import annotations

import io
import base64
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
import numpy as np
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
from backend.tests._textblock_stub import textblock_module_patch
from domain.project_artifacts import PageArtifactEvent, ProjectArtifactState
from translation_provider import ProviderValidationResult


def main() -> None:
    executable = shutil.which("node")
    if not executable:
        raise RuntimeError("Node.js is required for the editor contract check.")
    with tempfile.TemporaryDirectory(prefix="inkstage-editor-api-") as temporary:
        os.environ["APP_DATA_DIR"] = temporary
        import main as backend

        def erase_selection(base_rgb, mask, **_kwargs):
            result = base_rgb.copy()
            result[mask > 0] = 255
            return result

        def detect_text_mask(source_rgb, **_kwargs):
            mask = np.zeros(source_rgb.shape[:2], dtype=np.uint8)
            mask[290:310, 190:210] = 255
            return {"mask": mask, "textlines": [{"points": [[190, 290], [210, 290], [210, 310], [190, 310]], "probability": 0.99}]}

        ocr = AsyncMock(side_effect=[{"source_text": "合成 OCR 原文", "font_size": 24}, RuntimeError("synthetic OCR failure")])
        with (textblock_module_patch(), patch.object(backend, "API_TOKEN", ""),
              patch.object(backend.translator_engine, "_ensure_runtime_patches"),
              patch.object(backend.translator_engine, "_select_inference_device", return_value="cpu"),
              patch.object(backend.inference_backend, "recognize_region", new=ocr),
              patch.object(backend.translation_provider, "validate", new=AsyncMock(return_value=ProviderValidationResult(preview="synthetic"))),
              patch.object(backend.inference_backend, "erase_selection", new=AsyncMock(side_effect=erase_selection)),
              patch.object(backend.inference_backend, "detect_text_mask", new=AsyncMock(side_effect=detect_text_mask)),
              TestClient(backend.app) as client):
            picture = io.BytesIO()
            source = Image.new("RGB", (600, 900), "white")
            ImageDraw.Draw(source).rectangle((190, 290, 210, 310), fill="black")
            source.save(picture, format="PNG")
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as book:
                book.writestr("chapter/001.png", picture.getvalue())
                book.writestr("chapter/002.png", picture.getvalue())
            upload = client.post("/api/upload", files={"file": ("synthetic.png", picture.getvalue(), "image/png")})
            upload.raise_for_status()
            view = upload.json()
            project_id = view["session_id"]
            page_id = view["images"][0]["stored_name"]
            session = backend.SESSIONS[project_id]
            session["artifact_state"] = ProjectArtifactState.create([page_id]).apply(
                page_id, PageArtifactEvent.RECOGNIZED,
            ).model_dump(mode="json")
            session["workflow_stage"] = "detected"
            backend.translator_engine.persist_project_state(project_id, session)
            created = client.post(f"/api/pages/{project_id}/{page_id}/commands", json={
                "commands": [{"type": "create_region", "bbox": [50, 60, 240, 160]}],
            })
            created.raise_for_status()
            with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as errors:
                process = subprocess.Popen(
                    [executable, str(ROOT / "frontend-v3/scripts/check-editor-backend.mjs")],
                    cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=errors, text=True, encoding="utf-8", bufsize=1,
                )
                messages: queue.Queue[str | None] = queue.Queue()

                def read_output():
                    for line in process.stdout:
                        messages.put(line)
                    messages.put(None)

                threading.Thread(target=read_output, daemon=True).start()
                complete = False
                try:
                    process.stdin.write(json.dumps({"seed": {
                        "projectId": project_id, "pageId": page_id,
                        "regionId": created.json()["created_region_id"],
                        "imageBase64": base64.b64encode(picture.getvalue()).decode("ascii"),
                        "archiveBase64": base64.b64encode(archive.getvalue()).decode("ascii"),
                    }}) + "\n")
                    process.stdin.flush()
                    while True:
                        line = messages.get(timeout=30)
                        if line is None:
                            break
                        message = json.loads(line)
                        if message.get("complete"):
                            complete = True
                            break
                        # Drop the in-memory session on restore to check persisted data.
                        if message["url"] == f"/api/projects/{project_id}/restore":
                            backend.SESSIONS.pop(project_id, None)
                        if "form" in message:
                            # Preserve repeated multipart fields produced by the actual frontend helper.
                            fields = [(item["name"], (item["filename"], base64.b64decode(item["base64"]), item["contentType"]))
                                      if "filename" in item else (item["name"], (None, item["value"]))
                                      for item in message["form"]]
                            response = client.request(message["method"], message["url"], files=fields)
                        else:
                            response = client.request(message["method"], message["url"], json=message.get("body"))
                        if response.headers.get("content-type", "").startswith("image/"):
                            picture = Image.open(io.BytesIO(response.content)).convert("RGB")
                            body = {"width": picture.width, "height": picture.height,
                                    "center": list(picture.getpixel((200, 300))), "outside": list(picture.getpixel((500, 700)))}
                        else:
                            body = response.json()
                        process.stdin.write(json.dumps({"id": message["id"], "status": response.status_code,
                                                        "body": body}) + "\n")
                        process.stdin.flush()
                    process.stdin.close()
                    returncode = process.wait(timeout=10)
                    if returncode or not complete:
                        errors.seek(0)
                        raise AssertionError(errors.read() or "Editor check exited without completion.")
                    if ocr.await_count != 2:
                        raise AssertionError("OCR undo/redo must restore snapshots without invoking inference again.")
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=10)
                    process.stdout.close()
            print("PASS: frontend multipart import, editor/API history, OCR snapshots, review state, erase and settings persistence")


if __name__ == "__main__":
    main()
