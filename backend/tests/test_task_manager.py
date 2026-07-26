from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from task_manager import (
    ProjectTaskConflictError,
    TaskManager,
    build_public_task_error,
)


class TaskManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_lease_blocks_a_same_project_task_until_released(self) -> None:
        manager = TaskManager()

        async def runner(_publish):
            return {"ok": True}

        with manager.lease("project-a", "page-edit"):
            with self.assertRaises(ProjectTaskConflictError):
                manager.start("project-a", "translate", runner)

        task_id = manager.start("project-a", "translate", runner)
        await manager.wait(task_id)
        self.assertEqual(manager.snapshot(task_id)["status"], "completed")

    async def test_running_task_blocks_same_project_edit_but_not_another_project(self) -> None:
        manager = TaskManager()
        runner_started = asyncio.Event()
        release_runner = asyncio.Event()

        async def runner(_publish):
            runner_started.set()
            await release_runner.wait()

        task_id = manager.start("project-a", "translate", runner)
        await runner_started.wait()

        with self.assertRaises(ProjectTaskConflictError):
            manager.lease("project-a", "page-edit")
        with manager.lease("project-b", "page-edit"):
            self.assertEqual(
                manager.project_busy_snapshot("project-b"),
                {"is_busy": True, "busy_action": "page-edit"},
            )

        release_runner.set()
        await manager.wait(task_id)

    async def test_same_project_page_edits_are_mutually_exclusive(self) -> None:
        manager = TaskManager()

        first_page = manager.lease("project-a", "page-edit:one")
        with self.assertRaises(ProjectTaskConflictError):
            manager.lease("project-a", "page-edit:two")
        first_page.release()

        with manager.lease("project-a", "page-edit:two"):
            self.assertEqual(
                manager.project_busy_snapshot("project-a")["busy_action"],
                "page-edit:two",
            )

    async def test_stale_or_double_release_cannot_release_a_newer_lease(self) -> None:
        manager = TaskManager()

        stale = manager.lease("project-a", "first-edit")
        stale.release()
        current = manager.lease("project-a", "second-edit")
        stale.release()

        self.assertEqual(
            manager.project_busy_snapshot("project-a"),
            {"is_busy": True, "busy_action": "second-edit"},
        )
        with self.assertRaises(ProjectTaskConflictError):
            manager.lease("project-a", "third-edit")
        current.release()

    async def test_runner_failure_and_cancellation_release_the_project_lease(self) -> None:
        manager = TaskManager()

        async def failing_runner(_publish):
            raise RuntimeError("expected failure")

        failed_task = manager.start("project-failed", "translate", failing_runner)
        await manager.wait(failed_task)
        self.assertFalse(manager.project_busy_snapshot("project-failed")["is_busy"])

        runner_started = asyncio.Event()

        async def cancelled_runner(_publish):
            runner_started.set()
            await asyncio.Event().wait()

        cancelled_task = manager.start("project-cancelled", "detect", cancelled_runner)
        await runner_started.wait()
        await manager.cancel(cancelled_task)
        await manager.wait(cancelled_task)
        self.assertFalse(manager.project_busy_snapshot("project-cancelled")["is_busy"])

    async def test_task_registration_failure_rolls_back_task_and_project_lease(self) -> None:
        manager = TaskManager()

        async def runner(_publish):
            return {"ok": True}

        with mock.patch("task_manager.asyncio.create_task", side_effect=RuntimeError("loop closed")):
            with self.assertRaisesRegex(RuntimeError, "loop closed"):
                manager.start("project-a", "translate", runner)

        self.assertIsNone(manager.project_snapshot("project-a"))
        self.assertEqual(
            manager.project_busy_snapshot("project-a"),
            {"is_busy": False, "busy_action": ""},
        )
        with manager.lease("project-a", "page-edit"):
            self.assertTrue(manager.project_busy_snapshot("project-a")["is_busy"])

    async def test_immediate_cancellation_releases_lease_and_publishes_terminal_state(self) -> None:
        manager = TaskManager()

        async def runner(_publish):
            await asyncio.Event().wait()

        task_id = manager.start("project-a", "translate", runner)
        cancelled = await manager.cancel(task_id)
        waited = await manager.wait(task_id)

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(waited["events"][-1]["event"], "cancelled")
        self.assertFalse(manager.project_busy_snapshot("project-a")["is_busy"])

    async def test_cancellation_returns_while_cleanup_keeps_the_project_lease(self) -> None:
        manager = TaskManager()
        runner_started = asyncio.Event()
        cleanup_started = asyncio.Event()
        allow_cleanup = asyncio.Event()

        async def runner(_publish):
            try:
                runner_started.set()
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await allow_cleanup.wait()

        task_id = manager.start("project-a", "translate", runner)
        await runner_started.wait()
        cancelling = await asyncio.wait_for(manager.cancel(task_id), timeout=0.1)
        await cleanup_started.wait()

        self.assertEqual(cancelling["status"], "cancelling")
        self.assertTrue(manager.project_busy_snapshot("project-a")["is_busy"])

        allow_cleanup.set()
        completed = await manager.wait(task_id)
        self.assertEqual(completed["status"], "cancelled")
        self.assertFalse(manager.project_busy_snapshot("project-a")["is_busy"])

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
