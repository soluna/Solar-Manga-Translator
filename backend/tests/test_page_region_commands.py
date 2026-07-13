from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from domain.project_artifacts import PageArtifactEvent, ProjectArtifactState
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


def automatic_region(region_id: str, bbox: list[int]) -> dict:
    return {
        "region_id": region_id,
        "page_id": "0001.png",
        "kind": "auto",
        "bbox": bbox,
        "source_text": region_id,
        "translation": {"machine": "", "edited": "", "resolved": ""},
        "recognition": {},
        "style": {"font_size": 18},
        "flags": {},
    }


class PageRegionCommandTests(unittest.TestCase):
    def make_project(self, root: Path) -> tuple[TranslatorEngine, str, str, dict]:
        paths = make_test_paths(root)
        engine = TranslatorEngine(BACKEND_DIR, app_paths=paths)
        project_id = "unified-region-project"
        page_id = "0001.png"
        source_dir = paths.output_dir / project_id / "source"
        translated_dir = paths.output_dir / project_id / "translated"
        source_dir.mkdir(parents=True)
        translated_dir.mkdir(parents=True)
        Image.new("RGB", (120, 160), (255, 255, 255)).save(source_dir / page_id)
        artifact_state = ProjectArtifactState.create([page_id]).apply(
            page_id,
            PageArtifactEvent.RECOGNIZED,
        )
        session = {
            "source_dir": str(source_dir),
            "translated_dir": str(translated_dir),
            "source_images": [{"name": "Page 1", "stored_name": page_id}],
            "translated_output_map": {},
            "workflow_stage": "detected",
            "last_config": {},
            "manual_regions": {},
            "translation_region_overrides": {},
            "translation_region_skip_overrides": {},
            "translation_region_disabled_overrides": {},
            "translation_region_layout_overrides": {},
            "style_region_overrides": {},
            "artifact_state": artifact_state.model_dump(mode="json"),
        }
        engine.initialize_project(project_id, session, title="Unified regions")
        engine._write_json_file(
            engine._project_page_document_path(project_id, page_id),
            {
                "page_id": page_id,
                "dimensions": {"width": 120, "height": 160},
                "regions": [
                    automatic_region("auto-1", [5, 5, 30, 35]),
                    automatic_region("auto-2", [40, 10, 75, 45]),
                    automatic_region("auto-3", [10, 80, 55, 120]),
                ],
                "metadata": {"document_version": 1, "revision": 1},
            },
        )
        return engine, project_id, page_id, session

    def test_create_region_preserves_automatic_regions_when_editable_cache_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine, project_id, page_id, session = self.make_project(Path(tmp))

            result = asyncio.run(
                engine.apply_page_commands(
                    project_id=project_id,
                    session=session,
                    page_id=page_id,
                    raw_config={"target_lang": "CHS", "translator": "none"},
                    commands=[{"type": "create_region", "bbox": [65, 80, 110, 125]}],
                )
            )

            regions = result["document"]["regions"]
            self.assertEqual(len(regions), 4)
            self.assertEqual(
                [region["region_id"] for region in regions[:3]],
                ["auto-1", "auto-2", "auto-3"],
            )
            self.assertEqual(regions[-1]["kind"], "manual")
            self.assertEqual(regions[-1]["origin"], "user")
            self.assertTrue(all(region["origin"] == "automatic" for region in regions[:3]))
            self.assertEqual(regions[-1]["style"]["font_size"], 18)

    def test_delete_only_user_region_preserves_automatic_regions_and_does_not_resurrect_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine, project_id, page_id, session = self.make_project(Path(tmp))
            created = asyncio.run(
                engine.apply_page_commands(
                    project_id=project_id,
                    session=session,
                    page_id=page_id,
                    raw_config={"target_lang": "CHS", "translator": "none"},
                    commands=[{"type": "create_region", "bbox": [65, 80, 110, 125]}],
                )
            )
            created_region_id = created["created_region_id"]

            deleted = asyncio.run(
                engine.apply_page_commands(
                    project_id=project_id,
                    session=session,
                    page_id=page_id,
                    raw_config={"target_lang": "CHS", "translator": "none"},
                    commands=[
                        {
                            "type": "delete_manual_region",
                            "region_id": created_region_id,
                        }
                    ],
                )
            )

            regions = deleted["document"]["regions"]
            self.assertEqual(
                [region["region_id"] for region in regions],
                ["auto-1", "auto-2", "auto-3"],
            )
            self.assertNotIn(
                created_region_id,
                {region["region_id"] for region in regions},
            )

    def test_automatic_and_user_regions_share_translation_layout_and_style_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine, project_id, page_id, session = self.make_project(Path(tmp))
            created = asyncio.run(
                engine.apply_page_commands(
                    project_id=project_id,
                    session=session,
                    page_id=page_id,
                    raw_config={"target_lang": "CHS", "translator": "none"},
                    commands=[{"type": "create_region", "bbox": [65, 80, 110, 125]}],
                )
            )
            user_region_id = created["created_region_id"]

            edited = asyncio.run(
                engine.apply_page_commands(
                    project_id=project_id,
                    session=session,
                    page_id=page_id,
                    raw_config={"target_lang": "CHS", "translator": "none"},
                    commands=[
                        {"type": "update_translation", "region_id": "auto-1", "text": "自动框译文"},
                        {"type": "update_font_size", "region_id": "auto-1", "font_size": 22},
                        {"type": "update_region_bbox", "region_id": "auto-1", "bbox": [6, 6, 32, 38]},
                        {"type": "update_translation", "region_id": user_region_id, "text": "用户框译文"},
                        {"type": "update_font_size", "region_id": user_region_id, "font_size": 22},
                        {"type": "update_region_bbox", "region_id": user_region_id, "bbox": [64, 79, 111, 126]},
                    ],
                )
            )

            regions_by_id = {
                region["region_id"]: region
                for region in edited["document"]["regions"]
            }
            automatic = regions_by_id["auto-1"]
            authored = regions_by_id[user_region_id]
            self.assertEqual(automatic["translation"]["resolved"], "自动框译文")
            self.assertEqual(authored["translation"]["resolved"], "用户框译文")
            self.assertEqual(automatic["style"]["font_size_override"], 22)
            self.assertEqual(authored["style"]["font_size_override"], 22)
            self.assertEqual(automatic["bbox"], [6, 6, 32, 38])
            self.assertEqual(authored["bbox"], [64, 79, 111, 126])

    def test_page_command_batch_rolls_back_when_a_later_command_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine, project_id, page_id, session = self.make_project(Path(tmp))

            with self.assertRaisesRegex(ValueError, "暂不支持"):
                asyncio.run(
                    engine.apply_page_commands(
                        project_id=project_id,
                        session=session,
                        page_id=page_id,
                        raw_config={"target_lang": "CHS", "translator": "none"},
                        commands=[
                            {
                                "type": "update_translation",
                                "region_id": "auto-1",
                                "text": "不应保留",
                            },
                            {"type": "future_unsupported_command"},
                        ],
                    )
                )

            self.assertEqual(session["translation_region_overrides"], {})
            document = engine.get_page_document(project_id, session, page_id)
            auto_region = next(
                region for region in document["regions"] if region["region_id"] == "auto-1"
            )
            self.assertEqual(auto_region["translation"]["resolved"], "")

    def test_page_command_rolls_back_persisted_files_when_commit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine, project_id, page_id, session = self.make_project(Path(tmp))
            page_document_path = engine._project_page_document_path(project_id, page_id)
            project_manifest_path = engine._project_manifest_path(project_id)
            snapshots_dir = engine._project_snapshots_dir(project_id)
            previous_document = page_document_path.read_bytes()
            previous_manifest = project_manifest_path.read_bytes()
            previous_snapshots = {path.name for path in snapshots_dir.glob("*.json")}

            with patch.object(
                engine,
                "_refresh_project_index_entry",
                side_effect=OSError("simulated index failure"),
            ):
                with self.assertRaisesRegex(OSError, "simulated index failure"):
                    asyncio.run(
                        engine.apply_page_commands(
                            project_id=project_id,
                            session=session,
                            page_id=page_id,
                            raw_config={"target_lang": "CHS", "translator": "none"},
                            commands=[{"type": "create_region", "bbox": [65, 80, 110, 125]}],
                        )
                    )

            self.assertEqual(session["manual_regions"], {})
            self.assertEqual(page_document_path.read_bytes(), previous_document)
            self.assertEqual(project_manifest_path.read_bytes(), previous_manifest)
            self.assertEqual(
                {path.name for path in snapshots_dir.glob("*.json")},
                previous_snapshots,
            )
            self.assertEqual(
                [region["region_id"] for region in engine.get_page_document(project_id, session, page_id)["regions"]],
                ["auto-1", "auto-2", "auto-3"],
            )

    def test_page_commands_reject_a_stale_document_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine, project_id, page_id, session = self.make_project(Path(tmp))
            document = engine.get_page_document(project_id, session, page_id)
            current_revision = int(document["metadata"]["revision"])

            with self.assertRaisesRegex(RuntimeError, "版本"):
                asyncio.run(
                    engine.apply_page_commands(
                        project_id=project_id,
                        session=session,
                        page_id=page_id,
                        raw_config={"target_lang": "CHS", "translator": "none"},
                        commands=[
                            {
                                "type": "update_translation",
                                "region_id": "auto-1",
                                "text": "过期请求",
                            }
                        ],
                        expected_revision=current_revision - 1,
                    )
                )

            self.assertEqual(session["translation_region_overrides"], {})
            accepted = asyncio.run(
                engine.apply_page_commands(
                    project_id=project_id,
                    session=session,
                    page_id=page_id,
                    raw_config={"target_lang": "CHS", "translator": "none"},
                    commands=[
                        {
                            "type": "update_translation",
                            "region_id": "auto-1",
                            "text": "当前请求",
                        }
                    ],
                    expected_revision=current_revision,
                )
            )
            self.assertGreater(accepted["revision"], current_revision)

    def test_concurrent_page_commands_with_the_same_revision_cannot_overwrite_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine, project_id, page_id, session = self.make_project(Path(tmp))
            current_revision = int(
                engine.get_page_document(project_id, session, page_id)["metadata"]["revision"]
            )

            async def submit(text: str):
                return await engine.apply_page_commands(
                    project_id=project_id,
                    session=session,
                    page_id=page_id,
                    raw_config={"target_lang": "CHS", "translator": "none"},
                    commands=[
                        {
                            "type": "update_translation",
                            "region_id": "auto-1",
                            "text": text,
                        }
                    ],
                    expected_revision=current_revision,
                )

            async def run_concurrently():
                return await asyncio.gather(
                    submit("并发请求 A"),
                    submit("并发请求 B"),
                    return_exceptions=True,
                )

            results = asyncio.run(run_concurrently())

            successes = [result for result in results if isinstance(result, dict)]
            conflicts = [result for result in results if isinstance(result, RuntimeError)]
            self.assertEqual(len(successes), 1)
            self.assertEqual(len(conflicts), 1)
            self.assertIn("版本", str(conflicts[0]))
            self.assertIn(
                session["translation_region_overrides"]["auto-1"],
                {"并发请求 A", "并发请求 B"},
            )


if __name__ == "__main__":
    unittest.main()
