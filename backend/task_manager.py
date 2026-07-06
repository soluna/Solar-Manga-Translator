from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable


TaskEvent = dict[str, Any]
TaskPublisher = Callable[[TaskEvent], Awaitable[None]]
TaskRunner = Callable[[TaskPublisher], Awaitable[dict[str, Any] | None]]

TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
WINDOWS_PATH_PATTERN = re.compile(r"(?i)\b[A-Z]:\\(?:[^\\\s]+\\)*[^\\\s]*")
POSIX_PATH_PATTERN = re.compile(
    r"(?<!https:)(?<!http:)/(?:Users|home|var|tmp|Volumes|opt|private)(?:/[^\s:]+)+"
)
BEARER_TOKEN_PATTERN = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+")
NAMED_SECRET_PATTERN = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|token|secret)\s*[=:]\s*)[^\s,;]+"
)
OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


class TaskNotFoundError(KeyError):
    pass


class ProjectTaskConflictError(RuntimeError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_public_task_error(exc: BaseException) -> dict[str, Any]:
    raw_detail = " ".join(str(exc).split())
    lowered = raw_detail.lower()

    if any(
        marker in lowered
        for marker in (
            "http 401",
            "http 403",
            "unauthorized",
            "forbidden",
            "invalid api key",
            "incorrect api key",
        )
    ):
        code = "TRANSLATION_AUTH_FAILED"
        message = "翻译服务拒绝了当前 API 配置。"
        action = "请检查翻译服务地址、API Key、模型名称和账户权限后重试。"
        retryable = False
    elif any(
        marker in lowered
        for marker in (
            "timeout",
            "timed out",
            "connection",
            "network",
            "failed to fetch",
            "name resolution",
        )
    ):
        code = "NETWORK_ERROR"
        message = "连接外部服务失败。"
        action = "请检查网络、代理和服务地址，稍后重试。"
        retryable = True
    elif any(
        marker in lowered
        for marker in (
            "cuda",
            "gpu",
            "pytorch",
            "onnx",
            "model file",
            "checkpoint",
        )
    ):
        code = "RUNTIME_NOT_READY"
        message = "本地识别运行环境尚未就绪。"
        action = "请打开设置查看运行环境和模型状态，修复后重试。"
        retryable = False
    elif any(marker in lowered for marker in ("no space left", "disk full")):
        code = "STORAGE_ERROR"
        message = "可用磁盘空间不足，任务无法继续。"
        action = "请释放项目运行目录所在磁盘的空间后重试。"
        retryable = False
    else:
        code = "TASK_FAILED"
        message = "任务执行失败。"
        action = "请重试；若问题持续，请在设置中导出诊断包。"
        retryable = True

    technical_message = f"{type(exc).__name__}: {raw_detail or 'unknown error'}"
    technical_message = WINDOWS_PATH_PATTERN.sub("[LOCAL_PATH]", technical_message)
    technical_message = POSIX_PATH_PATTERN.sub("[LOCAL_PATH]", technical_message)
    technical_message = BEARER_TOKEN_PATTERN.sub(r"\1[REDACTED]", technical_message)
    technical_message = NAMED_SECRET_PATTERN.sub(r"\1[REDACTED]", technical_message)
    technical_message = OPENAI_KEY_PATTERN.sub("[REDACTED]", technical_message)
    technical_message = technical_message[:280]
    return {
        "code": code,
        "message": message,
        "action": action,
        "retryable": retryable,
        "technical_message": technical_message,
    }


@dataclass
class ManagedTask:
    task_id: str
    project_id: str
    action: str
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    sequence: int = 0
    events: deque[TaskEvent] = field(default_factory=lambda: deque(maxlen=500))
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    worker: asyncio.Task[None] | None = None


class TaskManager:
    """Own long-running project tasks independently from client connections."""

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        max_retained_tasks: int = 100,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._max_retained_tasks = max(10, max_retained_tasks)
        self._tasks: dict[str, ManagedTask] = {}
        self._project_tasks: dict[str, str] = {}

    def start(
        self,
        project_id: str,
        action: str,
        runner: TaskRunner,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        existing_id = self._project_tasks.get(project_id)
        if existing_id:
            existing = self._tasks.get(existing_id)
            if existing and existing.status not in TERMINAL_TASK_STATUSES:
                raise ProjectTaskConflictError(
                    f"Project {project_id} already has task {existing_id}"
                )

        self._prune_completed_tasks()
        task_id = uuid.uuid4().hex
        managed = ManagedTask(
            task_id=task_id,
            project_id=project_id,
            action=action,
            metadata=dict(metadata or {}),
        )
        self._tasks[task_id] = managed
        self._project_tasks[project_id] = task_id
        managed.worker = asyncio.create_task(
            self._execute(managed, runner),
            name=f"project-task-{task_id}",
        )
        return task_id

    def snapshot(self, task_id: str, *, include_events: bool = True) -> dict[str, Any]:
        managed = self._require(task_id)
        snapshot = {
            "task_id": managed.task_id,
            "project_id": managed.project_id,
            "action": managed.action,
            "metadata": dict(managed.metadata),
            "status": managed.status,
            "created_at": managed.created_at,
            "updated_at": managed.updated_at,
            "sequence": managed.sequence,
        }
        if include_events:
            snapshot["events"] = [dict(event) for event in managed.events]
        return snapshot

    def project_snapshot(self, project_id: str) -> dict[str, Any] | None:
        task_id = self._project_tasks.get(project_id)
        if not task_id:
            return None
        managed = self._tasks.get(task_id)
        if not managed:
            return None
        return self.snapshot(task_id)

    async def wait(self, task_id: str) -> dict[str, Any]:
        managed = self._require(task_id)
        if managed.worker:
            await managed.worker
        return self.snapshot(task_id)

    async def cancel(self, task_id: str) -> dict[str, Any]:
        managed = self._require(task_id)
        if managed.status in TERMINAL_TASK_STATUSES:
            return self.snapshot(task_id)
        managed.status = "cancelling"
        managed.updated_at = utc_now_iso()
        await self._publish(
            managed,
            {
                "event": "status",
                "message": "正在停止当前任务...",
                "task_status": "cancelling",
            },
        )
        if managed.worker:
            managed.worker.cancel()
        return self.snapshot(task_id)

    async def subscribe(
        self,
        task_id: str,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[TaskEvent]:
        managed = self._require(task_id)
        cursor = max(0, int(after_sequence or 0))

        while True:
            async with managed.changed:
                pending = [
                    dict(event)
                    for event in managed.events
                    if int(event.get("sequence") or 0) > cursor
                ]
                if not pending:
                    if managed.status in TERMINAL_TASK_STATUSES:
                        return
                    await managed.changed.wait()
                    continue

            for event in pending:
                cursor = max(cursor, int(event.get("sequence") or 0))
                yield event

    async def _execute(self, managed: ManagedTask, runner: TaskRunner) -> None:
        managed.status = "running"
        managed.updated_at = utc_now_iso()
        await self._publish(
            managed,
            {
                "event": "task",
                "task_status": "running",
                "project_id": managed.project_id,
                "action": managed.action,
                "metadata": dict(managed.metadata),
            },
        )
        try:
            result = await runner(lambda event: self._publish(managed, event))
        except asyncio.CancelledError:
            managed.status = "cancelled"
            managed.updated_at = utc_now_iso()
            public_error = {
                "code": "TASK_CANCELLED",
                "message": "任务已停止。",
                "action": "可以调整设置后重新开始。",
                "retryable": True,
                "technical_message": "",
            }
            await self._publish(
                managed,
                {
                    "event": "cancelled",
                    "task_status": "cancelled",
                    "message": public_error["message"],
                    "error": public_error,
                },
            )
        except Exception as exc:
            managed.status = "failed"
            managed.updated_at = utc_now_iso()
            self._logger.exception(
                "Managed project task failed. task_id=%s project_id=%s action=%s",
                managed.task_id,
                managed.project_id,
                managed.action,
            )
            public_error = build_public_task_error(exc)
            await self._publish(
                managed,
                {
                    "event": "error",
                    "task_status": "failed",
                    "message": public_error["message"],
                    "error": public_error,
                },
            )
        else:
            managed.status = "completed"
            managed.updated_at = utc_now_iso()
            await self._publish(
                managed,
                {
                    "event": "completed",
                    "task_status": "completed",
                    **(result or {}),
                },
            )
        finally:
            async with managed.changed:
                managed.changed.notify_all()

    async def _publish(self, managed: ManagedTask, event: TaskEvent) -> None:
        managed.sequence += 1
        managed.updated_at = utc_now_iso()
        payload = {
            **event,
            "task_id": managed.task_id,
            "sequence": managed.sequence,
        }
        managed.events.append(payload)
        async with managed.changed:
            managed.changed.notify_all()

    def _require(self, task_id: str) -> ManagedTask:
        managed = self._tasks.get(task_id)
        if not managed:
            raise TaskNotFoundError(task_id)
        return managed

    def _prune_completed_tasks(self) -> None:
        if len(self._tasks) < self._max_retained_tasks:
            return
        removable = [
            managed
            for managed in self._tasks.values()
            if managed.status in TERMINAL_TASK_STATUSES
        ]
        removable.sort(key=lambda item: item.updated_at)
        remove_count = len(self._tasks) - self._max_retained_tasks + 1
        for managed in removable[:remove_count]:
            self._tasks.pop(managed.task_id, None)
            if self._project_tasks.get(managed.project_id) == managed.task_id:
                self._project_tasks.pop(managed.project_id, None)
