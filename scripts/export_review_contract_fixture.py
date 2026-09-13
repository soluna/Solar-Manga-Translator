"""Export a synthetic Page Document through the real HTTP/command boundary.

Run with backend/.venv-mac/bin/python scripts/export_review_contract_fixture.py.
The recognition artifact is seeded and the upstream TextBlock runtime is
substituted; no OCR, model or provider is called. Temporary project data is removed
and no local paths/secrets are exported.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
from backend.tests._textblock_stub import textblock_module_patch
from domain.project_artifacts import PageArtifactEvent, ProjectArtifactState


def export_fixture() -> dict:
    with tempfile.TemporaryDirectory(prefix="inkstage-contract-") as temporary:
        os.environ["APP_DATA_DIR"] = temporary
        import main

        with (textblock_module_patch(), patch.object(main, "API_TOKEN", ""),
              patch.object(main.translator_engine, "_ensure_runtime_patches"), TestClient(main.app) as client):
            picture = io.BytesIO()
            Image.new("RGB", (600, 900), "white").save(picture, format="PNG")
            uploaded = client.post("/api/upload", files={"file": ("synthetic.png", picture.getvalue(), "image/png")})
            uploaded.raise_for_status()
            project = uploaded.json()
            project_id = project["session_id"]
            page_id = project["images"][0]["stored_name"]
            session = main.SESSIONS[project_id]
            session["artifact_state"] = ProjectArtifactState.create([page_id]).apply(
                page_id, PageArtifactEvent.RECOGNIZED,
            ).model_dump(mode="json")
            session["workflow_stage"] = "detected"
            main.translator_engine.persist_project_state(project_id, session)
            endpoint = f"/api/pages/{project_id}/{page_id}"
            document_response = client.get(f"{endpoint}/document")
            document_response.raise_for_status()
            document = document_response.json()["document"]

            def edit(commands):
                nonlocal document
                response = client.post(f"{endpoint}/commands", json={
                    "commands": commands,
                    "expected_page_revision": document["metadata"]["revision"],
                })
                if response.status_code != 200:
                    raise RuntimeError(response.text)
                response.raise_for_status()
                result = response.json()
                document = result["document"]
                return result

            first = edit([{"type": "create_region", "bbox": [50, 60, 240, 160]}])["created_region_id"]
            second = edit([{"type": "create_region", "bbox": [300, 200, 500, 400]}])["created_region_id"]
            result = edit([
                {"type": "update_translation", "region_id": first, "text": "合成测试译文"},
                {"type": "update_font_size", "region_id": first, "font_size": 28},
                {"type": "update_text_direction", "region_id": first, "direction": "horizontal"},
                {"type": "update_region_style", "region_id": first, "rotation": 12,
                 "stroke_width": 0.3, "letter_spacing": 1.1, "line_spacing": 1.2,
                 "fg_color": [25, 35, 45], "bg_color": [240, 245, 250], "preserve_background": True},
                {"type": "disable_region", "region_id": second},
            ])
            fixture = {"document": document, "page_artifact": result["page_artifact"]}
            settings_response = client.get("/api/app/settings")
            settings_response.raise_for_status()
            # Export only editable fields, never runtime paths or configuration maps.
            settings_keys = """translator selected_translator target_lang api_key translator_model
                openai_base_url openai_model use_gpu pause_after_detection mask_cleanup_strength
                advanced_text_repair font_key style_font_gothic_key style_font_mincho_key
                style_font_rounded_key style_font_cartoon_key style_font_handwritten_key
                style_font_sfx_key render_alignment render_letter_spacing rerender_output_format
                image_cleanup_mode image_cleanup_model image_cleanup_api_key advanced_erase_base_url
                advanced_erase_model advanced_erase_api_key advanced_erase_timeout_seconds
                advanced_erase_selection_prompt export_mask_debug configured_secrets""".split()
            fixture["settings"] = {key: value for key, value in settings_response.json()["settings"].items()
                                   if key in settings_keys}

            def scrub(value):
                if isinstance(value, dict):
                    return {key: "" if key == "font_path" else
                            "2026-09-11T00:00:00+00:00" if key.endswith("_at") else scrub(item)
                            for key, item in value.items()}
                if isinstance(value, list):
                    return [scrub(item) for item in value]
                if isinstance(value, str):
                    return value.replace(project_id, "synthetic-project").replace(first, "region-a").replace(second, "region-b")
                return value
            return scrub(fixture)


if __name__ == "__main__":
    target = ROOT / "frontend-v3/test-fixtures/page-document.json"
    fixture = export_fixture()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {target.relative_to(ROOT)}")
