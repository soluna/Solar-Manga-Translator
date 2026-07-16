from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from workflow_progress import (
    PHASE_TOTAL,
    TASK_ACTION_ALIASES,
    WORKFLOW_ACTIONS,
    WorkflowActionDescriptor,
    WorkflowStepDescriptor,
    UnsupportedWorkflowActionError,
    describe_task_action,
    describe_workflow_step,
    enrich_task_event,
    normalize_task_action,
    require_task_action,
)


@dataclass(frozen=True)
class ProjectCommand:
    project_id: str
    action: str
    config: Mapping[str, Any] = field(default_factory=dict)
    target_stored_name: str | None = None
    expected_page_revision: int | None = None

    def __post_init__(self) -> None:
        project_id = str(self.project_id or "").strip()
        if not project_id:
            raise ValueError("Project Command requires a project_id")
        action = require_task_action(self.action)
        target_stored_name = str(self.target_stored_name or "").strip() or None
        expected_page_revision = self.expected_page_revision
        if expected_page_revision is not None and (
            isinstance(expected_page_revision, bool)
            or not isinstance(expected_page_revision, int)
            or expected_page_revision <= 0
        ):
            raise ValueError("expected_page_revision must be a positive integer")
        if action == "translate-page" and target_stored_name is None:
            raise ValueError("translate-page requires target_stored_name")
        if action in {"detect", "translate", "resume-translate"} and target_stored_name:
            raise ValueError(f"{action} does not accept target_stored_name")
        if expected_page_revision is not None and target_stored_name is None:
            raise ValueError(
                "expected_page_revision requires a page-scoped Project Command"
            )
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "action", action)
        object.__setattr__(
            self,
            "config",
            MappingProxyType(copy.deepcopy(dict(self.config or {}))),
        )
        object.__setattr__(
            self,
            "target_stored_name",
            target_stored_name,
        )

__all__ = [
    "PHASE_TOTAL",
    "ProjectCommand",
    "TASK_ACTION_ALIASES",
    "WORKFLOW_ACTIONS",
    "WorkflowActionDescriptor",
    "WorkflowStepDescriptor",
    "UnsupportedWorkflowActionError",
    "describe_task_action",
    "describe_workflow_step",
    "enrich_task_event",
    "normalize_task_action",
    "require_task_action",
]
