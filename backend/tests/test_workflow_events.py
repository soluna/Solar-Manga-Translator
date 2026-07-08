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
        self.assertEqual(start_event["workflow_step"], "prepare")
        self.assertEqual(start_event["step_label"], "准备翻译任务")
        self.assertEqual(start_event["step_index"], 1)
        self.assertEqual(start_event["step_total"], 8)
        self.assertIn("当前页翻译已开始", start_event["default_message"])

        self.assertEqual(progress_event["progress_current"], 2)
        self.assertEqual(progress_event["progress_total"], 3)
        self.assertEqual(progress_event["progress_unit"], "page")
        self.assertEqual(progress_event["workflow_stage"], "translating")
        self.assertEqual(progress_event["workflow_step"], "render")
        self.assertEqual(progress_event["step_label"], "嵌字生成页面")
        self.assertIn("2 / 3", progress_event["default_message"])

        self.assertEqual(completed_event["workflow_stage"], "translated")
        self.assertEqual(completed_event["task_status"], "completed")

    async def test_task_status_events_infer_fine_grained_workflow_steps(self) -> None:
        manager = TaskManager()

        async def runner(publish):
            await publish({"event": "status", "message": "正在根据全项目原文提取专有名词库…"})
            await publish({"event": "status", "message": "首次使用正在下载模型：detector.ckpt。"})
            await publish({"event": "status", "message": "页面已处理完成，正在生成下载包…"})
            return {"workflow_stage": "translated"}

        task_id = manager.start("project-a", "resume-translate", runner)
        await manager.wait(task_id)
        events = manager.snapshot(task_id)["events"]

        glossary_event = next(event for event in events if "专有名词" in str(event.get("message") or ""))
        model_event = next(event for event in events if "下载模型" in str(event.get("message") or ""))
        package_event = next(event for event in events if "下载包" in str(event.get("message") or ""))

        self.assertEqual(glossary_event["workflow_step"], "glossary")
        self.assertEqual(glossary_event["step_label"], "提取专有名词")
        self.assertEqual(model_event["workflow_step"], "model")
        self.assertEqual(model_event["step_label"], "检查模型与运行环境")
        self.assertEqual(package_event["workflow_step"], "package")
        self.assertEqual(package_event["step_label"], "生成下载包")

    async def test_task_events_accept_explicit_workflow_step(self) -> None:
        manager = TaskManager()

        async def runner(publish):
            await publish({"event": "status", "workflow_step": "repair", "message": "复杂页增强修复中…"})
            return {"workflow_stage": "translated"}

        task_id = manager.start("project-a", "translate", runner)
        await manager.wait(task_id)
        repair_event = next(
            event for event in manager.snapshot(task_id)["events"]
            if event.get("workflow_step") == "repair"
        )

        self.assertEqual(repair_event["step_label"], "复杂页修复")
        self.assertEqual(repair_event["step_index"], 7)
        self.assertEqual(repair_event["step_total"], 8)


if __name__ == "__main__":
    unittest.main()
