from __future__ import annotations

import atexit
import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TEST_APP_DATA_DIR = Path(tempfile.mkdtemp(prefix="manga-translator-api-test-"))
atexit.register(shutil.rmtree, TEST_APP_DATA_DIR, True)
os.environ["APP_DATA_DIR"] = str(TEST_APP_DATA_DIR)

import main
from domain.project_artifacts import PageArtifactEvent, ProjectArtifactState


class DummyUpload:
    def __init__(self, data: bytes) -> None:
        self.file = io.BytesIO(data)


class ApiSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)

    def receive_task_events(self, websocket) -> list[dict]:
        events = []
        while not events or events[-1].get("event") not in {"completed", "error", "cancelled"}:
            events.append(websocket.receive_json())
        return events

    def test_status_is_minimal_and_public(self) -> None:
        with mock.patch.object(main, "API_TOKEN", "local-secret"):
            response = self.client.get("/api/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "running", "auth_required": True})
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertNotIn("runtime", response.json())

    def test_protected_api_requires_bearer_token(self) -> None:
        with mock.patch.object(main, "API_TOKEN", "local-secret"):
            denied = self.client.get("/api/projects")
            allowed = self.client.get(
                "/api/projects",
                headers={"Authorization": "Bearer local-secret"},
            )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)

    def test_cors_preflight_is_not_blocked_by_bearer_token(self) -> None:
        with mock.patch.object(main, "API_TOKEN", "local-secret"):
            response = self.client.options(
                "/api/projects",
                headers={
                    "Origin": "http://127.0.0.1:5173",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Authorization",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://127.0.0.1:5173")

    def test_opaque_media_paths_remain_readable_without_url_tokens(self) -> None:
        with mock.patch.object(main, "API_TOKEN", "local-secret"):
            response = self.client.get("/output/nonexistent/image.png")

        self.assertEqual(response.status_code, 404)

    def test_websocket_requires_token_subprotocol(self) -> None:
        with mock.patch.object(main, "API_TOKEN", "local-secret"):
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect("/ws/translate/missing"):
                    pass

            with self.client.websocket_connect(
                "/ws/translate/missing",
                subprotocols=["manga-translator", "auth.local-secret"],
            ) as websocket:
                payload = websocket.receive_json()

        self.assertEqual(payload["event"], "error")

    def test_copy_upload_enforces_size_limit_and_removes_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "upload.bin"
            with mock.patch.object(main, "MAX_UPLOAD_BYTES", 4):
                with self.assertRaises(main.UploadTooLargeError):
                    main.copy_upload_to_path(DummyUpload(b"12345"), destination)

            self.assertFalse(destination.exists())

    def test_delete_project_rejects_encoded_parent_directory(self) -> None:
        sentinel = TEST_APP_DATA_DIR / "path-traversal-sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")

        with mock.patch.object(main, "API_TOKEN", ""):
            response = self.client.delete("/api/projects/%2E%2E")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(sentinel.exists())

    def test_new_user_upload_detect_translate_and_export_contract(self) -> None:
        image_bytes = io.BytesIO()
        Image.new("RGB", (32, 32), (255, 255, 255)).save(image_bytes, format="PNG")

        with mock.patch.object(main, "API_TOKEN", ""):
            upload = self.client.post(
                "/api/upload",
                files={"file": ("page-1.png", image_bytes.getvalue(), "image/png")},
            )
        self.assertEqual(upload.status_code, 200)
        project_id = upload.json()["session_id"]

        async def fake_detect_session(*, session_id, session, progress_callback, **_kwargs):
            await progress_callback({"event": "status", "message": "mock detect"})
            cache_dir = main.translator_engine._prepare_rerender_cache_dir(session_id, reset=True)
            for source_image in session["source_images"]:
                page_cache = cache_dir / source_image["stored_name"]
                page_cache.mkdir(parents=True)
                (page_cache / "regions.json").write_text("[]", encoding="utf-8")
                Image.new("RGB", (32, 32), (255, 255, 255)).save(page_cache / "inpainted.png")
            session["rerender_cache_dir"] = str(cache_dir)
            session["workflow_stage"] = "detected"
            artifact_state = ProjectArtifactState.model_validate(
                session["artifact_state"]
            )
            for source_image in session["source_images"]:
                artifact_state = artifact_state.apply(
                    source_image["stored_name"],
                    PageArtifactEvent.RECOGNIZED,
                )
            session["artifact_state"] = artifact_state.model_dump(mode="json")
            return {"workflow_stage": "detected"}

        async def fake_resume_session(*, session_id, session, progress_callback, **_kwargs):
            await progress_callback({"event": "status", "message": "mock translate"})
            output_dir = Path(session["translated_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            translated_output_map = {}
            for source_image in session["source_images"]:
                stored_name = source_image["stored_name"]
                Image.new("RGB", (32, 32), (240, 240, 240)).save(output_dir / stored_name)
                translated_output_map[stored_name] = stored_name
            session["translated_output_map"] = translated_output_map
            session["workflow_stage"] = "translated"
            artifact_state = ProjectArtifactState.model_validate(
                session["artifact_state"]
            )
            for source_image in session["source_images"]:
                artifact_state = artifact_state.apply(
                    source_image["stored_name"],
                    PageArtifactEvent.TRANSLATED,
                )
            session["artifact_state"] = artifact_state.model_dump(mode="json")
            archive_path = main.translator_engine.build_session_archive(session_id, session)
            return {
                "workflow_stage": "translated",
                "download_url": f"/api/download/{session_id}",
                "download_path": archive_path,
            }

        with (
            mock.patch.object(main, "API_TOKEN", ""),
            mock.patch.object(main.translator_engine, "detect_session", side_effect=fake_detect_session),
            mock.patch.object(main.translator_engine, "resume_translation_session", side_effect=fake_resume_session),
        ):
            with self.client.websocket_connect(f"/ws/translate/{project_id}") as websocket:
                websocket.send_json({"action": "detect", "config": {}})
                events = self.receive_task_events(websocket)
            self.assertEqual(events[0]["event"], "task")
            self.assertTrue(events[0]["task_id"])
            self.assertEqual(events[-1]["event"], "completed")
            self.assertEqual(events[-1]["workflow_stage"], "detected")

            with self.client.websocket_connect(f"/ws/translate/{project_id}") as websocket:
                websocket.send_json({"action": "resume-translate", "config": {}})
                events = self.receive_task_events(websocket)
            self.assertEqual(events[-1]["event"], "completed")
            self.assertEqual(events[-1]["workflow_stage"], "translated")

            download = self.client.get(f"/api/download/{project_id}")

        self.assertEqual(download.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            self.assertEqual(len(archive.namelist()), 1)

    def test_download_rejects_a_project_without_current_final_artifacts(self) -> None:
        image_bytes = io.BytesIO()
        Image.new("RGB", (16, 16), (255, 255, 255)).save(
            image_bytes,
            format="PNG",
        )
        with mock.patch.object(main, "API_TOKEN", ""):
            upload = self.client.post(
                "/api/upload",
                files={"file": ("page-1.png", image_bytes.getvalue(), "image/png")},
            )
            response = self.client.get(f"/api/download/{upload.json()['session_id']}")

        self.assertEqual(response.status_code, 409)
        self.assertIn("翻译结果", response.json()["detail"])

    def test_restore_reports_corrupt_project_state_instead_of_silently_recovering(self) -> None:
        image_bytes = io.BytesIO()
        Image.new("RGB", (16, 16), (255, 255, 255)).save(
            image_bytes,
            format="PNG",
        )
        with mock.patch.object(main, "API_TOKEN", ""):
            upload = self.client.post(
                "/api/upload",
                files={"file": ("page-1.png", image_bytes.getvalue(), "image/png")},
            )
            project_id = upload.json()["session_id"]
            main.SESSIONS.pop(project_id, None)
            workspace = main.translator_engine.project_workspace
            head = workspace.read_project_head(project_id)
            state_blob = head["files"]["state/session.json"]["blob"]
            state_blob_path = (
                workspace.project_artifact_store_dir(project_id)
                / state_blob[:2]
                / state_blob
            )
            state_blob_path.write_text("{not valid json", encoding="utf-8")

            response = self.client.post(f"/api/projects/{project_id}/restore")

        self.assertEqual(response.status_code, 409)
        self.assertIn("校验失败", response.json()["detail"])

    def test_restore_rejects_an_unknown_project_state_schema(self) -> None:
        image_bytes = io.BytesIO()
        Image.new("RGB", (16, 16), (255, 255, 255)).save(
            image_bytes,
            format="PNG",
        )
        with mock.patch.object(main, "API_TOKEN", ""):
            upload = self.client.post(
                "/api/upload",
                files={"file": ("page-1.png", image_bytes.getvalue(), "image/png")},
            )
            project_id = upload.json()["session_id"]
            main.SESSIONS.pop(project_id, None)
            workspace = main.translator_engine.project_workspace
            head = workspace.read_project_head(project_id)
            state = workspace.read_project_session_document(project_id)
            state["schema_version"] = 99
            workspace.commit_project_head(
                project_id,
                state_document=state,
                project_manifest=workspace.read_project_manifest(project_id),
                page_documents={},
                expected_generation=head["generation"],
            )

            response = self.client.post(f"/api/projects/{project_id}/restore")

        self.assertEqual(response.status_code, 409)
        self.assertIn("不支持的项目状态版本", response.json()["detail"])

    def test_page_command_revision_conflict_is_a_structured_http_409(self) -> None:
        project_id = "revision-conflict-api"
        page_id = "0001.png"
        main.SESSIONS[project_id] = {"source_images": [{"stored_name": page_id}]}
        conflict = main.PageDocumentRevisionConflict(
            expected_revision=3,
            actual_revision=4,
            document={"page_id": page_id, "metadata": {"revision": 4}},
        )
        try:
            with (
                mock.patch.object(main, "API_TOKEN", ""),
                mock.patch.object(
                    main.translator_engine,
                    "apply_page_commands",
                    new=mock.AsyncMock(side_effect=conflict),
                ),
            ):
                response = self.client.post(
                    f"/api/pages/{project_id}/{page_id}/commands",
                    json={
                        "expected_revision": 3,
                        "commands": [{"type": "update_translation", "region_id": "region-1", "text": "译文"}],
                    },
                )
        finally:
            main.SESSIONS.pop(project_id, None)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "page_revision_conflict")
        self.assertEqual(response.json()["detail"]["actual_revision"], 4)

    def test_legacy_manual_region_mutations_invalidate_the_current_final_artifact(self) -> None:
        image_bytes = io.BytesIO()
        Image.new("RGB", (32, 32), (255, 255, 255)).save(
            image_bytes,
            format="PNG",
        )
        with mock.patch.object(main, "API_TOKEN", ""):
            upload = self.client.post(
                "/api/upload",
                files={"file": ("page-1.png", image_bytes.getvalue(), "image/png")},
            )
            payload = upload.json()
            project_id = payload["session_id"]
            page_id = payload["images"][0]["stored_name"]
            session = main.SESSIONS[project_id]
            output_dir = Path(session["translated_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (32, 32), (240, 240, 240)).save(
                output_dir / page_id
            )
            session["translated_output_map"] = {page_id: page_id}
            session["workflow_stage"] = "translated"
            session["artifact_state"] = ProjectArtifactState.create(
                [page_id]
            ).apply(
                page_id,
                PageArtifactEvent.RECOGNIZED,
            ).apply(
                page_id,
                PageArtifactEvent.TRANSLATED,
            ).model_dump(mode="json")
            main.translator_engine.persist_project_state(project_id, session)

            created = self.client.post(
                f"/api/manual-regions/{project_id}",
                json={
                    "action": "create",
                    "stored_name": page_id,
                    "bbox": [4, 4, 20, 20],
                    "config": {},
                },
            )
            self.assertEqual(created.status_code, 200)
            self.assertTrue(
                created.json()["page_artifact"]["capabilities"]["final_stale"]
            )

            region_id = created.json()["region"]["id"]
            session["artifact_state"] = ProjectArtifactState.model_validate(
                session["artifact_state"]
            ).apply(
                page_id,
                PageArtifactEvent.RENDERED,
            ).model_dump(mode="json")
            main.translator_engine.persist_project_state(project_id, session)
            deleted = self.client.post(
                f"/api/manual-regions/{project_id}",
                json={
                    "action": "delete",
                    "region_id": region_id,
                },
            )
            restored = self.client.post(f"/api/projects/{project_id}/restore")

        self.assertEqual(deleted.status_code, 200)
        page_artifact = restored.json()["page_artifacts"][page_id]
        self.assertTrue(page_artifact["capabilities"]["final_stale"])
        self.assertFalse(page_artifact["capabilities"]["can_export"])

    def test_glossary_extract_button_supplements_a_name_after_an_earlier_empty_result(self) -> None:
        image_bytes = io.BytesIO()
        Image.new("RGB", (32, 32), (255, 255, 255)).save(image_bytes, format="PNG")

        with mock.patch.object(main, "API_TOKEN", ""):
            upload = self.client.post(
                "/api/upload",
                files={"file": ("page-1.png", image_bytes.getvalue(), "image/png")},
            )
        self.assertEqual(upload.status_code, 200)
        project_id = upload.json()["session_id"]
        page_id = upload.json()["images"][0]["stored_name"]
        session = main.SESSIONS[project_id]
        session["project_glossary"] = {
            "entries": [],
            "auto_extract_completed": True,
            "auto_extracted_at": "2026-07-07T12:56:55+00:00",
        }
        page_document = {
            "page_id": page_id,
            "regions": [
                {
                    "region_id": "r1",
                    "source_text": "私の名前は片桐 奈々美",
                    "translation": {},
                },
                {
                    "region_id": "r2",
                    "source_text": "ど…どうしたの？奈々美ちゃん",
                    "translation": {},
                },
            ],
        }
        workspace = main.translator_engine.project_workspace
        current_head = workspace.read_project_head(project_id)
        workspace.commit_project_head(
            project_id,
            state_document=main.translator_engine._serialize_session_state(
                project_id,
                session,
            ),
            project_manifest=workspace.read_project_manifest(project_id),
            page_documents={page_id: page_document},
            expected_generation=current_head["generation"],
        )

        async def fake_glossary_request(_config, prompt):
            if "可能遗漏的 OCR 证据" in prompt and "片桐 奈々美" in prompt:
                return '[{"source":"片桐 奈々美","translation":"片桐奈奈美","category":"人名"}]'
            return "[]"

        with (
            mock.patch.object(main.translator_engine, "_ensure_runtime_patches", return_value=None),
            mock.patch.object(
                main.translator_engine,
                "_request_project_glossary_extraction",
                side_effect=fake_glossary_request,
            ),
            mock.patch.object(main, "API_TOKEN", ""),
        ):
            response = self.client.post(
                f"/api/projects/{project_id}/glossary/extract",
                json={
                    "config": {
                        "translator": "custom_openai",
                        "selected_translator": "openai-compatible",
                        "target_lang": "CHS",
                        "openai_base_url": "https://api.example.com/v1",
                        "openai_model": "example-model",
                        "api_key": "secret",
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [entry["source"] for entry in response.json()["glossary"]["entries"]],
            ["片桐 奈々美"],
        )
        self.assertIn("已补充 1 个", response.json()["message"])

    def test_translation_task_survives_websocket_disconnect_and_resumes(self) -> None:
        image_bytes = io.BytesIO()
        Image.new("RGB", (32, 32), (255, 255, 255)).save(image_bytes, format="PNG")

        with tempfile.TemporaryDirectory() as tmp, TestClient(main.app) as client:
            release_path = Path(tmp) / "release"
            with mock.patch.object(main, "API_TOKEN", ""):
                upload = client.post(
                    "/api/upload",
                    files={"file": ("page-1.png", image_bytes.getvalue(), "image/png")},
                )
            project_id = upload.json()["session_id"]

            async def fake_detect_session(*, session, progress_callback, **_kwargs):
                await progress_callback({"event": "status", "message": "waiting"})
                while not release_path.exists():
                    await asyncio.sleep(0.01)
                session["workflow_stage"] = "detected"
                return {"workflow_stage": "detected"}

            with (
                mock.patch.object(main, "API_TOKEN", ""),
                mock.patch.object(main.translator_engine, "detect_session", side_effect=fake_detect_session),
            ):
                with client.websocket_connect(f"/ws/translate/{project_id}") as websocket:
                    websocket.send_json({"action": "detect", "config": {}})
                    first_event = websocket.receive_json()

                self.assertEqual(first_event["event"], "task")
                task_id = first_event["task_id"]
                running = client.get(f"/api/tasks/{task_id}")
                self.assertEqual(running.status_code, 200)
                self.assertIn(running.json()["status"], {"running", "completed"})
                live_session = main.SESSIONS[project_id]
                restored = client.post(f"/api/projects/{project_id}/restore")
                self.assertEqual(restored.status_code, 200)
                self.assertIs(main.SESSIONS[project_id], live_session)

                release_path.touch()
                for _ in range(100):
                    snapshot = client.get(f"/api/tasks/{task_id}").json()
                    if snapshot["status"] == "completed":
                        break
                    time.sleep(0.01)
                else:
                    self.fail("后台任务在 WebSocket 断开后没有完成")

                with client.websocket_connect(f"/ws/translate/{project_id}") as websocket:
                    websocket.send_json({
                        "task_id": task_id,
                        "after_sequence": first_event["sequence"],
                    })
                    resumed_events = self.receive_task_events(websocket)

            self.assertEqual(resumed_events[-1]["event"], "completed")
            self.assertEqual(resumed_events[-1]["workflow_stage"], "detected")

    def test_translation_task_can_be_cancelled_through_api(self) -> None:
        image_bytes = io.BytesIO()
        Image.new("RGB", (32, 32), (255, 255, 255)).save(image_bytes, format="PNG")

        with TestClient(main.app) as client:
            with mock.patch.object(main, "API_TOKEN", ""):
                upload = client.post(
                    "/api/upload",
                    files={"file": ("page-1.png", image_bytes.getvalue(), "image/png")},
                )
            project_id = upload.json()["session_id"]

            async def fake_detect_session(*, progress_callback, **_kwargs):
                await progress_callback({"event": "status", "message": "waiting"})
                await asyncio.Event().wait()

            with (
                mock.patch.object(main, "API_TOKEN", ""),
                mock.patch.object(main.translator_engine, "detect_session", side_effect=fake_detect_session),
            ):
                with client.websocket_connect(f"/ws/translate/{project_id}") as websocket:
                    websocket.send_json({"action": "detect", "config": {}})
                    task_event = websocket.receive_json()
                    cancelled = client.post(f"/api/tasks/{task_event['task_id']}/cancel")
                    terminal_events = self.receive_task_events(websocket)

            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(terminal_events[-1]["event"], "cancelled")
            self.assertFalse(main.translator_engine.is_session_busy(project_id))


if __name__ == "__main__":
    unittest.main()
