from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from domain.project_artifacts import ProjectArtifactState
from domain.project_state import (
    PROJECT_STATE_SCHEMA_VERSION,
    InvalidProjectStateError,
    ProjectState,
    UnsupportedProjectStateVersionError,
)


class ProjectStateTests(unittest.TestCase):
    def test_v2_round_trips_the_runtime_session_through_one_typed_state(self) -> None:
        artifact_state = ProjectArtifactState.create(["0001.png"])
        runtime_session = {
            "project_id": "project-a",
            "project_title": "Project A",
            "project_note": "note",
            "review_mode": "canvas_beta",
            "project_created_at": "2026-07-10T00:00:00+00:00",
            "project_updated_at": "2026-07-10T00:01:00+00:00",
            "source_dir": "/project/source",
            "translated_dir": "/project/translated",
            "source_images": [
                {
                    "name": "Page 1.png",
                    "stored_name": "0001.png",
                    "url": "/output/project-a/source/0001.png",
                }
            ],
            "translated_output_map": {},
            "deferred_output_names": {"old.png"},
            "workflow_stage": "idle",
            "last_config": {"target_lang": "CHS"},
        }

        state = ProjectState.capture(
            project_id="project-a",
            session=runtime_session,
            artifact_state=artifact_state,
        )
        persisted = state.model_dump(mode="json")
        restored = ProjectState.load(
            persisted,
            expected_project_id="project-a",
        ).to_runtime_session()

        self.assertEqual(persisted["schema_version"], PROJECT_STATE_SCHEMA_VERSION)
        self.assertEqual(restored["source_images"], runtime_session["source_images"])
        self.assertEqual(restored["deferred_output_names"], {"old.png"})
        self.assertEqual(restored["artifact_state"], artifact_state.model_dump(mode="json"))

    def test_legacy_state_migrates_to_v2_without_preserving_unknown_fields(self) -> None:
        artifact_state = ProjectArtifactState.create(["0001.png"])
        legacy_state = {
            "project_id": "project-a",
            "project_title": "Legacy Project",
            "source_dir": "/project/source",
            "translated_dir": "/project/translated",
            "source_images": [
                {"name": "Page 1.png", "stored_name": "0001.png"}
            ],
            "workflow_stage": "idle",
            "obsolete_runtime_field": "drop during migration",
        }

        migrated = ProjectState.load(
            legacy_state,
            expected_project_id="project-a",
            legacy_artifact_state=artifact_state,
        )

        persisted = migrated.model_dump(mode="json")
        self.assertEqual(persisted["schema_version"], PROJECT_STATE_SCHEMA_VERSION)
        self.assertNotIn("obsolete_runtime_field", persisted)
        self.assertEqual(persisted["artifact_state"], artifact_state.model_dump(mode="json"))

    def test_v2_repairs_a_missing_page_artifact_from_the_typed_page_list(self) -> None:
        artifact_state = ProjectArtifactState.create(["0001.png", "0002.png"])
        state = ProjectState.capture(
            project_id="project-a",
            session={
                "source_images": [
                    {"name": "Page 1.png", "stored_name": "0001.png"},
                    {"name": "Page 2.png", "stored_name": "0002.png"},
                ]
            },
            artifact_state=artifact_state,
        ).model_dump(mode="json")
        state["artifact_state"]["pages"].pop("0002.png")

        repaired = ProjectState.load(
            state,
            expected_project_id="project-a",
        )

        self.assertEqual(set(repaired.artifact_state.pages), {"0001.png", "0002.png"})
        self.assertTrue(repaired.artifact_state.pages["0002.png"].source.ready)

    def test_empty_object_is_not_treated_as_a_recoverable_legacy_state(self) -> None:
        with self.assertRaises(InvalidProjectStateError):
            ProjectState.load(
                {},
                expected_project_id="project-a",
                legacy_artifact_state=ProjectArtifactState.create([]),
            )

    def test_unhashable_unknown_schema_version_is_reported_explicitly(self) -> None:
        with self.assertRaises(UnsupportedProjectStateVersionError):
            ProjectState.load(
                {"schema_version": [99]},
                expected_project_id="project-a",
            )


if __name__ == "__main__":
    unittest.main()
