from __future__ import annotations

import asyncio
import sys
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from domain.project_artifacts import (
    LegacyPageArtifactEvidence,
    PageArtifactEvent,
    ProjectArtifactSchemaError,
    ProjectArtifactState,
    UnsupportedProjectArtifactSchemaError,
)
from domain.project_state import PROJECT_STATE_SCHEMA_VERSION
from engine.translator import TranslatorEngine
from runtime_paths import AppPaths


def make_test_paths(root: Path) -> AppPaths:
    return AppPaths(
        code_dir=BACKEND_DIR,
        app_data_dir=root / "app-data",
        models_dir=root / "models",
        output_dir=root / "output",
        logs_dir=root / "logs",
        cache_dir=root / "cache",
        config_dir=root / "config",
    )


class ProjectArtifactStateTests(unittest.TestCase):
    def test_page_artifacts_follow_the_visible_three_step_workflow(self) -> None:
        state = ProjectArtifactState.create(["0001.png"])

        imported = state.page_view("0001.png")
        self.assertTrue(imported.artifacts.source.ready)
        self.assertFalse(imported.capabilities.can_review_recognition)
        self.assertFalse(imported.capabilities.can_export)

        state = state.apply("0001.png", PageArtifactEvent.RECOGNIZED)
        recognized = state.page_view("0001.png")
        self.assertTrue(recognized.capabilities.recognition_ready)
        self.assertTrue(recognized.capabilities.blank_ready)
        self.assertTrue(recognized.capabilities.can_review_recognition)
        self.assertTrue(recognized.capabilities.can_translate)
        self.assertFalse(recognized.capabilities.translation_ready)
        self.assertFalse(recognized.capabilities.final_ready)

        state = state.apply("0001.png", PageArtifactEvent.TRANSLATED)
        translated = state.page_view("0001.png")
        self.assertTrue(translated.capabilities.translation_ready)
        self.assertTrue(translated.capabilities.final_ready)
        self.assertFalse(translated.capabilities.final_stale)
        self.assertTrue(translated.capabilities.can_export)

        state = state.apply("0001.png", PageArtifactEvent.TRANSLATION_EDITED)
        edited = state.page_view("0001.png")
        self.assertTrue(edited.capabilities.translation_ready)
        self.assertTrue(edited.capabilities.final_available)
        self.assertFalse(edited.capabilities.final_ready)
        self.assertTrue(edited.capabilities.final_stale)
        self.assertFalse(edited.capabilities.can_export)

        state = state.apply("0001.png", PageArtifactEvent.RENDERED)
        rerendered = state.page_view("0001.png")
        self.assertTrue(rerendered.capabilities.final_ready)
        self.assertFalse(rerendered.capabilities.final_stale)
        self.assertTrue(rerendered.capabilities.can_export)

    def test_uploaded_blank_is_source_derived_and_invalidates_an_existing_render(self) -> None:
        state = ProjectArtifactState.create(["0001.png"])

        painted_blank = state.apply("0001.png", PageArtifactEvent.BLANK_EDITED)
        self.assertTrue(painted_blank.page_view("0001.png").capabilities.blank_ready)
        self.assertFalse(painted_blank.page_view("0001.png").capabilities.can_translate)

        state = state.apply("0001.png", PageArtifactEvent.BLANK_REPLACED)
        uploaded_before_recognition = state.page_view("0001.png")
        self.assertTrue(uploaded_before_recognition.capabilities.blank_ready)
        self.assertFalse(uploaded_before_recognition.capabilities.can_translate)

        state = state.apply("0001.png", PageArtifactEvent.RECOGNIZED)
        state = state.apply("0001.png", PageArtifactEvent.TRANSLATED)
        state = state.apply("0001.png", PageArtifactEvent.BLANK_REPLACED)
        replaced_after_render = state.page_view("0001.png")
        self.assertTrue(replaced_after_render.capabilities.blank_ready)
        self.assertTrue(replaced_after_render.capabilities.translation_ready)
        self.assertTrue(replaced_after_render.capabilities.final_stale)
        self.assertFalse(replaced_after_render.capabilities.can_export)

    def test_legacy_state_migrates_once_and_invalid_schema_is_explicit(self) -> None:
        evidence = [
            LegacyPageArtifactEvidence(
                page_id="0001.png",
                document_revision=3,
                recognition_ready=True,
                blank_ready=True,
            ),
            LegacyPageArtifactEvidence(
                page_id="0002.png",
                document_revision=7,
                recognition_ready=True,
                blank_ready=True,
                translation_ready=True,
                final_ready=True,
            ),
        ]

        migrated = ProjectArtifactState.load(None, legacy_pages=evidence)

        self.assertEqual(migrated.schema_version, 2)
        self.assertTrue(
            migrated.page_view("0001.png").capabilities.can_review_recognition
        )
        self.assertFalse(migrated.page_view("0001.png").capabilities.can_export)
        self.assertTrue(migrated.page_view("0002.png").capabilities.can_export)

        reloaded = ProjectArtifactState.load(
            migrated.model_dump(mode="json"),
            legacy_pages=evidence,
        )
        self.assertEqual(reloaded, migrated)

        with self.assertRaises(UnsupportedProjectArtifactSchemaError):
            ProjectArtifactState.load(
                {"schema_version": 99, "pages": {}},
                legacy_pages=evidence,
            )

        with self.assertRaises(ProjectArtifactSchemaError):
            ProjectArtifactState.load(
                {
                    "schema_version": 2,
                    "pages": {"0001.png": {"page_id": ""}},
                },
                legacy_pages=evidence,
            )

    def test_project_persists_and_exposes_page_artifact_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_test_paths(root)
            engine = TranslatorEngine(BACKEND_DIR, app_paths=paths)
            project_id = "artifact-project"
            source_dir = paths.output_dir / project_id / "source"
            translated_dir = paths.output_dir / project_id / "translated"
            source_dir.mkdir(parents=True)
            translated_dir.mkdir(parents=True)
            Image.new("RGB", (16, 16), (255, 255, 255)).save(
                source_dir / "0001.png"
            )
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [
                    {"name": "Page 1.png", "stored_name": "0001.png"}
                ],
                "translated_output_map": {},
                "workflow_stage": "idle",
            }

            engine.initialize_project(project_id, session, title="Artifact project")
            payload = engine.build_client_session_payload(project_id, session)

            self.assertEqual(payload["artifact_schema_version"], 2)
            page_artifact = payload["page_artifacts"]["0001.png"]
            self.assertTrue(page_artifact["artifacts"]["source"]["ready"])
            self.assertFalse(
                page_artifact["capabilities"]["can_review_recognition"]
            )
            self.assertEqual(
                payload["images"][0]["artifact_state"],
                page_artifact,
            )

            persisted = json.loads(
                engine.project_workspace.project_session_state_path(project_id).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                persisted["schema_version"],
                PROJECT_STATE_SCHEMA_VERSION,
            )
            self.assertEqual(persisted["artifact_state"]["schema_version"], 2)

            restored = engine.restore_project_session(project_id)
            restored_payload = engine.build_client_session_payload(
                project_id,
                restored,
            )
            self.assertEqual(
                restored_payload["page_artifacts"],
                payload["page_artifacts"],
            )

            state_path = engine.project_workspace.project_session_state_path(
                project_id
            )
            legacy_persisted = json.loads(state_path.read_text(encoding="utf-8"))
            legacy_persisted.pop("schema_version", None)
            legacy_persisted.pop("artifact_state", None)
            legacy_persisted["workflow_stage"] = "detected"
            engine._write_json_file(state_path, legacy_persisted)

            engine.restore_project_session(project_id)

            migrated_persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                migrated_persisted["artifact_state"]["schema_version"],
                2,
            )

    def test_manual_translation_edit_invalidates_the_rendered_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_test_paths(root)
            engine = TranslatorEngine(BACKEND_DIR, app_paths=paths)
            project_id = "edited-artifact-project"
            page_id = "0001.png"
            source_dir = paths.output_dir / project_id / "source"
            translated_dir = paths.output_dir / project_id / "translated"
            source_dir.mkdir(parents=True)
            translated_dir.mkdir(parents=True)
            Image.new("RGB", (24, 24), (255, 255, 255)).save(
                source_dir / page_id
            )
            Image.new("RGB", (24, 24), (240, 240, 240)).save(
                translated_dir / page_id
            )
            artifact_state = ProjectArtifactState.create([page_id]).apply(
                page_id,
                PageArtifactEvent.RECOGNIZED,
            ).apply(
                page_id,
                PageArtifactEvent.TRANSLATED,
            )
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "Page 1.png", "stored_name": page_id}],
                "translated_output_map": {page_id: page_id},
                "workflow_stage": "translated",
                "last_config": {},
                "translation_region_overrides": {},
                "translation_region_skip_overrides": {},
                "translation_region_disabled_overrides": {},
                "translation_region_layout_overrides": {},
                "style_region_overrides": {},
                "artifact_state": artifact_state.model_dump(mode="json"),
            }
            engine.initialize_project(project_id, session, title="Edited artifact")
            engine._write_json_file(
                engine._project_page_document_path(project_id, page_id),
                {
                    "page_id": page_id,
                    "dimensions": {"width": 24, "height": 24},
                    "regions": [
                        {
                            "region_id": "region-1",
                            "bbox": [2, 2, 20, 20],
                            "source_text": "原文",
                            "translation": {
                                "machine": "译文",
                                "edited": "",
                                "resolved": "译文",
                            },
                            "flags": {},
                            "style": {},
                        }
                    ],
                    "metadata": {"revision": 1},
                },
            )

            result = asyncio.run(
                engine.apply_page_commands(
                    project_id=project_id,
                    session=session,
                    page_id=page_id,
                    raw_config={},
                    commands=[
                        {
                            "type": "update_translation",
                            "region_id": "region-1",
                            "text": "人工译文",
                        }
                    ],
                )
            )

            self.assertEqual(
                result["translation_page"]["regions"][0]["current_translation"],
                "人工译文",
            )
            self.assertTrue(result["page_artifact"]["capabilities"]["final_stale"])
            session_payload = engine.build_client_session_payload(
                project_id,
                session,
            )
            capabilities = session_payload["page_artifacts"][page_id]["capabilities"]
            self.assertTrue(capabilities["translation_ready"])
            self.assertTrue(capabilities["final_available"])
            self.assertTrue(capabilities["final_stale"])
            self.assertFalse(capabilities["can_export"])
            self.assertEqual(session_payload["download_url"], "")

            with self.assertRaisesRegex(RuntimeError, "重新嵌字"):
                engine.build_session_archive(project_id, session)

            current_state = ProjectArtifactState.model_validate(
                session["artifact_state"]
            ).apply(page_id, PageArtifactEvent.RENDERED)
            session["artifact_state"] = current_state.model_dump(mode="json")
            self.assertTrue(Path(engine.build_session_archive(project_id, session)).exists())
            self.assertTrue(
                engine.build_client_session_payload(project_id, session)["download_url"]
            )

            structural_edit = asyncio.run(
                engine.apply_page_commands(
                    project_id=project_id,
                    session=session,
                    page_id=page_id,
                    raw_config={},
                    commands=[
                        {
                            "type": "create_region",
                            "bbox": [4, 4, 12, 12],
                        }
                    ],
                )
            )
            self.assertTrue(
                structural_edit["page_artifact"]["capabilities"]["final_stale"]
            )

    def test_blank_replacement_and_edit_invalidate_the_rendered_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_test_paths(root)
            engine = TranslatorEngine(BACKEND_DIR, app_paths=paths)
            project_id = "uploaded-blank-project"
            page_id = "0001.png"
            source_dir = paths.output_dir / project_id / "source"
            translated_dir = paths.output_dir / project_id / "translated"
            upload_dir = root / "uploads"
            source_dir.mkdir(parents=True)
            translated_dir.mkdir(parents=True)
            upload_dir.mkdir(parents=True)
            Image.new("RGB", (24, 24), (255, 255, 255)).save(
                source_dir / page_id
            )
            Image.new("RGB", (24, 24), (240, 240, 240)).save(
                translated_dir / page_id
            )
            uploaded_blank = upload_dir / "Page 1.png"
            Image.new("RGB", (24, 24), (230, 230, 230)).save(uploaded_blank)
            artifact_state = ProjectArtifactState.create([page_id]).apply(
                page_id,
                PageArtifactEvent.RECOGNIZED,
            ).apply(
                page_id,
                PageArtifactEvent.TRANSLATED,
            )
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "Page 1.png", "stored_name": page_id}],
                "translated_output_map": {page_id: page_id},
                "workflow_stage": "translated",
                "last_config": {},
                "artifact_state": artifact_state.model_dump(mode="json"),
            }
            engine.initialize_project(project_id, session, title="Uploaded blank")

            result = engine.attach_base_images(
                project_id,
                session,
                [str(uploaded_blank)],
            )

            self.assertEqual(result["updated_page_ids"], [page_id])
            capabilities = engine.build_client_session_payload(
                project_id,
                session,
            )["page_artifacts"][page_id]["capabilities"]
            self.assertTrue(capabilities["blank_ready"])
            self.assertTrue(capabilities["translation_ready"])
            self.assertTrue(capabilities["final_stale"])
            self.assertFalse(capabilities["can_export"])

            rerendered_state = ProjectArtifactState.model_validate(
                session["artifact_state"]
            ).apply(page_id, PageArtifactEvent.RENDERED)
            session["artifact_state"] = rerendered_state.model_dump(mode="json")
            blank_revision = rerendered_state.pages[page_id].blank.revision

            payload = engine.brush_edit_page(
                project_id,
                session,
                page_id,
                [
                    {
                        "mode": "paint",
                        "color": [255, 255, 255],
                        "size": 4,
                        "points": [[0.5, 0.5]],
                    }
                ],
            )

            edited_artifact = payload["page_artifacts"][page_id]
            self.assertEqual(
                edited_artifact["artifacts"]["blank"]["revision"],
                blank_revision + 1,
            )
            self.assertTrue(edited_artifact["capabilities"]["final_stale"])
            self.assertFalse(edited_artifact["capabilities"]["can_export"])


if __name__ == "__main__":
    unittest.main()
