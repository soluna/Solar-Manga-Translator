from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main

from workflow_progress import (
    TASK_ACTION_ALIASES,
    WORKFLOW_ACTIONS,
    UnsupportedWorkflowActionError,
    describe_task_action,
    require_task_action,
)


WORKFLOW_CONTRACT_PATH = REPO_ROOT / "contracts" / "workflow-actions-v1.json"


class WorkflowContractTests(unittest.TestCase):
    def test_contract_declares_command_and_stage_compatibility_semantics(self) -> None:
        contract = json.loads(WORKFLOW_CONTRACT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(contract["unknown_action"]["behavior"], "reject")
        self.assertTrue(
            all("aliases" in action for action in contract["actions"].values())
        )
        self.assertEqual(
            contract["compatibility"]["workflow_stage"],
            {
                "role": "compatibility_projection",
                "source_of_truth": "project_head",
                "capabilities_source": "project_view",
                "removal_condition": "project_view_capabilities_are_independent",
            },
        )

    def test_backend_action_descriptors_match_the_shared_contract(self) -> None:
        contract = json.loads(WORKFLOW_CONTRACT_PATH.read_text(encoding="utf-8"))
        contract_aliases = {
            alias: action
            for action, expected in contract["actions"].items()
            for alias in expected["aliases"]
        }

        self.assertEqual(contract["schema_version"], 1)
        self.assertEqual(set(WORKFLOW_ACTIONS), set(contract["actions"]))
        self.assertEqual(TASK_ACTION_ALIASES, contract_aliases)
        for action, expected in contract["actions"].items():
            expected_descriptor = {
                key: value for key, value in expected.items() if key != "aliases"
            }
            for candidate in (action, *expected["aliases"]):
                self.assertEqual(require_task_action(candidate), action, candidate)
                descriptor = describe_task_action(candidate)
                self.assertEqual(descriptor.action, action, candidate)
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
                    expected_descriptor,
                    candidate,
                )

    def test_unknown_actions_are_rejected_instead_of_starting_a_full_translation(self) -> None:
        contract = json.loads(WORKFLOW_CONTRACT_PATH.read_text(encoding="utf-8"))
        rejected_actions = (
            contract["unknown_action"]["example"],
            "",
            None,
            " \t ",
        )

        for rejected_action in rejected_actions:
            with self.subTest(action=repr(rejected_action)):
                with mock.patch.object(main.task_manager, "start") as start_task:
                    with self.assertRaises(UnsupportedWorkflowActionError):
                        main.start_translation_task(
                            session_id="contract-test-project",
                            session={},
                            action=rejected_action,
                            config={},
                            target_stored_name="",
                        )

                    start_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
