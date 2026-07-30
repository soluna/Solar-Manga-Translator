from __future__ import annotations

import io
import json
import socket
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from remote_execution import (
    RemoteExecutionAccess,
    RemoteExecutionManager,
    RemoteExecutionService,
    create_remote_execution_app,
)
from runtime_paths import AppPaths


class RemoteExecutionTests(unittest.TestCase):
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
        paths.code_dir.mkdir(parents=True, exist_ok=True)
        return paths

    def make_client(self, root: Path):
        paths = self.make_paths(root)
        access = RemoteExecutionAccess("worker-secret")
        service = RemoteExecutionService(
            paths=paths,
            runtime_provider=lambda: {
                "status": "ready",
                "api_key": "private-api-key",
                "data_dir": str(paths.app_data_dir),
            },
        )
        app = create_remote_execution_app(
            service=service,
            access=access,
            max_upload_bytes=4 * 1024 * 1024,
        )
        return TestClient(app), service

    def wait_for_job(
        self,
        client: TestClient,
        job_id: str,
        *,
        expected: set[str] | None = None,
        timeout: float = 8.0,
    ) -> dict:
        deadline = time.monotonic() + timeout
        headers = {"Authorization": "Bearer worker-secret"}
        terminal = expected or {"completed", "failed", "cancelled"}
        payload: dict = {}
        while time.monotonic() < deadline:
            response = client.get(f"/v1/jobs/{job_id}", headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()["job"]
            if payload["status"] in terminal:
                return payload
            time.sleep(0.05)
        self.fail(f"remote job did not finish: {payload}")

    def test_remote_routes_require_bearer_token_and_runtime_diagnostics_is_downloadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client, service = self.make_client(Path(tmp))
            self.addCleanup(service.shutdown)
            headers = {"Authorization": "Bearer worker-secret"}

            self.assertEqual(client.get("/v1/status").status_code, 401)
            self.assertEqual(
                client.get(
                    "/v1/status",
                    headers={"Authorization": "Bearer wrong"},
                ).status_code,
                401,
            )
            status = client.get("/v1/status", headers=headers)
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json()["service"], "solar-manga-translator-remote-worker")
            self.assertIn("command", status.json()["capabilities"]["tasks"])

            created = client.post(
                "/v1/jobs",
                headers=headers,
                json={"task": "runtime-diagnostics", "parameters": {}},
            )
            self.assertEqual(created.status_code, 202, created.text)
            job = self.wait_for_job(client, created.json()["job"]["id"])
            self.assertEqual(job["status"], "completed")
            diagnostics = next(
                artifact for artifact in job["artifacts"] if artifact["name"] == "runtime-diagnostics.json"
            )
            downloaded = client.get(
                f"/v1/jobs/{job['id']}/artifacts/{diagnostics['id']}",
                headers=headers,
            )
            self.assertEqual(downloaded.status_code, 200)
            self.assertNotIn("private-api-key", downloaded.text)
            self.assertEqual(downloaded.json()["api_key"], "[REDACTED]")

    def test_uploaded_bundle_can_run_without_shell_and_publish_results_and_live_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client, service = self.make_client(Path(tmp))
            self.addCleanup(service.shutdown)
            headers = {"Authorization": "Bearer worker-secret"}
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as bundle_zip:
                bundle_zip.writestr(
                    "run.py",
                    "from pathlib import Path\n"
                    "print('cuda benchmark worker ready', flush=True)\n"
                    "Path('result.json').write_text('{\"score\": 0.98}', encoding='utf-8')\n",
                )

            uploaded = client.post(
                "/v1/bundles",
                headers=headers,
                files={"file": ("benchmark.zip", archive.getvalue(), "application/zip")},
            )
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            bundle = uploaded.json()["bundle"]
            self.assertIn("run.py", {entry["relative_path"] for entry in bundle["files"]})

            created = client.post(
                "/v1/jobs",
                headers=headers,
                json={
                    "task": "command",
                    "parameters": {
                        "argv": [sys.executable, "run.py"],
                        "cwd": f"bundle:{bundle['id']}",
                        "timeout_seconds": 10,
                        "artifacts": ["result.json"],
                    },
                },
            )
            self.assertEqual(created.status_code, 202, created.text)
            job = self.wait_for_job(client, created.json()["job"]["id"])
            self.assertEqual(job["status"], "completed", job)
            self.assertEqual(job["exit_code"], 0)
            self.assertIn("cuda benchmark worker ready", job["log_tail"])
            result = next(artifact for artifact in job["artifacts"] if artifact["name"] == "result.json")
            response = client.get(
                f"/v1/jobs/{job['id']}/artifacts/{result['id']}",
                headers=headers,
            )
            self.assertEqual(response.json(), {"score": 0.98})

    def test_running_command_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client, service = self.make_client(Path(tmp))
            self.addCleanup(service.shutdown)
            headers = {"Authorization": "Bearer worker-secret"}
            created = client.post(
                "/v1/jobs",
                headers=headers,
                json={
                    "task": "command",
                    "parameters": {
                        "argv": [sys.executable, "-c", "import time; time.sleep(30)"],
                        "cwd": "job",
                        "timeout_seconds": 60,
                    },
                },
            )
            self.assertEqual(created.status_code, 202, created.text)
            job_id = created.json()["job"]["id"]
            cancelled = client.post(f"/v1/jobs/{job_id}/cancel", headers=headers)
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            job = self.wait_for_job(client, job_id, expected={"cancelled"})
            self.assertEqual(job["status"], "cancelled")

    def test_bundle_rejects_archive_path_traversal_and_unknown_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client, service = self.make_client(root)
            self.addCleanup(service.shutdown)
            headers = {"Authorization": "Bearer worker-secret"}
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as bundle_zip:
                bundle_zip.writestr("../outside.txt", "must not escape")

            uploaded = client.post(
                "/v1/bundles",
                headers=headers,
                files={"file": ("unsafe.zip", archive.getvalue(), "application/zip")},
            )
            self.assertEqual(uploaded.status_code, 422, uploaded.text)
            self.assertFalse((root / "outside.txt").exists())

            created = client.post(
                "/v1/jobs",
                headers=headers,
                json={"task": "arbitrary-unknown-task", "parameters": {}},
            )
            self.assertEqual(created.status_code, 422, created.text)
            self.assertIn("不支持的远程任务", created.json()["detail"])

    def test_enabled_manager_persists_token_and_auto_starts_after_backend_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_paths(Path(tmp))
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                preferred_port = int(probe.getsockname()[1])

            first = RemoteExecutionManager(
                paths=paths,
                runtime_provider=lambda: {"status": "ready"},
                preferred_port=preferred_port,
            )
            enabled = first.enable()
            self.assertTrue(enabled["enabled"])
            self.assertTrue(enabled["active"])
            token = enabled["token"]
            request = urllib.request.Request(
                f"http://127.0.0.1:{enabled['port']}/v1/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertEqual(json.loads(response.read())["service"], "solar-manga-translator-remote-worker")
            first.shutdown()

            second = RemoteExecutionManager(
                paths=paths,
                runtime_provider=lambda: {"status": "ready"},
                preferred_port=preferred_port,
            )
            try:
                restored = second.start_if_enabled()
                self.assertTrue(restored["active"])
                self.assertEqual(restored["token"], token)
            finally:
                second.disable()


if __name__ == "__main__":
    unittest.main()
