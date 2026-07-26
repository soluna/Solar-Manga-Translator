from __future__ import annotations

import json
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from remote_diagnostics import (
    RemoteDiagnosticsAccess,
    RemoteDiagnosticsCatalog,
    RemoteDiagnosticsManager,
    create_remote_diagnostics_app,
)
from runtime_paths import AppPaths


class RemoteDiagnosticsTests(unittest.TestCase):
    def make_paths(self, root: Path) -> AppPaths:
        app_data_dir = root / "app-data"
        paths = AppPaths(
            code_dir=root / "code",
            app_data_dir=app_data_dir,
            models_dir=app_data_dir / "models",
            output_dir=app_data_dir / "output",
            logs_dir=app_data_dir / "logs",
            cache_dir=app_data_dir / "cache",
            config_dir=app_data_dir / "config",
        )
        paths.ensure_directories()
        return paths

    def make_client(self, root: Path, clock=None):
        paths = self.make_paths(root)
        project_dir = paths.projects_dir / "project-a"
        source_dir = paths.output_dir / "project-a" / "source"
        attempt_dir = paths.cache_dir / "rerender_cache" / "project-a" / "page-1.png" / "advanced_erase"
        project_dir.mkdir(parents=True)
        source_dir.mkdir(parents=True)
        attempt_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            json.dumps({
                "project_id": "project-a",
                "title": "Private manga",
                "api_key": "private-api-key",
                "source_path": r"C:\\Users\\SOLUNA\\private-manga\\page-1.png",
            }),
            encoding="utf-8",
        )
        (source_dir / "page-1.png").write_bytes(b"source-image")
        (attempt_dir / "attempt.seedream.png").write_bytes(b"model-output")
        (attempt_dir / "attempt.mask.png").write_bytes(b"final-mask")
        (project_dir / ".env").write_text("API_KEY=private-api-key", encoding="utf-8")
        outside = root / "outside.png"
        outside.write_bytes(b"outside-secret")
        (project_dir / "outside-link.png").symlink_to(outside)
        (paths.logs_dir / "backend.log").write_text(
            "\n".join([
                "Authorization: Bearer private-api-key",
                "[Model48pxOCR] private dialogue",
                "advanced erase finished",
            ]),
            encoding="utf-8",
        )

        access = RemoteDiagnosticsAccess(clock=clock) if clock else RemoteDiagnosticsAccess()
        token = access.issue_token(ttl_seconds=60)
        catalog = RemoteDiagnosticsCatalog(paths)
        app = create_remote_diagnostics_app(
            catalog=catalog,
            access=access,
            runtime_provider=lambda: {
                "status": "ready",
                "data_dir": str(paths.app_data_dir),
                "api_key": "private-api-key",
            },
        )
        return TestClient(app), token

    def test_every_remote_route_requires_a_short_lived_bearer_token(self) -> None:
        now = [100.0]
        with tempfile.TemporaryDirectory() as tmp:
            client, token = self.make_client(Path(tmp), clock=lambda: now[0])

            self.assertEqual(client.get("/v1/status").status_code, 401)
            self.assertEqual(
                client.get("/v1/status", headers={"Authorization": "Bearer wrong"}).status_code,
                401,
            )
            response = client.get(
                "/v1/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("private-api-key", response.text)
            self.assertNotIn("SOLUNA", response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")

            now[0] = 161.0
            self.assertEqual(
                client.get(
                    "/v1/status",
                    headers={"Authorization": f"Bearer {token}"},
                ).status_code,
                401,
            )

    def test_catalog_exposes_only_allowlisted_project_files_and_sanitized_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client, token = self.make_client(Path(tmp))
            headers = {"Authorization": f"Bearer {token}"}

            projects = client.get("/v1/projects", headers=headers)
            self.assertEqual(projects.status_code, 200)
            self.assertEqual(projects.json()["projects"][0]["project_id"], "project-a")

            response = client.get("/v1/projects/project-a/files", headers=headers)
            self.assertEqual(response.status_code, 200)
            files = response.json()["files"]
            relative_paths = {entry["relative_path"] for entry in files}
            self.assertIn("cache/page-1.png/advanced_erase/attempt.seedream.png", relative_paths)
            self.assertIn("output/source/page-1.png", relative_paths)
            self.assertIn("project/project.json", relative_paths)
            self.assertNotIn("project/.env", relative_paths)
            self.assertNotIn("project/outside-link.png", relative_paths)

            model_entry = next(entry for entry in files if entry["relative_path"].endswith("attempt.seedream.png"))
            model_output = client.get(
                f"/v1/projects/project-a/files/{model_entry['id']}",
                headers=headers,
            )
            self.assertEqual(model_output.status_code, 200)
            self.assertEqual(model_output.content, b"model-output")

            project_entry = next(entry for entry in files if entry["relative_path"] == "project/project.json")
            project_state = client.get(
                f"/v1/projects/project-a/files/{project_entry['id']}",
                headers=headers,
            )
            self.assertEqual(project_state.status_code, 200)
            self.assertEqual(project_state.json()["api_key"], "[REDACTED]")
            self.assertEqual(project_state.json()["source_path"], "[LOCAL_PATH]")

            logs = client.get("/v1/logs?lines=200", headers=headers)
            self.assertEqual(logs.status_code, 200)
            self.assertNotIn("private-api-key", logs.text)
            self.assertNotIn("private dialogue", logs.text)
            self.assertIn("advanced erase finished", logs.text)

            self.assertEqual(
                client.get("/v1/projects/project-a/files/not-a-real-id", headers=headers).status_code,
                404,
            )
            self.assertEqual(
                client.get("/v1/projects/../files", headers=headers).status_code,
                404,
            )

    def test_remote_service_rejects_all_state_changing_methods(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client, token = self.make_client(Path(tmp))
            response = client.post(
                "/v1/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(response.status_code, 405)

    def test_manager_starts_a_separate_loopback_reachable_server_and_stops_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self.make_paths(root)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                preferred_port = int(probe.getsockname()[1])
            manager = RemoteDiagnosticsManager(
                paths=paths,
                runtime_provider=lambda: {"status": "ready"},
                preferred_port=preferred_port,
            )
            try:
                started = manager.start(ttl_seconds=300)
                self.assertTrue(started["active"])
                self.assertTrue(started["read_only"])
                self.assertEqual(started["port"], preferred_port)
                self.assertTrue(started["token"])
                request = urllib.request.Request(
                    f"http://127.0.0.1:{preferred_port}/v1/status",
                    headers={"Authorization": f"Bearer {started['token']}"},
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read())
                self.assertEqual(
                    payload["service"],
                    "solar-manga-translator-read-only-diagnostics",
                )
            finally:
                stopped = manager.stop()
            self.assertFalse(stopped["active"])


if __name__ == "__main__":
    unittest.main()
