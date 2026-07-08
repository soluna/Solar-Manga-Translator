from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowActionDescriptor:
    action: str
    action_label: str
    workflow_phase: str
    phase_label: str
    phase_index: int
    phase_total: int
    running_stage: str
    completed_stage: str
    scope: str
    scope_label: str
    start_message: str
    progress_message: str
    failure_message: str


@dataclass(frozen=True)
class WorkflowStepDescriptor:
    step: str
    step_label: str
    step_index: int
    step_total: int


PHASE_TOTAL = 6

WORKFLOW_ACTIONS: dict[str, WorkflowActionDescriptor] = {
    "detect": WorkflowActionDescriptor(
        action="detect",
        action_label="文本框识别",
        workflow_phase="recognize",
        phase_label="检测与 OCR",
        phase_index=2,
        phase_total=PHASE_TOTAL,
        running_stage="detecting",
        completed_stage="detected",
        scope="project",
        scope_label="整组页面",
        start_message="文本框识别已开始，共 {total} 张图片。",
        progress_message="正在识别并准备校对：{current} / {total}",
        failure_message="文本框识别失败。",
    ),
    "translate": WorkflowActionDescriptor(
        action="translate",
        action_label="整本翻译",
        workflow_phase="translate",
        phase_label="翻译与嵌字",
        phase_index=4,
        phase_total=PHASE_TOTAL,
        running_stage="translating",
        completed_stage="translated",
        scope="project",
        scope_label="整组页面",
        start_message="翻译已开始，共 {total} 张图片。",
        progress_message="翻译进行中：{current} / {total}",
        failure_message="翻译失败。",
    ),
    "resume-translate": WorkflowActionDescriptor(
        action="resume-translate",
        action_label="继续翻译",
        workflow_phase="translate",
        phase_label="翻译与嵌字",
        phase_index=4,
        phase_total=PHASE_TOTAL,
        running_stage="translating",
        completed_stage="translated",
        scope="project",
        scope_label="整组页面",
        start_message="继续翻译已开始，共 {total} 张图片。",
        progress_message="继续翻译进行中：{current} / {total}",
        failure_message="继续翻译失败。",
    ),
    "translate-page": WorkflowActionDescriptor(
        action="translate-page",
        action_label="当前页翻译",
        workflow_phase="translate",
        phase_label="翻译与嵌字",
        phase_index=4,
        phase_total=PHASE_TOTAL,
        running_stage="translating",
        completed_stage="translated",
        scope="page",
        scope_label="当前页",
        start_message="当前页翻译已开始。",
        progress_message="当前页翻译进行中：{current} / {total}",
        failure_message="当前页翻译失败。",
    ),
    "rerender": WorkflowActionDescriptor(
        action="rerender",
        action_label="重新嵌字",
        workflow_phase="render",
        phase_label="重新嵌字",
        phase_index=6,
        phase_total=PHASE_TOTAL,
        running_stage="translating",
        completed_stage="translated",
        scope="project",
        scope_label="整组页面",
        start_message="重嵌字已开始，共 {total} 张图片。",
        progress_message="重嵌字进行中：{current} / {total}",
        failure_message="重嵌字失败。",
    ),
}

ACTION_STEPS: dict[str, tuple[tuple[str, str], ...]] = {
    "detect": (
        ("prepare", "准备识别任务"),
        ("model", "检查模型与运行环境"),
        ("detect", "检测文本框"),
        ("ocr", "OCR 识别文字"),
        ("workspace", "生成校对工作台"),
    ),
    "translate": (
        ("prepare", "准备翻译任务"),
        ("model", "检查模型与运行环境"),
        ("glossary", "提取专有名词"),
        ("translate", "调用翻译服务"),
        ("inpaint", "生成无字底图"),
        ("render", "嵌字生成页面"),
        ("repair", "复杂页修复"),
        ("package", "生成下载包"),
    ),
    "resume-translate": (
        ("prepare", "准备翻译任务"),
        ("model", "检查模型与运行环境"),
        ("glossary", "提取专有名词"),
        ("translate", "调用翻译服务"),
        ("inpaint", "生成无字底图"),
        ("render", "嵌字生成页面"),
        ("repair", "复杂页修复"),
        ("package", "生成下载包"),
    ),
    "translate-page": (
        ("prepare", "准备翻译任务"),
        ("model", "检查模型与运行环境"),
        ("glossary", "提取专有名词"),
        ("translate", "调用翻译服务"),
        ("inpaint", "生成无字底图"),
        ("render", "嵌字生成页面"),
        ("repair", "复杂页修复"),
        ("package", "生成下载包"),
    ),
    "rerender": (
        ("prepare", "准备重嵌字"),
        ("render", "嵌字生成页面"),
        ("package", "生成下载包"),
    ),
}

