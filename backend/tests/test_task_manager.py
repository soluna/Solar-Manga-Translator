from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from task_manager import TaskManager, build_public_task_error


class TaskManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_survives_subscriber_disconnect_and_can_resume(self) -> None:
        manager = TaskManager()
        release_runner = asyncio.Event()

        async def runner(publish):
            await publish({"event": "status", "message": "started"})
            await release_runner.wait()
            await publish({"event": "progress", "current": 1, "total": 1})
            return {"workflow_stage": "translated"}

        task_id = manager.start("project-a", "translate", runner)
        first_subscription = manager.subscribe(task_id)
        first_event = await anext(first_subscription)
        await first_subscription.aclose()

        self.assertEqual(first_event["event"], "task")
        release_runner.set()
        await manager.wait(task_id)

        resumed_events = [
            event
            async for event in manager.subscribe(
                task_id,
                after_sequence=first_event["sequence"],
            )
        ]

        self.assertEqual(manager.snapshot(task_id)["status"], "completed")
        self.assertEqual(
            [event["event"] for event in resumed_events],
            ["status", "progress", "completed"],
        )
        self.assertEqual(resumed_events[-1]["workflow_stage"], "translated")

    async def test_cancel_stops_worker_and_publishes_terminal_event(self) -> None:
        manager = TaskManager()
        runner_started = asyncio.Event()
        runner_cleaned_up = asyncio.Event()

        async def runner(publish):
            try:
                runner_started.set()
                await asyncio.Event().wait()
            finally:
                runner_cleaned_up.set()

        task_id = manager.start("project-a", "translate", runner)
        await runner_started.wait()
        await manager.cancel(task_id)
        await manager.wait(task_id)

        snapshot = manager.snapshot(task_id)
        self.assertTrue(runner_cleaned_up.is_set())
        self.assertEqual(snapshot["status"], "cancelled")
        self.assertEqual(snapshot["events"][-1]["event"], "cancelled")

    def test_public_error_maps_provider_auth_failure_and_hides_local_path(self) -> None:
        local_path = "/".join(("", "Users", "private-user", "project", "provider.py"))
        fake_secret = "sk-" + "example-value-for-redaction"
        payload = build_public_task_error(
            RuntimeError(
                "OpenAI Compatible request failed: HTTP 403 at "
                f"{local_path} "
                f"Authorization: Bearer {fake_secret}"
            )
        )

        self.assertEqual(payload["code"], "TRANSLATION_AUTH_FAILED")
        self.assertFalse(payload["retryable"])
        self.assertIn("API", payload["message"])
        self.assertIn("翻译服务", payload["action"])
        self.assertNotIn(local_path, payload["technical_message"])
        self.assertNotIn(fake_secret, payload["technical_message"])
        self.assertLessEqual(len(payload["technical_message"]), 280)


if __name__ == "__main__":
    unittest.main()
