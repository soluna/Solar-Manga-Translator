from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from task_manager import TaskManager


class WorkflowTaskEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_events_include_workflow_descriptors(self) -> None:
        manager = TaskManager()

        async def runner(publish):
            await publish({"event": "start", "total_pages": 3})
            await publish({"event": "progress", "current": 2, "total": 3})
            return {"workflow_stage": "translated"}

        task_id = manager.start(
            "project-a",
            "translate-page",
            runner,
            metadata={"target_stored_name": "001.png"},
        )
        await manager.wait(task_id)
        events = manager.snapshot(task_id)["events"]

        start_event = next(event for event in events if event["event"] == "start")
        progress_event = next(event for event in events if event["event"] == "progress")
        completed_event = events[-1]

        self.assertEqual(start_event["task_action"], "translate-page")
        self.assertEqual(start_event["action_label"], "当前页翻译")
        self.assertEqual(start_event["workflow_phase"], "translate")
        self.assertEqual(start_event["phase_label"], "翻译与嵌字")
        self.assertEqual(start_event["scope"], "page")
        self.assertEqual(start_event["scope_label"], "当前页")
        self.assertEqual(start_event["progress_total"], 3)
        self.assertEqual(start_event["workflow_stage"], "translating")
        self.assertIn("当前页翻译已开始", start_event["default_message"])

        self.assertEqual(progress_event["progress_current"], 2)
        self.assertEqual(progress_event["progress_total"], 3)
        self.assertEqual(progress_event["progress_unit"], "page")
        self.assertEqual(progress_event["workflow_stage"], "translating")
        self.assertIn("2 / 3", progress_event["default_message"])

        self.assertEqual(completed_event["workflow_stage"], "translated")
        self.assertEqual(completed_event["task_status"], "completed")


if __name__ == "__main__":
    unittest.main()