STEP_ALIASES = {
    "model-download": "model",
    "model_download": "model",
    "runtime": "model",
    "ocr": "ocr",
    "page-document": "workspace",
    "page_document": "workspace",
    "workspace": "workspace",
    "glossary-extract": "glossary",
    "glossary_extract": "glossary",
    "image-cleanup": "inpaint",
    "image_cleanup": "inpaint",
    "cleanup": "inpaint",
    "erase": "inpaint",
    "inpainting": "inpaint",
    "rerender": "render",
    "re-render": "render",
    "complex": "repair",
    "complex-repair": "repair",
    "complex_repair": "repair",
    "ai-cleanup": "repair",
    "ai_cleanup": "repair",
    "archive": "package",
    "zip": "package",
}


def normalize_task_action(action: Any) -> str:
    normalized = str(action or "").strip().lower()
    if normalized in {"resume_translate", "resume"}:
        return "resume-translate"
    if normalized in {"translate_page", "page-translate"}:
        return "translate-page"
    if normalized in {"render", "re-render"}:
        return "rerender"
    return normalized or "translate"


def describe_task_action(
    action: Any,
    *,
    metadata: dict[str, Any] | None = None,
) -> WorkflowActionDescriptor:
    normalized = normalize_task_action(action)
    descriptor = WORKFLOW_ACTIONS.get(normalized) or WORKFLOW_ACTIONS["translate"]
    if descriptor.action == "rerender" and str((metadata or {}).get("target_stored_name") or "").strip():
        return WorkflowActionDescriptor(
            **{
                **descriptor.__dict__,
                "scope": "page",
                "scope_label": "当前页",
                "start_message": "当前页重嵌字已开始。",
                "progress_message": "当前页重嵌字进行中：{current} / {total}",
            }
        )
    return descriptor


def describe_workflow_step(
    action: Any,
    *,
    event_name: str = "",
    payload: dict[str, Any] | None = None,
) -> WorkflowStepDescriptor | None:
    descriptor = describe_task_action(action)
    step = _normalize_step_key(_explicit_step(payload or {}))
    if not step:
        step = _infer_step_key(
            action=descriptor.action,
            event_name=event_name,
            payload=payload or {},
        )
    if not step:
        return None

    steps = ACTION_STEPS.get(descriptor.action) or ACTION_STEPS["translate"]
    for index, (candidate_step, label) in enumerate(steps, start=1):
        if candidate_step == step:
            return WorkflowStepDescriptor(
                step=candidate_step,
                step_label=label,
                step_index=index,
                step_total=len(steps),
            )
    return None


def enrich_task_event(
    event: dict[str, Any],
    *,
    action: Any,
    metadata: dict[str, Any] | None = None,
    task_status: str = "",
) -> dict[str, Any]:
    payload = dict(event or {})
    descriptor = describe_task_action(action, metadata=metadata)
    event_name = str(payload.get("event") or "").strip().lower()

    payload.setdefault("action", descriptor.action)
    payload.setdefault("task_action", descriptor.action)
    payload.setdefault("action_label", descriptor.action_label)
    payload.setdefault("workflow_phase", descriptor.workflow_phase)
    payload.setdefault("phase_label", descriptor.phase_label)
    payload.setdefault("phase_index", descriptor.phase_index)
    payload.setdefault("phase_total", descriptor.phase_total)
    payload.setdefault("scope", descriptor.scope)
    payload.setdefault("scope_label", descriptor.scope_label)
    payload.setdefault("task_status", task_status or payload.get("task_status") or "")

    inferred_stage = _infer_workflow_stage(
        event_name=event_name,
        descriptor=descriptor,
        task_status=str(payload.get("task_status") or ""),
    )
    if inferred_stage:
        payload.setdefault("workflow_stage", inferred_stage)

    _attach_progress_fields(payload, event_name=event_name, descriptor=descriptor)
    _attach_step_fields(payload, action=descriptor.action, event_name=event_name)
    return payload


