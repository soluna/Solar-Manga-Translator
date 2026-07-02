from __future__ import annotations

import atexit
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TEST_APP_DATA_DIR = Path(tempfile.mkdtemp(prefix="manga-translator-api-test-"))
atexit.register(shutil.rmtree, TEST_APP_DATA_DIR, True)
os.environ["APP_DATA_DIR"] = str(TEST_APP_DATA_DIR)

import main


class DummyUpload:
    def __init__(self, data: bytes) -> None:
        self.file = io.BytesIO(data)


class ApiSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)

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


if __name__ == "__main__":
    unittest.main()
