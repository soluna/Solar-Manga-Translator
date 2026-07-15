from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.workflow_progress import (
    UnsupportedWorkflowActionError,
    describe_task_action,
    require_task_action,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_CONTRACT_PATH = REPO_ROOT / "contracts" / "workflow-actions-v1.json"


class WorkflowContractTests(unittest.TestCase):
    def test_backend_action_descriptors_match_the_shared_contract(self) -> None:
        contract = json.loads(WORKFLOW_CONTRACT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(contract["schema_version"], 1)
        for action, expected in contract["actions"].items():
            descriptor = describe_task_action(action)
            self.assertEqual(
                {
                    "action_label": descriptor.action_label,
                    "workflow_phase": descriptor.workflow_phase,
                    "phase_label": descriptor.phase_label,
                    "running_stage": descriptor.running_stage,
                    "completed_stage": descriptor.completed_stage,
                    "scope": descriptor.scope,
                    "scope_label": descriptor.scope_label,
                    "start_message": descriptor.start_message,
                    "progress_message": descriptor.progress_message,
                    "failure_message": descriptor.failure_message,
                },
                expected,
                action,
            )

    def test_unknown_actions_are_rejected_instead_of_starting_a_full_translation(self) -> None:
        self.assertEqual(require_task_action("resume_translate"), "resume-translate")
        with self.assertRaises(UnsupportedWorkflowActionError):
            require_task_action("future-unknown-action")


if __name__ == "__main__":
    unittest.main()