def _infer_workflow_stage(
    *,
    event_name: str,
    descriptor: WorkflowActionDescriptor,
    task_status: str,
) -> str:
    if event_name == "completed" or task_status == "completed":
        return descriptor.completed_stage
    if event_name in {"task", "start", "progress", "status"} and task_status not in {
        "cancelled",
        "failed",
    }:
        return descriptor.running_stage
    return ""


def _attach_progress_fields(
    payload: dict[str, Any],
    *,
    event_name: str,
    descriptor: WorkflowActionDescriptor,
) -> None:
    if event_name == "start":
        total = _safe_int(payload.get("total_pages"))
        if total is not None:
            payload.setdefault("progress_current", 0)
            payload.setdefault("progress_total", total)
            payload.setdefault("progress_unit", "page")
            payload.setdefault("default_message", descriptor.start_message.format(total=total))
        return

    if event_name == "progress":
        current = _safe_int(payload.get("current"))
        total = _safe_int(payload.get("total"))
        if current is not None:
            payload.setdefault("progress_current", current)
        if total is not None:
            payload.setdefault("progress_total", total)
        if current is not None and total is not None:
            payload.setdefault("progress_unit", "page")
            payload.setdefault(
                "default_message",
                descriptor.progress_message.format(current=current, total=total),
            )
        return

    if event_name == "error":
        payload.setdefault("default_message", descriptor.failure_message)


def _attach_step_fields(
    payload: dict[str, Any],
    *,
    action: str,
    event_name: str,
) -> None:
    step = describe_workflow_step(action, event_name=event_name, payload=payload)
    if step is None:
        return
    payload.setdefault("workflow_step", step.step)
    payload.setdefault("progress_step", step.step)
    payload.setdefault("step_label", step.step_label)
    payload.setdefault("step_index", step.step_index)
    payload.setdefault("step_total", step.step_total)


def _explicit_step(payload: dict[str, Any]) -> str:
    return str(
        payload.get("workflow_step")
        or payload.get("progress_step")
        or payload.get("step")
        or ""
    ).strip()


def _normalize_step_key(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace(" ", "-")
    return STEP_ALIASES.get(normalized, normalized)


def _infer_step_key(
    *,
    action: str,
    event_name: str,
    payload: dict[str, Any],
) -> str:
    if event_name in {"task", "start"}:
        return "prepare"
    if event_name == "progress":
        return "workspace" if action == "detect" else "render"
    if event_name != "status":
        return ""

    message = str(payload.get("message") or payload.get("default_message") or "").strip()
    if not message:
        return ""
    if any(marker in message for marker in ("下载模型", "运行时", "CUDA", "模型目录")):
        return "model"
    if "专有名词" in message:
        return "glossary"
    if any(marker in message for marker in ("无字底图", "LaMa", "去字", "擦除")):
        return "inpaint"
    if any(marker in message for marker in ("复杂页", "增强修复", "AI 去字")):
        return "repair"
    if any(marker in message for marker in ("下载包", "压缩包", "导出")):
        return "package"
    if any(marker in message for marker in ("嵌字", "重排", "重渲染", "回填")):
        return "render"
    if any(marker in message for marker in ("识别", "OCR", "校对缓存")):
        return "detect" if action == "detect" else "translate"
    if any(marker in message for marker in ("翻译", "文本框")):
        return "translate" if action != "detect" else "detect"
    return ""


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
