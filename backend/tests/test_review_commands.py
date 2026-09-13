from __future__ import annotations

import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.tests import test_page_region_commands as fixtures
from backend.tests._textblock_stub import textblock_module_patch


class ReviewCommandTests(unittest.TestCase):
    def test_ocr_snapshots_restore_all_origins_without_changing_typography_or_calling_ocr(self):
        with tempfile.TemporaryDirectory() as temporary, textblock_module_patch():
            engine, project_id, page_id, session = fixtures.PageRegionCommandTests().make_project(Path(temporary))
            def edit(commands):
                return asyncio.run(engine.apply_page_commands(project_id, session, page_id,
                    {"target_lang": "CHS", "translator": "none"}, commands))
            with patch.object(engine, "_ensure_runtime_patches"), patch.object(engine, "_select_inference_device", return_value="cpu"):
                manual_id = edit([{"type": "create_region", "bbox": [80, 85, 112, 140]}])["created_region_id"]
                for region_id in ("auto-1", manual_id):
                    result = edit([
                        {"type": "update_source_text", "region_id": region_id, "text": "修正原文"},
                        {"type": "update_translation", "region_id": region_id, "text": "已审定译文"},
                    ])
                    before = next(region for region in result["document"]["regions"] if region["region_id"] == region_id)
                    self.assertEqual(before["source_text"], "修正原文")
                    with patch.object(engine.inference_backend, "recognize_region", new=AsyncMock(return_value={
                        "source_text": "新 OCR 原文", "font_size": 80, "direction": "v", "fg_color": [255, 0, 0],
                    })) as recognize:
                        retry = edit([{"type": "retry_region_ocr", "region_id": region_id}])
                        after = next(region for region in retry["document"]["regions"] if region["region_id"] == region_id)
                        self.assertEqual(after["source_text"], "新 OCR 原文")
                        self.assertEqual(after["bbox"], before["bbox"])
                        self.assertEqual(after["style"], before["style"])
                        self.assertEqual(after["translation"], before["translation"])
                        for key, text in (("previous_ocr_snapshot", "修正原文"), ("ocr_snapshot", "新 OCR 原文")):
                            restored = edit([{"type": "restore_region_ocr_snapshot", "region_id": region_id, "snapshot": retry[key]}])
                            region = next(region for region in restored["document"]["regions"] if region["region_id"] == region_id)
                            self.assertEqual(region["source_text"], text)
                        self.assertEqual(recognize.await_count, 1)
                    with patch.object(engine.inference_backend, "recognize_region", new=AsyncMock(side_effect=RuntimeError("synthetic OCR failure"))):
                        failure = edit([{"type": "retry_region_ocr", "region_id": region_id}])
                        region = next(region for region in failure["document"]["regions"] if region["region_id"] == region_id)
                        self.assertEqual(region["source_text"], "新 OCR 原文")
                        self.assertEqual(region["recognition"]["status"], "failed")

    def test_review_mark_survives_reload_and_expires_after_blank_changes(self):
        with tempfile.TemporaryDirectory() as temporary, textblock_module_patch():
            engine, project_id, page_id, session = fixtures.PageRegionCommandTests().make_project(Path(temporary))
            with patch.object(engine, "_ensure_runtime_patches"):
                before = engine.get_page_document(project_id, session, page_id)
                artifact = engine.build_client_session_payload(project_id, session)["page_artifacts"][page_id]
                marked = asyncio.run(engine.apply_page_commands(project_id, session, page_id, {}, [
                    {"type": "set_review_status", "status": "reviewed"},
                ]))
                self.assertEqual(marked["document"]["metadata"]["review"]["status"], "reviewed")
                self.assertEqual(marked["page_artifact"], artifact)
                session = engine.restore_project_session(project_id)
                self.assertEqual(engine.get_page_document(project_id, session, page_id)["metadata"]["review"]["status"], "reviewed")
                engine.brush_edit_page(project_id, session, page_id, [
                    {"mode": "paint", "points": [{"x": .5, "y": .5}], "size": 10, "color": [0, 0, 0]},
                ])
                self.assertEqual(engine.get_page_document(project_id, session, page_id)["metadata"]["review"]["status"], "unreviewed")

    def test_source_correction_does_not_invent_a_blank_artifact_before_recognition(self):
        with tempfile.TemporaryDirectory() as temporary, textblock_module_patch():
            engine, project_id, page_id, session = fixtures.PageRegionCommandTests().make_project(Path(temporary))
            session["artifact_state"] = fixtures.ProjectArtifactState.create([page_id]).model_dump(mode="json")
            engine.persist_project_state(project_id, session)
            result = asyncio.run(engine.apply_page_commands(project_id, session, page_id, {}, [
                {"type": "update_source_text", "region_id": "auto-1", "text": "人工修正"},
            ]))
            self.assertFalse(result["page_artifact"]["capabilities"]["blank_ready"])

    def test_source_undo_restores_a_cacheless_page_document_after_project_reload(self):
        with tempfile.TemporaryDirectory() as temporary, textblock_module_patch():
            engine, project_id, page_id, session = fixtures.PageRegionCommandTests().make_project(Path(temporary))
            with patch.object(engine, "_ensure_runtime_patches"):
                corrected = asyncio.run(engine.apply_page_commands(
                    project_id, session, page_id, {},
                    [{"type": "update_source_text", "region_id": "auto-1", "text": "人工原文"}],
                ))
                self.assertEqual(corrected["document"]["regions"][0]["source_text"], "人工原文")

                session = engine.restore_project_session(project_id)
                self.assertEqual(
                    engine.get_page_document(project_id, session, page_id)["regions"][0]["source_text"],
                    "人工原文",
                )

                undone = asyncio.run(engine.apply_page_commands(
                    project_id,
                    session,
                    page_id,
                    {},
                    [{
                        "type": "restore_region_ocr_snapshot",
                        "region_id": "auto-1",
                        "snapshot": corrected["ocr_snapshots"]["auto-1"]["before"],
                    }],
                ))
                self.assertEqual(undone["document"]["regions"][0]["source_text"], "auto-1")

                session = engine.restore_project_session(project_id)
                restored_document = engine.get_page_document(project_id, session, page_id)
                self.assertEqual(restored_document["regions"][0]["source_text"], "auto-1")
                self.assertNotIn("auto-1", session.get("source_text_overrides") or {})

    def test_review_status_round_trips_through_a_real_project_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary, textblock_module_patch():
            engine, project_id, page_id, session = fixtures.PageRegionCommandTests().make_project(Path(temporary))
            with patch.object(engine, "_ensure_runtime_patches"):
                marked = asyncio.run(engine.apply_page_commands(
                    project_id, session, page_id, {},
                    [{"type": "set_review_status", "status": "reviewed", "reviewer": "qa"}],
                ))
                self.assertEqual(marked["review_state"]["status"], "reviewed")
                engine.persist_project_state(
                    project_id,
                    session,
                    snapshot_kind="review_checkpoint",
                    snapshot_summary="审校检查点",
                    persist_page_documents=True,
                    page_ids=[page_id],
                )
                snapshots = engine.project_workspace.read_snapshot_manifests(project_id)
                self.assertEqual(len(snapshots), 1)

                # Advance the live project so the restored result proves the
                # review mark came from the snapshot, not the current session.
                changed = asyncio.run(engine.apply_page_commands(
                    project_id, session, page_id, {},
                    [{"type": "update_translation", "region_id": "auto-1", "text": "后续译文"}],
                ))
                self.assertEqual(changed["review_state"]["status"], "unreviewed")

                restored_id, restored_session = engine.restore_snapshot_as_project(
                    project_id,
                    snapshots[0]["snapshot_id"],
                )
                restored_document = engine.get_page_document(
                    restored_id,
                    restored_session,
                    page_id,
                )
                self.assertEqual(restored_document["metadata"]["review"]["status"], "reviewed")
                self.assertEqual(restored_document["metadata"]["review"]["reviewer"], "qa")

    def test_review_status_expires_after_translation_rerender_and_erase_events(self):
        with tempfile.TemporaryDirectory() as temporary, textblock_module_patch():
            engine, project_id, page_id, session = fixtures.PageRegionCommandTests().make_project(Path(temporary))
            with patch.object(engine, "_ensure_runtime_patches"):
                async def mark_reviewed():
                    return await engine.apply_page_commands(
                        project_id,
                        session,
                        page_id,
                        {},
                        [{"type": "set_review_status", "status": "reviewed"}],
                    )

                marked = asyncio.run(mark_reviewed())
                self.assertEqual(marked["review_state"]["status"], "reviewed")
                for event in (
                    fixtures.PageArtifactEvent.TRANSLATION_EDITED,
                    fixtures.PageArtifactEvent.RENDERED,
                    fixtures.PageArtifactEvent.BLANK_EDITED,
                ):
                    engine._apply_page_artifact_event(project_id, session, [page_id], event)
                    engine.persist_project_state(
                        project_id,
                        session,
                        persist_page_documents=True,
                        page_ids=[page_id],
                    )
                    self.assertEqual(
                        engine.get_page_document(project_id, session, page_id)["metadata"]["review"]["status"],
                        "unreviewed",
                    )
                    marked = asyncio.run(mark_reviewed())
                    self.assertEqual(marked["review_state"]["status"], "reviewed")

    def test_ocr_restore_rejects_cross_page_and_malformed_snapshots_atomically(self):
        with tempfile.TemporaryDirectory() as temporary, textblock_module_patch():
            engine, project_id, page_id, session = fixtures.PageRegionCommandTests().make_project(Path(temporary))
            with patch.object(engine, "_ensure_runtime_patches"):
                corrected = asyncio.run(engine.apply_page_commands(
                    project_id, session, page_id, {},
                    [{"type": "update_source_text", "region_id": "auto-1", "text": "人工原文"}],
                ))
                before_maps = (
                    copy.deepcopy(session.get("source_text_overrides")),
                    copy.deepcopy(session.get("region_recognition_overrides")),
                )
                before = corrected["ocr_snapshots"]["auto-1"]["before"]

                cross_page = copy.deepcopy(before)
                cross_page["page_id"] = "0002.png"
                with self.assertRaisesRegex(ValueError, "页面或文本框"):
                    asyncio.run(engine.apply_page_commands(
                        project_id,
                        session,
                        page_id,
                        {},
                        [{"type": "restore_region_ocr_snapshot", "region_id": "auto-1", "snapshot": cross_page}],
                    ))
                self.assertEqual(
                    (session.get("source_text_overrides"), session.get("region_recognition_overrides")),
                    before_maps,
                )

                invalid_version = copy.deepcopy(before)
                invalid_version["version"] = True
                with self.assertRaisesRegex(ValueError, "识别快照不完整"):
                    asyncio.run(engine.apply_page_commands(
                        project_id,
                        session,
                        page_id,
                        {},
                        [{"type": "restore_region_ocr_snapshot", "region_id": "auto-1", "snapshot": invalid_version}],
                    ))
                self.assertEqual(
                    (session.get("source_text_overrides"), session.get("region_recognition_overrides")),
                    before_maps,
                )

                malformed = copy.deepcopy(before)
                malformed["recognition_override"] = {"present": True, "value": None}
                with self.assertRaisesRegex(ValueError, "识别内容无效"):
                    asyncio.run(engine.apply_page_commands(
                        project_id,
                        session,
                        page_id,
                        {},
                        [{"type": "restore_region_ocr_snapshot", "region_id": "auto-1", "snapshot": malformed}],
                    ))
                self.assertEqual(
                    (session.get("source_text_overrides"), session.get("region_recognition_overrides")),
                    before_maps,
                )


if __name__ == "__main__":
    unittest.main()
